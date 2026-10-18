# v1.2.0 Completeness Loop Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: full. The loop touches guard and permission surfaces (reviewer subagent tools, the Brain gate root), cross-client hooks, model resolution across the Commander, hooks and broker, and live Commander checks. It covers roughly 15 files.

## Summary

v1.2.0 is committed locally as `f62492a` on `origin/main` (v1.1.15). It is not pushed and not tagged. On 2026-10-17 the operator approved four completeness parts to squash into 1.2.0 before tagging. Standing goal: "my goal here is to have codex and Claude be as consistent as possible", with no workflow circumvention.

Operator directives from this brainstorm, quoted:
- Scope: "A: Locked reviewer agent, B: End-review hardening, C: Brain gate-root hardening, D: Verify Commander on Codex".
- Loop structure: "Yes, this structure (Recommended)". The loop measures first, then does A, B, C and D, then the release.
- Part A: "Yes, as designed (Recommended)". The fix for the execution-subagent hijack stays on the backlog.
- Model rule, given over four answers:
  1. "We should use the latest gpt family everywhere unless overridden by config".
  2. Precedence: "Explicit config wins (Recommended)".
  3. "Same as latest Claude family everywhere unless overridden by config".
  4. "Hold up we shouldn't be doing any pinned defaults in ironclaude anymore with this release".
- No cache: "Resolve where Codex runs, else unavailable (Recommended)".
- Part B: "Yes, as designed (Recommended)". This approval was given for the family-word rule. The no-pinned-defaults directive and the no-cache answer then replaced its built-in latest set.
- Part C: "Yes, as designed (Recommended)".
- Local configs: "if we have 5.x chat gpt families configured locally anywhere, please clear those so that we instead use the latest". The measurement below found none in IronClaude's local configuration.
- Part D: the operator interrupted with the no-pinned-defaults directive before approving the D section. The structure answer already approved D's form: the operator triggers the checks and the agent reads the logs.
- Final design approval, covering the revised Part B and Part D: "Yes, write the plan (Recommended)".

## Requirements

### R0 — Measure first (no source change)

- **A1, tool allowlist.** Measure whether a plugin agent defined with `tools: Read, Grep, Glob` sees only those three tools, with no Bash, Edit, Write, Skill or MCP tools. Method: dispatch it and have it list its tools, with a positive control.
- **A2, subagent session.** Measure whether a subagent's state-manager calls bind to the parent session. Method: a plain subagent calls the read-only `get_resume_state`, and its `session_id` is compared with the parent's. Never probe with a write call.
- **Precedent.** The 2026-10-16 audit shows subagents share the parent's session tag (memory `project_subagent_skill_call_hijacks_parent_stage`).

### R1 — Locked reviewer agent (Part A)

- **The agent.** Add `worker/agents/reviewer.md`, a report-only adversarial reviewer with `tools: Read, Grep, Glob`. It has no `model` pin; callers pass `model`.
- **Callers.** `subagent_type="ironclaude:reviewer"` replaces three dispatches:
  - `Explore` in `worker/skills/advisor-fallback/SKILL.md`;
  - `general-purpose` in the plan-review dispatch of `worker/skills/executing-plans/SKILL.md`;
  - `general-purpose` in its end-review dispatch.
- **Packet contract.** Reviewers have no Bash. The orchestrator includes the diff, the test output and any other evidence in the packet, which is the Codex broker's contract.
- **Tests.** Tests pin the new text and the agent's tools line.
- **Out of scope, backlog.** Execution subagents (`general-purpose`) keep Skill and MCP tools. This is the 2026-10-16 parent-stage hijack.

### R2 — No pinned Codex models; latest family unless config overrides (Part B)

The rule mirrors Claude, whose defaults are the aliases `haiku`, `sonnet`, `opus` and `fable`.
- **Default words.** Commander config defaults for Codex models become the family words `luna`, `terra`, `sol` and `astra`. The validator accepts either a family word or a `gpt-<version>-<word>` slug, and the word must match the tier.
- **Resolving a word where Codex runs.**
  - A local role uses the newest `gpt-<version>-<word>` in this Mac's `$CODEX_HOME/models_cache.json`.
  - A remote worker's launch command resolves from that box's own `models_cache.json`.
