# LLM Backend Config Schema

Authoritative schema and resolution rule for `~/.claude/ironclaude-hooks-config.json`
(or the path named by `IC_OLLAMA_CONFIG_PATH`). Every consumer — shell hooks, the TS
summarizer, and Commander (grader / summarization / shadow) — reads this same file
through this same rule. `worker/config-schema/resolution-cases.json` is the shared
conformance fixture; later per-language tests assert against it directly.

## Schema

```
{
  "backend": "ollama" | "openai" | "haiku",
  "validation_backend": "ollama" | "openai" | "haiku",   // legacy alias, see below

  "ollama": {
    "url": string,
    "fallback_url": string?,
    "model": string,
    "summarization_model": string?,                       // legacy alias, see below
    "timeout_seconds": number?,
    "connect_timeout_seconds": number?,
    "probe_timeout_seconds": number?,
    "hook_validation_budget_seconds": number?
  },

  "openai": {
    "base_url": string,
    "model": string,
    "max_tokens": number,
    "timeout_seconds": number?,
    "fallback_base_url": string?,
    "connect_timeout_seconds": number?,
    "probe_timeout_seconds": number?,
    "hook_validation_budget_seconds": number?
  },

  "spots": {
    "<spot>": {
      "backend": "ollama" | "openai" | "haiku"?,
      "model": string?
    }
  },

  "shadow_model": string?,                                 // legacy alias, see below

  "timeout_seconds": number?,
  "connect_timeout_seconds": number?,
  "probe_timeout_seconds": number?,
  "hook_validation_budget_seconds": number?,
  "fast_model_timeout_seconds": number?,
  "sonnet_model_timeout_seconds": number?
}
```

`<spot>` is one of: `validation`, `summarizer`, `grader`, `summarization`, `shadow`.

## Resolution rule (verbatim)

For a spot `S` in `{validation, summarizer, grader, summarization, shadow}`, reading
config `C`:

1. `backend = C.spots?.S?.backend ?? C.backend ?? C.validation_backend ?? <consumer default>`
   (defaults: shell=haiku, TS=SDK, Commander=haiku). The OpenAI branch is taken ONLY
   when the resolved backend == `"openai"`. `haiku` means the Haiku-class fast model with no local box: no url, and only `spots.S.model` supplies a model.
2. `model = C.spots?.S?.model ?? <legacy alias> ?? C.<resolvedBackend>.model ?? <consumer default model>`.
   Legacy aliases: `C.shadow_model` (TOP-LEVEL, S=shadow); `C.ollama.summarization_model`
   (NESTED under `ollama`, S=summarization). Alias is backend-agnostic and is outranked
   by `spots.S.model`.
3. `connection` = resolved backend block: `C.ollama.{url,fallback_url}` OR
   `C.openai.{base_url,fallback_base_url}`; `timeout` and (openai) `max_tokens` come from
   that block else `C.timeout_seconds`.
4. On any backend error, fail open exactly as the consumer does today.

`connect_timeout_seconds`, `probe_timeout_seconds`, and `hook_validation_budget_seconds`
resolve block-then-top identically to `timeout_seconds` — i.e.
`<resolvedBackend>.<key> ?? C.<key>`:

- `connect_timeout_seconds` (default 3) — TCP connect budget.
- `probe_timeout_seconds` (default 3) — the probe's READ half; worst-case probe wall
  time is `connect_timeout_seconds + probe_timeout_seconds`.
- `hook_validation_budget_seconds` (default 8) — total wall budget for a bash-hook LLM
  validation call.

Note `timeout_seconds` itself is the **inference READ budget only** — it does not cover
connect or probe time, which is why the three keys above exist separately.

Every parser (shell, TS, Python/Commander) must implement steps 1–4 identically. The
only thing that varies per consumer is the default in step 1/step 2 when nothing in
`C` resolves the value — see the defaults table below.

## Legacy aliases (explicit)

| Alias | Location | Applies to | Resolves to |
|---|---|---|---|
| `validation_backend` | top-level | any spot's backend resolution | global `backend` fallback (step 1) |
| `shadow_model` | **top-level** (not nested) | spot `shadow` only | `shadow` spot's `model` fallback (step 2) |
| `summarization_model` | **nested under `ollama`** — i.e. `ollama.summarization_model` (NOT top-level) | spot `summarization` only | `summarization` spot's `model` fallback (step 2) |

