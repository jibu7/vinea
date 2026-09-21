"""Revision 0026 over a tenant that already has a closed fiscal day.

`make migrate-check` runs on an empty scratch database, so it proves DDL and nothing else
(architecture rule 10). 0026 adds a nullable column and updates nothing — `fiscal_daily_reports`
is immutable by trigger (`VN011`) and a migration that rewrote a stored Z would be rewriting a
legal document — but "it is additive so it is safe" is a claim, and the claim worth checking is
about the *code* that reads the column afterwards:

* a Z stored **before** the revision has no mark, and must keep computing exactly what it
  computed before — its membership is the `sdc_datetime` range it was written with;
* the open day after it must start where that Z left off, which means translating a legacy
  clock boundary into a counter once, at the seam;
* and the **first Z closed after the upgrade** must take its mark from the device's current
  counter and own only what the legacy Z did not.

So the fixture provisions a tenant at 0025 with a live device, three signed receipts and a
stored Z over the first two of them, upgrades to head, and asserts all three. Same shape as
`tests/test_p7_backfill.py`, which is the file rule 10 names.
"""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.db import set_tenant
from app.fiscal import daily
from app.models.fiscalization import FiscalDailyReport, FiscalDevice
from app.models.user import User
from tests.conftest import ADMIN_URL

BACKFILL_DB = f"{ADMIN_URL.database}_p7_zhw_{os.getpid()}"

#: The revision a tenant that has already closed a fiscal day is on.
PRE_0026_REVISION = "0025_p7_close_day_permission"

#: The receipts the fixture signs, as (counter, gross). Two before the legacy close and one
#: after it — the third is what proves the open day starts above the legacy Z's boundary.
RECEIPTS = ((1, "11800.00"), (2, "23600.00"), (3, "5900.00"))

#: When the legacy Z was taken: after the first two receipts, before the third.
CLOSED_AT = datetime(2026, 3, 15, 18, 0, 0, tzinfo=UTC)
SIGNED_AT = {
    1: CLOSED_AT - timedelta(hours=4),
    2: CLOSED_AT - timedelta(hours=2),
    3: CLOSED_AT + timedelta(hours=1),
}


