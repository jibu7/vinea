"""Every Hypothesis test must be selectable by the nightly deep run.

`.github/workflows/nightly-property.yml` selects with `pytest -m slow`, and the `deep` profile
(300 examples) is the only place a property test is ever driven hard. An unmarked `@given`
test therefore runs at two examples in CI and at three hundred nowhere — it looks like a
property test in the source and behaves like an example test forever.

This is a collect-time guard rather than a convention: the marker is invisible at the call
site of the thing it protects, so nothing but a check like this will notice it going missing.
P5 step 2 added two property machines without it and the omission survived a full review; so
had the three arithmetic properties in `tests/kernel/test_money.py`, since P2.

It checks what **this run** collected, so a subset run (`pytest tests/inventory/`) only proves
it about that subset. CI runs the whole tree, which is where the guarantee is worth having —
and where an unmarked `@given` added tomorrow will fail this test rather than quietly going
undriven.
"""

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2])


def test_every_hypothesis_test_carries_the_slow_marker(request: pytest.FixtureRequest) -> None:
    unmarked = sorted(
        item.nodeid
        for item in request.session.items
        if getattr(getattr(item, "function", None), "is_hypothesis_test", False)
        and item.get_closest_marker("slow") is None
    )

    assert unmarked == [], (
        "these @given tests are invisible to `pytest -m slow`, so the nightly deep run will "
        f"never drive them: {unmarked}"
    )


#: Where the suite that actually gates a commit is invoked. Both are read, because a `slow`
#: deselection added to either would have the same effect and only one of them is CI.
INVOCATION_SITES = (".github/workflows/ci.yml", "Makefile")

#: A `-m` expression that excludes `slow`, however it is quoted or spaced. Matching the
#: *expression* rather than a literal `not slow` means `-m "not (slow or concurrency)"` is
#: caught too, which a string compare would have waved through.
DESELECTION = re.compile(r"""-m\s*["']?[^"']*\bnot\b[^"']*\bslow\b""")


def test_no_pytest_invocation_deselects_the_slow_marker() -> None:
    """The other direction, and the one that decides where a broken guard is *found*.

    The test above makes every property selectable by the nightly. This one keeps them in the
    **per-commit** suite, because `-m slow` is a selector and not a quarantine: the marker says
    "the nightly should drive this hard", not "the normal run should skip it".

    The distinction matters more since P7. Five refusals that the random machine reached only
    by luck now have properties in `test_property_targeted_refusals.py` that **construct** their
    preconditions, so every example reaches the guard — which means two examples catch a broken
    guard just as surely as three hundred do. Proven rather than argued: disabling the
    comparison in `_resolve_purchase_order_line` fails
    `test_a_receipt_beyond_what_the_order_has_left_is_refused` at the `ci` profile, in a run
    that takes ninety seconds.

    So those five guards are covered per commit today, and the only way to lose that is for
    somebody to speed the suite up with `-m "not slow"` — a change that looks like a pure
    win, leaves every test passing, and silently moves five guards to a nightly that gates
    nothing. This is the line of review that costs.
    """
    offenders: list[str] = []
    for relative in INVOCATION_SITES:
        path = REPO_ROOT / relative
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if "pytest" not in line or line.lstrip().startswith("#"):
                continue
            if DESELECTION.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert offenders == [], (
        "a pytest invocation deselects the `slow` marker, which moves every property test — "
        "including the five constructed-precondition guards in "
        "test_property_targeted_refusals.py, which catch a broken guard at two examples — out "
        "of the suite that gates a commit and into a nightly that gates nothing:\n"
        + "\n".join(offenders)
    )
