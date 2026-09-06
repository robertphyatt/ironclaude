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
    "timeout_seconds": number?
  },

  "openai": {
    "base_url": string,
    "model": string,
    "max_tokens": number,
    "timeout_seconds": number?,
    "fallback_base_url": string?
  },

  "spots": {
    "<spot>": {
      "backend": "ollama" | "openai" | "haiku"?,
      "model": string?
    }
  },

  "shadow_model": string?,                                 // legacy alias, see below

  "timeout_seconds": number?
}
```

`<spot>` is one of: `validation`, `summarizer`, `grader`, `summarization`, `shadow`.

## Resolution rule (verbatim)

For a spot `S` in `{validation, summarizer, grader, summarization, shadow}`, reading
config `C`:

1. `backend = C.spots?.S?.backend ?? C.backend ?? C.validation_backend ?? <consumer default>`
   (defaults: shell=haiku, TS=SDK, Commander=ollama). The OpenAI branch is taken ONLY
   when the resolved backend == `"openai"`.
2. `model = C.spots?.S?.model ?? <legacy alias> ?? C.<resolvedBackend>.model ?? <consumer default model>`.
   Legacy aliases: `C.shadow_model` (TOP-LEVEL, S=shadow); `C.ollama.summarization_model`
   (NESTED under `ollama`, S=summarization). Alias is backend-agnostic and is outranked
   by `spots.S.model`.
3. `connection` = resolved backend block: `C.ollama.{url,fallback_url}` OR
   `C.openai.{base_url,fallback_base_url}`; `timeout` and (openai) `max_tokens` come from
   that block else `C.timeout_seconds`.
4. On any backend error, fail open exactly as the consumer does today.

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

## Consumer defaults (unchanged by this change)

| Consumer | Spot | Backend default | Model default |
|---|---|---|---|
| Shell validation hooks | `validation` | `haiku` | `llama3.2:1b` (when backend resolves to `ollama`) |
| TS summarizer | `summarizer` | Anthropic SDK | `llama3.2:1b` (when backend resolves to `ollama`) |
| Commander grader | `grader` | `ollama` | `gemma4:12b-it-qat` |
| Commander summarization | `summarization` | `ollama` | `gemma4:9b` |
| Commander shadow | `shadow` | `ollama` | `gemma4:12b-it-qat` |

**This change preserves every one of these defaults.** Unification is achieved by a
single documented resolution rule and a single path resolver applied identically by
every consumer — not by changing any default backend or default model value.

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
