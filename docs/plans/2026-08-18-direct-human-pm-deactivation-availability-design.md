# Direct-Human Professional-Mode Deactivation Availability Design

> **Created:** 2026-08-18
> **Status:** Corrected design approved by operator on 2026-08-18
> **Scope mode:** hold

## Summary

A verified direct-human request to deactivate professional mode is IronClaude's
emergency authority boundary. It must remain available even when a plugin cache
directory has been replaced, deleted, symlinked, or version-skewed. The current
hook proves the human event first, then blocks the transition if
`PLUGIN_ROOT` or `CLAUDE_PLUGIN_ROOT` no longer names an existing canonical
cache directory. That converts disposable installation metadata into a veto over
the human operator.

Wave 25 preserves the proven authority boundary: the provider's trusted
`UserPromptSubmit` transport, direct-user source, and exact command form. A stable,
version-independent state-manager bridge performs the existing atomic epoch closure
and mode change. Plugin-cache paths remain forensic runtime evidence, but stale path
spelling and stable-runtime observation failures can never block an accepted human
request.

## Confirmed Root Cause

`worker/hooks/state-activator.sh` recognizes exact slash, dollar, and Codex
Markdown skill forms and separately rejects subagent, automation, malformed, and
non-`UserPromptSubmit` events. After that proof succeeds,
`resolve_trusted_mode_client` requires every supplied plugin root to exist,
canonicalize, and reside under one exact provider cache layout. A missing or
superseded directory returns `trusted provider root is invalid` before
`closeProfessionalModeFromTrustedHook` runs.

The focused deactivation suite currently encodes that failure as correct:
malformed or missing root evidence preserves `professional_mode='on'` and creates
no closed epoch. The observed `d2bc8c6d-db12-4253-99c3-7a2094011d5a` failure is
therefore an enforced source behavior, not an intermittent state-manager error.

## Chosen Approach

### Stable deactivation bridge with provider-session binding

IronClaude will deploy a small state-manager-owned deactivation runtime into the
version-independent stable hook directory. The runtime includes an immutable
manifest and the exact transition implementation it executes. Plugin installation,
session initialization, and provider-native state-manager startup refresh separate
durable records for:

- provider-root session and provider client (`claude` or `codex`);
- provider-native session/client identity when available;
- stable transition-runtime generation and manifest digest;
- current and last-known-good runtime heads;
- current, superseded, aliased, missing, unreadable, and mismatched installation
  observations;
- installation manifest, version, and bundle digests;
- creation source and timestamp.

The trusted `UserPromptSubmit` hook supplies the prompt, direct-user source,
provider-root session, repository observations, and any available plugin-root hints
to the stable bridge. The bridge allocates an event ID and fencing token server-side,
records root disagreement or missing evidence, and invokes the transition
transaction. An old cache path may identify a superseded installation for audit,
but it is never the authority source and never selects executable code.

The trusted hook also contains a generated, self-contained emergency closure
implementation owned by state-manager. It is not a public SQL or MCP surface. It
runs only after the trusted host hook supplies the exact direct-human event and
existing session identity and only when the stable runtime binding cannot be
verified or executed. It applies the same epoch, mode, audit, idempotency,
preservation, and rollback conformance vectors as the primary bridge, so runtime
availability cannot become a veto over human deactivation.

## Rejected Approaches

### Loosen the current path matcher

Accepting any lexical `.codex` or `.claude` path would remove the immediate error,
but would permit forged or cross-client environment values to choose audit identity
and transition code. It does not provide a trustworthy current-runtime binding.

### Select the newest cache directory

Directory ordering does not authenticate a provider, session, manifest, or bundle.
It can select a partially installed, stale, or cross-client runtime.

### Add cryptographic same-OS process attestation

Claude and Codex do not expose a signed human-event capability to plugins. The
approved authority model treats trusted host hook transport as the human boundary,
not hostile same-OS-user resistance. Inventing PID, secret, or process-ancestry
attestation would add an unprovable platform contract while leaving the actual
post-authority cache-root veto unfixed.

### General direct SQLite fallback

A raw hook-side mode flip omits authority-epoch closure and recreates the missing-
epoch re-entry failure. The only fallback is the generated state-manager-owned
emergency transaction embedded in the trusted direct-human hook; it is not callable
by an agent, Commander, or a general MCP client.

### Restart or operator path repair

