# Top-Tier Review on Request Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop reviews from escalating to a client's top tier (Claude Fable, Codex Astra) by default. The plan review, fix advisor and end review use the top tier only on explicit operator request, or after a same-tier reviewer at that step fails.

**Requirements:** `docs/plans/2026-10-16-top-tier-review-on-request-design.md` (§Requirements R1–R5)

**Architecture:** One shared "Top-tier rule" paragraph is added after the Step 1.5 ladder lines in `worker/skills/executing-plans/SKILL.md`. Four passages point to it:
- the Step 1.5 blast-radius judgment;
- the Codex `review_tier` selection sentence, so Sol passes `"same"` for plan review, the fix advisor and the end review;
- the HAS-ISSUES fix-advisor tier line;
- the Step 7.5 end-review dispatch.

The ladder lines, the lower steps, the Fable-unavailable fallback and the broker code are unchanged.

The operator confirmed on 2026-10-16 that a same-tier fix advisor is intentional: "most issues are found, not because something is a stronger model, but because that something doesn't have the same context ... Fable/Astra should be brought in only if Opus/Sol really really needs it". The fix advisor still runs as a fresh subagent with its own context.

**Tech Stack:** Markdown skill text; pytest.

Loop: light — skill instruction text plus one pytest file; no DB, guard/hook code, concurrency, git operations, or MCP schema; ~2 files / ~40 lines.

## Execution invariants

- Use literal absolute paths, and `git -C /Users/roberthyatt/Code/ironclaude`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push. `docs/` is gitignored, so stage plan files with `git add -f`.
- Execution subagents must not use the Skill tool.
- Keep these existing exact strings intact, because `test_executing_plans_skill.py` asserts them:
  - `Luna→Terra→Sol→Astra, each resolved by the broker to the newest installed \`gpt-<version>-<tier>\` model`
  - `Luna→Terra→Sol→Astra, each the newest installed \`gpt-<version>-<tier>\` model, Astra ceiling→same-tier Astra`
  - `fable_unavailable.json`

**Verified at planning time:**

