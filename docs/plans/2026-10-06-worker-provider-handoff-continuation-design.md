# Worker provider handoff native validation continuation design

Created: 2026-10-06
Status: Design complete
Loop: full
Scope: hold; continue original automatic bidirectional same-worktree worker handoff. Source, tests and documents only. No commit, push, deploy, application restart or live worker operation.

Requirements: docs/plans/2026-10-05-worker-provider-handoff-requirements.md
Original design: docs/plans/2026-10-05-worker-provider-handoff-design.md

## Native availability

Predicted answer: yes (delegated).
A provider-native usage error can occur before any cumulative token count. Unknown baseline is not zero. Recovery must use a fresh bound native turn with genuine output, clean completion and matching applicable quota scope when a cumulative delta cannot be established. A stale heartbeat, earlier cumulative output, quoted message, abort or failed turn is not recovery. Persist enough bounded reader state to distinguish those cases across chunks and restarts. Current positive recovery supersedes earlier exhaustion before any source termination.

## Controller validation

Predicted answer: yes (delegated).
Use trusted parser progress from the current daemon/registry or an independently validated controller scan. Bind it to source client, native UUID, generation, worktree and file incarnation; validate the append boundary and source snapshot before reuse. A caller's asserted state is not validation. Consume new suffix records to current EOF; budget exhaustion persists progress and remains unknown. Paused and pre-stop validation follows the same ownership and provenance rules. No repeated scan from byte zero when valid trusted progress exists.

## Preserved boundaries

Predicted answer: yes (delegated).
Keep original private workspace hold/CAS, single controller effect owner, source process death proof, fresh read-only target identity and own-session resume, PM continuity/review attribution/design-registration CAS, operator pause and human Git intent boundaries. Historical execution artifacts and reviewed source remain intact. Continue unfinished daemon task through a focused plan whose allowlist includes native parser paths. Source correctness remains distinct from installed runtime and live operation evidence.

## Verification

Predicted answer: yes (delegated).
Use real JSONL and SQLite state, controlled parser budgets and separate connections. Test first-error recovery and stale heartbeat/turn/scope/failure negatives. Test trusted EOF-cache reuse and incremental controller validation across budgets and restart; forged proposed state must not stop a worker. Retain all normal real Git/storage cases separately from fault scenarios. Run original required Python checks, focused workspace checks and compiler; report the known broader Vitest RPC limitation honestly.

## Next workflow

Predicted answer: yes, writing-plans and execution (delegated). Requirements and scope remain unchanged.