def _alembic(url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture
def pre_0026_engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{BACKFILL_DB}"'))
    admin.dispose()

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), PRE_0026_REVISION)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        yield engine
    finally:
        engine.dispose()
        admin = create_engine(ADMIN_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": BACKFILL_DB},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{BACKFILL_DB}"'))
        admin.dispose()


def _sale_payload(gross: str) -> dict:
    """A VSDC sale, as `fiscal_receipts.request` holds one.

    Only the fields a day's arithmetic reads are here, and they are spelled the way the
    authority spells them because that is what the column contains — this is a historical
    fixture, not a payload builder, and importing `rwanda/builders.py` to make one would tie
    the fixture to today's mapping rather than to what a stored row looks like.
    """
    amount = Decimal(gross)
    taxable = amount
    tax = (amount * Decimal("18") / Decimal("118")).quantize(Decimal("0.01"))
    return {
        "rcptTyCd": "S",
        "totItemCnt": 1,
        "totTaxblAmt": str(taxable),
        "totTaxAmt": str(tax),
        "totAmt": str(amount),
        "taxblAmtA": "0.00",
        "taxAmtA": "0.00",
        "taxRtA": "0.00",
        "taxblAmtB": str(taxable),
        "taxAmtB": str(tax),
        "taxRtB": "18.00",
        "taxblAmtC": "0.00",
        "taxAmtC": "0.00",
        "taxRtC": "0.00",
        "taxblAmtD": "0.00",
        "taxAmtD": "0.00",
        "taxRtD": "0.00",
        "itemList": [
            {
                "itemSeq": 1,
                "itemNm": "Bottle",
                "qty": "1",
                "prc": str(amount),
                "dcRt": "0",
                "dcAmt": "0.00",
                "taxblAmt": str(taxable),
                "taxAmt": str(tax),
                "taxTyCd": "B",
            }
        ],
    }


def _provision(engine: Engine) -> dict:  # noqa: ARG001 - the fixture's engine names the database
    """A tenant at 0025 with a live device, three signed receipts and a Z over the first two.

    In **one transaction**, not on the fixture's AUTOCOMMIT connection:
    `trg_journal_lines_balanced` is a DEFERRABLE INITIALLY DEFERRED constraint trigger, so under
    autocommit it fires after the first leg — with the entry one-sided — and refuses a posting
    that is perfectly balanced by the time both legs are in.
    """
    txn_engine = create_engine(ADMIN_URL.set(database=BACKFILL_DB))
    with txn_engine.begin() as conn:
        # Through the kernel's guards rather than around them, exactly as
        # `tests/test_p7_backfill.py` does: `app.posting_engine` is set the way `posting._write`
        # sets it, and every entry goes in as a draft and is flipped afterwards.
        conn.execute(text("SELECT set_config('app.posting_engine', 'on', false)"))
        company_id = conn.execute(
            text(
                "INSERT INTO companies (name, tin, vat_registered, fiscal_country, status, "
                "coa_template) VALUES ('Legacy Z Ltd', '999000099', true, 'RW', 'active', "
                "'rw_sme_v1') RETURNING id"
            )
        ).scalar_one()
        currency_id = conn.execute(
            text(
                "INSERT INTO currencies (company_id, code, name, symbol, decimal_places, "
                "is_base, is_active) VALUES (:cid, 'RWF', 'Rwandan Franc', 'FRw', 0, true, true) "
                "RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        branch_id = conn.execute(
            text(
                "INSERT INTO branches (company_id, code, name, is_main, is_active) "
                "VALUES (:cid, 'MAIN', 'Head Office', true, true) RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        user_id = conn.execute(
            text(
                "INSERT INTO users (email, hashed_password, full_name, is_platform_admin, "
                "is_active) VALUES ('legacy@example.test', 'x', 'Legacy Owner', false, true) "
                "RETURNING id"
            )
        ).scalar_one()
        partner_id = conn.execute(
            text(
                "INSERT INTO partners (company_id, name, customer_code, is_customer, "
                "is_supplier, is_active) "
                "VALUES (:cid, 'Walk-in', 'C0001', true, false, true) RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        accounts = {}
        for code, name, class_, parent, postable, control in (
            ("1000", "Assets", "asset", None, False, None),
            ("1200", "Accounts Receivable", "asset", "1000", True, "ar"),
            ("4000", "Income", "income", None, False, None),
            ("4100", "Sales Revenue", "income", "4000", True, None),
        ):
            accounts[code] = conn.execute(
                text(
                    """
                    INSERT INTO gl_accounts (company_id, code, name, class, parent_id,
                                             is_postable, is_control, control_type, is_active)
                    VALUES (:cid, :code, :name, CAST(:class AS account_class), :parent,
                            :postable, :is_control, CAST(:control AS gl_control_type), true)
                    RETURNING id
                    """
                ),
                {
                    "cid": company_id,
                    "code": code,
                    "name": name,
                    "class": class_,
                    "parent": accounts.get(parent) if parent else None,
                    "postable": postable,
                    "is_control": control is not None,
                    "control": control,
                },
            ).scalar_one()
        year_id = conn.execute(
            text(
                "INSERT INTO fiscal_years (company_id, name, start_date, end_date, status) "
                "VALUES (:cid, '2026', DATE '2026-01-01', DATE '2026-12-31', 'open') RETURNING id"
            ),
            {"cid": company_id},
        ).scalar_one()
        period_id = conn.execute(
            text(
                "INSERT INTO accounting_periods (company_id, fiscal_year_id, period_no, name, "
                "start_date, end_date, status) VALUES (:cid, :year, 3, 'March 2026', "
                "DATE '2026-03-01', DATE '2026-03-31', 'open') RETURNING id"
            ),
            {"cid": company_id, "year": year_id},
        ).scalar_one()
        device_id = conn.execute(
            text(
                """
                INSERT INTO fiscal_devices (company_id, branch_id, profile, environment,
                                            base_url, tin, bhf_id, dvc_srl_no, sdc_id, mrc_no,
                                            status, watermarks, activated_at)
                VALUES (:cid, :branch, 'vsdc', 'test', 'http://sandbox.invalid', '999000099',
                        '00', 'SRL-1', 'SDC010000005', 'WIS01006230', 'active', '{}'::jsonb,
                        :activated)
                RETURNING id
                """
            ),
            {
                "cid": company_id,
                "branch": branch_id,
                "activated": CLOSED_AT - timedelta(days=1),
            },
        ).scalar_one()

        documents: dict[int, int] = {}
        for counter, gross in RECEIPTS:
            entry_id = conn.execute(
                text(
                    """
                    INSERT INTO journal_entries (company_id, number, doc_type, event_type,
                                                 module, entry_date, period_id, description,
                                                 status, posted_at)
                    VALUES (:cid, :number, 'INV', 'ar_invoice_posted', 'ar', DATE '2026-03-15',
                            :period, 'sale', 'draft', :posted)
                    RETURNING id
                    """
                ),
                {
                    "cid": company_id,
                    "number": f"JE-{counter:06d}",
                    "period": period_id,
                    "posted": SIGNED_AT[counter],
                },
            ).scalar_one()
            for line_no, (account, amount) in enumerate(
                (("1200", gross), ("4100", f"-{gross}")), start=1
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO journal_lines (company_id, entry_id, line_no, gl_account_id,
                            currency_id, exchange_rate, amount, base_amount, tax_amount,
                            branch_id, partner_type, partner_id)
                        VALUES (:cid, :entry, :line_no, :account, :currency, 1,
                                CAST(:amount AS numeric), CAST(:amount AS numeric), 0, :branch,
                                :partner_type, :partner)
                        """
                    ),
                    {
                        "cid": company_id,
                        "entry": entry_id,
                        "line_no": line_no,
                        "account": accounts[account],
                        "currency": currency_id,
                        "amount": amount,
                        "branch": branch_id,
                        "partner_type": "customer" if account == "1200" else None,
                        "partner": partner_id if account == "1200" else None,
                    },
                )
            conn.execute(
                text("UPDATE journal_entries SET status = 'posted' WHERE id = :entry"),
                {"entry": entry_id},
            )
            documents[counter] = conn.execute(
                text(
                    """
                    INSERT INTO partner_documents (company_id, role, kind, number, doc_type,
                        transaction_type, partner_id, journal_entry_id, document_date,
                        currency_id, exchange_rate, branch_id, tax_mode, control_account_id,
                        description, net_amount, tax_amount, total_amount, base_total_amount,
                        open_amount, direction, status, payment_method)
                    VALUES (:cid, 'ar', 'invoice', :number, 'INV', 'INV', :partner, :entry,
                            DATE '2026-03-15', :currency, 1, :branch, 'exclusive', :control,
                            'sale', CAST(:net AS numeric), CAST(:tax AS numeric),
                            CAST(:gross AS numeric), CAST(:gross AS numeric), 0, 1, 'posted',
                            'cash')
                    RETURNING id
                    """
                ),
                {
                    "cid": company_id,
                    "number": f"INV-{counter:06d}",
                    "partner": partner_id,
                    "entry": entry_id,
                    "currency": currency_id,
                    "branch": branch_id,
                    "control": accounts["1200"],
                    "net": str(Decimal(gross) - Decimal(gross) * Decimal("18") / Decimal("118")),
                    "tax": str(Decimal(gross) * Decimal("18") / Decimal("118")),
                    "gross": gross,
                },
            ).scalar_one()

            payload = _sale_payload(gross)
            outbox_id = conn.execute(
                text(
                    """
                    INSERT INTO fiscal_outbox (company_id, device_id, kind, source_doc_type,
                        source_doc_id, sequence_no, invc_no, payload, status, attempts, sent_at)
                    VALUES (:cid, :device, 'sale', 'partner_document', :document, :seq,
                            :counter, CAST(:payload AS jsonb), 'sent', 1, :sent)
                    RETURNING id
                    """
                ),
                {
                    "cid": company_id,
                    "device": device_id,
                    "document": documents[counter],
                    "seq": counter,
                    "counter": counter,
                    "payload": json.dumps(payload),
                    "sent": SIGNED_AT[counter],
                },
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO fiscal_receipts (company_id, document_id, device_id, outbox_id,
                        receipt_type, invc_no, rcpt_no, tot_rcpt_no, intrl_data, rcpt_sign,
                        sdc_id, mrc_no, sdc_datetime, qr_payload, request, response, copy_count)
                    VALUES (:cid, :document, :device, :outbox, 'NS', :counter,
                            :counter, :counter, 'INTRL0000000000000000000', 'SIGN000000000000',
                            'SDC010000005', 'WIS01006230', :signed, 'qr',
                            CAST(:request AS jsonb), CAST(:response AS jsonb), 0)
                    """
                ),
                {
                    "cid": company_id,
                    "document": documents[counter],
                    "device": device_id,
                    "outbox": outbox_id,
                    "counter": counter,
                    "signed": SIGNED_AT[counter],
                    "request": json.dumps(payload),
                    "response": json.dumps({"resultCd": "000"}),
                },
            )

        # The legacy Z: taken at `CLOSED_AT`, over the first two receipts, with the figures the
        # pre-0026 computation produced for them and **no** high-water mark, because the column
        # does not exist yet.
        gross = sum(Decimal(amount) for counter, amount in RECEIPTS if counter <= 2)
        tax = sum(
            (Decimal(amount) * Decimal("18") / Decimal("118")).quantize(Decimal("0.01"))
            for counter, amount in RECEIPTS
            if counter <= 2
        )
        figures = {
            "ns_count": 2,
            "ns_gross": str(gross),
            "nr_count": 0,
            "nr_gross": "0.00",
            "net_gross": str(gross),
            "total_tax": str(tax),
            "items_ns": "2",
            "items_nr": "0",
            "copies_count": 0,
            "copies_gross": "0.00",
            "discounts": "0.00",
            "posted_net": str(gross),
            "declared_less_posted": "0.00",
            "queued_rows": 0,
            "classes": {
                "B": {
                    "taxable_ns": str(gross),
                    "tax_ns": str(tax),
                    "taxable_nr": "0.00",
                    "tax_nr": "0.00",
                    "rate": "18.00",
                }
            },
            "by_payment_method": {"cash": str(gross)},
            "refunds_by_payment_method": {},
        }
        conn.execute(
            text(
                """
                INSERT INTO fiscal_daily_reports (company_id, device_id, report_no, number,
                    from_at, to_at, figures, queued_rows, closed_by, closed_at)
                VALUES (:cid, :device, 1, 'Z-000001', :from_at, :to_at,
                        CAST(:figures AS jsonb), 0, :user, :to_at)
                """
            ),
            {
                "cid": company_id,
                "device": device_id,
                "from_at": CLOSED_AT - timedelta(days=1),
                "to_at": CLOSED_AT,
                "figures": json.dumps(figures),
                "user": user_id,
            },
        )
        # The `FZR` run, advanced past the Z that was taken — what a tenant that has closed a
        # day actually has. Branch-scoped, the way device activation creates it (decision 5).
        conn.execute(
            text(
                "INSERT INTO document_sequences (company_id, branch_id, doc_type, prefix, "
                "next_number) VALUES (:cid, :branch, 'FZR', 'Z-', 2)"
            ),
            {"cid": company_id, "branch": branch_id},
        )
    txn_engine.dispose()

    return {
        "company_id": company_id,
        "device_id": device_id,
        "user_id": user_id,
        "figures": figures,
    }


def _session(engine: Engine, company_id: int) -> Session:
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    set_tenant(session, company_id)
    return session


def test_a_legacy_z_keeps_its_figures_and_the_next_one_closes_by_counter(
    pre_0026_engine: Engine,
) -> None:
    """The whole of 0026's back-fill story, in one tenant.

    Three claims, and the third is the one `make migrate-check` could never reach: that the
    code on the far side of the upgrade reads a Z it did not write.
    """
    fixture = _provision(pre_0026_engine)
    company_id = fixture["company_id"]

    url = ADMIN_URL.set(database=BACKFILL_DB)
    _alembic(url.render_as_string(hide_password=False), "head")

    engine = create_engine(url)
    try:
        db = _session(engine, company_id)
        legacy = db.scalars(
            select(FiscalDailyReport).where(
                FiscalDailyReport.company_id == company_id,
                FiscalDailyReport.device_id == fixture["device_id"],
                FiscalDailyReport.report_no == 1,
            )
        ).one()
        device = db.get(FiscalDevice, fixture["device_id"])
        assert device is not None

        # 1. The upgrade did not touch the stored Z. Its mark is NULL, which is what "closed
        #    before counters" looks like, and its figures are byte-for-byte what was written.
        assert legacy.high_water_rcpt_no is None
        assert legacy.figures == fixture["figures"]

        # 2. It still computes what it computed: membership by its own clock range, the two
        #    receipts it was taken over, and the same figures recomputed from them.
        members = daily.membership_of(db, company_id, legacy)
        assert not members.by_counter, "a legacy Z owns a stretch of clock, not a run of counters"
        assert [receipt.tot_rcpt_no for receipt in
                daily.receipts_of(db, company_id, device.id, members)] == [1, 2]
        recomputed = daily.compute(db, company_id, device.id, members=members)
        assert recomputed.ns_count == 2
        assert str(recomputed.ns_gross) == fixture["figures"]["ns_gross"]

        # 3. The open day starts where the legacy Z left off — translated to a counter once, at
        #    the seam — so the third receipt is in it and the first two are not.
        after = daily.x_report(db, company_id, device.id)
        assert after.from_key == 2
        assert after.to_key is None
        assert after.figures.ns_count == 1
        assert str(after.figures.ns_gross) == RECEIPTS[2][1]

        # 4. And the first Z closed after the upgrade takes its mark from the device's counter.
        owner = db.get(User, fixture["user_id"])
        assert owner is not None
        closed = daily.close_day(
            db,
            company_id,
            device.id,
            actor=owner,
            now=datetime.now(UTC),
        )
        db.commit()

        assert closed.report_no == 2
        assert closed.high_water_rcpt_no == 3
        assert closed.figures["ns_count"] == 1
        assert [
            receipt.tot_rcpt_no
            for receipt in daily.receipts_of(
                db, company_id, device.id, daily.membership_of(db, company_id, closed)
            )
        ] == [3]
        db.close()
    finally:
        engine.dispose()
