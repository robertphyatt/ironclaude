# IronClaude v1.1.0 Overall Roadmap

**Date:** 2026-07-20  
**Release objective:** One complete v1.1.0 release with native Claude/Codex Commander parity, explicit configuration, bidirectional automatic failover, durable operator-visible behavior, and no surprise routing.  
**Requirements authority:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md`

## Operating Rules

1. Every implementation wave is its own complete brainstorm → write-plans → execute-plans PM loop.
2. Operator guidance remains authoritative. Each loop updates the requirements ledger only through operator-visible brainstorming and explicit approval.
3. A blind plan review receives only the approved requirements, design, human plan, and machine plan. It verifies requirements → design, design → plan, then technical executability.
4. First verified `HAS-ISSUES` result triggers one holistic invariant audit. Requirements/design conflicts retreat to brainstorming; plan-only defects produce one coherent human/machine plan regeneration and a fresh reviewer.
5. Reviewer findings are evidence, not authority. Source verification precedes any correction.
6. Each wave lands focused tests and full-suite evidence. No later wave may weaken an earlier acceptance gate.
7. Do not push until the operator explicitly directs it. Final history is squashed only after every release wave and final audit pass.

## Wave Map

| Wave | PM loop | Requirements / queued item | Outcome and release gate |
|---|---|---|---|
| 0 | Review convergence prerequisite | MP-W01–MP-W07, MP-W09, MP-W10 | **Source-complete and hot-deployed.** Requirements-aware blind packet, first-failure holistic convergence, source verification, coherent plan regeneration, exact verdict gate, focused/full tests, and installed-cache parity. Full process restart plus verified same-task activation is the process boundary; replacement-task recovery is conditional. |
| 1 | Workflow transition idempotency | MP-W08 | Inventory every state-transition caller and handler. Callers preflight current state and skip same-target transitions. State manager returns side-effect-free `changed: false` for defensive same-target requests. Focused transition/audit tests and full regression pass. |
| 1R | Codex runtime activation and same-task restart | MP-W12 | Capture provider-specific manifest and bundle identity once at MCP startup. After an IronClaude self-update, apply cachebuster before final build, reinstall, fully restart Codex, reopen the same native task, verify complete runtime fingerprint and installed-cache hashes, then require a normal different-stage transition to return `changed:true`. Missing/mismatched evidence fails closed; a replacement task is recovery-only. |
| 2 | Provider foundations | MP-001, MP-002, MP-011, MP-012, MP-D04, MP-C01 | Define explicit host/client/role/tier capability configuration; native Codex subscription authentication; Luna/Terra/Sol tier mapping; independent Claude-only, Codex-only, or dual-client SSH hosts. No API keys, API billing, translation layer, or Claude aliases. |
| 3 | Native role routing | MP-003, MP-006, MP-D01, MP-D05, MP-D10 | Route Brain, workers, graders, advisors, and applicable helpers at request/spawn time. Preserve native Fable behavior and degrade Fable → Opus before cross-client Sol. Verify local and SSH execution for each configured role/tier. |
| 4 | Automatic failover and conversation continuity | MP-004, MP-007, MP-009, MP-010, MP-C02, MP-C03 | Explicitly configured dual-client roles automatically fail over in both directions for verified infrastructure unavailability. Flip each eligible alternative once before waiting. Start a new native destination conversation with original prompt, raw source-client conversation log, workspace, and continuation instruction. Keep assignments sticky; never silently fail back. |
| 5 | Slack controls and signaling | MP-005, MP-008, MP-D02, MP-D03, MP-D06, MP-D07, MP-C04 | Add status, global swap, per-role swap, recovery, outage/exhaustion, and verified-recovery feedback. Every failover creates a new standalone Slack message and pins it; no shared message tracking, updating, replacing, or unpinning. Suppress duplicate unchanged outage noise. |
| 6 | Shadow mode and remote resilience | MP-D08, MP-D09, MP-C05 | Add opt-in observational shadow comparison with default off and no influence on routing or artifacts. Exercise forced limits, client failures, both-exhausted waiting, recovery, remote/SSH capability combinations, and end-to-end failover. |
| 7 | Codex professional-mode UX and hook compatibility | Queued Codex deactivation defect and Codex status-parity requirement | Teach hooks to recognize Codex `$ironclaude:` skill-link prompts as well as slash commands. Make documented session-variable fallback provider-aware (`CODEX_THREAD_ID` versus Claude identity). Verify activate/deactivate audit events through both syntaxes without weakening human-only deactivation. Expose clear IronClaude version, professional-mode on/off state, workflow status, and native task identity in the Codex-supported status surface. |
| 8 | Codex episodic-memory completeness | Queued memory validation | Prove Codex conversations are captured completely enough for `remembering-conversations`: root task identity, user/assistant/tool events, compaction continuity, concurrent-task isolation, and retrieval. Repair only evidence-backed gaps and add regression/operational validation. |
| 9 | Required-skill availability and safe fallback policy | Queued commit-protocol defect | Determine why Codex reported the required commit-protocol skill unavailable. Ensure required workflow skills are packaged/discoverable. Prohibit an agent from inventing an unauthorized substitute workflow when a mandatory skill is absent; produce a clear blocking/recovery path instead. |
| 10 | Read-only Git guard hardening | MP-C06 | Add anchored plain `git check-ignore` to the read-only Git allowlist while preserving protections against chaining, redirection, process substitution, and preceding-command bypasses at every professional stage. |
| 11 | MCP `create_plan` call-shape hardening | Queued `create_plan` defect | Prevent the missing `plan_json` wrapper failure through callable-schema guidance, skill examples, and focused dispatch/contract tests for `create_plan({ plan_json: <PlanJson> })`. Never invent a retry protocol that bypasses the hard gate. |
| 12 | Release integration and documentation | MP-013, MP-014, MP-C07 | Integrate all completed waves; update README, CHANGELOG, plugin metadata, manifests, version, configuration/migration guidance, status output, and direct-versus-Commander support language for v1.1.0. Confirm durable cross-compaction continuation and no surprise defaults. |
| 13 | Release verification and history | All active requirements, especially MP-D08 | Run requirement-by-requirement acceptance audit, focused suites, full Commander/state-manager/plugin suites, local install/hash verification, and adversarial final review. Any verified defect creates a separately scoped remediation PM wave before this gate is rerun. Squash all release work into the v1.1.0 commit only after the release is complete; do not push until explicit operator direction. |

## Dependency Sequence

```text
Wave 0 review convergence
  -> Wave 1 transition idempotency
  -> Wave 1R runtime activation and same-task restart
  -> Wave 2 provider foundations
  -> Wave 3 native role routing
  -> Wave 4 failover and continuity
  -> Wave 5 Slack control/signaling
  -> Wave 6 shadow and remote resilience

