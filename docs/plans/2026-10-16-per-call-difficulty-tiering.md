# Per-Call Difficulty Tiering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Every harness LLM call runs at the model class its difficulty needs. Haiku-level calls keep today's chain (local box, then the Haiku class). Sonnet-level calls (the Stop-hook continuation judge, Commander worker health) skip the local box and go straight to the Sonnet class. The fail-closed spawn screening and message gates step up one class on failure (Haiku to Sonnet) instead of jumping to Opus. `approve_plan` and the `kill_worker` completion check stay on the Opus class. Opus-class workers get an Opus-class advisor instead of Fable. The commit is fully green, so it can be deployed right after this loop.

**Requirements:** `docs/plans/2026-10-16-per-call-difficulty-tiering-design.md` (T1-T10), plus the operator clarification of 2026-10-16: "Please note that "opus" is an opus class model, which includes sol if we are running codex".

**Model classes.** Every tier in this plan is a class name, resolved per client:

| Class | Claude | Codex, hooks | Codex, Commander |
|---|---|---|---|
| `haiku` | Claude Haiku | newest installed `gpt-*-luna`, else `gpt-6-luna` | `providers.clients.codex.models.haiku` (default `gpt-5.6-luna`) |
| `sonnet` | Claude Sonnet | newest installed `gpt-*-terra`, else `gpt-5.6-terra` | `providers.clients.codex.models.sonnet` (default `gpt-5.6-terra`) |
| `opus` | Claude Opus | not used by the hooks | `providers.clients.codex.models.opus` (default `gpt-5.6-sol`) |

The hooks pick the newest installed Codex model, as the advisor broker does. The Commander is config-driven, as it already is for the Haiku class (`llm-backend.md:231`).

**Architecture:**
- **Hooks.** `call_validation_llm` in `worker/hooks/plan-validator.sh` takes an optional third argument, the class (`haiku` by default). `sonnet` skips the local box entirely and runs one lean child: `claude --model sonnet`, or under Codex `codex exec -m <newest gpt-*-terra>`. The flags, schema handling and session-free settings match the Haiku child. The child runs under its own cap, `sonnet_model_timeout_seconds`. A Sonnet failure fails open with no further step. `get-back-to-work-impl.sh` `run_check` passes `sonnet` for the continuation check only.
- **Commander, `fast_model.py`.** `fast_model_clients(commander_config, tier)` and `call_fast_model(..., tier="haiku")` map the class through each client's `models.<tier>`. The default `haiku` keeps today's argv, labels and client order.
- **Commander, `grader.py`.** `LocalGrader.grade(..., tier="haiku")`. `tier="sonnet"` builds no local client, sends no local request, and asks the Sonnet class once. Its failure is an `infrastructure_error`.
- **Commander, `orchestrator_mcp.py`.** `_call_grader(..., *, tier=None)` resolves a requested class through the existing `ProviderRouter` (`None` keeps the configured grader class, Opus by default). Spawn screening (C5/C6) escalates to `tier="sonnet"`. The `send_to_worker` / `send_keys_to_worker` / `post_message` backups (C8/C9/C10) call `_call_grader(..., tier="sonnet")`. Worker health (C11) calls the local grader with `tier="sonnet"`. `approve_plan` (C7) and the `kill_worker` check (C12) are unchanged, so they stay on the Opus class (Claude Opus, or `gpt-5.6-sol` on Codex). `_call_local_grader` forwards `tier=` only when it is not `haiku`.
- **Worker advisors (T8).** The default `advisor.advisor_models` becomes `{"claude-sonnet": "opus", "claude-opus": "opus"}`, so every advisor resolution site that reads it (`orchestrator_mcp.py` `_advisor_model_for` at both spawn paths, `main.py` `_handle_spawn_worker`, `provider_config.advisor_model_for`) gives an Opus-class worker an Opus-class advisor. An explicit `"claude-opus": "fable"` in config is still honored, and `_resolve_fable_advisor_model` still redirects it to `opus` while Fable is unavailable. Codex workers receive the broker directive instead of `/advisor`; the broker's `one-up` maps Sol to Astra, so the Commander sends `review_tier: "same"` when the worker's class equals its advisor's class (an Opus-class worker on Codex is reviewed by Sol), and `one-up` otherwise.
- **Advisor Fallback directive (T10).** When the `advisor` tool is unavailable, the substitute reviewer is one class up, capped at the Opus class: Claude `Agent model=opus` (`model=sonnet` from a Haiku session); Codex `run_codex_advisor_review` with `review_tier: "one-up"` below the Opus class and `review_tier: "same"` from Sol. Fable/Astra only on explicit operator request. Every parallel surface gets the same text: repo `CLAUDE.md` and `.claude/rules/behavioral.md` (directive 10 only), root `AGENTS.md` (directive 10 only), `worker/skills/advisor-fallback/SKILL.md`, every Advisor Fallback copy in `worker/skills/activate-professional-mode/SKILL.md`, and the two worker templates. The T8 per-spawn Codex instruction (`"same"` for an Opus-class worker with an Opus-class advisor, `"one-up"` otherwise) then agrees with the templates.
- **Default-label test (T7).** The stale test expecting "Ollama" for a missing config is renamed and expects "Haiku"; the matching docstring is corrected.

**Tech Stack:** bash hooks plus bash tests with stubs on PATH; Python 3 Commander plus pytest; TypeScript plus vitest (episodic-memory, regression only).

Loop: full. This changes hook enforcement code and Commander grader routing.

## Execution invariants

- Use literal absolute paths everywhere. Shell state does not persist between commands, so every command `cd`s or uses absolute paths itself.
- Use `git -C /Users/roberthyatt/Code/ironclaude` for every git command.
- `docs/` is gitignored, so stage plan and findings files with `git add -f`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push.
- Execution subagents must not use the Skill tool.
- Never edit `~/.claude/ironclaude-hooks-config.json`. It is a human-only guardrail file; even benign keys (such as `sonnet_model_timeout_seconds`) are changed only by the operator.
- Never use `2>/dev/null` on an evidence command. Each run writes its own log file under `/private/tmp/claude-502/` and prints its exit code.
- The placeholders `<SONNET_MODEL_TIMEOUT_DEFAULT>`, `<NEWEST_TERRA>` and `<RESOLVED_SONNET_MODEL>` are replaced by the literal values recorded in Task 1's findings "Gate values" block. Never invent them.
- Line numbers are planning-time values. Earlier steps and tasks insert lines above later targets (for example Task 2 Step 2 item 2 shifts `_newest_luna`; Task 4 shifts the Task 5 targets in `orchestrator_mcp.py` and `test_orchestrator_mcp.py`), so anchor every edit by the quoted symbol, test name or text.
- For a provider handle, `_effective_worker_type` returns `claude-<handle.effective_tier>` (`orchestrator_mcp.py:694-698`), so the Task 5 Codex tests pass an `opus` handle to get `claude-opus`.
- If `claude` refuses to start inside the executor's own Claude Code session, run it with `env -u CLAUDECODE` (see memory `project_restart_commander_strip_claudecode`). This is a contingency, not a step.
- **Known flaky check, not caused by this work (backlogged):** inside an aggregate run, the `test-gbtw-continuation-judge.sh` checks `throttle fourth Stop passes natively` and `throttle fourth Stop skips judge` fail intermittently. If only these fail, rerun the same command once before treating it as a failure, and report the flake in the task result either way.
- **The Commander suite must end fully green (T7).** Every full-suite run in this plan expects 0 failed. Task 4 fixes the one previously carried failure. Any other failure must be fixed; if it cannot be, show it pre-existing in a temporary `git worktree` of `HEAD` with the staged diff applied (never `git stash`) and STOP to report it, because the operator deploys right after this loop.

**Verified at planning time** (read from source on 2026-10-16, working tree with the staged Haiku-class fallback):

| What | Where / fact |
|---|---|
| Hook validator | `worker/hooks/plan-validator.sh`: `IC_FAST_MODEL_TIMEOUT_DEFAULT=30` `:93`; `_resolve_fast_model_timeout` `:96-102`; `_newest_luna` `:106-113` (regex hardcodes `-luna$` at `:109`, fallback `gpt-6-luna` `:112`); `_call_fast_model` `:136-158` (signature `$1 prompt $2 schema $3 cap $4 luna` `:137`; Codex schema made strict with `additionalProperties:false` `:142`; Claude argv hardcodes `--model haiku` `:150`); `call_validation_llm` `:171-307` (`local schema="${2:-}"` `:173`, exports initialised `:179-180`, config read `:183-189`, local `case` `:201-285`, chain fallback `:296-306`). It reads no third argument today. |
| Stop-hook caller | `worker/hooks/get-back-to-work-impl.sh` `run_check` `:824-871`, args `$1 check_name $2 prompt $3 schema $4 field` (`:825-828`). It already branches on `continuation` at `:836-838` to pick `CONTINUATION_CONTEXT`. It calls `call_validation_llm "$prompt" "$schema"` at `:844`. The six parallel checks are `:940-974`. `run_check` is defined after the `GBTW_TEST_MODE` return (`:275`), so test mode cannot call it directly. |
| Hook test, fallback | `worker/hooks/tests/test-fast-model-fallback.sh` (178 lines): the stub `claude` records argv `:23-36`, stub `codex` `:38-53`, stub `curl` `:55-63`; `run_cvl` calls with two args `:71-75`; the exact Claude argv assertion `:89-91`; the hang/cap blocks `:155-171`; cleanup `rm -rf "$WORK"` `:173`. |
| Hook test, continuation judge | `worker/hooks/tests/test-gbtw-continuation-judge.sh`: each case writes `validator.json` with `backend: openai` (`:95-97`); the `curl` stub answers the continuation check (schema key `diligently_finished_work`) and writes `MOCK_LOG` (`:57-64`); `claude`/`codex` stubs just `exit 1` (`:76-80`); the hook env line `:140`; "judge called" checks read `MOCK_LOG` (`:148-162`); `recovered final only calls continuation` counts `all-calls.txt` lines (`:180`); the throttle loop env `:226`; the `--live` mode calls `call_validation_llm` with two args at `:28`. Once the continuation check skips the local box, every judged case would read "unavailable", so this harness must be re-stubbed (Task 2). |
| Other hook suites | `test-last-assistant-text.sh` case 5 (`:52-80`) already puts failing `claude`/`codex` stubs first on PATH and expects "evaluation unavailable", which the Sonnet path also yields. No other hook suite grades the continuation check through a stub. `Makefile:40-44` `test-hooks` stops at the first failing suite. |
| Stop-hook timeout | `worker/hooks/hooks.json:272-277` sets no `timeout` on the Stop command, so the client default applies. Not verified further in this plan. |
| Codex models on this host | `/Users/roberthyatt/.codex/models_cache.json` lists one Terra slug, `gpt-5.6-terra`. `worker/mcp-servers/state-manager/src/tools/advisor-review.ts:22` `FALLBACK_CODEX_MODELS = ['gpt-6-luna', 'gpt-5.6-terra', 'gpt-6-sol', 'gpt-6-astra']`, so the Terra fallback slug is `gpt-5.6-terra`. |
| Commander fast model | `commander/src/ironclaude/fast_model.py`: `FAST_MODEL_TIMEOUT_DEFAULT = 30` `:28`; `FAST_LANE_FAST_MODEL_SECONDS = 11` `:31`; `CODEX_SCHEMA_STRICT = True` `:33`; `_DEFAULT_CLIENTS` `:46`; `fast_model_timeout` `:49-54`; `fast_model_clients(commander_config)` `:57-73` reads `client["models"]["haiku"]` `:70`; `call_fast_model(..., timeout, commander_config=None)` `:148-195`, label `haiku` / `luna:<model>` `:156`. Only `test_fast_model.py:78-84` calls `fast_model_clients` (one argument). |
| LocalGrader | `commander/src/ironclaude/grader.py`: imports `make_client` `:19` and `OllamaClient` `:21` by name; `_read_config` `:88-95`; `_build_client` `:106-128`; `grade(system_prompt, user_prompt, schema=None, *, read_timeout=None, on_fast_model=None)` `:177-233` (profile early return `:203-208`; client selection `:210-222`); `_fast_model_step` `:316-327`. |
| Provider classes | `commander/src/ironclaude/config.py:81-106`: claude `models` `haiku/sonnet/opus/fable` = `haiku/sonnet/opus/fable`; codex `models` = `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`, `gpt-6-astra`; `roles.grader` defaults to `{"preferred": "claude", "clients": ["claude"]}`. `provider_config.py:14` `TIER_NAMES = ("haiku", "sonnet", "opus", "fable")`; `_semantic_tier` `:200-205`; `model_for` `:71-81` returns a Claude role override for `(role, tier)` if one exists, else `clients[client].models[tier]`. Legacy role overrides are built only for the configured grader class (`:218-224`), so `("grader", "sonnet")` has none. |
| How `_call_grader` picks its model | `orchestrator_mcp.py` `_call_grader(self, system_prompt, user_prompt, batch=False)` `:1198-1338`. It computes `tier = _semantic_tier(self._grader_model, "grader_model", "opus")` (`:1233`), probes capabilities for that tier (`:1234`), resolves `router.resolve("grader", tier, ["local"])` (`:1239`) and takes `handle.client, handle.model` (`:1240`); `provider_router.py:41-96` calls `model_for(client, effective_tier, role)` (`:86`). So the **Codex branch picks `providers.clients.codex.models.opus` for the opus class: `gpt-5.6-sol` by default** (pinned by `test_grader_routing.py:226-243`); it is config-driven, not newest-installed. With no provider config, `except ProviderConfigError` (`:1245-1248`) uses `self._grader_model`. The Claude argv appends `[1m]` only when `_model_needs_1m_beta(model)` (`:1038`; `brain_client.py:147-150` says Sonnet and Fable return False). `probe_local` marks a tier `supported` when it is a key of the client's `models` (`provider_capabilities.py:83`), so `sonnet` is probeable. |
| Call sites | `orchestrator_mcp.py`: `_call_local_grader(system_prompt, user_prompt, format_schema, *, bounded=False)` `:1340-1348`. C5 `spawn_worker`: local pre-filter `:4852`, escalation on infrastructure error `:4858-4862` and on low/medium/missing confidence `:4867-4871`. C6 `spawn_workers`: pre-filter `:5717`, uncertain set `:5719-5729`, batch call `:5785`, individual calls `:5795`. C7 `approve_plan` `:6165`. C8 `send_to_worker` `:6250-6255` (bounded). C11 `evaluate_worker_health` `:6333`. C9 `send_keys_to_worker` `:6388-6391`. C12 `kill_worker` `:6996`. C10 `post_message` `:7491-7496` (bounded). Log strings "escalating to Opus" / "skipping Opus" appear in no test. |
| Tests that touch these sites | `test_local_grader.py`: C8 `:365-378`; C5 `:424-479`; C11 `:494-554`; C6 `:564-596`, `:617-639`; C7 `:645-656`; C9 send_keys `:671-711` (no infrastructure-error test exists). `test_orchestrator_mcp.py`: C10 `:3998-4010`; C6 single `:6507-6518`; C6 fallback `:6520-6540`, whose `mock_grader(system_prompt, user_prompt, batch=False)` (`:6528`) accepts no `tier` keyword and would raise `TypeError`; `_call_local_grader` delegation `:9185-9193` asserts exactly three positional args. `test_kill_worker_log_cap.py`: C12 `:138-159`. Every other existing escalation test uses `assert_called_once()`, which still passes when `tier` is added. |
| Default-label failure (T7) | `commander/tests/test_notifications.py:1045-1047` `test_resolve_degraded_backend_label_missing_config_defaults_ollama` expects `"Ollama"`, but `resolve_degraded_backend_label` (`commander/src/ironclaude/notifications.py:276-293`) returns `backend.capitalize()` for a non-ollama/openai backend, and a missing config now resolves to `haiku`, so it returns `"Haiku"`. The docstring line `:279` still says "a missing/unparseable config resolves to the ollama default -> 'Ollama'". |
| Advisor default (T8) | `commander/src/ironclaude/config.py:76` `"advisor_models": {"claude-sonnet": "opus", "claude-opus": "fable"}`; `:75` scalar `advisor_model: "opus"`; `DEFAULTS` is the dict at `:41`. The live `commander/config/ironclaude.json` sets `advisor.advisor_model` but no `advisor_models`, so the default applies there. |
| Advisor resolution sites | `orchestrator_mcp.py` `_advisor_model_for` `:534-540` reads `advisor_models[worker_type]`, else the scalar. Single spawn `:5325-5334` and batch spawn `:6017-6030`: Claude workers get `/advisor <_resolve_fable_advisor_model(model)>`; Codex workers get the fixed `_CODEX_ADVISOR_INSTRUCTION` (`:197-209`), which asks for `review_tier: "one-up"`. `main.py` `_handle_spawn_worker` `:4111-4118` reads the same config keys and sends `/advisor`. `provider_config.py` `advisor_model_for` `:83-90` returns the Claude `advisor_worker_models` override, else `model_for(client, tier, "advisor")`; nothing in `src/` currently calls `router.resolve("advisor", …, advisor_worker_type=…)`. `fable_availability.py:315-326` `resolve_advisor_model` maps `fable` to `opus` only while Fable is unavailable (and not usage-limited). `_WORKER_TYPE_TIER` (`orchestrator_mcp.py:186`) maps `claude-opus`/`claude-fable`/`claude-sonnet` to their class. |
| Codex broker mapping | `advisor-review.ts:602-604`: `review_tier: "same"` reviews with the requester's own model; otherwise `resolveCodexReviewer` (`:231-240`) goes one tier up, so a Sol requester gets Astra. |
| Advisor tests | Default asserted only by `test_config.py:100-106` (`"claude-opus": "fable"`). Every other `fable` advisor test passes an explicit `advisor_models` with `"claude-opus": "fable"` and therefore proves the explicit override: `test_orchestrator_mcp.py:1921-1964` (`test_spawn_worker_opus_sends_tiered_advisor_fable`), `:2774-2820`, `:7850-7860`; `test_main_validate.py:345-362` (`test_claude_opus_advisor_is_fable`), `:383-404`; `test_provider_config.py:210-230`. Codex directive constant pinned by `test_orchestrator_mcp.py:1731-1735`, `:6845-6850` (both `claude-sonnet` workers) and `:11439-11459`. |
| Advisor Fallback surfaces (T10) | Enumerated with `grep -rln -e "model=fable" -e "Fable if available" -e "luna→terra→sol→astra" -e "luna → terra → sol → astra" -e "Advisor Fallback"` over the repo (excluding `docs/`, `.git`, `node_modules`, `.venv`, `dist`): `CLAUDE.md:56-61` and `.claude/rules/behavioral.md:56-61` (directive 10, identical; bullet `:58` "Spawn a top-tier subagent … `model=fable` if Fable is available, otherwise `model=opus` …", bullet `:59` Client-aware); `AGENTS.md:56-61` (directive 10; `:57` "manual tiered-up adversarial reviewer", `:59` broker call with `review_tier: "one-up"`); `worker/skills/advisor-fallback/SKILL.md` (frontmatter `:3`, title `:6`, `:10`, `:13`, `:32`, Claude ladder `:47-50` `opus → fable`, Codex `review_tier` bullet `:63-64`, Codex ladder `:76-82`); `worker/skills/activate-professional-mode/SKILL.md` Codex canonical `:315-319`, Claude compact index `:388`, Claude full template `:452-456`, concept table row `:493`, Concept 9 append body `:516-520`; `commander/src/ironclaude/templates/worker_claude_md.md:50-54` and `worker_agents.md:50-54`. `worker/CLAUDE.md` and `commander/CLAUDE.md` carry no Advisor Fallback directive. `CHANGELOG.md` mentions are history (not edited). `worker/skills/executing-plans/SKILL.md:758` (`Agent` with `model=fable` for an actual-Fable consultation) and its tier-up review text are governed by the Top-tier rule from commit `3eb2584`, not by this directive (not edited). `commander/src/brain/rules/behavioral.md` already routes review through a `worker_type=claude-opus` worker. |
| Advisor Fallback pins (T10) | `test_advisor_fallback_directive.py` (all 6 tests; `PRIMARY = "model=fable"` `:13`); `test_activation_client_parity.py` `CANONICAL_CODEX_ADVISOR_BODY` `:120-124`, `CANONICAL_CLAUDE_ADVISOR_BODY` `:132-136` (asserted at `:316-317`), `test_activation_uses_provider_native_state_manager_names_and_advisors` `:413-427`; `test_worker_claude_md_template.py` byte-equality of both templates with the activation canonical blocks (`:43-62`) and `test_worker_templates_have_semantic_concept_parity` `:64-103` (`:94` "`Agent` tool (`model=fable`"). `test_instruction_surface_parity.py` pins only directive headings (`:26`), which do not change. `test_executing_plans_skill.py` pins the executing-plans skill, not this directive. The broker (`advisor-review.ts`) is unchanged. |
| Commander logs | The daemon (`main.py:5983-5999`) logs at INFO to `/tmp/ic/daemon.log`; C1-C4 run in this process. The five gates, worker health and the MCP spawn paths run in the orchestrator MCP subprocess (`orchestrator_mcp.py` `main` `:8447-8513`), which configures no file handler; FastMCP's `configure_logging` sends its INFO lines to that process's stderr only. The Brain's Claude Code MCP log (`~/Library/Caches/claude-cli-nodejs/-Users-roberthyatt--ironclaude-brain/mcp-logs-orchestrator/*.jsonl`) holds only the startup stderr ("Loaded plugin: …"), not later INFO lines. Worker panes are captured by `tmux pipe-pane` to `<log_dir>/<session>.log` (`tmux_manager.py:342-346`), `log_dir` default `/tmp/ic-logs` (`config.py:61`), session name `ic-<worker_id>`. |
| Docs | `worker/config-schema/llm-backend.md`: schema block ends `"fast_model_timeout_seconds": number?` `:51`; `## Haiku-class fast-model chain` `:228-235`; `## Out of scope` `:237`. `README.md`: `### Validation Backend: Haiku-Class Models (Recommended)` `:247`, Timeout paragraph `:263`; `### Model Configuration` worker/advisor table `:302-309` ("advisor **one tier up**", `claude-opus` → Fable) and tiering paragraph `:311` ("an Opus worker gets a Fable advisor"); Configuration Reference rows `:733-736` (`advisor.enabled` "one-tier-up advisor session", `advisor.advisor_models` default with `"claude-opus": "fable"`). |
| Scratch inputs | `/private/tmp/claude-502/haiku-measure/` holds `measure.py`, `schema.json`, `schema-strict.json` and `prompt.txt` from the Haiku loop. `/private/tmp/claude-502/hfm-sentinel/bin/{claude,codex}` exist. |