- **No matching model.** If no model of the family is listed, Codex is unavailable for that call. Routing falls to the next client, and hooks and graders fail open as today. A clear log line names the family and the cache path.
- **Explicit slugs win.** An explicit `gpt-<version>-<word>` slug in config is used as-is, locally and remotely. This reverses the 1.2.0 local-resolver behavior, which replaced explicit slugs.
- **Remove every pinned slug** used as a default or fallback:
  - `config.py` DEFAULTS `codex.models`;
  - `provider_config.EXPECTED_MODELS["codex"]`;
  - `codex_brain_client.py`, both the default `model` parameter and `_CODEX_TIER_MODELS`;
  - `plan-validator.sh` `_newest_luna` and `_newest_terra`;
  - `advisor-review.ts` `FALLBACK_CODEX_MODELS`, with a rebuilt `dist/index.js`;
  - docstrings and docs that cite default slugs.

  Tests that assert the old defaults change to the new rule. Fixtures may still use concrete slugs.
- **Hook and broker.** With no Commander config, they use the newest installed model of the family. If the cache has none, the hook treats Codex as unavailable for that call, and the broker returns its existing structured unavailable error.
- **Required `host`.** `host` becomes a required keyword on `ProviderConfig.model_for` and `advisor_model_for`, so a caller that omits it fails rather than silently resolving locally.
- **Bundle test.** A parity test reads `worker/mcp-servers/state-manager/dist/index.js` as well as `advisor-review.ts`.
- **Local config.** No IronClaude local configuration pins `gpt-5.x`, measured 2026-10-17:
  - `commander/config/ironclaude.json` sets only `codex.enabled`;
  - `~/.claude/ironclaude-hooks-config.json` has none;
  - `~/.codex/config.toml` has `model = "gpt-6.1-sol"`, and its only `gpt-5.6` hit is a TUI notice counter.

  Nothing local needs clearing. `claudetraderbot/config/llm-providers.json` is a separate project and is out of scope.

### R3 — Brain gate root (Part C)

- **The constant.** One constant, `~/.ironclaude/state/brain-gate`, replaces `/tmp/ic/codex-brain-gate` in `brain_client.py:36` and `codex_brain_client.py:55`.
- **Creation.** The Commander creates the root with mode `0700` at Brain start.
- **Environment.** It passes the root as `IRONCLAUDE_BRAIN_GATE_ROOT`, beside `IRONCLAUDE_BRAIN_GATE_SESSION`, to both Brain clients.
- **Hooks.** `worker/hooks/brain-gated-actions.sh` and `commander/hooks/brain-hooks-canary.sh` read the env value, which defaults to the same `$HOME` path.
- **Fail closed.** The hooks fail closed (the gate blocks, the canary does not write) unless the root is a real directory, not a symlink, owned by the current user, with no group or other permission bits.
- **Tests.** Existing hook tests use a temporary root. New negative cases cover a foreign-owned root, a group- or world-writable root and a symlinked root. A Python test asserts both clients pass the same root.
- **Unchanged.** `daemon.log`, `brain.pid` and the rest of `/tmp/ic` stay where they are.

### R4 — Live verification on Codex (Part D)

After the loop deploys:
1. The operator runs `/provider brain codex` and asks the 2026-10-10 question: "what unfinished work is sitting idle right now?"
2. The operator triggers the three per-call tiering live checks still owed:
   - 1b: `send_to_worker` or `post_message`;
   - 2: worker health on Sonnet;
   - 3: `/advisor opus` on a `claude-opus` worker.
3. The agent reads `/tmp/ic/daemon.log` and the Codex rollout.

Memory shows the 2026-10-10 defect was fixed and live-verified on 2026-10-11. `daemon.log` has no recurrence after 10-10, and Codex Brain tool calls on 10-11 to 10-13 carry no error category. A pass closes the backlog entry. A failure is debugged and fixed in this loop. The operator may switch back with `/provider brain claude`.

