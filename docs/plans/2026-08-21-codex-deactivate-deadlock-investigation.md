# Codex deactivate-deadlock investigation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Prove why `$ironclaude:deactivate-professional-mode` in Codex session
`019fc3b0-2757-7b22-b792-0011f9b29929` did not set `professional_mode='off'`, and write a findings
note naming the confirmed root cause (H-A hook-not-run / H-B 0-rows-UPDATE / H-C split-DB /
H-D prompt-bytes-mismatch) with the fix direction.

**Requirements:** docs/plans/2026-08-21-codex-deactivate-deadlock-investigation-requirements.md

**Architecture:** A strictly read-only investigation. The execute stage (which unblocks
Bash/sqlite) reads the Codex hook wiring, the state-cache A1 signal, the audit_log + codex-captured
stdout, the operator's actual prompt bytes, and the `sessions`/DB-path resolution — then writes a
findings note. No product code, config, DB row, or professional-mode change.

**Tech Stack:** bash (cat, grep, find, ls, sed), sqlite3 (SELECT, `-readonly`), Markdown.

**Execution invariants (author + reviewer check against these):** shell state does not persist
between steps (literal absolute paths); Bash cwd is `commander/` (use absolute paths and
`git -C <repo-root>`); quote globs; foreground `sleep` blocked; `docs/` gitignored (`git add -f`);
an empty result must be distinguishable from a failed command (no `2>/dev/null` on evidence; list
values, not counts; show the query behind any ABSENCE claim); this is a DISCOVERY task, so a
step's `expected` states the FORM of the answer and what a value MEANS — it never hard-codes the
value the step exists to find; use single-term greps (no `|` alternation); `logs_2.sqlite` is large
+ WAL-live — use `sqlite3 -readonly` with bounded `SELECT`s, never a full dump; the rollout JSONL
is 44k lines — Read line ranges / `sed -n`, never whole-file; STRICTLY READ-ONLY — no DB write, no
`set_professional_mode`, no config/hook edit, no change to the codex session's PM state.

---

## Task 1: Root-cause the Codex deactivate failure and write the findings note

**Files:**
- Create: `docs/plans/2026-08-21-codex-deactivate-deadlock-findings.md` (the sole deliverable)

No tests required: read-only diagnosis whose deliverable is a findings note; every value is
measured live in these steps and pasted, not asserted.

**Step 1 (R1 + A1 — the authoritative H-A probe): Is `state-activator.sh` wired for Codex, and did
it run for this session?** The hook wiring is the authoritative H-A evidence (NOT a `logs_2`
absence). Inspect the codex config + plugin hook manifest for a prompt-submit → `state-activator.sh`
binding:
```bash
grep -n "state-activator" /Users/roberthyatt/.codex/config.toml
```
```bash
ls -la /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude
```
(Also `grep -rn "state-activator"` the codex plugin cache root and read any codex hooks manifest.)
Then the **A1** corroboration — `~/.claude/ironclaude-state-cache-<SESSION_TAG>.json` is written
ONLY by `state-activator.sh` (:214-220), conditional on the `sessions` SELECT (:192) returning a row
for the key, so its existence proves the hook ran with the correct key at least once:
```bash
cat /Users/roberthyatt/.claude/ironclaude-state-cache-019fc3b0-2757-7b22-b792-0011f9b29929.json
```
Expected: whether Codex binds `state-activator.sh` (record the wiring or its provable absence, with
the commands shown), and the state-cache `timestamp` — a timestamp at/after 21:51Z proves the hook
ran around the deactivate turn (overwritten every prompt, so it proves wiring+key, not per-turn
behaviour). No wiring anywhere AND no cache → strong H-A.