Restarting can replace or corrupt PM state and is explicitly part of Wave 26.
The operator must never be asked to repair cache paths, edit state, or deactivate
from another session.

## Architecture

### 1. Direct-human provenance boundary

Exact prompt matching remains unchanged. Deactivation enters the bridge only from
the provider's trusted `UserPromptSubmit` hook when the invocation source is a
direct user. The bridge generates a monotonic event identity after that transport
check. Missing, subagent, automation, malformed, or unknown provenance remains
non-authoritative. No MCP tool, general state setter, skill prose, agent-generated
prompt, Commander message, or caller-supplied event ID can substitute for the
trusted hook event. Same-OS cryptographic resistance is explicitly outside this
authority model.

### 2. Crash-consistent stable transition runtime

The stable hook deployment includes the transition bridge, its manifest, and all
runtime material required to execute it without importing code from a disposable
plugin-cache directory. Immutable generations use digest-derived names. Deployment
writes, hashes, fsyncs, and self-tests all bytes before inserting a `prepared`
generation. One SQLite transaction publishes the provider binding and changes the
generation to `committed`; only afterward does deployment update the filesystem
`current` pointer as a reconstructible cache. Startup and resolution reconcile that
pointer from committed database state. Abandoned prepared generations are
recoverable garbage and can never be selected.

The bridge verifies its manifest and bundle digest before use. Before executing, it
atomically acquires a generation lease and monotonically increasing fencing token
bound to session, client, generation, binding, and server-created event. Every
heartbeat and final transaction compares the active token. Cleanup preserves the
current generation, last-known-good generation, and every generation with an active
unexpired current fence. A paused holder whose lease expires cannot resume after a
new token is issued. Release is idempotent; crashed executions expire only after a
bounded lease interval.
Agent-facing guards continue blocking writes to the stable runtime, binding ledger,
and lease ledger.

### 3. Provider identity, runtime binding, and installation observations

Authoritative deactivation identity is the existing provider-root session supplied
by trusted `UserPromptSubmit`; durable provider client identity is used when
available and otherwise recorded as explicit uncertainty. Runtime bindings and installation observations are
separate availability/evidence records. A current binding supplies stable runtime
identity; a separate append-only observation ledger records plugin roots and bytes:

- a matching current root confirms the binding;
- a matching superseded root records cache replacement;
- a legitimate canonical or symlink alias records its canonical target;
- a missing, unreadable, or deleted former root records uncertainty;
- a forged, cross-client, or unrelated root records disagreement and cannot alter
  the bound client, session, or executable runtime.

Resolver disagreement does not veto deactivation once direct-human provenance and
the trusted human event and existing session are authenticated. Missing or corrupt
runtime binding routes to emergency closure and becomes closed-epoch audit context.
A missing session rejects without mutation. Pre-feature sessions receive durable
client/runtime attachment through provider-native startup when possible; attachment
failure cannot veto deactivation and client uncertainty is never reconstructed from
cache paths.

### 4. Atomic transition

The bridge calls one state-manager transaction. It:

1. authenticates the existing session and provider-session binding;
2. records repository, installation, and resolver evidence, including uncertainty;
3. closes or idempotently reuses exactly one authority epoch;
4. sets `professional_mode='off'`;
5. preserves workflow stage, plan, tasks, receipts, index, working bytes, refs,
   assignments, and Commander state;
6. writes an exact client/session/runtime audit entry.

If the session does not exist or the event is not direct-human, no transition
occurs. Repository observation failure cannot abort the transaction; unavailable
fields are recorded as uncertainty.

If the stable runtime cannot be resolved, verified, leased, loaded, or executed,
the trusted direct-human hook invokes its generated emergency closure transaction.
That fallback validates the same existing session, uses the server-created event ID,
closes or reuses exactly one epoch, sets mode off,
records degraded-runtime evidence, and rolls back mode and epoch together on
failure. It cannot create a session, alter a plan, or perform Git or worktree
operations.

### 5. Idempotency and concurrency

State-manager allocates one delivery identity per accepted trusted-hook invocation.
Repeated delivery returns the same semantic closed epoch and does not duplicate
mode or epoch authority; distinct deliveries may retain distinct forensic delivery
records. A concurrent cache installation may advance the stable
runtime generation, but the transition retains its fenced generation until
execution completes. Supersession and cleanup cannot remove an in-flight runtime,
and a stale holder cannot commit after fence replacement. A concurrent session
cannot consume or rewrite another session's identity, binding, observation, or
lease.

