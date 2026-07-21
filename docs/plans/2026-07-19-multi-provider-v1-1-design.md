# IronClaude v1.1.0 Multi-Client Commander Design

**Date:** 2026-07-19  
**Status:** Approved revised design  
**Target release:** v1.1.0

## Purpose

IronClaude v1.1.0 adds complete Commander support for Claude Code and Codex. An operator may configure Claude only, Codex only, or both. When both clients are explicitly configured for a role and the active client becomes unavailable because of an infrastructure failure, Commander automatically switches to the other client and continues the work with the original prompt, existing workspace, and source client's native conversation log.

This design supersedes the unimplemented v1.0.25 workers-only multi-provider design in:

- `docs/plans/2026-07-18-multi-provider-support-design.md`
- `docs/plans/2026-07-18-multi-provider-support.md`
- `docs/plans/2026-07-18-multi-provider-support.plan.json`

Those artifacts remain historical evidence. Their validated feature-level guidance is preserved here, but v1.1.0 covers Brain, workers, graders, helpers, Slack operations, SSH hosts, failover, and release documentation as one complete public release.

Direct Codex plugin mode already works and remains a regression baseline. Rewriting direct mode is not part of this effort.

## Operator-guidance traceability

The durable requirements source of truth is `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md`. A blind design review receives that ledger as an independent input and fails if any active requirement is omitted, weakened, contradicted, or silently deferred. Technical feasibility review is additional; it cannot substitute for fidelity to operator guidance.

| Guidance | Faithful design application |
|---|---|
| MP-001–003: native GPT-5.6 support, Luna/Terra/Sol semantic tiers, and OpenAI-capable workers | Client and model support; lifecycle adapters; acceptance criteria 1–2 |
| MP-004, MP-007, MP-010: bidirectional automatic failover for Brain and helpers; flip first and block last | Failover behavior; automatic-failover runtime flow; acceptance criteria 3, 6, and 8 |
| MP-005, MP-009, MP-C04: signal switches and exhaustion in Slack; create and pin a fresh failover message without maintaining shared message state | Failover behavior; Slack and operator interface; automatic-failover runtime flow; acceptance criteria 5 and 8 |
| MP-006: configurable provider selection for grader and other applicable helpers | Client and model support; configuration; lifecycle adapters; acceptance criterion 1 |
| MP-008 and MP-D03: immediate Slack status, global swap, per-role swap, and recovery controls | Slack and operator interface; recovery and manual override |
| MP-011–012: ChatGPT subscription through native Codex; no API key, API billing, translator, Claude alias, or LiteLLM production path | Client and model support; lifecycle adapters; security boundaries; non-goals |
| MP-013: multiple complete internal PM loops, one complete public v1.1.0 release | Documentation and Release prerequisite workflow-hardening loop and product efforts 1–6 |
| MP-014: do not stop or hand back work because of context-window concerns | Durable design/plan artifacts and uninterrupted internal-effort execution across compaction |
| MP-D01, MP-D04: separate configuration, per-role/per-tier/per-host capability, routing, health, and failure classification | Architecture; capability registry; provider router; failure classifier |
| MP-D05: route at request or spawn time without restarting Commander | Architecture; normal execution |
| MP-D06: manual swap is immediate; recovery does not silently fail back | Failover behavior; recovery and manual override; non-goals |
| MP-D07: suppress unchanged outage notices and announce verified recovery | Failure classifier; Slack interface; recovery flow |
| MP-D08: isolated contracts, end-to-end bidirectional failover, Slack, forced-limit, client-failure, SSH, and full-suite testing | Testing Strategy |
| MP-D09, MP-C05: provider shadow comparison is opt-in and off by default | Configuration; shadow comparison; acceptance criterion 10 |
| MP-D10: preserve Claude Fable; degrade Fable to Opus before cross-client Sol; keep Commander green | Client and model support; provider router; testing; acceptance criterion 11 |
| MP-C01: each SSH host may expose Claude only, Codex only, or both independently | Configuration and surprise avoidance; SSH lifecycle adapter; end-to-end tests |
| MP-C02: detection never enables Codex; automatic failover begins after dual-client configuration | Configuration and surprise avoidance |
| MP-C03: start a fresh target conversation with original prompt, native source log, workspace, and continuation instruction | Native Conversation-Log Handoff; automatic-failover flow; acceptance criterion 4 |
| MP-C06: add safe `git check-ignore` while preserving deny-first protections | Professional workflow compatibility; unit coverage; acceptance criterion 13 |
| MP-C07: README, CHANGELOG, manifests, metadata, and status must describe complete Commander support | Documentation and Release; acceptance criterion 12 |
| MP-W01–W11: requirements-visible but revision-history-blind review, one-failure convergence, mandatory brainstorming retreat for guidance/design conflict, source-verified findings, live-code-grounded plans, plan-format parity, state-preflight discipline, and exact task/session binding | Blind review fidelity; plan-construction fidelity; review-fidelity state; state transition discipline; acceptance criteria 14–20 |

