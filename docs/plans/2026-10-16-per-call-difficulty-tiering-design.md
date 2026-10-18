# Per-Call Difficulty Tiering Design

> **Created:** 2026-10-16
> **Status:** Design Complete

Loop: full. It changes hook enforcement code and Commander grader routing.

## Summary

This builds on the staged Haiku-class fast-model fallback (`docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md`, Tasks 1-8 staged). It replaces an abandoned "Opus-first for five checks" correction that was never executed.

Operator directives, 2026-10-16, verbatim:
- "Um I didn't intend for haiku to replace opus. HOLD UP let's fix this"
- "HOLD UP, I don't want to overuse opus when times haiku should be better suited for the task...please help me understand why we aren't doign this?!"
- "First the question is this: what is the difficulty level of these tasks? if the difficulty level is appropriate for haiku, we use haiku class, if the difficulty level is sonnet, we use sonnet class, if the difficulty level is opus, we use opus class. THAT is the question we should be asking. I was tracking that most (all?) of the calls to ollama were haiku level tasks, so I was advocating for haiku class solutions. Let's do this: call out all the times that the harness needs to have an LLM do something and call out the DIFFICULTY level of each task and also call out HOW we are currently handling them today"
- After the inventory and difficulty table: "Fold tiering in now".
- On Sonnet-level calls: "Straight to Sonnet (Recommended)". Sonnet-level calls always use the Sonnet class (Claude Sonnet / Codex Terra), whatever the backend setting. The local box only serves Haiku-level calls.
- On backup: "One tier up, gates only (Recommended)". Fail-closed gates step up one tier on failure (Haiku→Sonnet, Sonnet→Opus) instead of jumping to Opus. Fail-open classifiers and hook checks keep failing open after their first-choice tier.
- Design approved: "Yes, write it up".

"Haiku class" means Claude Haiku in Claude sessions and the newest Codex Luna in Codex. "Sonnet class" means Claude Sonnet and the newest Codex Terra. Fable and Astra are never used here (the Top-tier rule).

## Requirements

**T1 — Tier per call.** The call-site IDs come from the operator-reviewed inventory of 2026-10-16.

| ID | Call | Site | Tier |
|---|---|---|---|
| H1 | Stop-hook bypass check | `get-back-to-work-impl.sh` run_check "bypass" | Haiku |
| H2 | Stop-hook continuation judge | run_check "continuation" | **Sonnet** |
| H3-H6 | rigor / prediction / COA / evidence | run_check | Haiku |
| H7 | task-completion validator | `task-completion-validator.sh:97` | Haiku |
| C1 | Brain-message validator | `main.py` `_validate_brain_message` | Haiku |
| C2 | awaiting-operator capture | `main.py` `_grade_bounded` | Haiku |
| C3 | prompt detection | `main.py` `_detect_worker_prompt` | Haiku |
| C4 | permission-seeking | `brain_client.py` | Haiku |
| C5/C6 | spawn screening (single / batch) | `orchestrator_mcp.py` spawn_worker / spawn_workers | Haiku pre-filter; **escalation (low/medium confidence or failure) → Sonnet** (was Opus) |
| C7 | approve_plan | `orchestrator_mcp.py` | Opus (unchanged) |
| C8 | send_to_worker | `orchestrator_mcp.py` | Haiku |
| C9 | send_keys_to_worker | `orchestrator_mcp.py` | Haiku |
| C10 | post_message | `orchestrator_mcp.py` | Haiku |
| C11 | worker health | `orchestrator_mcp.py` evaluate_worker_health | **Sonnet** |
| C12 | kill_worker completion check | `orchestrator_mcp.py` | Opus (unchanged) |
| C13 | session summaries | `orchestrator_mcp.py` list_claude_sessions | Haiku |
| W1 | episodic summarizer | `summarizer.ts` | Haiku (unchanged) |

Out of scope:
- C14, the escalation grader, is configured per call by this design.
- C15, the shadow grader, is unchanged and stays inert under `haiku`.
- C16, the Brain, stays Sonnet.
- C17, worker advisors, is queued separately.
- W2, the Codex advisor broker, is unchanged.

