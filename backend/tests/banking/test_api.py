"""The banking API, over HTTP.

The screens arrive at steps 6 and 7; these tests are what stands in for them until then, and
they exist for the reason `test_api_has_a_caller.py` exists — an endpoint nobody has driven is
a capability the product does not have. Each mutating route here carries a `GAP (P8, step N)`
line in that register, and this file is what proves the route works before the screen that
deletes the line is written.

The multipart upload in particular has no other cover: the service-level tests call
`import_statement` with bytes, and the only place `python-multipart`, the form fields and the
`Decimal` parsing of the two keyed balances are exercised is here.
"""

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.banking.statements import PREVIEW_ROWS
from tests.banking.conftest import REAL_SAMPLES, sample

OWNER = {
    "company_name": "Rugari Wines Ltd",
    "full_name": "Aline Uwase",
    "email": "owner@rugari.example",
    "password": "correct horse battery staple",
}

SEPTEMBER = "generic-bk-rwf-sep.csv"


def _signup(client: TestClient) -> None:
    assert client.post("/api/v1/auth/signup", json=OWNER).status_code in (200, 201)


def test_the_seeded_bank_and_cash_accounts_are_listed(client: TestClient) -> None:
    _signup(client)

    rows = client.get("/api/v1/banking/accounts").json()

    assert sorted(row["code"] for row in rows) == ["1110", "1120"]
    assert {row["kind"] for row in rows} == {"bank", "cash"}
    assert client.get("/api/v1/banking/accounts/unregistered").json() == []


def test_creating_a_bank_control_account_creates_its_master_row(client: TestClient) -> None:
    """The hook on the ordinary create path, over HTTP — the claim clause 6 exists to catch."""
    _signup(client)
    parent = next(
        account
        for account in client.get("/api/v1/gl/accounts").json()
        if account["code"] == "1100"
    )

    created = client.post(
        "/api/v1/gl/accounts",
        json={
            "code": "1121",
            "name": "Bank Account USD",
            "class": "asset",
            "parent_id": parent["id"],
            "control_type": "bank",
        },
    )
    assert created.status_code == 201

    rows = client.get("/api/v1/banking/accounts").json()
    usd = next(row for row in rows if row["code"] == "1121")
    assert usd["gl_account_id"] == created.json()["id"]
    assert usd["kind"] == "bank"


def test_registering_sets_the_currency_and_the_bank_details(client: TestClient) -> None:
    _signup(client)
    account = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )
    currencies = {row["code"]: row["id"] for row in client.get("/api/v1/gl/currencies").json()}

    patched = client.patch(
        f"/api/v1/banking/accounts/{account['id']}",
        json={
            "code": "BK-RWF",
            "name": "Bank of Kigali current account",
            "bank_name": "Bank of Kigali",
            "account_number": "00040-0000123-45",
            "currency_id": currencies["RWF"],
            "statement_format": {"preset": "generic"},
        },
    )

    assert patched.status_code == 200
    body = patched.json()
    assert body["code"] == "BK-RWF"
    assert body["account_number"] == "00040-0000123-45"
    assert body["statement_format"]["preset"] == "generic"


def test_register_fills_in_the_row_the_hook_made(client: TestClient) -> None:
    """`POST /banking/accounts` over HTTP — the create half of step 6's Bank accounts screen.

    Registering an account the hook already covered is the normal case, not an error: the hook
    makes the row in the base currency at the moment the GL account is created, and Register is
    what gives it its real currency and the bank's own details.
    """
    _signup(client)
    parent = next(
        account for account in client.get("/api/v1/gl/accounts").json()
        if account["code"] == "1100"
    )
    gl = client.post(
        "/api/v1/gl/accounts",
        json={
            "code": "1121",
            "name": "Bank Account USD",
            "class": "asset",
            "parent_id": parent["id"],
            "control_type": "bank",
        },
    ).json()
    currencies = {row["code"]: row["id"] for row in client.get("/api/v1/gl/currencies").json()}

    registered = client.post(
        "/api/v1/banking/accounts",
        json={
            "gl_account_id": gl["id"],
            "code": "BK-USD",
            "name": "Bank of Kigali USD account",
            "currency_id": currencies["USD"],
            "account_number": "00040-0000999-11",
        },
    )

    assert registered.status_code == 201
    body = registered.json()
    assert (body["code"], body["currency_id"]) == ("BK-USD", currencies["USD"])
    assert body["account_number"] == "00040-0000999-11"

    # A second Register that mentions only the name leaves the account number alone. A register
    # call that blanked whatever it did not mention would quietly erase what the last one set.
    again = client.post(
        "/api/v1/banking/accounts",
        json={"gl_account_id": gl["id"], "name": "BK USD (main)"},
    )
    assert again.status_code == 201
    assert again.json()["name"] == "BK USD (main)"
    assert again.json()["account_number"] == "00040-0000999-11"


def test_a_line_in_the_wrong_currency_is_refused_by_the_api(client: TestClient) -> None:
    """Decision 2's one-sided rule, over HTTP: the refusal a screen shows, with the field error
    on `currency_id`. `1121` is held in USD, so an RWF cashbook receipt onto it is refused."""
    _signup(client)
    accounts = {account["code"]: account for account in client.get("/api/v1/gl/accounts").json()}
    gl = client.post(
        "/api/v1/gl/accounts",
        json={
            "code": "1121",
            "name": "Bank Account USD",
            "class": "asset",
            "parent_id": accounts["1100"]["id"],
            "control_type": "bank",
        },
    ).json()
    currencies = {row["code"]: row["id"] for row in client.get("/api/v1/gl/currencies").json()}
    bank = client.post(
        "/api/v1/banking/accounts",
        json={"gl_account_id": gl["id"], "code": "BK-USD", "currency_id": currencies["USD"]},
    ).json()
    assert bank["currency_id"] == currencies["USD"]

    refused = client.post(
        "/api/v1/gl/cashbook-entries",
        json={
            "entry_date": "2026-09-15",
            "description": "opening",
            "cash_account_id": gl["id"],
            "kind": "receipt",
            "currency_id": currencies["RWF"],
            "lines": [{"gl_account_id": accounts["3400"]["id"], "amount": "1000"}],
        },
        headers={"Idempotency-Key": "cb-1"},
    )

    assert refused.status_code == 422
    assert refused.json()["code"] == "bank_account_currency_mismatch"
    assert "USD" in str(refused.json()["field_errors"])


