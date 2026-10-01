# v1.1.15 Forgiving Codex Model Ladder — Design

> **Created:** 2026-09-30
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder-requirements.md

## Summary

Codex moved to gpt-6. `gpt-6-sol` is its default, and the gpt-6 family has no terra. IronClaude hard-codes the Codex ladder as exact slugs in the advisor broker and in Commander's provider config. The broker therefore rejects a `gpt-6-sol` requester and blocks enforced plan review.

This release replaces the exact lists with a tier-word rule: `gpt-<version>-<luna|terra|sol|astra>`. Concrete models are chosen from Codex's own installed model list (`models_cache.json`), with a built-in fallback. Future Codex releases then work with no code change. Claude handling is unchanged.

## Components

### 1. Advisor broker (`worker/mcp-servers/state-manager/src/tools/advisor-review.ts`)

- **Replace** `REQUESTER_MODELS` and `REVIEWER_BY_REQUESTER` with:
  - `TIER_ORDER = ['luna', 'terra', 'sol', 'astra']`;
  - `CODEX_MODEL_RE = /^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$/`;
  - `FALLBACK_MODELS = ['gpt-6-luna', 'gpt-5.6-terra', 'gpt-6-sol', 'gpt-6-astra']`.
- **`parseCodexModel(slug)`** returns `{ version: number[], tier }` or null.
- **`readInstalledCodexModels(env, readFile)`**:
  - reads `path.join(env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'models_cache.json')`;
  - JSON-parses it and keeps `models[].slug` values that parse;
  - returns `FALLBACK_MODELS` on any error or an empty result.
  - It takes an injectable reader, added to `AdvisorReviewDeps`, so tests don't touch the real home directory.
- **`newestAtTier(installed, tier)`**: the highest version at that tier, comparing versions component by component (a missing component counts as 0).
- **`resolveReviewer(requester, reviewTier, installed)`**:
  - `same` → the requester.
  - `one-up` → from the requester's tier index + 1 (capped at astra), return the first tier that has an installed model; otherwise the fallback model for the capped tier.
- **Validation:** every existing check against the old allowlist now uses `parseCodexModel`, so a non-matching requester returns the same kind of error as before. The existing turn-metadata / `requester_model` equality check is unchanged.
- **Plan step:** the exact current validation and reviewer call sites must be read from the file.

### 2. Commander (`commander/src/ironclaude/provider_config.py`, `config.py`)

This component was revised under R6: the operator chose "Relax check only".

- **Additions to `provider_config.py`:** `CODEX_TIER_WORDS = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}` and `CODEX_MODEL_RE = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$")`.
- **Unchanged:** `EXPECTED_MODELS` keeps its current values, which stay the defaults and the documented reference. `config.py` defaults are unchanged, and Commander reads no cache.
- **Validation (`provider_config.py:121-125`):**
  - Claude is still an exact match against `EXPECTED_MODELS["claude"]`.
  - Codex: the key set must equal `set(TIER_NAMES)`, and each value must be a `str` that full-matches `CODEX_MODEL_RE` with group 2 equal to `CODEX_TIER_WORDS[tier]`. On failure: `ProviderConfigError("providers.clients.codex.models must map each tier to a gpt-<version>-<tier-word> model (haiku→luna, sonnet→terra, opus→sol, fable→astra)")`. The message still contains "models", which the existing tests match on.

### 1a. Broker schema (R3a)

`advisorReviewToolDefinition.inputSchema.properties.requester_model` becomes `{ type: 'string', pattern: CODEX_MODEL_RE.source, description: … }`, with no `enum`. The client would otherwise reject `gpt-6-sol` against the old enum before the broker ever sees it.

### 3. Docs and skills

The exact-slug ladder mentions are replaced by the tier-word rule plus today's resolution: gpt-6-luna, gpt-5.6-terra, gpt-6-sol, gpt-6-astra. They are in:
- AGENTS.md, CODEX_SETUP.md, README, config/ironclaude.json.example;
- `worker/skills/{activate-professional-mode,advisor-fallback,executing-plans}/SKILL.md`;
- `commander/src/ironclaude/templates/{worker_claude_md,worker_agents}.md`.

The plan enumerates the exact lines with `rg`. Tests that pin those strings are updated.

### 4. Release v1.1.15