---

## Task 1: Measure lean Sonnet and Terra children and compute the gate values

No tests required: measurement. No repository code changes.

**Files:**
- Create: `docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md`
- Stage only: `docs/plans/2026-10-16-per-call-difficulty-tiering-design.md`, `docs/plans/2026-10-16-per-call-difficulty-tiering.md`, `docs/plans/2026-10-16-per-call-difficulty-tiering.plan.json`

Scratch directory: `/private/tmp/claude-502/sonnet-measure/` (outside the repo). The Write/Edit tools are blocked there, so create files through Bash.

### Step 1: Prepare the scratch inputs

Copy the Haiku loop's inputs so the two measurements are comparable:
```bash
mkdir -p /private/tmp/claude-502/sonnet-measure && cp /private/tmp/claude-502/haiku-measure/measure.py /private/tmp/claude-502/haiku-measure/schema.json /private/tmp/claude-502/haiku-measure/schema-strict.json /private/tmp/claude-502/haiku-measure/prompt.txt /private/tmp/claude-502/sonnet-measure/ > /private/tmp/claude-502/sonnet-measure/prep.log 2>&1; echo "prep=$?"; cat /private/tmp/claude-502/sonnet-measure/prep.log; ls -la /private/tmp/claude-502/sonnet-measure
```
Expected: `prep=0`, and `measure.py`, `schema.json`, `schema-strict.json` and `prompt.txt` are listed, each with a non-zero size.

If `prep` is non-zero because the Haiku scratch files are gone, recreate them with a Bash heredoc in `/private/tmp/claude-502/sonnet-measure/`:
- `schema.json`: `{"type":"object","properties":{"reasoning":{"type":"string"},"evidence_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","evidence_quality"]}`
- `schema-strict.json`: the same object with `"additionalProperties":false` added.
- `prompt.txt`: `Grade whether the assistant backed its claims with evidence. TRANSCRIPT: User: what does f() return? Assistant: I read f.py; line 2 returns 3, and the test test_f passed. Respond with ONLY JSON: {"reasoning": "...", "evidence_quality": "A/B/C/D/F"}`
- `measure.py`: verbatim from `docs/plans/2026-10-16-haiku-class-fast-model-fallback.md` Task 1 Step 1.

### Step 2: Single-call Claude Sonnet latency (5 sequential runs)

```bash
cd /private/tmp/claude-502/sonnet-measure && python3 measure.py 5 1 prompt.txt single /tmp -- claude --model sonnet --print --tools "" --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$(cat schema.json)" > single.json 2> single.log; echo "single=$?"; cat single.json
```
Expected: `single=0`, and every `results[].rc` is 0. If any rc is non-zero, show that run's `.err` file and STOP.

### Step 3: Six concurrent Claude Sonnet children

```bash
cd /private/tmp/claude-502/sonnet-measure && python3 measure.py 6 6 prompt.txt conc /tmp -- claude --model sonnet --print --tools "" --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$(cat schema.json)" > conc.json 2> conc.log; echo "conc=$?"; cat conc.json
```
Expected: `conc=0`, and all six rc are 0.

### Step 4: Output shape, resolved model, and no persisted session

```bash
cd /private/tmp/claude-502/sonnet-measure && for f in single-*.out conc-*.out; do printf '%s ' "$f"; jq -c '(if type=="array" then .[] else . end) | select(.type=="result") | {so: .structured_output, models: ((.modelUsage // {}) | keys), sid: .session_id}' "$f"; done > shape.log 2>&1; echo "shape=$?"; cat shape.log; for sid in $(jq -r '(if type=="array" then .[] else . end) | select(.type=="result") | .session_id' single-*.out conc-*.out); do if [ -e "/Users/roberthyatt/.claude/projects/-private-tmp/$sid.jsonl" ]; then echo "PERSISTED $sid"; else echo "absent $sid"; fi; done > persist.log 2>&1; cat persist.log
```
Expected:
- `shape=0`. Every line has `so` as an object with `evidence_quality` in A-F.
- Every `models` list contains a model name containing `sonnet`. Record it as `RESOLVED_SONNET_MODEL`. If a list names no `sonnet` model, STOP and report.
- `persist.log` holds 11 lines, all beginning `absent`.

### Step 5: Codex Terra latency and shape (3 runs, strict schema)

```bash
cd /private/tmp/claude-502/sonnet-measure && TERRA=$(jq -r '[.models[]? | .slug? | select(type=="string" and test("^gpt-[0-9]+(\\.[0-9]+)*-terra$"))] | sort_by(capture("^gpt-(?<v>[0-9.]+)-terra$").v | split(".") | map(tonumber)) | last // "gpt-5.6-terra"' /Users/roberthyatt/.codex/models_cache.json); echo "terra=$TERRA"; touch codex-marker; python3 measure.py 3 1 prompt.txt codex /tmp -- codex exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only -c 'model_reasoning_effort="low"' -m "$TERRA" --output-schema /private/tmp/claude-502/sonnet-measure/schema-strict.json - > codex.json 2> codex.log; echo "codex=$?"; cat codex.json; for f in codex-*.out; do printf '%s ' "$f"; jq -Rc 'fromjson? | select(.type=="item.completed" and .item.type=="agent_message") | .item.text' "$f" | tail -n 1; jq -Rr 'fromjson? | select(.type=="thread.started") | .thread_id' "$f"; done > codex-shape.log 2>&1; cat codex-shape.log; find /Users/roberthyatt/.codex/sessions -newer codex-marker -type f > codex-rollouts.log 2>&1; echo "rollouts=$(wc -l < codex-rollouts.log)"
```
Expected:
- `terra=` prints the newest installed Terra slug. At planning time the cache lists only `gpt-5.6-terra`. Record the printed value as `NEWEST_TERRA`.
- `codex=0`, with all three rc 0. If any rc is non-zero, show that run's `.err` and STOP.
- Each `codex-*.out` has a last `agent_message` whose text parses as an object with `evidence_quality`, plus a `thread_id`.
- `rollouts=0`, unless another live Codex session wrote one. In that case none of the listed files may contain a measured `thread_id`.

### Step 6: Compute the gate values and write the findings doc

Record the versions:
```bash
claude --version > /private/tmp/claude-502/sonnet-measure/versions.log 2>&1; echo "claude_v=$?"; codex --version >> /private/tmp/claude-502/sonnet-measure/versions.log 2>&1; echo "codex_v=$?"; cat /private/tmp/claude-502/sonnet-measure/versions.log
```
Expected: `claude_v=0`, `codex_v=0`, two version lines.

Write `docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md`. It records:
- the raw `single.json`, `conc.json` and `codex.json` numbers;
- `RESOLVED_SONNET_MODEL`, `shape.log`, `persist.log`, `codex-shape.log` and the rollout result;
- the two versions.

End it with this block, filled mechanically (p95 values are `p95_nearest_rank` from `measure.py`):

```
## Gate values
P95_SINGLE_SONNET=<single.json p95_nearest_rank>
P95_CONCURRENT_SONNET=<conc.json p95_nearest_rank>
P95_SINGLE_TERRA=<codex.json p95_nearest_rank>
RESOLVED_SONNET_MODEL=<model name from Step 4>
NEWEST_TERRA=<slug from Step 5>
SONNET_MODEL_TIMEOUT_DEFAULT=max(30, ceil(2 × P95_CONCURRENT_SONNET)) = <integer>
SONNET_WITHIN_LIMIT=<yes if P95_CONCURRENT_SONNET ≤ 60, else STOP>
TERRA_WITHIN_CAP=<yes if P95_SINGLE_TERRA < SONNET_MODEL_TIMEOUT_DEFAULT, else STOP>
```

STOP rule: if any value reads STOP, stop execution and report the findings to the operator. Do not pick a different cap, model or class.

