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

import pytest


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