**T2 — Routing per tier.**
- **Haiku-level calls:** the configured local box (`ollama`/`openai`) first. Then Haiku class, as already built: the Haiku-class fallback chain.
- **Sonnet-level calls:** never the local box; Sonnet class directly.

**T3 — Backup.**
- Fail-closed gates step up one tier when their first choice cannot answer: an infrastructure error, timeout, or unparseable or missing fields.
  - C8, C9 and C10 go Haiku → **Sonnet**, through the Commander grader asked for the Sonnet tier, then grade F (blocked) as today. They no longer escalate to Opus.
  - C5/C6 escalate to **Sonnet**, whether Haiku is not "high" confidence or unavailable. The Sonnet escalation's own failure is F, as today.
  - C7 and C12 stay Opus, and their failure stays F. Nothing steps up from Opus (Fable/Astra only on operator request).
- Fail-open classifiers and hook checks (H1-H7, C1-C4, C11, C13) fail open after their first-choice tier. No step-up.

**T4 — Mechanism.**
- **Commander, `fast_model.py`:**
  - `call_fast_model(..., tier="haiku")`. `fast_model_clients(commander_config, tier)` maps the tier through each client's `models.<tier>` (provider config; e.g. claude `sonnet`, codex Terra).
  - The Claude argv uses `--model <that model>`.
  - The default tier `haiku` keeps today's behavior byte-for-byte.
- **Commander, `grader.py`:**
  - `LocalGrader.grade(..., tier="haiku")`. `tier="sonnet"` skips the local box and calls `call_fast_model(tier="sonnet")`.
  - The Sonnet path's failure returns `infrastructure_error`, with no further step-up inside LocalGrader.
- **Commander, `orchestrator_mcp.py`:**
  - The escalation grader `_call_grader` gains a way to request a specific tier (e.g. a `tier`/`model` argument resolved through the existing ProviderRouter). C5/C6 and the C8-C10 backups request Sonnet; C7 and C12 keep the default Opus tier.
  - C11 calls `grade(..., tier="sonnet")`.
- **Hooks, `plan-validator.sh`:**
  - `call_validation_llm` gains an optional third argument `tier` (default `haiku`). `sonnet` skips the local box and runs the lean Claude child with `--model sonnet`, or on Codex the newest installed `gpt-*-terra` (same isolation flags, schema handling and session-free settings as the Haiku path).
  - `get-back-to-work-impl.sh` passes `sonnet` for the continuation check only.
- **Sonnet cap.** The Sonnet tier gets its own cap, a config key `sonnet_model_timeout_seconds`. Its default is set from measurement: the plan measures lean Sonnet single and six-concurrent latency first (as Task 1 of the fallback loop did), then computes the default as `max(30, ceil(2 × p95 concurrent))`, with a STOP if the p95 exceeds 60 s.
- **The Opus-first correction is dropped entirely.** No `fast_fallback` parameter.

**T5 — Tests.** Each call site's tier and backup is asserted:
- the hook continuation check calls with tier `sonnet` and the Claude argv carries `--model sonnet`;
- C11 uses Sonnet and never builds a local client;
- C5/C6 escalate to Sonnet, not Opus, on low confidence and on Haiku failure;
- the C8-C10 backup asks the grader for Sonnet;
- C7 and C12 still use Opus;
- the default `haiku` behavior is unchanged (positive controls).

**T6 — Docs.** `worker/config-schema/llm-backend.md` and `README.md` gain the per-call tier table and the backup rule.

**T8 — Worker advisors** (folded in by the operator, 2026-10-16).
- **Directives.**
  - "we should also look at if we need to tier up to fable quite as often for tier up reviews, given that opus 5.5 is pretty darn close to fable 5.1?"
  - Review tier-ups were already fixed by commit `3eb2584` (Top-tier rule).
  - For the remaining Fable default, the Commander worker advisors, the operator chose "Fold into tiering now".
  - Operator, verbatim: "Please note that "opus" is an opus class model, which includes sol if we are running codex".