## Requirements

### Client and model support

- Support Claude Code and Codex independently for Commander Brain, workers, graders, and applicable helpers.
- Use ChatGPT subscription authentication through the Codex CLI. Do not require an OpenAI API key or API billing.
- Map semantic tiers as follows:
  - Haiku to GPT-5.6 Luna
  - Sonnet to GPT-5.6 Terra
  - Opus to GPT-5.6 Sol
- Preserve Fable as a Claude-specific tier. If Fable is unavailable, degrade to Opus; the Opus request may then cross clients to Sol.
- Preserve existing Fable behavior and the complete Commander test suite.

### Configuration and surprise avoidance

- Existing installations remain Claude-only unless Codex is explicitly enabled in Commander configuration.
- Detecting an installed or authenticated client is diagnostic only. Detection never enables routing.
- Once both clients are explicitly configured for a role or host, automatic failover is active by default. No second failover toggle or operator confirmation is required.
- Both clients are optional overall. If only one is configured, Commander uses it and waits when it is unavailable.
- Local and SSH hosts may expose Claude-only, Codex-only, or both-client capability independently.
- Each Codex-capable host authenticates independently with `codex login`. Commander detects authentication state but never copies credentials.
- Shadow mode is explicitly opt-in and defaults to off.

### Failover behavior

- Fail over only for infrastructure failures: usage limits, model unavailability, expired authentication, missing executable, network or provider outage, startup timeout, or client crash.
- Ordinary task failures remain task failures and never trigger a provider switch.
- Flip first and block last. Try every configured eligible alternative once before waiting.
- Assignments are sticky per role. Recovery makes a capability eligible but does not move work back automatically.
- Manual Slack swaps take effect immediately.
- If every configured capability is exhausted, announce the condition in Slack and wait.
- Announce every automatic Brain or role-provider switch in Slack.
- Every automatic failover posts a new standalone Slack message and pins that message. Commander does not maintain, update, replace, or unpin a shared provider-status message.

### Professional workflow compatibility

- Treat `git check-ignore` as a read-only Git inspection command at every professional-mode workflow stage, alongside `git status`, `git diff`, `git log`, and `git ls-files`.
- Preserve existing deny-first protections: chaining, redirection, process substitution, and commands where `git check-ignore` is not the anchored command remain blocked.
- Keep the general non-executing allowlist, reviewing-stage allowlist, and their user-facing allowed-command messages consistent.

### Blind review fidelity

