# IronClaude v1.1.0 Operator Requirements Ledger

**Date:** 2026-07-19  
**Status:** Operator validated  
**Purpose:** Durable, reviewer-visible source of operator intent for blind design and plan review.

## Review boundary

A blind reviewer receives this requirements ledger, the approved design, and the human and machine plans. The reviewer does not receive author rationale, prior review findings or verdicts, explanations of fixes, diffs, revision history, or previous reviewer identities.

Requirements may change only through operator-visible brainstorming and explicit operator approval. A reviewer may identify a conflict or infeasibility but has no authority to weaken, reinterpret, silently defer, or replace a requirement.

## Active product requirements

| ID | Original guidance or confirmed decision | Normalized acceptance requirement |
|---|---|---|
| MP-001 | Add native OpenAI GPT-5.6 support to IronClaude. | IronClaude v1.1.0 provides native Codex-backed GPT-5.6 support. |
| MP-002 | Luna corresponds to Haiku, Terra to Sonnet, and Sol to Opus. | Semantic tier mapping is Haiku→Luna, Sonnet→Terra, and Opus→Sol. |
| MP-003 | Workers must be able to use OpenAI models. | Commander workers operate through either explicitly configured client. |
| MP-004 | Brain must switch Claude→Codex or Codex→Claude when the active client reaches a usage limit. | Brain failover is automatic and bidirectional for verified infrastructure unavailability. |
| MP-005 | Every automatic Brain-provider switch must be explicitly announced in Slack. | Automatic role switches are visible in Slack. |
| MP-006 | Helpers, explicitly including grader, require configurable provider selection. | Brain, workers, graders, advisors, and applicable external-client helpers support configured client selection. |
| MP-007 | Helpers must fail over bidirectionally when both clients are configured and the current client reaches a usage limit. | Every routed role uses the same configured bidirectional failover policy. |
| MP-008 | Slack slash commands must provide explicit, immediate Claude↔Codex switching. | Authorized global and per-role manual swaps take effect immediately. |
| MP-009 | If both clients are exhausted, announce this in Slack and wait. | Commander never silently abandons work or loops when no configured capability remains. |
| MP-010 | “Flip first, block last.” | Commander tries each configured eligible alternative once before waiting. |
| MP-011 | No OpenAI API key and no API billing. | Codex authentication uses the operator's ChatGPT subscription only. |
| MP-012 | Interface with GPT-5.6 by using Codex instead of Claude Code; do not route Claude Code through an OpenAI translator. | Production OpenAI execution uses native Codex lifecycles, not LiteLLM, API translation, Claude aliases, or `ANTHROPIC_BASE_URL`. |
| MP-013 | Use multiple complete professional-mode efforts but ship one complete release. | v1.1.0 is one externally complete release built through multiple internal brainstorm→plan→execute loops. |
| MP-014 | Do not stop or hand work back because of context-window concerns. | Durable workflow state supports uninterrupted continuation across compaction. |
| MP-D01 | Availability is per role and tier, not one global provider bit. | Capability and health are scoped by host, client, role, and tier; sticky client assignment is per role. |
| MP-D02 | Provider switches, waits, recovery, and operator overrides receive Slack output. | Every operator-visible state transition produces clear Slack feedback. |
| MP-D03 | Slack controls include provider status, global swap, per-role swap, and recovery. | All four control surfaces ship in v1.1.0. |
| MP-D04 | Configuration, state, routing, health, and failure classification are separately testable concerns. | Commander uses explicit contracts rather than one coupled provider subsystem. |
| MP-D05 | Routing happens at request or spawn time without restarting Commander. | The next unit of work observes a successful switch immediately. |
| MP-D06 | Manual swaps are immediate; recovery does not silently move roles back. | Assignments remain sticky until failure or explicit operator action. |
| MP-D07 | Suppress duplicate unchanged outage notices and announce verified recovery. | Unchanged outage noise is deduplicated; category-appropriate recovery is announced without automatic failback. |
| MP-D08 | Test isolated contracts, end-to-end failover, Slack, forced limits, client failures, SSH, and the full suite. | Release evidence covers unit, adapter, integration, live failure, remote, and regression behavior. |
| MP-D09 | Provider shadow comparison is opt-in. | Shadow mode defaults off, remains observational, and cannot affect production routing or artifacts. |
| MP-D10 | Preserve Fable behavior and keep Commander green. | Claude Fable remains native; unavailable Fable degrades to Opus before any cross-client Sol fallback. |
| MP-C01 | SSH hosts may expose Claude only, Codex only, or both independently. | No host is required to install both clients; every Codex-capable host authenticates independently. |
| MP-C02 | Automatic failover should happen after it is configured; no surprise behavior. | Detection never enables routing, while explicitly configured dual-client roles arm failover without a second toggle. |
| MP-C03 | Reuse as much prior conversation as possible by starting a new conversation with the original prompt and native conversation log. | Cross-client handoff uses the source client's raw native log, current workspace, and a continuation instruction; no provider-neutral transcript is introduced. |
| MP-C04 | If failover happens, create a new Slack message and pin it; nothing shared needs tracking. | Every failover creates and pins one standalone message; Commander does not update, replace, unpin, or maintain a shared status message. |
| MP-C05 | Shadow mode is off by default. | Missing configuration is equivalent to `shadow_mode: false`. |
| MP-C06 | Add `git check-ignore` to the safe read-only Git whitelist under the Boy Scout rule. | Anchored plain `git check-ignore` works at every professional stage while chaining, redirection, process substitution, and preceding-command bypasses remain blocked wherever read-only enforcement applies. |
| MP-C07 | README and CHANGELOG must describe full v1.1.0 Commander support, not only direct Codex mode. | Documentation, status, manifests, metadata, and release notes consistently describe complete Claude/Codex Commander support and prerequisites. |