def test_a_cash_account_is_refused_a_statement_format(client: TestClient) -> None:
    _signup(client)
    cash = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1110"
    )

    refused = client.patch(
        f"/api/v1/banking/accounts/{cash['id']}",
        json={"statement_format": {"preset": "generic"}},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "cash_account_has_no_format"


def test_a_rule_is_created_and_listed(client: TestClient) -> None:
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )
    charges = next(
        account
        for account in client.get("/api/v1/gl/accounts").json()
        if account["code"] == "6700"
    )

    created = client.post(
        f"/api/v1/banking/accounts/{bank['id']}/rules",
        json={
            "pattern": "ACCOUNT FEE",
            "gl_account_id": charges["id"],
            "description": "Monthly account fee",
            "priority": 10,
        },
    )
    assert created.status_code == 201

    rules = client.get(f"/api/v1/banking/accounts/{bank['id']}/rules").json()
    assert [rule["pattern"] for rule in rules] == ["ACCOUNT FEE"]

    patched = client.patch(
        f"/api/v1/banking/rules/{created.json()['id']}",
        json={"pattern": "MONTHLY ACCOUNT FEE", "priority": 5},
    )
    assert patched.status_code == 200
    assert patched.json()["pattern"] == "MONTHLY ACCOUNT FEE"


def _currencies(client: TestClient) -> dict[str, int]:
    return {row["code"]: row["id"] for row in client.get("/api/v1/gl/currencies").json()}


def _gl(client: TestClient) -> dict[str, dict]:
    return {account["code"]: account for account in client.get("/api/v1/gl/accounts").json()}


def test_create_makes_the_gl_account_and_its_master_in_one_call(client: TestClient) -> None:
    """Step 6's *New bank account*: the pair in one request, the GL half shaped by the kind.

    Asset, postable, flagged `bank` — none of it asked for, because none of it is a choice for
    a bank account. The master comes back in the currency asked for, not the base currency the
    hook would have chosen, because the currency is set in the same transaction."""
    _signup(client)
    gl = _gl(client)

    created = client.post(
        "/api/v1/banking/accounts",
        json={
            "new_account": {
                "code": "1121",
                "name": "Bank Account USD",
                "kind": "bank",
                "parent_id": gl["1100"]["id"],
            },
            "code": "BK-USD",
            "currency_id": _currencies(client)["USD"],
            "bank_name": "Bank of Kigali",
        },
    )

    assert created.status_code == 201, created.json()
    body = created.json()
    account = _gl(client)["1121"]
    assert body["gl_account_id"] == account["id"]
    assert (account["class"], account["control_type"], account["is_postable"]) == (
        "asset",
        "bank",
        True,
    )
    assert (body["code"], body["kind"], body["currency_id"]) == (
        "BK-USD",
        "bank",
        _currencies(client)["USD"],
    )
    assert body["has_lines"] is False
    assert client.get("/api/v1/banking/accounts/unregistered").json() == []


def test_create_is_all_or_nothing(client: TestClient) -> None:
    """A refusal on the banking half leaves no GL account behind. `1110` is the cash account's
    master code, so the master cannot take it — and the `1199` GL account the same call would
    have made must not survive as an orphan control account with no row."""
    _signup(client)

    refused = client.post(
        "/api/v1/banking/accounts",
        json={
            "new_account": {"code": "1199", "name": "Second cash box", "kind": "cash"},
            "code": "1110",
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "bank_account_code_taken"
    assert "1199" not in _gl(client)


def test_create_needs_exactly_one_target(client: TestClient) -> None:
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )

    neither = client.post("/api/v1/banking/accounts", json={"code": "X"})
    both = client.post(
        "/api/v1/banking/accounts",
        json={
            "gl_account_id": bank["gl_account_id"],
            "new_account": {"code": "1122", "name": "Other", "kind": "bank"},
        },
    )

    for response in (neither, both):
        assert response.status_code == 409
        assert response.json()["code"] == "bank_account_target_ambiguous"


def test_create_needs_the_chart_permission_as_well(client: TestClient, db) -> None:
    """Creating the pair makes a chart-of-accounts row, so `bank:setup_manage` alone is not
    enough. No seeded role splits the two — and an owner holds everything — so the signed-up
    user is made a plain member holding an Administrator role with the chart permission
    removed, which is the custom role this refusal exists for."""
    from sqlalchemy import select

    from app.db import set_tenant
    from app.models.membership import CompanyMembership, Role

    _signup(client)
    company_id = client.get("/api/v1/auth/me").json()["company"]["id"]
    set_tenant(db, company_id)
    admin = db.scalar(
        select(Role).where(Role.company_id == company_id, Role.name == "Administrator")
    )
    admin.permissions = [p for p in admin.permissions if p != "gl:setup_manage"]
    assert "bank:setup_manage" in admin.permissions
    membership = db.scalar(
        select(CompanyMembership).where(CompanyMembership.company_id == company_id)
    )
    membership.is_owner = False
    membership.roles = [admin]
    db.commit()

    refused = client.post(
        "/api/v1/banking/accounts",
        json={"new_account": {"code": "1122", "name": "Other bank", "kind": "bank"}},
    )

    assert refused.status_code == 403
    assert "1122" not in _gl(client)


def test_the_currency_locks_once_the_account_has_lines(client: TestClient) -> None:
    """`has_lines` is what the screen reads to lock the picker; `bank_account_has_lines` is the
    refusal it is locking against. Both, over HTTP, before and after the first posting."""
    _signup(client)
    gl = _gl(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )
    assert bank["has_lines"] is False

    posted = client.post(
        "/api/v1/gl/cashbook-entries",
        json={
            "entry_date": "2026-09-15",
            "description": "opening",
            "cash_account_id": gl["1120"]["id"],
            "kind": "receipt",
            "lines": [{"gl_account_id": gl["3400"]["id"], "amount": "1000"}],
        },
        headers={"Idempotency-Key": "cb-lock-1"},
    )
    assert posted.status_code == 201, posted.json()

    after = client.get(f"/api/v1/banking/accounts/{bank['id']}").json()
    assert after["has_lines"] is True
    refused = client.patch(
        f"/api/v1/banking/accounts/{bank['id']}",
        json={"currency_id": _currencies(client)["USD"]},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "bank_account_has_lines"
    assert "currency_id" in refused.json()["field_errors"]


def test_test_with_a_file_previews_under_the_mapping_being_edited(client: TestClient) -> None:
    """The format editor's *Test with a file*: the mapping on the form, not the one stored.

    The September sample is ISO-dated. Read under a `DD/MM/YYYY` mapping every data row fails,
    each with its own row number counted over the file; read under the stored (generic) one,
    nothing does. The account's stored format is unchanged either way — a preview writes
    nothing, including the mapping it was asked to try."""
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )
    content = sample(SEPTEMBER)

    tried = client.post(
        "/api/v1/banking/statements/preview",
        data={
            "bank_account_id": bank["id"],
            "statement_format": '{"preset": "custom", "date_format": "%d/%m/%Y"}',
        },
        files={"file": (SEPTEMBER, content, "text/csv")},
    )
    assert tried.status_code == 200, tried.json()
    errors = tried.json()["errors"]
    assert [error["row"] for error in errors] == [2, 3, 4, 5, 6, 7]
    assert {error["column"] for error in errors} == {"date_column"}

    stored = client.post(
        "/api/v1/banking/statements/preview",
        data={"bank_account_id": bank["id"]},
        files={"file": (SEPTEMBER, content, "text/csv")},
    )
    assert stored.json()["errors"] == []
    assert client.get(f"/api/v1/banking/accounts/{bank['id']}").json()["statement_format"] is None


def test_a_half_written_mapping_comes_back_as_a_refusal_not_a_500(client: TestClient) -> None:
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )

    for mapping in ('{"amount_mode": "signed"}', "{not json", '["a list"]'):
        refused = client.post(
            "/api/v1/banking/statements/preview",
            data={"bank_account_id": bank["id"], "statement_format": mapping},
            files={"file": (SEPTEMBER, sample(SEPTEMBER), "text/csv")},
        )
        assert refused.status_code == 422, mapping
        assert refused.json()["code"] == "statement_format_invalid"


