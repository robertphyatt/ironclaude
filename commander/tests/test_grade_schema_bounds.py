"""Every schema passed to a local grade() call must bound its output length
through the sampling grammar (maxLength on every string, maxItems on every
array), so a response can never be truncated at the token cap into non-JSON
(v1.1.13: 'Non-JSON response (3905 chars)' from an unbounded
_PROMPT_WAITING_SCHEMA at openai.max_tokens=1024)."""
import re
from pathlib import Path

import pytest

from ironclaude import main as daemon_main
from ironclaude.brain_client import _PERMISSION_SEEKING_SCHEMA
from ironclaude import grader as grader_module
from ironclaude.grader import (
    GRAMMAR_MAX_STRING_LENGTH,
    LLAMA_CPP_MAX_REPETITION_THRESHOLD,
)
from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.shadow_grader import GRADER_VERDICT_SCHEMA

SRC = Path(__file__).resolve().parents[1] / "src" / "ironclaude"

GRADE_SCHEMAS = {
    "main._BRAIN_MSG_SCHEMA": daemon_main._BRAIN_MSG_SCHEMA,
    "main._AWAITING_OP_SCHEMA": daemon_main._AWAITING_OP_SCHEMA,
    "main._PROMPT_WAITING_SCHEMA": daemon_main._PROMPT_WAITING_SCHEMA,
    "brain_client._PERMISSION_SEEKING_SCHEMA": _PERMISSION_SEEKING_SCHEMA,
    "OrchestratorTools._LOCAL_VERDICT_SCHEMA": OrchestratorTools._LOCAL_VERDICT_SCHEMA,
    "OrchestratorTools._LOCAL_CONFIDENCE_SCHEMA": OrchestratorTools._LOCAL_CONFIDENCE_SCHEMA,
    "OrchestratorTools._LOCAL_HEALTH_SCHEMA": OrchestratorTools._LOCAL_HEALTH_SCHEMA,
    "shadow_grader.GRADER_VERDICT_SCHEMA": GRADER_VERDICT_SCHEMA,
}


def _unbounded(node, path="$"):
    """Paths of string nodes lacking maxLength and array nodes lacking maxItems."""
    found = []
    if not isinstance(node, dict):
        return found
    t = node.get("type")
    types = t if isinstance(t, list) else [t]
    if "string" in types and "maxLength" not in node:
        found.append(path)
    if "array" in types and "maxItems" not in node:
        found.append(path)
    for key, child in (node.get("properties") or {}).items():
        found.extend(_unbounded(child, f"{path}.{key}"))
    if "items" in node:
        found.extend(_unbounded(node["items"], f"{path}[]"))
    return found


def test_walker_detects_unbounded_string_and_array():
    # Positive control: the walker must flag what it exists to catch.
    schema = {"type": "object", "properties": {
        "s": {"type": ["string", "null"]},
        "a": {"type": "array", "items": {"type": "object", "properties": {"v": {"type": "string"}}}},
    }}
    assert _unbounded(schema) == ["$.s", "$.a", "$.a[].v"]


@pytest.mark.parametrize("name", sorted(GRADE_SCHEMAS))
def test_grade_schema_is_grammar_bounded(name):
    assert _unbounded(GRADE_SCHEMAS[name]) == [], name


def test_prompt_waiting_interaction_block_is_grammar_capped():
    # Deliberately below tmux_manager.py's 4096 validator ceiling; see the bisect findings note.
    props = daemon_main._PROMPT_WAITING_SCHEMA["properties"]
    assert props["interaction_block"]["maxLength"] == GRAMMAR_MAX_STRING_LENGTH
    assert props["options"]["maxItems"] == 16


def test_no_inline_schema_literal_reaches_call_local_grader():
    # Completeness: every orchestrator local-grade schema must be a named,
    # guard-covered constant — an inline literal would escape this test.
    src = (SRC / "orchestrator_mcp.py").read_text()
    assert re.findall(r"_call_local_grader\(\s*\w+,\s*\w+,\s*\{", src) == []
    assert re.findall(r"\b(?:confidence|health)_schema\s*=\s*\{", src) == []


def _max_lengths(node, path="$"):
    """(path, maxLength) for every node carrying maxLength."""
    found = []
    if not isinstance(node, dict):
        return found
    if "maxLength" in node:
        found.append((path, node["maxLength"]))
    for key, child in (node.get("properties") or {}).items():
        found.extend(_max_lengths(child, f"{path}.{key}"))
    if "items" in node:
        found.extend(_max_lengths(node["items"], f"{path}[]"))
    return found


def test_grammar_cap_pinned_to_stock_llama_cpp_ceiling():
    # llama.cpp hardcodes MAX_REPETITION_THRESHOLD 2000 (llama-grammar.cpp) and
    # maxLength compiles to char{0,N}; measured live, 1999 passes and 2000 fails
    # (docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md). A bump to
    # 2000 or beyond breaks every grade call with HTTP 500.
    assert LLAMA_CPP_MAX_REPETITION_THRESHOLD == 2000
    assert GRAMMAR_MAX_STRING_LENGTH == LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1 == 1999
    assert not hasattr(grader_module, "GRAMMAR_MAXLENGTH_HIGHEST_PASS")


@pytest.mark.parametrize("name", sorted(GRADE_SCHEMAS))
def test_every_max_length_within_grammar_cap(name):
    over = [(p, n) for p, n in _max_lengths(GRADE_SCHEMAS[name]) if n > GRAMMAR_MAX_STRING_LENGTH]
    assert over == [], name
