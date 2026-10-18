"""Conformance tests for backend_resolver against the shared fixture."""
import json
from pathlib import Path

import pytest

from ironclaude.backend_resolver import resolve_backend


def _load_cases():
    fixture = (
        Path(__file__).resolve().parents[2]
        / "worker" / "config-schema" / "resolution-cases.json"
    )
    return json.loads(fixture.read_text())


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_fixture_case(case):
    resolved = resolve_backend(case["config"], case["spot"])
    expect = case["expect"]
    assert resolved.backend == expect["backend"], case["name"]
    assert resolved.model == expect["model"], case["name"]
    assert resolved.url == expect["url"], case["name"]
    assert resolved.connect_timeout == expect.get("connect_timeout"), case["name"]
    assert resolved.probe_timeout == expect.get("probe_timeout"), case["name"]
    assert resolved.hook_validation_budget == expect.get("hook_validation_budget"), case["name"]


def test_unset_backend_defaults_to_haiku_for_grader():
    resolved = resolve_backend({}, "grader")
    assert resolved.backend == "haiku"
    assert resolved.url is None


def test_haiku_backend_takes_only_the_spot_model_and_no_local_url():
    cfg = {"backend": "haiku", "ollama": {"url": "http://x", "model": "m", "fallback_url": "http://y"},
           "openai": {"base_url": "http://o/v1", "model": "om", "max_tokens": 900},
           "shadow_model": "legacy", "spots": {"grader": {"model": "spot-m"}}}
    grader = resolve_backend(cfg, "grader")
    assert (grader.backend, grader.model, grader.url, grader.fallback_url, grader.max_tokens) == (
        "haiku", "spot-m", None, None, None)
    shadow = resolve_backend(cfg, "shadow")
    assert (shadow.backend, shadow.model, shadow.url) == ("haiku", None, None)


def test_explicit_ollama_still_resolves_localhost():
    resolved = resolve_backend({"backend": "ollama"}, "grader")
    assert (resolved.backend, resolved.url) == ("ollama", "http://localhost:11434")
