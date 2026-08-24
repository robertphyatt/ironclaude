# Reaper "release failed" diagnosis (investigation loop) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Prove whether the reaper's ~47 startup "release failed … identity does not match"
WARNINGs are H1 (commander runs a pre-`7388ad7` workspace-manager — a stale deploy of an
already-shipped fix) or H2b (current code; worktrees present on a foreign branch — correct
preserve behavior), and write a findings note with the evidence and recommended remediation.

**Requirements:** docs/plans/2026-08-21-reaper-owned-gone-diagnosis-requirements.md

**Architecture:** A strictly read-only investigation. The execute stage (which unblocks
Bash/sqlite) uses the **worktree presence/branch state as the primary, time-robust
discriminator** (worktree deletion is monotonic; only pre-`7388ad7` code throws the identity
message on a *gone* worktree), guarded by exact-message discipline (only
`validateManagedIdentity`'s `:179` string counts) and the code's present-conjunction
(`existsSync` AND in `git worktree list`). The bundle version-match and temporal signals are
corroboration. No product code, no worktree/branch/ref/row mutation, no restart, no redeploy.

**Tech Stack:** bash (ps, stat, ls, find, grep, printenv), sqlite3 (SELECT only, `-readonly`),
git (worktree list --porcelain / log), Markdown (findings note).

**Execution invariants (author + reviewer check against these):** shell state does not persist
between steps (literal absolute paths); Bash cwd is `commander/` (use absolute paths and
`git -C <repo-root>`); quote globs; foreground `sleep` blocked; `docs/` gitignored
(`git add -f`); an empty result must be distinguishable from a failed command (no `2>/dev/null`
on evidence; list values, not counts; no `head`-truncation of a proof grep); this is a
DISCOVERY task, so a step's `expected` states the FORM of the answer and what a value MEANS — it
never hard-codes the value the step exists to find; the `professional-mode-guard` treats a
quoted `|` as a shell pipe and matches the word "commit"/"push" anywhere in a command (use
single-term greps; use `printenv VAR` never `$VAR`; name artifacts without those words); macOS
(darwin) `stat`/`ps` use BSD syntax (`stat -f`, `ps -E`); STRICTLY READ-ONLY — no `sqlite3`
write, no `git worktree prune`, no ref/branch/row change.

---

## Task 1: Diagnose the reaper owned-gone failures and write the findings note

**Files:**
- Create: `docs/plans/2026-08-21-reaper-owned-gone-findings.md` (the sole deliverable)

No tests required: this is a read-only diagnosis whose deliverable is a findings note; every
observed value is measured live in these steps and pasted into the note, not asserted by a test.

**Step 1: Resolve the workspace-manager DB path (env-first, the DAEMON's env) and dump the
leaked rows.** `_workspace_manager_db_path` (main.py:238-242) is env-FIRST:
`os.environ.get("WORKSPACE_MANAGER_DB_PATH") or expanduser("~/.claude/ironclaude-workspaces.db")`
— and the reaper resolves it in the COMMANDER daemon's environment, not this shell. Locate the
commander PID, read its env for the override, and record BOTH outcomes:
```bash
ps -eo pid,lstart,command
```
```bash
printenv WORKSPACE_MANAGER_DB_PATH
```
(Then, with the commander PID from the first command, inspect the daemon env — darwin BSD form —
and use that value if set: `ps -p <commander-PID> -wwwE`.) The resolved path = the daemon env
value if set, else `~/.claude/ironclaude-workspaces.db`. Dump the leaked rows read-only:
```bash
sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT workspace_guid, repository_identity, lifecycle_status, recovery_ref, worktree_path, owner_session_id FROM assignments WHERE lifecycle_status != 'cleaned' ORDER BY repository_identity, lifecycle_status;"
```
Expected: BOTH DB-path outcomes recorded (env set? → which; else default), then one row per
still-leaked assignment showing `lifecycle_status`, `recovery_ref` presence, and `worktree_path`.
Substitute the resolved path into the `sqlite3 -readonly` command if the env override is set
(a missing DB must be visible — no `2>/dev/null`).

**Step 2 (PRIMARY discriminator): classify every leaked row gone vs present-on-foreign-branch by
the code's conjunction.** "present" is `existsSync(worktree_path)` AND the path appears in
`git worktree list` (workspace-service.ts:752-753); "foreign branch" = present AND the porcelain
`branch` line ≠ `refs/heads/ironclaude/<workspace_guid>` (detached/null counts as foreign, per
:178). Derive the owning (game) repo primary checkout from a `worktree_path` (the substring
before `/.ironclaude/worktrees/`), then run BOTH — the dir listing and the porcelain worktree
list (one of each covers all rows):
```bash
ls -la <owning-repo-primary-checkout>/.ironclaude/worktrees
```
```bash
git -C <owning-repo-primary-checkout> worktree list --porcelain
```
(Enumerate `<owning-repo-primary-checkout>` from Step 1's `worktree_path` values; do not assume a
repo name. If multiple owning repos appear, run both commands per repo.) Expected: a per-guid
classification — GONE (dir absent OR not git-listed) vs PRESENT-ON-FOREIGN-BRANCH (both hold and
the branch ≠ the managed `ironclaude/<guid>` ref). GONE ⟹ H1 evidence for that guid;
present-on-foreign-branch ⟹ H2b evidence. A MIXED split across guids is a valid result.

**Step 3: Exact-message discipline — match each warned guid to its precise exception text.** The
gone⟹H1 inference holds ONLY for `validateManagedIdentity`'s exact string; post-`7388ad7` code
throws OTHER messages on gone worktrees through the same `release failed` wrapper
(tombstone/durable-proof/eligibility guards). Read the commander log and record, per guid, the
exact exception:
```bash
grep -n "release failed" /tmp/ic/daemon.log
```
Expected: for each warned `workspace=<guid>`, the exact trailing exception text. Only warnings
whose text is `Managed worktree Git identity does not match durable assignment; reconciliation
must preserve it` enter the gone⟹H1 inference in Step 5; a different message means a different
(post-`7388ad7`-compatible) path and must NOT be read as H1. (If the log path differs, record the
actual path used; no `2>/dev/null`.)

**Step 4 (CORROBORATION): identify the bundle the commander resolves, and capture temporal
signals.** The commander resolves its workspace-manager by version-match, not the command line
(`_select_discovered_root`, workspace_client.py:73-88): the cache root whose
`.claude-plugin/plugin.json` (codex: `.codex-plugin/plugin.json`) base_version == the commander
version, with `cli_exists` and `manifest_valid`. Enumerate the roots, read each manifest version,
and grep the matched bundle for the post-`7388ad7` marker `reapLeakedAssignment`; capture mtimes
and the commander start time:
```bash
ls -la /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude
```
```bash
ls -la /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude
```
For each cache root, read its manifest version and mark `cli_exists` (one command per root):
```bash
cat <root>/.claude-plugin/plugin.json
```
```bash
grep -c reapLeakedAssignment <matched-root>/mcp-servers/workspace-manager/dist/cli.js
```
```bash
stat -f '%Sm %N' <matched-root>/mcp-servers/workspace-manager/dist/cli.js
```
```bash
stat -f '%Sm %N' <owning-repo-primary-checkout>/.ironclaude/worktrees
```
Expected: the bundle the commander runs now (matched root), its `reapLeakedAssignment` count
(0 = pre-`7388ad7`, >0 = post), the bundle `cli.js` mtime vs the commander `lstart` (from Step 1
— restarted since the deploy?), and the worktrees-dir mtime (earlier than `lstart` ⟹ no external
deletion since startup, closing monotonicity). This CORROBORATES and drives the remediation
recommendation (stale-resolved-root vs already-current); it does NOT by itself prove what ran at
warning time.

**Step 5: Write the findings note.** Create
`docs/plans/2026-08-21-reaper-owned-gone-findings.md` with the ACTUAL command output from Steps
1-4 pasted as evidence: (a) the primary per-guid gone/present-foreign classification (Step 2) and
the exact-message match (Step 3); (b) the leaked rows' `lifecycle_status`/`recovery_ref` summary
(Step 1); (c) corroboration — the resolved bundle's pre/post-`7388ad7` status and the temporal
signals (Step 4); (d) the verdict per guid — **H1** (gone + `:179` message: pre-`7388ad7` ran;
remediation = update the claude-cache workspace-manager to post-`7388ad7` and restart the
commander, after which the gone-tolerant reaper reaps the rows, anchoring any branch tip first),
**H2b** (present-on-foreign-branch: the guard is correct; remediation = a scoped follow-up loop),
or a MIXED split; (e) any guid/question that could not be answered and why. Expected: the note
exists and answers (a)-(d).

**Step 6: Stage the findings note.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-08-21-reaper-owned-gone-findings.md
```
Expected: the findings note is staged (professional mode blocks commit).
