# v1.2.0 Completeness Findings


## Baseline (before change)

A2: SAME (parent_session_id = subagent_session_id = 7c7f63a0-e272-4e34-a05b-3d6ebc128c94; haiku general-purpose subagent, get_resume_state only)

### Commander pytest (commander/tests, .venv python)
suite=0
Summary: 4034 passed in 475.40s (0:07:55)
FAILED/ERROR lines: none

### Hooks shell tests
test-antipattern-lexicon.sh rc=0
test-bash-readonly-guard.sh rc=0
test-codex-brain-stand-down.sh rc=0
test-episodic-memory-sync-singleton.sh rc=0
test-execution-drift-check.sh rc=0
test-execution-drift-identity.sh rc=0
test-execution-drift-roots.sh rc=0
test-execution-drift-writers.sh rc=0
test-fast-model-fallback.sh rc=0
test-gbtw-antipattern-override.sh rc=0
test-gbtw-block-tone.sh rc=0
test-gbtw-bypass-carveout.sh rc=0
test-gbtw-client-aware.sh rc=0
test-gbtw-continuation-context.sh rc=0
test-gbtw-continuation-judge.sh rc=0
test-gbtw-continuation-suppression.sh rc=0
test-gbtw-inflight.sh rc=0
test-gbtw-review-gate.sh rc=0
test-gbtw-waiting.sh rc=0
test-git-authority-activation.sh rc=0
test-hooks-json-brain-entries.sh rc=0
test-ic-skill-ref.sh rc=0
test-last-assistant-text.sh rc=0
test-managed-worktree-guard.sh rc=0
test-mcp-state-logger-active-skill.sh rc=0
test-memory-search-gate.sh rc=0
test-misc-hooks-client-aware.sh rc=0
test-openai-backend.sh rc=0
test-plan-task-context-apply-patch.sh rc=0
test-pmguard-client-aware.sh rc=0
test-pmguard-codex-primary.sh rc=0
test-pmguard-enterplanmode.sh rc=0
test-professional-mode-deactivation.sh rc=0
test-professional-mode-docs-nested-repo.sh rc=0
test-sad-antipattern.sh rc=0
test-session-init-plan-lineage.sh rc=0
test-session-init-stable-dir-seed.sh rc=0
test-skill-handoffs-restaging.sh rc=0
test-skill-state-bridge-inherit-review.sh rc=0
test-state-activator-ppid-scope.sh rc=0
test-state-activator-single-object.sh rc=0
test-subagent-circuit-breaker.sh rc=0
test-warning-queue.sh rc=0
test-workflow-transition-idempotency.sh rc=0
test-brain-gated-actions.sh rc=0
test-brain-hooks-canary.sh rc=0
Line count: 46; all rc=0.

### state-manager npm test
npm=0
Test Files  17 passed (17)
     Tests  273 passed (273)
FAIL names: none

### Typecheck (npx tsc --noEmit)
tsc=0
errors: none

### Named failing sets
- Commander: none
- hooks: none
- state-manager: none
- typecheck: none

A1 runs in Task 13 (post-deploy).

## Commit and worktree verification (R4b)

Memory dir: `/Users/roberthyatt/.claude/projects/-Users-roberthyatt-Code-ironclaude/memory/`. Harness: `/private/tmp/claude-502/taf2/r4b/` (fixture.sh, hook.sh, drive.mjs). Step 1 line numbers matched the plan (HUMAN_CHANNEL= at 83/89/94/100; index.ts:294; cli.ts:43); no drift. Step 3 smoke = 0 (94 passed, 0 failed). Each step's command was taken verbatim from plan.json task 7 and run in zsh.

