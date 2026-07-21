# Workflow Transition Idempotency — Wave 1 Validation

**Status:** runtime-active: verified

**Restart verification:** `2026-07-20T17:17:28Z`

**Native Codex task:** `019f7742-abd8-7c62-af7b-fe07189f1ffd`

**Installed version:** `1.0.24+codex.20260720171005`

**Installed cache:** `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.0.24+codex.20260720171005`

## Authoritative artifacts

- Requirements: `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md`
- Design: `docs/plans/2026-07-20-workflow-transition-idempotency-design.md`
- Human plan: `docs/plans/2026-07-20-workflow-transition-idempotency.md`
- Machine plan: `docs/plans/2026-07-20-workflow-transition-idempotency.plan.json`
- Roadmap: `docs/plans/2026-07-20-v1-1-overall-roadmap.md`
- Final blind-plan verdict: `SOLID`, bound to plan SHA-256 `a22f4df4894ede8e08eb0749b843481c7ddc485c974e710dd69ce578c62e2d64`

## Root cause and implemented contract

Explicit workflow transitions were split across caller instructions, hooks, and independent state-manager handlers. Callers could request the stage already active, while handlers could still run prerequisites, callbacks, timestamp updates, artifact changes, flags, wave changes, or audits. This made same-target transitions observable and allowed caller/state divergence.

Wave 1 establishes one contract:

1. Every direct skill caller reads `get_resume_state`, verifies the provider-native root identity, compares `workflow_stage` with its target, skips an equal target, calls a different target once, and requires `changed: true`. A retry requires a fresh read.
2. The state manager performs equality first and returns exact `changed: false` before any validation, guard, callback, artifact, flag, wave, timestamp, or audit side effect.
3. Changed transitions remain transactional: the stage update and its single audit commit together or roll back together.
4. Compound operations retain their domain behavior. `create_plan` reload is not skipped merely because the stage is unchanged.
5. Hook paths suppress same-target and `changed: false` effects. Malformed logger envelopes remain silent and cannot mutate workflow state.

`systematic-debugging` is the real caller topology for entry into `debugging`: its prompt reaches `worker/hooks/skill-state-bridge.sh`. No workflow skill directly calls `mark_debugging`, and Wave 1 does not introduce a duplicate transition path.

## TDD and regression evidence

| Gate | Evidence |
|---|---|
| State-manager transition RED | 24 focused tests: 18 failed, 6 passed before implementation. |
| State-manager transition GREEN | 24/24 focused tests passed. |
| State-manager related GREEN | 7/7 files, 85/85 tests passed, including MP-W11 identity, dispatch, diagnostics, review gates, tier-up policy, and plan reload behavior. |
| State-manager complete GREEN | 11/11 files, 108/108 tests passed. |
| TypeScript/build | `npx tsc --noEmit` passed from the state-manager package; build and esbuild passed; `dist/index.js` is 537.3 kB. |
| Commander caller-contract RED | 4 focused failures before caller preflight was added. |
| Hook-contract RED | 21 cases passed and 10 failed before same-target/logger suppression was added. |
| Commander caller-contract GREEN | 13/13 focused workflow-skill tests passed. |
| Hook-contract GREEN | 44/44 cases passed, including same-target snapshots, changed paths, all four supported response envelopes, malformed envelopes, and positive controls. |
| Version full-suite RED | `test_version_sources_match` failed because raw `1.0.24+codex.20260720062826` was compared with release version `1.0.24`; 1 failed and 1,989 passed. |
| Version focused RED | 10/10 new helper/contract cases failed before implementation. |
| Version focused GREEN | 10/10 passed before cachebusting and 10/10 passed after final cachebuster `1.0.24+codex.20260720171005`. |
| Commander complete GREEN | 1,999/1,999 tests passed in 1,120.74 seconds. |
| Whitespace gates | `git diff --check` and `git diff --cached --check` passed. |
| Plugin validation | `validate_plugin.py worker` passed before and after the final cachebuster. |

The version-consistency repair compares the Codex manifest release prefix with exact Commander, Claude-plugin, marketplace, and Makefile release declarations. Only one supported `+codex.<lowercase-alphanumeric-or-hyphen-token>` suffix is accepted; empty, repeated, non-Codex, uppercase, underscore, dot, and otherwise malformed build metadata are rejected.

## Installed-cache parity

The configured `ironclaude` marketplace resolved to `/Users/roberthyatt/Code/ironclaude`. `codex plugin add ironclaude@ironclaude` installed the final cache path above. Every required pair existed and matched byte-for-byte:

