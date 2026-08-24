# Direct-Human Professional-Mode Deactivation Availability Requirements

> **Created:** 2026-08-18
> **Status:** Corrected requirements approved by operator on 2026-08-18

## R1. Availability

### R1.1
A verified direct-human `UserPromptSubmit` deactivation request MUST set the exact
existing provider-root session to `professional_mode='off'` even when an invoking
plugin-cache root is stale, deleted, unreadable, symlinked, superseded, or version-
skewed.

### R1.2
No repository, worktree, branch, index, receipt, assignment, plugin-cache, or
runtime-observation failure MAY veto R1.1. Missing evidence MUST be recorded as
uncertainty for re-entry.

### R1.3
The operator MUST NOT need to restart an application, use another session,
repair a path, edit state, or run Git to deactivate professional mode.

## R2. Human-only authority

### R2.1
Only an exact direct-human `UserPromptSubmit` event MAY authorize deactivation.

### R2.2
Subagents, automation, malformed or unknown event sources, agent-authored prompts,
Commander messages, general MCP calls, and environment-variable possession MUST NOT
authorize deactivation.

### R2.3
Existing exact slash, dollar, and standalone Markdown skill forms MUST remain
supported. Embedded prose, code spans, suffixes, wrong links, and case variants
MUST remain non-authoritative.

### R2.4
The provider's trusted `UserPromptSubmit` transport, direct-user source, and exact
command form are the authority boundary. State-manager MUST allocate event identity
server-side after that check. General MCP/state setters, Commander, agent-authored
prompts, subagents, automation, and caller-supplied event IDs MUST NOT authorize
deactivation. Hostile same-OS-user cryptographic resistance is out of scope.

## R3. Provider-session identity

### R3.1
IronClaude MUST maintain separate durable records for provider-root session/client
identity, stable transition-runtime binding, and append-only current,
superseded, alias, missing, unreadable, cross-client, unrelated, and digest-mismatch
installation observations.

### R3.2
Disposable plugin-root paths MUST be diagnostic observations, not deactivation
authority or executable-code selectors.

### R3.3
Forged, unrelated, cross-session, and cross-client root hints MUST NOT change the
bound session, client, or runtime. Resolver disagreement MUST be audited without
blocking a verified direct-human transition.

### R3.4
An exact existing session accepted through trusted direct-user `UserPromptSubmit`
with a missing or corrupt runtime binding MUST use the emergency closure and record
uncertainty. A missing session MUST reject without mutation. Missing durable client
attachment MUST be recorded as uncertainty and MUST NOT be inferred from runtime or
cache evidence or veto deactivation.

### R3.5
Pre-feature existing sessions SHOULD acquire durable client/runtime attachment
through replay-safe provider-native startup. Migration MUST not create sessions or
infer missing/conflicting client identity; attachment failure MUST not veto trusted
human deactivation.

## R4. Stable transition runtime

### R4.1
The deactivation transition MUST execute through a version-independent,
state-manager-owned stable runtime whose manifest and bundle are verified before
use.

### R4.2
Stable runtime deployment MUST use atomic generations and retain the previous
verified generation until live-session references and execution leases have
expired.

### R4.3
Transition execution MUST NOT import code from an unverified or merely newest
plugin-cache directory.

### R4.4
After exact direct-human provenance and existing-session identity are authenticated,
failure to resolve, verify, lease, load, or execute a stable runtime MUST invoke a
self-contained state-manager-owned emergency closure embedded in the trusted hook.
That fallback MUST enforce the same epoch, mode, audit, idempotency, preservation,
and rollback invariants and MUST NOT be callable by agents, Commander, or general
MCP clients.

### R4.5
Immutable generation bytes MUST be fully written, hashed, fsynced, and self-tested
before a `prepared` database record is created. One database transaction MUST
publish the provider binding and committed runtime head. Filesystem `current` is a
reconstructible cache updated only afterward; startup/resolution MUST reconcile it
from committed database state. Partial or abandoned prepared generations MUST NOT
execute.

### R4.6
Current and last-known-good generations MUST have durable database representation.
Publication changes current; only a successful verified transition promotes
last-known-good. Failure MUST NOT promote it, and cleanup MUST preserve both.

## R5. Atomic state transition

### R5.1
One transaction MUST close or idempotently reuse exactly one authority epoch and
set professional mode off.

### R5.2
The transaction MUST preserve workflow stage, plan, wave tasks, reviews, receipts,
index, working bytes, commits, refs, assignments, and Commander state.

### R5.3
The closed epoch and audit MUST bind the exact session and client and include
runtime-resolution evidence and every unavailable observation.