- Brainstorming produces a durable operator-requirements ledger containing original guidance, confirmed decisions, active/superseded status, and normalized acceptance requirements. The operator validates it before design completion.
- Every fresh plan reviewer receives exactly four intent artifacts: operator requirements, approved design, human plan, and machine plan. The reviewer may inspect current in-scope source and tests as technical evidence, but receives no revision-history artifact.
- Reviewers never receive author rationale, self-assessment, prior findings or verdicts, explanations of fixes, diffs, revision history, or previous reviewer identities.
- Review order is requirements→design fidelity, design→plan fidelity, then technical correctness and executability. A technically sound plan fails if it drifts from guidance.
- Findings are classified as `requirements-design`, `design-plan`, or `plan-technical`. The reviewer identifies problems only and does not rewrite requirements, design, or plan.
- Every finding cites authoritative current evidence. Before any correction, the main workflow independently verifies that evidence against the live scoped files; a finding based on a stale, unrelated, or untracked artifact is rejected rather than implemented.
- The first failed review of any verdict category triggers a holistic, workflow-private requirements/design/plan invariant audit before another blind review. This audit gate is cumulative with category-specific disposition; it is never waived by retreat.
- Any `requirements-design` conflict or apparent requirement/design infeasibility additionally requires an operator-visible retreat to brainstorming. It cannot be resolved by silently changing or weakening guidance in the plan. After the operator resolves the design conflict, the pending invariant audit must still be submitted before another blind review.
- A `design-plan` or `plan-technical` failure permits plan revision without changing requirements or design, but the first such failure still activates the same invariant-audit gate.
- The revision audit and prior findings remain durable for workflow resumption but are never included in the next reviewer's input. Each revised plan receives a brand-new blind reviewer against the same approved requirements and design.

### Plan-construction fidelity

- Plan writing begins by inspecting the current implementation, tests, schemas, and call sites that constrain each task. File names, symbols, signatures, and test seams are verified rather than inferred from design prose or earlier plans.
- Human plans specify exact observable behavior, invariants, failure cases, and TDD evidence. They do not use dead-reckoned replacement snippets as implementation authority. Any indispensable code fragment must be derived from and checked against the current source contract.
- The human and machine plans express the same normative behavior and must remain semantically identical (MP-W10): the same requirement/design coverage, task IDs, dependencies, allowed files, ordered steps and commands, tests, and expected results. Neither representation may introduce, omit, or supersede behavior present in the other.
- Planning and revision keep both representations in lockstep and run the writing-plans parity audit (requirement/design coverage, task IDs, dependencies, allowed files, ordered steps and commands, tests and expected results) before mark_plan_ready. A revision that changes behavior updates both representations coherently rather than patching one.
- Before blind review, the semantic parity audit confirms the human and machine plans agree, and live-source grounding fails plans that assert unresolved files, symbols, columns, keys, signatures, or commands. Plan facts are verified against current source, not inferred from design prose or an earlier plan.
- If investigation shows the approved design cannot be implemented faithfully, planning stops and retreats to brainstorming; the planner does not invent a workaround that changes operator intent.

### State transition discipline

- IronClaude's persisted workflow-session key is always derived from the active client's native parent/root conversation identity, never a subagent's independent identity. Claude uses the current root `session_id`. Codex hooks use payload `session_id`; Codex MCP calls use `_meta["x-codex-turn-metadata"].session_id` as the native root identity.
- Codex top-level MCP `_meta.threadId` and nested `.thread_id` identify the invoking thread. On a root call they equal root `.session_id`. On a subagent call they identify the child while `.session_id`, `.parent_thread_id`, and `.forked_from_thread_id` identify the root. Resolver validation is scope-aware: root fields must agree on the root identity, while subagent fields must preserve the expected root/child split. A child `threadId` is never used as IronClaude's persisted workflow key.
- Provider-specific identity capture happens at ingress, before generic state handling. The normalized value is passed explicitly through each read or mutation; a Claude PPID file may transport Claude identity only and can never select or substitute for a Codex thread.
- Identity is resolved and revalidated on every MCP invocation so compaction, resume, interleaved tasks, or a long-lived server cannot retain a stale binding. If a Codex invocation does not expose native root `.session_id`, or a subagent's parent/root fields disagree with it, workflow-state access fails closed; it does not create or select state under the child ID.
- `turn_id`, process ID, working directory, recency, and process-global mutable binding are never session selectors. Hooks and MCP tools never fall back between clients or to a most-recent session.
- A preflight snapshot returns its session identity. The subsequent transition must validate that identity and expected source stage atomically; ambiguous, missing, changed, or cross-session identity fails closed with a diagnostic and no mutation.
- Diagnostics are observational against live sessions. Any write/read-back health probe uses an isolated temporary record or rollback transaction and leaves session timestamps, workflow fields, artifacts, flags, and audit history unchanged.
- Before calling any workflow transition tool, a skill reads the current session state. If current and target stages match, the skill skips the transition call and continues from the confirmed state.
- Transition tools share one state-machine path rather than duplicating stage checks. Same-target requests are idempotent defensive no-ops returning `success: true`, `changed: false`, `from`, and `to`.
- A no-op does not update the session, insert an audit row, re-register artifacts, clear flags, recompute waves, or trigger hooks. Invalid different-state transitions remain errors.
- Tool metadata marks genuinely idempotent transition operations accordingly, and skill instructions require preflight instead of relying on server no-op behavior.
- This closes the current split where `validateWorkflowTransition()` accepts same-state no-ops, individual handlers bypass it, `canTransitionTo()` rejects the same state, and skills call transitions without checking current state.

