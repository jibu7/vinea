"""P7 step 1 — fiscalization masters, the outbox schema, settings and the seed pack.

Revision ID: 0022_p7_fiscal
Revises: 0021_p6_landed_cost
Create Date: 2026-09-16

The shape the rest of Phase 7 keys against, and nothing that posts or sends. No adapter call
is made from a migration, no outbox row is written and no receipt exists yet: what lands here
is the vocabulary — devices, the authority's code tables, registered items, the queue, the
receipt, the daily report, the two feeds, the VAT return and the revaluation run — plus the
columns that let a partner document, an item, a unit and a tax code *be* fiscalized.

**The whole phase is built so that a second country is rows and an adapter.** Every column
here spells what Vinea means rather than what one authority's JSON calls it, with three
deliberate exceptions that hold an authority's code *as data* (`fiscal_codes.code`,
`fiscal_items.item_cd`, `tax_codes.fiscal_tax_type`). That is rule 12 in schema form.

**Three tables are immutable by trigger, not by convention** (rule 3's reasoning, one domain
along). A fiscal receipt is a revenue authority's signature over a sale; a filed VAT return is
a declaration; a Z report is the close of a trading day. None of them is a row anybody gets to
edit afterwards, and `VN011` says so from the database. The one exception is written into the
receipt trigger by name: `copy_count` may rise, because a reprint has to stay countable
without making the receipt editable.

**Numbering.** Seven runs enter `DocType` with this revision (`FIS`, `FIP`, `FSAR`, `FITM`,
`FZR`, `VAT`, `FXR`), and they could not have entered it earlier: a claimant naming a table
that does not exist fails `test_every_claimant_names_a_real_table_and_column`. Four of them
number an integer column rather than a formatted string, which is why `SequenceClaimant`
grew `numeric` in the same commit — a fiscal invoice number with a hole in it is a question
from a revenue authority, so leaving those runs out of the gapless check was never an option.

**What the back-fill does**, for a tenant provisioned before P7:

* the five accounts P7 adds to `rw_sme_v1` — `2250` VAT settlement, `1290`/`2190` the
  revaluation contras, `4410`/`6955` unrealized FX — none of which is a control account;
* the five `gl_settings` account keys that point at them;
* the `VAT-IN-IMP` tax code, and `fiscal_tax_type` on the four codes that already existed;
* the full permission list on every system Administrator role.

Each of those fails, without it, at whichever posting or return first needs it rather than at
the upgrade — which is the reason `tests/test_p7_backfill.py` exists and why it provisions a
tenant **with posted rows** before upgrading. `make migrate-check` runs on an empty database
and proves DDL; it cannot prove a back-fill (rule 10).

What the back-fill deliberately does **not** do:

**It seeds no document sequences.** `claim_number` creates a run on demand, and P6's note
still holds — a seeded run sends the gapless checker at a table before anything has claimed
from it. The five branch-scoped runs are created by *device activation*, which is where the
branch is known.

**It marks no existing account as a control account and changes no posted row.** Every account
this revision touches is one it creates, so there is no balance to disturb; `tax_codes`,
`gl_settings` and `roles` are the only existing tables updated, and none of them is posted.

**It assumes `rw_sme_v1` is the only chart-of-accounts template**, exactly as 0012 and 0018
did, and for the same reason: the parents are found by code and those codes are that
template's. Recorded so the second template is a known task rather than a discovery.
"""

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_p7_fiscal"
down_revision = "0021_p6_landed_cost"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 6)
RATE = sa.Numeric(20, 10)
QUANTITY = sa.Numeric(20, 6)

TENANT_TABLES = (
    "fiscal_devices",
    "fiscal_codes",
    "fiscal_item_classes",
    "fiscal_items",
    "fiscal_outbox",
    "fiscal_receipts",
    "fiscal_daily_reports",
    "fiscal_purchase_feed",
    "fiscal_import_declarations",
    "vat_returns",
    "fx_revaluations",
    "fx_revaluation_lines",
)

# (type name, labels). Created outright rather than rebuilt: every one of them is new, so
# there is no existing column to retype and none of 0018's `ALTER TYPE … ADD VALUE` problem.
NEW_ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fiscal_profile", ("vsdc", "osdc")),
    ("fiscal_environment", ("test", "production")),
    ("fiscal_device_status", ("pending", "active", "suspended")),
    (
        "fiscal_outbox_kind",
        (
            "item",
            "sale",
            "refund",
            "purchase",
            "purchase_confirm",
            "stock_io",
            "stock_master",
            "import_update",
        ),
    ),
    (
        "fiscal_outbox_status",
        ("queued", "sending", "sent", "failed", "unknown", "needs_receipt", "cancelled"),
    ),
    ("fiscal_receipt_type", ("NS", "NR")),
    ("fiscal_tax_type", ("A", "B", "C", "D")),
    ("fiscal_item_type_code", ("1", "2", "3")),
    (
        "payment_method",
        ("cash", "credit", "cash_credit", "bank_cheque", "card", "mobile_money", "other"),
    ),
    ("fiscal_feed_decision", ("pending", "accepted", "rejected")),
    ("fiscal_import_status", ("pending", "approved", "rejected")),
    ("vat_return_status", ("posted", "reversed")),
    ("fx_revaluation_status", ("posted", "reversed")),
    ("fx_revaluation_role", ("ar", "ap", "both")),
)