| Relative path | SHA-256 |
|---|---|
| `.codex-plugin/plugin.json` | `6ba4a5248958bd535bae51e16815b9e4a6182267a3d4a9e8703e2ea1510a5757` |
| `mcp-servers/state-manager/src/state-machine.ts` | `74c3de40709f667f1780519999717fcbb73c8248550152434cb31ed1a6834c98` |
| `mcp-servers/state-manager/src/tools/write-tools.ts` | `ced37cb38aa588388f2d5646542193203e1e38979cd47a09a77a5de9c9cce121` |
| `mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts` | `b756c766918cde416398034f2397880becb0908b6b58a0b4701b1d6b07a67337` |
| `mcp-servers/state-manager/dist/index.js` | `005ee1be78d499a9704c9ca0545a929800eee618191b6bebde8db63d128c0272` |
| `skills/brainstorming/SKILL.md` | `5c977ec756c783c3ddb96afea556864cde4d7f1054cd9328db33be01cf57ca3f` |
| `skills/writing-plans/SKILL.md` | `50ac684695ba795f7bd216e3ad96709d202abf8aa02e9c287220cd2f20bdd7eb` |
| `skills/executing-plans/SKILL.md` | `8d8afdd2953c66191370c5c45deb08d4d8b57bc0275e772633c8f3904b2a9548` |
| `hooks/skill-state-bridge.sh` | `652837cbe36d44713bece8652d0e770a95dc776c2aa46616c0e429b1823f1e7b` |
| `hooks/mcp-state-logger.sh` | `fb15e6d17e6f37c699bfb99c7296353387cbc875960342df992f40398559113f` |
| `hooks/tests/test-workflow-transition-idempotency.sh` | `d6486adcf8a21915ad2b63f3ccfc772f75050b5f1a5b03425f3bc985cfc55e1d` |

## Active requirements disposition

Wave 1 implements MP-W08 and protects completed MP-W11. All other active requirements remain release-blocking; installation of Wave 1 does not mark them complete.

| Requirement set | Named PM loop | Status |
|---|---|---|
| MP-W01, MP-W02, MP-W03, MP-W04, MP-W05, MP-W06, MP-W07, MP-W09, MP-W10 | Roadmap Wave 0 — Review convergence prerequisite | Implemented and hot-deployed; must remain green. |
| MP-W08 | Roadmap Wave 1 — Workflow transition idempotency | Complete: source, tests, installed-cache parity, restart continuity, and live diagnostics verified. |
| MP-W11 | Native provider-root session identity prerequisite | Implemented prerequisite; protected by 11/11 identity tests plus dispatch and diagnostics regression coverage. |
| MP-001, MP-002, MP-011, MP-012, MP-D04, MP-C01 | Roadmap Wave 2 — Provider foundations | Pending; release-blocking. |
| MP-003, MP-006, MP-D01, MP-D05, MP-D10 | Roadmap Wave 3 — Native role routing | Pending; release-blocking. |
| MP-004, MP-007, MP-009, MP-010, MP-C02, MP-C03 | Roadmap Wave 4 — Automatic failover and conversation continuity | Pending; release-blocking. |
| MP-005, MP-008, MP-D02, MP-D03, MP-D06, MP-D07, MP-C04 | Roadmap Wave 5 — Slack controls and signaling | Pending; release-blocking. |
| MP-D08, MP-D09, MP-C05 | Roadmap Wave 6 — Shadow mode and remote resilience | Pending; release-blocking. |
| MP-C06 | Roadmap Wave 10 — Read-only Git guard hardening | Pending; release-blocking. |
| MP-013, MP-014, MP-C07 | Roadmap Wave 12 — Release integration and documentation | Pending; release-blocking. |

This table assigns every active product requirement from MP-001 through MP-C07 to a named future PM loop. Roadmap Waves 7–9 and 11 separately retain their queued Codex compatibility defects; Wave 13 remains the final all-requirements verification and history gate.

## Queued adjacent defect

`worker/mcp-servers/state-manager/src/types.ts` and `PlanJsonSchema` omit the `requirements_file` property required by the shipped writing/executing skills. Runtime currently accepts and preserves the unknown field. This verified schema/type mismatch remains queued for Roadmap Wave 11 (`create_plan` call-shape hardening); it was not expanded into Wave 1.

## Post-restart live activation

Codex was fully quit and relaunched, then this same task `019f7742-abd8-7c62-af7b-fe07189f1ffd` was reopened.

- `get_resume_state` returned the same native session ID, professional mode `on`, plan `Workflow Transition Idempotency`, workflow stage `executing`, Tasks 1–3 `review_passed`, and Task 4 `pending` before claim.
- `codex plugin list` reported `ironclaude@ironclaude` installed and enabled at `1.0.24+codex.20260720171005` from marketplace `/Users/roberthyatt/Code/ironclaude`.
- The deterministic post-restart comparison reproduced all 11 hashes in the installed-cache parity table above.
- Reloaded state-manager diagnostics passed 11/11 checks. Both identity checks resolved `client=codex`, `session=019f7742-abd8-7c62-af7b-fe07189f1ffd`, and `source=codex_meta`; no fallback, collision, cross-client identity, stale port, or stale token was reported.
- A read-only live snapshot before and after a second diagnostic run was identical: session `updated_at` remained `2026-07-20 17:15:13`, session fields were unchanged, audit count remained 162, and maximum audit ID remained 56810. Diagnostic write/audit probes therefore rolled back without live-state mutation.

Runtime-active verification is complete. Wave 1 may close after its Task 4 review gate passes.

## Repository history state

No commit was created. No push was performed.