## Architecture

Commander becomes provider-neutral at its routing control plane while retaining client-specific execution lifecycles.

Routing accepts a semantic role and tier rather than a provider-specific model name. It combines explicit configuration, current sticky assignment, host capability, and availability state to choose a concrete client, model, and host. A capability is tracked at the `(host, client, role, tier)` level so an outage affecting one role or model does not incorrectly disable every use of that client.

Client adapters preserve each role's existing contract:

- **Brain:** Claude retains its current SDK lifecycle. Codex uses `codex app-server` over stdio for persistent threads, turn streaming, and steering.
- **Workers:** Both clients run as interactive tmux sessions. This preserves Commander's long-lived worker lifecycle, direct IronClaude commands, and both existing spawn paths.
- **Graders and one-shot helpers:** Claude retains its current subprocess behavior. Codex uses `codex exec` with JSON Schema output where structured output is required.
- **SSH workers:** Use the same client-specific worker adapters through the existing remote execution boundary.

Client and provider identity is stored when a role or worker is spawned. Routing changes apply without restarting Commander.

## Native Conversation-Log Handoff

Cross-client continuity deliberately reuses the native logs both clients already write. IronClaude does not create a provider-neutral transcript database, translate event schemas, or attempt to resume a native session in another client.

For each active conversational role, Commander retains:

- Original prompt
- Current client and native session ID
- Current native conversation-log path

When failover is required, Commander stops the failed process and starts a brand-new conversation in the fallback client. The first prompt contains:

1. Original prompt unchanged
2. A statement that work began in another client
3. Instruction to read the supplied conversation log
4. Source conversation-log path or raw attached log
5. Current workspace path
6. Instruction to inspect existing work and continue from the latest unfinished point

Same-host failover references the readable native log directly. Cross-host failover copies the raw JSONL file unchanged through the existing SSH transport and references its destination path. The fallback model reads the log and determines how to continue. No transcript parsing or reconstruction is required.

Existing workspace changes remain in place. The continuation instruction explicitly tells the fallback client to inspect current state before acting so it does not blindly repeat mutations already performed.

If a conversation log is missing or unreadable, failover still proceeds with the original prompt and current workspace. Commander labels the handoff degraded and reports the missing log in Slack.

## Components

### Configuration

Extend current Commander configuration rather than replacing it. Existing `brain_model`, `grader_model`, `claude_path`, and Claude-only machine entries remain valid. New configuration expresses:

- Globally and per-host enabled clients
- Preferred client per role
- Semantic model-tier mappings
- Optional Claude and Codex executable paths
- Shadow mode setting, default off

Invalid or contradictory combinations fail during startup validation instead of silently selecting another client. Authentication data is never stored in Commander configuration.

### Capability registry and probes

Record explicit enablement, executable availability, authentication usability, supported role and tier, and observed health for each local or remote capability. Probes distinguish configured-but-unavailable from not configured.

### Provider router

Select a concrete client, model, and host from semantic role and tier. Preserve sticky assignments, Fable degradation, manual overrides, and no automatic failback.

### Lifecycle adapters

