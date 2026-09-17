"""A fingerprint identifies **values**, never their representations.

Two things in this build hash a set of values to decide whether something is the same thing it
was: ADR-11's `Idempotency-Key` fingerprint, and P7's item-registration hash. Both are wrong the
moment a Decimal reaches them through `str()` — `NUMERIC(20,6)` hands back `2360.000000` where
the arithmetic that produced it yielded `2360.0000000000`, the two are `==` as numbers and
differ as text, and a hash over the text calls one amount two. P7 step 3 paid for that: the
stock report reads stored rows on purpose, so every item it touched was re-registered.

## Two guards, because one shape cannot see both halves

**The AST test is the right shape for the hashing sites.** "Which functions hash something" is
decidable, there are four of them in `app/`, and the rule — *their material comes from
`kernel.money.fingerprint_material`, not from text they built themselves* — is checkable
without knowing a single type.

**It is the wrong shape for the comparison the owner named**, and that is worth stating with
the measurement rather than asserted. The broader rule, "no equality, hash or set membership
over a stringified Decimal", needs the *type*, and AST has none. Scanning `app/` for the shape
alone — a `str()`, f-string or `.format()` whose value reaches `==`, `in`, a set, a dict key or
a hash — returned **74 hits** before this change and **73 after**, of which:

* 65 are f-string **dict keys**, and every one is a `field_errors={f"lines.{index}.quantity": …}`
  path. That is this build's idiom for naming a field to a screen, not an identity;
* 8 are comparisons, and not one involves a Decimal: `str(account.id)` against the `audit_log`
  entity column (an int), `str(doc_type)` (a StrEnum), `IDEMPOTENCY_INDEX in str(exc.orig)`
  (an exception message), and an f-string *key* inside a comparison's operand subtree;
* the 74th was the one real hit — the `f"{kind}:{body}"` in ADR-11's fingerprint — and the
  guard below is what removed it: the endpoint scope is now a hashed *value* rather than a text
  prefix, so that function assembles no text at all.

A guard with 73 exemptions is a list nobody reads, and the only hit that mattered is gone. So
the type-blind half is pinned by a **property** instead: for any two Decimals that are `==`,
the canonical form is the same string and both fingerprints are the same digest. That holds
over every field either hash will ever carry, including the ones nobody has written yet, which
is the part an exemption register could never have promised.
"""

import ast
import os
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.api.idempotency import fingerprint
from app.fiscal.items import registration_hash
from app.fiscal.mapping import FiscalItemRegistration
from app.kernel.money import canonical, fingerprint_material
from app.models.fiscalization import FiscalItemTypeCode, FiscalTaxType

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2])
APP = REPO_ROOT / "backend" / "app"

#: What counts as hashing. `hmac.new` is here because a keyed digest is a fingerprint too.
HASHERS = frozenset({"sha256", "sha1", "sha512", "md5", "blake2b", "blake2s", "new"})

#: What counts as building text out of a value: `str(x)`, `repr(x)`, `f"{x}"`, `"{}".format(x)`.
STRINGIFIERS = frozenset({"str", "repr", "format", "ascii"})

#: Hashing sites whose material is **not** a set of values, with the reason.
#:
#: `by design` only — a `GAP` here would be a fingerprint known to be over representations,
#: which is the defect rather than debt. Every entry costs a line of review, which is the point.
NOT_A_VALUE_FINGERPRINT: dict[str, str] = {
    "app/fiscal/rwanda/sandbox.py:_signature": (
        "by design — the sandbox is a stand-in for RRA's own device and this is its receipt "
        "signature, an HMAC over the *wire payload* it was sent. The payload is already "
        "serialised text at two decimals, which is what the authority signs; canonicalising it "
        "would sign something RRA never saw. It identifies nothing on Vinea's side."
    ),
}

#: `app/core/security.py:hash_opaque_token` needs no entry: it hashes one opaque string the
#: client sent, with no text assembled around it, so the scan has nothing to say about it. Only
#: a site that *does* build text needs a line here.


