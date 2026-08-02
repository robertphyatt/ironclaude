"""Controls proving the isolation layers are live.

Each must FAIL if its layer is removed. A vacuous check here would defeat the
mechanism it exists to certify.
"""
from pathlib import Path

import pytest


def test_tripwire_blocks_access_to_real_ironclaude_paths(
    real_home, tripwire_error, trip_ledger
):
    """The audit hook must raise BEFORE the operation executes, and record it.

    Read mode on a path that does not exist, deliberately: a write-mode canary
    would create a file in the operator's real ~/.claude during the RED step —
    the exact harm this test detects.

    Catch the class, not BaseException with a match: in the RED state the bare
    open() raises FileNotFoundError, which is also a BaseException.

    The hook appends to the ledger BEFORE raising, and pytest.raises swallows
    the exception — so this test must assert its own entry and clear it, or the
    autouse teardown turns this passing test into an error and plants a phantom
    trip in both run logs. Asserting-then-clearing proves both layers at once.
    """
    canary = Path(real_home) / ".claude" / "tripwire-canary-does-not-exist"
    with pytest.raises(tripwire_error):
        open(canary)
    assert len(trip_ledger) == 1
    assert trip_ledger[0][0] == "open"
    trip_ledger.clear()


def test_home_equals_the_fake_home(_redirect_home):
    """R8(b): $HOME must resolve to the fake home, not merely differ from real."""
    assert Path.home() == _redirect_home
