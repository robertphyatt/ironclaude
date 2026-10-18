# Haiku-Class Fast-Model Fallback Design

> **Created:** 2026-10-16
> **Status:** Design Complete

Loop: full. It touches hook and guard enforcement code (`worker/hooks/plan-validator.sh` `call_validation_llm`, used by the get-back-to-work Stop hook and the task-completion validator) and the Commander grader routing.

## Summary

The operator took the AMD-halo inference box down for LoRA training and set `~/.claude/ironclaude-hooks-config.json` `backend` to `"haiku"`. It did not work: every hook check still fails open. The operator asked: "let's have the next loop fix haiku/luna; the config is ready now, we just need to use it properly with a new fix + deploy."

Operator directives (2026-10-16), verbatim:
- "instead of failing open as a default, it first tries haiku/luna and only then (if it is unavailable) does it fail open"
- "The revisit part is to make sure we are handling the sessions right"
- "please note that we are treating "haiku" as "haiku class" = "luna" if codex is running"
- "please also note that we are recommending folks to use haiku class models instead of running their own because they are so dang cheap and good nowadays"
- "please have a fable sub agent take a look at all the haiku class updates we are doing and ensure that we aren't making any rash changes"
- Scope answer: "Hooks + Commander". Sections 1–4 were each approved.
- After the Fable review, the operator asked "Why can't we use haiku here?" and then chose "Chain all, gated" for the hot paths and "Keep it + cover gaps" for the default backend flip.

## Root causes (measured 2026-10-16)

1. **The output is never parsed.**
   - The haiku arm (`plan-validator.sh:211-227`) runs `claude --model haiku --print --tools "" --output-format json --json-schema`. On Claude CLI 2.1.302 that returns an **array of events**, with the answer in the last element: `{"type":"result","structured_output":{...}}`.
   - The arm echoes the raw array. `get-back-to-work-impl.sh:858` parses it with `jq -r ".$field"`, which fails with "Cannot index array with string", so every check is marked "invalid grade" and becomes "unavailable" (fail open).
   - A probe gave rc=0 and Haiku graded correctly.