**Step 2 (R2/H-D — prompt bytes, NOT the event name): capture the delivered prompt and test the
gates.** The deactivate branch (state-activator.sh:137-151) is gated ONLY by the `:138` slash
regex, the `:144` codex-link regex, and the `:148` exact `$ironclaude:deactivate-professional-mode`
string, plus the empty-prompt `exit 0` (:38-40). The `hook_event_name=="UserPromptSubmit"` gate at
`:53` wraps only the HUMAN_OPERATION block (:53-72) — it does NOT gate deactivation. Extract the
operator's deactivate-invocation bytes from the rollout JSONL (independent of any codex
hook-payload logging) and test them against the three conditions:
```bash
grep -n "skills/deactivate-professional-mode/SKILL.md" /Users/roberthyatt/.codex/sessions/2026/08/02/rollout-2026-08-02T12-15-29-019fc3b0-2757-7b22-b792-0011f9b29929.jsonl
```
(Then `sed -n '<line>p'` the user `input_text` turn near the 21:51 deactivate; test the exact bytes
against `CODEX_DEACTIVATE_LINK_RE` from state-activator.sh:144 and the `:148` exact string.) Also,
if the codex payload log records hook stdin, capture `.prompt` and `.session_id` from
`logs_2.sqlite` for that turn (schema via `.tables`/`.schema` first, bounded `-readonly` SELECT).
Expected: the exact operator bytes and whether they match `:138`/`:144`/`:148`. H-D = `.prompt`
missing/empty OR bytes match none of the three (a trailing/rendering difference from the `:144`
regex). `hook_event_name` must NOT drive the verdict.

**Step 3 (R3a — durable branch evidence): audit_log success row + codex-captured 0-rows warning.**
There is no ironclaude hook-log file (hook-logger emits stdout-only systemMessage JSON;
`~/.claude/ironclaude-errors.log` is the MCP-error sideband). The SUCCESS path writes a durable
row (`db_audit_log "hook:state-activator" "professional_mode_off" …`, state-activator.sh:163/:173):
```bash
sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude.db "SELECT created_at, terminal_session, actor, action, old_value, new_value, context FROM audit_log WHERE action='professional_mode_off' AND terminal_session LIKE '%019fc3b0%';"
```
The 0-rows warning (:166/:176) is stdout-only — captured, if anywhere, by the codex transcript;
search for its literal string (single-term grep):
```bash
grep -c "Deactivation UPDATE affected 0 rows" /Users/roberthyatt/.codex/sessions/2026/08/02/rollout-2026-08-02T12-15-29-019fc3b0-2757-7b22-b792-0011f9b29929.jsonl
```
Expected: the audit row or its provable absence, plus the warning string's presence/absence.
Interpretation: audit row present → the UPDATE succeeded → points at H-C (MCP-read side); audit row
absent + warning present → H-B; audit row absent + no warning + hook proven to run (Step 1) → the
deactivate branch never fired → H-D. (Success stdout is verbose-gated so its absence proves nothing;
`log_warning` is ungated.)

**Step 4 (R3b — H-B vs H-C DB/key): STATE_MANAGER_DB_PATH + the sessions row key.** The hook's
`DB_PATH` is hardcoded `~/.claude/ironclaude.db` (hook-logger.sh:29); the `state-manager` MCP uses
`getDbPath()` (state-manager `src/db.ts:30-35`) = `STATE_MANAGER_DB_PATH` override else the same
file. So H-C reduces to whether the codex MCP has an override:
```bash
grep -n "STATE_MANAGER_DB_PATH" /Users/roberthyatt/.codex/config.toml
```
(Also grep the codex plugin MCP config for the env override.) For H-B, `SAFE_SESSION` derives from
payload `.session_id` (defaults `'none'`, hook-logger.sh:95-99); read the sessions row and compare
its key to the hook's UPDATE target:
```bash
sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude.db "SELECT terminal_session, professional_mode, updated_at FROM sessions WHERE terminal_session LIKE '%019fc3b0%';"
```
Expected: whether `STATE_MANAGER_DB_PATH` is set for the codex MCP (set + different file → H-C), and
whether the codex session's `sessions` row exists under the exact `terminal_session` key the hook's
UPDATE targets (key mismatch → H-B).

**Step 5 (R4): Write the findings note.** Create
`docs/plans/2026-08-21-codex-deactivate-deadlock-findings.md` with the ACTUAL output from Steps 1-4
pasted as evidence, naming the ONE confirmed hypothesis (**H-A** codex never runs the hook / **H-B**
0-rows UPDATE — `.session_id`/`SESSION_TAG` or key mismatch / **H-C** split-DB — `STATE_MANAGER_DB_PATH`
override / **H-D** prompt bytes match none of :138/:144/:148) with the evidence FOR it and AGAINST
the others, plus the fix direction and any question that could not be answered. Expected: the note
exists and names one confirmed root cause with evidence.

**Step 6: Stage the findings note.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-08-21-codex-deactivate-deadlock-findings.md
```
Expected: the findings note is staged (professional mode blocks commit).
