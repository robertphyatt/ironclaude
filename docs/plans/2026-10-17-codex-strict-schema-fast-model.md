# Codex Strict Schema for the Fast Model Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Codex Luna/Terra fast-model children accept every Commander schema: the Codex arm sends a strict copy (every object node closed, every property required, formerly optional properties nullable), and answers are restored to the original shape.

**Requirements:** `docs/plans/2026-10-17-codex-strict-schema-fast-model-design.md` (R1-R5)

**Architecture:** A pure `strict_codex_schema()` in `commander/src/ironclaude/fast_model.py` replaces the top-level-only `{**schema, "additionalProperties": False}` in the Codex arm of `call_fast_model`. After a Codex answer parses, top-level `None` values for keys the original schema did not require are dropped. The Claude argv and the local box are untouched.

**Tech Stack:** Python 3, pytest, Codex CLI (`codex exec --output-schema`).

**Execution invariants:**
- Shell state does not persist between steps; every command uses absolute paths.
- Bash cwd may be `commander/`; git uses `git -C /Users/roberthyatt/Code/ironclaude`. `docs/` is gitignored, so plan and findings files need `git add -f`.
- Always run pytest with `PYTHONUNBUFFERED=1`.
- Each run writes its own log under `/private/tmp/claude-502/csfm/`.

---

## Task 1: Strict Codex schema in `call_fast_model`

TDD.

**Files:**
- Modify: `commander/src/ironclaude/fast_model.py` (new functions after `codex_fast_argv`; Codex arm and post-parse in `call_fast_model`)
- Test: `commander/tests/test_fast_model.py` (append)
- Create: `docs/plans/2026-10-17-codex-strict-schema-fast-model-findings.md`

### Step 1: Tests (RED)

Append to `commander/tests/test_fast_model.py`:
```python


# ── Codex strict structured output (design 2026-10-17 R1-R4) ──

STRICT_IN = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 5},
        "who": {"type": ["string", "null"]},
        "mode": {"enum": ["a", "b"]},
        "pick": {"type": "string", "enum": ["x", "y"]},
        "opts": {"type": "array", "items": {
            "type": "object", "properties": {"v": {"type": "string"}, "note": {"type": "string"}},
            "required": ["v"]}},
    },
    "required": ["valid", "opts"],
}

STRICT_OUT = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "reason": {"type": ["string", "null"], "maxLength": 5},
        "who": {"type": ["string", "null"]},
        "mode": {"enum": ["a", "b", None]},
        "pick": {"type": ["string", "null"], "enum": ["x", "y", None]},
        "opts": {"type": "array", "items": {
            "type": "object",
            "properties": {"v": {"type": "string"}, "note": {"type": ["string", "null"]}},
            "required": ["v", "note"], "additionalProperties": False}},
    },
    "required": ["valid", "reason", "who", "mode", "pick", "opts"],
    "additionalProperties": False,
}


def test_strict_codex_schema_closes_every_object_and_nullables_optional():
    import copy
    original = copy.deepcopy(STRICT_IN)
    assert fast_model.strict_codex_schema(STRICT_IN) == STRICT_OUT
    assert STRICT_IN == original


def test_strict_codex_schema_keeps_a_propertyless_object_closed():
    assert fast_model.strict_codex_schema({"type": "object"}) == {"type": "object", "additionalProperties": False}


def test_strict_codex_schema_covers_the_commander_schemas():
    from ironclaude import main
    for schema in (main._BRAIN_MSG_SCHEMA, main._AWAITING_OP_SCHEMA, main._PROMPT_WAITING_SCHEMA):
        strict = fast_model.strict_codex_schema(schema)
        assert strict["required"] == list(schema["properties"])
        assert strict["additionalProperties"] is False
    items = fast_model.strict_codex_schema(main._PROMPT_WAITING_SCHEMA)["properties"]["options"]["items"]
    assert items["additionalProperties"] is False


def test_codex_child_gets_strict_schema_and_claude_child_the_original(monkeypatch):
    seen = {}

    def fake(argv, *, input_text, timeout, env):
        if argv[0] == "/bin/claude-x":
            seen["claude"] = json.loads(argv[argv.index("--json-schema") + 1])
            raise subprocess.TimeoutExpired(argv, timeout)
        with open(argv[argv.index("--output-schema") + 1]) as f:
            seen["codex"] = json.load(f)
        return _proc(_codex_jsonl({"valid": True, "reason": "ok", "who": None, "mode": None,
                                   "pick": None, "opts": []}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    fast_model.call_fast_model("S", "U", STRICT_IN, timeout=5,
                               commander_config=_cfg(preferred="claude", clients=("claude", "codex"),
                                                     codex_enabled=True))
    assert seen["claude"] == STRICT_IN
    assert seen["codex"] == STRICT_OUT


def test_codex_answer_drops_null_optional_keys_only(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_codex_jsonl(
        {"valid": False, "reason": None, "who": "w", "mode": None, "pick": None, "opts": []})))
    out = fast_model.call_fast_model("S", "U", STRICT_IN, timeout=5,
                                     commander_config=_cfg(preferred="codex", clients=("codex",),
                                                           codex_enabled=True))
    assert out == {"valid": False, "who": "w", "opts": []}


def test_codex_answer_keeps_null_for_a_required_key(monkeypatch):
    schema = {"type": "object", "properties": {"q": {"type": ["string", "null"]}}, "required": ["q"]}
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_codex_jsonl({"q": None})))
    out = fast_model.call_fast_model("S", "U", schema, timeout=5,
                                     commander_config=_cfg(preferred="codex", clients=("codex",),
                                                           codex_enabled=True))
    assert out == {"q": None}


def test_claude_answer_is_returned_unchanged_positive_control(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_claude_array(
        {"valid": False, "reason": None, "opts": []})))
    out = fast_model.call_fast_model("S", "U", STRICT_IN, timeout=5, commander_config=None)
    assert out == {"valid": False, "reason": None, "opts": []}
```