### R4b — Commit and worktree verification (Part E, added 2026-10-17)

The operator asked: "are we in a place where we can start actually using work trees and /commit and /commit-and-push without it breaking?" Asked where to put the work, the operator answered "Fold into 1.2.0 (Recommended)".
- **Verify in this loop.** For each defect recorded in memory, run a reproduction against the 1.2.0 code and record a verdict, with the evidence, in the findings:
  - `LIVE`;
  - `FIXED`;
  - `UNVERIFIED-LIVE: <what would prove it>`;
  - `UNVERIFIED-HARNESS: <error>`.
- **Reproduction rules.** Each reproduction is read-only or uses a throwaway git repository under `/private/tmp`, with a scratch `HOME`. Never touch the operator's checkouts, never allocate or activate on real repositories, and never commit or push in the operator's repositories. Defects that need a real Codex session, the sandbox or Slack get an operator-assisted live half in the post-deploy task, and are never marked FIXED from a code trace.
- **The nine defects:**
  1. `/commit` intent-evidence skew, a silent partial commit (`project_git_intent_mint_consume_skew.md`).
  2. Codex literal `/commit` minting no intent (`project_codex_commit_intent_not_minted.md`).
  3. `/commit <message>` minting no intent (`project_backlog_commit_with_message_no_intent.md`).
  4. Codex `/commit-and-push` refusing a session with no assignment (`project_codex_commit_and_push_refuses_no_assignment.md`).
  5. The Codex sandbox unable to write a managed worktree's git index (`project_codex_sandbox_blocks_worktree_git_index.md`).
  6. Shared resources symlinking untracked source into worktrees (`project_shared_resources_hide_uncommitted_source.md`).
  7. The worktree integration target locked at activation (`project_reconcile_target_locked_at_activation.md`).
  8. A stale `base_commit` after a manual fast-forward (`project_reconcile_stale_base_misleading_sync.md`).
  9. The activate flow calling `list_active_assignments` with an empty repository path (`project_activate_list_assignments_empty_repo_path.md`).
- **Fix in a follow-up loop.** A follow-up PM loop designs fixes for the LIVE defects, the silent partial commit first, with operator approval. It also squashes into 1.2.0 before the `v1.2.0` tag. This loop changes no commit or worktree code.

### R4c — Local-box backoff (Part F, added 2026-10-17)

The operator asked: "What about having a configurable back off if the local model fails? Meaning, after a failed attempt, it doesn't try again for x minutes? Then we can set it to 15 minutes or something as a default? If it is up and running, it's fine. If it gets flaky or is down, we don't waste a lot of time then?" Asked where to put the work, the operator answered "Fold into 1.2.0 (Recommended)".
- **Failure marks the box down.** Any failed local-box attempt marks it: a connect failure, a timeout, an empty answer or invalid JSON. Failure writes a per-user marker under `~/.ironclaude/state/` holding the endpoint and `until = now + backoff`. The directory is created `0700` and owner-checked, as in R3, and the path can be overridden for tests.
- **While the marker is live** and matches the configured endpoint, hooks and the Commander skip the local box and call the Haiku-class model (Claude Haiku, or the newest Luna) directly.
- **After expiry** the next call tries the box again. Success clears the marker, and failure renews it.
- **Configuration.** `local_backoff_minutes` lives in `~/.claude/ironclaude-hooks-config.json`, defaulting to a built-in 15, so no config edit is needed. `0` disables the backoff. The plan never edits that config file, which is operator-only.
- **One mechanism.** Hooks and the Commander share one marker and one default, with a parity test. The Commander's in-process circuit breaker stays for its other callers, but only the shared marker decides whether to skip the local box. The shadow grader and session summarization are out of scope.
- **Tests.** Hook and Commander tests cover:
  - skipping during the window, with a positive control that the box is tried when there is no marker;
  - expiry;
  - clearing on success;
  - `0` disabling the backoff;
  - refusing a wrong-owner directory or one with group or other permission bits;
  - each side honoring a marker written by the other.

  Existing hook suites turn the backoff off so they never write into the real `~/.ironclaude`.
