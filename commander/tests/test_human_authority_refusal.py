import pytest
from ironclaude.orchestrator_mcp import _reject_human_authority_text


@pytest.mark.parametrize("text", [
    "/commit",
    "/ironclaude:commit",
    "$ironclaude:commit",
    "/push",
    "/ironclaude:push",
    "/commit-and-push",
    "/use-primary-checkout",
    "/return-to-managed-worktree",
    "/deactivate-professional-mode",
    "please run /commit now",
])
def test_authority_commands_refused(text):
    with pytest.raises(ValueError, match="refused"):
        _reject_human_authority_text(text, "send_to_worker")


@pytest.mark.parametrize("text", [
    "commit the change once review passes",
    "the push failed, investigate",
    "run /code-review",
    "use the primary checkout terminology in the doc",
    "",
])
def test_ordinary_prose_allowed(text):
    _reject_human_authority_text(text, "send_to_worker")