Encapsulate the real startup, prompting, completion, session-ID discovery, and native-log discovery behavior for each client and role. Do not force Brain, interactive workers, and graders through one universal process abstraction.

### Failure classifier

Classify verified infrastructure failures separately from ordinary task failures. Quarantine only the affected capability, suppress duplicate unchanged outage notices, and recognize recovery only from evidence appropriate to the recorded failure category. Authentication success verifies authentication only; it does not clear usage-limit, model-unavailability, network, provider-outage, or crash quarantine. A clean successful completion may recover the exact capability that completed.

### Slack and operator interface

Extend the existing `/ironclaude <command>` convention:

- `/ironclaude provider status`
- `/ironclaude provider swap codex` for a global role swap
- `/ironclaude provider swap brain claude` for a per-role swap
- `/ironclaude provider recover codex` to re-probe a client
- Optional host argument for remote status and recovery

Existing operator authorization continues to protect slash commands. `/ironclaude status` adds version, professional-mode state, current client and model per role, unavailable capabilities, and whether automatic failover is armed.

Every manual swap, wait, recovery, and operator override returns an explicit Slack confirmation. Unchanged outage notices are deduplicated, but each actual failover always creates and pins its own new standalone message.

Example compact status:

`ironclaude v1.1.0 | Professional Mode: ON | Brain: Claude/Opus | Workers: mixed | Failover: armed`

Each automatic failover notification names source and destination clients, affected role, model or tier, host, failure category, and source session or log identifier. It never includes conversation contents or credentials. Commander posts a fresh message and pins it. A pin failure is reported as an operational warning but does not undo a successful failover.

### Shadow comparison

When explicitly enabled, run the same eligible request through primary and shadow clients. Only the primary result controls workflow. Shadow output is observational comparison data and cannot affect routing, availability, task results, or production artifacts. Shadow failure never triggers failover. Coding shadows use an isolated snapshot or worktree so they cannot mutate production state.

### Review-fidelity state

Extend the existing state manager rather than introducing a second workflow store. Session/plan state records the approved requirements artifact, design artifact, plan hash, review attempt, verdict class, whether revision audit is required, and whether brainstorming retreat is required. Prior findings may remain in workflow-private durable state for resumption, but reviewer dispatch constructs its input exclusively from requirements, design, and current plans.

Every tier-up attempt is recorded, including failures. Findings retain their typed scope and authoritative evidence references in workflow-private state. The first failed review of any category sets `revision_audit_required`; no revised plan can be re-reviewed until a structured requirements/design/plan audit is submitted. A requirements/design conflict additionally sets `retreat_required`; revised-plan registration and execution remain blocked until workflow retreats to brainstorming and the operator resolves the conflict. Clearing retreat never clears the independent audit requirement. A successful review remains bound to the exact requirements, design, human-plan, and machine-plan hashes reviewed.

Workflow transition handlers use the existing state machine's prerequisite validation through one shared transition helper. A per-invocation provider-native task/session binding—not PPID reuse across clients or recency—selects the row, and the helper atomically checks the preflight session identity and expected stage before mutation. Caller preflight prevents redundant requests; server-side same-target no-ops protect against stale callers without producing state or audit mutations.

## Runtime Flow

### Normal execution

1. Receive role and semantic tier request.
2. Read explicit client configuration, sticky assignment, host capability, and availability.
3. Select client, model, and host.
4. Start or resume the role through its client-specific adapter.
5. Record native session ID and log path.
6. On clean completion, retain assignment and use result as health evidence.

### Automatic failover

1. Classify failure.
2. Return ordinary task failures unchanged.
3. Mark affected infrastructure capability unavailable.
4. Stop failed process.
5. Select next configured eligible client once.
6. Start a fresh target-client conversation with original prompt and native-log continuation instruction.
7. Update sticky assignment.
8. Post and pin a new Slack failover message.
9. If no capability remains, announce exhaustion and wait.

### Recovery and manual override

