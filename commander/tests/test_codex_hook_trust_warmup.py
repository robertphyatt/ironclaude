"""R7/R7a: before the Codex Brain starts, a throwaway Codex TUI in a dedicated directory
answers Codex's hook-trust prompt from the rendered screen, then is killed by its exact
session name."""
from unittest.mock import MagicMock

from ironclaude.main import (
    _HOOK_TRUST_WARMUP_SESSION,
    _answer_codex_hooks_review,
    _warm_up_codex_hook_trust,
)

PROMPT = (
    "  Hooks need review\n  9 hooks are new or changed.\n"
    "› 1. Review hooks\n  2. Trust all and\n  continue\n  3. Continue without trusting\n"
)
BANNER = ">_ OpenAI Codex (v0.162.0)\n› Ask Codex to do anything\n"


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Tools:
    """Rendered screens in order; the last one repeats."""

    def __init__(self, screens):
        self.screens = list(screens)
        self.reads = 0

    def _codex_screen(self, session_name, ssh_host):
        assert session_name == _HOOK_TRUST_WARMUP_SESSION and ssh_host is None
        self.reads += 1
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]


def _answer(screens, settle=20.0):
    tools, tmux, clock = _Tools(screens), MagicMock(), _Clock()
    answered = _answer_codex_hooks_review(
        tools, tmux, _HOOK_TRUST_WARMUP_SESSION, settle_seconds=settle,
        sleep=clock.sleep, clock=clock)
    return answered, tmux, tools


def test_prompt_after_banner_is_answered_once():
    answered, tmux, _ = _answer([BANNER, BANNER, PROMPT, BANNER])
    assert answered is True
    tmux.send_raw_keys.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, ["2", "Enter"])


def test_no_prompt_within_settle_window_sends_nothing():
    answered, tmux, tools = _answer([BANNER], settle=5.0)
    assert answered is False
    tmux.send_raw_keys.assert_not_called()
    assert tools.reads >= 5


def test_prompt_that_never_clears_is_pressed_only_once():
    answered, tmux, _ = _answer([PROMPT])
    assert answered is False
    assert tmux.send_raw_keys.call_count == 1


def test_trust_option_without_review_heading_is_not_answered():
    answered, tmux, _ = _answer(["  2. Trust all and continue\n"], settle=3.0)
    assert answered is False
    tmux.send_raw_keys.assert_not_called()


def test_empty_capture_is_not_answered():
    answered, tmux, _ = _answer([""], settle=3.0)
    assert answered is False
    tmux.send_raw_keys.assert_not_called()


def _warmup_tools(screen=""):
    tools = MagicMock()
    tools._wait_for_ready.return_value = True
    tools._codex_screen.return_value = screen
    return tools


def test_warmup_spawns_waits_and_kills(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = _warmup_tools()
    _warm_up_codex_hook_trust(tools, tmux, "/opt/codex", str(tmp_path), timeout=5, settle_seconds=0)
    tmux.spawn_session.assert_called_once_with(
        _HOOK_TRUST_WARMUP_SESSION, "exec /opt/codex --no-daemon", cwd=str(tmp_path))
    tools._wait_for_ready.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, timeout=5, client="codex")
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)


def test_warmup_answers_prompt_before_kill(tmp_path, caplog):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    order = []
    tmux.send_raw_keys.side_effect = lambda *a, **k: order.append("keys")
    tmux.kill_session.side_effect = lambda *a, **k: order.append("kill")
    tools = _warmup_tools()
    tools._codex_screen.side_effect = [PROMPT] + [BANNER] * 5  # clears after the key press
    with caplog.at_level("INFO"):
        _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5, settle_seconds=2)
    assert order == ["keys", "kill"]
    assert "trust_prompt_answered=True" in caplog.text


def test_warmup_kills_even_when_wait_raises(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = _warmup_tools()
    tools._wait_for_ready.side_effect = RuntimeError("boom")
    _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5, settle_seconds=0)
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)


def test_warmup_replaces_leftover_session_and_skips_wait_on_spawn_failure(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = True
    tmux.spawn_session.return_value = False
    tools = _warmup_tools()
    _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5, settle_seconds=0)
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)
    tools._wait_for_ready.assert_not_called()


def test_warmup_reports_unanswered_when_prompt_stays(tmp_path, caplog):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = _warmup_tools(PROMPT)  # never clears: the selection was not confirmed
    with caplog.at_level("INFO"):
        _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5,
                                  settle_seconds=2, clear_seconds=1)
    assert "trust_prompt_answered=False" in caplog.text
    assert "still on screen after answering" in caplog.text
    tmux.send_raw_keys.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, ["2", "Enter"])
