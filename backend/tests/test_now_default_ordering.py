"""A column defaulting to `now()` cannot order rows on its own, so a query that sorts on one
must carry a tie-break. This is the static guard that says so.

**The defect this exists to prevent.** PostgreSQL's `now()` is the *transaction* clock, not the
statement clock: every row one transaction writes takes the same value. `audit_log.at` defaults
to it, so a rename history sorted `ORDER BY at DESC` alone has no defined order between the rows
one request wrote — the planner decides, and the screen calls whichever it gets first the latest
change.

Issue #20 found this on `GET /subledger/{role}/partners/{id}/history`, and the same `order_by`
was in `GET /gl/accounts/{id}/history`. Neither had ever returned a visibly wrong answer,
because the planner reaches `ix_audit_log_company_at` and a backward btree scan happens to
return tied rows newest-id first. That is an implementation detail of the index, not a promise
the query made: take the index away and the same query becomes a sort, and a sort of equal keys
returns them in whatever order it returns them in.

Which is why this is a static guard and not an ordering test. A test that writes two rows and
reads them back asserts the plan it happened to get — it passes on broken code when the accident
falls the right way, and *that is the same class of luck the fix removes*. The order a query is
entitled to is the order it asked for, and the only place that is decidable is the source.

The rule: **an `order_by` that sorts on a `now()`-defaulted column also names that model's
`id`.** The columns are read from the mapped models rather than listed here, so a new
`server_default=func.now()` is covered the day it is added — `audit_log.at` today, plus
`created_at`/`updated_at` on every table carrying `AuditedMixin`.
"""

import ast
import pathlib

import app
from app.db import Base

APP = pathlib.Path(app.__file__).parent

#: The tie-break. Every mapped table's primary key, and in write order because it comes from a
#: sequence claimed at INSERT, where the `now()` columns are stamped at BEGIN.
TIE_BREAK = "id"


def now_defaulted_columns() -> dict[str, set[str]]:
    """`{model class name: attribute names whose server default is now()}`.

    Read from the mappers, so the guard covers a column the day someone adds it rather than
    the day someone remembers to add it here.
    """
    found: dict[str, set[str]] = {}
    for mapper in Base.registry.mappers:
        for prop in mapper.column_attrs:
            column = prop.columns[0]
            default = column.server_default
            if default is None:
                continue
            if "now()" not in str(getattr(default, "arg", "")).lower():
                continue
            found.setdefault(mapper.class_.__name__, set()).add(prop.key)
    return found


def _model_attributes(node: ast.AST) -> set[tuple[str, str]]:
    """The `Model.column` pairs named anywhere under `node` — `AuditLog.at.desc()` included."""
    return {
        (child.value.id, child.attr)
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
    }


def order_bys_on_a_transaction_clock(
    source: str, columns: dict[str, set[str]]
) -> list[tuple[int, str, bool]]:
    """Every `order_by(...)` in `source` that sorts on a `now()` column.

    Returns `(line, "Model.column", whether the same order_by also names Model.id)`. The unit
    is the **`order_by` call**, not the query and not the function: a tie-break in a
    neighbouring sort is not a tie-break in this one, and an `id` in the `where` clause orders
    nothing.
    """
    tree = ast.parse(source)
    found: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "order_by"
        ):
            continue
        named = set()
        for argument in node.args:
            named |= _model_attributes(argument)
        for model, attribute in sorted(named):
            if attribute in columns.get(model, set()):
                found.append((node.lineno, f"{model}.{attribute}", (model, TIE_BREAK) in named))
    return found


def test_every_sort_on_a_now_default_carries_a_tie_break() -> None:
    columns = now_defaulted_columns()
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for lineno, column, tie_broken in order_bys_on_a_transaction_clock(
            path.read_text(), columns
        ):
            if not tie_broken:
                offenders.append(f"{path.relative_to(APP.parent)}:{lineno} sorts on {column}")
    assert offenders == [], (
        f"these sort on a `now()` default with no tie-break: {offenders}. `now()` is the "
        "transaction clock, so every row one request writes shares the value and the planner "
        f"picks the winner — add `.{TIE_BREAK}` to the same `order_by`, in the same direction. "
        "See this module's docstring."
    )


# --- Anti-vacuity: a guard that cannot fail is a guard that is not there ---------------------


def test_the_models_really_do_carry_a_now_default() -> None:
    """The guard is only as wide as this map. If the introspection silently returned nothing,
    every assertion above would pass over every file and mean nothing."""
    columns = now_defaulted_columns()
    assert columns.get("AuditLog") == {"at"}, columns.get("AuditLog")
    # The mixin puts the same default on every audited table, so the class is wider than
    # `audit_log` and the guard has to know it.
    assert "created_at" in columns["JournalEntry"]
    assert "updated_at" in columns["JournalEntry"]


def test_the_guard_recognises_the_shape_it_forbids() -> None:
    columns = {"AuditLog": {"at"}}
    untied = "select(AuditLog).order_by(AuditLog.at.desc())"
    assert order_bys_on_a_transaction_clock(untied, columns) == [(1, "AuditLog.at", False)]

    tied = "select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc())"
    assert order_bys_on_a_transaction_clock(tied, columns) == [(1, "AuditLog.at", True)]


def test_the_guard_does_not_accept_a_tie_break_from_a_neighbouring_sort() -> None:
    """Two sorts in one function, one of them tie-broken: the other must still be reported.
    A function-scoped check would pass this, and passing it is how the next one gets written —
    beside a correct query that lends it cover."""
    source = """
def read(db, company_id):
    good = db.scalars(
        select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc())
    )
    bad = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()))
    return good, bad
"""
    assert order_bys_on_a_transaction_clock(source, {"AuditLog": {"at"}}) == [
        (4, "AuditLog.at", True),
        (6, "AuditLog.at", False),
    ]


def test_an_id_in_the_where_clause_is_not_a_tie_break() -> None:
    """`id` has to be in the sort to order anything. Filtering on it orders nothing, and a
    guard that accepted it would wave through exactly the queries these endpoints were."""
    source = """
select(AuditLog).where(AuditLog.id > cursor).order_by(AuditLog.at.desc())
"""
    assert order_bys_on_a_transaction_clock(source, {"AuditLog": {"at"}}) == [
        (2, "AuditLog.at", False)
    ]


def test_the_guard_leaves_everything_else_alone() -> None:
    """A sort on a column that is not a `now()` default needs nothing, and an attribute read
    is not a sort — flagging either would push people to silence the guard rather than obey
    it."""
    columns = {"AuditLog": {"at"}}
    assert order_bys_on_a_transaction_clock("select(X).order_by(X.code)", columns) == []
    assert order_bys_on_a_transaction_clock("moment = row.at", columns) == []
    # `sequence_no` is a unique key in write order, which is what the stock ledger and the
    # fiscal outbox sort on. Nothing to add.
    assert order_bys_on_a_transaction_clock(
        "select(StockMove).order_by(StockMove.move_date, StockMove.sequence_no)", columns
    ) == []