### 6. Result reporting

The deactivation skill retains three outcomes:

- verified off;
- verified not off;
- transition status unknown because later verification was unavailable.

Verification failure is not reported as proof that the transition failed. Resolver
diagnostics are surfaced without asking the operator to restart, edit paths, run
Git, or try another session.

## Failure Handling

- **Stale or deleted cache root:** deactivate through the stable session binding;
  record the stale root and replacement generation.
- **Symlinked root:** authenticate the registered alias and canonical target;
  unregistered aliases and escapes are diagnostic disagreement and cannot select
  code or block the human transition.
- **Cross-client or forged environment:** ignore it as authority, retain forensic
  evidence, and use the registered exact-session client.
- **Agent, Commander, MCP, subagent, or automation request:** it does not arrive as
  the trusted direct-user `UserPromptSubmit` event; reject before event allocation,
  lease, epoch, audit, or mode mutation.
- **Missing runtime binding with authenticated provider identity:** use the
  emergency closure and record uncertainty.
- **Missing durable client attachment:** record explicit client uncertainty and
  deactivate the exact existing session; never reconstruct client from cache hints.
- **Missing repository evidence:** record uncertainty and deactivate.
- **Missing session:** fail without creating a session or epoch.
- **Stable runtime verification or execution failure:** invoke the generated
  trusted-human emergency closure and audit the degraded runtime evidence. Never
  ask for operator-side state or path repair.
- **Concurrent generation supersession:** retain the leased generation through
  completion; cleanup skips current, last-known-good, and actively leased
  generations.
- **Abandoned execution lease:** expire it after the bounded lease interval, issue a
  higher fencing token before reuse, and reject any resumed stale holder while
  preserving current and last-known-good generations.
- **Publication crash:** reconcile the filesystem pointer from the latest committed
  database head; prepared or partial generations never execute.
- **Transaction failure:** roll back mode and epoch together; preserve all other
  state and report the exact server failure.

## Testing Strategy

Tests use isolated state databases, real temporary Git repositories, and installed-
runtime fixtures.

1. Replace the current stale-root rejection expectation with a direct-human
   availability matrix.
2. Reproduce the exact `trusted provider root is invalid` path and prove off mode,
   one closed epoch, exact session/client audit, and unchanged workflow/Git state.
3. Cover normal install, Codex cachebuster replacement, Claude same-version byte
   replacement, deleted former root, canonical root, legitimate symlink, restart,
   and concurrent direct sessions.
4. Prove subagent, automation, malformed event, agent-authored prompt, nonexistent
   session, forged session, and cross-session attempts cannot deactivate.
5. Inject manifest, bundle, audit, and transaction failures to prove compensation,
   idempotency, and no partial mode/epoch state.
6. Exercise agent, Commander, MCP, subagent, automation, malformed-source, and
   near-miss prompt paths; prove none reach trusted-hook event allocation, lease,
   epoch, audit, or mode mutation.
7. Prove every stable-runtime resolution and execution failure reaches the
   generated emergency closure after human/session authentication, while forged,
   agent, Commander, and cross-session calls cannot reach it.
8. Inject crashes after every generation write, fsync, self-test, prepared insert,
   committed transaction, and pointer update; reconciliation selects only complete
   committed bytes.
9. Race generation supersession and cleanup against a fenced transition; prove an
   in-use generation remains executable and a resumed expired holder cannot commit.
10. Run the same deactivation conformance vectors against primary and generated
   fallback implementations and compare complete database/result/preservation state.
11. Run fresh Claude and Codex provider-native acceptance without retries. Each
   direct-human invocation must deactivate without operator terminal work.
12. Apply the one cachebuster and rebuild first, then construct and verify the exact
   runtime oracle, run focused and full repository suites against those final
   bytes, and run provider acceptance. Reinstall remains the final runtime mutation,
   followed only by read-only fresh-runtime proof and review evidence.

## Out of Scope

- Commander startup mode/epoch overwrite (Wave 26)
- PM re-entry classification and false conflicts (Wave 24)
- General receipt recovery (Wave 19)
- Worktree lifecycle automation
- Agent or Commander authority to deactivate professional mode
- Commit or push
