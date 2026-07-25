from unittest.mock import MagicMock
import ironclaude.orchestrator_mcp as omcp
from ironclaude.db import init_db


def _tools(tmp_path, providers_overrides=None):
    conn = init_db(str(tmp_path / "commander.db"))
    cfg = {
        "grader_model": "opus",
        "brain_model": "sonnet",
        "default_opus_model": "opus",
        "advisor": {"advisor_model": "opus", "advisor_models": {}},
        "providers": providers_overrides or {
            "clients": {
                "claude": {"enabled": True, "path": "claude",
                           "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}},
                "codex": {"enabled": False, "path": "codex",
                          "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}},
            },
            "roles": {
                "brain": {"preferred": "claude", "clients": ["claude"]},
                "worker": {"preferred": "claude", "clients": ["claude"]},
                "grader": {"preferred": "claude", "clients": ["claude"]},
                "advisor": {"preferred": "claude", "clients": ["claude"]},
            },
        },
    }
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=conn, config=cfg)


def test_grader_router_builds_and_caches(tmp_path):
    tools = _tools(tmp_path)
    router1, config1, *_ = tools._provider_router()
    router2, _config2, *_ = tools._provider_router()
    assert router1 is router2                      # cached
    assert config1.roles["grader"].preferred == "claude"


def test_ensure_grader_capabilities_records_claude(tmp_path):
    tools = _tools(tmp_path)
    tools._ensure_role_capabilities("grader", "opus")
    _r, _c, state, _reg = tools._provider_router()
    obs = state.capability_observation("local", "claude", "grader", "opus")
    assert obs is not None and obs["configured"] is True


def test_ensure_grader_capabilities_never_raises(tmp_path, monkeypatch):
    tools = _tools(tmp_path)
    from ironclaude.provider_capabilities import CapabilityProbe
    monkeypatch.setattr(CapabilityProbe, "probe_local",
                        MagicMock(side_effect=RuntimeError("boom")))
    tools._ensure_role_capabilities("grader", "opus")       # must not raise