### Step 7: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md docs/plans/2026-10-16-per-call-difficulty-tiering-design.md docs/plans/2026-10-16-per-call-difficulty-tiering.md docs/plans/2026-10-16-per-call-difficulty-tiering.plan.json
```
Expected: no output.

---

## Task 2: Hooks — the class argument in `call_validation_llm`; continuation judge on the Sonnet class

TDD.

**Files:**
- Modify: `worker/hooks/plan-validator.sh` (`:91-113`, `:131-158`, `:164-201`, `:304`)
- Modify: `worker/hooks/get-back-to-work-impl.sh` (`:835-838`, `:844`)
- Modify: `worker/hooks/tests/test-fast-model-fallback.sh` (stub `:23-28`; insert before `:173`)
- Modify: `worker/hooks/tests/test-gbtw-continuation-judge.sh` (`:28`, `:47-80`, `:140`, `:148-162`, after `:216`, `:226`, before `:235`)

### Step 1: Tests (RED)

**1a. `test-fast-model-fallback.sh`.** In the stub `claude` heredoc, insert after the argv loop (`:26`) the line:
```bash
printf '%s\n' "$2" >> "$STUB_LOG_DIR/claude.models"
```
(`$2` is the value after `--model`.) Then insert this block directly before `rm -rf "$WORK"` (`:173`), substituting `<SONNET_MODEL_TIMEOUT_DEFAULT>`:
```bash
# ── Per-call class: the third argument. sonnet never uses the local box. ──
run_cvl_tier() {
  reset_logs
  call_validation_llm "grade this" "$SCHEMA" "$1" > "$WORK/out"; RC=$?
  OUT=$(jq -c . "$WORK/out" 2>/dev/null); RAW=$(cat "$WORK/out"); BACKEND="$VALIDATION_LLM_BACKEND"
}
export IC_HOOK_CLIENT=claude STUB_CURL_MODE=ok STUB_CLAUDE_MODE=array

echo "=== sonnet class skips an openai box and asks Claude Sonnet ==="
cfg '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}'
run_cvl_tier sonnet
assert_eq "sonnet -> claude answer" '{"ok":true,"reason":"from-array"}' "$OUT"
assert_eq "sonnet -> openai box never called" "no" "$(called curl.calls)"
assert_eq "sonnet -> backend label sonnet" "sonnet" "$BACKEND"
assert_eq "sonnet isolation flags (exact argv)" \
  "--model|sonnet|--print|--tools||--no-session-persistence|--strict-mcp-config|--settings|{\"disableAllHooks\":true}|--output-format|json|--json-schema|$SCHEMA|" \
  "$(tr '\n' '|' < "$STUB_LOG_DIR/claude.argv")"

echo "=== sonnet class skips an ollama box too ==="
cfg '{"backend":"ollama","ollama":{"url":"http://x","model":"m"}}'
run_cvl_tier sonnet
assert_eq "sonnet -> ollama box never called" "no" "$(called curl.calls)"
assert_eq "sonnet (ollama config) -> claude answer" '{"ok":true,"reason":"from-array"}' "$OUT"

echo "=== explicit haiku class equals the default (positive control) ==="
cfg '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}'
run_cvl_tier haiku
assert_eq "haiku -> local answer" '{"ok":true,"reason":"local"}' "$OUT"
assert_eq "haiku -> claude not called" "no" "$(called claude.argv)"
assert_eq "haiku -> backend label openai:m" "openai:m" "$BACKEND"

echo "=== sonnet failure fails open with no Haiku retry and no local box ==="
STUB_CLAUDE_MODE=fail; run_cvl_tier sonnet
assert_eq "sonnet fail -> rc 1" "1" "$RC"
assert_eq "sonnet fail -> empty stdout" "" "$RAW"
assert_eq "sonnet fail -> one claude call, at sonnet" "sonnet" "$(cat "$STUB_LOG_DIR/claude.models")"
assert_eq "sonnet fail -> local box never called" "no" "$(called curl.calls)"
STUB_CLAUDE_MODE=array

echo "=== Codex sonnet class uses the newest installed Terra ==="
export IC_HOOK_CLIENT=codex CODEX_HOME="$WORK/codexhome2" STUB_CODEX_MODE=ok; mkdir -p "$CODEX_HOME"
printf '%s' '{"models":[{"slug":"gpt-5.9-terra"},{"slug":"gpt-5.10-terra"},{"slug":"gpt-6-luna"},{"slug":"gpt-6-sol"},{"slug":null},{}]}' > "$CODEX_HOME/models_cache.json"
run_cvl_tier sonnet
assert_eq "codex sonnet -> last agent_message object" '{"ok":true,"reason":"last"}' "$OUT"
assert_eq "codex sonnet -> backend label terra:<numeric-newest>" "terra:gpt-5.10-terra" "$BACKEND"
assert_eq "codex sonnet -> local box never called" "no" "$(called curl.calls)"
assert_eq "codex sonnet isolation flags (argv head)" \
  'exec|--json|--ephemeral|--ignore-user-config|--ignore-rules|--skip-git-repo-check|-s|read-only|-c|model_reasoning_effort="low"|-m|gpt-5.10-terra|--output-schema|' \
  "$(head -n 13 "$STUB_LOG_DIR/codex.argv" | tr '\n' '|')"
assert_eq "codex sonnet schema is strict" "$(printf '%s' "$SCHEMA" | jq -c '. + {additionalProperties:false}')" "$(jq -c . "$STUB_LOG_DIR/codex.schema")"
cfg '{"backend":"haiku"}'; run_cvl_tier haiku
assert_eq "codex haiku class still uses the newest Luna (positive control)" "luna:gpt-6-luna" "$BACKEND"
rm -f "$CODEX_HOME/models_cache.json"; run_cvl_tier sonnet
assert_eq "codex sonnet, no cache -> terra:gpt-5.6-terra" "terra:gpt-5.6-terra" "$BACKEND"
export IC_HOOK_CLIENT=claude; unset CODEX_HOME STUB_CODEX_MODE

echo "=== sonnet_model_timeout_seconds resolution ==="
assert_eq "sonnet cap unset -> measured default" "<SONNET_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_sonnet_model_timeout '{}')"
assert_eq "sonnet cap empty config -> measured default" "<SONNET_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_sonnet_model_timeout '')"
assert_eq "sonnet cap configured 7 -> 7" "7" "$(_resolve_sonnet_model_timeout '{"sonnet_model_timeout_seconds":7}')"
assert_eq "sonnet cap non-number -> measured default" "<SONNET_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_sonnet_model_timeout '{"sonnet_model_timeout_seconds":"x"}')"
assert_eq "sonnet cap ignores fast_model_timeout_seconds" "<SONNET_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_sonnet_model_timeout '{"fast_model_timeout_seconds":7}')"

echo "=== sonnet cap is enforced and the child is killed ==="
cfg '{"backend":"haiku","sonnet_model_timeout_seconds":2}'; STUB_CLAUDE_MODE=hang
start=$SECONDS; run_cvl_tier sonnet; elapsed=$((SECONDS - start)); sleep 1
assert_eq "sonnet cap -> rc 1" "1" "$RC"
assert_eq "sonnet cap -> returned within 6s" "yes" "$([ "$elapsed" -le 6 ] && echo yes || echo no)"
assert_eq "sonnet cap -> child process killed" "dead" "$(kill -0 "$(cat "$STUB_LOG_DIR/claude.pid")" 2>/dev/null && echo alive || echo dead)"
unset STUB_CURL_MODE
```

**1b. `test-gbtw-continuation-judge.sh`.**
1. `:28`: change `raw="$(call_validation_llm "$prompt" "$CONTINUATION_SCHEMA" 2>"$ROOT/$label.stderr")" || raw=""` to `raw="$(call_validation_llm "$prompt" "$CONTINUATION_SCHEMA" sonnet 2>"$ROOT/$label.stderr")" || raw=""`, so the live mode measures the class production uses.
2. In the `curl` stub, replace the continuation branch body (`:58-64`, from `[ "${MOCK_FAIL_KIND:-}" != continuation ] || exit 1` through the closing `fi` of the `MOCK_CONTINUATION_RAW` test) with:
```bash
  # The continuation judge is a Sonnet-class call: it must never reach the local box.
  printf 'continuation reached the local box\n' >>"$MOCK_LOCAL_CONT_LOG"
  exit 1
```
3. Replace the four stub lines `:76-80` (the comment and the two `exit 1` stubs plus `chmod`) with:
```bash
# Fast-model children. Only the Sonnet-class continuation judge answers (Claude
# --model sonnet, or Codex -m *-terra); any other call is a Haiku-class fallback and
# fails like an unavailable model. Every call is recorded as "<model> <schema keys>".
cat >"$ROOT/bin/claude" <<'MOCK'
#!/usr/bin/env bash
model="" schema="" prev=""
for a in "$@"; do
  if [ "$prev" = --model ]; then model="$a"; fi
  if [ "$prev" = --json-schema ]; then schema="$a"; fi
  prev="$a"
done
prompt="$(cat)"
keys="$(jq -c '.properties | keys' <<<"$schema" 2>/dev/null || printf '[]')"
if [ -n "${MOCK_MODEL_LOG:-}" ]; then printf '%s %s\n' "$model" "$keys" >>"$MOCK_MODEL_LOG"; fi
[ "$model" = sonnet ] || exit 1
[ "${MOCK_FAIL:-0}" != 1 ] || exit 1
jq -e '.properties.diligently_finished_work' >/dev/null 2>&1 <<<"$schema" || exit 1
[ "${MOCK_FAIL_KIND:-}" != continuation ] || exit 1
if [ -n "${MOCK_ALL_LOG:-}" ]; then printf '%s\n' "$keys" >>"$MOCK_ALL_LOG"; fi
printf '%s' "$prompt" >"$MOCK_LOG"
if [ -n "${MOCK_CONTINUATION_RAW:-}" ]; then answer="$MOCK_CONTINUATION_RAW"
else answer="$(jq -cn --arg g "$MOCK_GRADE" '{reasoning:"stub validator response",diligently_finished_work:$g}')"; fi
if jq -e 'type == "object"' >/dev/null 2>&1 <<<"$answer"; then
  jq -cn --argjson so "$answer" '[{type:"system"},{type:"result",subtype:"success",structured_output:$so}]'
else
  jq -cn --arg r "$answer" '[{type:"result",subtype:"success",result:$r}]'
fi
MOCK
cat >"$ROOT/bin/codex" <<'MOCK'
#!/usr/bin/env bash
model="" schema_file="" prev=""
for a in "$@"; do
  if [ "$prev" = -m ]; then model="$a"; fi
  if [ "$prev" = --output-schema ]; then schema_file="$a"; fi
  prev="$a"
done
prompt="$(cat)"
schema="$(cat "$schema_file" 2>/dev/null || printf '{}')"
keys="$(jq -c '.properties | keys' <<<"$schema" 2>/dev/null || printf '[]')"
if [ -n "${MOCK_MODEL_LOG:-}" ]; then printf '%s %s\n' "$model" "$keys" >>"$MOCK_MODEL_LOG"; fi
case "$model" in *-terra) ;; *) exit 1 ;; esac
[ "${MOCK_FAIL:-0}" != 1 ] || exit 1
jq -e '.properties.diligently_finished_work' >/dev/null 2>&1 <<<"$schema" || exit 1
[ "${MOCK_FAIL_KIND:-}" != continuation ] || exit 1
if [ -n "${MOCK_ALL_LOG:-}" ]; then printf '%s\n' "$keys" >>"$MOCK_ALL_LOG"; fi
printf '%s' "$prompt" >"$MOCK_LOG"
if [ -n "${MOCK_CONTINUATION_RAW:-}" ]; then answer="$MOCK_CONTINUATION_RAW"
else answer="$(jq -cn --arg g "$MOCK_GRADE" '{reasoning:"stub validator response",diligently_finished_work:$g}')"; fi
jq -cn '{type:"thread.started",thread_id:"t-1"}'
jq -cn --arg t "$answer" '{type:"item.completed",item:{type:"agent_message",text:$t}}'
MOCK
chmod +x "$ROOT/bin/claude" "$ROOT/bin/codex"
export MOCK_LOCAL_CONT_LOG="$ROOT/local-continuation.log"
mkdir -p "$ROOT/codexhome"
printf '%s' '{"models":[{"slug":"gpt-5.9-terra"},{"slug":"gpt-5.10-terra"},{"slug":"gpt-6-luna"}]}' >"$ROOT/codexhome/models_cache.json"
```
4. Hook invocation `:140`: insert `CODEX_HOME="$ROOT/codexhome" MOCK_MODEL_LOG="$dir/model-calls.txt"` right after `PATH="$ROOT/bin:$PATH"` in the env list.
5. Inside the `if [ -s "$log" ]; then` block (`:152-161`), after the `"$label workflow context"` check, add:
```bash
      local expect_model=sonnet
      [ "$client" != codex ] || expect_model=gpt-5.10-terra
      check "$label continuation asks the Sonnet class" "$expect_model" "$(grep -F diligently_finished_work "$dir/model-calls.txt" 2>/dev/null | cut -d' ' -f1 | sort -u | tr -d '\n')"
```
6. After the two `mixed_failure_and_continuation_D` checks (`:214-215`), add:
```bash
check 'mixed failure: the four other checks fall back to the Haiku class' 4 "$(grep -c '^haiku ' "$CASE_DIR/model-calls.txt" || true)"
check 'mixed failure: only the continuation asks the Sonnet class' 1 "$(grep -c '^sonnet ' "$CASE_DIR/model-calls.txt" || true)"
check 'mixed failure: the Sonnet call is the continuation schema' 1 "$(grep -c '^sonnet .*diligently_finished_work' "$CASE_DIR/model-calls.txt" || true)"
```
7. Throttle invocation `:226`: insert `CODEX_HOME="$ROOT/codexhome"` right after `PATH="$ROOT/bin:$PATH"`.
8. Directly before `printf 'results: %d pass, %d fail\n'` (`:235`), add:
```bash
check 'continuation never reaches the local box' no "$([ -e "$MOCK_LOCAL_CONT_LOG" ] && printf yes || printf no)"
```
The `recovered final only calls continuation` check (`:180`) stays unchanged. It still counts one line, because the model stub appends the continuation's schema keys to `all-calls.txt`.

RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/pct-t2-red-fallback.log 2>&1; echo "red_fallback=$?"; grep -c '^FAIL' /private/tmp/claude-502/pct-t2-red-fallback.log; tail -n 1 /private/tmp/claude-502/pct-t2-red-fallback.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh > /private/tmp/claude-502/pct-t2-red-gbtw.log 2>&1; echo "red_gbtw=$?"; grep -c '^FAIL' /private/tmp/claude-502/pct-t2-red-gbtw.log; tail -n 1 /private/tmp/claude-502/pct-t2-red-gbtw.log
```
Expected (derived from current code, which ignores a third argument):
- `red_fallback=1`, last line `Results: N passed, M failed` with M ≥ 1. The log contains:
  - `FAIL: sonnet -> openai box never called` and `FAIL: sonnet -> backend label sonnet` (today the openai box answers and the label is `openai:m`);
  - `FAIL: codex sonnet -> backend label terra:<numeric-newest>`;
  - `FAIL: sonnet fail -> rc 1` (today the local answer returns 0);
  - `_resolve_sonnet_model_timeout: command not found`;
  - `FAIL: sonnet cap -> returned within 6s` (today the Haiku path uses the 30 s fast cap, so this case takes about 30 s).