def _enum(name: str) -> postgresql.ENUM:
    """The type as a column reference — created once by `_create_enums`, never by a column."""
    return postgresql.ENUM(name=name, create_type=False)


# (code, name, class, parent code) — added to `rw_sme_v1` by this phase. **No control types.**
# The revaluation accounts sit *beside* `1200`/`2100` precisely because those are
# subledger-only and their balance is Σ open items at booking rates (P4's invariant); making
# the revaluation contras control accounts would only move the problem.
NEW_ACCOUNTS = (
    ("1290", "AR Revaluation", "asset", "1100"),
    ("2190", "AP Revaluation", "liability", "2000"),
    ("2250", "VAT Payable (RRA)", "liability", "2000"),
    ("4410", "Unrealized Foreign Exchange Gain", "income", "4000"),
    ("6955", "Unrealized Foreign Exchange Loss", "expense", "6000"),
)

#: (settings column, account code). All five resolve by code alone, and every account is one
#: this revision creates — so unlike 0012's inventory marking there is no pre-existing balance
#: that could be affected by what these keys start pointing at.
SETTINGS_ACCOUNTS = (
    ("vat_settlement_account_id", "2250"),
    ("ar_revaluation_account_id", "1290"),
    ("ap_revaluation_account_id", "2190"),
    ("unrealized_fx_gain_account_id", "4410"),
    ("unrealized_fx_loss_account_id", "6955"),
)

#: tax code → the class the authority reports a line carrying it under. Seeded rather than
#: derived from `rate_pct`: the class is what the authority computes the tax from, and
#: deriving it would make a future rate change a silent reclassification.
TAX_TYPE_BY_CODE = (
    ("VAT-OUT-18", "B"),
    ("VAT-IN-18", "B"),
    ("VAT-EXEMPT", "A"),
    ("VAT-ZERO", "C"),
)

#: The import-VAT code. Its own code, not a reuse of `VAT-IN-18`, because the VAT return
#: reports imports on their own line: it is input VAT paid at the border rather than to a
#: supplier, and an accountant filing a return has to see the two apart.
#:
#: It carries **no account code**: the back-fill copies the GL account, and the effective-date
#: window, off the tenant's own `VAT-IN-18` row rather than looking `1400` up. A company that
#: pointed its input VAT somewhere else did so on purpose, and imports belong wherever the
#: rest of its input VAT does.
IMPORT_TAX_CODE = {
    "code": "VAT-IN-IMP",
    "name": "Input VAT 18% (Imports)",
    "nature": "input",
    "rate_pct": "18",
    "fiscal_tax_type": "B",
}

NEW_PERMISSIONS = (
    "fiscal:setup_manage",
    "fiscal:queue_manage",
    "fiscal:reports_view",
    "tax:vat_return_view",
    "tax:vat_return_file",
    "gl:fx_revalue",
)

# `app.core.permissions.ALL_PERMISSIONS` frozen as of this revision (architecture rule 10: a
# migration must not drift with the constants it was written against).
ALL_PERMISSIONS_AT_0022 = (
    "users:create",
    "users:read",
    "users:update",
    "users:delete",
    "users:manage_roles",
    "roles:create",
    "roles:read",
    "roles:update",
    "roles:delete",
    "roles:manage_permissions",
    "company:read",
    "company:update",
    "accounting_periods:manage",
    "accounting_periods:reopen",
    "common:setup_currencies",
    "common:setup_taxes",
    "common:setup_branches",
    "gl:setup_manage",
    "gl:journal_post",
    "gl:reports_view",
    "projects:read",
    "projects:manage",
    "ar:setup_manage",
    "ar:transactions_post",
    "ar:reports_view",
    "ar:writeoff_approve",
    "ar:credit_limit_override",
    "ap:setup_manage",
    "ap:transactions_post",
    "ap:reports_view",
    "ap:credit_limit_override",
    "inv:setup_manage",
    "inv:transactions_adjust",
    "inv:reports_view",
    "inv:count_enter",
    "inv:count_process",
    "inv:item_rename",
    "oe:setup_manage",
    "oe:sales_orders_manage",
    "oe:purchase_orders_manage",
    "oe:grv_process",
    "oe:reports_view",
    "oe:landed_cost_post",
    "fiscal:setup_manage",
    "fiscal:queue_manage",
    "fiscal:reports_view",
    "tax:vat_return_view",
    "tax:vat_return_file",
    "gl:fx_revalue",
    "reporting:financial_statements_view",
    "reporting:financial_statements_generate",
    "reporting:templates_manage",
    "reporting:schedules_manage",
    "reporting:bank_reconciliation_manage",
    "reporting:ar_aging_view",
    "reporting:ap_aging_view",
    "reporting:gl_advanced_view",
    "reporting:comparative_analysis",
    "reporting:cash_flow_view",
    "reporting:trial_balance_view",
    "reporting:inventory_valuation_view",
    "reporting:dashboard_view",
    "reporting:export",
    "bom:setup_manage",
    "bom:manufacturing_create",
    "bom:manufacturing_process",
    "bom:reports_view",
    "bom:mrp_run",
    "pos:setup_manage",
    "pos:till_operate",
    "pos:till_manage",
    "pos:sales_create",
    "pos:returns_process",
    "pos:reports_view",
    "pos:reconcile",
)

