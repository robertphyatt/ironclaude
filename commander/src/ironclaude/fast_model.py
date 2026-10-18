"""Haiku-class fast-model step for the Commander's local-grader spots.

When the configured local box (ollama/openai) fails (any infrastructure_error,
including an open circuit breaker), answers non-JSON, or the backend is "haiku",
LocalGrader and session summarization call the Haiku-class tier: Claude Haiku, or
the codex client's configured Haiku-tier (Luna) model, in the `grader` role's client
order (preferred first). Children leave no session behind: claude runs with
--no-session-persistence, --strict-mcp-config and hooks disabled; codex with
--ephemeral --ignore-user-config --ignore-rules.

The model class is per call (design T1): `tier="haiku"` (default) or `tier="sonnet"`.
Each client's `models.<tier>` names the model. On Codex that is a family word (`luna`, `terra`)
resolved to the newest installed model of the family, or an explicit slug used as-is; with no
model of the family installed, Codex is skipped for the call. Sonnet-class calls never use the
local box; LocalGrader routes them here directly.

Deliberately NOT routed through OrchestratorTools._call_grader, which squashes
answers to {grade, approved, feedback}, holds _grader_lock and uses the grader tier.
Imports nothing from orchestrator_mcp (grader.py must not import it).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Mapping

from ironclaude.provider_config import CodexModelUnavailable, resolve_local_codex_model

logger = logging.getLogger(__name__)

# Measured 2026-10-16 (docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md):
# max(30, ceil(2 x p95 of six concurrent lean Haiku children)).
FAST_MODEL_TIMEOUT_DEFAULT = 30
# Fast-lane (_grade_bounded) allowance when the fast model is the responder:
# ceil(2 x p95 of a single lean Haiku child).
FAST_LANE_FAST_MODEL_SECONDS = 11
# Codex --output-schema is strict (measured): see strict_codex_schema.
CODEX_SCHEMA_STRICT = True
# Measured 2026-10-16 (docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md):
# max(30, ceil(2 x p95 of six concurrent lean Sonnet children)).
SONNET_MODEL_TIMEOUT_DEFAULT = 30

FAST_MODEL_CWD = "/tmp"
_DISABLE_HOOKS_SETTINGS = '{"disableAllHooks":true}'
# Variables dropped from every Claude fast child (claude children only; codex authenticates with
# its own ChatGPT login and gets the env unmodified): the billing/routing strip shared with
# OrchestratorTools._GRADER_ENV_STRIP, plus CLAUDE_CODE_EFFORT_LEVEL, which a worker exports and
# which beats --effort low when inherited (measured 2026-10-17, design R5). The grader keeps its
# own list and sets its effort through _grader_env.
_ENV_STRIP = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_EFFORT_LEVEL",
)
_DEFAULT_CLIENTS = {
    "haiku": (("claude", "claude", "haiku"),),
    "sonnet": (("claude", "claude", "sonnet"),),
}
_CODEX_CLASS_WORD = {"haiku": "luna", "sonnet": "terra"}


def fast_model_timeout(hooks_cfg) -> int:
    """`fast_model_timeout_seconds` from the hooks config, else the measured default."""
    value = hooks_cfg.get("fast_model_timeout_seconds") if isinstance(hooks_cfg, Mapping) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    return FAST_MODEL_TIMEOUT_DEFAULT


def sonnet_model_timeout(hooks_cfg) -> int:
    """`sonnet_model_timeout_seconds` from the hooks config, else the measured default."""
    value = hooks_cfg.get("sonnet_model_timeout_seconds") if isinstance(hooks_cfg, Mapping) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    return SONNET_MODEL_TIMEOUT_DEFAULT


def fast_model_clients(commander_config, tier: str = "haiku") -> list[tuple[str, str, str]]:
    """(client, executable path, model for the class) in `grader` role order, preferred first.
    Missing or malformed provider config -> Claude at that class only."""
    try:
        providers = commander_config["providers"]
        role = providers["roles"]["grader"]
        preferred = role["preferred"]
        names = [preferred] + [name for name in role["clients"] if name != preferred]
        out = []
        for name in names:
            client = providers["clients"][name]
            if name not in ("claude", "codex") or not client.get("enabled", False):
                continue
            model = client["models"][tier]
            if name == "codex":
                try:
                    model = resolve_local_codex_model(model)
                except CodexModelUnavailable as exc:
                    logger.warning("fast model skips codex: %s", exc)
                    continue
            out.append((name, client.get("path") or name, model))
        return out or list(_DEFAULT_CLIENTS[tier])
    except (KeyError, TypeError, AttributeError):
        return list(_DEFAULT_CLIENTS[tier])


def claude_fast_argv(path: str, model: str, schema: dict) -> list[str]:
    # --restricted drops the server-side advisor tool, user plugins, skills and auto-memory (the
    # child self-reported `advisor` without it; tool-audit findings 2.3). --effort low matches the
    # Codex child's model_reasoning_effort="low".
    return [
        path, "-p", "--model", model, "--restricted", "--effort", "low",
        "--tools", "", "--no-session-persistence",
        "--strict-mcp-config", "--settings", _DISABLE_HOOKS_SETTINGS,
        "--output-format", "json", "--json-schema", json.dumps(schema),
    ]


# Tool features the Codex child must not have: it is a text-only classifier, and its prompts
# carry untrusted pane and Brain text (measured 2026-10-17: with the shell on, Luna ran `cat`
# on a local file and returned its contents). Mirrors the Claude child's `--tools ""`.
CODEX_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "view_image", "apps",
    "browser_use", "computer_use", "multi_agent", "image_generation",
    "memories", "plugins", "hooks",
)

# The shared Codex no-tools set (design 2026-10-17 tool-audit-fixes R1): every disabled feature,
# goals and tool_suggest (the Codex Brain's FIXED_OVERRIDES turn these off too), agents.enabled=false
# (with features.multi_agent=false the v2 collaboration tools stayed; agents.enabled=false removed
# them, tool-audit findings 2.4) and web search off. The Commander's fast child and grader, the
# hook's Codex child (plan-validator.sh IC_CODEX_NO_TOOLS_ARGS) and the advisor broker
# (advisor-review.ts CODEX_NO_TOOLS_ARGS) pass these pairs in this order; tests keep them in sync.
CODEX_NO_TOOLS_ARGS = tuple(
    arg
    for value in (
        *(f"features.{name}=false" for name in (*CODEX_DISABLED_FEATURES, "goals", "tool_suggest")),
        "agents.enabled=false",
        'web_search="disabled"',
    )
    for arg in ("-c", value)
)


def codex_fast_argv(path: str, model: str, schema_file: str) -> list[str]:
    return [
        path, "exec", "-c", 'model_reasoning_effort="low"', *CODEX_NO_TOOLS_ARGS, "--json", "--ephemeral",
        "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "--output-schema", schema_file, "-s", "read-only", "-m", model, "-",
    ]


def _nullable(prop: dict) -> dict:
    """Allow null for a formerly optional property (strict mode makes it required)."""
    prop = dict(prop)
    kind = prop.get("type")
    if isinstance(kind, str) and kind != "null":
        prop["type"] = [kind, "null"]
    elif isinstance(kind, list) and "null" not in kind:
        prop["type"] = [*kind, "null"]
    if isinstance(prop.get("enum"), list) and None not in prop["enum"]:
        prop["enum"] = [*prop["enum"], None]
    return prop


def strict_codex_schema(schema: dict) -> dict:
    """Strict copy for ``codex exec --output-schema`` (measured 2026-10-17: Codex rejects a
    schema whose ``required`` omits a property, HTTP 400 invalid_json_schema). Every object
    node gets ``additionalProperties: false`` and lists every property in ``required``; a
    formerly optional property becomes nullable. Recurses through properties, items, anyOf
    and oneOf. Never mutates the input."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if isinstance(out.get("items"), dict):
        out["items"] = strict_codex_schema(out["items"])
    for key in ("anyOf", "oneOf"):
        if isinstance(out.get(key), list):
            out[key] = [strict_codex_schema(sub) for sub in out[key]]
    props = out.get("properties")
    if isinstance(props, dict):
        required = set(out.get("required", []))
        out["properties"] = {
            name: strict_codex_schema(prop) if name in required else _nullable(strict_codex_schema(prop))
            for name, prop in props.items()
        }
        out["required"] = list(props)
    if isinstance(props, dict) or out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def parse_claude_output(stdout: str) -> dict | None:
    """FULL structured_output of the type=="result" event (array or single object)."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, list):
        events = [e for e in payload if isinstance(e, dict) and e.get("type") == "result"]
        event = events[-1] if events else None
    elif isinstance(payload, dict) and (payload.get("type") == "result" or "structured_output" in payload):
        event = payload
    else:
        event = None
    out = event.get("structured_output") if event else None
    return out if isinstance(out, dict) else None


def parse_codex_output(stdout: str) -> dict | None:
    """Last item.completed agent_message text, parsed as a JSON object."""
    text = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
    if not isinstance(text, str):
        return None
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _claude_env() -> dict:
    env = dict(os.environ)
    for var in _ENV_STRIP:
        env.pop(var, None)
    return env


def _run_child(argv, *, input_text, timeout, env):
    """The only subprocess boundary (tests stub it). subprocess.run kills the child on timeout."""
    return subprocess.run(
        argv, input=input_text, cwd=FAST_MODEL_CWD, env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def call_fast_model(system_prompt: str, user_prompt: str, schema: dict | None, *,
                    timeout: float, commander_config=None, tier: str = "haiku") -> dict:
    """Ask the fast model at the given class (haiku default, or sonnet). Returns the FULL structured object, or
    {"infrastructure_error": True, "error_detail": ...}. Never raises."""
    schema = schema or {"type": "object"}
    prompt = f"{system_prompt}\n\n{user_prompt}"
    errors = []
    for client, path, model in fast_model_clients(commander_config, tier):
        label = tier if client == "claude" else f"{_CODEX_CLASS_WORD[tier]}:{model}"
        schema_file = None
        try:
            if client == "codex":
                codex_schema = strict_codex_schema(schema) if CODEX_SCHEMA_STRICT else schema
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    json.dump(codex_schema, handle)
                    schema_file = handle.name
                argv, env, parse = codex_fast_argv(path, model, schema_file), dict(os.environ), parse_codex_output
            else:
                argv, env, parse = claude_fast_argv(path, model, schema), _claude_env(), parse_claude_output
            proc = _run_child(argv, input_text=prompt, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            errors.append(f"{label} timed out after {timeout}s")
            continue
        except Exception as exc:  # noqa: BLE001 — a fast-model failure must never crash a grading caller
            errors.append(f"{label} error: {exc}")
            continue
        finally:
            if schema_file is not None:
                try:
                    os.unlink(schema_file)
                except OSError:
                    pass
        if proc.returncode != 0:
            errors.append(f"{label} exited {proc.returncode}")
            continue
        out = parse(proc.stdout)
        if out is None:
            errors.append(f"{label} returned no structured object")
            continue
        if client == "codex":
            # Strict mode made optional keys required-but-nullable; restore the original shape.
            optional_ok = set(schema.get("required", []))
            out = {key: value for key, value in out.items() if value is not None or key in optional_ok}
        missing = [key for key in schema.get("required", []) if key not in out]
        if missing:
            errors.append(f"{label} answer missing required fields {missing}")
            continue
        logger.info("fast model answered via %s", label)
        return out
    detail = "fast model unavailable: " + "; ".join(errors)
    logger.warning(detail)
    return {"infrastructure_error": True, "error_detail": detail}
