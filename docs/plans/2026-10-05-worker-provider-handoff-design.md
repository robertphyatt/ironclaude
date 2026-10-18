# Automatic worker provider handoff design

Created: 2026-10-05
Status: Design complete
Loop: full — assignment ownership, database writes, process termination and concurrent lifecycle operations.
Scope: hold. Delegated authority applies to routine design/plan/execution choices after operator's "proceed as suggested"; no live worker action or release authority.

## Requirements

Predicted answer: yes (delegated)

Use docs/plans/2026-10-05-worker-provider-handoff-requirements.md. Operator wants automatic Claude-to-Codex and Codex-to-Claude continuation when native account usage is exhausted. Preserve same managed worktree and unfinished work. Source/test/docs only; stage without committing, pushing, deploying, restarting applications or touching live workers.

## Architecture and alternatives

Predicted answer: yes (delegated)

Use one durable Commander handoff controller and assignment-side hold. New native session receives same worker identity, workspace GUID/path/branch and objective, plus durable PM progress. Global provider selection does not change. Source process must be proved stopped before successor can edit.

Existing kill-and-spawn is unsuitable: kill finalizes/releases assignment and spawn allocates another GUID. It is simple but violates same-worktree preservation. Public rebind is unsuitable: bind deliberately rejects different non-null owners and loosening it would permit takeover. Explicit held transfer is feasible with existing private workspace CLI, SQLite, provider routing and native launch primitives; it adds a bounded recovery state because these databases and processes cannot commit atomically. It aligns with operator's requested kill/fresh-worker mechanism while protecting work from daemon finalization.

## Assignment hold and ownership

Predicted answer: yes (delegated)

Add an additive workspace handoff table; do not add a lifecycle or rebuild assignments. Claim holds exact repository/GUID/worker/source root/path/branch/base/HEAD/integration target, generation nonce and phase. Ordinary bind remains unchanged. Private begin/transfer/finish commands require same scoped capability token issued for this claim; no public MCP takeover tool or worker environment token. Token remains Commander control data, not a human Git intent. Validate actual canonical Git identity and no primary ownership, integration/rebase operation or competing assignment mutation before claiming.

Handoff claim and normal mutation claims share assignment-scoped exclusion. Every integrating/destructive workspace entry acquires mutation claim before first filesystem/Git effect, checks handoff hold atomically and releases only its own claim. A crashed operation leaves an explicit blocker; no timeout grants permission. Cover Commander finalization, direct authority, reconciliation/rebase, sync, rescue/abandon, cleanup/reap and shared-resource relinking. Existing orphan protection preserves every non-cleaned assignment. Beginning hold while another operation runs fails before stopping source. Transfer CAS requires exact old owner, claim token and unchanged evidence; it changes only owner plus recovery-obligation ownership and revokes unconsumed old-root human intents. Hold persists through registry update and continuation dispatch.

## Commander controller and process sequence

Predicted answer: yes (delegated)

Persist worker_handoffs in existing Commander DB with source runtime/workspace/objective snapshot, native quota event key, selected target, fresh target root, phases, notification claim and failure reason. Atomic claim changes worker status to handoff_pending; only one active generation per worker. All completion/finalization/kill/dispatch entry points consult it, including daemon results from stale running-worker snapshots. No handoff path calls ordinary kill_worker, spawn allocation, abandon or finalize.

Preflight fresh opposite capability on same host and semantic tier, validate installed workspace command support and both instruction files already present. Missing files are blockers; do not inject files into held dirty worktree. Snapshot source pane process identity (PID plus creation time), descendants, worker process identity and tracked external tasks. Terminate only source tree, prove no snapshotted process can still write, check no unknown/reparented workspace process/external task remains. Unknown, inaccessible host or PID ownership blocks. Tmux absence alone is insufficient. Prevent old automatic quota continuation by verified termination.

Launch opposite client in read-only native mode without the work objective or a tool-driving prompt, observe fresh native UUID through existing hook/PID binding, activate PM through existing SQLite helper, verify PM and identity. Prove that native conversation storage can resume this UUID; a hook UUID file alone is insufficient. Permit one bounded read-only acknowledgment turn (reply ready, no tools, no objective) when needed to produce native persistence, and verify its completion and saved identity before transfer. Unsupported or unproved persistence pauses safely. Prepare progress and transfer assignment. Stop read-only bootstrap and prove its tree stopped; resume that target's own fresh UUID with normal worker command, verify same UUID and readiness. Register all target runtime fields in one worker-row CAS, then dispatch continuation. This is same-provider resume of fresh target only; never resume source UUID across providers. Unsupported native read-only/resume flags or unverifiable remote process driver pause rather than falling back to unsafe launch.