Notes:
- The `ollama` block itself is otherwise unchanged — `ollama.summarization_model` is an
  additional optional key inside it, not a restructuring.
- All aliases are **backend-agnostic**: `shadow_model` and `ollama.summarization_model`
  can supply a model name regardless of which backend that spot resolves to (e.g. a
  `shadow_model` value is used even when the `shadow` spot resolves to `backend: "openai"`).
- Aliases are outranked by `spots.<spot>.model` — an explicit per-spot model always
  wins over the alias (see resolution-cases.json cases 4 and 7).

## Consumer defaults

| Consumer | Spot | Backend default | Model default |
|---|---|---|---|
| Shell validation hooks | `validation` | `haiku` | `llama3.2:1b` (when backend resolves to `ollama`) |
| TS summarizer | `summarizer` | Anthropic SDK | `llama3.2:1b` (when backend resolves to `ollama`) |
| Commander grader | `grader` | `haiku` | `gemma4:12b-it-qat` (when a spot is set to `ollama`) |
| Commander summarization | `summarization` | `haiku` | `gemma4:9b` (when a spot is set to `ollama`) |
| Commander shadow | `shadow` | `haiku` | `gemma4:12b-it-qat` (when a spot is set to `ollama`) |

Commander's default backend changed from ollama to haiku on 2026-10-16 (operator decision "Keep it + cover gaps"); a local ollama/openai box stays fully supported through config.

### Deliberate exception: summarization on ollama

The Commander summarization spot on the **ollama** backend intentionally keeps its
`gemma4:9b` default and does **not** apply step 2's `C.ollama.model` fallback — a shared
`ollama.model` (set for workers) must not silently change which model summarizes. Override
the summarization model only via `spots.summarization.model` or the
`ollama.summarization_model` alias. This exception is Commander-specific and pinned by a
byte-identity guard in `commander/tests/test_orchestrator_mcp.py`; the openai arm and every
other spot follow the uniform rule above.

## Path resolution

All parsers (shell, TS, Python/Commander) honor, in order:

1. `IC_OLLAMA_CONFIG_PATH` environment variable, if set.
2. `~/.claude/ironclaude-hooks-config.json`, otherwise.

## OpenAI backend specifics

- No real API key is required or validated. Send a dummy bearer token, e.g.
  `Authorization: Bearer <anything>`.
- Plain HTTP (`http://`) endpoints are allowed — no TLS requirement.
- Models are selected by **name**, including reasoning-effort variants such as
  `example-model-b:off`, `example-model-b:low`, `example-model-b:xhigh`. These names are free-text
  and are not required to appear in the backend's `/v1/models` listing.
- `reasoning_effort` (or any equivalent request parameter) is **ignored** by this
  integration — the reasoning level is encoded entirely in the model name suffix
  (`:off` / `:low` / `:xhigh`), not passed as a separate field.
- `max_tokens` **must be ≥ 400**. Hidden/internal reasoning tokens consume budget
  before visible content is emitted; a smaller `max_tokens` starves the visible
  response and returns empty content.
- Responses are parsed from `.choices[0].message.content` (OpenAI chat-completions
  response shape).

## Worked examples

### (a) Legacy Ollama-only (unchanged behavior)

```json
{
  "validation_backend": "ollama",
  "ollama": {
    "url": "http://localhost:11434",
    "model": "llama3.2:1b"
  },
  "timeout_seconds": 60
}
```

`validation` spot resolves: backend `ollama` (via `validation_backend`), model
`llama3.2:1b` (via `ollama.model`), url `http://localhost:11434`.

### (b) All-openai (self-hosted endpoint)

```json
{
  "backend": "openai",
  "openai": {
    "base_url": "http://llm-host:8080/v1",
    "model": "example-model-a",
    "max_tokens": 1024,
    "timeout_seconds": 300
  },
  "spots": {
    "shadow": {
      "model": "example-model-b"
    }
  }
}
```

`grader` spot resolves: backend `openai` (via global `backend`), model
`example-model-a` (via `openai.model`), url `http://llm-host:8080/v1`.
`shadow` spot resolves: backend `openai` (via global `backend`, no per-spot override),
model `example-model-b` (via `spots.shadow.model`, outranks the `openai.model` default).

### (c) Mixed (validation on openai, shadow on ollama)