# --- Immutability (rule 3's reasoning, one domain along) --------------------------------------
#
# `to_jsonb(NEW) - 'col'` rather than a column-by-column comparison, deliberately: a later
# revision that adds a column to one of these tables must not silently fall outside the guard.
# The exempt columns are named here and nowhere else, which is where a reader looks for them.
FISCAL_FUNCTIONS = {
    "fiscal_block_receipt_mutation": """
        CREATE FUNCTION fiscal_block_receipt_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'fiscal receipt % is immutable', OLD.id
                    USING ERRCODE = 'VN011';
            END IF;
            -- A reprint increments `copy_count` and is audited; everything the authority
            -- signed stays exactly as it came back.
            IF (to_jsonb(NEW) - 'copy_count' - 'updated_at' - 'updated_by')
               IS DISTINCT FROM (to_jsonb(OLD) - 'copy_count' - 'updated_at' - 'updated_by')
            THEN
                RAISE EXCEPTION
                    'fiscal receipt % is immutable; only copy_count may change', OLD.id
                    USING ERRCODE = 'VN011';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    "fiscal_block_return_mutation": """
        CREATE FUNCTION fiscal_block_return_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'VAT return % is filed and immutable', OLD.id
                    USING ERRCODE = 'VN011';
            END IF;
            -- A filed return can be *reversed*, which is a status and a reversal entry. The
            -- figures, the range and the high-water mark are what "filed" means and never
            -- move: a late entry lands in the next return, it does not restate this one.
            IF (to_jsonb(NEW) - 'status' - 'reversal_entry_id' - 'updated_at' - 'updated_by')
               IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'reversal_entry_id' - 'updated_at' - 'updated_by')
            THEN
                RAISE EXCEPTION
                    'VAT return % is filed; only its reversal may be recorded', OLD.id
                    USING ERRCODE = 'VN011';
            END IF;
            RETURN NEW;
        END
        $$
    """,
    "fiscal_block_daily_report_mutation": """
        CREATE FUNCTION fiscal_block_daily_report_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'fiscal daily report % is a close and is immutable',
                OLD.id USING ERRCODE = 'VN011';
        END
        $$
    """,
}

FISCAL_TRIGGERS = (
    (
        "trg_fiscal_receipts_immutable",
        "fiscal_receipts",
        "BEFORE UPDATE OR DELETE ON fiscal_receipts FOR EACH ROW "
        "EXECUTE FUNCTION fiscal_block_receipt_mutation()",
    ),
    (
        "trg_vat_returns_immutable",
        "vat_returns",
        "BEFORE UPDATE OR DELETE ON vat_returns FOR EACH ROW "
        "EXECUTE FUNCTION fiscal_block_return_mutation()",
    ),
    (
        "trg_fiscal_daily_reports_immutable",
        "fiscal_daily_reports",
        "BEFORE UPDATE OR DELETE ON fiscal_daily_reports FOR EACH ROW "
        "EXECUTE FUNCTION fiscal_block_daily_report_mutation()",
    ),
)


def _audit_columns() -> list[sa.Column]:
    now = sa.func.now()
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    ]


def _company_column() -> sa.Column:
    return sa.Column(
        "company_id",
        sa.BigInteger(),
        sa.ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _tenant_fk(
    name: str, local: str, table: str, remote: str = "id", ondelete: str = "RESTRICT"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["company_id", local],
        [f"{table}.company_id", f"{table}.{remote}"],
        name=name,
        ondelete=ondelete,
    )


def _create_enums() -> None:
    for name, labels in NEW_ENUMS:
        op.execute(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(label) for label in labels)})")


def _drop_enums() -> None:
    for name, _ in reversed(NEW_ENUMS):
        op.execute(f"DROP TYPE IF EXISTS {name}")


def _create_devices() -> None:
    op.create_table(
        "fiscal_devices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("branch_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _enum("fiscal_profile"), nullable=False),
        sa.Column(
            "environment", _enum("fiscal_environment"), nullable=False, server_default="test"
        ),
        sa.Column("base_url", sa.String(300), nullable=False),
        sa.Column("tin", sa.String(20)),
        sa.Column("bhf_id", sa.String(2), nullable=False, server_default="00"),
        sa.Column("dvc_srl_no", sa.String(100), nullable=False),
        sa.Column("mrc_no", sa.String(30)),
        sa.Column("sdc_id", sa.String(30)),
        sa.Column("dvc_id", sa.String(30)),
        sa.Column("cmc_key", sa.Text()),
        sa.Column("intrl_key", sa.Text()),
        sa.Column("sign_key", sa.Text()),
        sa.Column(
            "status", _enum("fiscal_device_status"), nullable=False, server_default="pending"
        ),
        sa.Column(
            "watermarks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fiscal_devices_company_id_id"),
        sa.UniqueConstraint("company_id", "branch_id", name="uq_fiscal_devices_company_branch"),
        _tenant_fk("fk_fiscal_devices_branch", "branch_id", "branches"),
        sa.CheckConstraint("length(bhf_id) = 2", name="bhf_id_is_two_characters"),
        sa.CheckConstraint(
            "status <> 'active' OR (sdc_id IS NOT NULL AND tin IS NOT NULL)",
            name="active_device_is_initialized",
        ),
    )
    op.create_index("ix_fiscal_devices_company_id", "fiscal_devices", ["company_id"])


def _create_reference_tables() -> None:
    op.create_table(
        "fiscal_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("code_class", sa.String(10), nullable=False),
        sa.Column("code_class_name", sa.String(200)),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.SmallInteger()),
        sa.Column("user_defined_1", sa.String(100)),
        sa.Column("user_defined_2", sa.String(100)),
        sa.Column("user_defined_3", sa.String(100)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "code_class", "code", name="uq_fiscal_codes_company_class_code"
        ),
    )
    op.create_index("ix_fiscal_codes_company_id", "fiscal_codes", ["company_id"])
    op.create_index("ix_fiscal_codes_company_class", "fiscal_codes", ["company_id", "code_class"])

    op.create_table(
        "fiscal_item_classes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("item_cls_cd", sa.String(20), nullable=False),
        sa.Column("item_cls_nm", sa.String(300), nullable=False),
        sa.Column("item_cls_lvl", sa.SmallInteger()),
        sa.Column("tax_ty_cd", sa.String(2)),
        sa.Column("mjr_tg_yn", sa.Boolean()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "item_cls_cd", name="uq_fiscal_item_classes_company_code"
        ),
    )
    op.create_index("ix_fiscal_item_classes_company_id", "fiscal_item_classes", ["company_id"])
    op.create_index(
        "ix_fiscal_item_classes_company_name", "fiscal_item_classes", ["company_id", "item_cls_nm"]
    )

    op.create_table(
        "fiscal_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("item_cd", sa.String(30), nullable=False),
        sa.Column("item_cls_cd", sa.String(20), nullable=False),
        sa.Column("item_ty_cd", _enum("fiscal_item_type_code"), nullable=False),
        sa.Column("orgn_nat_cd", sa.String(2), nullable=False),
        sa.Column("pkg_unit_cd", sa.String(5), nullable=False),
        sa.Column("qty_unit_cd", sa.String(5), nullable=False),
        sa.Column("tax_ty_cd", _enum("fiscal_tax_type"), nullable=False),
        sa.Column("dft_prc", MONEY, nullable=False, server_default="0"),
        sa.Column("bcd", sa.String(50)),
        sa.Column("use_yn", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("last_payload_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "item_id", name="uq_fiscal_items_company_item"),
        sa.UniqueConstraint("company_id", "item_cd", name="uq_fiscal_items_company_item_cd"),
        _tenant_fk("fk_fiscal_items_item", "item_id", "items"),
    )
    op.create_index("ix_fiscal_items_company_id", "fiscal_items", ["company_id"])


def _create_outbox_and_receipts() -> None:
    op.create_table(
        "fiscal_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", _enum("fiscal_outbox_kind"), nullable=False),
        sa.Column("source_doc_type", sa.String(10)),
        sa.Column("source_doc_id", sa.BigInteger()),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("invc_no", sa.Integer()),
        sa.Column("sar_no", sa.Integer()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status", _enum("fiscal_outbox_status"), nullable=False, server_default="queued"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_result_cd", sa.String(10)),
        sa.Column("last_error", sa.Text()),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.BigInteger()),
        sa.Column("resolution_note", sa.Text()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fiscal_outbox_company_id_id"),
        _tenant_fk("fk_fiscal_outbox_device", "device_id", "fiscal_devices"),
        sa.CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )
    op.create_index("ix_fiscal_outbox_company_id", "fiscal_outbox", ["company_id"])
    op.create_index(
        "ix_fiscal_outbox_device_pending",
        "fiscal_outbox",
        ["company_id", "device_id", "sequence_no"],
        postgresql_where=sa.text("status NOT IN ('sent', 'cancelled')"),
    )
    op.create_index(
        "ix_fiscal_outbox_source",
        "fiscal_outbox",
        ["company_id", "source_doc_type", "source_doc_id"],
        postgresql_where=sa.text("source_doc_id IS NOT NULL"),
    )

    op.create_table(
        "fiscal_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("outbox_id", sa.BigInteger(), nullable=False),
        sa.Column("receipt_type", _enum("fiscal_receipt_type"), nullable=False),
        sa.Column("invc_no", sa.Integer(), nullable=False),
        sa.Column("org_invc_no", sa.Integer()),
        sa.Column("rcpt_no", sa.Integer(), nullable=False),
        sa.Column("tot_rcpt_no", sa.Integer(), nullable=False),
        sa.Column("intrl_data", sa.String(100), nullable=False),
        sa.Column("rcpt_sign", sa.String(100), nullable=False),
        sa.Column("sdc_id", sa.String(30), nullable=False),
        sa.Column("mrc_no", sa.String(30)),
        sa.Column("sdc_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qr_payload", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("copy_count", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fiscal_receipts_company_id_id"),
        sa.UniqueConstraint(
            "company_id", "document_id", name="uq_fiscal_receipts_company_document"
        ),
        sa.UniqueConstraint("company_id", "outbox_id", name="uq_fiscal_receipts_company_outbox"),
        _tenant_fk("fk_fiscal_receipts_document", "document_id", "partner_documents"),
        _tenant_fk("fk_fiscal_receipts_device", "device_id", "fiscal_devices"),
        _tenant_fk("fk_fiscal_receipts_outbox", "outbox_id", "fiscal_outbox"),
        sa.CheckConstraint("copy_count >= 0", name="copy_count_not_negative"),
    )
    op.create_index("ix_fiscal_receipts_company_id", "fiscal_receipts", ["company_id"])
    op.create_index(
        "ix_fiscal_receipts_device_counter",
        "fiscal_receipts",
        ["company_id", "device_id", "tot_rcpt_no"],
    )

    op.create_table(
        "fiscal_daily_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("report_no", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("from_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("figures", postgresql.JSONB(), nullable=False),
        sa.Column("queued_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_by", sa.BigInteger()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fiscal_daily_reports_company_id_id"),
        sa.UniqueConstraint(
            "company_id",
            "device_id",
            "report_no",
            name="uq_fiscal_daily_reports_device_number",
        ),
        _tenant_fk("fk_fiscal_daily_reports_device", "device_id", "fiscal_devices"),
        sa.CheckConstraint("to_at > from_at", name="range_is_forward"),
    )
    op.create_index("ix_fiscal_daily_reports_company_id", "fiscal_daily_reports", ["company_id"])


def _create_feeds() -> None:
    op.create_table(
        "fiscal_purchase_feed",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("spplr_tin", sa.String(20), nullable=False),
        sa.Column("spplr_nm", sa.String(200)),
        sa.Column("spplr_bhf_id", sa.String(2)),
        sa.Column("spplr_invc_no", sa.Integer(), nullable=False),
        sa.Column("sales_dt", sa.Date()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("total_taxable_amount", MONEY, nullable=False),
        sa.Column("total_tax_amount", MONEY, nullable=False),
        sa.Column("total_amount", MONEY, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "decision", _enum("fiscal_feed_decision"), nullable=False, server_default="pending"
        ),
        sa.Column("decided_by", sa.BigInteger()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("ap_document_id", sa.BigInteger()),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fiscal_purchase_feed_company_id_id"),
        sa.UniqueConstraint(
            "company_id",
            "device_id",
            "spplr_tin",
            "spplr_invc_no",
            name="uq_fiscal_purchase_feed_supplier_invoice",
        ),
        _tenant_fk("fk_fiscal_purchase_feed_device", "device_id", "fiscal_devices"),
        _tenant_fk(
            "fk_fiscal_purchase_feed_ap_document", "ap_document_id", "partner_documents"
        ),
    )
    op.create_index("ix_fiscal_purchase_feed_company_id", "fiscal_purchase_feed", ["company_id"])

    op.create_table(
        "fiscal_import_declarations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("task_cd", sa.String(30), nullable=False),
        sa.Column("dcl_no", sa.String(50), nullable=False),
        sa.Column("dcl_de", sa.Date()),
        sa.Column("item_seq", sa.Integer(), nullable=False),
        sa.Column("hs_cd", sa.String(30)),
        sa.Column("item_nm", sa.String(300)),
        sa.Column("orgn_nat_cd", sa.String(2)),
        sa.Column("pkg", QUANTITY),
        sa.Column("pkg_unit_cd", sa.String(5)),
        sa.Column("qty", QUANTITY),
        sa.Column("qty_unit_cd", sa.String(5)),
        sa.Column("spplr_nm", sa.String(200)),
        sa.Column("agnt_nm", sa.String(200)),
        sa.Column("invc_fcur_amt", MONEY),
        sa.Column("invc_fcur_cd", sa.String(5)),
        sa.Column("invc_fcur_exc_rt", RATE),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", _enum("fiscal_import_status"), nullable=False, server_default="pending"
        ),
        sa.Column("item_id", sa.BigInteger()),
        sa.Column("decided_by", sa.BigInteger()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        *_audit_columns(),
        sa.UniqueConstraint(
            "company_id", "id", name="uq_fiscal_import_declarations_company_id_id"
        ),
        sa.UniqueConstraint(
            "company_id",
            "device_id",
            "task_cd",
            "dcl_no",
            "item_seq",
            name="uq_fiscal_import_declarations_line",
        ),
        _tenant_fk("fk_fiscal_import_declarations_device", "device_id", "fiscal_devices"),
        _tenant_fk("fk_fiscal_import_declarations_item", "item_id", "items"),
    )
    op.create_index(
        "ix_fiscal_import_declarations_company_id", "fiscal_import_declarations", ["company_id"]
    )


def _create_returns_and_revaluations() -> None:
    op.create_table(
        "vat_returns",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("figures", postgresql.JSONB(), nullable=False),
        sa.Column("high_water_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("reversal_entry_id", sa.BigInteger()),
        sa.Column("output_vat", MONEY, nullable=False),
        sa.Column("input_vat", MONEY, nullable=False),
        sa.Column("net_payable", MONEY, nullable=False),
        sa.Column("status", _enum("vat_return_status"), nullable=False, server_default="posted"),
        sa.Column("filed_by", sa.BigInteger()),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_vat_returns_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_vat_returns_company_number"),
        _tenant_fk("fk_vat_returns_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk("fk_vat_returns_reversal_entry", "reversal_entry_id", "journal_entries"),
        sa.CheckConstraint("period_to >= period_from", name="range_is_forward"),
    )
    op.create_index("ix_vat_returns_company_id", "vat_returns", ["company_id"])
    op.create_index(
        "ix_vat_returns_company_range", "vat_returns", ["company_id", "period_from", "period_to"]
    )

    op.create_table(
        "fx_revaluations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("revaluation_date", sa.Date(), nullable=False),
        sa.Column("role", _enum("fx_revaluation_role"), nullable=False),
        sa.Column("journal_entry_id", sa.BigInteger()),
        sa.Column("mirror_entry_id", sa.BigInteger()),
        sa.Column("reversal_entry_id", sa.BigInteger()),
        sa.Column(
            "status", _enum("fx_revaluation_status"), nullable=False, server_default="posted"
        ),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("idempotency_hash", sa.String(64)),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fx_revaluations_company_id_id"),
        sa.UniqueConstraint("company_id", "number", name="uq_fx_revaluations_company_number"),
        _tenant_fk("fk_fx_revaluations_journal_entry", "journal_entry_id", "journal_entries"),
        _tenant_fk("fk_fx_revaluations_mirror_entry", "mirror_entry_id", "journal_entries"),
        _tenant_fk("fk_fx_revaluations_reversal_entry", "reversal_entry_id", "journal_entries"),
    )
    op.create_index("ix_fx_revaluations_company_id", "fx_revaluations", ["company_id"])
    op.create_index(
        "ix_fx_revaluations_company_date", "fx_revaluations", ["company_id", "revaluation_date"]
    )

    op.create_table(
        "fx_revaluation_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _company_column(),
        sa.Column("revaluation_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("currency_id", sa.BigInteger(), nullable=False),
        sa.Column("open_amount", MONEY, nullable=False),
        sa.Column("booking_rate", RATE, nullable=False),
        sa.Column("carrying_base", MONEY, nullable=False),
        sa.Column("rate_at_date", RATE, nullable=False),
        sa.Column("revalued_base", MONEY, nullable=False),
        sa.Column("difference", MONEY, nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("company_id", "id", name="uq_fx_revaluation_lines_company_id_id"),
        _tenant_fk(
            "fk_fx_revaluation_lines_revaluation",
            "revaluation_id",
            "fx_revaluations",
            ondelete="CASCADE",
        ),
        _tenant_fk("fk_fx_revaluation_lines_document", "document_id", "partner_documents"),
        _tenant_fk("fk_fx_revaluation_lines_currency", "currency_id", "currencies"),
    )
    op.create_index("ix_fx_revaluation_lines_company_id", "fx_revaluation_lines", ["company_id"])
    op.create_index(
        "ix_fx_revaluation_lines_run", "fx_revaluation_lines", ["company_id", "revaluation_id"]
    )


def _extend_existing_tables() -> None:
    # --- partner_documents: how it is paid, who it refunds, what was signed for it ----------
    op.add_column("partner_documents", sa.Column("payment_method", _enum("payment_method")))
    op.add_column("partner_documents", sa.Column("purchase_code", sa.String(6)))
    op.add_column("partner_documents", sa.Column("refund_of_document_id", sa.BigInteger()))
    op.add_column("partner_documents", sa.Column("refund_reason", sa.String(2)))
    op.add_column("partner_documents", sa.Column("fiscal_receipt_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_partner_documents_refund_of_document",
        "partner_documents",
        "partner_documents",
        ["company_id", "refund_of_document_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_partner_documents_fiscal_receipt",
        "partner_documents",
        "fiscal_receipts",
        ["company_id", "fiscal_receipt_id"],
        ["company_id", "id"],
        ondelete="RESTRICT",
    )

    # --- items, uoms, tax_codes: the mapping columns ----------------------------------------
    op.add_column("items", sa.Column("fiscal_class_code", sa.String(20)))
    op.add_column("items", sa.Column("fiscal_origin_country", sa.String(2)))
    op.add_column("items", sa.Column("fiscal_package_unit", sa.String(5)))
    op.add_column("items", sa.Column("fiscal_item_type", _enum("fiscal_item_type_code")))
    op.add_column("uoms", sa.Column("fiscal_quantity_unit", sa.String(5)))
    op.add_column("tax_codes", sa.Column("fiscal_tax_type", _enum("fiscal_tax_type")))

    # --- gl_settings: five accounts and the default purchase class --------------------------
    for column, _ in SETTINGS_ACCOUNTS:
        op.add_column("gl_settings", sa.Column(column, sa.BigInteger()))
        op.create_foreign_key(
            f"fk_gl_settings_{column.removesuffix('_id')}",
            "gl_settings",
            "gl_accounts",
            ["company_id", column],
            ["company_id", "id"],
            ondelete="RESTRICT",
        )
    op.add_column(
        "gl_settings", sa.Column("fiscal_default_purchase_class_code", sa.String(20))
    )


def upgrade() -> None:
    _create_enums()
    _create_devices()
    _create_reference_tables()
    _create_outbox_and_receipts()
    _create_feeds()
    _create_returns_and_revaluations()
    _extend_existing_tables()

    for body in FISCAL_FUNCTIONS.values():
        op.execute(body)
    for name, table, definition in FISCAL_TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {definition}")

    _backfill_existing_tenants()

    # --- Row Level Security (ADR-01) -------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (company_id = app_current_company_id() OR app_platform_mode())
            WITH CHECK (company_id = app_current_company_id() OR app_platform_mode())
            """
        )