- **Rule.**
  - Opus-class workers get an **Opus-class** advisor: Claude Opus, or Codex Sol. This replaces the default `advisor_models: {"claude-opus": "fable"}` (`commander/src/ironclaude/config.py:76`).
  - Sonnet-class workers keep an Opus-class advisor.
  - The top tier (Fable / Astra) is used only on explicit operator request, through config.
  - The Codex advisor mapping follows the same class rule: no Sol→Astra.
- **Where it is resolved.** All advisor resolution sites follow the rule: `orchestrator_mcp.py` `_advisor_model_for` and its uses (:5320, :6012), `main.py:4107`, and `provider_config.advisor_model_for`.
- **README.** The worker/advisor table and the `advisor.advisor_models` default (`README.md:280-289`, `:711-713`) are updated.
- **Tests.** Opus-class workers resolve to an Opus-class advisor by default on both clients, and an explicit config override to Fable is still honored.

Every tier in this design is a CLASS resolved per client:
- haiku class = Claude Haiku / newest Codex Luna;
- sonnet class = Claude Sonnet / newest Codex Terra;
- opus class = Claude Opus / newest Codex Sol.

**T10 — Advisor Fallback directive.**
- **Decision.** The operator chose "Include it now" on 2026-10-16. This option was previously excluded as option D. It is the last place Fable/Astra is the default.
- **Rule.** When the `advisor` tool is unavailable, the substitute reviewer is a fresh **Opus-class** subagent: Claude Opus via `Agent model=opus`, or on Codex `run_codex_advisor_review` with `review_tier: "same"` from a Sol session. "One-up" applies only below the Opus class: Luna→Terra and Terra→Sol on Codex, Haiku→Sonnet and Sonnet→Opus on Claude. The top tier (Fable/Astra) is used only on explicit operator request.
- **Surfaces, all parallel and kept identical** per the one-IronClaude rule:
  - `CLAUDE.md` (repo) and `.claude/rules/behavioral.md` (Advisor Fallback directive);
  - `worker/skills/advisor-fallback/SKILL.md`;
  - the activation canonical bodies in `worker/skills/activate-professional-mode/SKILL.md`;
  - `commander/src/ironclaude/templates/worker_claude_md.md` and `worker_agents.md`;
  - any other copy found by enumeration.

  Their parity and pinning tests are updated to the new text. Examples include `test_advisor_fallback_directive.py`, `test_activation_client_parity.py`, `test_worker_claude_md_template.py` and `test_instruction_surface_parity.py`; enumerate them from source.
- **No conflict.** The per-spawn advisor instruction for Codex workers (T8) and the templates must agree after this change.
- **Live proof (post-deploy).** The operator chose "Process sampling now". The five Commander gates log from the orchestrator subprocess, which has no log file, so the live check samples running processes during a real gate call (the Opus grader's `--system-prompt-file` versus a Haiku-class fast-model child). Durable per-call logging comes in the usage-ledger loop.

**T7 — Fix the default-label test in this loop.** The operator chose "Deploy after this loop" (2026-10-16), because until deploy the running Commander's five gates call Opus on every use. This loop's commit must be green, so the previously carried fix moves here:
- In `commander/tests/test_notifications.py:1045-1047`, rename `test_resolve_degraded_backend_label_missing_config_defaults_ollama` to `…_defaults_haiku` and expect "Haiku".
- Update the stale docstring at `commander/src/ironclaude/notifications.py:279` ("missing config → ollama default → 'Ollama'").

The full commander suite must then be fully green.

**T9 — Deploy right after this loop.** After commit: release, deploy-hooks, Claude-cache sync, rebuild of the installed episodic plugin, then restart the Commander and ChatGPT. Then run live checks in the Commander log:
- the five gates answer via Haiku, with no Opus escalation;
- worker health runs on Sonnet;
- a `claude-opus` worker's advisor is Opus, not Fable;
- the hook Stop checks are graded, and no session files appear.

**Non-goals:**
- no change to the Brain model, the shadow grader, episodic routing, or the hook chain's local-first behavior for Haiku-level checks (worker advisors are in scope via T8);
- no new escalation for fail-open classifiers.
