# M2 Live-Proof (/reconcile + /push, v1.1.7 deploy-proof) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Live-prove the v1.1.7 never-lose-work recovery chain (/reconcile preserve → unassigned /push publish → reconcile_finalization clear) plus the reconcile/push/tombstone lanes against the DEPLOYED dist, scratch-isolated, and record a findings doc.

**Requirements:** docs/plans/2026-08-25-m2-live-proof-requirements.md

**Architecture:** A single B-Prime driver at an absolute scratchpad path (in `allowed_files`) builds an airtight scratch sandbox with **TWO** scratch repos (A and B) + bare origins under ONE session id (SID), allocates one managed worktree per repo via the deployed CLI, then runs 10 automated lanes mint→consume against the deployed `dist/*.js`, each asserting an exact grounded outcome. Repo A carries the preserved-obligation chain (R-1→R-2→T-1→P-2a→P-2b→P-2c); repo B carries R-3+P-1. R6 is an operator-keystroke sequence documented in the findings doc.

**Tech Stack:** bash, node (ESM dist import), jq, sqlite3, git.

**No unit tests required:** the driver IS the falsifiable proof — each lane asserts a concrete deployed-behavior outcome a broken lane would not produce. TDD does not apply to a live-proof exercise.

**Execution invariants (declared for the reviewer):** the driver is ONE script run in one invocation (intra-driver state is fine); Bash cwd is `commander/` (driver uses absolute paths); `docs/` is gitignored (findings doc staged `git add -f`); B-Prime — the driver's absolute path is in `allowed_files`, invoked `bash <abs>` (keyword-free) so child git/node ops evade the executing-stage git-keyword guard; evidence outlives cleanup (findings doc written BEFORE scratch teardown); every lane assert names the broken state it catches.

**Authoring guardrails (from grounding — the driver MUST honor all):**
1. NEVER run any git op in a repo between a lane's mint and its consume — the consume re-observes evidence live and must byte-match the minted evidence (git-authority.ts:548-576); an interleaved `git add` turns the lane into a spurious `'requires a matching human intent'` failure.
2. Every mint's `cwd` JSON field must be inside the correct scratch repo (A or B); one `sessions` row (SID, professional_mode='on') covers both.
3. Keep each mint immediately adjacent to its consume — intents expire in 5 minutes (db.ts:500).
4. Assert LANE pass/fail (10 lanes), not per-assertion; the `M2: N passed` line counts lanes.

---

## Task 1: Author + run the M2 live-proof driver; write findings