### R5.4
Transaction failure MUST roll back both epoch and mode changes and MUST NOT mutate
unrelated state.

## R6. Idempotency and concurrency

### R6.1
State-manager MUST allocate a delivery identity for each accepted trusted-hook
invocation. Repeated delivery MUST return the same semantic closed authority result
without duplicate epoch or mode effects; distinct deliveries MAY retain distinct
forensic delivery records while mode closure remains state-idempotent.

### R6.2
Concurrent installation or cache replacement MUST select one fully verified stable
runtime generation, atomically lease it for the transition lifetime, and MUST NOT
observe a partial generation.

### R6.3
Concurrent sessions MUST remain isolated; one session MUST NOT close, consume, or
rewrite another session's epoch or binding.

### R6.4
Generation cleanup MUST preserve the current generation, last-known-good
generation, and every generation with an active unexpired lease. Lease release MUST
be idempotent, crash expiry MUST be bounded, and supersession MUST NOT invalidate an
in-flight transition.

### R6.5
Lease acquisition MUST issue a monotonically increasing fencing token. Heartbeat,
transition commit, promotion, release, and cleanup MUST compare that token. After
expiry/reacquisition, a paused stale holder MUST be unable to mutate state or use
deleted runtime bytes.

## R7. Reporting

### R7.1
User-facing reporting MUST distinguish verified off, verified not-off, and status
unknown after an unavailable verification step.

### R7.2
A later verification error MUST NOT be reported as proof that the hook transition
failed.

### R7.3
Diagnostics MUST identify stale, missing, symlinked, cross-client, forged, or
manifest-mismatched evidence without asking the operator to perform recovery work.

## R8. Behavioral verification

### R8.1
Real isolated tests MUST reproduce the exact `trusted provider root is invalid`
failure before implementation and prove it no longer blocks deactivation afterward.

### R8.2
The matrix MUST cover Claude and Codex normal installation, Codex cachebuster
upgrade, Claude same-version replacement, stale/deleted root, canonical and
symlinked root, restart, and concurrent sessions.

### R8.3
Every positive case MUST prove mode off, exactly one closed epoch, exact client and
session audit, and byte-for-byte preservation of workflow and Git evidence.

### R8.4
Negative cases MUST prove no deactivation from subagent, automation, malformed
event, embedded prompt, forged session, nonexistent session, cross-session, or
agent/MCP invocation.

### R8.5
Mutation-sensitive tests MUST prove manifest, bundle, transaction, audit,
idempotency, and concurrency controls can fail when production protections are
removed.

### R8.6
Fresh provider-native Claude and Codex acceptance MUST execute once per case with
no silent retry and no operator terminal handback.

### R8.7
Real failure-injection tests MUST prove every stable-runtime observation and
execution failure reaches the emergency closure only after direct-human and session
authentication, and that the fallback preserves all non-mode state.

### R8.8
Real concurrency tests MUST hold a transition lease while publishing and cleaning
up a successor generation, prove the leased runtime remains usable, and prove safe
idempotent release and bounded crash expiry.

### R8.9
Negative tests MUST exercise agent, Commander, MCP, subagent, automation,
malformed-source, caller-supplied event-ID, and near-miss prompt paths and prove none
reach trusted-hook event allocation or mutation.

### R8.10
Crash-injection tests MUST cover generation creation, every file and manifest write,
fsync, self-test, prepared insertion, binding/head commit, pointer update, startup
reconciliation, and cleanup. Resolution MUST select only committed digest-valid
bytes.

### R8.11
The same backend-neutral conformance vectors MUST execute against primary and
generated emergency implementations and compare returned results, database state,
audit fields, idempotent replay, rollback, and every preserved state surface.

## R9. Release and scope

### R9.1
The one cachebuster and every build MUST occur before constructing and validating
the exact runtime oracle. Focused suites, provider parity, plugin validation,
version checks, and the full repository suite MUST then pass against those final
bytes before release.

### R9.2
Reinstallation MUST be the final source/plugin/runtime mutation. Fresh runtimes
MUST then prove installed roots, manifests, bundle hashes, session identity, and
direct-human behavior. Only read-only runtime proof, workflow evidence, and task
review may follow installation.

### R9.3
Wave 25 MUST NOT implement Commander startup repair, PM re-entry classification,
receipt recovery, worktree lifecycle behavior, commit, or push.

### R9.4
Wave 25 documentation MUST record, without implementing, the deferred operational
rule that an operator-approved bounded action MUST NOT trigger another permission
request unless destination, sensitivity, destructive effect, or scope materially
changes.
