# Worker provider handoff requirements

## Operator authority

Operator reports Claude worker d1501-r2-fix-loop3 blocked by account usage despite worker provider selected Codex, and requests a PM loop so Codex can pick up for Claude when usage runs out and vice versa. Same worktree must be preserved. Operator answered "proceed as suggested" after recommendation "Automatic, with safeguards". Original scoped dialogue outranks this derived contract.

## Required behavior

R1. On confirmed provider-native usage exhaustion, automatically replace a managed Claude worker with a fresh Codex worker or a managed Codex worker with fresh Claude worker, subject to configured/enabled opposite-client capability on the same host and same semantic tier/fallback rules. Do not change global provider preference or unrelated workers.
R2. Keep same workspace GUID, repository identity, physical worktree path, branch, base, integration target, HEAD, staged/unstaged/untracked contents and shared-resource links. No reset, clean, stash, copy, commit, integration, abandonment or worktree removal during handoff.
R3. Preserve original objective, authorized constraints and unfinished disposition. Fresh native session ID is mandatory; never pass one provider's ID to the other or forge session metadata. Successor receives exact durable PM plan/task progress with original review attribution, inspects existing plan/design/task artifacts and current worktree, and continues through normal PM gates. Preserve completed tasks; reopen unfinished/submitted tasks for work and review. Transfer current design registration ownership with predecessor attribution so successor planning gates remain valid. Never inherit human Git intents or clear existing safety blocks.
R4. Before successor gets editing authority, stop source process and verify it and its relevant descendants cannot continue writing. Unknown process ownership, active external work or unverified death is a blocker; preserve work and surface reason. Source must not later auto-resume beside successor.
R5. Durable atomic handoff claim excludes completion, finalization, idle reaping, duplicate dispatch and concurrent/replayed takeover. Rebinding checks exact previous owner and managed assignment evidence; stale/foreign/root/path/branch/HEAD changes fail closed. Never relax ordinary bind semantics.
R6. Fresh native target readiness, PM activation and observed identity must be verified. Register provider/model/native UUID/workspace consistently; dispatch continuation only after successful assignment transfer. Startup/bind/dispatch failures leave preserved unfinished work with explicit recovery state, never report completed.
R7. Detect current native quota evidence rather than quoted prose, stale log text, model-unavailable or generic transport failures. Treat ambiguous evidence as hold. Both-provider exhaustion/missing capability must preserve paused work and avoid repeated switching or repeated notifications. Allow a later confirmed quota on a successor only when other provider is freshly available; never ping-pong on old evidence.
R8. Reuse existing routing, workspace CLI, provider metadata, PM startup and tests. Explain legacy claude-* labels as semantic tiers that can select Codex. Keep same-provider resume and ordinary completion behavior unchanged.
R9. Test both directions with failure controls, real temporary Git worktree/index/untracked preservation and owner CAS, duplicate/restart recovery, native identity verification, unavailable providers, source liveness, stale quota evidence and completion-race exclusions. Stub provider processes/transport at boundaries; no live worker termination or new general test harness.

## Scope

Implement, verify, review and stage source/test/doc changes through brainstorming → writing-plans → executing-plans. No live worker handoff, Slack messages, production configuration edits, deployment/restart, commit or push in this loop. Prior restart-supervisor issue and broader Vitest RPC timeout are separate; do not fold them into this change.
