# Opus-Tier Unpin — Natural Resolution — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Remove every `claude-opus-4-8` pin so the internal Opus tier resolves naturally via the bare `opus` alias.

**Requirements:** docs/plans/2026-09-24-opus-unpin-requirements.md

**Architecture:** A uniform literal substitution — `claude-opus-4-8` → `opus` — across all source, config, and test files (only the Opus tier is pinned; sonnet/haiku/fable already use bare aliases). The `provider_config.EXPECTED_MODELS` validator, the config files, and the test fixtures that must match it flip in lockstep. `_MODELS_NEEDING_1M_BETA=("opus",)` is deliberately untouched (Opus still needs the `[1m]` beta; the swap turns `claude-opus-4-8[1m]` into `opus[1m]`). Docs are updated separately.

**Tech Stack:** Python (commander), JSON config, pytest.

**Execution invariants (this plan's commands honor these):**
- Bash cwd is `commander/`; all commands use **absolute paths**.
- Shell state does not persist between steps; each command is self-contained.
- macOS `sed` requires `sed -i ''`.
- An empty search result is made provable via an explicit exit-code echo (no `2>/dev/null`).
- The substitution is scoped to `commander/src`, `commander/tests`, `commander/config`, and repo-root `config/` — it never touches `README.md` or `CHANGELOG.md` (Task 2 handles those; the historical CHANGELOG entry stays).
- `allowed_files` lists every file the substitution touches (enumerated, not globbed).

---

## Task 1: Unpin the Opus tier (mechanical substitution + verify)

**Files:** (Modify — every file currently containing the literal `claude-opus-4-8` in source/config/tests)
- `commander/src/ironclaude/provider_config.py`
- `commander/src/ironclaude/config.py`
- `commander/src/ironclaude/main.py`
- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/src/ironclaude/fable_availability.py`
- `commander/src/ironclaude/notifications.py`
- `commander/src/ironclaude/brain_client.py`
- `config/ironclaude.json`
- `config/ironclaude.json.example`
- `commander/config/ironclaude.json`
- `commander/tests/test_worker_adapter.py`
- `commander/tests/test_brain_client.py`
- `commander/tests/test_grader_router.py`
- `commander/tests/test_grader_routing.py`
- `commander/tests/conftest.py`
- `commander/tests/test_config.py`
- `commander/tests/test_main_validate.py`
- `commander/tests/test_orchestrator_mcp.py`
- `commander/tests/test_fable_availability.py`

**No RED-first TDD:** this is a mechanical rename, not new behavior. Existing tests assert the old literal; they are updated in lockstep with source, then the full suite verifies GREEN. (No tests are added or removed.)

**Step 1: Enumerate the current occurrences (baseline — do not hardcode the list).**
```bash
rg -n claude-opus-4-8 /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/commander/tests /Users/roberthyatt/Code/ironclaude/commander/config /Users/roberthyatt/Code/ironclaude/config
```
Expected: matches across the 19 files listed above, including repo-root `config/ironclaude.json` (`{"brain_model": "claude-opus-4-8"}`) (the `codex` entries `gpt-5.6-sol` etc. do NOT match; only `claude-opus-4-8` does). Confirms the set before editing.

**Step 2: Substitute `claude-opus-4-8` → `opus` in exactly those files.**
```bash
rg -l claude-opus-4-8 /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/commander/tests /Users/roberthyatt/Code/ironclaude/commander/config /Users/roberthyatt/Code/ironclaude/config | xargs sed -i '' 's/claude-opus-4-8/opus/g'
```
Expected: no output (sed succeeds silently). Every literal — including inside `claude-opus-4-8[1m]`, which becomes `opus[1m]` — is replaced.

**Step 3: Prove no stray literal remains in scope (falsifiable — catches a missed file).**
```bash
rg -n claude-opus-4-8 /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/commander/tests /Users/roberthyatt/Code/ironclaude/commander/config /Users/roberthyatt/Code/ironclaude/config ; echo "rg-exit=$?"
```
Expected: no match lines, then `rg-exit=1` (ripgrep exits 1 when nothing matches). A non-empty match or `rg-exit=0` means a file was missed.

**Step 4: Prove the `[1m]` beta guard is untouched (falsifiable — the swap must NOT alter it).**

Uses `rg -F` (fixed-string): the source line contains literal parens that default regex would treat as a group and fail to match.
```bash
rg -F -n '_MODELS_NEEDING_1M_BETA = ("opus",)' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/brain_client.py ; echo "rg-exit=$?"
```
Expected: one match line (`brain_client.py:120`), then `rg-exit=0`. Confirms `_MODELS_NEEDING_1M_BETA` still reads `("opus",)` so Opus keeps the `[1m]` beta.

**Step 5: Run the full commander test suite.**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: `3309 passed` (0 failed). The rename adds/removes no tests; the grader-command test now asserts `--model opus[1m]`, the config/validator tests now expect `opus`, the brain-client fallback test now expects `opus`.

**Step 6: Stage the changes.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/provider_config.py commander/src/ironclaude/config.py commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/fable_availability.py commander/src/ironclaude/notifications.py commander/src/ironclaude/brain_client.py config/ironclaude.json config/ironclaude.json.example commander/config/ironclaude.json commander/tests/test_worker_adapter.py commander/tests/test_brain_client.py commander/tests/test_grader_router.py commander/tests/test_grader_routing.py commander/tests/conftest.py commander/tests/test_config.py commander/tests/test_main_validate.py commander/tests/test_orchestrator_mcp.py commander/tests/test_fable_availability.py
```
Expected: staged (professional mode blocks commit).

---

## Task 2: Update the rationale docs

**Files:**
- Modify: `README.md:287-292` (the "Recommended model: Opus 4.8" blockquote)
- Modify: `CHANGELOG.md` (add a new `## [Unreleased]` entry; leave the historical `457-458` entry intact)

**Depends on:** Task 1

**No tests required: documentation only.**

**Step 1: Rewrite the README rationale blockquote.**

Replace the current blockquote at `README.md:287-292`:
```
> **Recommended model: Opus 4.8, not Opus 5.** IronClaude currently pins its Opus tier to
> `claude-opus-4-8`, and that is the recommendation. In practice Opus 5 tends to be too "ADHD" —
> too eager to jump ahead and improvise — to follow IronClaude's structured brainstorm → plan →
> execute workflow reliably, so every internal Opus-tier string resolves to `claude-opus-4-8` to
> keep the `opus` alias from silently drifting to Opus 5. If you want a different build, pin it
> explicitly with `ANTHROPIC_DEFAULT_OPUS_MODEL` (below).
```
with:
```
> **Opus tier resolves naturally.** IronClaude no longer pins its Opus tier to a specific version:
> every internal Opus-tier string uses the bare `opus` alias, which the Claude CLI resolves to
> current-latest Opus (as the `sonnet`, `haiku`, and `fable` tiers already do). An earlier pin to
> `claude-opus-4-8` existed because Opus 5 was too eager to improvise to follow the structured
> brainstorm → plan → execute workflow reliably; Opus 5.5 resolves that, so the pin is retired. To
> force a specific build, set `ANTHROPIC_DEFAULT_OPUS_MODEL` (below).
```

**Step 2: Add a new CHANGELOG entry** immediately below the versioning blockquote and above `## 1.1.12` in `CHANGELOG.md`:
```
## [Unreleased]

- **Opus tier is no longer pinned to `claude-opus-4-8` — it resolves naturally via the bare `opus` alias.** The pin existed because the `opus` alias used to auto-resolve to Opus 5, which was too eager to improvise to follow the brainstorm → plan → execute workflow; natural resolution now lands on Opus 5.5, which is reliable, so the pin's premise is obsolete. Every internal Opus-tier string (`provider_config.EXPECTED_MODELS`, `config.py` defaults, `main.py` / `orchestrator_mcp.py` defaults and Fable-unavailable redirect literals, `fable_availability.py`, `notifications.py`, `brain_client.py` fallbacks) and both config files now use `opus`, exactly as the sonnet/haiku/fable tiers already do — no explicit version id is named anywhere. The `[1m]` 1M-context beta path is unchanged (`_MODELS_NEEDING_1M_BETA=("opus",)`), so the tier still launches as `opus[1m]`. (`provider_config.py`, `config.py`, `main.py`, `orchestrator_mcp.py`, `fable_availability.py`, `notifications.py`, `brain_client.py`, `config/ironclaude.json.example`, `commander/config/ironclaude.json`, `README.md`; covered by the existing config/grader/brain/advisor suites.) Deploy: Commander restart; verify `claude --model 'opus[1m]'` is accepted on the daemon box before restarting the Brain.
```

**Step 3: Prove the historical CHANGELOG entry is intact (falsifiable — Task 1 must not have touched CHANGELOG, and this task must not blanket-replace it).**
```bash
rg -n claude-opus-4-8 /Users/roberthyatt/Code/ironclaude/CHANGELOG.md ; echo "rg-exit=$?"
```
Expected: the historical entry lines still match (the `## 1.1.10`-era "Opus tier pinned to `claude-opus-4-8`" entry), then `rg-exit=0`. Zero matches would mean history was wrongly rewritten.

**Step 4: Prove the README no longer pins 4.8 (falsifiable).**
```bash
rg -n claude-opus-4-8 /Users/roberthyatt/Code/ironclaude/README.md ; echo "rg-exit=$?"
```
Expected: no match lines, then `rg-exit=1`.

**Step 5: Stage the docs.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add README.md CHANGELOG.md
```
Expected: staged.