- The positive controls `haiku -> local answer` and `codex haiku class still uses the newest Luna (positive control)` PASS.
- `red_gbtw=1`. The log contains `FAIL brainstorming decision expected=block actual=approve` (the continuation now hits the curl stub's local-box branch and becomes "unavailable"), `FAIL brainstorming judge called expected=yes actual=no`, and `FAIL continuation never reaches the local box expected=no actual=yes`.

Record the measured lists.

### Step 2: Implementation (GREEN)

**`worker/hooks/plan-validator.sh`**
1. Header `:15-16`: replace with
```
#   - call_validation_llm() — centralized LLM call per model class. Haiku class (default):
#     local box (Ollama/OpenAI-compatible), then Claude Haiku or the newest Codex Luna,
#     then fail open. Sonnet class: Claude Sonnet or the newest Codex Terra only, then fail open.
```
2. After `_resolve_fast_model_timeout` (`:102`), insert (substituting the gate value):
```bash

# Default for sonnet_model_timeout_seconds, set from measured lean Sonnet latency
# (docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md).
IC_SONNET_MODEL_TIMEOUT_DEFAULT=<SONNET_MODEL_TIMEOUT_DEFAULT>

# Pure helper: Sonnet-class cap from config JSON content ($1); default when unset/invalid.
_resolve_sonnet_model_timeout() {
  local t
  t=$(printf '%s' "$1" | jq -r --argjson d "$IC_SONNET_MODEL_TIMEOUT_DEFAULT" \
    '.sonnet_model_timeout_seconds // $d | if type == "number" and . > 0 then floor else $d end' 2>/dev/null) || t=""
  [[ "$t" =~ ^[0-9]+$ ]] || t="$IC_SONNET_MODEL_TIMEOUT_DEFAULT"
  echo "$t"
}
```
3. Replace `_newest_luna` (`:104-113`, its two comment lines through its closing `}`) with:
```bash
# Newest installed gpt-<version>-<word> (word: luna|terra) from the Codex models cache,
# else $2. Mirrors readInstalledCodexModels/newestAtTier and FALLBACK_CODEX_MODELS in
# state-manager advisor-review.ts.
_newest_codex_model() {
  local word="$1" fallback="$2" cache="${CODEX_HOME:-$HOME/.codex}/models_cache.json" slug=""
  if [ -r "$cache" ]; then
    slug=$(jq -r --arg w "$word" '[.models[]? | .slug? | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-" + $w + "$"))]
      | sort_by(capture("^gpt-(?<v>[0-9.]+)-").v | split(".") | map(tonumber)) | last // empty' "$cache" 2>/dev/null) || slug=""
  fi
  echo "${slug:-$fallback}"
}

# Haiku class on Codex: the newest Luna. Sonnet class on Codex: the newest Terra.
_newest_luna() { _newest_codex_model luna gpt-6-luna; }
_newest_terra() { _newest_codex_model terra gpt-5.6-terra; }
```
4. `_call_fast_model` (`:131-158`):
   - Replace its first comment line `# Run the Haiku-class model once under its own cap.` with `# Run one fast-model child (Haiku or Sonnet class) under its own cap.`
   - Replace the `# Args:` line with `# Args: $1 prompt, $2 JSON schema ('{"type":"object"}' when empty), $3 cap seconds, $4 Codex model slug (Codex only), $5 Claude model (haiku|sonnet; default haiku)`.
   - `:137`: `local prompt="$1" schema="${2:-}" cap="$3" codex_model="$4" claude_model="${5:-haiku}" work out="" rc=0`
   - `:145`: `-m "$luna"` becomes `-m "$codex_model"`.
   - `:150`: `claude --model haiku` becomes `claude --model "$claude_model"`.
5. `call_validation_llm` doc comment (`:164-169`): change the `# Args:` line to `# Args: $1 = prompt to send, $2 = JSON schema string (standard JSON Schema format), $3 = model class: haiku (default) | sonnet`.
6. After `local schema="${2:-}"` (`:173`), add `local tier="${3:-haiku}"`.
7. After `export VALIDATION_LLM_RESPONSE=""` (`:180`), insert:
```bash

  # Sonnet-class calls never use the local box (design T2) and do not step up (T3).
  if [ "$tier" = "sonnet" ]; then
    local sonnet_cap terra="" sonnet_result
    sonnet_cap=$(_resolve_sonnet_model_timeout "$(cat "$config" 2>/dev/null)")
    if ic_is_codex; then
      terra=$(_newest_terra)
      export VALIDATION_LLM_BACKEND="terra:${terra}"
    else
      export VALIDATION_LLM_BACKEND="sonnet"
    fi
    sonnet_result=$(_call_fast_model "$prompt" "$schema" "$sonnet_cap" "$terra" sonnet) || return 1
    export VALIDATION_LLM_RESPONSE="$sonnet_result"
    echo "$sonnet_result"
    return 0
  fi
```
8. `:304`: `fast_result=$(_call_fast_model "$prompt" "$schema" "$fast_cap" "$luna") || return 1` becomes `fast_result=$(_call_fast_model "$prompt" "$schema" "$fast_cap" "$luna" haiku) || return 1`.

**`worker/hooks/get-back-to-work-impl.sh`**
1. `:835-838` becomes:
```bash
    local fenced_context check_context="$RECENT_CONTEXT" tier="haiku"
    if [ "$check_name" = "continuation" ]; then
        check_context="$CONTINUATION_CONTEXT"
        tier="sonnet"   # design T1 H2: the continuation judge is a Sonnet-level call
    fi
```
2. `:844`: `call_validation_llm "$prompt" "$schema" 2>/dev/null` becomes `call_validation_llm "$prompt" "$schema" "$tier" 2>/dev/null`. (This `2>/dev/null` is existing hook code, not an evidence command.)

### Step 3: GREEN plus hook regression

Recreate the sentinel so no suite can spawn a real model, then run the touched suites and the whole hook set with the sentinel first on PATH:
```bash
mkdir -p /private/tmp/claude-502/hfm-sentinel/bin && printf '#!/bin/bash\necho "$(pwd) $(basename "$0") $*" >> /private/tmp/claude-502/hfm-sentinel/calls.log\nexit 1\n' > /private/tmp/claude-502/hfm-sentinel/bin/claude && cp /private/tmp/claude-502/hfm-sentinel/bin/claude /private/tmp/claude-502/hfm-sentinel/bin/codex && chmod +x /private/tmp/claude-502/hfm-sentinel/bin/claude /private/tmp/claude-502/hfm-sentinel/bin/codex; rm -f /private/tmp/claude-502/hfm-sentinel/calls.log; for t in test-fast-model-fallback test-gbtw-continuation-judge test-last-assistant-text test-openai-backend; do PATH=/private/tmp/claude-502/hfm-sentinel/bin:$PATH bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/$t.sh > /private/tmp/claude-502/pct-t2-$t.log 2>&1; echo "$t=$?"; tail -n 1 /private/tmp/claude-502/pct-t2-$t.log; done; PATH=/private/tmp/claude-502/hfm-sentinel/bin:$PATH make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/pct-t2-hooks-all.log 2>&1; echo "hooks_all=$?"; tail -n 3 /private/tmp/claude-502/pct-t2-hooks-all.log; ls -la /private/tmp/claude-502/hfm-sentinel/calls.log
```
Expected:
- `test-fast-model-fallback=0` with `Results: N passed, 0 failed`.
- `test-gbtw-continuation-judge=0` with `results: N pass, 0 fail`.
- `test-last-assistant-text=0` and `test-openai-backend=0`.
- `hooks_all=0`. If only the known flaky throttle checks fail, rerun the `make` command once (own log `pct-t2-hooks-all-rerun.log`) and report the flake.
- `calls.log` may or may not exist. If it exists, record each line; do not modify those suites.

Any other failure must be shown pre-existing in a temporary `git worktree` of `HEAD` with the staged diff applied (never `git stash`), or else fixed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/plan-validator.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-fast-model-fallback.sh worker/hooks/tests/test-gbtw-continuation-judge.sh
```
Expected: no output.

---

## Task 3: Commander — model class in `fast_model` and `LocalGrader`

TDD. The subprocess is stubbed only at the boundary, `fast_model._run_child` (the autouse conftest fixture already fails it by default).

**Files:**
- Modify: `commander/src/ironclaude/fast_model.py` (`:1-14`, `:26-73`, `:148-156`)
- Modify: `commander/src/ironclaude/grader.py` (`:177-208`)
- Modify: `commander/tests/test_fast_model.py` (append)
- Modify: `commander/tests/test_fast_model_chain.py` (append)

### Step 1: Tests (RED)

Append to `commander/tests/test_fast_model.py` (substitute `<SONNET_MODEL_TIMEOUT_DEFAULT>`):
```python


# ── Model class (tier) per call ──

def _cfg_classes(preferred="claude", clients=("claude",), codex_enabled=False):
    return {"providers": {
        "clients": {
            "claude": {"enabled": True, "path": "/bin/claude-x",
                       "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}},
            "codex": {"enabled": codex_enabled, "path": "/bin/codex-x",
                      "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}},
        },
        "roles": {"grader": {"preferred": preferred, "clients": list(clients)}},
    }}


def test_clients_map_the_requested_class():
    assert fast_model.fast_model_clients(None, "sonnet") == [("claude", "claude", "sonnet")]
    assert fast_model.fast_model_clients({"providers": "bad"}, "sonnet") == [("claude", "claude", "sonnet")]
    assert fast_model.fast_model_clients(_cfg_classes(), "sonnet") == [("claude", "/bin/claude-x", "sonnet")]
    assert fast_model.fast_model_clients(
        _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True), "sonnet"
    ) == [("codex", "/bin/codex-x", "gpt-5.6-terra"), ("claude", "/bin/claude-x", "sonnet")]
    # Positive control: the default class is still haiku.
    assert fast_model.fast_model_clients(_cfg_classes()) == [("claude", "/bin/claude-x", "haiku")]


@pytest.mark.parametrize("cfg,expected", [
    ({}, None),
    ({"sonnet_model_timeout_seconds": 7}, 7),
    ({"sonnet_model_timeout_seconds": "x"}, None),
    ({"sonnet_model_timeout_seconds": True}, None),
    ({"fast_model_timeout_seconds": 7}, None),
])
def test_sonnet_model_timeout(cfg, expected):
    expected = fast_model.SONNET_MODEL_TIMEOUT_DEFAULT if expected is None else expected
    assert fast_model.sonnet_model_timeout(cfg) == expected


def test_sonnet_cap_default_is_the_measured_gate_value():
    assert fast_model.SONNET_MODEL_TIMEOUT_DEFAULT == <SONNET_MODEL_TIMEOUT_DEFAULT>


def test_call_at_sonnet_class_uses_the_sonnet_argv(monkeypatch):
    seen = []

    def fake(argv, *, input_text, timeout, env):
        seen.append({"argv": argv, "timeout": timeout})
        return _proc(_claude_array({"kind": "x"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=9, commander_config=None, tier="sonnet")
    assert out == {"kind": "x"}
    assert seen == [{"argv": fast_model.claude_fast_argv("claude", "sonnet", SCHEMA), "timeout": 9}]


def test_sonnet_failure_names_the_class(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc("", rc=1))
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None, tier="sonnet")
    assert out["infrastructure_error"] is True
    assert "sonnet exited 1" in out["error_detail"]


def test_codex_sonnet_class_is_terra(monkeypatch):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv)
        return _proc("", rc=1)

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model(
        "S", "U", SCHEMA, timeout=5, tier="sonnet",
        commander_config=_cfg_classes(preferred="codex", clients=("codex",), codex_enabled=True))
    assert calls[0][calls[0].index("-m") + 1] == "gpt-5.6-terra"
    assert "terra:gpt-5.6-terra exited 1" in out["error_detail"]


def test_default_class_is_haiku_positive_control(monkeypatch):
    seen = []
    monkeypatch.setattr(fast_model, "_run_child",
                        lambda argv, **kw: seen.append(argv) or _proc(_claude_array({"kind": "x"})))
    assert fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None) == {"kind": "x"}
    assert seen[0][:4] == ["claude", "-p", "--model", "haiku"]
```

Append to `commander/tests/test_fast_model_chain.py`:
```python


# ── LocalGrader model class: sonnet never uses the local box ──

def test_sonnet_class_skips_the_local_box(monkeypatch, tmp_path):
    cfg = _write(tmp_path, {"backend": "ollama", "ollama": {"url": "http://box.invalid:11434", "model": "m"},
                            "sonnet_model_timeout_seconds": 13})
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    with patch("ironclaude.grader.OllamaClient") as ollama, patch("ironclaude.grader.make_client") as make, \
            patch("requests.post", side_effect=AssertionError("no local request at the sonnet class")):
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA, tier="sonnet") == {"valid": True}
    ollama.assert_not_called()
    make.assert_not_called()
    assert [c["argv"][c["argv"].index("--model") + 1] for c in calls] == ["sonnet"]
    assert calls[0]["timeout"] == 13


def test_sonnet_class_failure_is_infrastructure_error_without_step_up(monkeypatch, tmp_path):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(fast_model, "_run_child", fake)
    cfg = _write(tmp_path, {"backend": "haiku"})
    result = LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA, tier="sonnet")
    assert result["infrastructure_error"] is True
    assert "sonnet exited 1" in result["error_detail"]
    assert [a[a.index("--model") + 1] for a in calls] == ["sonnet"]


def test_sonnet_class_uses_codex_terra_from_grader_role(monkeypatch, tmp_path):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv)
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"valid": True})}})
        return subprocess.CompletedProcess(argv, 0, line, "")

    monkeypatch.setattr(fast_model, "_run_child", fake)
    commander_cfg = {"providers": {
        "clients": {"claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku", "sonnet": "sonnet"}},
                    "codex": {"enabled": True, "path": "codex",
                              "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra"}}},
        "roles": {"grader": {"preferred": "codex", "clients": ["codex"]}}}}
    cfg = _write(tmp_path, {"backend": "ollama"})
    with patch("ironclaude.grader.OllamaClient") as ollama:
        out = LocalGrader(config_path=cfg, commander_config=commander_cfg).grade("s", "u", SCHEMA, tier="sonnet")
    assert out == {"valid": True}
    ollama.assert_not_called()
    assert calls[0][calls[0].index("-m") + 1] == "gpt-5.6-terra"


def test_default_class_keeps_the_haiku_chain_positive_control(monkeypatch, tmp_path):
    cfg = _write(tmp_path, {"backend": "haiku"})
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}
    assert calls[0]["argv"][calls[0]["argv"].index("--model") + 1] == "haiku"
    assert calls[0]["timeout"] == fast_model.FAST_MODEL_TIMEOUT_DEFAULT
```

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_fast_model_chain.py -q > /private/tmp/claude-502/pct-t3-red.log 2>&1; echo "red=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t3-red.log; tail -n 3 /private/tmp/claude-502/pct-t3-red.log
```
Expected: `red=1`, no collection error. These fail:
- `test_clients_map_the_requested_class` (`TypeError`: one positional argument expected);
- all five `test_sonnet_model_timeout[...]` and `test_sonnet_cap_default_is_the_measured_gate_value` (`AttributeError`: no `SONNET_MODEL_TIMEOUT_DEFAULT` / `sonnet_model_timeout`);
- `test_call_at_sonnet_class_uses_the_sonnet_argv`, `test_sonnet_failure_names_the_class`, `test_codex_sonnet_class_is_terra` (`TypeError`: unexpected keyword `tier`);
- `test_sonnet_class_skips_the_local_box`, `test_sonnet_class_failure_is_infrastructure_error_without_step_up`, `test_sonnet_class_uses_codex_terra_from_grader_role` (`TypeError`: `grade()` got an unexpected keyword `tier`).

These pass: `test_default_class_is_haiku_positive_control`, `test_default_class_keeps_the_haiku_chain_positive_control`, and every pre-existing test. Record the measured list.

### Step 2: Implementation (GREEN)