Category-appropriate evidence or a clean completion of the exact capability marks it recovered. For example, successful `codex login status` may clear authentication quarantine but never usage-limit or provider-outage quarantine. Recovery is announced but does not change sticky assignments. Manual global or per-role swaps use the same fresh-conversation/native-log handoff and take effect immediately.

### Blind plan review and revision

1. Read current workflow state; skip any transition whose target already matches.
2. Resolve the approved requirements, design, human plan, and machine plan bound to the current session.
3. Dispatch a brand-new reviewer with only those four artifacts.
4. Reviewer checks requirements→design, design→plan, then technical executability and returns typed findings with current source evidence but without fixes.
5. Main workflow independently verifies every finding against authoritative live files and rejects unsupported or out-of-scope claims.
6. Record the verified verdict against the exact requirements, design, human-plan, and machine-plan hashes.
7. On the first verified failed review of any category, set `revision_audit_required` and block another blind review until a holistic workflow-private requirements/design/plan invariant audit is submitted.
8. If any verified finding is `requirements-design`, additionally set `retreat_required`, block plan revision, and retreat to brainstorming for operator discussion. After operator-approved design correction, preserve the pending audit gate.
9. If verified findings are only `design-plan` or `plan-technical`, or after a requirements/design conflict has been resolved in brainstorming, revise both plan representations in lockstep, rerun grounding and parity checks, submit the required invariant audit, and dispatch another fresh blind reviewer without prior-review context.
10. On `SOLID` and operator approval, bind the passing review to all four artifact hashes and enter execution.

## Security and Data Boundaries

- Treat native logs as sensitive workspace artifacts.
- Reference same-host logs in place without duplication.
- Copy logs across hosts only between explicitly configured Commander SSH hosts through existing transport, with user-only permissions in Commander temporary storage.
- Never put log contents or authentication material in Slack.
- Never copy Claude or Codex authentication state between hosts.
- Leave source native logs unmodified and owned by their clients.
- Apply existing Commander temporary-file lifecycle to cross-host copies.

## Testing Strategy

Tests must prove observable behavior, not only mock router calls.

### Unit coverage

- Tier mapping, including Fable to Opus to Sol
- Per-role and per-host capability selection
- Explicit enablement and backward-compatible Claude-only defaults
- Sticky assignment and no automatic failback
- Infrastructure-versus-task failure classification
- Claude and Codex native-log discovery
- Slack switch, exhaustion, recovery, pinning, and deduplication behavior
- Shadow mode default off and isolation
- `git check-ignore` accepted as an anchored read-only command in every professional workflow stage
- `git check-ignore` rejected when combined with chaining, redirection, process substitution, or a preceding command in every stage where read-only Bash enforcement applies
- Requirements ledger registration and exact requirements/design/plan artifact binding
- Blind reviewer input excludes rationale, prior findings, fix explanations, diffs, and revision history
- Blind reviewer may inspect scoped current code and tests but cannot receive unrelated artifacts as intent or provenance
- Findings require authoritative evidence references and independent live-source verification before revision
- Typed `requirements-design`, `design-plan`, and `plan-technical` disposition gates
- First failed review of any category requires a workflow-private convergence audit before re-review; a requirements/design conflict additionally requires brainstorming retreat and preserves the audit gate after retreat
- Requirements/design conflict blocks plan revision and forces brainstorming retreat
- Plan-grounding validation rejects unresolved files, symbols, signatures, and dead-reckoned implementation contracts
- Human and machine plan parity rejects omitted, added, or contradictory task behavior, dependencies, files, tests, and acceptance conditions
- Human and machine plans stay semantically identical; a revision updates both coherently and the parity audit rejects omitted, added, or contradictory task behavior, dependencies, files, tests, and acceptance conditions
- Every transition-calling skill performs state preflight and skips same-target requests
- Claude hook and MCP paths resolve the same current native Claude `session_id`
- Codex hook `session_id` and MCP nested turn-metadata `session_id` resolve the same native root Codex task
- Root calls require top-level `threadId`, nested `thread_id`, and nested `session_id` to agree; subagent calls require nested `session_id`, `parent_thread_id`, and `forked_from_thread_id` to agree while top-level `threadId` and nested `thread_id` agree on the child
- Root and subagent events resolve the same parent/root workflow session; child `threadId` is never persisted as workflow identity
- Hook and MCP state reads resolve the same explicit task/session under interleaved Claude and Codex activity
- A transition with missing, ambiguous, changed, or mismatched preflight identity fails closed without mutating either session
- Diagnostics prove binding and persistence health without changing any live session row or durable audit history
- Direct same-target transition calls return `changed: false` without session, artifact, flag, wave, hook, or audit-log side effects
- Invalid different-state transitions remain errors

