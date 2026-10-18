# Drift Check: Recognize Another Session's Work — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Compare foreign drift-ledger roots by file identity instead of spelling, and make the 1.2.0 deploy steps tell operators to run `/reload-plugins` in open Claude Code sessions.

**Requirements:** `docs/plans/2026-10-18-drift-foreign-session-design.md` (R1, R2). It is both the requirements file and the design file.

**Architecture:** In `_drift_foreign_paths` (`worker/hooks/execution-drift-check.sh:419-452`), rule (b) skips a ledger unless `"$(cat "${f%.txt}.root")" = "$root"` (line 446). Replace that string test with a file-identity test (`-ef`), as rule (a) already does through `_drift_rel_by_identity`. Docs: the 1.2.0 `Deploy:` line (`CHANGELOG.md:86`), the 1.2.0 drift bullet (`CHANGELOG.md:68`) and `README.md:304`.

**Tech Stack:** bash, sqlite3, jq (hook and its test harness).

**Execution invariants:**
- Shell state does not persist between steps; use absolute paths. Repo root `/Users/roberthyatt/Code/ironclaude`; git as `git -C /Users/roberthyatt/Code/ironclaude`. zsh `nomatch`: quote globs.
- Logs under `/private/tmp/claude-502/taf5/` (`mkdir -p` first), one per run; no `2>/dev/null` on evidence commands.
- `docs/` is gitignored: `git add -f` for `docs/plans` files. Never commit, tag or push.
- RED expectations are derived by tracing the current hook; a differing RED is a STOP; any GREEN failure is a STOP.

---

## Task 1: Foreign-ledger roots by file identity (R1)

**Files:** Modify `worker/hooks/execution-drift-check.sh`, `worker/hooks/tests/test-execution-drift-check.sh`.

**Step 1: RED test** — in `worker/hooks/tests/test-execution-drift-check.sh`, replace `EXPECTED_PASSES=53` with `EXPECTED_PASSES=54`, and insert immediately before the line `printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"`:

```bash
# 54. A live session's ledger that records this repository through another spelling (a
#     symlinked path) still excludes: roots compare by file identity, not spelling
#     (design 2026-10-18 drift-foreign-session R1). Case 47 is the negative control.
ln -s "$ROOT_PHYS" "$TMP_ROOT/repo-link"
sqlite3 "$DB" "INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${OTHER}', 3, 1, 'Other linked task', 'in_progress');"
OTHER_LINK_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${OTHER}';")
OTHER_LINK_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${OTHER}.${OTHER_LINK_ROW}.txt"
printf 'l54.txt\n' > "$OTHER_LINK_LEDGER"
printf '%s\n' "$TMP_ROOT/repo-link" > "${OTHER_LINK_LEDGER%.txt}.root"
shell_event PreToolUse w54 "touch l54.txt" >/dev/null
printf 'l\n' > "$REPO/l54.txt"
shell_event PostToolUse w54 "touch l54.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && grep -qxF "l54.txt" "$LEDGER" && ok "54 another session's ledger for this repo under another spelling does not block" || bad "54 another session's ledger for this repo under another spelling does not block (rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/l54.txt"

```

**Step 2: Run RED**

```bash
mkdir -p /private/tmp/claude-502/taf5 && bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/taf5/t1-red.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf5/t1-red.log; tail -n 1 /private/tmp/claude-502/taf5/t1-red.log
```