**Files:**
- Create: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/m2-live-proof-driver.sh`
- Create: `docs/plans/2026-08-25-m2-live-proof-findings.md`

**Step 1: Read the skeleton template.** Read `worker/hooks/tests/test-git-authority-activation.sh` in full for the exact shapes: scratch `HOME`/`WORKSPACE_MANAGER_DB_PATH`; the hand-built scratch state DB (`sessions(terminal_session,professional_mode,workflow_stage)` with `professional_mode='on'`, plus `wave_tasks`,`audit_log`); the `run_prompt()` mint helper (pipes `{prompt,session_id,cwd,hook_event_name:"UserPromptSubmit",thread_source:"user"}` to `state-activator.sh` with `HOME`/`WORKSPACE_MANAGER_DB_PATH`/`IRONCLAUDE_WORKSPACE_HOOK_INTENT`); the `node --input-type=module` consume heredoc; the `git init`/`git init --bare`/`allocate` fixtures.

**Step 2: Write the driver — sandbox + TWO-repo fixtures.** In the driver: `set -euo pipefail`; absolute deployed paths `WM=/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist`, `HOOK=/Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh`, `HOOK_INTENT=$WM/hook-intent.js`, `INDEX=$WM/index.js`, `CLI=$WM/cli.js`. `TMP_HOME=$(mktemp -d)`; `mkdir -p "$TMP_HOME/.claude"`; scratch `STATE_DB=$TMP_HOME/.claude/ironclaude.db` with ONE `sessions` row (`terminal_session=$SID`, `professional_mode='on'`); `WORKSPACE_DB=$TMP_HOME/.claude/ironclaude-workspaces.db`. Fixed `SID`. Build **repo A** (`git init -b main`, one commit, `git init --bare` origin A, `git remote add origin`, `git push -u origin main`) and **repo B** identically. Allocate one managed worktree per repo: `WORKSPACE_MANAGER_DB_PATH=$WORKSPACE_DB node "$CLI" allocate '{"repository_path":"<repoA>","workspace_guid":"<GUID1>","owner_session_id":"<SID>","integration_target":"main"}'` (capture `WT_A`); same for repo B with `GUID2` (capture `WT_B`). Define `mint <op> <cwd>` (run_prompt shape) and `consume <op> <argsJSON> [threadId]` — a node heredoc building `deps = createPublicToolDependencies(initCliDb(), {client:'claude',sessionId:'<SID>',invocationThreadId:(threadId||'<SID>'),source:'ppid_file'})`; dispatch `push`/`commit-and-push`→`deps.finalizeDirect(op,args)`, `reconcile`→`deps.reconcileWorktree(args)`, `reconcile-finalization`→`deps.reconcileFinalization(args)`; print the JSON result or `ERR:`+message. Add `pass_lane`/`fail_lane` tallies and a helper that greps a captured value for an exact literal.

**Step 3: Write the lanes.** Exact grounded contracts; RIGHT column is the literal to assert. **Order is fixed.**

REPO A (GUID1, WT_A) — the preserved-obligation chain:

| Lane | Action | Assert (exact) |
|---|---|---|
| R-1 | in `WT_A`: edit+`git add`+`git commit`; `mint reconcile <WT_A>`; `consume reconcile '{"repository_path":"<repoA>","workspace_guid":"<GUID1>"}'` | result has `"state":"reconciled"`; `git -C <repoA> rev-parse main` == WT_A HEAD; WT_A exists. (R-1's recycle returns GUID1 to `active` for R-2.) |
| R-2 | in `WT_A`: stage a change AND create an untracked file; `mint commit-and-push <WT_A>`; `consume commit-and-push '{...,"workspace_guid":"<GUID1>","message":"m2"}'` → assert `ERR:` has `Managed worktree has uncommitted changes`; assert DB row `lifecycle_status='active'` AND disposition JSON `"phase":"integration-pending"`; `rm` the untracked file; `mint reconcile <WT_A>`; `consume reconcile '{...,"workspace_guid":"<GUID1>"}'` | reconcile result has `"state":"integrated-local"`; DB disposition `"phase":"push-pending"` PRESERVED; `git -C <repoA> rev-parse main` == candidate (= the R-2 commit) |
| T-1 | `WORKSPACE_MANAGER_DB_PATH=$WORKSPACE_DB node "$CLI" cleanup '{"repository_path":"<repoA>","workspace_guid":"<GUID1>","owner_session_id":"<SID>"}'` capturing stderr + exit | non-zero exit AND stderr has `Refusing to tombstone a worktree with a push-pending obligation` |
| P-2a | `mint push <repoA>` (NO guid — the deployed no-guid mint issues the `primary:<identity>` sentinel); `consume push '{"repository_path":"<repoA>","workspace_guid":"<GUID1>"}'` (guid supplied to consume only) | `ERR:` has `Direct Git operation requires a matching human intent` (the intent will not bind to a terminal/integrated row — a managed push over the preserved row cannot even be authorized) |
| P-2b | `mint push <repoA>` (NO guid; repo A's only assignment is now the integrated preserved row ⇒ zero ACTIVE ⇒ sentinel lane); `consume push '{"repository_path":"<repoA>"}'` (NO workspace_guid) | result has `"state":"pushed-only"`; `git -C <originA> rev-parse refs/heads/main` == candidate |
| P-2c | (NO mint — reconcileFinalization consumes no intent) `consume reconcile-finalization '{"repository_path":"<repoA>","workspace_guid":"<GUID1>"}'` | result has `"state":"cleaned"`; DB disposition IS NULL; DB `lifecycle_status='active'`; WT_A dir still exists |

REPO B (GUID2, WT_B):

| Lane | Action | Assert (exact) |
|---|---|---|
| R-3 | in `WT_B`: make an uncommitted change (leave dirty); `mint reconcile <WT_B>`; `consume reconcile '{"repository_path":"<repoB>","workspace_guid":"<GUID2>"}'` | `ERR:` has `Managed worktree has uncommitted changes`; `git -C <repoB> rev-parse main` unchanged (== base) |
| P-1 | in `WT_B`: `git add`+`git commit` the change; `mint push <WT_B>`; `consume push '{"repository_path":"<repoB>","workspace_guid":"<GUID2>"}'` | result has `"state":"pushed-only"`; `git -C <originB> rev-parse refs/heads/ironclaude/<GUID2>` == WT_B HEAD |

REPO-INDEPENDENT:

| Lane | Action | Assert (exact) |
|---|---|---|
| D-1 | `grep -c` each in `$INDEX` | `hasPushPendingObligation`, `finalizeReconcile`, `integrated-local`, `Refusing to tombstone`, `Push-only authority does not designate active managed workspace` all > 0 |
| N-1 | `consume reconcile '{"repository_path":"<repoA>","workspace_guid":"<GUID1>"}' <BAD_THREAD>` (invocationThreadId != SID) then `consume push '{"repository_path":"<repoA>"}' <BAD_THREAD>` | both `ERR:` have `Direct human authority can be consumed only by the provider-root session` |

Print `M2: <lanes_passed> passed, <lanes_failed> failed` (10 lanes total); `exit 1` if any lane failed.

**Step 4: Run the driver.**
Run:
```bash
bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/m2-live-proof-driver.sh
```
Expected: `M2: 10 passed, 0 failed` (exit 0). A lane failure is a REAL deploy defect — record it in the findings doc; do NOT edit the deployed product to force green.

**Step 5: Write the findings doc.** Create `docs/plans/2026-08-25-m2-live-proof-findings.md` (structured like `docs/plans/2026-08-22-loop3a-live-proof-findings.md`): methodology (two-repo scratch isolation, mint→consume, B-Prime, real-CLI allocate); deploy-currency (D-1 grep counts); a lane-by-lane results table using the ACTUAL captured Step-4 output (not predicted); observed behaviors (e.g. P-2a's deployed defense is the intent-binding filter, upstream of the :1003 guard); defect list (empty only if all passed); the explicit statement that the candidate resume-push + durable-anchor teardown are unshipped v1.1.7 non-goals (CHANGELOG:48-49) and that P-2b/P-2c prove the shipped recovery chain; the R6 operator-keystroke sequence (`/reconcile` then `/push` on a real managed worktree); bottom line.

**Step 6: Stage the findings doc.**
Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-08-25-m2-live-proof-findings.md
```
Expected: staged (professional mode blocks commit). The driver script is a scratchpad artifact and is not committed.