### Adapter contract coverage

Run deterministic fake `claude` and `codex` executables as real subprocesses. Assert exact client arguments, selected model, original prompt, raw log path or attachment, continuation instruction, workspace path, and session/log discovery.

### End-to-end failover coverage

Start Commander in a disposable workspace. Source client writes a native-shaped conversation log, changes workspace state, and emits a verified infrastructure failure. Assert fallback client starts, reads prior log, inspects existing work, continues from unfinished state, updates sticky routing, and posts and pins exactly one failover message. Run both Claude-to-Codex and Codex-to-Claude paths, locally and through SSH command construction.

Cover missing authentication, missing executable, startup timeout, client crash, both clients exhausted, degraded missing-log handoff, recovery without failback, manual swaps, and isolated shadow execution.

### Release validation

- Actual authenticated Claude and Codex smoke runs
- Controlled forced-limit and client-failure exercises
- Existing Fable behavior
- Complete Commander suite
- Direct Codex plugin regression suite
- No skipped, assertion-free, snapshot-only, or error-swallowing tests
- Blind-review fixtures prove that a second reviewer receives original requirements but none of the first review's findings or fix rationale
- Transition tests cover every workflow stage and every public transition tool, not only the shared validator
- Provider-contract tests feed native Claude and Codex identity envelopes through the same resolver and require the expected persisted session key
- Parent/subagent tests use captured native-shaped Codex metadata to prove root `.session_id` remains the workflow key while child `threadId` remains invocation-only
- Interleaved-session tests prove that hooks, reads, and mutations remain bound to the initiating Claude or Codex task and never select the most-recent unrelated row
- Negative tests reject Codex root-field disagreement, subagent parent/root disagreement, missing native root `session_id`, child `threadId` as workflow identity, Claude-PPID fallback during Codex calls, `turn_id` as a session key, and process-global rebinding
- Diagnostic tests snapshot live session and audit state before and after every health check and require byte-for-byte semantic equality

## Documentation and Release

v1.1.0 ships as one complete public release after multiple internal efforts. Each effort runs its own full professional-mode brainstorm, writing-plan, execution, task-boundary review, and verification loop.

Before any provider-foundation blind review resumes, complete a **prerequisite workflow-hardening PM loop** covering requirements-ledger enforcement, requirements-visible/revision-history-blind review, authoritative-source verification, one-failure convergence, live-code-grounded plan construction, human/machine plan parity, exact task/session binding, and idempotent state-transition discipline. The hardened workflow then governs every product effort:

1. Read-only `git check-ignore` guard compatibility; provider configuration, capability detection, tier mapping, and routing
2. Codex Brain, worker, grader, and helper adapters
3. Bidirectional failover using native conversation logs
4. Slack controls, status reporting, recovery, and shadow mode
5. Local and SSH integration, end-to-end testing, and release validation
6. Documentation, migration guidance, versioning, and final release audit

README updates are release-blocking and must explain:

- Full Commander support for Claude Code and Codex
- Claude-only, Codex-only, and dual-client configurations
- GPT-5.6 Luna, Terra, and Sol mappings
- Per-host `codex login` prerequisite
- Automatic failover triggers and sticky routing
- Native conversation-log continuation
- Fable to Opus to Sol behavior
- Slack status, swap, recovery, and pinned failover messages
- Shadow mode being off by default
- Direct Codex mode versus Commander support
- Local and SSH-host capability
- Version, professional-mode, and routing status display
- Requirements-visible/revision-history-blind plan review and brainstorming-retreat behavior
- Source-verified review findings, live-code-grounded plan contracts, and human/machine plan parity
- State-preflight and side-effect-free same-target transition behavior
- Exact task/session binding across hooks and MCP state tools
- Provider-native Claude and Codex root `session_id` resolution with Codex child-thread separation