Run:
```bash
mkdir -p /private/tmp/claude-502/csfm && cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py -q > /private/tmp/claude-502/csfm/red.log 2>&1; echo "red=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/csfm/red.log; tail -n 3 /private/tmp/claude-502/csfm/red.log
```
Expected: `red=1`. These fail:
- with `AttributeError` (no `strict_codex_schema`): `test_strict_codex_schema_closes_every_object_and_nullables_optional`, `test_strict_codex_schema_keeps_a_propertyless_object_closed`, `test_strict_codex_schema_covers_the_commander_schemas`;
- with `AssertionError`: `test_codex_child_gets_strict_schema_and_claude_child_the_original` (the Codex file is top-level-only) and `test_codex_answer_drops_null_optional_keys_only` (the `None` keys are kept).

These pass at RED: `test_codex_answer_keeps_null_for_a_required_key` and `test_claude_answer_is_returned_unchanged_positive_control`, because they guard behavior that must not change. Record the measured list.

### Step 2: Implementation (GREEN) in `fast_model.py`

1. After `codex_fast_argv`, add:
```python


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
```
2. In `call_fast_model`, replace
`codex_schema = {**schema, "additionalProperties": False} if CODEX_SCHEMA_STRICT else schema`
with
`codex_schema = strict_codex_schema(schema) if CODEX_SCHEMA_STRICT else schema`.
3. In `call_fast_model`, immediately after the `if out is None:` block (before `missing = …`), add:
```python
        if client == "codex":
            # Strict mode made optional keys required-but-nullable; restore the original shape.
            optional_ok = set(schema.get("required", []))
            out = {key: value for key, value in out.items() if value is not None or key in optional_ok}
```
4. Update the comment above `CODEX_SCHEMA_STRICT` to `# Codex --output-schema is strict (measured): see strict_codex_schema.`

### Step 3: GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_fast_model_chain.py -q > /private/tmp/claude-502/csfm/green.log 2>&1; echo "green=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/csfm/green.log; tail -n 3 /private/tmp/claude-502/csfm/green.log
```
Expected: `green=0`, 0 failed.

### Step 4: Real Luna probe (R5)

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -c "
import json, shutil, logging
logging.basicConfig(level=logging.INFO)
from ironclaude import fast_model, main
cfg = {'providers': {'clients': {'codex': {'enabled': True, 'path': shutil.which('codex'), 'models': {'haiku': 'gpt-5.6-luna'}}}, 'roles': {'grader': {'preferred': 'codex', 'clients': ['codex']}}}}
for name in ('_BRAIN_MSG_SCHEMA', '_AWAITING_OP_SCHEMA', '_PROMPT_WAITING_SCHEMA'):
    r = fast_model.call_fast_model('Answer per schema.', 'Message: d12 status: tests green. Is it valid? waiting_on neither. kind none.', getattr(main, name), timeout=60, commander_config=cfg)
    print(name, 'INFRA_ERROR' if r.get('infrastructure_error') else 'OK', json.dumps(r)[:300])
" > /private/tmp/claude-502/csfm/probe.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/csfm/probe.log
```
Expected: `probe=0`; each of the three lines reads `OK` with a structured object, and no line reads `INFRA_ERROR`. Before the change, the same probe printed `infrastructure_error` with "luna:gpt-5.6-luna exited 1" for all three, so this check fails on the old code.

### Step 5: Full Commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/csfm/suite.log 2>&1; echo "suite=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/csfm/suite.log; tail -n 3 /private/tmp/claude-502/csfm/suite.log
```
Expected: `suite=0`, 0 failed, and no `FAILED` or `ERROR` lines.

### Step 6: Findings

Write `docs/plans/2026-10-17-codex-strict-schema-fast-model-findings.md`. It records:
- the reproduction and positive control from the design's Evidence section;
- the RED list, the GREEN and suite summary lines, and the probe log contents from Steps 1-5;
- the non-goals carried for the operator (`_GRADER_VERDICT_SCHEMA` / `_call_grader` Codex arm, the Advisor Fallback Astra parity, and the remaining end-review observations).

### Step 7: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/fast_model.py commander/tests/test_fast_model.py; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-codex-strict-schema-fast-model-findings.md docs/plans/2026-10-17-codex-strict-schema-fast-model-design.md docs/plans/2026-10-17-codex-strict-schema-fast-model.md docs/plans/2026-10-17-codex-strict-schema-fast-model.plan.json
```
Expected: no output.
