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

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.banking.conftest import sample

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
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["stored"] is None


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