CHANGELOG receives a dedicated v1.1.0 section covering features, behavior changes, configuration additions, operational prerequisites, and known limitations. Package metadata, plugin manifests, displayed status, README, and CHANGELOG must agree on `1.1.0`.

Release is blocked if documentation describes only direct Codex support, implies OpenAI API-key billing, omits automatic failover or pinned Slack signaling, retains obsolete Commander-unsupported language, or describes blind review without its requirements-ledger and retreat guarantees.

## Non-Goals

- OpenAI API keys, API billing, or LiteLLM production routing
- Claude-compatible aliases or `ANTHROPIC_BASE_URL` routing for Codex
- Provider-neutral transcript storage
- Cross-provider native-session resumption
- Automatic client enablement
- Automatic failback after recovery
- Commander support for unconfigured hosts
- Rewriting working direct Codex plugin mode
- Giving a fresh blind reviewer author rationale, prior findings, fix explanations, diffs, or revision history
- Allowing reviewers or implementers to alter operator requirements outside brainstorming
- Treating reviewer output as authority without independent verification against current scoped sources
- Treating speculative implementation snippets or one plan representation as authority over live code or the other plan representation
- Treating same-target transition no-ops as permission for skills to skip state preflight
- Selecting workflow state by recency or mutating a session other than the explicitly bound current task

## Acceptance Criteria

v1.1.0 is complete when:

1. Brain, workers, graders, and applicable helpers run through either explicitly configured client with correct tier mappings.
2. Claude-only and Codex-only configurations work independently.
3. Dual-client configuration automatically fails over in both directions on verified infrastructure failures.
4. Fallback conversation receives original prompt, native source log, continuation instruction, and current workspace.
5. Automatic failover changes sticky routing and posts and pins a new Slack message.
6. Ordinary task failures never trigger a switch.
7. Recovery never causes automatic failback.
8. Both-client exhaustion announces and waits.
9. Local and SSH paths pass end-to-end validation.
10. Shadow mode remains off by default and observational when enabled.
11. Direct Codex mode, Fable behavior, and complete Commander suite remain green.
12. README, CHANGELOG, manifests, package metadata, and status output consistently describe v1.1.0.
13. Professional mode permits safe, anchored `git check-ignore` inspection in every stage and continues to block metacharacter-based bypasses.
14. Every plan review receives the operator-approved requirements ledger, approved design, and current human/machine plans—and no prior-review or author-rationale context.
15. The first failed review of any category triggers a workflow-private convergence audit before re-review; requirements/design conflicts additionally force brainstorming retreat, and clearing retreat does not clear the audit gate.
16. Passing tier-up review remains bound to the exact requirements, design, and plan hashes reviewed.
17. Every transition-calling skill reads current state first; same-target server calls are side-effect-free `changed: false` no-ops, while invalid different-state transitions still fail.
18. Every actionable review finding cites authoritative current evidence and is independently verified before revision; unsupported or out-of-scope findings are rejected without changing artifacts.
19. Plans are grounded in inspected live code, tests, schemas, and call sites; the human and machine plans remain semantically identical (MP-W10); and the writing-plans parity audit rejects any divergence in requirement coverage, task IDs, dependencies, files, steps, tests, or acceptance conditions.
20. Under interleaved root and subagent activity across Claude and Codex, Claude hooks/MCP use the native parent/root Claude `session_id`; Codex hooks and MCP use the native root Codex `session_id`, while child `threadId` remains invocation-only. Root and subagent metadata invariants resolve the exact persisted session guarded by that client. Missing, ambiguous, mismatched, cross-client, turn-scoped, recency-based, or process-global identity fails closed without cross-session effects, while diagnostics leave live session and audit state unchanged.