- Bump all seven manifest version fields, with a fresh Codex stamp, and add the CHANGELOG `## 1.1.15` and README "What's New" entries.
- Run the full commander pytest and the state-manager vitest, and rebuild the state-manager `dist/`.
- Commit locally with no trailers and tag it.
- Push and deploy on an explicit go: copy the dist into both plugin caches and restart Commander.

### 5. Codex package layout (R9)

A ChatGPT.app update moved Codex into `Resources/codex-cli/`: `codex-package.json` has layoutVersion 1 and entrypoint `bin/codex`. `bin/codex` is a wrapper next to `bin/codex-code-mode-host`, and the real binary is `CodexCLI.app/Contents/MacOS/codex`, with no companion. The preflight assumes the companion sits beside the real binary, so it reports `source-missing`.

- **`worker/scripts/codex-runtime-preflight.mjs`:**
  - New exported async function `resolvePackageEntrypoint(launcher)` implements R9.1. It uses `realpath`, `readFile` and `JSON.parse` in a try/catch, and `isExecutableRegularFile`. The walk is bounded to 6 parents, and the entrypoint must satisfy `!path.isAbsolute(e) && !e.split('/').includes('..')`.
  - `resolveCodexPath` returns `{ invokedLauncher: await resolvePackageEntrypoint(candidate) }` for both the explicit and PATH branches. The rest of `inspectRuntime` is unchanged: with the wrapper as the invoked launcher, the source and destination companion are the same file, so the result is `healthy` with reason `source-is-destination`.
- **`advisor-review.ts`:**
  - `resolveCodexExecutable(deps)` checks the PATH candidates as today and returns `await packageEntrypoint(candidate, deps)`.
  - If none is found and `deps.env.CODEX_CLI_PATH` is absolute and passes `deps.access(X_OK)`, it returns the normalised form of that.
  - `packageEntrypoint` mirrors R9.1 in TypeScript and gets its filesystem through injectable deps. It uses the same `readFile` dep that component 1 adds, plus `realpath` and `stat`, so tests use fixtures without touching the real filesystem.
  - The broker's preflight equality checks (`:463-467`) are unchanged. `invoked_launcher === codexExecutable` holds because the broker passes the normalised path.
- **Nothing inside `CodexCLI.app`** is touched (R9.4).
- **Tests:**
  - **Preflight (pytest):** a temp tree with the new layout gives healthy / source-is-destination. The old layout is unchanged. Negative cases, each still `source-missing`: `layoutVersion: 2`, an absolute entrypoint, a `..` entrypoint, a missing entrypoint, malformed JSON, and a manifest 7 levels up.
  - **Broker (vitest):** with a PATH candidate inside the fixture package, the broker launches `<pkg>/bin/codex`. `CODEX_CLI_PATH` is used when PATH has none, and a relative `CODEX_CLI_PATH` is ignored.
- **Docs:** README and CODEX_SETUP.

## Error Handling

- A missing, unreadable or malformed cache, or one with no matching slugs, falls back to the built-in list. There is no crash or network call.
- A malformed requester is rejected, as before.

## Testing Strategy

TDD throughout.

- **Broker (vitest):**
  - `gpt-6-sol` is accepted;
  - `gpt-6-sol` one-up resolves to `gpt-6-astra`;
  - `gpt-6-luna` one-up resolves to `gpt-5.6-terra`, skipping the absent gpt-6 terra;
  - `gpt-5.6-terra` one-up resolves to `gpt-6-sol`;
  - astra one-up resolves to the newest astra;
  - `same` returns the requester;
  - an unreadable cache uses the fallback;
  - a newer version wins (a fake `gpt-7-sol` is chosen over `gpt-6-sol`);
  - a malformed or non-Codex requester is rejected.
- **Commander (pytest):**
  - gpt-6 and gpt-5.6 configs are both accepted;
  - a wrong tier word (e.g. `sonnet: gpt-6-sol`) is rejected;
  - a malformed slug is rejected;
  - a missing tier is rejected;
  - the defaults are unchanged;
  - Claude is still an exact match.
- **Broker schema:** `requester_model` has a `pattern` and no `enum`.
- **Full suites:** commander pytest and state-manager vitest.

## Implementation Notes

- `docs/` is gitignored; use `git add -f`.
- `allowed_files` are git-root-relative (`commander/...`, `worker/...`); `design_file` is unprefixed.
- Deployed runtime: Codex runs the state-manager from the plugin cache `dist/`, so the fix only reaches it after the dist rebuild and cache copy.