**`fast_model.py`**
1. Module docstring: after the first paragraph (ending `--ephemeral --ignore-user-config --ignore-rules.`), add a paragraph:
```
The model class is per call (design T1): `tier="haiku"` (default) or `tier="sonnet"`.
Each client's `models.<tier>` names the model, so on Codex the Sonnet class is the
configured Terra model (default gpt-5.6-terra). Sonnet-class calls never use the local
box; LocalGrader routes them here directly.
```
2. After `CODEX_SCHEMA_STRICT = True` (`:33`), add (substituting the gate value):
```python
# Measured 2026-10-16 (docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md):
# max(30, ceil(2 x p95 of six concurrent lean Sonnet children)).
SONNET_MODEL_TIMEOUT_DEFAULT = <SONNET_MODEL_TIMEOUT_DEFAULT>
```
3. Replace `_DEFAULT_CLIENTS = (("claude", "claude", "haiku"),)` (`:46`) with:
```python
_DEFAULT_CLIENTS = {
    "haiku": (("claude", "claude", "haiku"),),
    "sonnet": (("claude", "claude", "sonnet"),),
}
_CODEX_CLASS_WORD = {"haiku": "luna", "sonnet": "terra"}
```
4. After `fast_model_timeout` (`:49-54`), add:
```python


def sonnet_model_timeout(hooks_cfg) -> int:
    """`sonnet_model_timeout_seconds` from the hooks config, else the measured default."""
    value = hooks_cfg.get("sonnet_model_timeout_seconds") if isinstance(hooks_cfg, Mapping) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    return SONNET_MODEL_TIMEOUT_DEFAULT
```
5. `fast_model_clients` (`:57-73`): signature `def fast_model_clients(commander_config, tier: str = "haiku") -> list[tuple[str, str, str]]:`; docstring `"""(client, executable path, model for the class) in `grader` role order, preferred first. Missing or malformed provider config -> Claude at that class only."""`; `client["models"]["haiku"]` becomes `client["models"][tier]`; both `list(_DEFAULT_CLIENTS)` become `list(_DEFAULT_CLIENTS[tier])`.
6. `call_fast_model` (`:148-156`): signature gains `tier: str = "haiku"` after `commander_config=None`; docstring first line becomes `"""Ask the fast model at the given class (haiku default, or sonnet). Returns the FULL`; `fast_model_clients(commander_config)` becomes `fast_model_clients(commander_config, tier)`; the label line becomes `label = tier if client == "claude" else f"{_CODEX_CLASS_WORD[tier]}:{model}"`.

**`grader.py`**
1. `grade` signature (`:177-185`): add `tier: str = "haiku",` after `on_fast_model=None,`.
2. Docstring: after the `on_fast_model` paragraph add:
```
        `tier` is the call's model class (design T1/T2). "haiku" (default) is the
        chain above. "sonnet" never uses the local box: no local client is built and
        no local request is made; the Sonnet class is asked once under
        `sonnet_model_timeout_seconds`, and its failure is returned as an
        infrastructure_error with no further step-up.
```
3. After the profile block (`:203-208`) and before `try:` (`:210`), insert:
```python
        if tier == "sonnet":
            if on_fast_model is not None:
                on_fast_model()
            return fast_model.call_fast_model(
                profiled_system_prompt, user_prompt, schema,
                timeout=fast_model.sonnet_model_timeout(self._read_config()),
                commander_config=self._commander_config, tier="sonnet")
```

### Step 3: GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_local_grader.py tests/test_grader.py -q > /private/tmp/claude-502/pct-t3-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/pct-t3-green.log
```
Expected: `green=0`, with 0 failed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/fast_model.py commander/src/ironclaude/grader.py commander/tests/test_fast_model.py commander/tests/test_fast_model_chain.py
```
Expected: no output.

---

## Task 4: Commander call sites — class per site, one-class step-up; default-label test

TDD.