Waves 7-11 are bounded corrective PM loops. They may run after Wave 1 when their
dependencies are available, but each must complete before Wave 12 integration.

Waves 2-11 complete
  -> Wave 12 release integration/docs
  -> Wave 13 final verification/squash
```

## Cross-Wave Release Gates

- **Configuration safety:** Detection never enables routing. Dual-client failover activates automatically only after the operator explicitly configures both eligible clients for the role.
- **Identity safety:** Preserve MP-W11 provider-native root identity and interleaved-task isolation in every hook, MCP, provider, SSH, memory, and failover change.
- **Continuity:** Cross-client handoff uses raw native conversation history plus continuation context; no provider-neutral transcript subsystem.
- **Operator visibility:** Manual switches, automatic failover, exhaustion, waiting, and verified recovery are explicit in Slack.
- **No silent degradation:** Both-client exhaustion waits visibly; missing mandatory workflow capability blocks with an actionable error rather than an invented fallback.
- **No surprise behavior:** Shadow mode defaults off; recovery does not silently fail back; Fable semantics remain native; provider selection is explicit and sticky.
- **Evidence:** Each PM loop records focused regression results, affected full-suite results, built artifacts, and installed-cache parity when hot-deployed.

## Immediate Next Action

After an IronClaude self-update, fully quit and relaunch Codex, reopen the same
native task, verify the provider-specific runtime fingerprint against the
intended installed cache, and require one valid different-stage transition to
return `changed:true`. Only use a replacement task if that verification fails.
Then continue with the next named roadmap wave as its own PM loop; do not combine
provider implementation or queued corrective items across loops.