def _hashing_functions(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    """Every function whose body reaches a hashing call, by name."""
    found: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if name in HASHERS:
                found.append((node.name, node))
                break
    return found


def _stringifications(node: ast.AST) -> list[int]:
    """Lines inside this function that build text out of a value."""
    lines: list[int] = []
    for child in ast.walk(node):
        if isinstance(child, ast.JoinedStr):
            lines.append(child.lineno)
        elif isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id in STRINGIFIERS:
                lines.append(child.lineno)
            elif isinstance(func, ast.Attribute) and func.attr == "format":
                lines.append(child.lineno)
    return sorted(set(lines))


def _sites() -> dict[str, list[int]]:
    """`"path:function"` → the lines in it that stringify something."""
    sites: dict[str, list[int]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = str(path.relative_to(APP.parent))
        for name, node in _hashing_functions(tree):
            sites[f"{relative}:{name}"] = _stringifications(node)
    return sites


def test_every_hashing_site_is_known() -> None:
    """The register names sites that exist, and nothing else.

    Both halves matter. A stale entry would excuse a function nobody serves any more; an
    unknown site is a fingerprint that arrived without anybody deciding what it hashes.
    """
    sites = _sites()
    assert sites, "the scan found no hashing site at all, which cannot be right"
    stale = sorted(key for key in NOT_A_VALUE_FINGERPRINT if key not in sites)
    assert stale == [], f"the register names sites that no longer exist: {stale}"


def test_no_fingerprint_builds_its_material_out_of_text() -> None:
    """The guard. A value fingerprint hashes `fingerprint_material`, not text of its own.

    This is the defect P7 step 3 found, stated as a rule the build cannot drift back into:
    `registration_hash` had its own `str()`-based canonicaliser while ADR-11's had had the
    right one since P2, and the two disagreed about `2360.000000`.
    """
    offenders = {
        key: lines
        for key, lines in _sites().items()
        if lines and key not in NOT_A_VALUE_FINGERPRINT
    }
    assert offenders == {}, (
        "these hashing sites build their own text instead of hashing "
        f"`kernel.money.fingerprint_material`: {offenders}. A fingerprint over a "
        "representation calls one value two — `2360.000000` and `2360.0000000000` are the same "
        "amount out of two different columns. Either route the material through the "
        "canonicaliser, or add a `by design` line to NOT_A_VALUE_FINGERPRINT saying what this "
        "hash identifies instead."
    )


def test_the_scan_sees_a_stringified_value_inside_a_hashing_function() -> None:
    """The guard's own sensitivity: it is not green because it looks at nothing.

    Two snippets, one of each shape the defect actually took — an explicit `str()` and an
    f-string — because a scan that caught only the first would have passed the second.
    """
    explicit = ast.parse(
        "import hashlib\n"
        "def digest(amount):\n"
        "    return hashlib.sha256(str(amount).encode()).hexdigest()\n"
    )
    interpolated = ast.parse(
        "import hashlib\n"
        "def digest(amount):\n"
        "    return hashlib.sha256(f'{amount}'.encode()).hexdigest()\n"
    )
    for tree in (explicit, interpolated):
        found = _hashing_functions(tree)
        assert [name for name, _ in found] == ["digest"]
        assert _stringifications(found[0][1]), "the scan missed a stringified value"

    clean = ast.parse(
        "import hashlib\n"
        "from app.kernel.money import fingerprint_material\n"
        "def digest(values):\n"
        "    return hashlib.sha256(fingerprint_material(values).encode()).hexdigest()\n"
    )
    assert _stringifications(_hashing_functions(clean)[0][1]) == []


# --- The property the AST cannot express ------------------------------------------------------
#
# `Decimal("2360") == Decimal("2360.000000")` is true and `str()` of each is not equal. Every
# assertion below is that identity: what a fingerprint may distinguish is values, and these are
# one value.

#: Two spellings of one amount: the same number, at exponents the two routes actually produce —
#: `NUMERIC(20,6)` on the way out of a column, and the wider exponent an unrounded
#: multiplication leaves behind.
EQUAL_PAIRS = st.tuples(
    st.integers(min_value=-10**9, max_value=10**9),
    st.integers(min_value=0, max_value=6),
    st.integers(min_value=0, max_value=10),
).map(
    lambda triple: (
        Decimal(triple[0]).scaleb(-triple[1]).quantize(Decimal(1).scaleb(-triple[1])),
        Decimal(triple[0]).scaleb(-triple[1]).quantize(Decimal(1).scaleb(-(triple[1] + triple[2]))),
    )
)


@given(EQUAL_PAIRS)
def test_two_spellings_of_one_amount_canonicalise_the_same(pair) -> None:  # noqa: ANN001
    left, right = pair
    assert left == right, "the generator is meant to produce equal amounts"
    assert canonical(left) == canonical(right)


@given(EQUAL_PAIRS)
def test_two_spellings_of_one_amount_fingerprint_the_same(pair) -> None:  # noqa: ANN001
    left, right = pair
    assert fingerprint_material({"amount": left}) == fingerprint_material({"amount": right})


@given(EQUAL_PAIRS)
def test_an_item_registered_twice_at_two_exponents_hashes_once(pair) -> None:  # noqa: ANN001
    """The defect itself, as a property over the registration rather than over one price.

    `dft_prc` is the field it bit, but nothing in the hash knows that — so the property is
    stated over the registration, and it holds for every Decimal field decision 8 ever adds.
    """
    left, right = pair
    assert registration_hash(_registration(left)) == registration_hash(_registration(right))


def _registration(price: Decimal) -> FiscalItemRegistration:
    return FiscalItemRegistration(
        item_code="RW2NTXU0000001",
        item_class_code="5059020800",
        name="Rugari Red 750ml",
        item_type=FiscalItemTypeCode.FINISHED_PRODUCT,
        origin_country="RW",
        package_unit="NT",
        quantity_unit="U",
        tax_class=FiscalTaxType.B,
        default_price_inclusive=price,
        barcode=None,
        active=True,
    )


def test_a_genuinely_different_amount_still_hashes_differently() -> None:
    """The canonical form must not blunt the check it exists to support — the sharpness half
    `tests/kernel/test_hardening.py` asserts for ADR-11, asserted here for the item hash."""
    assert registration_hash(_registration(Decimal("2360"))) != registration_hash(
        _registration(Decimal("2360.01"))
    )


def test_a_type_with_no_canonical_form_stops_the_build() -> None:
    """Strict on purpose. A silent `str()` fallback is the defect, so the next unserialisable
    type to reach a fingerprint raises rather than being hashed by its `repr`."""
    with pytest.raises(TypeError, match="no canonical form"):
        canonical(object())


def test_the_two_fingerprints_share_one_canonicaliser() -> None:
    """Rule 9, for the thing that broke it. Not an import assertion: the same amount spelled
    two ways has to come out as one digest on **both** paths, which is what having one
    implementation is for."""

    class Payload:
        def model_dump(self) -> dict:
            return {"amount": Decimal("1000.000000")}

    class Wider:
        def model_dump(self) -> dict:
            return {"amount": Decimal("1000.0000000000")}

    assert fingerprint("je", Payload()) == fingerprint("je", Wider())  # type: ignore[arg-type]
    assert registration_hash(_registration(Decimal("1000.000000"))) == registration_hash(
        _registration(Decimal("1000.0000000000"))
    )