| # | Memory file | Method | Verdict | Evidence |
|---|---|---|---|---|
| 1 | project_git_intent_mint_consume_skew.md | SCRATCH-REPRO (real hook + dist) | LIVE | `/commit` minted `intent\|commit\|claude-user-prompt`; commit RESULT committed; HEAD lists only a.txt, c.txt still staged, no notice naming c.txt |
| 2 | project_codex_commit_intent_not_minted.md | SCRATCH-REPRO; live half operator-assisted | LIVE (scratch channel mismatch); live half UNVERIFIED-LIVE: a fresh claude-user-prompt row from a Codex `/commit` that the Codex consumer refuses | literal `/commit` minted `claude-user-prompt`; codex drive: `ERROR Direct Git operation requires a matching human intent`; control `$ironclaude:commit` minted `codex-user-prompt` and committed. Whether a phone prompt fired the hook is unprovable here |
| 3 | project_backlog_commit_with_message_no_intent.md | SCRATCH-REPRO | LIVE (control reached intent issuance, so the bare form is recognized; the message form printed nothing at all, so it is not) | `/commit Turn off tools` printed no intent row and no diagnostic (human_intents table never created: `no such table`). Plan control did NOT mint: fixture had nothing staged, so control gave `Human intent issuance failed: Nothing to commit within this session's allowed_files`. A supplemental staged control was blocked by the IronClaude guard (see below) and not retried |
| 4 | project_codex_commit_and_push_refuses_no_assignment.md | SCRATCH-REPRO (simulated codex client, not a real Codex session) | FIXED for the with-plan lane (scratch); no-plan lane LIVE (residual gap) | `$ironclaude:commit-and-push` minted codex-user-prompt; RESULT pushed; remote lists only a.txt; b.txt still staged. No-plan drive: `ERROR Cannot read plan scope: no allowed_files for this session (no active plan)`. Literal `/commit-and-push` typed in Codex hits defect 2 (unverified live) |
| 5 | project_codex_sandbox_blocks_worktree_git_index.md | READ-ONLY-TRACE; operator-assisted | UNVERIFIED-LIVE: git add inside a managed worktree under a workspace-write Codex sandbox | git-authority.ts:294 places worktree at `<primary>/.ironclaude/worktrees/<guid>`; `writable_roots_rc=1` (no IronClaude code sets writable roots); operator config line 5 is `sandbox_mode = "danger-full-access"` (not workspace-write) |
| 6 | project_shared_resources_hide_uncommitted_source.md | SCRATCH-REPRO | LIVE | configure added `tools/mod.py`; worktree path is a symlink to primary; worktree `status:` empty; `/tools/mod.py` in .git/info/exclude (line 8) |
| 7 | project_reconcile_target_locked_at_activation.md | SCRATCH-REPRO | LIVE | both allocations returned `"integration_target":"feature"` rc 0; index.ts tool text still says `into local main` (lines 175, 185, 215); use-managed-worktree/SKILL.md:45 says "Call `activate_session_workspace` with only the canonical `repository_path`" |
| 8 | project_reconcile_stale_base_misleading_sync.md | SCRATCH-REPRO | LIVE | ff_rc=0; first reconcile `ERROR Finalization base_commit is not the merge-base; run sync_worktree_to_target`; sync `Sync requires an active assignment; preserving worktree` rc 1; second reconcile `reconciled` |
| 9 | project_activate_list_assignments_empty_repo_path.md | READ-ONLY-TRACE; operator-assisted | LIVE (skill text); live half UNVERIFIED-LIVE: an errored first list_active_assignments call in a fresh activation transcript | SKILL.md lines 45/48/150 name `list_active_assignments` ("for the repository") with no `repository_path` or `show-toplevel` instruction (grep hits none); index.ts:274 dispatch, :237/:244 `repository_path must be a non-empty string` |

### Guard block
A supplemental (not in the plan) staged control for defect 3 was blocked by the PreToolUse guard before running. Exact message: `✗ [professional-mode-guard|7c7f63a0-e272-4e34-a05b-3d6ebc128c94]: Blocked - BLOCKED — GIT COMMIT/PUSH NOT ALLOWED. Git commit, push, merge, and rebase are blocked during plan execution. Only 'git add' (staging) is allowed.` It was not rephrased or retried. All 12 plan steps themselves ran unblocked.

### Step outputs