| What | Where / fact |
|---|---|
| Step 1.5 tier judgment | `SKILL.md:195-207`. Line 201 ends `   widely-shared code, schema/migration, concurrency, security surface, and diff size/spread.` and line 202 begins `   A light-loop plan`. |
| Ladder lines | `SKILL.md:208-211`. Line 211 is the Codex ladder line ending `uses a fresh same-tier \`gpt-6-astra\` reviewer.` and line 212 is blank. |
| Codex review_tier sentence | `SKILL.md:272-274`: `   Use \`review_tier: "same"\` only for the selected same-tier blind plan-review` / `   decision. Use \`review_tier: "one-up"\` for selected tier-up plan review, fix-advisor,` / `   and end-of-work review. Omission is compatibility-only, never preferred new usage.` This is the only Codex tier instruction for the fix advisor and end review, because the Step 7.5 dispatch is Agent-only. `test_executing_plans_skill.py:143-144` asserts that both `review_tier: "same"` and `review_tier: "one-up"` stay present. |
| Fix-advisor tier line | `SKILL.md:393`: `   - **Tier:** one above your current model on the trusted client's ladder (Claude:` |
| End-review dispatch | `SKILL.md:849`: ``(Agent tool, `subagent_type="general-purpose"`, `model=<one tier above your current model>` per the`` and `SKILL.md:850`: `Step 1.5 tier-up resolution incl. the Fable-availability fallback) over the staged diff, following the` |
| Section anchors | Each occurs once: `Decide the reviewer tier` (:195), `Resolve the reviewer model` (:208), `MANDATORY tier-up fix advisor` (:390), `It is NOT blind` (:399), `Step 7.5: Tier-up adversarial end review` (:834), `Step 8: Final summary` (:859). `Top-tier rule` occurs 0 times today. |
| Codex same tier | `worker/mcp-servers/state-manager/src/tools/advisor-review.ts:25` `REVIEW_TIERS = ['same', 'one-up']`; no broker change. |

---

## Task 1: Add the shared Top-tier rule and reference it at all review sites

TDD.

**Files:**
- Modify: `commander/tests/test_executing_plans_skill.py` (append at end of file)
- Modify: `worker/skills/executing-plans/SKILL.md` (`:201-202`, `:211-212`, `:272-274`, `:393`, `:849-850`)
- Stage only: `docs/plans/2026-10-16-top-tier-review-on-request-design.md`, `docs/plans/2026-10-16-top-tier-review-on-request.md`, `docs/plans/2026-10-16-top-tier-review-on-request.plan.json`

### Step 1: Test (RED)

Append this to the end of `commander/tests/test_executing_plans_skill.py`:
```python


def test_top_tier_reviewer_only_on_request_or_same_tier_failure():
    text = " ".join(_read().split())
    assert (
        "**Top-tier rule (both clients):** a client's top tier — Claude Fable, Codex Astra — "
        "is used only when the operator explicitly requested the top tier for this effort, "
        "or when a same-tier reviewer at that same step failed to produce a usable result"
    ) in text
    assert '`model=opus` on Claude, `review_tier: "same"` on Codex' in text
    judgment = text[text.index("Decide the reviewer tier"):text.index("Resolve the reviewer model")]
    assert "the Top-tier rule below decides" in judgment
    advisor = text[text.index("MANDATORY tier-up fix advisor"):text.index("It is NOT blind")]
    assert "subject to the Top-tier rule (Step 1.5)" in advisor
    end_review = text[
        text.index("Step 7.5: Tier-up adversarial end review"):text.index("Step 8: Final summary")
    ]
    assert "and the Top-tier rule" in end_review
    assert "subject to the Top-tier rule>" in end_review
    assert (
        'and for plan review, fix-advisor, and end-of-work review wherever the Top-tier rule '
        'above replaces a Sol→Astra step. Otherwise use `review_tier: "one-up"`'
    ) in text
    assert (
        'Use `review_tier: "same"` only for the selected same-tier blind plan-review decision'
    ) not in text
```
RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_executing_plans_skill.py -q -k top_tier_reviewer
```
Expected: `1 failed` with an `AssertionError` on the first assert, because `Top-tier rule` does not exist yet. Record the measured output.

### Step 2: Implementation (GREEN) in `worker/skills/executing-plans/SKILL.md`

Each edit keeps the 3-space list indentation of the surrounding text. Line numbers refer to the file before any edit; find each spot by the quoted text, because earlier insertions shift later lines.

1. Directly after line 201 (`   widely-shared code, schema/migration, concurrency, security surface, and diff size/spread.`), insert:
```
   From Claude Opus or Codex Sol, one tier up is the client's top tier, so the Top-tier rule below
   decides: do not surface a tier-up question unless the operator requested the top tier.
```
2. Directly after line 211 (the Codex ladder line ending `uses a fresh same-tier \`gpt-6-astra\` reviewer.`), insert a blank line and then:
```
   **Top-tier rule (both clients):** a client's top tier — Claude Fable, Codex Astra — is used
   only when the operator explicitly requested the top tier for this effort, or when a same-tier
   reviewer at that same step failed to produce a usable result (an error, empty output, or an
   off-task report). Otherwise, where one tier up would resolve to the top tier (from Claude Opus
   or Codex Sol), dispatch a fresh same-tier reviewer instead: `model=opus` on Claude,
   `review_tier: "same"` on Codex. This applies here, to the HAS-ISSUES fix advisor, and to the
   Step 7.5 end review. A session already on the top tier keeps its fresh same-tier review.
```
3. Replace line 393:
```
   - **Tier:** one above your current model on the trusted client's ladder (Claude:
```
with:
```
   - **Tier:** one above your current model on the trusted client's ladder, subject to the
     Top-tier rule (Step 1.5) (Claude:
```
4. Replace lines 849-850:
```
(Agent tool, `subagent_type="general-purpose"`, `model=<one tier above your current model>` per the
Step 1.5 tier-up resolution incl. the Fable-availability fallback) over the staged diff, following the
```
with:
```
(Agent tool, `subagent_type="general-purpose"`, `model=<one tier above your current model, subject to the Top-tier rule>` per the
Step 1.5 tier-up resolution incl. the Fable-availability fallback and the Top-tier rule) over the staged diff, following the
```
5. Replace lines 272-274:
```
   Use `review_tier: "same"` only for the selected same-tier blind plan-review
   decision. Use `review_tier: "one-up"` for selected tier-up plan review, fix-advisor,
   and end-of-work review. Omission is compatibility-only, never preferred new usage.
```
with:
```
   Use `review_tier: "same"` for the selected same-tier blind plan-review decision, and
   for plan review, fix-advisor, and end-of-work review wherever the Top-tier rule above
   replaces a Sol→Astra step. Otherwise use `review_tier: "one-up"` for selected tier-up
   plan review, fix-advisor, and end-of-work review. Omission is compatibility-only, never
   preferred new usage.
```

### Step 3: GREEN plus regression

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_executing_plans_skill.py tests/test_spawn_tier_bias.py tests/test_writing_plans_skill.py tests/test_workflow_transition_preflight.py -q > /private/tmp/claude-502/tt-focused.out 2>&1; echo "focused=$?"; tail -n 3 /private/tmp/claude-502/tt-focused.out; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/tt-suite.out 2>&1; echo "suite=$?"; tail -n 3 /private/tmp/claude-502/tt-suite.out; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-handoffs-restaging.sh > /private/tmp/claude-502/tt-hook.out 2>&1; echo "hook=$?"
```
Expected: `focused=0`, `suite=0` (0 failed), `hook=0`. The hook test reads `executing-plans/SKILL.md`, so it guards the re-stage block against collateral edits.

Any other failure must be shown pre-existing by running it in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-top-tier-review-on-request-design.md docs/plans/2026-10-16-top-tier-review-on-request.md docs/plans/2026-10-16-top-tier-review-on-request.plan.json
```
Expected: no output.
