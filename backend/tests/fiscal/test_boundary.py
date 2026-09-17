"""Rule 12, as a test: **no module outside `app/fiscal/` imports `rwanda/`.**

The rule is easy to state and easy to lose. It goes one convenient import at a time — a screen
that wants a tax-class label, a report that wants a receipt-type constant — and each one is
defensible on its own. What they add up to is a second country being a rewrite.

So this reads the import graph, at collect time, from the source rather than from a convention.
An AST scan for the same reason `tests/test_platform_scope.py` uses one: a `grep` would be
fooled by a string, and an import inside a function is still an import.

The **only** sanctioned caller outside the package is `app/fiscal/registry.py`, which exists
precisely so that nothing else needs to be.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
RWANDA_PACKAGE = "app.fiscal.rwanda"

#: Where the Rwanda package may be imported from. `registry.py` maps a company's fiscal country
#: onto an implementation and is the reason nothing else has to; the package's own modules
#: import each other, as any package's do.
SANCTIONED_IMPORTERS = {
    APP / "fiscal" / "registry.py",
}


def _imports_rwanda(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith(RWANDA_PACKAGE) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(RWANDA_PACKAGE):
                return True
    return False


def test_nothing_outside_the_fiscal_package_imports_the_rwanda_adapter() -> None:
    offenders = sorted(
        str(path.relative_to(APP.parent))
        for path in APP.rglob("*.py")
        if RWANDA_PACKAGE not in str(path).replace("/", ".")
        and path not in SANCTIONED_IMPORTERS
        and _imports_rwanda(path.read_text(encoding="utf-8"))
    )

    assert offenders == [], (
        "these modules import app.fiscal.rwanda directly, which is architecture rule 12: "
        f"{offenders}. Reach an adapter through `app.fiscal.adapter_for(country)` — the "
        "registry exists so that nothing else has to know a country's field names."
    )


def test_the_registry_is_the_only_sanctioned_importer_and_it_still_imports_lazily() -> None:
    """Lazily, so that importing `app.fiscal` does not drag in every country's payload models
    — and so the sanction stays a single line rather than a growing list."""
    source = (APP / "fiscal" / "registry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_level = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any(name.startswith(RWANDA_PACKAGE) for name in module_level), (
        "the registry imports the Rwanda adapter inside the factory, not at module scope"
    )
    assert _imports_rwanda(source), "the registry is how the adapter is reached at all"


#: Field names a leak would most plausibly carry — the ones a screen, a report or a queue
#: drainer would be tempted to reach for. A full lexicon would be unmaintainable; these are
#: specific enough to mean something and rare enough not to fire by accident.
#:
#: `orgInvcNo` and `invcNo` are P7 step 2's addition, and they are here because the drainer
#: *did* reach for one: writing a refund's receipt needs the number of the sale it reverses,
#: and the shortest way to it was `row.payload["orgInvcNo"]` in a country-neutral module. The
#: number now travels on `FiscalReceiptData`, filled by the adapter, and this line is what
#: stops the shortcut coming back.
RRA_FIELD_NAMES = (
    "taxblAmt",
    "rcptTyCd",
    "itemClsCd",
    "prcOrdCd",
    "sarTyCd",
    "orgInvcNo",
    "invcNo",
)


def _code_corpus(source: str) -> str:
    """Identifiers and string literals — **not** comments or docstrings.

    Prose explaining the boundary is not a breach of it. `app/models/fiscalization.py` says in
    a docstring that `fiscal_class_code` is what the adapter sends as `itemClsCd`, and a reader
    is better off for that sentence; what must not exist is code that *uses* the name.

    The same distinction P6 drew in the other direction: there a comment naming an endpoint was
    ruled not to be a caller, because the register is about calls. Here a comment naming a field
    is ruled not to be a leak, because the rule is about what the code knows.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, ast.arg):
            parts.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            parts.append(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            parts.append(node.name)
        elif isinstance(node, ast.alias):
            parts.append(node.name)
    return "\n".join(parts)


def test_no_rra_field_name_appears_in_code_outside_the_rwanda_package() -> None:
    offenders: dict[str, list[str]] = {}
    for path in APP.rglob("*.py"):
        if RWANDA_PACKAGE in str(path).replace("/", "."):
            continue
        corpus = _code_corpus(path.read_text(encoding="utf-8"))
        found = [needle for needle in RRA_FIELD_NAMES if needle in corpus]
        if found:
            offenders[str(path.relative_to(APP.parent))] = found

    assert offenders == {}, (
        "these modules outside app/fiscal/rwanda use a revenue authority's field names in "
        f"code: {offenders}. The DTOs in app/fiscal/mapping.py are the vocabulary above the "
        "adapter."
    )


def test_the_scan_sees_a_field_name_in_code_and_not_one_in_prose() -> None:
    """Proving the guard is sensitive, which a guard written around an exception has to be.

    Without this the corpus builder could silently degrade to "look at nothing" and the test
    above would pass over any leak at all.
    """
    in_code = _code_corpus('payload = {"taxblAmtB": 0}\n')
    in_prose = _code_corpus('"""The adapter sends it as taxblAmtB."""\n')

    assert "taxblAmt" in in_code
    assert "taxblAmt" not in in_prose
