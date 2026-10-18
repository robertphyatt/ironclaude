# Drift Check Session Attribution: Findings (2026-10-15)

Plan: `docs/plans/2026-10-15-drift-session-attribution.md`. Design and requirements (R1–R6, R3b): `docs/plans/2026-10-15-drift-session-attribution-design.md`.

## What changed

The execution drift check now counts only the paths that changed during this session's own shell commands (`Bash`, or Codex `exec_command`), including commands run by its subagents.

**Windows.** A window records one such command:
- a non-read-only command opens a window, a snapshot of the dirty paths, keyed by agent lineage (`agent_id`, or `main`) and `tool_use_id`;
- the command's own PostToolUse closes it;
- the same lineage's next shell PreToolUse closes any leftovers of that lineage;
- SubagentStop closes that subagent's windows;
- `submit_task` and Stop checkpoint every open window: they append its delta, re-snapshot and keep it.

**Ledger and check.**
- Closing a window appends (dirty after − dirty before) to a ledger for each in-progress task.
- Drift = (ledger ∩ dirty now) − `allowed_files`.
- The claim-time baseline is retired.
- The block message no longer suggests touching files the session did not change, and lists the ways out (R4/R5).

**Files changed:**
- `worker/hooks/execution-drift-check.sh`
- `worker/hooks/hooks.json`:
  - the PreToolUse matcher is `submit_task|Bash|exec_command`;
  - the PostToolUse matcher is `Bash|exec_command`, replacing `claim_task`;
  - a new SubagentStop entry was added.
- `worker/hooks/get-back-to-work-impl.sh` (comment and log text only)
- the three drift suites

## Verification

**RED: new suites against the unchanged hook.** Measured; exit codes `check=1`, `roots=1`, `identity=1`.
- check `Results: 18 pass, 12 fail`. FAIL 1, 2, 3, 5, 7, 8, 9, 11, 12, 15, 16, 17.
- roots `Results: 5 pass, 3 fail`. FAIL 1, 6, 7.
- identity `Results: 12 pass, 1 fail`. FAIL 4.

**GREEN.**
- `bash -n` passed on both scripts, `jq empty hooks.json` passed, and `LC_ALL=C comm -23` appears exactly once.
- check `Results: 33 pass, 0 fail`. This was 30 at first submission; 31-33 were added by the repair.
- roots `Results: 8 pass, 0 fail`.
- identity `Results: 13 pass, 0 fail`. The volume is case-insensitive, so case 3 ran.
- `make -C /Users/roberthyatt/Code/ironclaude test-hooks` exited 0 across every hook suite.

**Can the repair's tests fail?** With the repair stripped from a temp copy of the hooks, cases 31 and 33 fail. Case 32 failed in that run only because 31's window was left behind. On its own it is a regression check that cannot fail, because an empty lineage matches no window.

**Reviews.**
1. Blind Opus plan review: HAS-ISSUES, with two findings:
   - the predicted RED lists;
   - closing every window truncated concurrent subagent windows.
2. Fable fix advisor: confirmed both.
3. The operator approved R3b ("Same-agent close") and the plan was regenerated. The Fable validation returned REMEDIATED.
4. Task-boundary code review: C at first. A finished subagent's leftover window was checkpointed forever, which reintroduced the cross-session false block.
5. After the repair (close at SubagentStop, and a checkpoint can no longer recreate a closed window), re-review graded it A.

## Cost per command (Task 2, read-only)

`git status --porcelain=v1 -z --untracked-files=all` in `/Users/roberthyatt/Code/roleplaying-agents`:
- `real 1.29` s (user 0.25, sys 1.97), exit 0, 28,494 bytes of output;
- repeat run: `real 1.23` s.

Each window costs two of these, so a mutating shell command in that checkout gains about 2.5 s while a task is executing. Read-only commands open no window.

**⚑ Operator decision before deploy:** the time exceeds the plan's 1.0 s flag. Accept it, or narrow the window trigger. One way to narrow it would be to skip windows for commands the guard already classifies as test runners; that would need its own loop.

## Accepted gaps (fail open)

- **R3:**
  - Codex `write_stdin` input;
  - background commands that write after their PostToolUse;
  - files written by MCP tools.
- **R3b:** two parallel shell commands from one agent lineage. The second's PreToolUse closes the first's window early, so a write the first makes after that is not counted.
- **Failed or denied commands** (Claude fires `PostToolUseFailure`, which is not registered; denied commands fire nothing): their leftover windows are swept (next same-lineage PreToolUse, SubagentStop, settle-main at submit/Stop) and the delta is discarded (R3f), so their own off-plan writes are not counted.

## Residual false block (R4)

A false block happens only when another session writes a file while one of this session's commands is running. The block message names the file and lists the three ways out:
1. it clears by itself once the file is committed or reverted;
2. retreat and add the file to `allowed_files`;
3. ask the operator.

There is no escape counter.

## Live checks after deploy

