"""Design 2026-10-17 v120-completeness R4c: after a failed local-box attempt the box is skipped for
local_backoff_minutes (default 15, 0 off). The hooks and the Commander share one marker."""
import json
import os
import stat
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / "worker" / "hooks"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
LOCALHOST = "http://localhost:11434"


def _fast_answer(monkeypatch, obj):
    from ironclaude import fast_model

    def fake(argv, *, input_text, timeout, env):
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"type": "result", "structured_output": obj}]), "")
    monkeypatch.setattr(fast_model, "_run_child", fake)


def _hook(*args):
    script = 'source "$1/hook-logger.sh"; source "$1/plan-validator.sh"; shift; "$@"'
    return subprocess.run(["bash", "-c", script, "probe", str(HOOKS), *args],
                          capture_output=True, text=True, timeout=30)


def test_default_minutes_directory_marker_name_and_endpoints(monkeypatch, tmp_path):
    from ironclaude import local_backoff
    assert local_backoff.DEFAULT_MINUTES == 15
    assert local_backoff.MARKER_NAME == "local-box-backoff.json"
    assert local_backoff.backoff_dir() == Path(os.environ["HOME"]) / ".ironclaude" / "state" / "local-backoff"
    monkeypatch.setenv("IRONCLAUDE_LOCAL_BACKOFF_DIR", str(tmp_path / "custom") + "/")
    assert local_backoff.backoff_dir() == tmp_path / "custom"
    assert local_backoff.endpoint_for("ollama", {}) == LOCALHOST
    assert local_backoff.endpoint_for("ollama", {"url": "http://h:1/"}) == "http://h:1"
    assert local_backoff.endpoint_for("openai", {"base_url": "http://b/v1/"}) == "http://b/v1"
    assert local_backoff.endpoint_for("openai", {}) is None
    assert local_backoff.endpoint_for("haiku", {}) is None


def test_backoff_minutes_resolves_block_then_top_level():
    from ironclaude.local_backoff import backoff_minutes
    assert backoff_minutes({}, "openai") == 15
    assert backoff_minutes({"local_backoff_minutes": 30}, "openai") == 30
    both = {"local_backoff_minutes": 30, "openai": {"local_backoff_minutes": 2}}
    assert backoff_minutes(both, "openai") == 2
    assert backoff_minutes(both, "ollama") == 30
    assert backoff_minutes({"local_backoff_minutes": 0}, "ollama") == 0
    assert backoff_minutes({"local_backoff_minutes": 2.7}, "ollama") == 2
    for bad in ("x", -1, True, None, [5]):
        assert backoff_minutes({"local_backoff_minutes": bad}, "ollama") == 15, bad


def test_failure_marks_the_endpoint_until_now_plus_minutes_and_expiry_retries():
    from ironclaude import local_backoff
    now = 1_000_000.0
    local_backoff.record_failure("http://box.invalid/v1", 15, now=now)
    directory = local_backoff.backoff_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    marker = json.loads((directory / local_backoff.MARKER_NAME).read_text())
    assert marker == {"endpoint": "http://box.invalid/v1", "until": int(now) + 900}
    assert local_backoff.is_backed_off("http://box.invalid/v1", 15, now=now + 899)
    assert not local_backoff.is_backed_off("http://box.invalid/v1", 15, now=now + 900)  # expired
    assert not local_backoff.is_backed_off("http://other.invalid/v1", 15, now=now)


def test_success_clears_only_the_matching_endpoint():
    from ironclaude import local_backoff
    local_backoff.record_failure("http://a.invalid", 15)
    local_backoff.record_success("http://b.invalid")
    assert local_backoff.is_backed_off("http://a.invalid", 15)
    local_backoff.record_success("http://a.invalid")
    assert not (local_backoff.backoff_dir() / local_backoff.MARKER_NAME).exists()


def test_zero_minutes_disables_the_backoff():
    from ironclaude import local_backoff
    local_backoff.record_failure("http://a.invalid", 0)
    assert not (local_backoff.backoff_dir() / local_backoff.MARKER_NAME).exists()
    local_backoff.record_failure("http://a.invalid", 15)
    assert not local_backoff.is_backed_off("http://a.invalid", 0)


def test_marker_refuses_an_unsafe_directory(monkeypatch, tmp_path):
    from ironclaude import local_backoff
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    os.chmod(open_dir, 0o770)
    live = json.dumps({"endpoint": "http://x.invalid", "until": int(time.time()) + 600})
    (open_dir / local_backoff.MARKER_NAME).write_text(live)
    monkeypatch.setenv("IRONCLAUDE_LOCAL_BACKOFF_DIR", str(open_dir))
    assert local_backoff.dir_problem(open_dir) == "has group or other permission bits"
    assert not local_backoff.is_backed_off("http://x.invalid", 15)
    local_backoff.record_failure("http://y.invalid", 15)
    assert (open_dir / local_backoff.MARKER_NAME).read_text() == live  # not rewritten
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(real)
    assert local_backoff.dir_problem(tmp_path / "link") == "is a symlink"
    assert local_backoff.dir_problem(tmp_path / "missing") == "is not a directory"
    assert local_backoff.dir_problem(Path(os.path.realpath("/tmp"))) == "is not owned by the current user"


