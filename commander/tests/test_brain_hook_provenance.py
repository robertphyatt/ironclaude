"""R9: every Brain hook must run a script shipped from the repo. R10: the Codex
Brain's never-trusted project .codex/hooks.json is retired."""
import json

from ironclaude.main import _brain_hook_provenance_errors, _retire_codex_brain_project_hooks

H = "bash $HOME/.claude/ironclaude-hooks/"
D = H + "codex-brain-hooks.sh "


def _fixture(tmp_path, settings_cmds, plugin_cmds):
    commander_hooks = tmp_path / "commander_hooks"
    worker_hooks = tmp_path / "worker_hooks"
    commander_hooks.mkdir()
    worker_hooks.mkdir()
    (commander_hooks / "block-push.sh").write_text("")
    (worker_hooks / "brain-gated-actions.sh").write_text("")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "", "hooks": [{"type": "command", "command": c}]} for c in settings_cmds]}}))
    plugin = tmp_path / "hooks.json"
    plugin.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": c}]} for c in plugin_cmds],
        "SessionStart": [{"hooks": [{"type": "command",
                                     "command": "bash ${CLAUDE_PLUGIN_ROOT}/hooks/episodic-memory-sync.sh --background"}]}],
    }}))
    return [str(settings), str(tmp_path / "missing-local.json")], str(plugin), [str(commander_hooks), str(worker_hooks)]


def test_clean_configuration_has_no_errors(tmp_path):
    args = _fixture(tmp_path, [H + "block-push.sh"], [D + "brain-gated-actions.sh"])
    assert _brain_hook_provenance_errors(*args) == []


def test_settings_script_outside_repo_is_reported(tmp_path):
    args = _fixture(tmp_path, ["bash $HOME/.ironclaude/brain/hooks/local-only.sh"], [])
    errors = _brain_hook_provenance_errors(*args)
    assert len(errors) == 1 and "local-only.sh" in errors[0]


def test_plugin_dispatch_outside_repo_is_reported(tmp_path):
    args = _fixture(tmp_path, [], [D + "not-shipped.sh"])
    errors = _brain_hook_provenance_errors(*args)
    assert len(errors) == 1 and "not-shipped.sh" in errors[0]


def test_corrupt_settings_is_reported(tmp_path):
    settings_paths, plugin, dirs = _fixture(tmp_path, [], [])
    (tmp_path / "settings.json").write_text("{not json")
    errors = _brain_hook_provenance_errors(settings_paths, plugin, dirs)
    assert len(errors) == 1 and "settings.json" in errors[0]


def test_retire_renames_project_hooks(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text("{}")
    _retire_codex_brain_project_hooks(str(tmp_path))
    assert not (codex_dir / "hooks.json").exists()
    assert (codex_dir / "hooks.json.retired").read_text() == "{}"
    _retire_codex_brain_project_hooks(str(tmp_path))  # absent file: no-op
    assert (codex_dir / "hooks.json.retired").read_text() == "{}"