```json
{
  "backend": "haiku",
  "ollama": {
    "url": "http://localhost:11434",
    "model": "gemma4:12b-it-qat"
  },
  "openai": {
    "base_url": "http://llm-host:8080/v1",
    "model": "example-model-b:low",
    "max_tokens": 800
  },
  "spots": {
    "validation": {
      "backend": "openai"
    },
    "shadow": {
      "backend": "ollama"
    }
  }
}
```

`validation` spot resolves: backend `openai` (via `spots.validation.backend`, overrides
global `backend: "haiku"`), model `example-model-b:low` (via `openai.model`), url
`http://llm-host:8080/v1`.
`shadow` spot resolves: backend `ollama` (via `spots.shadow.backend`, overrides global
`backend: "haiku"`), model `gemma4:12b-it-qat` (via `ollama.model`), url
`http://localhost:11434`.

## Haiku-class fast-model chain

- **The order.** Local box (`ollama`/`openai`, 3 s connect / 8 s budget) -> Haiku-class model -> fail open. `backend: "haiku"` skips the local box. A local result that is empty, failed, non-JSON, or (Commander) any `infrastructure_error` falls through.
- **Local-box backoff.** A box failure skips that box for `local_backoff_minutes` and goes straight to the Haiku-class model, in the hooks and in the Commander's local grader. A box failure is a connect failure (refused or connect timeout), an HTTP error, an empty answer, invalid JSON, a read timeout at the box's own read timeout (`timeout_seconds`; unset, 120 s in the Commander), or, in the Commander, any other local `infrastructure_error`. The hooks also count their own budget timeout (`--max-time` = `hook_validation_budget_seconds`, default 8) as a box failure. The only thing that does not count is a Commander read timeout under a per-call `read_timeout` shorter than the Commander's configured read timeout, such as the fast lane's `fast_lane_grade_timeout_seconds` (default 5): it writes no marker and clears none. Resolution is block-then-top: `<backend>.local_backoff_minutes` // `local_backoff_minutes` // `15`; `0` disables it. The hooks and the Commander share one marker, `local-box-backoff.json`, holding the endpoint URL and its expiry, in `$IRONCLAUDE_LOCAL_BACKOFF_DIR` (default `~/.ironclaude/state/local-backoff`). That directory must be a real 0700 directory owned by the user; otherwise the marker is ignored and never written. The first call after expiry tries the box again: a success clears the marker and a failure renews it. The shadow spot and session summarization keep their own handling.
- **The per-client mapping.** Hooks: Claude `claude --model haiku`; Codex: the newest installed `gpt-*-luna` from `$CODEX_HOME/models_cache.json`, the same resolution as the advisor broker. There is no pinned fallback: with no Luna installed, Codex is unavailable for the call and the hook fails open. Commander: the `grader` role's client order. Claude uses `models.haiku`; Codex uses `providers.clients.codex.models.haiku`, the family word `luna` by default, resolved to the same newest-installed Luna (an explicit slug is used as-is), so the hook and the Commander pick the same model.
- **The isolation flags.**
  - **Claude:** `--restricted --effort low --tools "" --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}'`, cwd `/tmp`. The hook also unsets `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and `CLAUDE_CODE_EFFORT_LEVEL`, as the Commander does; an inherited `CLAUDE_CODE_EFFORT_LEVEL` would override `--effort low`.
  - **Codex:** `--ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only` and `model_reasoning_effort="low"`. The `--output-schema` is fully strict: every object is closed and every property required, and formerly optional properties become nullable. Codex also gets the shared no-tools set `fast_model.CODEX_NO_TOOLS_ARGS`: `-c features.<name>=false` for shell_tool, unified_exec, view_image, apps, browser_use, computer_use, multi_agent, image_generation, memories, plugins, hooks, goals and tool_suggest, plus `-c agents.enabled=false` and `-c web_search="disabled"`. The Codex grader and the Codex advisor broker use the same set.
  - **Still on:** `unified_exec` stays on whatever the flag says (measured 2026-10-17).
- **The cap.** `fast_model_timeout_seconds` (top level) caps each fast-model call, default 30. It replaces `timeout_seconds` on this path. The Commander's fast lane allows 11 s extra only when the fast model answers. Prompt detection stays synchronous (the measured 5.02 s p95 was accepted by the operator).
- **The shadow note.** The `shadow` spot needs a local box. It skips quietly (no Slack post) when its backend resolves to `haiku` or the box is unreachable, and it stays skipped under a top-level `haiku` until `spots.shadow.backend` is set.
- **Also:** the Commander's shared `claude -p` grader argv also carries `--no-session-persistence`, which is safe for every grader tier because `_grader_home` is only the cwd.

## Per-call model classes

Each harness LLM call runs at the model class its difficulty needs. A class name is resolved per client.

**Classes are resolved per client.**

| Class | Claude | Codex, hooks | Codex, Commander |
|---|---|---|---|
| `haiku` | Claude Haiku | newest installed `gpt-*-luna`; none installed -> Codex unavailable | `providers.clients.codex.models.haiku` (default `luna`: newest installed Luna) |
| `sonnet` | Claude Sonnet | newest installed `gpt-*-terra`; none installed -> Codex unavailable | `providers.clients.codex.models.sonnet` (default `terra`: newest installed Terra) |
| `opus` | Claude Opus | not used by the hooks | `providers.clients.codex.models.opus` (default `sol`: newest installed Sol) |

The opus class on Codex is Sol. Fable and Astra are never used for these calls.

**Which call uses which class.**

| ID | Call | Site | Class |
|---|---|---|---|
| H1 | Bypass check | hooks | haiku |
| H2 | Stop-hook continuation judge | hooks | sonnet |
| H3-H6 | Rigor, prediction, COA and evidence checks | hooks | haiku |
| H7 | Task-completion validator | hooks | haiku |
| C1 | Brain-message validator | Commander | haiku |
| C2 | Awaiting-operator capture | Commander | haiku |
| C3 | Prompt detection | Commander | haiku |
| C4 | Permission-seeking check | Commander | haiku |
| C5/C6 | Spawn screening (`spawn_worker`, `spawn_workers`) | Commander | haiku pre-filter, escalating to sonnet |
| C7 | `approve_plan` | Commander | opus |
| C8 | `send_to_worker` | Commander | haiku |
| C9 | `send_keys_to_worker` | Commander | haiku |
| C10 | `post_message` | Commander | haiku |
| C11 | Worker health | Commander | sonnet |
| C12 | `kill_worker` completion check | Commander | opus |
| C13 | Session summaries | Commander | haiku |
| W1 | Episodic summarizer | episodic memory | haiku |

**Routing.** Haiku-class calls follow the "Haiku-class fast-model chain" above (local box first). Sonnet-class calls never use the local box, whatever `backend` says.

**Backup rule.** Fail-closed gates step up one class when their first choice cannot answer (infrastructure error, timeout, or unparseable or missing fields).
- C8, C9 and C10 go Haiku, then Sonnet through the Commander grader, then grade F.
- C5/C6 escalate to Sonnet when the Haiku pre-filter is not "high" confidence or is unavailable. A failed Sonnet escalation is F.
- C7 and C12 stay on the opus class. Their failure is F, and nothing steps up from the opus class.
- Fail-open classifiers and hook checks (H1-H7, C1-C4, C11, C13) fail open after their first-choice class, with no step-up.

**The Sonnet cap.** `sonnet_model_timeout_seconds` (top level) caps each Sonnet-class call. The default is 30, measured as `max(30, ceil(2 × p95 of six concurrent lean Sonnet children))` (findings: `docs/plans/2026-10-16-per-call-difficulty-tiering-findings.md`). It is separate from `fast_model_timeout_seconds`.

**Labels.** The hook's `VALIDATION_LLM_BACKEND` reads `sonnet` (Claude) or `terra:<slug>` (Codex) for a Sonnet-class call.

## Out of scope

The Ollama-**worker** lifecycle (as opposed to the Ollama **backend** for these five
spots) stays Ollama-only and is not converted by this schema or any consumer of it:

- `/api/ps` VRAM gating
- `/api/create` with `num_ctx` variants
- `keep_alive: 0` model unload
- `ollama_mcp.py` CLI
- `ANTHROPIC_BASE_URL` worker spawns

## Fixture scope

`resolution-cases.json` holds **only** language-agnostic, fully-deterministic cases —
every field of `config`, `spot`, and `expect` is concrete, so the same case produces
the same expected result under any consumer's parser. Per-consumer "backend/model
unset, fall back to my built-in default" behavior is **not** in the shared fixture; it
is tested inline in each language's own test suite (shell/TS/Python), against that
consumer's own row in the "Consumer defaults" table above.
