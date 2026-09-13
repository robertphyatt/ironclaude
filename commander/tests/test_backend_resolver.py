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


def test_unset_backend_defaults_to_ollama_for_grader():
    resolved = resolve_backend({}, "grader")
    assert resolved.backend == "ollama"