2. **Sessions pile up.** Each call persists a session under `~/.claude/projects/-private-tmp`: the probe session `414ee095-…` exists, and 52 files have been written there since 15:00 today. The earlier grader/summarizer snowball (16k+ sessions) came from this path.
3. **The children are heavy.** The child session ran IronClaude hooks (`SessionStart:startup`, plus the Stop hook `get-back-to-work-claude.sh`, 59 ms with no LLM call, so there is no runaway recursion today) and loaded every IronClaude MCP server.
4. **The timeout is wrong.** The haiku arm uses `timeout_seconds` (600 in the operator's config), not the bounded hook budget. Haiku hook calls took ~12-15 s in February 2026 (robertpowers), which is longer than the 8 s hook budget.
5. **The Commander has no haiku backend.** `backend_resolver.resolve_backend` (`:54-72`) treats any non-`openai` backend as Ollama at `localhost:11434`, and the default is `_COMMANDER_DEFAULT_BACKEND = "ollama"` (`:17`). With `backend: "haiku"`, the local graders point at Ollama with no model.

## Requirements

- **R1 — Chain.** Every fast-model consumer calls the configured local box first (`openai` or `ollama`, with the existing 3 s connect / 8 s budget). If that fails or returns empty, it calls the Haiku-class model. It fails open (the existing non-blocking "unavailable" result) only if the Haiku-class model also fails. `backend: "haiku"` skips the local box.
- **R2 — Haiku class per client.** "haiku" means the Haiku-class tier:
  - Claude Haiku (`claude --model haiku`) in Claude sessions;
  - the newest installed `gpt-*-luna` in Codex sessions. This is resolved like the broker (`advisor-review.ts:212-228`): read `~/.codex/models_cache.json` (or `$CODEX_HOME`), take the highest-version `gpt-<v>-luna`, and fall back to `gpt-6-luna`. Installed today: `gpt-6-luna`, `gpt-5.6-luna`.

  The hooks detect the client with the existing `ic_is_codex()` (`hook-logger.sh:146-155`). The Commander follows its `grader` role preference: Claude first, then Codex.
- **R3 — Sessions handled right.**
  - Claude children run with `--no-session-persistence` and `--strict-mcp-config` (no MCP servers), with IronClaude hooks disabled inside the child.
  - Codex children run with `--ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only`, the advisor broker's flags.
  - Proof: no new session file, and no hook attachments in the child.
- **R4 — Parsing.**
  - Claude: take `structured_output` from the `type == "result"` event, whether the output is an array or a single object. This mirrors `_parse_claude_grader_output` (`orchestrator_mcp.py:1051-1089`).
  - Codex: take the last `item.completed` `agent_message` text, parsed as JSON. This mirrors `_parse_codex_grader_output` (`orchestrator_mcp.py:1118-1144`).
  - `call_validation_llm` keeps returning the plain JSON object its callers already parse (`get-back-to-work-impl.sh:844-867`, `task-completion-validator.sh:97-108`), so the callers are unchanged.
- **R5 — Timeout.** The fast-model step gets its own cap, a new config key `fast_model_timeout_seconds`. Its default is set from latency measured in execution, targeted at ~30 s. It replaces the 600 s `timeout_seconds` on this path.
- **R6 — Commander.**
  - Every local-grader spot follows the same chain. That includes the hot paths, per the operator's decision "Chain all, gated" (2026-10-16), after they asked "Why can't we use haiku here?".
  - The Haiku-class step lives in a shared fast-model helper in `grader.py` or a new module. It does not live in `orchestrator_mcp.py`, because `grader.py` deliberately avoids importing it.
  - The helper reuses only the argv builders: `_claude_grader_argv`-style with `--model haiku`, `--no-session-persistence` and hooks off; `_codex_grader_argv`-style with Luna and low reasoning effort. Its parser returns the FULL `structured_output` object, because the local spots read `kind`, `valid`, `reason`, `waiting_on`, `worker_id`, `question` and `permission_seeking`.
  - It never routes through `_call_grader`: that path squashes answers to `{grade, approved, feedback}`, holds `_grader_lock`, and uses the `claude_grader` profile and the `self._grader_model` tier.
  - Both `OrchestratorTools` and the standalone `LocalGrader` instances (`main.py:1620`, `brain_client.py:304`) call the helper.
  - **Gated hot paths.** These are the awaiting-operator capture `_grade_bounded` (`main.py:3245-3290`: 5 s join, a single in-flight slot) and prompt-waiting detection `_detect_worker_prompt` (`main.py:4238`: synchronous on the 15 s poll thread). Execution first measures lean Haiku/Luna child latency under realistic concurrency. The plan then applies what the numbers require: raise the fast-lane bound when the backend answering is the fast model, or move prompt detection off the poll thread. The measured numbers are recorded in the plan.
  - The shadow grader, a qwen concordance check of the local model, keeps requiring the local box. It skips quietly, with no Slack failure post, when the box is unreachable or the backend is `haiku`. It stays skipped under a top-level `haiku` until `spots.shadow.backend` is set, and that behavior is documented.
- **R7 — Defaults and docs.** The operator chose "Keep it + cover gaps".
  - The Commander default backend changes from `ollama` to `haiku`, matching the hooks (`plan-validator.sh:102`).
  - Session summarization (`orchestrator_mcp.py:6530-6552`, today an Ollama `get_ps()` branch for any non-`openai` backend) gains a Haiku-class branch, so users with no config do not lose summaries.
  - `llm-backend.md` (consumer-defaults section) and `commander/tests/test_backend_resolver.py` (pinned `ollama`) are updated.
  - Local `ollama` and `openai` remain fully supported through config.
  - The README recommends Haiku-class models (Haiku on Claude, Luna on Codex) as cheap and good, documents local hosting as optional, and describes the chain.
- **R7b — Episodic summarizer sessions.** This was required by the operator on 2026-10-12: "Haiku calls must NOT persist sessions … both paths". `worker/mcp-servers/episodic-memory/src/summarizer.ts` `callClaude` (`:261-300`) passes `persistSession: false` on fresh (non-`resume`) SDK calls. The resume path is checked separately in execution.
- **R8 — Deploy.**
  - Run `make codex-plugin-release` and `make deploy-hooks`, copy `hooks.json` and sync skills into the Claude plugin cache, rebuild the episodic MCP bundle if the build requires it, then restart the Commander and ChatGPT.
  - Live checks:
    - (a) a real Stop hook in a Claude session shows graded checks (not "unavailable") and writes no new session file under `~/.claude/projects/-private-tmp`;
    - (b) a real Codex Stop hook is graded with `luna:…` and writes no new rollout under `~/.codex/sessions`.

Additional adjustments from the Fable review (operator-requested):
- Hooks-off uses `--settings '{"disableAllHooks":true}'`. Fable confirmed from the CLI 2.1.302 binary that `--settings` accepts inline JSON and that `disableAllHooks` is honored whatever source sets it. Execution still verifies it via the child transcript. Never use `--bare`, which disables first-party sign-in.
- The chain also falls back when the local result is not valid JSON (`jq -e .`), not only when it is empty.
- The fast-model cap is measured with six concurrent children (the Stop hook's parallel checks). The cap test asserts that the child process is killed, because `portable_timeout`'s fallback kills only `bash -c`.
- The Luna model choice is aligned: hooks resolve the newest installed `gpt-*-luna`, while the Commander uses `provider_config` `gpt-5.6-luna`. Either align them or document the difference.
- Adding `--no-session-persistence` to the shared Claude argv also affects other grader tiers. This is safe, because `_grader_home` is only the cwd, and it is stated explicitly.

Non-goals:
- no change to the callers' parsing or blocking policy;
- no change to the Fable/Top-tier work or the Commander worker advisors (queued separately);
- no removal of the local backends.

## Architecture

### Hooks (`worker/hooks/plan-validator.sh`)

`call_validation_llm` becomes:
1. Run the configured local arm (`ollama`/`openai`) as today, unless the backend is `haiku`.
2. If the result is empty or the call failed, call `_call_fast_model "$prompt" "$schema"`:
   - **Codex** (`ic_is_codex`): `codex exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only -m "$(_newest_luna)" --output-schema <tmpfile> -`, then extract the last agent_message JSON.
   - **Claude:** `claude --model haiku --print --tools "" --no-session-persistence --strict-mcp-config <hooks-off mechanism> --output-format json --json-schema "$schema"` from `/tmp`, then extract `structured_output`.
   - Both run under `portable_timeout "$fast_model_timeout"`.
3. Set `VALIDATION_LLM_BACKEND` to the backend that actually answered (e.g. `openai:gemma…`, `haiku`, `luna:gpt-6-luna`), and echo the plain JSON object, or return 1 when every step failed.

**Hooks-off mechanism.** It is chosen in execution by measurement. A Claude child is run once with each candidate and its transcript is inspected for hook attachments:
- (a) `--settings '{"disableAllHooks":true}'`;
- (b) an `IC_HOOK_CHILD=1` env var, checked as the first statement of every IronClaude hook entry point.

Candidate (a) is preferred if the measurement proves it works, because it needs no per-hook edits.

### Commander (`commander/src/ironclaude`)

- `backend_resolver.resolve_backend` accepts `haiku`, and `_COMMANDER_DEFAULT_BACKEND = "haiku"`.
- A shared fast-model helper (see R6) is called by `LocalGrader`/`_call_local_grader` when the local client fails, returns non-JSON, or the backend is `haiku`. It returns the full structured object. It is never `_call_grader`. The exact call sites are enumerated from source in the plan.
- The gated hot paths (`_grade_bounded`, `_detect_worker_prompt`) are adjusted per the measured latency (R6).
- Session summarization gains a Haiku-class branch (R7).
- The shadow grader's spot skips when the box is unreachable or the backend is `haiku`.
- `summarizer.ts` gets `persistSession: false` (R7b).

## Testing Strategy

- **Hooks:** new `worker/hooks/tests/test-fast-model-fallback.sh`, with stub `claude`, `codex` and `curl` on PATH that record their argv. Cases:
  - event-array output is parsed (RED today);
  - single-object output is parsed;
  - openai failure falls back to Haiku;
  - `backend: haiku` never calls `curl`;
  - `IC_HOOK_CLIENT=codex` uses `codex exec` with the newest luna from a fixture models cache;
  - both steps failing returns failure (fail open);
  - the exact isolation flags are asserted;
  - the timeout cap is enforced.

  Add the test to the Makefile's `test-hooks` set if it is not picked up by glob.
- **Commander:** pytest for the resolver's `haiku` default and handling; for the local-grader chain (local → claude grader → codex grader → failure) with the existing fixtures; for `--no-session-persistence` on the grader argv; and for the shadow grader skipping.
- **Regression:** the full commander suite, plus `make test-hooks`.
- **Live (after deploy):** see R8.

## Error Handling

Every failure stays non-blocking (fail open), unchanged in kind. The chain only adds a step before fail-open. Timeouts are bounded by the 8 s local budget plus the fast-model cap.