def test_local_grader_skips_a_backed_off_box_then_retries_after_expiry(monkeypatch):
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import OllamaConnectionError
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.side_effect = OllamaConnectionError("refused")
    assert grader.grade("s", "u", SCHEMA) == {"ok": True, "src": "fast"}
    assert grader._client.post_generate.call_count == 1
    assert local_backoff.is_backed_off(LOCALHOST, 15)
    assert grader.grade("s", "u", SCHEMA) == {"ok": True, "src": "fast"}
    assert grader._client.post_generate.call_count == 1  # skipped while backed off
    marker = local_backoff.backoff_dir() / local_backoff.MARKER_NAME
    data = json.loads(marker.read_text())
    data["until"] = int(time.time()) - 1
    marker.write_text(json.dumps(data))
    grader.grade("s", "u", SCHEMA)
    assert grader._client.post_generate.call_count == 2  # expired: tried again
    assert local_backoff.is_backed_off(LOCALHOST, 15)  # and the failure renewed it


def test_local_grader_success_clears_the_marker(monkeypatch):
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    _fast_answer(monkeypatch, {"ok": False, "src": "fast"})
    local_backoff.record_failure(LOCALHOST, 15, now=time.time() - 3600)  # expired marker
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.return_value = '{"ok": true}'
    assert grader.grade("s", "u", SCHEMA) == {"ok": True}
    assert not (local_backoff.backoff_dir() / local_backoff.MARKER_NAME).exists()


def test_hook_and_commander_share_one_marker_both_ways():
    from ironclaude import local_backoff
    assert _hook("_local_backoff_record", "http://hook.invalid/v1", "15", "fail").returncode == 0
    assert local_backoff.is_backed_off("http://hook.invalid/v1", 15)  # Commander honors the hook's marker
    local_backoff.record_failure("http://cmd.invalid:11434", 15)
    assert _hook("_local_backoff_active", "http://cmd.invalid:11434", "15").returncode == 0  # and vice versa
    assert _hook("_local_backoff_active", "http://hook.invalid/v1", "15").returncode == 1  # one marker
    local_backoff.record_success("http://cmd.invalid:11434")
    assert _hook("_local_backoff_active", "http://cmd.invalid:11434", "15").returncode == 1


def test_hook_and_commander_share_the_default_directory_and_check():
    import re
    from ironclaude import local_backoff
    text = (HOOKS / "plan-validator.sh").read_text()
    assert f"IC_LOCAL_BACKOFF_DEFAULT_MINUTES={local_backoff.DEFAULT_MINUTES}" in text
    assert f'IC_LOCAL_BACKOFF_MARKER_NAME="{local_backoff.MARKER_NAME}"' in text
    assert "${IRONCLAUDE_LOCAL_BACKOFF_DIR:-$HOME/.ironclaude/state/local-backoff}" in text
    check = re.search(r"^_local_backoff_dir_problem\(\) \{\n(.*?)^\}", text, re.S | re.M)
    gate = re.search(r"^gate_root_problem\(\) \{\n(.*?)^\}",
                     (HOOKS / "brain-gated-actions.sh").read_text(), re.S | re.M)
    assert check is not None and gate is not None and check.group(1) == gate.group(1)


def _ollama_config(tmp_path, block=None):
    cfg = {"backend": "ollama"}
    if block is not None:
        cfg["ollama"] = block
    path = tmp_path / "hooks-config.json"
    path.write_text(json.dumps(cfg))
    return str(path)


def test_caller_deadline_read_timeout_writes_and_clears_no_marker(monkeypatch, tmp_path):
    """R4c amendment: a read timeout under a caller read_timeout (0.2 s) shorter than the box's own
    read timeout (no timeout_seconds -> 120 s) is the caller's deadline, not a box failure."""
    from unittest.mock import patch
    import requests
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import OllamaClient
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path=_ollama_config(tmp_path))
    marker = local_backoff.backoff_dir() / local_backoff.MARKER_NAME
    with patch("ironclaude.ollama_client.requests.post", side_effect=requests.ReadTimeout()) as post, \
            patch.object(OllamaClient, "_probe_reachable", return_value=True):
        assert grader.grade("s", "u", SCHEMA, read_timeout=0.2) == {"ok": True, "src": "fast"}
        assert post.call_count == 1
        assert not marker.exists()
        local_backoff.record_failure(LOCALHOST, 15, now=time.time() - 3600)  # an expired marker
        expired = marker.read_text()
        assert grader.grade("s", "u", SCHEMA, read_timeout=0.2) == {"ok": True, "src": "fast"}
        assert post.call_count == 2  # expired: the box was asked
    assert marker.read_text() == expired  # neither cleared nor renewed


