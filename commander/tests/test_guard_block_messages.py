"""The reviewing-stage block messages must name the set is_review_allowed admits.

They drifted once already: the messages listed the pre-widening allowlist while the
predicate admitted diff, `git -C` forms and env-prefixed pytest. A reader could not
tell "Bash is blocked here" from "this command is not on the list".

Single-word members are checked by comma-token EQUALITY, not substring. `"diff" in
text` is satisfied by "git diff/status/..." and `"ls" in text` by "ls-files", so a
substring check on those can never fail — the cannot-fail defect this repo's own
review checklist exists to catch.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "worker" / "hooks" / "professional-mode-guard.sh"

# Multi-word phrases: substring is unambiguous.
COMPOUND = ("git diff/status/log/show/blame/ls-files/check-ignore",
            "git -C <path>", "make test")
# Single words: must appear as their own comma-delimited token.
TOKENS = ("sqlite3", "pytest", "cat", "head", "tail", "wc", "grep", "rg",
          "find", "ls", "diff")


def _messages() -> list[str]:
    """The two reviewing-stage block message bodies."""
    text = GUARD.read_text()
    parts = (text.split("Allowed commands:")[1:]
             + text.split("Only the following commands are allowed during code review:")[1:])
    assert len(parts) >= 2, "expected two reviewing-stage block messages"
    return [p[:700] for p in parts]


def test_both_messages_name_every_admitted_member():
    for body in _messages():
        for member in COMPOUND:
            assert member in body, f"{member!r} missing from a reviewing-stage block message"
        tokens = {t.strip() for chunk in body.split("\n") for t in chunk.split(",")}
        for member in TOKENS:
            assert member in tokens, f"{member!r} not a standalone token in a block message"
