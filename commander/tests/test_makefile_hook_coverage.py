"""Guard: the root Makefile `test-hooks` target must run the commander gate suites.

Regression guard for the Fable v1.1.9 finding F1 — the commander Brain-gate bash
suites (commander/hooks/tests/test-*.sh) were unreachable from any make target, so
those gates could regress to fail-open with `make test` still green. This asserts
the glob is wired into the test-hooks recipe so `make test` exercises them.
"""
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def test_test_hooks_target_runs_commander_gate_suites():
    text = MAKEFILE.read_text()
    # Scope the assertion to the test-hooks recipe (unique anchor `\ntest-hooks:`),
    # so the glob can only satisfy it from inside that target — not a comment or a
    # different target elsewhere in the Makefile.
    recipe = text.split("\ntest-hooks:", 1)[1].split("\n\n", 1)[0]
    # The commander gate suites must be in the test-hooks glob.
    assert "commander/hooks/tests/test-*.sh" in recipe
    # The edit augments — the worker suites must remain wired too.
    assert "worker/hooks/tests/test-*.sh" in recipe
    assert "worker/hooks/test-*.sh" in recipe
