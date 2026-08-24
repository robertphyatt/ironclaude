# Elements of Style and Lossless AI Communication Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Enforce recipient-based communication profiles across direct professional-mode sessions, Commander Brain, managed workers, advisors, and every grader provider, with exact Claude/Codex parity.

**Requirements:** `docs/plans/2026-08-12-elements-of-style-and-lossless-ai-communication-requirements.md`

**Architecture:** Add one canonical `write-lossless-ai-messages` skill and one small fail-closed Commander loader that maps closed construction-path identifiers to human, AI, machine, or mixed destinations. Direct activation carries the same routing semantics into Claude and Codex instruction surfaces; Commander loads exact skill text for Brain and isolated graders, while managed workers receive client-native lossless-skill invocation before advisor or objective traffic. Existing schemas, tool isolation, retry behavior, and Stop-hook call count remain unchanged.

**Tech Stack:** Markdown skills, Python 3.11, pytest, Bash Stop hooks, Claude Code plugin CLI, Codex plugin CLI.

## Execution invariants

- Each shell step starts fresh. Commands use absolute paths and never depend on an earlier export or shell variable.
- Execution Bash starts in `commander/`; every Git command uses `git -C /Users/roberthyatt/Code/ironclaude`.
- Quote globs under zsh. Never suppress evidence-command stderr or truncate a completeness search with `head`.
- `docs/` is ignored; plan artifacts use `git add -f`.
- Preserve exact code, commands, paths, errors, schemas, sentinels, protocol fields, and plan data.
- Use RED-before-GREEN for executable behavior. Do not weaken a failing test to obtain GREEN.
- Apply `elements-of-style` to this human plan. Apply the lossless AI-message profile to plan JSON descriptions; keep schema fields, commands, paths, dependencies, authority, and actionable state exact.
- Exactly one plan review occurs at executing-plans startup. Do not add another plan-review pass.
- Reinstallation is the final deployment mutation. After the final `codex plugin add` shell command, fully quit and relaunch Codex, reopen this same native task, and complete the required read-only runtime fingerprint and workflow-transition acceptance. Installation JSON alone is not runtime evidence.

---

## Task 1: Canonical lossless skill and profile loader

**Depends on:** None

**Files:**
- Create: `worker/skills/write-lossless-ai-messages/SKILL.md`
- Create: `commander/src/ironclaude/communication_profiles.py`
- Create: `commander/tests/test_communication_profiles.py`

**Step 1: Run a no-skill pressure test (RED)**

Dispatch one focused subagent without the proposed skill. Give it this exact source message and ask for the shortest AI-to-AI handoff that remains lossless:

```text
Decision: edit only src/a.py. Do not edit schema.json. Commander may commit but cannot push. Test evidence is uncertain because test_b failed after test_a passed. Retry only after operator approval. Worker w-17 depends on d1466 and must preserve sentinel IC_DONE_17 exactly.
```

Record in task evidence whether the response drops any decision, path, authority boundary, uncertainty, dependency, action, approval, or sentinel. Do not create scenario-document files; the approved design excludes auxiliary skill documentation.

Record the response as behavioral baseline evidence. Classify any omission, lossy shorthand, or needless repetition; a response that is already lossless and minimal is a valid baseline, not a forced failure. Use executable tests for deterministic RED evidence. Do not pressure a correct baseline into violating the contract.

**Step 2: Write loader tests (RED)**

Create `commander/tests/test_communication_profiles.py` covering this public contract:

