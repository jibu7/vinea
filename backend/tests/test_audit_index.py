"""`ix_audit_log_company_entity_at` replaced `ix_audit_log_company_at`, and this is what keeps
the replacement safe (issue #54).

Dropping the narrow index rests on a claim about the *callers*: nothing reads `audit_log` by
company and time alone. That was true when the index was replaced — the four readers all filter
`company_id`, `entity` and `entity_id` — and the issue flagged it as "the kind of thing that
stops being true quietly". A retention reaper scanning by age, or an operator console showing a
tenant's recent activity, would want exactly the prefix that was dropped, and would get a scan
of the whole tenant instead. Nothing would fail; the screen would just be slow, on the tenants
with the most history.

So the claim is a guard rather than a sentence in a commit message. It reads the AST of `app/`
and fails on any `AuditLog` query that filters `company_id` without also narrowing to an
entity.

**A static guard, not a runtime one**, because the thing being protected is a *query shape* and
there is no moment at runtime when a wrong one announces itself: it returns correct rows, in
the right order, slowly. The same reasoning as `test_platform_scope.py`, which asks "which
modules call this" of the syntax rather than of a running program.

**What counts as narrowing.** A comparison against `AuditLog.entity` **and** one against
`AuditLog.entity_id` in the same `select()` chain. `entity` alone is not enough — every row of
a tenant's partner history shares `entity = 'partners'`, so filtering on it narrows 200 000
rows to 10 000 rather than to 20, which is the scan this index exists to stop.

**What is exempt.** `INDEX_PREFIX_READERS` below, with a reason. Empty today, and an entry in
it is a decision to keep `ix_audit_log_company_at` alive — so the register and the migration
have to move together, which is the point.
"""

import ast
import os
from pathlib import Path

from sqlalchemy import inspect

from app.db import engine
from app.models.audit import AuditLog

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2])
APP_ROOT = REPO_ROOT / "backend" / "app"

#: The index the migration creates, and the columns in order. Asserted against the live schema
#: as well as the model, because `__table_args__` and the migration are two statements of the
#: same fact and `alembic check` only notices when they disagree about a *column*, not about an
#: index's column order.
INDEX_NAME = "ix_audit_log_company_entity_at"
INDEX_COLUMNS = ["company_id", "entity", "entity_id", "at", "id"]

#: The index this replaced. Its absence is the assertion: while it exists, nothing forces a
#: reader to narrow, and the two indexes cost twice for one query shape.
REPLACED_INDEX = "ix_audit_log_company_at"

#: `"path:function"` → why it may read `audit_log` by company alone.
#:
#: Empty, deliberately. An entry here is a decision to bring `ix_audit_log_company_at` back,
#: because a reader with no entity in it will scan the tenant without it — so adding a line
#: here and leaving the schema alone is not a fix, it is the defect with a note attached.
INDEX_PREFIX_READERS: dict[str, str] = {}

#: The attribute names a query has to mention to be narrowing.
_COMPANY = "company_id"
_ENTITY = "entity"
_ENTITY_ID = "entity_id"


def _audit_attributes(node: ast.AST) -> set[str]:
    """Every `AuditLog.<attr>` mentioned anywhere under this node."""
    found: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "AuditLog"
        ):
            found.add(child.attr)
    return found


def _audit_query_functions() -> dict[str, set[str]]:
    """`"path:function"` → the `AuditLog` columns its body mentions.

    Scoped to the enclosing function rather than to a single `select()` call, because a query
    in this build is routinely built across several statements — `statement = select(...)`,
    then `statement = statement.where(...)` — and a matcher that only read the call would call
    a correctly-narrowed query a violation.

    The cost of the wider scope is that a function holding *two* queries, one narrowed and one
    not, would pass. There is no such function today, and the two-query case would be visible
    in review anyway; a false pass on a shape nobody writes is cheaper than a false failure on
    the shape everybody does.
    """
    queries: dict[str, set[str]] = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(APP_ROOT.parent).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            attributes = _audit_attributes(node)
            if _COMPANY in attributes:
                queries[f"{relative}:{node.name}"] = attributes
    return queries


def test_every_audit_query_narrows_to_an_entity() -> None:
    """The claim `ix_audit_log_company_at` was dropped on."""
    offenders = sorted(
        site
        for site, attributes in _audit_query_functions().items()
        if site not in INDEX_PREFIX_READERS
        and not ({_ENTITY, _ENTITY_ID} <= attributes)
    )

    assert offenders == [], (
        "these read audit_log by company without narrowing to an entity, which scans the "
        f"tenant's whole audit trail: {offenders}. Either add the entity filter, or put the "
        "site in INDEX_PREFIX_READERS with a reason — and bring back a "
        "(company_id, at) index in the same change, because without one the scan is the "
        "behaviour rather than an oversight."
    )


def test_the_guard_can_see_a_query_that_does_not_narrow() -> None:
    """Anti-vacuity. A guard over an AST is worth exactly what its matcher can see, and this
    one would pass just as happily if `_audit_attributes` returned nothing at all.

    So: the same matcher, over a query written the way the dropped index's caller would have
    written it, must report it.
    """
    source = (
        "def recent_activity(db, company_id):\n"
        "    return db.scalars(\n"
        "        select(AuditLog)\n"
        "        .where(AuditLog.company_id == company_id)\n"
        "        .order_by(AuditLog.at.desc())\n"
        "    ).all()\n"
    )
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))

    attributes = _audit_attributes(function)

    assert _COMPANY in attributes
    assert not ({_ENTITY, _ENTITY_ID} <= attributes)


def test_the_guard_sees_a_query_built_across_statements() -> None:
    """The other half of the anti-vacuity check: a narrowed query assembled in several steps
    must *not* be reported. This is the shape the function scope exists for."""
    source = (
        "def history(db, company_id, entity_id):\n"
        "    statement = select(AuditLog).where(AuditLog.company_id == company_id)\n"
        "    statement = statement.where(AuditLog.entity == 'partners')\n"
        "    statement = statement.where(AuditLog.entity_id == entity_id)\n"
        "    return db.scalars(statement).all()\n"
    )
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))

    assert {_COMPANY, _ENTITY, _ENTITY_ID} <= _audit_attributes(function)


def test_the_four_known_readers_are_still_found() -> None:
    """The guard is only as good as its reach. These are the readers the issue named, plus the
    EBM queue history P7 added after it was filed — if the scan stops finding them, it has
    stopped scanning."""
    sites = _audit_query_functions()

    assert {site.split(":")[0] for site in sites} == {
        "app/api/v1/subledger.py",
        "app/api/v1/gl.py",
        "app/api/v1/inventory.py",
        "app/fiscal/enquiries.py",
    }


def test_the_index_is_on_the_table_with_its_columns_in_order() -> None:
    """`__table_args__` and the migration are two statements of one fact. `alembic check`
    compares columns and would not notice an index whose column *order* drifted — and the
    order is the whole point here, since a prefix is what the planner can use."""
    indexes = {
        index["name"]: list(index["column_names"])
        for index in inspect(engine).get_indexes("audit_log")
    }

    assert indexes.get(INDEX_NAME) == INDEX_COLUMNS
    assert REPLACED_INDEX not in indexes, (
        f"{REPLACED_INDEX} is a prefix of {INDEX_NAME} and buys nothing beside it; keeping "
        "both pays for two indexes on one query shape"
    )


def test_the_model_declares_the_same_index_as_the_schema() -> None:
    declared = {
        index.name: [column.name for column in index.columns]
        for index in AuditLog.__table__.indexes
    }

    assert declared.get(INDEX_NAME) == INDEX_COLUMNS