- **Amendment (2026-10-17): only box failures count.** The full Commander suite exposed a gap. `test_daemon.py::TestGradeBoundedFastLaneReadTimeout` sets a caller deadline of 0.2 s, and the fast lane's `fast_lane_grade_timeout_seconds` defaults to 5 s. Under the first version, either deadline wrote the shared marker, so a slow but healthy box was skipped for 15 minutes by every hook and by the Commander. That traffic went to Haiku, which spends Claude usage. Asked what gives engineers the best experience, the operator answered: "Proceed as suggested".
  - **What counts as a failure.** A connect failure, an HTTP error, an empty answer, invalid JSON, or a read timeout at the box's own configured read timeout.
  - **What does not count.** A read timeout caused by a caller deadline shorter than that configured timeout. It writes no marker and clears no marker.
  - **What stays the same.** The rest of R4c is unchanged: one failure skips the box for `local_backoff_minutes` (default 15), and `0` turns the backoff off.
  - **The hook's budget counts (2026-10-18).**
    - **Why it counts.** The hook's curl `--max-time` is `hook_validation_budget_seconds` (default 8). The box's `timeout_sec` (default 60) never reaches curl, so for hooks the budget is the box's real limit. The operator was asked whether a hook timeout should count and chose "A: Hook budget counts (Recommended)". The hook is therefore unchanged from Task 8.
    - **The only exemption.** A Commander per-call `read_timeout` shorter than the Commander client's configured read timeout does not count. One example is the fast lane's `fast_lane_grade_timeout_seconds` (default 5).
    - **Result.** A box too slow for hooks is skipped for 15 minutes, and a box that is only briefly slow is not locked out by a Commander fast-lane call.
  - **Tests (TDD).**
    - The existing `test_daemon.py` fast-lane test passes unchanged.
    - New cases in the hook and Commander backoff suites cover three situations: a caller-deadline timeout writes no marker; a timeout at the box's own read timeout writes one; a connect failure writes one.
  - **Docs.** The README, the CHANGELOG 1.2.0 entry and the release commit message tell an engineer running their own box (Ollama or an OpenAI-compatible server):
    - what trips the backoff;
    - what does not trip it;
    - how long it lasts;
    - how to change it or turn it off;
    - that answers go to the Haiku-class model in the meantime.