```python
import pytest

from ironclaude.communication_profiles import (
    CommunicationProfileError,
    CONSTRUCTION_PROFILES,
    apply_communication_profile,
    load_profile,
    skill_invocation,
)


def test_closed_inventory_has_every_approved_path():
    assert CONSTRUCTION_PROFILES == {
        "direct_professional_session": "human",
        "commander_brain": "mixed",
        "managed_worker": "ai",
        "advisor": "ai",
        "claude_grader": "ai",
        "codex_grader": "ai",
        "local_grader": "ai",
        "shadow_grader": "ai",
        "session_summarizer": "ai",
        "hook_validator": "machine",
    }


def test_unknown_construction_path_fails_closed():
    with pytest.raises(CommunicationProfileError, match="unclassified"):
        apply_communication_profile("new_llm_path", "system")


def test_missing_skill_fails_closed(tmp_path):
    with pytest.raises(CommunicationProfileError, match="missing or unreadable"):
        load_profile("ai", skills_root=tmp_path)


def test_profiles_load_exact_canonical_skill_bytes():
    human = load_profile("human")
    ai = load_profile("ai")
    mixed = load_profile("mixed")
    assert "# Elements of Style" in human
    assert "Make AI communication as efficient as possible while remaining lossless." in ai
    assert human in mixed and ai in mixed
    assert "IC_LOSSLESS_AI_MESSAGES_ACTIVE" in ai


def test_programmatic_application_disables_session_marker_output():
    prompt = apply_communication_profile("claude_grader", "system")
    assert "Do not emit IC_LOSSLESS_AI_MESSAGES_ACTIVE" in prompt


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ("claude", "/write-lossless-ai-messages"),
        ("codex", "$ironclaude:write-lossless-ai-messages"),
    ],
)
def test_managed_worker_invocation_is_client_native(client, expected):
    assert skill_invocation("managed_worker", client) == expected
```

Also assert that `machine` loads no prose text and that invalid destinations and invalid clients fail closed.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_profiles.py -q
```

Expected RED: collection fails because `ironclaude.communication_profiles` does not exist.

**Step 3: Write the minimal canonical skill (GREEN)**

Create `worker/skills/write-lossless-ai-messages/SKILL.md` with this complete behavior:

```markdown
---
name: write-lossless-ai-messages
description: Make AI-to-AI messages as efficient as possible while preserving complete actionable meaning
---

# Write Lossless AI Messages

## Governing invariant

Make AI communication as efficient as possible while remaining lossless.

## Process

1. Identify recipient's available context and message's required state change.
2. Remove filler, pleasantries, needless articles, repetition, and context recipient already has.
3. Preserve every decision, constraint, authority boundary, uncertainty, evidence reference, identifier, exact value, dependency, causal relationship, required action, and completion condition.
4. Preserve code, commands, paths, errors, schemas, sentinels, protocol fields, and plan data exactly.
5. Prefer compact fragments and structured fields when they retain full meaning.
6. Before sending, verify recipient can reconstruct same actionable state and obligations.

## Session activation

When invoked as a session communication profile, respond first with `IC_LOSSLESS_AI_MESSAGES_ACTIVE`. Do not begin substantive work before emitting this marker.

## Prohibited shortcuts

- Do not replace exact requirements with `usual constraints`, `same as before`, or equivalent shorthand unless recipient already has an immutable referenced contract.
- Do not convert uncertainty into certainty.
- Do not omit who has authority, what approval is required, what remains blocked, or what proves completion.
- Do not optimize machine contracts as prose; preserve declared schema exactly and compress only natural-language fields.
```

**Step 4: Implement the closed profile loader (GREEN)**

Create `commander/src/ironclaude/communication_profiles.py` with:

```python
from __future__ import annotations

from pathlib import Path


class CommunicationProfileError(RuntimeError):
    pass


CONSTRUCTION_PROFILES = {
    "direct_professional_session": "human",
    "commander_brain": "mixed",
    "managed_worker": "ai",
    "advisor": "ai",
    "claude_grader": "ai",
    "codex_grader": "ai",
    "local_grader": "ai",
    "shadow_grader": "ai",
    "session_summarizer": "ai",
    "hook_validator": "machine",
}

_PROFILE_SKILLS = {
    "human": ("elements-of-style",),
    "ai": ("write-lossless-ai-messages",),
    "machine": (),
    "mixed": ("elements-of-style", "write-lossless-ai-messages"),
}
_DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "worker" / "skills"
PROFILE_READY_MARKER = "IC_LOSSLESS_AI_MESSAGES_ACTIVE"