#### Step 1
```
83:HUMAN_CHANNEL=""
87:    if [ "$TRIMMED_PROMPT" = "/$operation" ] || [ "$TRIMMED_PROMPT" = "/ironclaude:$operation" ]; then
89:      HUMAN_CHANNEL="claude-user-prompt"
94:      HUMAN_CHANNEL="codex-user-prompt"
100:      HUMAN_CHANNEL="codex-user-prompt"
294:  const humanChannel = identity.client === 'claude' ? 'claude-user-prompt' : 'codex-user-prompt';
43:export const INTERNAL_COMMAND_NAMES = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile', 'cleanup', 'sync', 'reap', 'configure-shared-resources', 'list-shared-resources', 'list-surfaced-orphans', 'reap-orphans', 'resolve-orphan', 'begin-handoff', 'transfer-handoff', 'finish-handoff', 'handoff-status', 'reap-stale-mutations', 'release-handoff', 'finish-handoff-after-target'] as const;
29:      throw new Error('Cannot read plan scope: no allowed_files for this session (no active plan)');
523:    throw new Error('Sync requires an active assignment; preserving worktree');
897:      throw new Error('Finalization base_commit is not the merge-base; run sync_worktree_to_target');
237:  if (typeof value !== 'string' || value.length === 0) throw new Error(`${key} must be a non-empty string`);
244:  if (typeof value !== 'string' || value.length === 0) throw new Error(`${key} must be a non-empty string`);
-rw-r--r--@ 1 roberthyatt  staff  192543 Oct  9 14:56 /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js
-rw-r--r--@ 1 roberthyatt  staff  130734 Oct  9 14:56 /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/hook-intent.js
-rw-r--r--@ 1 roberthyatt  staff  701327 Oct  9 14:56 /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js
```

#### Step 2
```
drive.mjs
fixture.sh
hook.sh
out1.txt
out2.txt
plan
```

#### Step 3
```
smoke=0
PASS: review_passed-only wave resets workflow_stage to idle

Results: 94 passed, 0 failed
```

#### Step 4
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/1
hook_rc=0
intent|commit|claude-user-prompt|
RESULT {"state":"committed","commit":"59408280b8aab098461f359c50a1a75684a311b0"}
head:
a.txt
still_staged:
c.txt
```

#### Step 5
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/2
hook_rc=0
intent|commit|claude-user-prompt|
ERROR Direct Git operation requires a matching human intent — the operator must invoke the rendered git form (/commit, /commit-and-push, or /push) as their literal prompt; free-text prose does not mint intent
hook_rc=0
intent|commit|claude-user-prompt|
intent|commit|codex-user-prompt|
RESULT {"state":"committed","commit":"df13845b698e423a5d6bddc0a9b5c4403d233878"}
```

#### Step 6
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/3
hook_rc=0
Error: in prepare, no such table: human_intents
--- control
⚠️ [state-activator|33333333-3333-4333-8333-333333333333]: ERROR - Human intent issuance failed: Nothing to commit within this session's allowed_files (only foreign or unchanged files are staged)
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"⚠️ [state-activator|33333333-3333-4333-8333-333333333333]: ERROR - Human intent issuance failed: Nothing to commit within this session's allowed_files (only foreign or unchanged files are staged)"}}
hook_rc=0
```

#### Step 7
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/4
hook_rc=0
intent|commit-and-push|codex-user-prompt|
RESULT {"state":"pushed","integratedCommit":"ec1d1d04183738b840214ad75ebce04b3f08c46e"}
remote:
a.txt
still_staged:
b.txt
--- no plan
⚠️ [state-activator|33333333-3333-4333-8333-333333333333]: ERROR - Human intent issuance failed: Cannot read plan scope: no allowed_files for this session (no active plan)
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"⚠️ [state-activator|33333333-3333-4333-8333-333333333333]: ERROR - Human intent issuance failed: Cannot read plan scope: no allowed_files for this session (no active plan)"}}
hook_rc=0
intent|commit-and-push|codex-user-prompt|2026-10-18T05:21:31.723Z
ERROR Cannot read plan scope: no allowed_files for this session (no active plan)
```

#### Step 8
```
294:  const expectedPath = path.join(repository.primaryCheckoutPath, '.ironclaude', 'worktrees', assignment.workspace_guid);
writable_roots_rc=1
5:sandbox_mode = "danger-full-access"
```