## Active workflow-fidelity requirements

| ID | Original guidance or confirmed decision | Normalized acceptance requirement |
|---|---|---|
| MP-W01 | The blind review exists to ensure operator guidance is faithfully applied to design and design is faithfully applied to plan. | Reviewer evaluates requirements→design, design→plan, and technical executability in that order. |
| MP-W02 | Reviews must remain blind so they do not validate their own earlier plan or fixes. | Every review uses a fresh reviewer with no author rationale, prior findings, fix explanations, diff, or revision history. |
| MP-W03 | Original requirements should be part of the review. | This operator-approved ledger is mandatory reviewer input alongside design and plans. |
| MP-W04 | If plan has problems, fix them while remaining aligned with guidance. | Plan-only defects trigger holistic revision and a fresh blind review. |
| MP-W05 | If guidance cannot work, retreat to brainstorming and discuss it. | Any requirements/design conflict or apparent infeasibility blocks plan revision and requires operator-visible brainstorming retreat. |
| MP-W06 | Apply convergence after one failed plan review. | First failed review triggers a full requirements/design/plan invariant audit before another plan may be reviewed. |
| MP-W07 | Make this a formal IronClaude change, not session-only behavior. | Shipped workflow skills and focused regression tests enforce the review contract, while the existing tier-up review records and plan-hash gate provide durable workflow state; no new review-lifecycle MCP subsystem is required. |
| MP-W08 | Before trying to transition state, understand the current state and transition only when the target differs. | Every workflow caller performs a state preflight and skips same-target calls; the state manager defensively returns a side-effect-free `changed: false` no-op if stale code still requests the current state. |
| MP-W09 | Reviewer output is evidence, not authority; verify findings against live files before changing anything. | Every finding identifies authoritative current evidence and is independently source-verified before revision; unrelated, stale, or untracked artifacts cannot redefine scope or justify a correction. |
| MP-W10 | Stop revision churn caused by speculative plan code and mismatched human/machine plans. | Plans are grounded in inspected live code and express exact behavioral and TDD contracts; speculative replacement snippets are not treated as source truth, and human and machine plans must remain semantically identical. |
| MP-W11 | Use the correct native thread/session ID for Claude and Codex or the state machine breaks. | Claude state ingress uses the current native parent/root Claude session ID. Codex state ingress uses Codex's native root `session_id`, not a subagent's child `threadId`; root and child metadata are validated according to invocation scope. Hooks and MCP tools normalize that provider-native root identity into the same IronClaude session key, revalidate it on every call, reject missing, ambiguous, or mismatched identity, never guess or cross-fallback between clients, isolate interleaved tasks, and keep diagnostics from mutating live session state. |
| MP-W12 | After an IronClaude Codex self-update, fix and continue the current native task instead of requiring a replacement task. | Fully quit and relaunch Codex, reopen the same native task, and verify unchanged provider-native root identity, provider-active startup manifest/bundle fingerprint against the intended installed cache, and a normal different-stage transition returning `changed:true` before the next PM loop. A new task is recovery-only if same-task verification fails; installation output, `codex plugin list`, filesystem parity, compaction, or a shipped compatibility-cache copy cannot substitute for loaded-runtime proof. |