def test_the_upload_previews_then_imports_then_refuses_the_same_file(
    client: TestClient,
) -> None:
    """The whole import path over multipart, which nothing else exercises: the form fields,
    the file bytes and the keyed balances arriving as strings and parsed as `Decimal`."""
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )
    content = sample(SEPTEMBER)

    preview = client.post(
        "/api/v1/banking/statements/preview",
        data={"bank_account_id": bank["id"]},
        files={"file": (SEPTEMBER, content, "text/csv")},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert (body["line_count"], body["new_count"], body["skipped_count"]) == (6, 6, 0)
    assert (body["opening_balance"], body["closing_balance"]) == ("1000000", "1090500")
    assert body["errors"] == []

    imported = client.post(
        "/api/v1/banking/statements",
        data={"bank_account_id": bank["id"]},
        files={"file": (SEPTEMBER, content, "text/csv")},
        headers={"Idempotency-Key": "bst-1"},
    )
    assert imported.status_code == 201
    result = imported.json()
    assert result["statement"]["number"] == "BST-000001"
    assert (result["new_count"], result["skipped_count"]) == (6, 0)

    again = client.post(
        "/api/v1/banking/statements",
        data={"bank_account_id": bank["id"]},
        files={"file": (SEPTEMBER, content, "text/csv")},
        headers={"Idempotency-Key": "bst-2"},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "statement_already_imported"


def test_the_detail_carries_its_lines_and_the_void_empties_the_listing(
    client: TestClient,
) -> None:
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )
    imported = client.post(
        "/api/v1/banking/statements",
        data={"bank_account_id": bank["id"]},
        files={"file": (SEPTEMBER, sample(SEPTEMBER), "text/csv")},
        headers={"Idempotency-Key": "bst-1"},
    ).json()
    statement_id = imported["statement"]["id"]

    detail = client.get(f"/api/v1/banking/statements/{statement_id}").json()
    assert len(detail["lines"]) == 6
    # Credit positive on the wire exactly as in the database — a screen that flipped the sign
    # for display would be the second sign convention this phase exists to avoid.
    assert detail["lines"][0]["amount"] == "118000.000000"
    assert detail["lines"][2]["amount"] == "-384000.000000"

    voided = client.post(
        f"/api/v1/banking/statements/{statement_id}/void", json={"reason": "wrong account"}
    )
    assert voided.status_code == 200
    assert voided.json()["status"] == "void"
    assert client.get("/api/v1/banking/statements").json() == []


def test_a_manual_statement_is_keyed_over_the_api(client: TestClient) -> None:
    _signup(client)
    bank = next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )

    created = client.post(
        "/api/v1/banking/statements/manual",
        json={
            "bank_account_id": bank["id"],
            "opening_balance": "0",
            "closing_balance": "90",
            "lines": [
                {"value_date": "2026-09-03", "description": "DEPOSIT", "amount": "100"},
                {"value_date": "2026-09-04", "description": "FEE", "amount": "-10"},
            ],
        },
        headers={"Idempotency-Key": "manual-1"},
    )

    assert created.status_code == 201
    assert created.json()["statement"]["source"] == "manual"
    assert created.json()["new_count"] == 2


def test_the_seed_pack_points_the_three_banking_keys_at_their_accounts(
    client: TestClient,
) -> None:
    """Read off `GET /gl/settings`, which is what the Defaults screen reads at step 6. A key
    left NULL does not fail here — it fails at the first month-end revaluation, which is the
    reason the seed sets it and the reason this asserts it."""
    _signup(client)
    settings = client.get("/api/v1/gl/settings").json()
    codes = {
        account["id"]: account["code"] for account in client.get("/api/v1/gl/accounts").json()
    }

    assert codes[settings["bank_revaluation_account_id"]] == "1130"
    assert codes[settings["bank_charges_account_id"]] == "6700"
    assert codes[settings["bank_interest_account_id"]] == "4300"


