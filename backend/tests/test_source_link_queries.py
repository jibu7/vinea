"""`stock_moves.source_line_id` is not namespaced, so a query that filters on it alone is a
bug waiting to be written. This is the static guard that says so.

**The defect this exists to prevent.** Every kind of stock document writes its own line ids
into one column: a goods receipt writes `goods_received_note_lines` ids, a landed cost writes
the *GRN* line ids it allocated onto, the partner-document companion writes
`partner_document_lines` ids. Three id spaces, one column, no discriminator. A lookup by
`source_line_id` alone therefore returns whichever row the planner reaches first, and which
one that is depends on what else has been posted.

P6 step 5's acceptance tape found it at row 13. `_issued_unit_cost` matched the move a
returned invoice line was issued on by `source_line_id` and nothing else. A landed cost had
revalued the same receipt, so its zero-quantity move — keyed on GRN line 1, carrying no
`unit_cost`, because a revaluation has none — collided with sale line 1 and won. A null
`unit_cost` reads exactly like "this line was never issued", so the credit note fell through to
the current average and brought 5 bottles back at 1 111.1 instead of the 1 000 they left at.

Fixing the one reader fixes today. It does not fix tomorrow: the next person to write
`StockMove.source_line_id == line.id` will write it the same way, because the column's name
gives no hint that it is shared. So the rule is enforced against the source rather than left
in a comment on one function — **a query that filters on `source_line_id` filters on
`source_doc_type` too**.

Read statically rather than exercised, because the failure is a line of code nobody would
look at twice, not a behaviour a posting test would fail on. The right unit is the query, not
the file or the function: two unrelated queries in one function must not let each other pass.
"""

import ast
import pathlib

import app

APP = pathlib.Path(app.__file__).parent

#: The shared column, and the discriminator that makes a lookup on it unambiguous.
SHARED_LINK = "source_line_id"
DISCRIMINATOR = "source_doc_type"


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    links: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            links[child] = node
    return links


def _attribute_names(node: ast.AST) -> set[str]:
    return {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}


def _is_select_call(node: ast.AST) -> bool:
    """A `select(...)` call — what makes an expression a query rather than an attribute read."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "select"
    )


def queries_filtering_on_the_shared_link(source: str) -> list[tuple[int, bool]]:
    """Every query in `source` that mentions `source_line_id`, and whether it also discriminates.

    The unit is the **outermost call expression** containing the reference — `select(...)`
    through every chained `.join(...)` and `.where(...)` — so the discriminator has to be in
    the same query and not merely somewhere in the same function. An expression with no
    `select` in it is an attribute read on a model instance, not a query, and is left alone.
    """
    tree = ast.parse(source)
    parents = _parents(tree)
    found: list[tuple[int, bool]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == SHARED_LINK):
            continue
        # Walk out to the widest enclosing call: `db.scalar(select(X).where(...))` and
        # `select(X).where(...)` both collapse to one query.
        outermost = None
        current: ast.AST | None = node
        while current is not None:
            if isinstance(current, ast.Call):
                outermost = current
            current = parents.get(current)
        if outermost is None or id(outermost) in seen:
            continue
        if not any(_is_select_call(child) for child in ast.walk(outermost)):
            continue  # not a query
        seen.add(id(outermost))
        found.append((outermost.lineno, DISCRIMINATOR in _attribute_names(outermost)))
    return found


def test_every_query_on_source_line_id_also_filters_the_document_type() -> None:
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for lineno, discriminated in queries_filtering_on_the_shared_link(path.read_text()):
            if not discriminated:
                offenders.append(f"{path.relative_to(APP.parent)}:{lineno}")
    assert offenders == [], (
        f"these query `{SHARED_LINK}` without `{DISCRIMINATOR}`: {offenders}. That column is "
        "shared by goods receipts, landed costs and partner-document companions, so the "
        "lookup returns whichever row the planner reaches first — see this module's docstring "
        "for the franc it cost."
    )


def test_the_guard_recognises_the_shape_it_forbids() -> None:
    """Anti-vacuity. A guard that cannot fail is a guard that is not there."""
    undiscriminated = """
db.scalar(
    select(StockMove).where(
        StockMove.company_id == company_id,
        StockMove.source_line_id == line.id,
    )
)
"""
    assert queries_filtering_on_the_shared_link(undiscriminated) == [(2, False)]

    discriminated = """
db.scalar(
    select(StockMove).where(
        StockMove.company_id == company_id,
        StockMove.source_doc_type == "partner_document",
        StockMove.source_line_id == line.id,
    )
)
"""
    assert queries_filtering_on_the_shared_link(discriminated) == [(2, True)]


def test_the_guard_does_not_accept_a_discriminator_from_a_neighbouring_query() -> None:
    """The unit is the query, not the function.

    Two queries in one function, one of them discriminated: the undiscriminated one must still
    be reported. A file-scoped or function-scoped check would pass this, and passing it is
    exactly how the next row 13 gets written — beside a correct query that lends it cover.
    """
    source = """
def read(db, company_id, line):
    good = db.scalar(
        select(StockMove).where(
            StockMove.source_doc_type == "partner_document",
            StockMove.source_line_id == line.id,
        )
    )
    bad = db.scalar(
        select(StockMove).where(StockMove.source_line_id == line.id)
    )
    return good, bad
"""
    assert queries_filtering_on_the_shared_link(source) == [(3, True), (9, False)]


def test_the_guard_leaves_plain_attribute_reads_alone() -> None:
    """`move.source_line_id` on a row already fetched is not a query and needs no
    discriminator — flagging it would push people to silence the guard rather than obey it."""
    assert queries_filtering_on_the_shared_link("x = move.source_line_id") == []
    assert (
        queries_filtering_on_the_shared_link(
            "StockLine(source_line_id=line.line_id, quantity=q)"
        )
        == []
    )