def _backfill_existing_tenants() -> None:
    """A tenant provisioned before P7 comes out of `alembic upgrade head` able to fiscalize,
    file a VAT return and revalue.

    The P4–P6 lesson, unchanged: a NULL default here does not fail at migration time, it fails
    at whichever posting first needs it, long after anyone can connect the two.
    """
    for code, name, class_, parent_code in NEW_ACCOUNTS:
        op.execute(
            sa.text(
                """
                INSERT INTO gl_accounts
                    (company_id, code, name, class, parent_id, is_postable, is_control,
                     control_type, is_active)
                SELECT p.company_id, :code, :name, CAST(:class AS account_class), p.id,
                       true, false, NULL, true
                  FROM gl_accounts p
                 WHERE p.code = :parent_code
                   AND NOT EXISTS (
                       SELECT 1 FROM gl_accounts existing
                        WHERE existing.company_id = p.company_id AND existing.code = :code
                   )
                """
            ).bindparams(code=code, name=name, **{"class": class_}, parent_code=parent_code)
        )

    for column, account_code in SETTINGS_ACCOUNTS:
        op.execute(
            sa.text(
                f"""
                UPDATE gl_settings s
                   SET {column} = a.id
                  FROM gl_accounts a
                 WHERE a.company_id = s.company_id
                   AND a.code = :account_code
                   AND s.{column} IS NULL
                """
            ).bindparams(account_code=account_code)
        )

    # The fiscal class of the four codes every tenant already has. Matched on `code` rather
    # than on rate or nature, because the code is the thing the seed pack promised and a rate
    # is a thing a company may legitimately have edited.
    for code, tax_type in TAX_TYPE_BY_CODE:
        op.execute(
            sa.text(
                "UPDATE tax_codes SET fiscal_tax_type = CAST(:tax_type AS fiscal_tax_type) "
                "WHERE code = :code AND fiscal_tax_type IS NULL"
            ).bindparams(code=code, tax_type=tax_type)
        )

    # Import VAT, for every tenant whose VAT-input account exists. `valid_from` is taken from
    # the tenant's own input code so the new one is effective over the same window — a code
    # dated from today would be refused by `resolve_tax_code` on a back-dated import.
    op.execute(
        sa.text(
            """
            INSERT INTO tax_codes
                (company_id, code, name, nature, rate_pct, gl_account_id, valid_from,
                 is_active, fiscal_tax_type)
            SELECT existing.company_id, :code, :name, CAST(:nature AS tax_nature),
                   CAST(:rate_pct AS numeric), existing.gl_account_id, existing.valid_from,
                   true, CAST(:fiscal_tax_type AS fiscal_tax_type)
              FROM tax_codes existing
             WHERE existing.code = 'VAT-IN-18'
               AND NOT EXISTS (
                   SELECT 1 FROM tax_codes duplicate
                    WHERE duplicate.company_id = existing.company_id
                      AND duplicate.code = :code
               )
            """
        ).bindparams(**IMPORT_TAX_CODE)
    )

    _backfill_administrator_permissions()