## PM continuity

Predicted answer: yes (delegated)

Use private bounded Python helper against existing state DB, through existing local/SSH argv transport. No new public state-manager tool or forged provider metadata. Source is quiescent; validate exact roots/project/plan JSON/lineage/wave/tasks against snapshot. Target must be fresh PM-on idle with no plan, tasks or review evidence. Transaction copies exact session progress and source-attributed plan review rows required by current gates; original historical session/task/review rows remain unchanged. Preserve exact model/hash/verdict/timestamp and record predecessor provenance in durable handoff row/audit log. Copy completed review_passed tasks unchanged; in_progress/submitted reopen pending and force normal task review. Preserve circuit breaker, memory obligation and review-block counters. Reset testing-theatre flag so successor checks its work. The current design registration is transferable ownership, not immutable review history. CAS its terminal_session from source to successor against exact snapshotted design_file/registered_at/consumed; preserve those fields and record full predecessor evidence in the same audit transaction. Never call replacement registration that resets consumed, widen planning gates, or mint human authority. Never copy human intents, subagent ownership, pending questions or old chat identity. Brainstorming/design/planning stages retain their actual stage/artifact obligations; do not manufacture an execution approval. A malformed/unsupported state pauses with exact reason. Completed execution is not usage-handoff eligible.

## Native quota and routing

Predicted answer: yes (delegated)

Read bounded provider-native session JSONL records tied to registered native UUID/worktree/process generation. Claude predicate is native synthetic assistant API error: isApiErrorMessage=true, error=rate_limit, apiErrorStatus=429, quotaLimits.status=rejected and known nonempty rateLimitType/reset; observed blocked-worker record also confirms no available overage/fallback. Codex predicate is native event_msg token_count rate_limits with explicit known rate_limit_reached_type and applicable limit_id, or native usage-limit error code. Unknown/null quota fields, percentages alone, credits balance alone, server_overloaded/auth/network/model/context errors and quoted response content do not qualify. Validate session_meta identity for Codex, timestamp/current turn and latest status; newer recovery/success supersedes old quota. Never match historical pipe-pane text.

Exclude exhausted client per router call with backward-compatible keyword. Fresh auth/install probes are necessary but do not prove quota reset. Persist exhaustion quarantine; only fresh provider-specific availability evidence or explicit operator recovery clears it. Both unavailable means paused unfinished, one notification per incident, no automatic ping-pong. Do not change role-global current_client. Legacy claude-* worker_type remains semantic tier, concrete client/model stored separately.

## Recovery and errors

Predicted answer: yes (delegated)

Phases: claimed, source_stopped, target_ready, pm_prepared, owner_transferred, runtime_registered, continuation_dispatched. Each compare-and-set stores evidence before next irreversible operation. Source/assignment/PM/registry are separate resources; restart reconciles recorded identity/phase without claiming global atomicity. Do not auto-relaunch a possibly live successor or resend uncertain continuation. Pre-transfer failure stops verified bootstrap and keeps held source assignment. Post-transfer failure keeps target-owned held assignment and marks paused, with exact evidence. No automatic rollback to old UUID, release, completion or cleanup. Dispatch intent is recorded before send; uncertainty holds for explicit reconciliation, not replay. Successful verified dispatch completes claim and releases assignment hold; stale old .done files/log offsets cannot finalize successor.

## Verification

Predicted answer: yes (delegated)

Reuse existing pytest/Vitest suites. Real temporary Git tests compare HEAD, branch, index tree, staged/unstaged content, untracked files and shared links before/after owner transfer. Test distinct-owner bind still rejects. Test assignment mutation/hold exclusion with separate DB connections and stale claims; finalized/foreign/changed evidence refuses. Python tests cover both providers and exact native record contracts, task/review attribution, no copied human authority, source creation-time/death uncertainty, target bootstrap/readiness, same fresh-target resume identity, atomic registry changes, duplicate/restart/dispatch uncertainty, both-provider quarantine and daemon completion races. Stub external CLI/tmux/process/SSH only; retain real SQLite and temp Git at storage seams. Run planned focused tests, relevant existing regressions and TypeScript build. Broad-suite RPC failure remains separate and must be reported honestly if encountered.

## Next workflow

Predicted answer: Yes, invoke writing-plans (delegated)

Continue with writing-plans then sequential focused Terra implementation tasks, mandatory blind plan review and task-boundary reviews. Source completion does not certify deployed/live handoff; this loop performs no production handoff or application restart.
