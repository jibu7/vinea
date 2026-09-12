"""Every document run has a registered claimant, and every claimant is real.

`assert_ledger_invariants` proves a `document_sequences` run gapless by asking
`SEQUENCE_CLAIMANTS` who may hold its numbers. That makes the registry load-bearing in a way
nothing else notices: a doc type added without a claimant would not fail the invariant loudly
in every suite — it would fail only in the suites that happen to use it, and a doc type whose
claimant named a column that had been renamed would fail with a database error nobody would
read as "the registry is stale".

So both halves are checked here, at the cheapest possible level: the keys against `DocType`,
and every named table and column against the actual schema.
"""

import pytest
from sqlalchemy import Engine, inspect

from app.kernel.sequences import (
    DEFAULT_PREFIXES,
    SEQUENCE_CLAIMANTS,
    DocType,
    claimants_for,
)


def test_every_doc_type_registers_a_claimant() -> None:
    """The forcing function: a phase that starts numbering something registers it beside
    `document_sequences`, rather than editing the checker that reads the registry."""
    unregistered = sorted(
        str(doc_type) for doc_type in DocType if doc_type not in SEQUENCE_CLAIMANTS
    )

    assert unregistered == [], (
        "these doc types claim numbers with nobody registered to hold them: "
        f"{unregistered} — add them to SEQUENCE_CLAIMANTS in app/kernel/sequences.py"
    )


def test_the_registry_names_no_doc_type_that_does_not_exist() -> None:
    known = {str(doc_type) for doc_type in DocType}

    assert sorted(key for key in SEQUENCE_CLAIMANTS if key not in known) == []


def test_every_doc_type_has_a_prefix() -> None:
    """A run whose prefix is missing falls back to `<DOCTYPE>-`, which is not what anybody
    meant to put in front of an auditor."""
    assert sorted(str(doc_type) for doc_type in DocType if doc_type not in DEFAULT_PREFIXES) == []


def test_claimants_for_refuses_an_unregistered_doc_type() -> None:
    with pytest.raises(KeyError) as error:
        claimants_for("NOPE")

    assert "SEQUENCE_CLAIMANTS" in str(error.value), "the refusal has to say what to do"


def test_every_claimant_names_a_real_table_and_column(admin_engine: Engine) -> None:
    """The registry is strings — the kernel does not import the subledger or the inventory
    module — so nothing but this test would notice a rename."""
    inspector = inspect(admin_engine)
    tables = set(inspector.get_table_names())
    for doc_type, claimants in SEQUENCE_CLAIMANTS.items():
        for claimant in claimants:
            assert claimant.table in tables, f"{doc_type}: no table {claimant.table}"
            columns = {column["name"] for column in inspector.get_columns(claimant.table)}
            assert claimant.number_column in columns, (
                f"{doc_type}: {claimant.table} has no {claimant.number_column}"
            )
            assert "company_id" in columns, (
                f"{doc_type}: {claimant.table} is not company-scoped; the claimant query "
                "filters on company_id"
            )
            if claimant.doc_type_column is not None:
                assert claimant.doc_type_column in columns, (
                    f"{doc_type}: {claimant.table} has no {claimant.doc_type_column}"
                )