- **Amendment (2026-10-18): one skip rule.** A side review found the code breaks "only the shared marker decides" (above). The grader's box call goes `grader.py:303` → `post_generate` → `_attempt`, which consults the Commander's in-process transport breaker (`ollama_client.py:223-224`, `openai_client.py:102`). That breaker opens after 3 consecutive connection failures for 5 s, doubling on each failed probe up to 300 s (`ollama_client.py:39-41, 89-103`), and other callers (the shadow grader, the orchestrator's ollama tools) feed it. So at `local_backoff_minutes: 0` the box could still be skipped for up to 5 minutes, and another caller's failures could make the grader skip a box the marker calls healthy. Asked how these features should work together for the best engineer experience, the operator chose "One rule (Recommended)" and approved this section ("Yes, write it").
  - **The rule.** In the fast-model chain (hooks and the Commander `LocalGrader`), the shared marker is the only skip decision. The hooks have no breaker and are unchanged.
  - **Mechanism.** `OllamaClient` and `OpenAIClient` take a constructor keyword `use_breaker` (default `True`); `make_client` passes it through. `LocalGrader._build_client` builds its normal and fast-lane clients with `use_breaker=False`. With `use_breaker=False`, `_attempt` skips the `_BREAKERS.allow()` gate but still records every outcome (success, failure, slow), so heartbeat down/busy signals and other callers' breakers stay accurate.
  - **Result.** At `0` the box is tried on every call; at 15 it is skipped only while the marker is live; no other caller's failures make the grader skip it. The breaker stays an internal guard for callers with no fallback model.
  - **Tests (TDD).** For the Ollama and the OpenAI-compatible backend: open the breaker for the endpoint (3 recorded failures), set `local_backoff_minutes: 0`, and assert `LocalGrader.grade` still sends a request (fails before the change). Positive control: a default client (`use_breaker=True`) still skips the open breaker. The existing breaker suites pass unchanged.
  - **Docs.** The CHANGELOG, README and release commit paragraph state the single rule. No breaker caveat is needed.

### R4d — Drift rule (b) counts only in-progress rows (Part G, added 2026-10-17)

The drift cross-session hotfix (commit `196e469`, squashed into the local v1.2.0 as `b2c97bc`) excludes a path when another live session's same-root ledger lists it. Its Fable end review found a gap. The liveness query in `_drift_foreign_paths` (`worker/hooks/execution-drift-check.sh:443`) filters only on the other session's `workflow_stage`, so a *completed* task's ledger still counts while that session keeps executing or reviewing. In that case this session's own side-quest write to the same path is excused, which fails open. Asked what the final v1.2.0 should include, the operator answered "Loop A–F + rule (b) fix (Recommended)".
- **Fix.** The query also requires `w.status='in_progress'`.
- **Tests (TDD).** In `worker/hooks/tests/test-execution-drift-check.sh`, add a case where the other session stays `executing` but the ledger's row is `completed`. This session's write to that path still blocks. It fails before the fix. Case 46 is the positive control: an in-progress row of a live session still excludes.
- **Release note.** The CHANGELOG 1.2.0 known issue "A finished task's ledger still counts while its session is executing or reviewing" is removed. The ledger-pruning note stays.

### Corrections from plan grounding (2026-10-17)

- **Reviewer registration.** `worker/.claude-plugin/plugin.json` lists agents explicitly, so R1 registers `reviewer.md` there.
- **A1 timing.** The plugin loads from the Claude cache, so A1, the reviewer's tool-list check, runs after deploy.
- **R2 routing.** The fall-through to the next client for an unavailable Codex family goes through `provider_router.py`.
- **R2 broker.** The broker gains a structured reason, `codex-reviewer-model-unavailable`, since it had no unavailable error before.
- **Codex Brain.** The Codex Brain does not route: an uninstalled family makes the Codex Brain fail to start, with a log line naming the family. That is the "unavailable" outcome for the Brain.
- **Test fixture.** The conftest `CODEX_HOME` holds a fixture model cache, so family-word defaults resolve in tests.
- **Task order.** Full suites run before the release docs, which need their counts, and the post-deploy live checks come last. The operator deploys an untagged commit and tags `v1.2.0` only after the post-deploy checks pass.

Operator approval of Parts E and F and these corrections, including the Codex Brain's loud start failure: "Approve as written (Recommended)".

### R5 — Release

- Add this loop's changes to the 1.2.0 CHANGELOG and README entries.
- Re-squash onto `origin/main` as one v1.2.0 commit.
- Tag `v1.2.0`, then redeploy: Claude cache, `make codex-plugin-release`, `make deploy-hooks`, Commander and ChatGPT restarts.
- No push.

### R6 — Tests

- TDD for every code change, with RED before GREEN.
- Full Commander, hooks and state-manager suites.
- After deploy, live probes:
  - a Codex grader on the resolved newest model;
  - the reviewer agent's tool list;
  - a Brain gate marker written under the new root.

## Non-goals

- Fixing the execution-subagent Skill/MCP hijack (backlog).
- Moving the rest of `/tmp/ic`.
- `claudetraderbot` configuration.
- `unified_exec`, which cannot be turned off.

## Claude/Codex differences that remain

- **Model resolution.** Claude resolves family aliases inside its CLI. IronClaude resolves Codex family words from `models_cache.json`, because the Codex CLI's own alias support is unmeasured.
- **Codex `functions.exec`.** It stays on in Codex children.
- **Reviewers.** Claude's reviewer reads the repo (Read, Grep, Glob). Codex's broker reviewer works from the packet only.