## Operator-approved implementation sequence

All requirements above remain active release requirements for v1.1.0. “Active” does not mean that every internal PM loop implements every release requirement. MP-013 requires multiple complete brainstorm→plan→execute loops, and the operator explicitly ordered the anti-flailing workflow hot-deploy before the remaining provider work.

| Requirement set | Current-loop disposition | Required traceability |
|---|---|---|
| MP-W01 through MP-W07, MP-W09, and MP-W10 | Implement in the anti-flailing prerequisite loop. | Current design section, current plan task, observable acceptance condition, and current test. |
| MP-W08 | Implement in the immediately following workflow-transition PM loop. | Current source inventory of transition callers/handlers, caller preflight, defensive same-target `changed: false`, and focused tests. |
| MP-W11 | Completed prerequisite retained in the v1.1.0 working tree; protect from regression in this loop. | Current source evidence and existing session-identity/dispatch tests. |
| MP-W12 | Implement in the Wave 1R runtime-activation remediation loop before provider foundations. | Startup-captured provider-specific fingerprint, same-task full-restart protocol, focused anti-theatre tests, installed-cache parity, and live `changed:true` evidence. |
| MP-001 through MP-C07 | Implement in the immediately following v1.1.0 PM loops after the anti-flailing hot-deploy is live. | Named future loop and release-blocking status now; those later loops must add their own current design, plan, acceptance, and test mappings before execution. |

A revision audit covers the complete ledger. It fails when a current-loop requirement lacks current design/plan/test traceability, when a completed prerequisite regresses, when a future-loop requirement is silently weakened, omitted from the durable queue, or falsely claimed complete, or when any disposition differs from this operator-approved sequence. Explicit future-loop disposition is sequencing, not requirement deferral or scope removal. No release may be marked complete until every release-active requirement has implementation evidence.

## Rejected and superseded approaches

| ID | Rejected approach |
|---|---|
| MP-R01 | OpenAI API-key or API-billing integration. |
| MP-R02 | LiteLLM or any OpenAI HTTP API as the production Commander path. |
| MP-R03 | Launching GPT models through Claude Code aliases or `ANTHROPIC_BASE_URL`. |
| MP-R04 | One giant PM loop for all v1.1.0 work. |
| MP-R05 | Pausing or checkpointing because context is large. |
| MP-R06 | Provider-neutral transcript storage or cross-provider native-session resumption. |
| MP-R07 | Giving a fresh blind reviewer prior findings, fix rationale, diffs, or revision history. |
| MP-R08 | Letting a reviewer or implementer change operator guidance to make a plan easier. |
| MP-R09 | Treating a reviewer finding as self-validating without checking its cited current source and scope. |
| MP-R10 | Dead-reckoning implementation snippets or allowing Markdown and JSON plans to prescribe different behavior. |
| MP-R11 | Falling back to a process-global or most-recent session when a hook or state tool cannot bind the current task explicitly. |

## Validation rule

The operator validates this ledger before the design can return to `design_ready`. Later requirement changes append or supersede explicit IDs through another brainstorming approval; they are never inferred from reviewer feedback.
