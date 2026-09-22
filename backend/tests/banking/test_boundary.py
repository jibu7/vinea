"""Decision 1, as a test: **nothing in `app/banking/` writes a journal line.**

The rule is easy to state and easy to lose, one convenient import at a time. A reconciliation
that is out by a fee wants to "just book it"; a match that does not balance wants to "just
adjust". Each is defensible alone, and what they add up to is a second posting authority —
which is ADR-05 gone, and with it the guarantee that the ledger is one story.

So the boundary is read from the source rather than trusted to review, at collect time, three
ways:

* **No module under `app/banking/` writes `journal_entries` or `journal_lines`.** Not by ORM
  construction, not by `insert()`, not by raw SQL in a `text()`.
* **None of them reaches into the engine's internals.** Calling `posting.post` with a typed
  event is the sanctioned door and it is how `post_cashbook_from_line` works; importing
  `_write`, `_resolve_lines` or `_Context` is going round it.
* **No new `module` string reaches `journal_entries.module`.** Every posting this phase causes
  is a `CashbookEntry` (`cb`), a P4 document (`ar` / `ap`) or a P7 revaluation (`gl`), and
  `BANKING_MODULE` exists in the models as a *name for the thing that does not happen*.

An AST scan for the reason `tests/test_platform_scope.py` and `tests/fiscal/test_boundary.py`
both use one: a `grep` is fooled by a docstring, and an import inside a function is still an
import.
"""

import ast
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.banking import BANKING_MODULE
from app.models.journal import JournalEntry

APP = Path(__file__).resolve().parents[2] / "app"
BANKING = APP / "banking"

#: The two tables only the PostingEngine may write.
LEDGER_TABLES = frozenset({"JournalEntry", "JournalLine", "journal_entries", "journal_lines"})

#: Names inside `app.kernel.posting` that are the engine's own workings. `post`, `replay`,
#: `module_reversal` and `gl_settings_for` are its public door; everything else is machinery,
#: and importing machinery is how a second writer starts.
ENGINE_INTERNALS = frozenset(
    {
        "_write",
        "_resolve_lines",
        "_Context",
        "_check_account",
        "_rounding_line",
        "_cashbook_specs",
        "_reject_unbalanced",
        "_check_bank_account_currency",
    }
)

#: What `app/banking/` may call on the posting engine.
SANCTIONED_ENGINE_NAMES = frozenset({"post", "replay", "module_reversal", "gl_settings_for"})


def _banking_modules() -> list[Path]:
    return sorted(BANKING.rglob("*.py"))


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_banking_module_constructs_a_journal_row() -> None:
    """A `JournalLine(...)` anywhere under `app/banking/` is a second writer, whatever it is
    called and however good the reason."""
    offenders: list[str] = []
    for path in _banking_modules():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in LEDGER_TABLES:
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert offenders == [], (
        f"these construct a journal row directly: {offenders}. Every posting this phase "
        "causes goes through `post_event` with a typed event, through P4's "
        "`post_document`/`allocate`, or through the P7 revaluation (decision 1)."
    )


def test_no_banking_module_writes_the_ledger_tables_in_sql() -> None:
    """The other way in: an `insert()`/`update()`/`delete()` against the ORM classes, or raw
    SQL naming the tables. The engine's own `VN006` trigger would refuse it at runtime — this
    is what refuses it at review time, with a file and a line number."""
    offenders: list[str] = []
    for path in _banking_modules():
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"insert", "update", "delete"}:
                    written = {
                        argument.id
                        for argument in node.args
                        if isinstance(argument, ast.Name)
                    }
                    if written & LEDGER_TABLES:
                        offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lowered = node.value.lower()
                if any(
                    verb in lowered
                    for verb in ("insert into journal", "update journal_", "delete from journal")
                ):
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno} (raw SQL)")
    assert offenders == [], f"these write the ledger tables directly: {offenders}"


def test_no_banking_module_imports_the_engine_internals() -> None:
    """`posting.post` is the door. `posting._write` is going round it."""
    offenders: list[str] = []
    for path in _banking_modules():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.kernel.posting"
            ):
                for alias in node.names:
                    if alias.name in ENGINE_INTERNALS or alias.name.startswith("_"):
                        offenders.append(
                            f"{path.relative_to(APP.parent)}:{node.lineno} {alias.name}"
                        )
            if isinstance(node, ast.Attribute) and node.attr in ENGINE_INTERNALS:
                value = node.value
                if isinstance(value, ast.Name) and "posting" in value.id:
                    offenders.append(
                        f"{path.relative_to(APP.parent)}:{node.lineno} {value.id}.{node.attr}"
                    )
    assert offenders == [], (
        f"these reach into the posting engine's internals: {offenders}. The sanctioned names "
        f"are {sorted(SANCTIONED_ENGINE_NAMES)}."
    )


def test_the_guard_can_see_a_module_that_breaks_the_rule() -> None:
    """Anti-vacuity. All three scans above would pass just as happily over a matcher that
    matched nothing, so each one is shown a module that does the thing it forbids."""
    constructs = ast.parse("def post(db):\n    db.add(JournalLine(amount=1))\n")
    found = [
        node
        for node in ast.walk(constructs)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in LEDGER_TABLES
    ]
    assert found, "the constructor scan cannot see a JournalLine(...) call"

    sql = ast.parse('def post(db):\n    db.execute(text("INSERT INTO journal_lines VALUES (1)"))\n')
    found = [
        node
        for node in ast.walk(sql)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "insert into journal" in node.value.lower()
    ]
    assert found, "the raw-SQL scan cannot see an INSERT INTO journal_lines"

    internals = ast.parse("from app.kernel.posting import _write\n")
    found = [
        alias.name
        for node in ast.walk(internals)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in ENGINE_INTERNALS
    ]
    assert found == ["_write"], "the internals scan cannot see an import of `_write`"


def test_the_scan_actually_reaches_the_banking_package() -> None:
    """The third way a guard like this goes quiet: the path is wrong and it scans nothing.
    Named modules rather than a count, so that deleting one is visible."""
    found = {path.name for path in _banking_modules()}

    assert {
        "accounts.py",
        "formats.py",
        "matching.py",
        "reconciliation.py",
        "statements.py",
    } <= found, f"the boundary scan is not reading the banking package: {sorted(found)}"


def test_no_entry_is_posted_under_the_banking_module_string(db: Session, banking) -> None:  # noqa: ANN001
    """The claim from the ledger's side rather than the source's: `BANKING_MODULE` is a name
    for something that never happens, and no `journal_entries.module` ever equals it.

    Asserted against a live tenant because the AST scans cannot see this one — a module string
    reaching the ledger would come from a typed event's own definition, not from a line of code
    in `app/banking/`.
    """
    modules = set(
        db.scalars(
            select(JournalEntry.module).where(JournalEntry.company_id == banking.company_id)
        )
    )

    assert BANKING_MODULE not in modules, (
        f"an entry was posted under module {BANKING_MODULE!r}. Every posting this phase causes "
        "belongs to the module that owns the event: `cb` for a cashbook entry, `ar`/`ap` for a "
        "P4 document, `gl` for a revaluation."
    )