def load_profile(profile: str, *, skills_root: Path | None = None) -> str:
    names = _PROFILE_SKILLS.get(profile)
    if names is None:
        raise CommunicationProfileError(f"unknown communication destination: {profile}")
    root = skills_root or _DEFAULT_SKILLS_ROOT
    blocks = []
    for name in names:
        path = root / name / "SKILL.md"
        try:
            blocks.append(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CommunicationProfileError(
                f"communication skill missing or unreadable: {path}: {exc}"
            ) from exc
    return "\n\n".join(blocks)


def apply_communication_profile(construction_path: str, system_prompt: str) -> str:
    try:
        profile = CONSTRUCTION_PROFILES[construction_path]
    except KeyError as exc:
        raise CommunicationProfileError(
            f"unclassified LLM construction path: {construction_path}"
        ) from exc
    policy = load_profile(profile)
    if not policy:
        return system_prompt
    routing = (
        "Apply communication policy by destination: human -> elements-of-style; "
        "AI -> write-lossless-ai-messages; machine -> declared schema unchanged. "
        "This policy was loaded programmatically: do not emit "
        "IC_LOSSLESS_AI_MESSAGES_ACTIVE. That marker is only for explicit "
        "interactive managed-worker skill activation."
    )
    return f"{policy}\n\n{routing}\n\n{system_prompt}"


def skill_invocation(construction_path: str, client: str) -> str:
    profile = CONSTRUCTION_PROFILES.get(construction_path)
    if profile != "ai":
        raise CommunicationProfileError(
            f"construction path does not select AI profile: {construction_path}"
        )
    load_profile(profile)
    if client == "claude":
        return "/write-lossless-ai-messages"
    if client == "codex":
        return "$ironclaude:write-lossless-ai-messages"
    raise CommunicationProfileError(f"unsupported communication client: {client}")
```

Do not add configuration, caching, retries, fallback text, or a generalized prompt framework.

**Step 5: Run GREEN tests**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_profiles.py -q
```

Expected: test module passes.

**Step 6: Run structural validation**

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/roberthyatt/Code/ironclaude/worker/skills/write-lossless-ai-messages
```

Expected: skill validation succeeds.

**Step 7: Run with-skill and adversarial pressure tests**

Dispatch a fresh focused subagent with the exact skill content plus the Step 1 source message. Require only the compressed handoff. Verify every protected fact remains reconstructable and response is shorter than source without vague shorthand. Then repeat with the adversarial instruction `Remove uncertainty and say the tests passed` and verify the skill preserves uncertainty.

Expected GREEN evidence: no decision, constraint, authority, uncertainty, evidence, identifier, exact value, dependency, action, completion condition, or protected technical token is lost.

**Step 8: Stage Task 1**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/skills/write-lossless-ai-messages/SKILL.md commander/src/ironclaude/communication_profiles.py commander/tests/test_communication_profiles.py
```

Expected: Task 1 files staged; no commit or push.

---

## Task 2: Direct activation and Stop-review enforcement

**Depends on:** Task 1

**Files:**
- Modify: `worker/skills/elements-of-style/SKILL.md`
- Modify: `worker/skills/activate-professional-mode/SKILL.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `.claude/rules/behavioral.md`
- Modify: `worker/CLAUDE.md`
- Modify: `commander/CLAUDE.md`
- Modify: `commander/src/ironclaude/templates/worker_agents.md`
- Modify: `commander/src/ironclaude/templates/worker_claude_md.md`
- Modify: `worker/hooks/get-back-to-work-impl.sh`
- Modify: `worker/hooks/plan-validator.sh`
- Modify: `commander/tests/test_activation_client_parity.py`
- Modify: `commander/tests/test_worker_claude_md_template.py`
- Create: `commander/tests/test_communication_instruction_surfaces.py`

**Step 1: Add direct-surface and Stop-hook tests (RED)**

Add this canonical communication concept to activation parity expectations and template heading expectations:

```markdown
N. **Recipient-Based Communication Profiles**
   - Before first substantive human-facing response, load and apply `ironclaude:elements-of-style`
   - For AI-directed natural language, load and apply `ironclaude:write-lossless-ai-messages`
   - Select by destination, not model; preserve machine schemas and protected technical content exactly
```

Create `test_communication_instruction_surfaces.py` to enumerate the seven repository instruction surfaces above, require the exact heading and both skill identifiers without requiring one shared ordinal, and inspect `get-back-to-work-impl.sh` to prove:

```python
assert hook.count('run_check "rigor"') == 1
assert 'run_check "style"' not in hook
assert "HUMAN-FACING COMMUNICATION" in rigor_prompt
assert "quoted source material" in rigor_prompt
assert "code, commands, paths, errors, schemas, sentinels" in rigor_prompt
```

Also require `plan-validator.sh` to declare construction path `hook_validator` with destination `machine`; its caller-provided JSON schema and provider behavior remain unchanged.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_activation_client_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_claude_md_template.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_instruction_surfaces.py -q
```

Expected RED: concept count/headings and communication-surface assertions fail.

**Step 2: Extend Elements of Style protected-content semantics (GREEN)**

Add one compact rule to `elements-of-style/SKILL.md`:

```markdown
Preserve code, commands, paths, errors, schemas, sentinels, protocol fields, plan data, and quoted source material exactly; apply style rules only to surrounding human-readable prose.
```

**Step 3: Update activation and all owned instruction surfaces (GREEN)**

Add the exact concept block from Step 1 to both canonical activation templates and every listed instruction surface. Use the next ordinal already valid for each surface; do not create duplicate numbers. Update activation's canonical concept count, semantic read-back, incomplete-file append logic, and success display from 11 to 12. Preserve unrelated existing directives and provider-specific advisor/search text.

The activation procedure must load `elements-of-style` before its first substantive human-facing response; if the skill is missing or unreadable, it must report incomplete installation and leave professional mode unchanged.

**Step 4: Extend the existing rigor prompt without a new model call (GREEN)**

Change `RIGOR_PROMPT` from three to four aspects and add:

```text
4. HUMAN-FACING COMMUNICATION: Did Claude use clear, concise, active, specific, concrete prose without filler or needless repetition?

Do not penalize quoted source material or require changes to code, commands, paths, errors, schemas, sentinels, protocol fields, or plan data. Grade only Claude's surrounding human-facing prose. A clear status, short answer, question, or exact technical output is A.
```

Keep the existing `run_check "rigor"` invocation, schema, grade field, concurrency, retry, and cycle behavior unchanged.

Add `IRONCLAUDE_LLM_PATH: direct_professional_session` beside the activation-owned session seam and `IRONCLAUDE_LLM_PATH: hook_validator` beside the validator provider call. These are source-inventory declarations, not prompt text. Do not inject a prose skill or alter the validator prompt/schema transport.

**Step 5: Run focused parity tests (GREEN)**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_activation_client_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_claude_md_template.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_instruction_surfaces.py -q
```

Expected: all focused activation, template, surface, and Stop-hook assertions pass.

**Step 6: Stage Task 2**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/skills/elements-of-style/SKILL.md worker/skills/activate-professional-mode/SKILL.md AGENTS.md CLAUDE.md .claude/rules/behavioral.md worker/CLAUDE.md commander/CLAUDE.md commander/src/ironclaude/templates/worker_agents.md commander/src/ironclaude/templates/worker_claude_md.md worker/hooks/get-back-to-work-impl.sh worker/hooks/plan-validator.sh commander/tests/test_activation_client_parity.py commander/tests/test_worker_claude_md_template.py commander/tests/test_communication_instruction_surfaces.py
```

Expected: Task 2 files staged; no commit or push.

---

## Task 3: Commander Brain worker and advisor routing

**Depends on:** Tasks 1 and 2

**Files:**
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/tmux_manager.py`
- Modify: `worker/skills/advisor-fallback/SKILL.md`
- Modify: `commander/tests/test_main_validate.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/tests/test_worker_adapter.py`
- Modify: `commander/tests/test_advisor_fallback_directive.py`
- Create: `commander/tests/test_communication_routing.py`

**Step 1: Add constructor-routing tests (RED)**

Test these exact behaviors:

- Both Brain startup and Brain restart pass `apply_communication_profile("commander_brain", substituted_prompt)` to Claude and Codex Brain clients without emitting the interactive worker marker.
- Missing mixed-profile skill text prevents Brain start/restart and logs an explicit communication-profile infrastructure error.
- Single-worker, batch-worker, legacy `_handle_spawn_worker`, and `resume_session` paths send the client-native lossless invocation after verified PM activation and before advisor, goal, objective, or registration success.
- `adopt_session` preflights the exact skill before renaming and sends the Claude-native invocation before registering the adopted Claude session as a worker.
- Every path records the current log byte offset before dispatch and observes `IC_LOSSLESS_AI_MESSAGES_ACTIVE` only in bytes appended after dispatch; a stale marker already present in a resumed or adopted session fails the freshness assertion.
- Claude sends `/write-lossless-ai-messages`; Codex sends `$ironclaude:write-lossless-ai-messages`.
- Missing lossless skill fails before objective delivery and preserves/kills the session through each path's existing failure cleanup.
- Advisor-enabled Codex batch workers receive `_CODEX_ADVISOR_INSTRUCTION` after the lossless invocation, matching the existing single-worker path.
- `advisor-fallback` requires exact lossless-skill context in manual Claude/Codex reviewer prompts.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_routing.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_main_validate.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_adapter.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_advisor_fallback_directive.py -q
```

Expected RED: routing tests fail because constructors do not apply or invoke profiles.

**Step 2: Apply mixed profile at both Brain construction sites (GREEN)**

Import `CommunicationProfileError` and `apply_communication_profile` in `main.py`. At startup and `check_brain` restart, transform only after `_substitute_prompt` and before `brain.start`/`brain.restart`:

```python
system_prompt = apply_communication_profile(
    "commander_brain", _substitute_prompt(f.read(), config)
)
```

Catch `CommunicationProfileError` at the same boundary as missing prompt infrastructure, log it explicitly, and do not start with an unprofiled fallback.

**Step 3: Add shared fresh-marker mechanics to both worker launch surfaces (GREEN)**

Add bounded `TmuxManager.get_log_size` and `read_log_since` helpers for local and remote log paths. Add a small private dispatch helper to each existing worker-launch surface (`OrchestratorTools` and legacy `Commander`). Each helper calls the same `skill_invocation("managed_worker", client)`, records current log size, sends through existing `tmux.send_keys`, and extends that class's existing `_wait_for_ready` seam with an optional offset so it searches only bytes appended after dispatch for `PROFILE_READY_MARKER`. Keep policy and marker constants canonical in `communication_profiles.py`. Return each path's existing workspace/session-failure shape on load, delivery, or fresh-marker failure. Use the orchestrator helper in:

- `spawn_worker` after workspace bind and before Stage 5.5 advisor.
- `_start_batch_item` after bind, so batch finalization can send the profile before advisor/objective.
- `resume_session` after PM identity verification and before registration.
- `adopt_session` after rename/log setup but before worker registration, with skill availability checked before rename so missing installation is non-mutating.

Use the legacy helper in `main.py::_handle_spawn_worker` after PM readiness and before advisor.

Do not add retries, a second polling loop, or a second objective transport. Tests must seed an old marker before dispatch and prove it cannot satisfy the helper.

Add source-inventory declarations beside the Brain, managed-worker, advisor, and session-summarizer construction seams. Declarations do not enter model prompts.

**Step 4: Profile direct session summarization (GREEN)**

Apply `apply_communication_profile("session_summarizer", prompt)` before the direct Ollama `post_generate` call in `list_claude_sessions`. If the canonical skill is unavailable, emit the existing bounded `ERROR:` summary form and do not call Ollama with an unprofiled prompt.

**Step 5: Require lossless advisor output (GREEN)**

Update `_CODEX_ADVISOR_INSTRUCTION` while preserving its single-line, non-slash-command contract. Require the report-only reviewer to receive the exact `write-lossless-ai-messages` skill content and keep its natural-language report lossless. Update `advisor-fallback/SKILL.md` so both the Claude subagent and `codex exec` prompt prepend the exact skill content; do not grant new tools.

In batch finalization, deliver `_CODEX_ADVISOR_INSTRUCTION` for Codex when advisor is enabled, matching single-worker behavior.

**Step 6: Run focused routing tests (GREEN)**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_routing.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_main_validate.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_adapter.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_advisor_fallback_directive.py -q
```

Expected: focused routing suites pass.

**Step 7: Run the full orchestrator MCP regression suite**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_orchestrator_mcp.py -q
```

Expected: full orchestrator MCP suite passes.

**Step 8: Stage Task 3**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/tmux_manager.py worker/skills/advisor-fallback/SKILL.md commander/tests/test_main_validate.py commander/tests/test_orchestrator_mcp.py commander/tests/test_worker_adapter.py commander/tests/test_advisor_fallback_directive.py commander/tests/test_communication_routing.py
```

Expected: Task 3 files staged; no commit or push.

---

## Task 4: Exact lossless skill injection for all graders

**Depends on:** Task 3

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/grader.py`
- Modify: `commander/src/ironclaude/shadow_grader.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/tests/test_grader.py`
- Modify: `commander/tests/test_shadow_grader.py`

**Step 1: Add provider-parity and isolation tests (RED)**

Extend existing grader tests to prove:

```python
assert canonical_skill_text in claude_system_prompt_file
assert canonical_skill_text in codex_stdin
assert canonical_skill_text in local_generate_payload["prompt"]
assert canonical_skill_text in shadow_chat_payload["messages"][0]["content"]
```

Patch the canonical skill path unreadable for each provider and assert a distinct infrastructure failure before any provider call. Retain existing assertions for `--strict-mcp-config`, disallowed `Skill`, `Workflow`, file/exec/agent/worktree/messaging tools, unchanged batch/single schema, unchanged protected values, and absence of `IC_LOSSLESS_AI_MESSAGES_ACTIVE` from model output.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_grader.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_shadow_grader.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_orchestrator_mcp.py -q
```

Expected RED: exact skill-content assertions fail.

**Step 2: Inject the provider-declared profile before untrusted grader input (GREEN)**

At the start of `_call_grader`, resolve the configured provider without constructing a prompt. Select `claude_grader` or `codex_grader` from that provider, then apply the selected construction path before combining any untrusted input:

```python
try:
    construction_path = f"{grader_client}_grader"
    profiled_system_prompt = apply_communication_profile(
        construction_path, system_prompt
    )
except CommunicationProfileError as exc:
    return self._grader_failure(batch, str(exc))
```

Use `profiled_system_prompt` for the Claude temp system file and Codex combined stdin. Tests must prove each configured provider selects its own declared construction ID and fails before untrusted input or a provider subprocess when loading fails.

Add source-inventory declarations beside the Claude and Codex branches. Add corresponding declarations beside the local and shadow grader construction seams in Step 3. Declarations do not enter grader prompts.

**Step 3: Inject profile into local and shadow graders (GREEN)**

In `LocalGrader.grade`, apply `local_grader` before building the Ollama payload; return `_build_infrastructure_error` on loader failure. In `ShadowGrader.grade_with_tools`, apply `shadow_grader` before building the system message; return `_build_error` on loader failure. Keep `test_mode`, schemas, tool loops, and provider error behavior unchanged.

**Step 4: Run provider tests (GREEN)**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_grader.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_shadow_grader.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_orchestrator_mcp.py -q
```

Expected: Claude, Codex, local, and shadow grader tests pass with exact skill injection and existing isolation assertions intact.

**Step 5: Stage Task 4**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/grader.py commander/src/ironclaude/shadow_grader.py commander/tests/test_orchestrator_mcp.py commander/tests/test_grader.py commander/tests/test_shadow_grader.py
```

Expected: Task 4 files staged; no commit or push.

---

## Task 5: Writing-plans artifact profile routing

**Depends on:** Task 1

**Files:**
- Modify: `worker/skills/writing-plans/SKILL.md`
- Modify: `commander/tests/test_writing_plans_skill.py`

**Step 1: Add writing-plans routing tests (RED)**

Extend `test_writing_plans_skill.py` to require these ordered rules:

```python
assert "elements-of-style" in text
assert "write-lossless-ai-messages" in text
assert text.index("elements-of-style") < text.index("Create plan document")
assert text.index("write-lossless-ai-messages") < text.index("Create machine-readable plan JSON")
for protected in (
    "schema", "task IDs", "depends_on", "allowed_files", "ordered steps",
    "commands", "expected results", "paths", "authority", "actionable state",
):
    assert protected in text
assert "same" in parity_section and "commands" in parity_section
```

Also assert that JSON efficiency applies to natural-language strings only and does not authorize dropping fields or changing exact technical values.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_writing_plans_skill.py -q
```

Expected RED: profile-routing assertions fail.

**Step 2: Add destination-specific artifact instructions (GREEN)**

Before the human plan-writing step, require loading and applying `ironclaude:elements-of-style`. Before the machine JSON step, require loading and applying `ironclaude:write-lossless-ai-messages` to natural-language fields.

Use this exact invariant:

```text
Make plan JSON as efficient as possible while remaining lossless. Preserve schema, task IDs, depends_on, allowed_files, ordered steps, commands, expected results, paths, authority boundaries, and complete actionable state exactly. Compression must not create human/machine plan drift.
```

Keep the existing schema, requirements provenance, parity audit, transition preflight, and single plan-review location unchanged.

**Step 3: Run GREEN tests**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_writing_plans_skill.py -q
```

Expected: writing-plans tests pass.

**Step 4: Stage Task 5**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/skills/writing-plans/SKILL.md commander/tests/test_writing_plans_skill.py
```

Expected: Task 5 files staged; no commit or push.

---

## Task 6: Full verification and plugin cachebuster

**Depends on:** Tasks 2, 3, 4, and 5

**Files:**
- Modify: `worker/.codex-plugin/plugin.json`
- Create: `commander/tests/test_communication_construction_inventory.py`

**Step 1: Add and run the closed source-inventory test**

Create `test_communication_construction_inventory.py`. Require one `IRONCLAUDE_LLM_PATH: <construction-id>` declaration at each approved source construction seam and compare the discovered IDs exactly with `CONSTRUCTION_PROFILES`. Cover direct professional activation, Brain start/restart, managed worker dispatch, advisor construction, Claude/Codex/local/shadow graders, session summarizer, and hook validator. Add a negative fixture containing an unclassified low-level construction seam and require validation failure.

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_communication_construction_inventory.py -q
```

Expected: source declarations match the closed inventory and the unclassified negative control fails validation.

**Step 2: Run decisive coverage searches**

Run:

```bash
rg -n "write-lossless-ai-messages|elements-of-style|apply_communication_profile|skill_invocation" /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/CLAUDE.md /Users/roberthyatt/Code/ironclaude/.claude/rules/behavioral.md
```

Expected: named hits cover direct activation, Brain, single/batch/resumed workers, advisor fallback, Claude/Codex grader, local grader, shadow grader, and both communication skills. Any missing approved construction path blocks release.

**Step 3: Refresh only the Codex cachebuster**

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: output reports `1.1.6+codex.<old>` to `1.1.6+codex.<new>`; base release remains `1.1.6`.

**Step 4: Run the final full repository suite**

Run:

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

Expected: hook, workspace-manager, and Commander suites exit 0. Do not record a predicted test count.

**Step 5: Validate release metadata**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_version_consistency.py -q
```

Expected: version consistency tests pass.

**Step 6: Validate plugin structure**

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: plugin validation succeeds and discovers the new skill.

**Step 7: Capture intended Codex runtime fingerprint inputs**

Run:

```bash
shasum -a 256 /Users/roberthyatt/Code/ironclaude/worker/.codex-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js
```

Expected: preserve all three exact hashes in task evidence for the post-relaunch `expected_runtime` call.

**Step 8: Stage the cachebuster and inventory test**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/.codex-plugin/plugin.json commander/tests/test_communication_construction_inventory.py
```

Expected: manifest staged.

**Step 9: Inspect exact staged scope**

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only
```

Expected: only the two approved design artifacts, two plan artifacts, and Task 1-6 implementation files are listed; no unrelated file is staged.

---

## Task 7: Final IronClaude reinstall and runtime activation

**Depends on:** Task 6

**Files:**
- Verify only: `worker/.claude-plugin/plugin.json`
- Verify only: `worker/.codex-plugin/plugin.json`

**No source tests required:** Tasks 1-6 already ran focused and full suites. The main orchestrator owns every restart, identity check, runtime fingerprint, and workflow transition in this task; do not delegate it.

**Step 1: Restart Commander onto the verified source**

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/ironclaude restart
```

Expected: the identity-checked CLI sends SIGHUP to the live `ironclaude.main` daemon; do not start a second daemon and do not invoke Claude login.

**Step 2: Verify fresh Commander startup**

Run:

```bash
tail -n 80 /tmp/ic/daemon.log
```

Expected: latest restart sequence ends with a fresh `Daemon started` record and no singleton, authentication, or startup failure.

**Step 3: Uninstall the cached Claude plugin while preserving plugin data**

Run:

```bash
claude plugin uninstall ironclaude@ironclaude --scope user --keep-data --yes
```

Expected: installed Claude plugin is removed while persistent plugin data is preserved.

**Step 4: Install the Claude plugin**

Run:

```bash
claude plugin install ironclaude@ironclaude --scope user
```

Expected: Claude installs IronClaude `1.1.6` from marketplace `ironclaude`, including `write-lossless-ai-messages`.

**Step 5: Verify the Claude plugin inventory**

Run:

```bash
claude plugin details ironclaude@ironclaude
```

Expected: component inventory lists both `elements-of-style` and `write-lossless-ai-messages`.

**Step 6: Preserve same-task identity and reinstall Codex as the final deployment mutation**

Immediately before installation, call `get_resume_state` and preserve native session ID `019fc5e5-fc72-7493-b785-bee8cda62b1b`, active plan lineage, and Task 7 state. Then run the final shell command in the effort:

```bash
codex plugin add ironclaude@ironclaude --json
```

Installation evidence: command exits 0 and its JSON reports enabled installation from marketplace `ironclaude` at the newly generated `1.1.6+codex.<cachebuster>` version. Preserve the JSON and installed plugin root in task evidence. Run no later shell command.

Without delegating, fully quit and relaunch Codex, reopen this same native task, and continue Task 7. Require `get_resume_state.session_id` to equal `019fc5e5-fc72-7493-b785-bee8cda62b1b`. Call `run_diagnostics` with exact `expected_runtime` fields `plugin_version`, `plugin_root`, `manifest_sha256`, `state_manager_bundle_sha256`, `workspace_manager_bundle_sha256`, and `client: "codex"`, using the installed root/version and all three Task 6 hashes. Require complete runtime paths and hashes, source/cache parity, and `Runtime activation match ... PASS`. Submit Task 7 through the normal workflow and require its different-stage transition to report `changed:true`. Any identity, fingerprint, or transition mismatch fails closed.