**Files:**
- Modify: `commander/tests/test_notifications.py` (`:1045-1047`)
- Modify: `commander/src/ironclaude/notifications.py` (`:279`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (`:1198-1248`, `:1340-1348`, `:4851-4873`, `:5719-5735`, `:5785`, `:5795`, `:6254-6255`, `:6333`, `:6390-6391`, `:7495-7496`)
- Modify: `commander/tests/test_grader_routing.py` (append)
- Modify: `commander/tests/test_local_grader.py` (`:365-378`, `:424-479`, `:564-639`, `:645-656`; append one class)
- Modify: `commander/tests/test_orchestrator_mcp.py` (`:3998-4010`, `:6507-6540`, `:9185-9193`)
- Modify: `commander/tests/test_kill_worker_log_cap.py` (`:158`)

### Step 1: Tests (RED)

**Default-label test (T7).** Replace `test_resolve_degraded_backend_label_missing_config_defaults_ollama` (`test_notifications.py:1045-1047`) with:
```python
def test_resolve_degraded_backend_label_missing_config_defaults_haiku(tmp_path):
    from ironclaude.notifications import resolve_degraded_backend_label
    assert resolve_degraded_backend_label(str(tmp_path / "nope.json")) == "Haiku"
```
This test fails on the old expectation today and passes with the new one; its RED evidence is the current failure of the old test (run it before replacing):
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest "tests/test_notifications.py::test_resolve_degraded_backend_label_missing_config_defaults_ollama" -q > /private/tmp/claude-502/pct-t4-notif-red.log 2>&1; echo "notif_red=$?"; tail -n 5 /private/tmp/claude-502/pct-t4-notif-red.log
```
Expected: `notif_red=1`, with `AssertionError` showing `'Haiku' == 'Ollama'`.

**Existing tests that encode the old routing.** They use `assert_called_once()`, so they would stay green after the change; each gains one class assertion. Two names that say "opus" are renamed to "sonnet". No other assertion changes.

`commander/tests/test_local_grader.py`:

| Test | Change |
|---|---|
| `TestP0SendToWorkerRouting::test_send_to_worker_infrastructure_error_escalates_to_opus` (`:365`) | Rename to `test_send_to_worker_infrastructure_error_escalates_to_sonnet`; docstring "…escalates to the Sonnet class for send_to_worker."; add `assert tools._call_grader.call_args.kwargs.get("tier") == "sonnet"` after `:378`. |
| `TestP1SpawnWorkerHybrid::test_medium_confidence_escalates_to_opus` (`:424`) | Rename to `test_medium_confidence_escalates_to_sonnet`; docstring "Medium confidence triggers Sonnet-class escalation."; add the same `tier == "sonnet"` assertion after `:434`. |
| `TestP1SpawnWorkerHybrid::test_low_confidence_escalates_to_opus` (`:436`) | Rename to `test_low_confidence_escalates_to_sonnet`; docstring "Low confidence triggers Sonnet-class escalation."; add the assertion after `:445`. |
| `TestP1SpawnWorkerHybrid::test_missing_confidence_escalates` (`:447`) | Add the assertion after `:455`. |
| `TestP1SpawnWorkerHybrid::test_invalid_confidence_escalates` (`:457`) | Add the assertion after `:466`. |
| `TestP1SpawnWorkerHybrid::test_ollama_failure_escalates` (`:468`) | Add the assertion after `:479`. |
| `TestP1SpawnWorkersBatch::test_mixed_batch_partitions_correctly` (`:564`) | Add the assertion after `:594`. |
| `TestP1SpawnWorkersBatch::test_all_uncertain_full_batch` (`:617`) | Add the assertion after `:638`. |
| `TestUnchangedBehavior::test_approve_plan_uses_opus_grader` (`:645`) | Add `assert tools._call_grader.call_args.kwargs.get("tier") is None` after `:655` (C7 keeps the default Opus class). |

Append this class at the end of `commander/tests/test_local_grader.py`:
```python


class TestPerCallClasses:
    """Design T1/T3: each call site asks for its model class; backups step up one class."""

    def test_send_keys_infrastructure_error_steps_up_to_sonnet(self, tools, registry, mock_tmux):
        """C9: an unavailable Haiku-class grade steps up to the Sonnet class, not Opus."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        tools._call_local_grader = MagicMock(return_value={
            "infrastructure_error": True, "error_detail": "fast model unavailable"})
        tools._call_grader = MagicMock(return_value={"grade": "A", "approved": True, "feedback": "ok"})
        with patch("ironclaude.orchestrator_mcp._load_avatar_skill", return_value="mock avatar"):
            tools.send_keys_to_worker("w1", list("This is a long message that exceeds twenty characters"))
        tools._call_grader.assert_called_once()
        assert tools._call_grader.call_args.kwargs.get("tier") == "sonnet"

    def test_send_keys_haiku_answer_does_not_escalate(self, tools, registry, mock_tmux):
        """C9 positive control: a Haiku-class answer is final; no grader call."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        _mock_grader_approve(tools)
        with patch("ironclaude.orchestrator_mcp._load_avatar_skill", return_value="mock avatar"):
            tools.send_keys_to_worker("w1", list("This is a long message that exceeds twenty characters"))
        assert tools._call_local_grader.call_args.kwargs.get("tier", "haiku") == "haiku"
        tools._call_grader.assert_not_called()

    def test_worker_health_asks_the_sonnet_class(self, tools, registry, mock_tmux):
        """C11: worker health is a Sonnet-level call."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        mock_tmux.capture_pane.return_value = "Editing src/main.py\n"
        tools._call_local_grader = MagicMock(return_value={
            "healthy": True, "diagnosis": "ok", "severity": "none"})
        tools.evaluate_worker_health("w1")
        assert tools._call_local_grader.call_args.kwargs.get("tier") == "sonnet"

    def test_send_to_worker_first_choice_is_haiku_class(self, tools, registry, mock_tmux):
        """C8 positive control: the first-choice call stays at the Haiku class."""
        registry.register_worker("w1", "claude-sonnet", "ic-w1", repo="/tmp")
        _mock_local_grader_approve(tools)
        tools.send_to_worker("w1", "How is the design going?")
        assert tools._call_local_grader.call_args.kwargs.get("tier", "haiku") == "haiku"
```

`commander/tests/test_orchestrator_mcp.py`:

| Test | Change |
|---|---|
| `test_post_message_ollama_failure_escalates_to_opus` (`:3998`) | Rename to `test_post_message_ollama_failure_escalates_to_sonnet`; docstring "Infrastructure error from the Haiku class steps up to the Sonnet class."; add `assert tools._call_grader.call_args.kwargs.get("tier") == "sonnet"` after `:4009`. |
| `TestSpawnWorkersBatch`-area `test_batch_single_request_works` (`:6507`) | Add `assert tools._call_grader.call_args.kwargs.get("tier") == "sonnet"` after `:6518`. |
| `test_batch_grader_fallback` (`:6520`) | Its `mock_grader(system_prompt, user_prompt, batch=False)` (`:6528`) takes no `tier`, so the new call would raise `TypeError`. Change it to `def mock_grader(system_prompt, user_prompt, batch=False, tier=None):` with `tiers.append(tier)` as its first line, and `tiers = []` next to `call_count = [0]`. After `:6540` add `assert tiers and all(t == "sonnet" for t in tiers)`. |

In `TestCallLocalGraderDelegation` (`:9185`), after `test_delegates_to_local_grader` (which stays unchanged and still pins exactly three positional args), add:
```python

    def test_forwards_only_a_non_haiku_class(self, tools):
        mock_grade = MagicMock(return_value={"healthy": True})
        tools._local_grader = MagicMock()
        tools._local_grader.grade = mock_grade
        schema = {"type": "object"}
        tools._call_local_grader("s", "u", schema, tier="sonnet")
        mock_grade.assert_called_once_with("s", "u", schema, tier="sonnet")
        mock_grade.reset_mock()
        tools._call_local_grader("s", "u", schema, tier="haiku")
        mock_grade.assert_called_once_with("s", "u", schema)
```

`commander/tests/test_kill_worker_log_cap.py` `test_kill_worker_grades_when_log_read_fails`: after `tools._call_grader.assert_called_once()` (`:158`) add `assert tools._call_grader.call_args.kwargs.get("tier") is None` (C12 keeps the default Opus class).

Append to `commander/tests/test_grader_routing.py`:
```python


# ── Requested model class (design T4): resolved per client through the router ──

def test_sonnet_class_claude_uses_bare_sonnet(tmp_path):
    tools = _tools(tmp_path)
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "A", "approved": True, "feedback": "ok"})) as run:
        assert tools._call_grader("sys", "user", tier="sonnet")["grade"] == "A"
    argv = run.call_args.args[0]
    assert argv[0] == "claude"
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_sonnet_class_codex_uses_terra(tmp_path, monkeypatch):
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    with patch.object(omcp.subprocess, "run", return_value=_codex_proc("codex_grader_verdict.jsonl")) as run:
        assert tools._call_grader("sys", "user", tier="sonnet")["grade"] == "B"
    argv = run.call_args.args[0]
    assert argv[0] == "codex"
    assert argv[argv.index("-m") + 1] == "gpt-5.6-terra"


def test_default_class_codex_is_opus_class_sol(tmp_path, monkeypatch):
    """C7/C12 positive control: no requested class -> the opus class, Sol on Codex."""
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    with patch.object(omcp.subprocess, "run", return_value=_codex_proc("codex_grader_verdict.jsonl")) as run:
        assert tools._call_grader("sys", "user")["grade"] == "B"
    argv = run.call_args.args[0]
    assert argv[argv.index("-m") + 1] == "gpt-5.6-sol"


def test_legacy_config_sonnet_class_uses_bare_sonnet(tmp_path):
    """No providers block: the requested class still reaches the legacy claude path."""
    conn = init_db(str(tmp_path / "commander.db"))
    tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                                   db_conn=conn, config={"grader_model": "opus"})
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "B", "approved": True, "feedback": "ok"})) as run:
        tools._call_grader("sys", "user", tier="sonnet")
    argv = run.call_args.args[0]
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_sonnet_class_batch_keeps_batch_schema(tmp_path):
    tools = _tools(tmp_path)
    verdicts = [{"grade": "A", "approved": True, "feedback": "ok"}]
    with patch.object(omcp.subprocess, "run", return_value=_fake_result({"verdicts": verdicts})) as run:
        out = tools._call_grader("sys", "user", batch=True, tier="sonnet")
    assert out == verdicts
    argv = run.call_args.args[0]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "verdicts" in argv[argv.index("--json-schema") + 1]
```

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_grader_routing.py tests/test_local_grader.py tests/test_orchestrator_mcp.py tests/test_kill_worker_log_cap.py -q > /private/tmp/claude-502/pct-t4-red.log 2>&1; echo "red=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t4-red.log; tail -n 3 /private/tmp/claude-502/pct-t4-red.log
```
Expected: `red=1`. These fail:
- with `TypeError` (unexpected keyword `tier`): `test_sonnet_class_claude_uses_bare_sonnet`, `test_sonnet_class_codex_uses_terra`, `test_legacy_config_sonnet_class_uses_bare_sonnet`, `test_sonnet_class_batch_keeps_batch_schema`, `test_forwards_only_a_non_haiku_class`;
- with `AssertionError` (`None == 'sonnet'`): the eight `test_local_grader.py` escalation tests in the table above, `test_send_keys_infrastructure_error_steps_up_to_sonnet`, `test_worker_health_asks_the_sonnet_class`, `test_post_message_ollama_failure_escalates_to_sonnet`, `test_batch_single_request_works`, `test_batch_grader_fallback`.

These pass at RED (they guard the unchanged behavior): `test_default_class_codex_is_opus_class_sol`, `test_approve_plan_uses_opus_grader`, `test_kill_worker_grades_when_log_read_fails`, `test_send_keys_haiku_answer_does_not_escalate`, `test_send_to_worker_first_choice_is_haiku_class`, `test_delegates_to_local_grader`. Record the measured list.

### Step 2: Implementation (GREEN) in `orchestrator_mcp.py`

1. `_call_grader` signature (`:1198`): `def _call_grader(self, system_prompt: str, user_prompt: str, batch: bool = False, *, tier: str | None = None) -> dict | list:`
2. Docstring: after the paragraph ending `No usable capability -> grade F.` add:
```
        ``tier`` requests a model class (haiku|sonnet|opus|fable), resolved per client
        through the same router: each client's ``models.<tier>`` (Claude ``sonnet``;
        Codex ``gpt-5.6-terra`` by default). ``None`` keeps the configured grader class
        (``grader_model``, the opus class by default: Claude Opus, or Codex Sol).
```
3. Routing preamble (`:1232-1248`):
   - Before `try:` (`:1232`) add `requested_tier = tier`.
   - `:1233` becomes `tier = requested_tier or _semantic_tier(self._grader_model, "grader_model", "opus")`.
   - In `except ProviderConfigError:` (`:1245-1248`), `grader_client, grader_model = "claude", self._grader_model` becomes `grader_client = "claude"` plus `grader_model = self._grader_model if requested_tier is None else requested_tier`. Update the comment to `# No provider config -> legacy claude grader (byte-identical when no class is requested).`
4. `_call_local_grader` (`:1340-1348`): signature `def _call_local_grader(self, system_prompt: str, user_prompt: str, format_schema: dict, *, bounded: bool = False, tier: str = "haiku") -> dict:`; append to the docstring `tier is the call's model class (design T1); it is forwarded only when not "haiku", so default calls keep their exact three-argument form.`; body:
```python
        grader = self._message_grader if bounded else self._local_grader
        if tier == "haiku":
            return grader.grade(system_prompt, user_prompt, format_schema)
        return grader.grade(system_prompt, user_prompt, format_schema, tier=tier)
```
5. C5 `spawn_worker` (`:4851-4873`): the comment becomes `# Hybrid grading: Haiku-class pre-filter, Sonnet-class escalation (design T3)`; both `self._call_grader(system_prompt, user_prompt)` calls (`:4860`, `:4869`) gain `, tier="sonnet"`; in the three log strings replace `escalating to Opus` with `escalating to the Sonnet class` and `skipping Opus` with `skipping escalation`.
6. C6 `spawn_workers`: in the log strings at `:5720`, `:5728`, `:5731` make the same two replacements; the comment `# Phase 2: Batch uncertain requests to Opus` (`:5735`) becomes `# Phase 2: Batch uncertain requests to the Sonnet class`; `:5785` becomes `self._call_grader(opus_system_prompt, user_prompt, batch=True, tier="sonnet")`; the individual call at `:5795` gains `, tier="sonnet"` before its closing parenthesis (after the `f"objective: {req['objective']}"` argument). Variable names `opus_*` stay unchanged.
7. C8 `send_to_worker` (`:6254-6255`): log `escalating to Opus` → `escalating to the Sonnet class`; `self._call_grader(system_prompt, user_prompt, tier="sonnet")`.
8. C11 `evaluate_worker_health` (`:6333`): `self._call_local_grader(system_prompt, user_prompt, self._LOCAL_HEALTH_SCHEMA, tier="sonnet")`.
9. C9 `send_keys_to_worker` (`:6390-6391`): same change as C8.
10. C10 `post_message` (`:7495-7496`): same change as C8.
11. C7 `approve_plan` (`:6165`) and C12 `kill_worker` (`:6996`): no change.

**`notifications.py` (T7).** Replace the docstring line `:279` with `Fail-safe: a missing/unparseable config resolves to the Commander default backend (haiku) -> 'Haiku'.` No code change.

### Step 3: GREEN for the touched files

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_grader_routing.py tests/test_local_grader.py tests/test_orchestrator_mcp.py tests/test_kill_worker_log_cap.py tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_notifications.py -q > /private/tmp/claude-502/pct-t4-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/pct-t4-green.log
```
Expected: `green=0`, with 0 failed.

### Step 4: Full Commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/pct-t4-suite.log 2>&1; echo "suite=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t4-suite.log; tail -n 3 /private/tmp/claude-502/pct-t4-suite.log
```
Expected: `suite=0`, 0 failed, and no `FAILED`/`ERROR` lines. Any failure must be fixed; if it cannot be, show it pre-existing in a temporary `git worktree` of `HEAD` with the staged diff applied (never `git stash`) and STOP to report it.

### Step 5: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/notifications.py commander/tests/test_grader_routing.py commander/tests/test_local_grader.py commander/tests/test_orchestrator_mcp.py commander/tests/test_kill_worker_log_cap.py commander/tests/test_notifications.py
```
Expected: no output.

---

## Task 5: Worker advisors — Opus-class workers get an Opus-class advisor (T8)

TDD.

**Files:**
- Modify: `commander/src/ironclaude/config.py` (`:76`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (after `:209`; `:534-540`; `:5326-5329`; `:6018-6021`)
- Modify: `commander/tests/test_config.py` (`:100-106`)
- Modify: `commander/tests/test_orchestrator_mcp.py` (`:1738` area in `TestRemoteProviderLaunching`; `:1947-1964`; after `:6859` in `TestProviderAwareBatchLifecycle`; `TestAdvisorModelFor` `:7850-7872`; `TestCodexAdvisorInstructionConstant` `:11439-11459`)
- Modify: `commander/tests/test_main_validate.py` (`:345-362`; append to `TestSpawnWorkerAdvisorTiering`)
- Modify: `commander/tests/test_provider_config.py` (append)

`main.py` (`:4113-4117`), `provider_config.advisor_model_for` (`:83-90`) and `fable_availability.resolve_advisor_model` need no code change: they read the configured `advisor_models`, so the new default reaches them. The tests below prove that for each site.

### Step 1: Tests (RED)

Add `import copy` and `from ironclaude.config import DEFAULTS` to the imports of `test_orchestrator_mcp.py`, `test_main_validate.py` and `test_provider_config.py` where absent. Reference the new Codex constant and helper through the module (`import ironclaude.orchestrator_mcp as omcp` inside the test), never in a module-level `from … import`, so a missing name fails only its own tests.

**Existing tests whose assertions change.**

| Test | Change |
|---|---|
| `test_config.py::TestLoadConfig::test_advisor_models_map_default` (`:100-106`) | Docstring `"""advisor_models maps each worker type to its advisor class: opus class for both (design T8)."""`; expected dict becomes `{"claude-sonnet": "opus", "claude-opus": "opus"}`. |
| `test_orchestrator_mcp.py::test_spawn_worker_opus_sends_tiered_advisor_fable` (`:1947`) | Rename to `test_spawn_worker_opus_explicit_fable_advisor_is_honored`; docstring `"""An explicit advisor_models override to fable is still honored (design T8)."""`. Assertions unchanged. |
| `test_main_validate.py::test_claude_opus_advisor_is_fable` (`:349`) | Rename to `test_claude_opus_explicit_fable_advisor_is_honored`. Assertions unchanged. |

**New tests.**

In `TestAdvisorModelFor` (after `:7872`):
```python

    def test_defaults_give_opus_class_workers_an_opus_class_advisor(self, tools):
        tools._advisor_cfg = copy.deepcopy(DEFAULTS["advisor"])
        assert tools._advisor_model_for("claude-opus") == "opus"
        assert tools._advisor_model_for("claude-sonnet") == "opus"
```

After `test_spawn_worker_opus_explicit_fable_advisor_is_honored` (in the same class as `:1947`):
```python

    def test_spawn_worker_opus_default_advisor_is_opus(self, registry, mock_tmux, tmp_path, db_conn):
        """With the default advisor config a claude-opus worker gets /advisor opus (design T8)."""
        tools = OrchestratorTools(
            registry, mock_tmux, str(tmp_path / "task-ledger.json"), db_conn=db_conn,
            advisor_cfg=copy.deepcopy(DEFAULTS["advisor"]),
        )
        tools._activate_pm_via_sqlite = MagicMock(return_value=None)
        _mock_grader_approve(tools)
        with patch("ironclaude.orchestrator_mcp.time.sleep"):
            tools.spawn_worker(worker_id="w-tier-opus-default", worker_type="claude-opus",
                               repo="/tmp/repo", objective="Do the thing")
        keys_sent = [call[0][1] for call in mock_tmux.send_keys.call_args_list]
        assert "/advisor opus" in keys_sent
        assert "/advisor fable" not in keys_sent
```

In `TestCodexAdvisorInstructionConstant` (after `:11459`):
```python

    def test_same_tier_variant_differs_only_in_review_tier(self):
        import ironclaude.orchestrator_mcp as omcp
        same = omcp._CODEX_ADVISOR_INSTRUCTION_SAME_TIER
        assert "\n" not in same
        assert 'review_tier: "same"' in same
        assert 'review_tier: "one-up"' not in same
        assert same == omcp._CODEX_ADVISOR_INSTRUCTION.replace('review_tier: "one-up"', 'review_tier: "same"')

    def test_codex_directive_follows_the_advisor_class(self, tools):
        import ironclaude.orchestrator_mcp as omcp
        tools._advisor_cfg = copy.deepcopy(DEFAULTS["advisor"])
        # Opus-class worker, opus-class advisor: Sol reviews Sol.
        assert tools._codex_advisor_instruction("claude-opus") == omcp._CODEX_ADVISOR_INSTRUCTION_SAME_TIER
        # Sonnet-class worker, opus-class advisor: Terra -> Sol is one class up.
        assert tools._codex_advisor_instruction("claude-sonnet") == omcp._CODEX_ADVISOR_INSTRUCTION
        # Explicit top-tier override is honored: Sol -> Astra.
        tools._advisor_cfg = {"enabled": True, "advisor_model": "opus",
                              "advisor_models": {"claude-opus": "fable"}}
        assert tools._codex_advisor_instruction("claude-opus") == omcp._CODEX_ADVISOR_INSTRUCTION
```

In `TestProviderAwareBatchLifecycle`, after `test_batch_codex_uses_provider_lifecycle_and_persists_provider` (ends `:6859`):
```python

    def test_batch_codex_opus_worker_gets_same_tier_advisor_by_default(
        self, tools, registry, mock_tmux,
    ):
        import ironclaude.orchestrator_mcp as omcp
        tools._resolve_worker_client = MagicMock(return_value=_provider_handle(
            requested="opus", effective="opus", model="gpt-5.6-sol"))
        tools._advisor_cfg = copy.deepcopy(DEFAULTS["advisor"])
        result = tools.spawn_workers([self._request(worker_type="claude-opus")])
        messages = [call.args[1] for call in mock_tmux.send_keys.call_args_list]
        assert messages == [
            "$ironclaude:write-lossless-ai-messages",
            omcp._CODEX_ADVISOR_INSTRUCTION_SAME_TIER,
            "Objective w1",
        ]
        assert result[0]["status"] == "spawned"
```

In `TestRemoteProviderLaunching`, after `test_remote_client_command_gate_preserves_claude_only_slash_commands` (ends `:1738`), add a test that copies that test's body for the Codex branch only, with these differences: name `test_remote_codex_opus_worker_gets_same_tier_advisor_by_default`; no `client` parameter (use `client = "codex"`); `advisor_cfg=copy.deepcopy(DEFAULTS["advisor"])`; `_provider_handle(client="codex", requested="opus", effective="opus", model="gpt-5.6-sol")`; `worker_type="claude-opus"`; and the final assertion
```python
        import ironclaude.orchestrator_mcp as omcp
        assert sent == [
            "$ironclaude:write-lossless-ai-messages",
            omcp._CODEX_ADVISOR_INSTRUCTION_SAME_TIER,
            "Only objective",
        ]
```

Append to `TestSpawnWorkerAdvisorTiering` in `test_main_validate.py`:
```python

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="OPUS_CMD")
    def test_claude_opus_default_advisor_is_opus(self, mock_make, mock_trust, mock_fmt, mock_logev):
        config = {"effort_level": "high", "advisor": copy.deepcopy(DEFAULTS["advisor"]),
                  "dispatch": {"use_goal": False}}
        daemon = self._spawn_daemon(config)
        daemon._handle_spawn_worker({"worker_id": "w-opus-default", "type": "claude-opus",
                                     "repo": "/tmp", "objective": "task"})
        assert self._advisor_sends(daemon) == ["/advisor opus"]
```

Append to `test_provider_config.py`:
```python


def test_default_advisor_class_for_claude_opus_worker_is_opus():
    cfg = provider_config_from_commander(copy.deepcopy(DEFAULTS))
    assert cfg.advisor_model_for("claude", "claude-opus", "opus") == "opus"


def test_default_advisor_class_for_codex_opus_worker_is_sol():
    cfg = provider_config_from_commander(copy.deepcopy(DEFAULTS))
    assert cfg.advisor_model_for("codex", "claude-opus", "opus") == "gpt-5.6-sol"
```

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_config.py tests/test_orchestrator_mcp.py tests/test_main_validate.py tests/test_provider_config.py -q > /private/tmp/claude-502/pct-t5-red.log 2>&1; echo "red=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t5-red.log; tail -n 3 /private/tmp/claude-502/pct-t5-red.log
```
Expected: `red=1`, no collection error. These fail:
- with `AssertionError` showing `fable`: `test_advisor_models_map_default`, `test_defaults_give_opus_class_workers_an_opus_class_advisor`, `test_spawn_worker_opus_default_advisor_is_opus` (`/advisor fable` was sent), `test_claude_opus_default_advisor_is_opus` (`['/advisor fable']`), `test_default_advisor_class_for_claude_opus_worker_is_opus`;
- with `AttributeError` (no `_CODEX_ADVISOR_INSTRUCTION_SAME_TIER` / `_codex_advisor_instruction`): `test_same_tier_variant_differs_only_in_review_tier`, `test_codex_directive_follows_the_advisor_class`, `test_batch_codex_opus_worker_gets_same_tier_advisor_by_default`, `test_remote_codex_opus_worker_gets_same_tier_advisor_by_default`.

These pass at RED: `test_default_advisor_class_for_codex_opus_worker_is_sol` (the Codex class already follows the requested tier), the two renamed explicit-fable tests, and every other existing advisor test. Record the measured list.

### Step 2: Implementation (GREEN)

**`config.py`** `:76` becomes:
```python
        "advisor_models": {"claude-sonnet": "opus", "claude-opus": "opus"},  # advisor class per worker type (design T8): opus class for both; the top tier (fable) only by explicit config
```

**`orchestrator_mcp.py`**
1. After `_CODEX_ADVISOR_INSTRUCTION` (`:197-209`), add:
```python

# Same-tier variant (design T8). The broker's "one-up" maps Sol to Astra, so an
# Opus-class worker on Codex whose advisor class is also Opus asks for "same"
# (Sol reviews Sol). Single line, like the constant above.
_CODEX_ADVISOR_INSTRUCTION_SAME_TIER = _CODEX_ADVISOR_INSTRUCTION.replace(
    'review_tier: "one-up"', 'review_tier: "same"'
)
```
   The constant's text contains `review_tier: "one-up"` exactly once (`:201`).
2. `_advisor_model_for` docstring (`:535-539`): replace "Looks up worker_type in advisor_models (one-tier-up map)" with "Looks up worker_type in advisor_models (advisor class per worker type; design T8: opus class by default, the top tier only by explicit config)".
3. After `_advisor_model_for`, add:
```python

    def _codex_advisor_instruction(self, worker_type: str) -> str:
        """Codex advisor directive for this worker (design T8). Same rule as Claude:
        when the worker's class equals its advisor's class (the default for Opus-class
        workers), ask the broker for review_tier "same" so Sol is not mapped to Astra;
        otherwise (Sonnet -> Opus, or an explicit top-tier override) keep "one-up"."""
        worker_class = _WORKER_TYPE_TIER.get(worker_type)
        advisor_class = _semantic_tier(self._advisor_model_for(worker_type), "advisor_model", "opus")
        if worker_class is not None and worker_class == advisor_class:
            return _CODEX_ADVISOR_INSTRUCTION_SAME_TIER
        return _CODEX_ADVISOR_INSTRUCTION
```
4. Single spawn `:5327-5329`: `self.tmux.send_keys(session_name, _CODEX_ADVISOR_INSTRUCTION, ssh_host=ssh_host,)` becomes `self.tmux.send_keys(session_name, self._codex_advisor_instruction(effective_worker_type), ssh_host=ssh_host,)`.
5. Batch spawn `:6019-6021`: `_CODEX_ADVISOR_INSTRUCTION,` becomes `self._codex_advisor_instruction(effective_worker_type),`.

### Step 3: GREEN plus full suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_config.py tests/test_orchestrator_mcp.py tests/test_main_validate.py tests/test_provider_config.py -q > /private/tmp/claude-502/pct-t5-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/pct-t5-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/pct-t5-suite.log 2>&1; echo "suite=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t5-suite.log; tail -n 3 /private/tmp/claude-502/pct-t5-suite.log
```
Expected: `green=0` and `suite=0`, each with 0 failed and no `FAILED`/`ERROR` lines.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/config.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_config.py commander/tests/test_orchestrator_mcp.py commander/tests/test_main_validate.py commander/tests/test_provider_config.py
```
Expected: no output.

---

## Task 6: Advisor Fallback directive follows the class rule on every surface (T10)

TDD (pin tests first). Edit ONLY the Advisor Fallback text on each surface; the repo `CLAUDE.md`, `.claude/rules/behavioral.md` and `AGENTS.md` are the operator's instruction files, so nothing else in them changes. Directive headings stay unchanged (`test_instruction_surface_parity.py` pins them).

**Files:**
- Modify: `CLAUDE.md` (directive 10 bullets `:58-59` only)
- Modify: `.claude/rules/behavioral.md` (directive 10 bullets `:58-59` only)
- Modify: `AGENTS.md` (directive 10 bullets `:57` and `:59` only)
- Modify: `worker/skills/advisor-fallback/SKILL.md` (`:3`, `:6`, `:10`, `:13`, `:32`, `:38`, `:47-50`, `:63-64`, `:76-80`)
- Modify: `worker/skills/activate-professional-mode/SKILL.md` (`:317`, `:388`, `:454-455`, `:493`, `:518-519`)
- Modify: `commander/src/ironclaude/templates/worker_claude_md.md` (`:52-53`)
- Modify: `commander/src/ironclaude/templates/worker_agents.md` (`:52`)
- Modify: `commander/tests/test_advisor_fallback_directive.py`
- Modify: `commander/tests/test_activation_client_parity.py` (`:120-136`, `:413-427`)
- Modify: `commander/tests/test_worker_claude_md_template.py` (`:94-101`)

**The new text.** Use these exact lines; every surface of the same shape gets the same bytes.

*Codex canonical bullet* (replaces the `Call \`run_codex_advisor_review\` …` bullet in `worker_agents.md:52` and activation `:317`):
```
   - Call `run_codex_advisor_review` with the complete inline review packet, current `requester_model`, and `review_tier: "one-up"` below the Opus class (from Luna or Terra) or `review_tier: "same"` from Sol, so the reviewer is one class up and capped at the Opus class; omission remains compatibility-only. The broker rejects any mismatch with provider-authenticated Codex turn metadata and applies its fixed `luna → terra → sol → astra` mapping (`gpt-<version>-luna → gpt-<version>-terra → gpt-<version>-sol → gpt-<version>-astra`) exactly once, so Astra is used only on explicit operator request.
```

*Claude canonical bullets* (replace the "Spawn a top-tier subagent …" and "Client-aware …" bullets in `worker_claude_md.md:52-53`, activation full template `:454-455` and Concept 9 `:518-519`):
```
   - Spawn a fresh Opus-class subagent via the `Agent` tool (`model=opus`; `model=sonnet` from a Haiku session; `model=fable` only when the operator explicitly requests the top tier) with the same context and a focused, report-only adversarial-review prompt (task, change/decision, evidence, specific questions)
   - Client-aware: that is the Claude path; a Codex session calls `run_codex_advisor_review` with a complete inline packet, its current `requester_model`, and `review_tier: "one-up"` below the Opus class or `review_tier: "same"` from Sol (omission is compatibility-only); the broker rejects any mismatch with provider-authenticated Codex turn metadata and owns the one-time `luna→terra→sol→astra` mapping (`gpt-<version>-luna→gpt-<version>-terra→gpt-<version>-sol→gpt-<version>-astra`), so Astra is used only on explicit operator request — see the `ironclaude:advisor-fallback` skill
```

*Activation compact index* `:388` becomes:
```
9. **Advisor Fallback** — If the `advisor` tool is unavailable, spawn a fresh Opus-class subagent (`Agent`, `model=opus`; `model=fable` only when the operator explicitly requests the top tier) to do the same adversarial review; never skip the advisor step.
```

*Activation concept table row* `:493`, third cell becomes: `Instructions to use an \`Agent\`-tool subagent one class up, capped at the Opus class (the top tier only on explicit operator request), instead of skipping advisor review`.

*Repo `CLAUDE.md` and `.claude/rules/behavioral.md`, directive 10* — replace bullets `:58` and `:59` (both files identically) with:
```
   - Spawn a fresh Opus-class subagent to perform the same role: dispatch it via the `Agent` tool — `model=opus` (`model=sonnet` from a Haiku session); use `model=fable` only when the operator explicitly requests the top tier. Give it the same context and a focused, report-only adversarial-review prompt — the task, the change or decision, the evidence, and the specific questions to pressure-test.
   - **Client-aware:** the above is the Claude path. A Codex session has no `Agent` tool or Claude models — it calls `run_codex_advisor_review` with a complete inline packet, its current `requester_model`, and `review_tier: "one-up"` below the Opus class or `review_tier: "same"` from Sol; the broker rejects any mismatch with provider-authenticated Codex turn metadata and owns the one-time `luna→terra→sol→astra` mapping, so Astra is used only on explicit operator request. See the `ironclaude:advisor-fallback` skill for both branches.
```

*Root `AGENTS.md`, directive 10*: in bullet `:57` replace `a manual tiered-up adversarial reviewer` with `a manual adversarial reviewer one class up, capped at the Opus class,`. Replace bullet `:59` with the Codex canonical bullet text above (four-space indent, as the surrounding bullets use) followed by ` Keep the reviewer a Codex model — Codex is its own advisor peer.`

*`worker/skills/advisor-fallback/SKILL.md`*:
1. `:3` description: `Client-aware adversarial advisor one class up, capped at the Opus class (Claude subagent / fixed-function Codex broker), fired at natural discretionary advisor points`.
2. `:6` title: `# Advisor Fallback (manual class-capped advisor)`.
3. `:10` `**tiered-up adversarial reviewer**` → `**adversarial reviewer one class up, capped at the Opus class**`.
4. `:13` `dispatch a fresh, blind, one-tier-up reviewer over the work` → `dispatch a fresh, blind reviewer one class up, capped at the Opus class, over the work (the top tier — Fable or Astra — only when the operator explicitly requests it)`.
5. `:32` `Resolve the **one-tier-up** reviewer model` → `Resolve the reviewer model (one class up, capped at the Opus class)`.
6. Replace `:47-50` (the **Claude tier ladder** paragraph) with: `**Claude reviewer:** \`haiku → sonnet\`, \`sonnet → opus\`, \`opus → opus\` (a fresh, blind same-class pass), \`fable → opus\`. Use \`model=fable\` only when the operator explicitly requests the top tier; if Fable is then unavailable, use \`opus\` (read the flag at \`IRONCLAUDE_FABLE_STATE_PATH\` if set else \`~/.ironclaude/state/fable_unavailable.json\`; on any read error treat Fable as available).`
7. Replace `:63-64` (the `review_tier` bullet) with: ``- `review_tier: "one-up"` from a Luna or Terra session; `review_tier: "same"` from a Sol or Astra session (Sol reviews Sol); `review_tier: "one-up"` from Sol (Astra) only when the operator explicitly requests the top tier. The selector is bounded to `"same"` or `"one-up"`; omission defaults to `"one-up"` only for compatibility.``
8. `:76`: `**Codex tier ladder (broker-owned, one mapping only):**` → `**Codex broker mapping for \`"one-up"\` (broker-owned, one mapping only):**`; at the end of the sentence ending `**\`gpt-6-sol\` → \`gpt-6-astra\`**.` (`:80`) add ` That is why a Sol session asks for \`"same"\`.`
9. `:38`: replace ``` `model=<one-tier-up>`), giving it ``` with ``` `model=opus`, or `model=sonnet` from a Haiku session — see **Claude reviewer** below), giving it ```.

### Step 1: Pin tests (RED)

**`test_advisor_fallback_directive.py`:**
1. Module docstring: replace "spelling out the TIER-RELATIVE fallback (Fable if available, else Opus)" with "spelling out the class-capped fallback (one class up, capped at the Opus class; the top tier only on explicit operator request)".
2. Replace the constants `PRIMARY`/`FALLBACK` (`:13-14`) with:
```python
OPUS_CLASS = "model=opus"   # the substitute reviewer (Opus class)
TOP_TIER = "model=fable"    # named only as the explicit-operator-request exception
TOP_TIER_RULE = "explicitly requests the top tier"
RETIRED = "if Fable is available"
SAME_TIER = 'review_tier: "same"'
```
   and in every test replace `PRIMARY` with `TOP_TIER` and `FALLBACK` with `OPUS_CLASS` (the brain test keeps asserting both are absent).
3. `test_directive_in_canonical_behavioral`: add `assert TOP_TIER_RULE in text`, `assert SAME_TIER in text`, `assert RETIRED not in text`.
4. Add:
```python


def test_directive_in_root_claude_md_matches_behavioral():
    claude = _read("CLAUDE.md")
    rules = _read(".claude/rules/behavioral.md")

    def block(text):
        return text.split("10. **Advisor Fallback", 1)[1].split("11. **No Workflow", 1)[0]

    assert block(claude) == block(rules)
    assert OPUS_CLASS in block(claude) and TOP_TIER_RULE in block(claude)
    assert SAME_TIER in block(claude)
    assert RETIRED not in block(claude)
```
5. `test_directive_in_agents_md`: add `assert SAME_TIER in text`, `assert "capped at the Opus class" in text`, `assert "only on explicit operator request" in text`, `assert "tiered-up" not in text`.
6. `test_directive_in_activation_skill`: add `assert RETIRED not in text`, `assert "Fable if available" not in text`, `assert text.count(SAME_TIER) >= 3`, `assert text.count(TOP_TIER_RULE) >= 3`.
7. `test_codex_advisor_ladder_names_astra_requester_and_ceiling_without_claiming_fable`: add `assert SAME_TIER in text`, `assert "capped at the Opus class" in text`, `assert "opus → fable" not in text`, `assert "one-tier-up" not in text`.

**`test_activation_client_parity.py`:** replace the bullet lines of `CANONICAL_CODEX_ADVISOR_BODY` (`:122`) and `CANONICAL_CLAUDE_ADVISOR_BODY` (`:134-135`) with the Codex and Claude canonical bullets above (escape each `"` as `\"`, as the current constants do). In `test_activation_uses_provider_native_state_manager_names_and_advisors` (`:413-427`) add `assert 'review_tier: "same"' in codex`, `assert 'review_tier: "same"' in claude`, `assert "explicitly requests the top tier" in claude`, `assert "if Fable is available" not in claude`.

**`test_worker_claude_md_template.py`:** `:94` becomes `assert "`Agent` tool (`model=opus`" in claude`; add `assert "`model=fable` only when the operator explicitly requests the top tier" in claude`, `assert 'review_tier: "same"' in claude`, `assert 'review_tier: "same"' in codex`.

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py tests/test_activation_client_parity.py tests/test_worker_claude_md_template.py tests/test_instruction_surface_parity.py tests/test_executing_plans_skill.py -q > /private/tmp/claude-502/pct-t6-red.log 2>&1; echo "red=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t6-red.log; tail -n 3 /private/tmp/claude-502/pct-t6-red.log
```
Expected: `red=1`, no collection error. These fail with `AssertionError`: `test_directive_in_canonical_behavioral`, `test_directive_in_root_claude_md_matches_behavioral` (the blocks are equal today, but `TOP_TIER_RULE` is absent), `test_directive_in_agents_md`, `test_directive_in_activation_skill`, `test_codex_advisor_ladder_names_astra_requester_and_ceiling_without_claiming_fable`, `test_activation_contains_full_independent_canonical_bodies`, `test_activation_uses_provider_native_state_manager_names_and_advisors`, `test_worker_templates_have_semantic_concept_parity`. These pass: `test_directive_in_brain_behavioral`, the two template byte-equality tests (templates and activation are still equal), every `test_instruction_surface_parity.py` and `test_executing_plans_skill.py` test. Record the measured list.

### Step 2: Edit the surfaces (GREEN)

Apply the new text above to every listed surface. Keep `worker_claude_md.md` byte-identical to the activation Claude full-template block and `worker_agents.md` byte-identical to the activation Codex canonical block (`test_worker_claude_md_template.py:43-62` compares them).

### Step 3: GREEN plus agreement with T8 and the full suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py tests/test_activation_client_parity.py tests/test_worker_claude_md_template.py tests/test_instruction_surface_parity.py tests/test_executing_plans_skill.py tests/test_communication_instruction_surfaces.py tests/test_boy_scout_directive.py tests/test_orchestrator_mcp.py -q > /private/tmp/claude-502/pct-t6-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/pct-t6-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/pct-t6-suite.log 2>&1; echo "suite=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t6-suite.log; tail -n 3 /private/tmp/claude-502/pct-t6-suite.log; grep -rn -e "if Fable is available" -e "Fable if available" -e "top-tier subagent" -e "tiered-up" -e "one-tier-up" -e "opus → fable" /Users/roberthyatt/Code/ironclaude/CLAUDE.md /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/.claude/rules/behavioral.md /Users/roberthyatt/Code/ironclaude/worker/skills/advisor-fallback/SKILL.md /Users/roberthyatt/Code/ironclaude/worker/skills/activate-professional-mode/SKILL.md /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/templates > /private/tmp/claude-502/pct-t6-retired.log 2>&1; echo "retired=$?"; cat /private/tmp/claude-502/pct-t6-retired.log
```
Expected:
- `green=0` and `suite=0`, each with 0 failed and no `FAILED`/`ERROR` lines.
- `retired=1` with an empty log: no surface still carries the old wording.
- Agreement with T8: for an Opus-class Codex worker with the default advisor config, the per-spawn directive is `_CODEX_ADVISOR_INSTRUCTION_SAME_TIER` (`review_tier: "same"`), which matches the templates' "`review_tier: "same"` from Sol"; a Sonnet-class worker gets `"one-up"` (Terra → Sol), matching "`review_tier: "one-up"` below the Opus class"; an explicit `"claude-opus": "fable"` config is the explicit operator request that allows Sol → Astra. Task 5's tests already pin all three; `test_orchestrator_mcp.py` above re-runs them.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add CLAUDE.md .claude/rules/behavioral.md AGENTS.md worker/skills/advisor-fallback/SKILL.md worker/skills/activate-professional-mode/SKILL.md commander/src/ironclaude/templates/worker_claude_md.md commander/src/ironclaude/templates/worker_agents.md commander/tests/test_advisor_fallback_directive.py commander/tests/test_activation_client_parity.py commander/tests/test_worker_claude_md_template.py
```
Expected: no output.

---

## Task 7: Docs — per-call class table, the backup rule, and worker advisors

No tests required: documentation.

**Files:**
- Modify: `worker/config-schema/llm-backend.md` (`:50-51`; new section before `## Out of scope` at `:237`)
- Modify: `README.md` (`:20`; after `:263`; `:302-311`; `:733-736`)

### Step 1: Edit `llm-backend.md`

1. Schema block: `"fast_model_timeout_seconds": number?` (`:51`) gains a trailing comma, followed by a new line `  "sonnet_model_timeout_seconds": number?`.
2. Insert before `## Out of scope` a section `## Per-call model classes`. It states:
   - **Classes are resolved per client.** Reproduce the three-row class table from this plan's header (haiku / sonnet / opus; Claude; Codex hooks newest installed Luna/Terra with fallbacks `gpt-6-luna` / `gpt-5.6-terra`; Codex Commander `providers.clients.codex.models.<class>`, defaults `gpt-5.6-luna` / `gpt-5.6-terra` / `gpt-5.6-sol`). The opus class on Codex is Sol. Fable and Astra are never used here.
   - **Which call uses which class.** The design T1 table, with columns ID, Call, Site, Class: H1 bypass, H3-H6 rigor/prediction/COA/evidence, H7 task-completion validator, C1 Brain-message validator, C2 awaiting-operator capture, C3 prompt detection, C4 permission-seeking, C8 send_to_worker, C9 send_keys_to_worker, C10 post_message, C13 session summaries, W1 episodic summarizer = haiku; H2 Stop-hook continuation judge and C11 worker health = sonnet; C5/C6 spawn screening = haiku pre-filter with sonnet escalation; C7 approve_plan and C12 kill_worker completion check = opus.
   - **Routing.** Haiku-class calls follow the "Haiku-class fast-model chain" above (local box first). Sonnet-class calls never use the local box, whatever `backend` says.
   - **Backup rule.** Fail-closed gates step up one class when their first choice cannot answer (infrastructure error, timeout, or unparseable or missing fields): C8, C9 and C10 go Haiku → Sonnet through the Commander grader, then grade F; C5/C6 escalate to Sonnet when the Haiku pre-filter is not "high" confidence or is unavailable, and a failed Sonnet escalation is F. C7 and C12 stay on the opus class; their failure is F, and nothing steps up from the opus class. Fail-open classifiers and hook checks (H1-H7, C1-C4, C11, C13) fail open after their first-choice class, with no step-up.
   - **The Sonnet cap.** `sonnet_model_timeout_seconds` (top level) caps each Sonnet-class call, default `<SONNET_MODEL_TIMEOUT_DEFAULT>`, measured as `max(30, ceil(2 × p95 of six concurrent lean Sonnet children))` (findings: `docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md`). It is separate from `fast_model_timeout_seconds`.
   - **Labels.** The hook's `VALIDATION_LLM_BACKEND` reads `sonnet` (Claude) or `terra:<slug>` (Codex) for a Sonnet-class call.

### Step 2: Edit `README.md`

0. `:20`: replace "picks the reviewer one tier up from the models Codex actually has installed" with "resolves the reviewer from the models Codex actually has installed (one class up below the Opus class, a fresh same-class reviewer from Sol)".

After the **Timeout.** paragraph (`:263`), add a paragraph:

**Per-call classes.** Each check runs at the class its difficulty needs. Most checks are Haiku-class. The Stop hook's continuation judge and the Commander's worker-health check are Sonnet-class (Claude Sonnet, or the newest Codex Terra) and never use a local box; `sonnet_model_timeout_seconds` (default `<SONNET_MODEL_TIMEOUT_DEFAULT>`) caps them. Plan approval and the kill-worker completion check use the Opus class (Claude Opus, or Codex Sol). When a blocking gate's first choice cannot answer, it steps up one class (Haiku to Sonnet) instead of jumping to Opus; non-blocking checks fail open. See `worker/config-schema/llm-backend.md` for the full table.

Then update the worker/advisor text (T8):
1. `:302`: "each worker gets an advisor **one tier up** for oversight:" becomes "each worker gets an **Opus-class** advisor for oversight (Claude Opus, or Codex Sol). The top tier (Fable, or Codex Astra) is used only when you configure it:".
2. Table row `:307` (`claude-opus`): the Advisor cell `Fable` becomes `Opus (Fable only if configured)`.
3. Tiering paragraph `:311`: replace "Combined with the one-tier-up advisors above — a Sonnet worker gets an Opus advisor, an Opus worker gets a Fable advisor — the system delivers **Fable-level capability on the hardest work without burning Fable tokens continuously**" with "Combined with the Opus-class advisors above — Sonnet and Opus workers both get an Opus advisor, since Opus 5.5 is close to Fable, and Fable is reached only through a `claude-fable` worker or an explicit `advisor.advisor_models` override — the system delivers **near-top capability on the hardest work without burning Fable tokens continuously**".
4. Configuration Reference `:733`: `Whether workers get a one-tier-up advisor session` becomes `Whether workers get an advisor session`.
5. `:735`: the default becomes `{"claude-sonnet": "opus", "claude-opus": "opus"}` and the description `Advisor class per worker type (opus class by default; set "fable" to use the top tier)`.

### Step 3: Verify

```bash
grep -n -e "## Per-call model classes" -e "sonnet_model_timeout_seconds" -e "gpt-5.6-sol" -e "terra:<slug>" /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md > /private/tmp/claude-502/pct-t7-schema.log 2>&1; echo "schema=$?"; cat /private/tmp/claude-502/pct-t7-schema.log; grep -n -e "Per-call classes" -e "sonnet_model_timeout_seconds" -e "Opus-class" -e '"claude-opus": "opus"' -e "one tier up" -e '"claude-opus": "fable"' -e "one-tier-up" /Users/roberthyatt/Code/ironclaude/README.md > /private/tmp/claude-502/pct-t7-readme.log 2>&1; echo "readme=$?"; cat /private/tmp/claude-502/pct-t7-readme.log
```
Expected:
- `schema=0`. `## Per-call model classes` appears once; `sonnet_model_timeout_seconds` appears at least twice (schema block and section); `gpt-5.6-sol` and `terra:<slug>` each appear at least once.
- `readme=0`. `Per-call classes` appears once; `sonnet_model_timeout_seconds` and `Opus-class` at least once; `"claude-opus": "opus"` once. `one tier up`, `"claude-opus": "fable"` and `one-tier-up` appear on no line.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/config-schema/llm-backend.md README.md
```
Expected: no output.

---

## Task 8: Full regression

No tests required beyond the runs below; this is the regression run.

**Files:**
- Stage only: `docs/plans/2026-10-16-per-call-difficulty-tiering-design.md`, `docs/plans/2026-10-16-per-call-difficulty-tiering.md`, `docs/plans/2026-10-16-per-call-difficulty-tiering.plan.json`, `docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md`

### Step 1: Commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/pct-t8-commander.log 2>&1; echo "commander=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/pct-t8-commander.log; tail -n 3 /private/tmp/claude-502/pct-t8-commander.log
```
Expected: `commander=0`, 0 failed, no `FAILED`/`ERROR` lines. Any failure: fix; if it cannot be fixed, show it pre-existing in a temporary `git worktree` of `HEAD` with the staged diff applied (never `git stash`) and STOP to report it.

### Step 2: Hook suites (sentinel first on PATH)

```bash
mkdir -p /private/tmp/claude-502/hfm-sentinel/bin && printf '#!/bin/bash\necho "$(pwd) $(basename "$0") $*" >> /private/tmp/claude-502/hfm-sentinel/calls.log\nexit 1\n' > /private/tmp/claude-502/hfm-sentinel/bin/claude && cp /private/tmp/claude-502/hfm-sentinel/bin/claude /private/tmp/claude-502/hfm-sentinel/bin/codex && chmod +x /private/tmp/claude-502/hfm-sentinel/bin/claude /private/tmp/claude-502/hfm-sentinel/bin/codex; rm -f /private/tmp/claude-502/hfm-sentinel/calls.log; PATH=/private/tmp/claude-502/hfm-sentinel/bin:$PATH make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/pct-t8-hooks.log 2>&1; echo "hooks=$?"; tail -n 3 /private/tmp/claude-502/pct-t8-hooks.log; ls -la /private/tmp/claude-502/hfm-sentinel/calls.log
```
Expected: `hooks=0`. If only the known flaky throttle checks fail, rerun once into `pct-t8-hooks-rerun.log` and report the flake. Record `calls.log`, if present.

### Step 3: Episodic-memory suite

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run > /private/tmp/claude-502/pct-t8-episodic.log 2>&1; echo "episodic=$?"; tail -n 5 /private/tmp/claude-502/pct-t8-episodic.log
```
Expected: `episodic=0`. Any failure must be shown pre-existing in a temporary `git worktree` of `HEAD` with the staged diff applied (never `git stash`), or else fixed.

### Step 4: Stage the plan docs

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-per-call-difficulty-tiering-design.md docs/plans/2026-10-16-per-call-difficulty-tiering.md docs/plans/2026-10-16-per-call-difficulty-tiering.plan.json docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md; git -C /Users/roberthyatt/Code/ironclaude status --short
```
Expected: no output from `add`. `status` shows every file staged by Tasks 1-8 and by the earlier staged Haiku-class loop, plus the pre-existing unrelated ` M worker/.codex-plugin/plugin.json`, which is not staged.

---

## Post-commit deploy and live checks (operator, not plan tasks; design T9)

The operator chose "Deploy after this loop": until deploy, the running Commander's five gates call the Opus grader on every use. After the operator commits:
1. `make -C /Users/roberthyatt/Code/ironclaude codex-plugin-release` (Codex plugin release) and `make -C /Users/roberthyatt/Code/ironclaude deploy-hooks` (stable hook dir and Claude plugin cache).
2. Claude-cache sync: copy `worker/hooks/hooks.json` and sync `worker/skills/` into the Claude plugin cache version directory.
3. In the installed episodic-memory plugin copy, run `npm run build` (`cli/mcp-server-wrapper.js` rebuilds `dist/` only when files are missing).
4. Restart the Commander (`make stop`, then relaunch with the Claude Code env vars unset, detached; memory `project_restart_commander_strip_claudecode`) and ChatGPT.app (kill the exact PID; `feedback_codex_restart_no_graceful_quit`). Restarting the Commander restarts the Brain, which starts a fresh orchestrator MCP subprocess on the new code.

**Where the evidence lives (verified at planning time).** The daemon writes INFO lines to `/tmp/ic/daemon.log`; C1-C4 run there. The five gates (spawn pre-filter, batch spawn, `send_to_worker`, `send_keys_to_worker`, `post_message`), worker health and the MCP spawn paths run in the orchestrator MCP subprocess, whose INFO lines ("escalating to …", "fast model answered via …") are not persisted anywhere. So the gate and health checks below watch the child processes instead: the Opus/Sonnet grader child (`_call_grader`) always carries `--system-prompt-file`; a fast-model child carries `--settings {"disableAllHooks":true}` and `--model haiku` or `--model sonnet`. Each check samples `ps` twice a second into its own log while the operator triggers the call (operator decision "Process sampling now"; durable per-call logging comes in the usage-ledger loop). Only `_call_grader` uses `--system-prompt-file` in `commander/src` (`orchestrator_mcp.py:1032`), so the Brain and workers never match it. This assumes the default Claude grader role; with a Codex grader role the grader child is `codex exec … --output-schema` instead.

Live checks (each command writes its own log and prints its exit code):
- **(1a) Daemon gates answer via Haiku.** Before triggering anything, record the log position; after a Brain message has been validated:
  ```bash
  wc -l < /tmp/ic/daemon.log > /private/tmp/claude-502/pct-live-daemon-start.txt; echo "start=$?"; cat /private/tmp/claude-502/pct-live-daemon-start.txt
  # ... let the Brain post at least one message ...
  tail -n +"$(( $(cat /private/tmp/claude-502/pct-live-daemon-start.txt) + 1 ))" /tmp/ic/daemon.log > /private/tmp/claude-502/pct-live-daemon-window.log 2>&1; echo "window=$?"; grep -c "fast model answered via haiku" /private/tmp/claude-502/pct-live-daemon-window.log; echo "haiku_grep=$?"
  ```
  Expected: window=0; the Haiku count is ≥ 1 with haiku_grep=0 (the chain is local-box-first, so a window the local box answered legitimately shows 0). Stale-deploy detection is check (1b): any --model opus grader line.
- **(1b) The five MCP gates answer via Haiku with no Opus grader.** Start the sampler, then within 60 s have the Brain do one `send_to_worker` and one `post_message`:
  ```bash
  for i in $(seq 1 120); do ps -axo pid=,command= | grep -e "--system-prompt-file" -e "disableAllHooks" | grep -v -e "grep" ; sleep 0.5; done > /private/tmp/claude-502/pct-live-gates-ps.log 2>&1; echo "sampler=$?"; grep -c -e "--model haiku" /private/tmp/claude-502/pct-live-gates-ps.log; echo "haiku_children=$?"; grep -c -e "--system-prompt-file" /private/tmp/claude-502/pct-live-gates-ps.log; echo "grader_children=$?"
  ```
  Expected: `sampler=0`; the `--model haiku` count is ≥ 1 (`haiku_children=0`); the `--system-prompt-file` count is `0` (`grader_children=1`), so no Opus (or Sonnet) grader ran for those two gate calls. If the Haiku child fails, the gate legitimately steps up to the Sonnet class and a `--system-prompt-file … --model sonnet` line appears; any `--model opus` grader line is a failure.
- **(2) Worker health runs on the Sonnet class.** Start the same sampler with its own log `pct-live-health-ps.log`, then have the Brain call `evaluate_worker_health` on a running worker:
  ```bash
  for i in $(seq 1 120); do ps -axo pid=,command= | grep -e "disableAllHooks" | grep -v -e "grep"; sleep 0.5; done > /private/tmp/claude-502/pct-live-health-ps.log 2>&1; echo "sampler=$?"; grep -c -e "--model sonnet" /private/tmp/claude-502/pct-live-health-ps.log; echo "sonnet_children=$?"
  ```
  Expected: `sampler=0`; the `--model sonnet` count is ≥ 1 (`sonnet_children=0`). On a Codex grader role the child is `codex exec … -m gpt-5.6-terra`; then grep for `-terra` instead.
- **(3) A `claude-opus` worker's advisor is Opus.** Have the Brain spawn a `claude-opus` worker (say `w-adv`), then:
  ```bash
  grep -c "/advisor opus" /tmp/ic-logs/ic-w-adv.log > /private/tmp/claude-502/pct-live-advisor.log 2>&1; echo "advisor_opus=$?"; grep -c "/advisor fable" /tmp/ic-logs/ic-w-adv.log >> /private/tmp/claude-502/pct-live-advisor.log 2>&1; echo "advisor_fable=$?"; cat /private/tmp/claude-502/pct-live-advisor.log
  ```
  Expected: `advisor_opus=0` with a count ≥ 1; `advisor_fable=1` with a count of `0`. (`/tmp/ic-logs` is the default `log_dir`; use the configured one if it differs.)
- **(4) Hook Stop checks are graded and leave no session files.** Create a marker with `touch /private/tmp/claude-502/pct-live-marker`, end a turn in a Claude session, then `find /Users/roberthyatt/.claude/projects/-private-tmp -newer /private/tmp/claude-502/pct-live-marker -type f > /private/tmp/claude-502/pct-live-sessions.log 2>&1; echo "find=$?"; cat /private/tmp/claude-502/pct-live-sessions.log`. Expected: the Stop hook's system message shows graded checks (letters, not "evaluation unavailable"), and no listed file belongs to a hook child. On Codex, a Stop is graded with `terra:<NEWEST_TERRA>` for the continuation check, and `find /Users/roberthyatt/.codex/sessions -newer /private/tmp/claude-502/pct-live-marker -type f` lists no rollout from the hook's children.