def test_read_timeout_at_the_box_configured_timeout_writes_the_marker(monkeypatch, tmp_path):
    """A read timeout at the box's own configured read timeout (ollama.timeout_seconds) is a box failure."""
    from unittest.mock import patch
    import requests
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import OllamaClient
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path=_ollama_config(tmp_path, {"timeout_seconds": 30}))
    with patch("ironclaude.ollama_client.requests.post", side_effect=requests.ReadTimeout()) as post, \
            patch.object(OllamaClient, "_probe_reachable", return_value=True):
        assert grader.grade("s", "u", SCHEMA) == {"ok": True, "src": "fast"}
        assert post.call_args.kwargs["timeout"][1] == 30  # the box's own read timeout was in force
        assert local_backoff.is_backed_off(LOCALHOST, 15)
        local_backoff.record_success(LOCALHOST)
        # A per-call read_timeout equal to the box's own is not shorter: still a box failure.
        assert grader.grade("s", "u", SCHEMA, read_timeout=30) == {"ok": True, "src": "fast"}
        assert post.call_args.kwargs["timeout"][1] == 30
    assert local_backoff.is_backed_off(LOCALHOST, 15)


def test_connect_failure_under_a_caller_deadline_writes_the_marker(monkeypatch, tmp_path):
    """A connect timeout is a box failure even under a short caller read_timeout."""
    from unittest.mock import patch
    import requests
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path=_ollama_config(tmp_path))
    with patch("ironclaude.ollama_client.requests.post", side_effect=requests.ConnectTimeout()) as post:
        assert grader.grade("s", "u", SCHEMA, read_timeout=0.2) == {"ok": True, "src": "fast"}
        assert post.call_count == 1
    assert local_backoff.is_backed_off(LOCALHOST, 15)


OPENAI_BOX = "http://box.invalid/v1"


def _openai_config(tmp_path, block):
    path = tmp_path / "hooks-config.json"
    path.write_text(json.dumps({"backend": "openai", "openai": block}))
    return str(path)


def test_ollama_grader_asks_the_box_while_the_transport_breaker_is_open(monkeypatch, tmp_path):
    """R4c amendment (2026-10-18), one skip rule: only the shared marker makes the grader skip the
    box. An open in-process breaker (3 recorded connect failures, which any caller can feed) does not,
    so at local_backoff_minutes 0 the box is tried on every call."""
    from unittest.mock import patch
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import _BREAKERS
    _fast_answer(monkeypatch, {"ok": False, "src": "fast"})
    for _ in range(3):
        _BREAKERS.record_failure(LOCALHOST)
    assert LOCALHOST in _BREAKERS.open_urls()
    grader = LocalGrader(config_path=_ollama_config(tmp_path, {"local_backoff_minutes": 0}))
    with patch("ironclaude.ollama_client.requests.post") as post:
        post.return_value.json.return_value = {"response": '{"ok": true}'}
        assert grader.grade("s", "u", SCHEMA) == {"ok": True}
        assert post.call_count == 1
        assert post.call_args.args[0] == LOCALHOST + "/api/generate"
    assert _BREAKERS.open_urls() == []  # the outcome is still recorded: the success closed it


def test_openai_grader_asks_the_box_while_the_transport_breaker_is_open(monkeypatch, tmp_path):
    """The same rule for the OpenAI-compatible backend, whose client the grader builds via make_client."""
    from unittest.mock import patch
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import _BREAKERS
    _fast_answer(monkeypatch, {"ok": False, "src": "fast"})
    for _ in range(3):
        _BREAKERS.record_failure(OPENAI_BOX)
    assert OPENAI_BOX in _BREAKERS.open_urls()
    grader = LocalGrader(config_path=_openai_config(
        tmp_path, {"base_url": OPENAI_BOX, "model": "m", "local_backoff_minutes": 0}))
    with patch("ironclaude.openai_client.requests.post") as post:
        post.return_value.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        assert grader.grade("s", "u", SCHEMA) == {"ok": True}
        assert post.call_count == 1
        assert post.call_args.args[0] == OPENAI_BOX + "/chat/completions"
    assert _BREAKERS.open_urls() == []  # the outcome is still recorded: the success closed it


def test_default_clients_still_skip_an_open_breaker():
    """Positive control: a client built without use_breaker (the default every other caller uses)
    still refuses an open breaker and sends no request."""
    from unittest.mock import patch
    import pytest
    from ironclaude.ollama_client import OllamaClient, OllamaConnectionError, _BREAKERS
    from ironclaude.openai_client import OpenAiClient
    for _ in range(3):
        _BREAKERS.record_failure(LOCALHOST)
        _BREAKERS.record_failure(OPENAI_BOX)
    # Both modules call the one requests module, so this patch sees either client's request.
    with patch("ironclaude.ollama_client.requests.post") as post:
        with pytest.raises(OllamaConnectionError, match="circuit open"):
            OllamaClient(LOCALHOST).post_generate({"model": "m", "prompt": "p", "stream": False})
        with pytest.raises(OllamaConnectionError, match="circuit open"):
            OpenAiClient(OPENAI_BOX).post_generate({"model": "m", "messages": []})
        assert post.call_count == 0