#### Step 9
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/6
{"added":["tools/mod.py"],"skipped":[],"rejected":[],"secretBlocked":[],"entries":["tools/mod.py"],"secretHits":{},"scanTruncated":[],"relinked":{}}
configure_rc=0
{"workspace_guid":"33333333-3333-4333-8333-333333333333","repository_identity":"/private/tmp/claude-502/taf2/r4b/6/repo/.git","worktree_path":"/private/tmp/claude-502/taf2/r4b/6/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","branch":"ironclaude/33333333-3333-4333-8333-333333333333","base_commit":"156191c95f712bcfa8fd36fa1e4bb25fe3db064c","current_head":"156191c95f712bcfa8fd36fa1e4bb25fe3db064c","owner_session_id":"33333333-3333-4333-8333-333333333333","worker_id":null,"lifecycle_status":"active","integration_target":"main","integrated_commit":null,"recovery_ref":null,"disposition":null,"created_at":"2026-10-18 05:21:47","updated_at":"2026-10-18 05:21:47"}
allocate_rc=0
lrwxr-xr-x@ 1 roberthyatt  wheel  52 Oct 17 23:21 /private/tmp/claude-502/taf2/r4b/6/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333/tools/mod.py -> /private/tmp/claude-502/taf2/r4b/6/repo/tools/mod.py
status:
8:/tools/mod.py
```

#### Step 10
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/7
{"workspace_guid":"33333333-3333-4333-8333-333333333333","repository_identity":"/private/tmp/claude-502/taf2/r4b/7/repo/.git","worktree_path":"/private/tmp/claude-502/taf2/r4b/7/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","branch":"ironclaude/33333333-3333-4333-8333-333333333333","base_commit":"2d483d17a46b65767ccdb5faab2e6c57c205d23a","current_head":"2d483d17a46b65767ccdb5faab2e6c57c205d23a","owner_session_id":"33333333-3333-4333-8333-333333333333","worker_id":null,"lifecycle_status":"active","integration_target":"feature","integrated_commit":null,"recovery_ref":null,"disposition":null,"created_at":"2026-10-18 05:21:54","updated_at":"2026-10-18 05:21:54"}
first_rc=0
{"workspace_guid":"33333333-3333-4333-8333-333333333333","repository_identity":"/private/tmp/claude-502/taf2/r4b/7/repo/.git","worktree_path":"/private/tmp/claude-502/taf2/r4b/7/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","branch":"ironclaude/33333333-3333-4333-8333-333333333333","base_commit":"2d483d17a46b65767ccdb5faab2e6c57c205d23a","current_head":"2d483d17a46b65767ccdb5faab2e6c57c205d23a","owner_session_id":"33333333-3333-4333-8333-333333333333","worker_id":null,"lifecycle_status":"active","integration_target":"feature","integrated_commit":null,"recovery_ref":null,"disposition":null,"created_at":"2026-10-18 05:21:54","updated_at":"2026-10-18 05:21:54"}
second_rc=0
175:    description: 'Integrate this managed worktree HEAD into local main and keep the worktree alive, gated to the provider-root session; never pushes.',
185:    description: 'Land an operator-confirmed conflict resolution into local main via the isRepair channel; consumes /confirm-resolution intent, requires the registered candidate to equal the authorized HEAD; keeps the worktree; never pushes.',
215:    description: 'Integrate this managed worktree HEAD into local main and FULLY tear the worktree down (remove worktree + temp branch), auto-resolving push-pending/dirty/recoverable-rebase; gated to the provider-root session; never pushes.',
45:5. Call `activate_session_workspace` with only the canonical `repository_path`.
```

#### Step 11
```
wal
fixture=/private/tmp/claude-502/taf2/r4b/8
{"workspace_guid":"33333333-3333-4333-8333-333333333333","repository_identity":"/private/tmp/claude-502/taf2/r4b/8/repo/.git","worktree_path":"/private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","branch":"ironclaude/33333333-3333-4333-8333-333333333333","base_commit":"18506d99dd3e98ffb9a38416fea8ded559cf5243","current_head":"18506d99dd3e98ffb9a38416fea8ded559cf5243","owner_session_id":"33333333-3333-4333-8333-333333333333","worker_id":null,"lifecycle_status":"active","integration_target":"main","integrated_commit":null,"recovery_ref":null,"disposition":null,"created_at":"2026-10-18 05:21:54","updated_at":"2026-10-18 05:21:54"}
ff_rc=0
hook_rc=0
intent|reconcile|claude-user-prompt|
ERROR Finalization base_commit is not the merge-base; run sync_worktree_to_target
Sync requires an active assignment; preserving worktree
sync_rc=1
hook_rc=0
intent|reconcile|claude-user-prompt|2026-10-18T05:21:55.216Z
intent|reconcile|claude-user-prompt|
RESULT {"state":"reconciled","integratedCommit":"86ef22cdd78b3001b81341dbd7cb5a5f9f56816a"}
```