The hooks run from the plugin cache, so the change takes effect only after a redeploy. Then check:
- a Codex `exec_command` opens a window, and its PostToolUse closes it (the payload's `tool_name` may be `Bash`);
- a Claude SubagentStop payload carries the parent `session_id` and an `agent_id`;
- what a Codex SubagentStop carries.

## Backlog (recorded only)

- `linkSubagent` (`state-manager/src/db.ts:666`) has no caller, so GBTW `IS_SUBAGENT` is always false.
- `subagent-drift-detector.sh` uses `.session_id` as the child session, so its link cleanup most likely does nothing. This predates the change.
- Old `~/.claude/ironclaude-drift-baseline-*` files are now unused.
- The newline-path block still reads whole-checkout dirt, so another session's newline-named file can still block. Its message still says "line-based baseline". This predates the change.

**Hardening noted by review:**
- the race between the checkpoint's `-f` check and its `mv` is narrowed, not closed; a double append would be harmless;
- `agent_id` and `tool_use_id` are logged unsanitized;
- a window that spans two tasks attributes its delta to the newer task's ledger;
- the test setup's `claim_task` call is now inert;
- there are no tests for the Codex `cmd` array form or for `BashOutput`.

## End-review fixes (R3c, R3d)

Findings (Fable end review):
- (A) An unreadable or empty-root window made `git -C ""` list the whole checkout of the hook cwd into the ledgers.
- (B) Main-lineage leftover windows were checkpointed (not closed) at submit/Stop and absorbed other sessions' writes into the ledger.

Fixes:
- R3d: `_drift_close_window_file` reads the window once without pipes and skips (return 1) a window that is missing or has an empty root line.
- R3c: new `ic_drift_settle_windows` closes the main lineage's open windows and checkpoints subagent windows; `ic_drift_stop_violations` and the `submit_task` branch call it instead of `ic_drift_checkpoint_windows`.

Evidence:
- RED (before hook change, `check=1`, `Results: 33 pass, 3 fail`): FAIL labels "12 submit closes an open main-lineage window", "35 empty-root window appends nothing", "36 missing window file appends nothing".
- GREEN: `Results: 36 pass, 0 fail`, `check=0`.
- `make test-hooks` exit code: 0.

Operator acceptance: the operator accepted the roughly 2.5 s cost per mutating command.

## End-review #2 fix (R3e)

Finding: shell window closes ran after the workflow-stage gate and root resolution, so a leftover window survived a stage change (retreat) and later absorbed unrelated writes.

Fix: the first `case "$TOOL_NAME"` in `execution-drift-check.sh` now handles `Bash|exec_command` fully for closing: it reads the key and lineage, skips events without `tool_use_id`, closes the command's window on PostToolUse, and closes the lineage's leftovers on PreToolUse, all before the stage gate and `_drift_effective_root`. The later `shell)` branch keeps only the open path.

Evidence:
- RED (before hook change, `check=1`, `Results: 36 pass, 2 fail`): FAIL labels "37 leftover window closes at the next shell PreToolUse outside executing", "38 PostToolUse closes its window outside executing".
- GREEN: `Results: 38 pass, 0 fail`, `check=0`.
- `make test-hooks` exit code: 0.

Recorded accepted gap: this session's own edit to an already-dirty file never enters the ledger.

## End-review #3 fix (R3f)

- Finding: sweeping a leftover window appended its delta to the ledger, so another session's writes made after a failed or denied command ended were charged to this session.
- Operator decision: "Discard on sweep".
- Fix: `ic_drift_close_lineage` now deletes each matching window file without reading it or appending anything; the command's own PostToolUse close and the subagent checkpoint are unchanged. Case 12 rewritten; cases 39-40 added.
- RED (`check=1`, `Results: 37 pass, 3 fail`): FAIL labels "12 submit discards a leftover main-lineage window", "39 lineage sweep discards a leftover window's delta", "40 SubagentStop discards a leftover window's delta".
- GREEN: `Results: 40 pass, 0 fail`, `check=0`.
- `make test-hooks` exit code: 0.

## End-review #4 fix (R3g)

- Finding 1: checkpointing a subagent window at submit/Stop re-snapshots it, so a failed or denied subagent command's leftover window keeps absorbing other sessions' writes into the ledger at every later submit/Stop.
- Finding 2: the main-lineage close at submit/Stop still appended the delta of a leftover window, which may be another session's write made after the command ended.
- Operator decision: "Discard all at submit/Stop".
- Fix: `ic_drift_settle_windows` now deletes every `ironclaude-drift-window-<session>.*.txt` (main and subagent) without appending. `ic_drift_checkpoint_windows` and the checkpoint branch of `_drift_close_window_file` are removed; that function is now the single append-and-delete path used by a command's own PostToolUse. Cases 33-34 rewritten; case 41 (SubagentStop registration present once) added.
- RED (`check=1`, `Results: 35 pass, 6 fail`): FAIL labels "33 submit discards a subagent's leftover window", "34 Stop discards an open subagent window", "37 leftover window closes at the next shell PreToolUse outside executing", "38 PostToolUse closes its window outside executing", "39 lineage sweep discards a leftover window's delta", "40 SubagentStop discards a leftover window's delta". Cases 37-40 are cascades from the leftover window that case 33 left open.
- GREEN: `Results: 41 pass, 0 fail`, `check=0`.
- `make test-hooks` exit code: 0.
- New accepted gap: a subagent command still running at the main session's submit or Stop loses its changes (its window is discarded).
- Post-deploy live check: confirm `agent_id` is present on subagent PreToolUse, PostToolUse and SubagentStop payloads. A missing `agent_id` is fail-open (subagent windows fold into `main`; writes can be missed, nothing is falsely blocked).
