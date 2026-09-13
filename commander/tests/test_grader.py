"""Unit tests for LocalGrader."""
import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from ironclaude.communication_profiles import CommunicationProfileError, PROFILE_READY_MARKER
from ironclaude.grader import LocalGrader
from ironclaude.ollama_client import OllamaConnectionError, OllamaTimeoutError


SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"valid": {"type": "boolean"}},
    "required": ["valid"],
}


def _make_grader():
    """LocalGrader with a pre-injected mock client (no real network calls)."""
    grader = LocalGrader(config_path="/nonexistent/config.json")
    mock_client = MagicMock()
    grader._client = mock_client
    return grader, mock_client


def _canonical_lossless_skill() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "worker" / "skills" / "write-lossless-ai-messages" / "SKILL.md"
    ).read_text(encoding="utf-8")


class TestHappyPath:
    def test_exact_lossless_skill_precedes_untrusted_input(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true}'

        result = grader.grade("TRUSTED_SYSTEM", "UNTRUSTED_USER", SIMPLE_SCHEMA)

        prompt = mock_client.post_generate.call_args.args[0]["prompt"]
        canonical = _canonical_lossless_skill()
        assert canonical in prompt
        assert prompt.index(canonical) < prompt.index("UNTRUSTED_USER")
        assert result == {"valid": True}
        assert PROFILE_READY_MARKER not in json.dumps(result)

    def test_valid_json_returned_as_dict(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}

    def test_payload_includes_prompt_model_options_format(self):
        grader, mock_client = _make_grader()
        grader._cfg = {"model": "test-model:7b"}
        mock_client.post_generate.return_value = '{"valid": true}'
        grader.grade("the system prompt", "the user prompt", SIMPLE_SCHEMA)
        payload = mock_client.post_generate.call_args[0][0]
        assert payload["model"] == "test-model:7b"
        assert "the system prompt" in payload["prompt"]
        assert "the user prompt" in payload["prompt"]
        assert payload["stream"] is False
        assert payload["options"]["temperature"] == 0.1
        assert payload["options"]["num_predict"] == -1
        assert payload["format"] == SIMPLE_SCHEMA

    def test_no_schema_omits_format_from_payload(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"anything": 1}'
        grader.grade("sys", "user")
        payload = mock_client.post_generate.call_args[0][0]
        assert "format" not in payload


class TestErrorPaths:
    def test_missing_lossless_skill_is_infrastructure_failure_before_ollama(self):
        grader, mock_client = _make_grader()
        with patch(
            "ironclaude.grader.apply_communication_profile",
            side_effect=CommunicationProfileError("communication skill missing or unreadable"),
        ):
            result = grader.grade("sys", "untrusted", SIMPLE_SCHEMA)

        assert result == {
            "infrastructure_error": True,
            "error_detail": "communication skill missing or unreadable",
        }
        mock_client.post_generate.assert_not_called()

    def test_ollama_connection_error_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.side_effect = OllamaConnectionError("refused")
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "refused" in result["error_detail"]

    def test_ollama_timeout_error_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.side_effect = OllamaTimeoutError("timed out")
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "timed out" in result["error_detail"]

    def test_empty_response_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = ""
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "empty" in result["error_detail"].lower()

    def test_non_json_response_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "This is not JSON at all"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-json" in result["error_detail"].lower()

    def test_consistent_error_shape_across_all_paths(self):
        cases = [
            ("connection", OllamaConnectionError("fail"), None),
            ("timeout", OllamaTimeoutError("timeout"), None),
        ]
        for label, exc, _ in cases:
            grader, mock_client = _make_grader()
            mock_client.post_generate.side_effect = exc
            result = grader.grade("sys", "user", SIMPLE_SCHEMA)
            assert result.get("infrastructure_error") is True, label
            assert isinstance(result.get("error_detail"), str), label
            assert len(result["error_detail"]) > 0, label