#### Step 12
```
45:  `mcp__plugin_ironclaude_workspace-manager__list_active_assignments`,
48:- Codex: Codex `workspace-manager` `list_active_assignments`, Codex `workspace-manager` `activate_session_workspace`, and Codex `workspace-manager` `get_workspace_status`.
150:Call the trusted client's `list_active_assignments` for the repository. This is
237:  if (typeof value !== 'string' || value.length === 0) throw new Error(`${key} must be a non-empty string`);
244:  if (typeof value !== 'string' || value.length === 0) throw new Error(`${key} must be a non-empty string`);
274:    case 'list_active_assignments': return dependencies.listActiveAssignments(args);
```

### Operator items for Task 13
- Defect 2: operator types `/commit` in a Codex session with no staged changes; the agent then runs `sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT operation,human_channel,consumed_at FROM human_intents ORDER BY intent_id DESC LIMIT 1"`. LIVE if a fresh `claude-user-prompt` row appears that the Codex consumer refuses; none if the hook never fired.
- Defect 5: in a Codex thread under `workspace-write` (the operator's config is `danger-full-access`, so a workspace-write thread must be chosen explicitly) on a scratch repository with a managed worktree, run `git -C <worktree> add <file>`. LIVE: `fatal: Unable to create '<primary>/.git/worktrees/<guid>/index.lock': Operation not permitted`; FIXED or mitigated: exit 0.
- Defect 9: operator runs `/activate-professional-mode` in a fresh session; the agent greps that session's transcript for a first `list_active_assignments` call without `repository_path` that errored.

## Full suites (after change)

Run after Task 15 (2026-10-18), against the Task 1 baselines.

| Suite | Baseline (Task 1) | After change (Task 11) |
|---|---|---|
| Commander pytest (`commander/tests`) | `suite=0`, 4034 passed | `suite=0`, 4072 passed in 492.70s, no FAILED/ERROR lines |
| Hook suites | 46 files, all rc=0 | 47 files, all rc=0 (45 under `worker/hooks/tests` incl. the new `test-local-backoff.sh`, plus `test-brain-gated-actions.sh` and `test-brain-hooks-canary.sh`); no log holds a FAIL line (`fail_files_rc=1`) |
| state-manager `npm test` | `npm=0`, 17 files, 273 tests | `npm=0`, 17 files, 273 tests |

The end review noted that `make test-hooks` (Makefile:40-44) runs 60 suites: the 47 above plus 6 more `worker/hooks/test-*.sh` and 6 more `commander/hooks/tests` suites. `make test-hooks` was then run in full: `make_rc=0`, all 60 suites pass.

`test_daemon.py::TestGradeBoundedFastLaneReadTimeout::test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout` passes (Task 14). `test_local_backoff.py` holds 16 tests (10 from Task 9, 3 from Task 14, 3 from Task 15). No failing set in either run.

## Release commit message paragraph

Local-box backoff, for anyone running their own Ollama or OpenAI-compatible box: when the box fails, the hooks and the Commander's local grader skip it for local_backoff_minutes (default 15; 0 turns it off; set it top-level or per backend in ~/.claude/ironclaude-hooks-config.json) and ask the Haiku-class model instead (Claude Haiku, or the newest Luna on Codex). A box failure is a connect failure, an HTTP error, an empty answer, invalid JSON, or a read timeout at the box's own configured timeout_seconds. The hooks also count their own budget timeout (hook_validation_budget_seconds, default 8 s) as a failure. The only thing that does not trip the backoff is a Commander caller deadline shorter than the box's configured timeout, such as the fast lane's fast_lane_grade_timeout_seconds (default 5 s). The hooks and the Commander share one marker, ~/.ironclaude/state/local-backoff/local-box-backoff.json, in a private 0700 directory. The first call after expiry tries the box again; a success clears the marker and a failure renews it. The marker is the only thing that makes IronClaude skip your box, so with 0 your box is tried on every call.
