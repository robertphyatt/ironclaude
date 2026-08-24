# Codex hook trailing-entity normalization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop Codex Desktop's trailing `&#x20;` from breaking the `state-activator.sh`
deactivate and commit/push hook-command gates, closing the Codex deadlock.

**Requirements:** docs/plans/2026-08-21-codex-hook-entity-normalization-requirements.md

**Design:** docs/plans/2026-08-21-codex-hook-entity-normalization-design.md

**Architecture:** Normalize `TRIMMED_PROMPT` once at `worker/hooks/state-activator.sh:46` —
after the whitespace trim, strip an end-anchored run of enumerated HTML space-entities. Every
codex command gate reads that one variable, so the single change fixes deactivate and
commit/push without touching any gate regex or the intent machinery, and cannot widen the
guard (each gate stays anchored/exact).

**Tech Stack:** bash, `sed -E` (BSD/macOS-safe: one substitution, no label/branch), the
existing bash hook test harness (`sqlite3`, `jq`).

**Execution invariants (author + reviewer check against these):** shell state does not
persist between steps (literal absolute paths); Bash cwd is `commander/` (use `git -C
<repo-root>` and absolute paths); `docs/` is gitignored (`git add -f`); quote globs; the
test harness's pass/fail IS the measured oracle for the RED→GREEN steps (no predicted
magic values); an empty result must be distinguishable from a failed command. macOS `sed`
is BSD sed — the normalization must be a single `s/…+$//` substitution (no `:a … ta`
label/branch, which BSD sed rejects after `;`).

---

## Task 1: Normalize `TRIMMED_PROMPT` against trailing HTML space-entities (TDD)

**Files:**
- Modify: `worker/hooks/state-activator.sh:46`
- Test: `worker/hooks/tests/test-professional-mode-deactivation.sh`

This task involves executable code → TDD (RED → GREEN → stage).

**Step 1 (RED — add the four cases + the intent-path runner).** In
`worker/hooks/tests/test-professional-mode-deactivation.sh`, add a `run_prompt_intent`
helper (a `run_prompt` variant that also sets `hook_event_name:"UserPromptSubmit"` so the
HUMAN_OPERATION block at state-activator.sh:53 is reachable), then append four assertions
after the existing negative block (after line 108):

```bash
run_prompt_intent() {
  local prompt="$1" session_id="${2:-$SESSION}"
  jq -cn --arg prompt "$prompt" --arg session_id "$session_id" \
    '{prompt: $prompt, session_id: $session_id, hook_event_name: "UserPromptSubmit"}' \
    | HOME="$TMP_HOME" bash "$HOOK"
}

# (a) POSITIVE: codex deactivate link with a trailing &#x20; still deactivates
assert_deactivates "Codex deactivate link with trailing entity" \
  '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.6/skills/deactivate-professional-mode/SKILL.md)&#x20;'

# (b) NEGATIVE: trailing entity must not rescue a prose-suffixed link
assert_ignored "Codex deactivate link with prose suffix then entity" \
  '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.6/skills/deactivate-professional-mode/SKILL.md) now&#x20;'

# (c) POSITIVE: codex commit-and-push link with a trailing &#x20; reaches HUMAN_OPERATION
reset_current
op_pos_output=$(run_prompt_intent '[$ironclaude:commit-and-push](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.6/skills/commit-and-push/SKILL.md)&#x20;')
assert_contains "commit-and-push link with trailing entity reaches HUMAN_OPERATION" \
  "Human intent issuance runtime is unavailable" "$op_pos_output"

# (d) NEGATIVE: prose-suffixed commit-and-push link does NOT reach HUMAN_OPERATION
reset_current
op_neg_output=$(run_prompt_intent '[$ironclaude:commit-and-push](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.6/skills/commit-and-push/SKILL.md) now&#x20;')
assert_not_contains "prose-suffixed commit-and-push link does not reach HUMAN_OPERATION" \
  "Human intent issuance runtime is unavailable" "$op_neg_output"
```

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```
Expected: the run ends non-zero (RED). Cases (a) and (c) FAIL on the current hook — (a)'s
state stays `on|brainstorming` (the `&#x20;` breaks the `:145` link regex) and (c) is
missing the `Human intent issuance runtime is unavailable` string (the `&#x20;` breaks the
`:65` `CODEX_COMMAND_LINK_RE`, so `HUMAN_OPERATION` never sets). Cases (b) and (d) PASS
already (they must stay ignored both before and after — non-widening controls). This proves
each new check can fail.

**Step 2 (GREEN — implement the normalization).** In `worker/hooks/state-activator.sh`,
replace line 46:

```bash
TRIMMED_PROMPT=$(printf '%s' "$USER_PROMPT" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
```
with:
```bash
TRIMMED_PROMPT=$(printf '%s' "$USER_PROMPT" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/(&#x20;|&#32;|&nbsp;|&#160;|&#xa0;|&#xA0;|[[:space:]])+$//')
```

One `sed -E` invocation, three `s` commands: strip leading whitespace, strip trailing
whitespace, then strip an end-anchored run of the enumerated HTML space-entities (and any
interleaved whitespace). No `:a … ta` label/branch — BSD/macOS-sed safe. Only a TRAILING run
is removed, so a `&#x20;` mid-string or a prose suffix leaves the anchored gates failing.

**Step 3 (GREEN — verify).** Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```
Expected: `Results: N pass, 0 fail` and exit 0. All four new cases pass and every pre-existing
case (lines 88–118) still passes. If it does not print `0 fail`, the normalization or a test
line is wrong — fix before staging (do not stage RED).

**Step 4: Stage changes.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/hooks/state-activator.sh worker/hooks/tests/test-professional-mode-deactivation.sh
```
Expected: both files staged (professional mode blocks commit — the human commits). Do NOT stage
anything else (plan docs stay unstaged until after Step 5's check).

**Step 5 (no-regression evidence — bounded to the two bash files).** With only those two files
staged, confirm the staged set is exactly them — the no-regression proof for the TypeScript
(state-manager/workspace-manager) and Python (commander) suites, none of which import or exercise
this bash hook. This is the UNFILTERED staged diff (no pathspec), so a stray third staged file
would appear and fail the check (falsifiable), matching requirements R4 verbatim:
```bash
git -C /Users/roberthyatt/Code/ironclaude diff --staged --name-only
```
Expected: exactly the two lines `worker/hooks/state-activator.sh` and
`worker/hooks/tests/test-professional-mode-deactivation.sh` and nothing else staged. No TS/py
source is staged, so those suites cannot regress from this change. (The design's "run the other
suites for no regression" note is satisfied by proving the staged set is exactly these two files —
see requirements R4.)