class TestThinkTagStripping:
    def test_think_tags_stripped_before_parse(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '<think>reasoning here</think>{"valid": true}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}

    def test_multiline_think_tags_stripped(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '<think>\nline 1\nline 2\n</think>{"valid": false}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": False}

    def test_think_stripped_but_remaining_non_json_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "<think>reasoning</think>not valid json"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-json" in result["error_detail"].lower()


class TestSchemaValidation:
    def test_missing_required_field_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"other": "field"}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "missing required" in result["error_detail"].lower()

    def test_all_required_fields_present_returns_parsed(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true, "extra": "ok"}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["valid"] is True

    def test_no_schema_skips_required_field_check(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"whatever": 42}'
        result = grader.grade("sys", "user")
        assert result == {"whatever": 42}


class TestNonDictVerdict:
    def test_bare_true_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "true"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-dict" in result["error_detail"].lower()

    def test_bare_number_returns_infrastructure_error(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "42"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True
        assert "non-dict" in result["error_detail"].lower()

    def test_bare_list_does_not_raise(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = "[1, 2, 3]"
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result["infrastructure_error"] is True


class TestMarkdownFenceStripping:
    def test_json_fence_stripped_before_parse(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '```json\n{"valid": true}\n```'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}

    def test_bare_fence_stripped_before_parse(self):
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '```\n{"valid": false}\n```'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": False}


class TestConfigLoading:
    def test_config_absent_uses_localhost_defaults(self, tmp_path):
        with patch("ironclaude.grader.OllamaClient") as MockClient:
            MockClient.return_value.post_generate.return_value = '{"valid": true}'
            grader = LocalGrader(config_path=str(tmp_path / "nonexistent.json"))
            grader.grade("sys", "user", SIMPLE_SCHEMA)
        MockClient.assert_called_once_with(
            url="http://localhost:11434",
            fallback_url=None,
            timeout=120,
            connect_timeout=3,
            probe_timeout=3,
        )


class TestTimeoutOverride:
    def test_timeout_override_beats_config(self, tmp_path):
        import json as _json
        cfg = tmp_path / "c.json"
        cfg.write_text(_json.dumps({"ollama": {"url": "http://x"}, "timeout_seconds": 600}))
        assert LocalGrader(config_path=str(cfg), timeout=15)._get_client()._timeout == 15

    def test_config_timeout_used_when_no_override(self, tmp_path):
        import json as _json
        cfg = tmp_path / "c.json"
        cfg.write_text(_json.dumps({"ollama": {"url": "http://x"}, "timeout_seconds": 600}))
        assert LocalGrader(config_path=str(cfg))._get_client()._timeout == 600


class TestKeepAliveAndTruncation:
    def test_keep_alive_in_payload_when_set(self):
        grader = LocalGrader(config_path="/nonexistent/config.json", keep_alive="30m")
        grader._client = MagicMock()
        grader._client.post_generate.return_value = '{"valid": true}'
        grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert grader._client.post_generate.call_args[0][0]["keep_alive"] == "30m"

    def test_no_keep_alive_when_unset(self):
        # guards the untouched 600s grader path: no keep_alive when not constructed with one
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true}'
        grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert "keep_alive" not in mock_client.post_generate.call_args[0][0]

    def test_truncate_middle_bounds_length(self):
        from ironclaude.grader import truncate_middle
        out = truncate_middle("A" * 5000, head=1500, tail=500)
        assert len(out) <= 1500 + 500 + 60
        assert out.startswith("A" * 100) and out.endswith("A" * 100)
        assert truncate_middle("short", 1500, 500) == "short"


class TestConfigHotReload:
    def test_client_rebuilt_when_config_mtime_changes(self, tmp_path):
        import json as _json, os
        cfg = tmp_path / "c.json"
        cfg.write_text(_json.dumps({"ollama": {"url": "http://old"}}))
        g = LocalGrader(config_path=str(cfg))
        c1 = g._get_client()
        assert c1._url == "http://old"
        cfg.write_text(_json.dumps({"ollama": {"url": "http://new"}}))
        os.utime(str(cfg), (10_000, 10_000))    # bump mtime
        c2 = g._get_client()
        assert c2._url == "http://new" and c2 is not c1

    def test_deleted_config_keeps_last_client_no_rebuild_spam(self, tmp_path):
        import json as _json, os
        cfg = tmp_path / "c.json"
        cfg.write_text(_json.dumps({"ollama": {"url": "http://old"}}))
        g = LocalGrader(config_path=str(cfg))
        c1 = g._get_client()
        os.remove(str(cfg))
        assert g._get_client() is c1            # missing file -> keep cached client


class TestFastLaneReadTimeout:
    def test_no_read_timeout_uses_normal_cached_client_unchanged(self):
        """grade() with no read_timeout must not touch the fast-lane path at all."""
        grader, mock_client = _make_grader()
        mock_client.post_generate.return_value = '{"valid": true}'
        result = grader.grade("sys", "user", SIMPLE_SCHEMA)
        assert result == {"valid": True}
        mock_client.post_generate.assert_called_once()
        assert grader._fast_client is None

    def test_read_timeout_builds_separate_client_with_configured_timeout(self, tmp_path):
        with patch("ironclaude.grader.OllamaClient") as MockClient:

            def _make(**kw):
                c = MagicMock()
                c._timeout = kw.get("timeout")
                c.post_generate.return_value = '{"valid": true}'
                return c

            MockClient.side_effect = _make
            grader = LocalGrader(config_path=str(tmp_path / "nonexistent.json"))

            # Build + use the normal (600s-style) client first.
            normal_client = grader._get_client()

            # Now grade with a fast-lane read_timeout.
            result = grader.grade("sys", "user", SIMPLE_SCHEMA, read_timeout=2)

        assert result == {"valid": True}
        assert MockClient.call_count == 2  # one normal + one fast
        fast_client = grader._fast_client
        assert fast_client is not None
        assert fast_client is not normal_client
        assert fast_client._timeout == 2
        assert normal_client._timeout == 120  # unchanged default, not clobbered
        fast_client.post_generate.assert_called_once()
        normal_client.post_generate.assert_not_called()

    def test_second_call_with_same_read_timeout_reuses_cached_fast_client(self, tmp_path):
        with patch("ironclaude.grader.OllamaClient") as MockClient:

            def _make(**kw):
                c = MagicMock()
                c._timeout = kw.get("timeout")
                c.post_generate.return_value = '{"valid": true}'
                return c

            MockClient.side_effect = _make
            grader = LocalGrader(config_path=str(tmp_path / "nonexistent.json"))

            grader.grade("sys", "user", SIMPLE_SCHEMA, read_timeout=2)
            first_fast_client = grader._fast_client
            call_count_after_first = MockClient.call_count

            grader.grade("sys", "user", SIMPLE_SCHEMA, read_timeout=2)

        assert MockClient.call_count == call_count_after_first  # cache hit, no rebuild
        assert grader._fast_client is first_fast_client
        assert first_fast_client.post_generate.call_count == 2


class TestMakeClientConnectProbeBudgets:
    def test_resolved_connect_timeout_threaded_through(self):
        from ironclaude.backend_resolver import make_client, ResolvedBackend
        resolved = ResolvedBackend(
            backend="openai", model="m", url="http://x/v1", connect_timeout=4
        )
        client = make_client(resolved)
        assert client._connect_timeout == 4

    def test_explicit_connect_timeout_overrides_resolved(self):
        from ironclaude.backend_resolver import make_client, ResolvedBackend
        resolved = ResolvedBackend(
            backend="openai", model="m", url="http://x/v1", connect_timeout=4
        )
        client = make_client(resolved, connect_timeout=7)
        assert client._connect_timeout == 7

    def test_resolved_connect_timeout_none_defaults_to_three(self):
        from ironclaude.backend_resolver import make_client, ResolvedBackend
        resolved = ResolvedBackend(
            backend="openai", model="m", url="http://x/v1", connect_timeout=None
        )
        client = make_client(resolved)
        assert client._connect_timeout == 3