Expected (derived): `rc=1`; exactly one FAIL line, case 54 (the ledger's `.root` is the symlink path, which the string test at `:446` rejects, so the write blocks with rc 2); last line `Results: 53 pass, 1 fail`.

**Step 3: GREEN code** — in `worker/hooks/execution-drift-check.sh`:

Replace

```bash
  local session="$1" root="$2" candidates="$3" safe log rows since entry rel f name other row live
```

with

```bash
  local session="$1" root="$2" candidates="$3" safe log rows since entry rel f name other row live ledger_root
```

replace

```bash
    [ "$(cat "${f%.txt}.root" 2>/dev/null)" = "$root" ] || continue
```

with

```bash
    # Roots compare by file identity, not spelling (code vs Code, symlinks), like rule (a)
    # (design 2026-10-18 drift-foreign-session R1).
    ledger_root=$(cat "${f%.txt}.root" 2>/dev/null) || continue
    [ -n "$ledger_root" ] && [ "$ledger_root" -ef "$root" ] || continue
```

and in the comment above `_drift_foreign_paths`, replace `its <ledger>.root equals` with `its <ledger>.root names the same directory as` (file identity).

**Step 4: Run GREEN and the other drift suites**

```bash
cd /Users/roberthyatt/Code/ironclaude && for t in worker/hooks/tests/test-execution-drift-check.sh worker/hooks/tests/test-execution-drift-identity.sh worker/hooks/tests/test-execution-drift-roots.sh worker/hooks/tests/test-execution-drift-writers.sh worker/hooks/tests/test-lock-free-read-git.sh; do bash "$t" > "/private/tmp/claude-502/taf5/t1-green-$(basename "$t" .sh).log" 2>&1; echo "$t rc=$?"; tail -n 1 "/private/tmp/claude-502/taf5/t1-green-$(basename "$t" .sh).log"; done
```

Expected: every `rc=0`; `test-execution-drift-check.sh` ends `Results: 54 pass, 0 fail`.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh
```

Expected: no output.

---

## Task 2: Deploy note, docs and staging (R2) (depends on 1)

No tests required: documentation and a regression run only.

**Files:** Modify `CHANGELOG.md`, `README.md`. Stage the design, this plan and its `.plan.json`.

**Step 1: CHANGELOG.** In `CHANGELOG.md`:
- In the 1.2.0 `Deploy:` line (line 86), replace `then restart Commander and ChatGPT.` with `then restart Commander and ChatGPT, and run `/reload-plugins` in every open Claude Code session: hook scripts update in place, but a session keeps the hook registrations it started with until it reloads (the conversation is kept).`
- In the 1.2.0 drift bullet (line 68), replace `A session in a shared checkout is no longer blocked by files another session wrote with its file tools or lists in an in-progress task, and its own side quests still block.` with `A session in a shared checkout is no longer blocked by files another session wrote with its file tools or lists in an in-progress task, and its own side quests still block. Ledger roots compare by file identity, so a `code`/`Code` or symlinked spelling of the same repository still matches.`

```bash
rg -n -F -e 'run `/reload-plugins` in every open Claude Code session' -e 'Ledger roots compare by file identity' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: two lines, 68 and 86.

**Step 2: README.** In `README.md`, replace the line `Hooks deploy to `~/.claude/ironclaude-hooks/` via `make deploy-hooks` and run from there at runtime (not from the repo directory).` with `Hooks deploy to `~/.claude/ironclaude-hooks/` via `make deploy-hooks` and run from there at runtime (not from the repo directory). After an update, run `/reload-plugins` in each open Claude Code session: the hook scripts change in place, but which events run them is read when a session starts, and reloading keeps the conversation.`

```bash
rg -n -F 'run `/reload-plugins` in each open Claude Code session' /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: one line, 304.

**Step 3: Hook suites**

```bash
cd /Users/roberthyatt/Code/ironclaude && make test-hooks > /private/tmp/claude-502/taf5/t2-hooks.log 2>&1; echo "make_rc=$?"; rg -c '^--- ' /private/tmp/claude-502/taf5/t2-hooks.log
```

Expected: `make_rc=0` and `61`.

**Step 4: Stage and check**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md README.md; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-drift-foreign-session-design.md docs/plans/2026-10-18-drift-foreign-session.md docs/plans/2026-10-18-drift-foreign-session.plan.json; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status; git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: against HEAD `af72c2b`: M `CHANGELOG.md`, `README.md`, `worker/hooks/execution-drift-check.sh`, `worker/hooks/tests/test-execution-drift-check.sh` (4 M); A the three `docs/plans/2026-10-18-drift-foreign-session*` files (3 A); nothing unstaged. Any other entry: STOP and report.