def test_the_defaults_screen_can_move_the_bank_revaluation_account(client: TestClient) -> None:
    """The three keys are settable, and the class rule holds: `1130` is an asset, and pointing
    the revaluation contra at an expense account is refused before it can post."""
    _signup(client)
    accounts = {account["code"]: account for account in client.get("/api/v1/gl/accounts").json()}

    refused = client.put(
        "/api/v1/gl/settings", json={"bank_revaluation_account_id": accounts["6700"]["id"]}
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "invalid_gl_setting_account_class"

    moved = client.put(
        "/api/v1/gl/settings", json={"bank_charges_account_id": accounts["6990"]["id"]}
    )
    assert moved.status_code == 200
    assert moved.json()["bank_charges_account_id"] == accounts["6990"]["id"]


# --- The workspace, over HTTP (P8 step 2) -------------------------------------------------
#
# Nine endpoints the reconciliation screen will drive at step 7. Until then these are what
# stands in for it, for the reason the register exists: an endpoint nobody has driven is a
# capability the product does not have.


def test_a_real_bpr_export_previews_through_the_mapping_stored_on_its_account(
    client: TestClient,
) -> None:
    """Precondition (d) over HTTP: `bpr.format.json` saved on the account through the same
    PATCH the format editor sends, then June 2025 previewed with no mapping in the form — so
    the figures are the stored mapping's, `empty_description: reference` included, and the
    22 description-less fee lines are rows rather than errors."""
    _signup(client)
    bank = _bank(client)
    mapping = json.loads((REAL_SAMPLES / "bpr.format.json").read_text())
    saved = client.patch(
        f"/api/v1/banking/accounts/{bank['id']}", json={"statement_format": mapping}
    )
    assert saved.status_code == 200

    preview = client.post(
        "/api/v1/banking/statements/preview",
        data={"bank_account_id": bank["id"]},
        files={
            "file": ("bpr-2025-06.csv", (REAL_SAMPLES / "bpr-2025-06.csv").read_bytes(), "text/csv")
        },
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["errors"] == []
    assert (body["line_count"], body["new_count"], body["skipped_count"]) == (45, 45, 0)
    # The rows handed back stop at `PREVIEW_ROWS`; the counts and the balances are the file's.
    assert len(body["lines"]) == PREVIEW_ROWS
    assert (Decimal(body["opening_balance"]), Decimal(body["closing_balance"])) == (
        Decimal("2408456.00"),
        Decimal("4274862.00"),
    )


def _bank(client: TestClient) -> dict:
    return next(
        row for row in client.get("/api/v1/banking/accounts").json() if row["code"] == "1120"
    )


def _cashbook(client: TestClient, *, amount: str, on: str, kind: str, key: str) -> dict:
    accounts = {row["code"]: row for row in client.get("/api/v1/gl/accounts").json()}
    posted = client.post(
        "/api/v1/gl/cashbook-entries",
        json={
            "entry_date": on,
            "description": "opening",
            "cash_account_id": accounts["1120"]["id"],
            "kind": kind,
            "lines": [{"gl_account_id": accounts["3400"]["id"], "amount": amount}],
        },
        headers={"Idempotency-Key": key},
    )
    assert posted.status_code in (200, 201), posted.json()
    return posted.json()


def _bank_line_id(entry: dict, gl_account_id: int) -> int:
    return next(
        line["id"] for line in entry["lines"] if line["gl_account_id"] == gl_account_id
    )


def test_the_workspace_ticks_locks_and_reopens_over_http(client: TestClient) -> None:
    """Paper mode end to end: post, tick, open, lock at zero, reopen. The figures come back on
    every call, which is what the strip renders."""
    _signup(client)
    bank = _bank(client)
    entry = _cashbook(client, amount="1000", on="2026-09-03", kind="receipt", key="cb-1")
    line_id = _bank_line_id(entry, bank["gl_account_id"])

    ticked = client.post(
        "/api/v1/banking/matches/tick",
        json={"bank_account_id": bank["id"], "journal_line_ids": [line_id]},
    )
    assert ticked.status_code == 201
    assert ticked.json()["rule"] == "tick"
    assert ticked.json()["journal_line_ids"] == [line_id]

    opened = client.post(
        "/api/v1/banking/reconciliations",
        json={
            "bank_account_id": bank["id"],
            "reconciliation_date": "2026-09-30",
            "statement_balance": "1000",
        },
        headers={"Idempotency-Key": "brc-1"},
    )
    assert opened.status_code == 201
    figures = opened.json()["figures"]
    # `ledger_balance` is summed out of `NUMERIC(20,6)` columns and carries their scale; the
    # other two are computed from it, and an empty sum is plain `Decimal(0)`. The scale on the
    # wire is not the presentation — `formatMoney` renders to the currency's own decimals — so
    # the assertions below are on the *values*, spelled as each one actually arrives.
    assert figures["ledger_balance"] == "1000.000000"
    assert Decimal(figures["outstanding_total"]) == Decimal(0)
    assert Decimal(figures["difference"]) == Decimal(0)

    locked = client.post(
        f"/api/v1/banking/reconciliations/{opened.json()['id']}/lock",
        json={},
        headers={"Idempotency-Key": "lock-1"},
    )
    assert locked.status_code == 200
    assert locked.json()["status"] == "locked"
    assert locked.json()["number"] == "BRC-000001"
    # A locked one carries both figure sets: what it said, and what today computes.
    assert locked.json()["stored"]["ledger_balance"] == "1000.000000"

    reopened = client.post(
        f"/api/v1/banking/reconciliations/{opened.json()['id']}/reopen",
        json={"reason": "the bank restated a fee"},
        headers={"Idempotency-Key": "reopen-1"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["stored"] is None

    # The replay after a dropped response returns the reopened row, rather than refusing
    # `reconciliation_not_locked` over the reopen it is a copy of. A different key is a new
    # request, and that one is refused.
    replayed = client.post(
        f"/api/v1/banking/reconciliations/{opened.json()['id']}/reopen",
        json={"reason": "the bank restated a fee"},
        headers={"Idempotency-Key": "reopen-1"},
    )
    assert replayed.status_code == 200
    assert replayed.json()["status"] == "open"
    fresh = client.post(
        f"/api/v1/banking/reconciliations/{opened.json()['id']}/reopen",
        json={"reason": "the bank restated a fee"},
        headers={"Idempotency-Key": "reopen-2"},
    )
    assert fresh.status_code == 409
    assert fresh.json()["code"] == "reconciliation_not_locked"


def test_reopen_needs_an_idempotency_key(client: TestClient) -> None:
    """Decision 11 names reopen among the calls that carry one; open and lock always did."""
    _signup(client)
    refused = client.post(
        "/api/v1/banking/reconciliations/1/reopen", json={"reason": "no key"}
    )
    assert refused.status_code == 422


def test_the_left_pane_lists_unmatched_lines_first_with_their_match(
    client: TestClient,
) -> None:
    """The workspace's statement pane: every live line on the account, what is left to do
    first, and a matched line carrying its match. A voided statement's lines are gone."""
    _signup(client)
    bank = _bank(client)
    entry = _cashbook(client, amount="59000", on="2026-09-03", kind="receipt", key="cb-1")
    line_id = _bank_line_id(entry, bank["gl_account_id"])
    keyed = client.post(
        "/api/v1/banking/statements/manual",
        json={
            "bank_account_id": bank["id"],
            "opening_balance": "0",
            "closing_balance": "56500",
            "lines": [
                {
                    "value_date": "2026-09-05",
                    "description": "MOMO DEPOSIT 0788",
                    "amount": "59000",
                    "balance_after": "59000",
                },
                {
                    "value_date": "2026-09-06",
                    "description": "MONTHLY ACCOUNT FEE",
                    "amount": "-2500",
                    "balance_after": "56500",
                },
                {
                    "value_date": "2026-10-02",
                    "description": "AFTER THE DATE",
                    "amount": "-100",
                    "balance_after": "56400",
                },
            ],
        },
        headers={"Idempotency-Key": "manual-1"},
    )
    assert keyed.status_code == 201, keyed.json()
    first = client.get(
        f"/api/v1/banking/accounts/{bank['id']}/statement-lines",
        params={"on_or_before": "2026-09-30"},
    ).json()
    momo = next(line for line in first if line["description"] == "MOMO DEPOSIT 0788")
    matched = client.post(
        "/api/v1/banking/matches",
        json={
            "bank_account_id": bank["id"],
            "statement_line_ids": [momo["id"]],
            "journal_line_ids": [line_id],
        },
    )
    assert matched.status_code == 201

    pane = client.get(
        f"/api/v1/banking/accounts/{bank['id']}/statement-lines",
        params={"on_or_before": "2026-09-30"},
    ).json()
    # The October line is after the date and not in the pane; the fee is unmatched and first.
    assert [line["description"] for line in pane] == ["MONTHLY ACCOUNT FEE", "MOMO DEPOSIT 0788"]
    assert pane[0]["state"]["match_id"] is None
    assert pane[1]["state"]["match_id"] == matched.json()["id"]
    assert pane[1]["state"]["match_rule"] == "manual"
    assert pane[1]["state"]["journal_line_count"] == 1
    assert len(client.get(f"/api/v1/banking/accounts/{bank['id']}/statement-lines").json()) == 3


def test_the_new_dialog_reads_the_balance_an_empty_field_would_get(client: TestClient) -> None:
    """The default *New reconciliation* shows is `open_reconciliation`'s own fallback."""
    _signup(client)
    bank = _bank(client)
    url = f"/api/v1/banking/accounts/{bank['id']}/default-statement-balance"
    assert client.get(url, params={"on": "2026-09-30"}).json() == {"statement_balance": None}
    client.post(
        "/api/v1/banking/statements/manual",
        json={
            "bank_account_id": bank["id"],
            "opening_balance": "0",
            "closing_balance": "900",
            "lines": [
                {"value_date": "2026-09-05", "description": "IN", "amount": "1000",
                 "balance_after": "1000"},
                {"value_date": "2026-10-05", "description": "OUT", "amount": "-100",
                 "balance_after": "900"},
            ],
        },
        headers={"Idempotency-Key": "manual-1"},
    )
    assert Decimal(client.get(url, params={"on": "2026-09-30"}).json()["statement_balance"]) == 1000
    assert Decimal(client.get(url, params={"on": "2026-10-31"}).json()["statement_balance"]) == 900


def test_a_lock_is_refused_over_http_with_the_figure_in_the_envelope(
    client: TestClient,
) -> None:
    """Both refusals reach the screen as `{code, message, field_errors}` with their figure in
    them — which is what lets the workspace show them *before* the button."""
    _signup(client)
    bank = _bank(client)
    _cashbook(client, amount="1000", on="2026-09-03", kind="receipt", key="cb-1")

    opened = client.post(
        "/api/v1/banking/reconciliations",
        json={
            "bank_account_id": bank["id"],
            "reconciliation_date": "2026-09-30",
            "statement_balance": "1000",
        },
        headers={"Idempotency-Key": "brc-1"},
    ).json()

    refused = client.post(
        f"/api/v1/banking/reconciliations/{opened['id']}/lock",
        json={},
        headers={"Idempotency-Key": "lock-1"},
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "reconciliation_difference"
    assert "+1000" in refused.json()["field_errors"]["statement_balance"][0]


def test_auto_match_and_the_candidate_listing_over_http(client: TestClient) -> None:
    _signup(client)
    bank = _bank(client)
    entry = _cashbook(client, amount="59000", on="2026-09-03", kind="receipt", key="cb-1")
    line_id = _bank_line_id(entry, bank["gl_account_id"])
    client.post(
        "/api/v1/banking/statements/manual",
        json={
            "bank_account_id": bank["id"],
            "opening_balance": "0",
            "closing_balance": "59000",
            "lines": [
                {
                    "value_date": "2026-09-05",
                    "description": "MOMO DEPOSIT 0788",
                    "amount": "59000",
                }
            ],
        },
        headers={"Idempotency-Key": "manual-1"},
    )
    unmatched = client.get(
        f"/api/v1/banking/accounts/{bank['id']}/unmatched-statement-lines"
    ).json()
    assert len(unmatched) == 1

    candidates = client.get(
        f"/api/v1/banking/statement-lines/{unmatched[0]['id']}/candidates"
    ).json()
    assert [candidate["rule"] for candidate in candidates] == ["amount_date"]
    assert candidates[0]["amount"] == "59000.000000"

    result = client.post(f"/api/v1/banking/accounts/{bank['id']}/auto-match")
    assert result.status_code == 200
    assert len(result.json()["matched"]) == 1
    assert result.json()["matched"][0]["journal_line_ids"] == [line_id]
    assert result.json()["ambiguous"] == {}

    assert client.get(
        f"/api/v1/banking/accounts/{bank['id']}/unmatched-statement-lines"
    ).json() == []


def test_an_unbalanced_manual_match_is_refused_over_http(client: TestClient) -> None:
    _signup(client)
    bank = _bank(client)
    entry = _cashbook(client, amount="1000", on="2026-09-03", kind="receipt", key="cb-1")
    line_id = _bank_line_id(entry, bank["gl_account_id"])
    client.post(
        "/api/v1/banking/statements/manual",
        json={
            "bank_account_id": bank["id"],
            "opening_balance": "0",
            "closing_balance": "900",
            "lines": [
                {"value_date": "2026-09-03", "description": "A DEPOSIT", "amount": "900"}
            ],
        },
        headers={"Idempotency-Key": "manual-1"},
    )
    unmatched = client.get(
        f"/api/v1/banking/accounts/{bank['id']}/unmatched-statement-lines"
    ).json()

    refused = client.post(
        "/api/v1/banking/matches",
        json={
            "bank_account_id": bank["id"],
            "statement_line_ids": [unmatched[0]["id"]],
            "journal_line_ids": [line_id],
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "match_unbalanced"
    assert "-100" in refused.json()["field_errors"]["lines"][0]


def test_posting_a_fee_from_its_line_over_http(client: TestClient) -> None:
    """The drawer, with its prefill read first — which is how the screen fills it in."""
    _signup(client)
    bank = _bank(client)
    accounts = {row["code"]: row for row in client.get("/api/v1/gl/accounts").json()}
    client.post(
        f"/api/v1/banking/accounts/{bank['id']}/rules",
        json={
            "pattern": "ACCOUNT FEE",
            "gl_account_id": accounts["6700"]["id"],
            "description": "Monthly account fee",
        },
    )
    client.post(
        "/api/v1/banking/statements/manual",
        json={
            "bank_account_id": bank["id"],
            "opening_balance": "0",
            "closing_balance": "-2500",
            "lines": [
                {
                    "value_date": "2026-09-12",
                    "description": "MONTHLY ACCOUNT FEE",
                    "amount": "-2500",
                }
            ],
        },
        headers={"Idempotency-Key": "manual-1"},
    )
    line = client.get(
        f"/api/v1/banking/accounts/{bank['id']}/unmatched-statement-lines"
    ).json()[0]

    prefill = client.get(f"/api/v1/banking/statement-lines/{line['id']}/prefill").json()
    assert prefill["gl_account_id"] == accounts["6700"]["id"]
    assert prefill["kind"] == "payment"
    assert prefill["description"] == "Monthly account fee"

    posted = client.post(
        f"/api/v1/banking/statement-lines/{line['id']}/post-cashbook",
        json={"gl_account_id": prefill["gl_account_id"]},
        headers={"Idempotency-Key": "post-1"},
    )

    assert posted.status_code == 201
    assert posted.json()["entry_number"].startswith("CB-")
    assert posted.json()["match"]["rule"] == "posted_from_statement"
    assert client.get(
        f"/api/v1/banking/accounts/{bank['id']}/unmatched-statement-lines"
    ).json() == []


def test_unmatching_inside_a_locked_reconciliation_is_refused_over_http(
    client: TestClient,
) -> None:
    _signup(client)
    bank = _bank(client)
    entry = _cashbook(client, amount="1000", on="2026-09-03", kind="receipt", key="cb-1")
    line_id = _bank_line_id(entry, bank["gl_account_id"])
    match = client.post(
        "/api/v1/banking/matches/tick",
        json={"bank_account_id": bank["id"], "journal_line_ids": [line_id]},
    ).json()
    opened = client.post(
        "/api/v1/banking/reconciliations",
        json={
            "bank_account_id": bank["id"],
            "reconciliation_date": "2026-09-30",
            "statement_balance": "1000",
        },
        headers={"Idempotency-Key": "brc-1"},
    ).json()
    client.post(
        f"/api/v1/banking/reconciliations/{opened['id']}/lock",
        json={},
        headers={"Idempotency-Key": "lock-1"},
    )

    refused = client.delete(f"/api/v1/banking/matches/{match['id']}")

    assert refused.status_code == 409
    assert refused.json()["code"] == "reconciliation_locked"


# --- Payment runs (decision 7) -----------------------------------------------------------------


def _supplier(client: TestClient, *, name: str, code: str, bank: bool = True) -> dict:
    created = client.post(
        "/api/v1/subledger/ap/partners",
        json={
            "name": name,
            "supplier_code": code,
            **(
                {
                    "bank_name": "Bank of Kigali",
                    "bank_account_number": f"00040-{code}-01",
                    "bank_account_holder": name,
                }
                if bank
                else {}
            ),
        },
    )
    assert created.status_code == 201, created.json()
    return created.json()


def _supplier_invoice(client: TestClient, partner: dict, *, amount: str, on: str) -> dict:
    accounts = {row["code"]: row for row in client.get("/api/v1/gl/accounts").json()}
    posted = client.post(
        "/api/v1/subledger/ap/documents",
        json={
            "kind": "invoice",
            "partner_id": partner["id"],
            "document_date": on,
            "description": f"supplies from {partner['name']}",
            "lines": [{"unit_price": amount, "gl_account_id": accounts["6990"]["id"]}],
        },
        headers={"Idempotency-Key": f"sin-{partner['supplier_code']}"},
    )
    assert posted.status_code in (200, 201), posted.json()
    return posted.json()


def test_the_supplier_screen_keeps_bank_details(client: TestClient) -> None:
    """The three fields a run's instruction file reads, over the Suppliers screen's own route —
    written on create, edited, and cleared as one fact rather than three."""
    _signup(client)
    supplier = _supplier(client, name="Kigali Timber", code="S1")
    assert supplier["bank_account_number"] == "00040-S1-01"

    edited = client.patch(
        f"/api/v1/subledger/ap/partners/{supplier['id']}",
        json={"bank_account_number": "00040-S1-02"},
    )
    assert edited.status_code == 200
    assert edited.json()["bank_account_number"] == "00040-S1-02"
    assert edited.json()["bank_name"] == "Bank of Kigali", "the others are left alone"

    cleared = client.patch(
        f"/api/v1/subledger/ap/partners/{supplier['id']}",
        json={"clear_bank_details": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["bank_name"] is None
    assert cleared.json()["bank_account_number"] is None
    assert cleared.json()["bank_account_holder"] is None


def test_the_suppliers_screen_replaces_the_three_as_one_form(client: TestClient) -> None:
    """The Bank details section sends the flag with the three values: replace, not merge. A
    holder deleted on the screen stays deleted — without the flag, a blank means "leave it"."""
    _signup(client)
    supplier = _supplier(client, name="Kigali Timber", code="S1")
    assert supplier["bank_account_holder"] is not None

    replaced = client.patch(
        f"/api/v1/subledger/ap/partners/{supplier['id']}",
        json={
            "clear_bank_details": True,
            "bank_name": "I&M Bank Rwanda",
            "bank_account_number": "2000-778-01",
            "bank_account_holder": None,
        },
    )

    assert replaced.status_code == 200
    body = replaced.json()
    assert (body["bank_name"], body["bank_account_number"], body["bank_account_holder"]) == (
        "I&M Bank Rwanda",
        "2000-778-01",
        None,
    )


def test_a_payment_run_is_previewed_posted_and_reversed_over_http(
    client: TestClient,
) -> None:
    """Preview → Post → the instruction file → Reverse, the four presses `/ap/payment-runs`
    will make. The preview writes nothing, which is asserted by there being no run after it."""
    _signup(client)
    bank = _bank(client)
    _cashbook(client, amount="500000", on="2026-09-01", kind="receipt", key="cb-1")
    s1 = _supplier(client, name="Kigali Timber", code="S1")
    s3 = _supplier(client, name="Huye Hardware", code="S3", bank=False)
    sin1 = _supplier_invoice(client, s1, amount="236000", on="2026-09-01")
    sin3 = _supplier_invoice(client, s3, amount="50000", on="2026-09-01")

    selectable = client.get(
        "/api/v1/banking/payment-runs/selectable", params={"bank_account_id": bank["id"]}
    )
    assert selectable.status_code == 200
    assert {row["document_id"] for row in selectable.json()} == {sin1["id"], sin3["id"]}

    body = {
        "bank_account_id": bank["id"],
        "payment_date": "2026-09-10",
        "lines": [{"document_id": sin1["id"]}, {"document_id": sin3["id"]}],
    }
    preview = client.post("/api/v1/banking/payment-runs/preview", json=body)
    assert preview.status_code == 200
    assert Decimal(preview.json()["total"]) == Decimal(286000)
    warnings = {
        supplier["partner_id"]: supplier["warnings"]
        for supplier in preview.json()["suppliers"]
    }
    assert "bank_details_missing" in warnings[s3["id"]]
    assert warnings[s1["id"]] == []
    assert client.get("/api/v1/banking/payment-runs").json() == [], "a preview writes nothing"

    posted = client.post(
        "/api/v1/banking/payment-runs", json=body, headers={"Idempotency-Key": "pyr-1"}
    )
    assert posted.status_code == 201, posted.json()
    run = posted.json()
    assert run["number"] == "PYR-000001"
    assert run["reference"] == "PYR-000001"
    assert Decimal(run["total"]) == Decimal(286000)
    assert len(run["lines"]) == 2
    assert len(run["remittance_job_ids"]) == 2, "one advice per supplier"
    # The detail's links: the invoice paid, the `PMT-` and the `ALC-` it produced, by number.
    by_invoice = {line["document_number"]: line for line in run["lines"]}
    assert set(by_invoice) == {sin1["number"], sin3["number"]}
    assert by_invoice[sin1["number"]]["partner_name"] == "Kigali Timber"
    assert by_invoice[sin1["number"]]["settlement_number"].startswith("PMT-")
    assert by_invoice[sin1["number"]]["settlement_status"] == "posted"
    assert by_invoice[sin1["number"]]["allocation_number"].startswith("ALC-")
    assert run["supplier_count"] == 2
    assert run["reconciliation_locked"] is None
    listed = client.get("/api/v1/banking/payment-runs").json()
    assert [(row["number"], row["supplier_count"]) for row in listed] == [("PYR-000001", 2)]

    # The AP document's "Paid in run PYR-n" — on the settlement, and on nothing else.
    settlement_id = by_invoice[sin1["number"]]["settlement_document_id"]
    settlement = client.get(f"/api/v1/subledger/ap/documents/{settlement_id}").json()
    assert (settlement["payment_run_id"], settlement["payment_run_number"]) == (
        run["id"],
        "PYR-000001",
    )
    assert settlement["payment_run_status"] == "posted"
    invoice = client.get(f"/api/v1/subledger/ap/documents/{sin1['id']}").json()
    assert invoice["payment_run_id"] is None, "the invoice was paid by the run, not posted by it"

    instruction = client.get(f"/api/v1/banking/payment-runs/{run['id']}/instruction.csv")
    assert instruction.status_code == 200
    assert instruction.headers["content-type"].startswith("text/csv")
    rows = instruction.text.strip().split("\r\n")
    assert rows[0] == "beneficiary,bank,account number,amount,currency,reference,supplier code"
    assert len(rows) == 3
    assert ",,,50000,RWF,PYR-000001,S3" in instruction.text, "S3's account fields are empty"

    reversed_run = client.post(
        f"/api/v1/banking/payment-runs/{run['id']}/reverse",
        json={"reason": "the transfer was recalled"},
    )
    assert reversed_run.status_code == 200
    assert reversed_run.json()["status"] == "reversed"
    assert {line["settlement_status"] for line in reversed_run.json()["lines"]} == {"reversed"}
    assert (
        client.get(f"/api/v1/subledger/ap/documents/{settlement_id}").json()[
            "payment_run_status"
        ]
        == "reversed"
    ), "the link stays; the guard reads the status"
    assert (
        Decimal(
            client.get(f"/api/v1/subledger/ap/documents/{sin1['id']}").json()["open_amount"]
        )
        == Decimal(236000)
    )


def test_the_preview_names_a_suppliers_open_credits_and_never_nets_them(
    client: TestClient,
) -> None:
    """Decision 7: a supplier with an unallocated payment on account is **listed with a warning
    naming it**, and the run pays the invoice in full — netting is P4's Allocate screen's job.
    `/ap/payment-runs/new` renders the warning from exactly this string."""
    _signup(client)
    bank = _bank(client)
    _cashbook(client, amount="500000", on="2026-09-01", kind="receipt", key="cb-1")
    s1 = _supplier(client, name="Kigali Timber", code="S1")
    sin1 = _supplier_invoice(client, s1, amount="236000", on="2026-09-01")
    accounts = {row["code"]: row for row in client.get("/api/v1/gl/accounts").json()}
    on_account = client.post(
        "/api/v1/subledger/ap/documents",
        headers={"Idempotency-Key": "pmt-on-account"},
        json={
            "kind": "settlement",
            "partner_id": s1["id"],
            "document_date": "2026-09-05",
            "description": "Payment on account",
            "amount": "20000",
            "cash_account_id": accounts["1120"]["id"],
            "instrument_type": "bank",
        },
    )
    assert on_account.status_code == 201, on_account.text

    preview = client.post(
        "/api/v1/banking/payment-runs/preview",
        json={
            "bank_account_id": bank["id"],
            "payment_date": "2026-09-10",
            "lines": [{"document_id": sin1["id"]}],
        },
    )

    assert preview.status_code == 200, preview.json()
    (supplier,) = preview.json()["suppliers"]
    assert supplier["warnings"] == [f"open_credits: {on_account.json()['number']}"]
    assert Decimal(supplier["total"]) == Decimal(236000), "never netted"


def test_paying_more_than_is_open_is_refused_over_http(client: TestClient) -> None:
    """The refusal reaches the screen as `{code, message, field_errors}` with the line named,
    which is what lets the selection grid mark the row rather than the form."""
    _signup(client)
    bank = _bank(client)
    _cashbook(client, amount="500000", on="2026-09-01", kind="receipt", key="cb-1")
    s1 = _supplier(client, name="Kigali Timber", code="S1")
    sin1 = _supplier_invoice(client, s1, amount="236000", on="2026-09-01")

    refused = client.post(
        "/api/v1/banking/payment-runs",
        json={
            "bank_account_id": bank["id"],
            "payment_date": "2026-09-10",
            "lines": [{"document_id": sin1["id"], "amount": "300000"}],
        },
        headers={"Idempotency-Key": "pyr-over"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "payment_exceeds_open"
    assert "lines.0.amount" in refused.json()["field_errors"]
    assert client.get("/api/v1/banking/payment-runs").json() == [], "no number was claimed"


def test_reversing_a_member_settlement_over_http_is_refused(client: TestClient) -> None:
    """What `/ap/documents/{id}` reads before it shows its Reverse button."""
    _signup(client)
    bank = _bank(client)
    _cashbook(client, amount="500000", on="2026-09-01", kind="receipt", key="cb-1")
    s1 = _supplier(client, name="Kigali Timber", code="S1")
    sin1 = _supplier_invoice(client, s1, amount="236000", on="2026-09-01")
    posted = client.post(
        "/api/v1/banking/payment-runs",
        json={
            "bank_account_id": bank["id"],
            "payment_date": "2026-09-10",
            "lines": [{"document_id": sin1["id"]}],
        },
        headers={"Idempotency-Key": "pyr-1"},
    )
    assert posted.status_code == 201
    settlement_id = posted.json()["lines"][0]["settlement_document_id"]

    refused = client.post(
        f"/api/v1/subledger/ap/documents/{settlement_id}/reverse",
        json={"on_date": "2026-09-10", "reason": "wrong beneficiary"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "payment_run_member"
    assert "PYR-000001" in refused.json()["message"]


def test_the_entry_page_reads_each_bank_line_locked_matched_or_outstanding(
    client: TestClient,
) -> None:
    """Decision 10's GL entry page, and the two fields step 8's reports link by.

    Three entries on one account: one ticked and locked into `BRC-000001`, one ticked after the
    lock (matched, in no reconciliation), one untouched (outstanding). The entry page's reading
    names each — the same `list_ledger_lines` the workspace pane reads, narrowed to the entry —
    and an entry with no bank line answers an empty list rather than a 404.
    """
    _signup(client)
    bank = _bank(client)
    locked = _cashbook(client, amount="1000", on="2026-09-03", kind="receipt", key="ep-1")
    matched = _cashbook(client, amount="400", on="2026-09-20", kind="payment", key="ep-2")
    outstanding = _cashbook(client, amount="250", on="2026-09-21", kind="receipt", key="ep-3")

    def tick(entry: dict) -> None:
        response = client.post(
            "/api/v1/banking/matches/tick",
            json={
                "bank_account_id": bank["id"],
                "journal_line_ids": [_bank_line_id(entry, bank["gl_account_id"])],
            },
        )
        assert response.status_code == 201

    tick(locked)
    opened = client.post(
        "/api/v1/banking/reconciliations",
        json={
            "bank_account_id": bank["id"],
            "reconciliation_date": "2026-09-10",
            "statement_balance": "1000",
        },
        headers={"Idempotency-Key": "ep-brc"},
    ).json()
    assert client.post(
        f"/api/v1/banking/reconciliations/{opened['id']}/lock",
        json={},
        headers={"Idempotency-Key": "ep-lock"},
    ).status_code == 200
    tick(matched)

    def read(entry: dict) -> list[dict]:
        response = client.get(f"/api/v1/banking/journal-entries/{entry['id']}/bank-lines")
        assert response.status_code == 200
        return response.json()

    [on_locked] = read(locked)
    assert on_locked["bank_account_code"] == bank["code"]
    assert on_locked["reconciliation_number"] == "BRC-000001"
    assert on_locked["reconciliation_id"] == opened["id"]
    assert on_locked["is_outstanding"] is False

    [on_matched] = read(matched)
    assert on_matched["match_rule"] == "tick"
    assert on_matched["reconciliation_number"] is None
    assert Decimal(on_matched["amount"]) == Decimal(-400)

    [on_outstanding] = read(outstanding)
    assert on_outstanding["is_outstanding"] is True
    assert on_outstanding["match_id"] is None

    # Only the bank side: the contra line on 3400 is not a bank line and is not listed.
    assert len(read(outstanding)) == 1

    report = client.get(f"/api/v1/banking/reports/reconciliation/{opened['id']}").json()
    assert report["bank_account_name"] == bank["name"]
    summary = client.get(
        "/api/v1/banking/reports/cashbook-summary",
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    ).json()
    row = next(item for item in summary if item["bank_account_id"] == bank["id"])
    assert row["last_reconciliation_id"] == opened["id"]