def _backfill_administrator_permissions() -> None:
    """The Administrator role stores its permissions as *data*, so a role seeded before this
    revision has none of P7's six and no amount of application code will give it one. The
    union goes in, not just this phase's addition — a tenant several phases old may be missing
    more than one."""
    op.execute(
        sa.text(
            """
            UPDATE roles r
               SET permissions = r.permissions || (
                   SELECT COALESCE(jsonb_agg(missing.value), '[]'::jsonb)
                     FROM jsonb_array_elements(CAST(:all_permissions AS jsonb))
                          AS missing(value)
                    WHERE NOT (r.permissions @> jsonb_build_array(missing.value))
               )
             WHERE r.is_system AND r.name = 'Administrator'
               AND NOT (r.permissions @> CAST(:all_permissions AS jsonb))
            """
        ).bindparams(all_permissions=json.dumps(list(ALL_PERMISSIONS_AT_0022)))
    )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for name, table, _ in FISCAL_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for name in FISCAL_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")

    for permission in NEW_PERMISSIONS:
        op.execute(
            sa.text(
                "UPDATE roles SET permissions = permissions - CAST(:permission AS text) "
                "WHERE is_system AND name = 'Administrator'"
            ).bindparams(permission=permission)
        )
    op.execute(sa.text("DELETE FROM tax_codes WHERE code = :code").bindparams(code="VAT-IN-IMP"))

    op.drop_column("gl_settings", "fiscal_default_purchase_class_code")
    for column, _ in SETTINGS_ACCOUNTS:
        op.drop_constraint(
            f"fk_gl_settings_{column.removesuffix('_id')}", "gl_settings", type_="foreignkey"
        )
        op.drop_column("gl_settings", column)
    op.drop_column("tax_codes", "fiscal_tax_type")
    op.drop_column("uoms", "fiscal_quantity_unit")
    for column in (
        "fiscal_item_type",
        "fiscal_package_unit",
        "fiscal_origin_country",
        "fiscal_class_code",
    ):
        op.drop_column("items", column)
    op.drop_constraint(
        "fk_partner_documents_fiscal_receipt", "partner_documents", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_partner_documents_refund_of_document", "partner_documents", type_="foreignkey"
    )
    for column in (
        "fiscal_receipt_id",
        "refund_reason",
        "refund_of_document_id",
        "purchase_code",
        "payment_method",
    ):
        op.drop_column("partner_documents", column)

    for table in (
        "fx_revaluation_lines",
        "fx_revaluations",
        "vat_returns",
        "fiscal_import_declarations",
        "fiscal_purchase_feed",
        "fiscal_daily_reports",
        "fiscal_receipts",
        "fiscal_outbox",
        "fiscal_items",
        "fiscal_item_classes",
        "fiscal_codes",
        "fiscal_devices",
    ):
        op.drop_table(table)

    op.execute(
        sa.text("DELETE FROM gl_accounts WHERE code = ANY(:codes)").bindparams(
            codes=[code for code, *_ in NEW_ACCOUNTS]
        )
    )
    _drop_enums()
