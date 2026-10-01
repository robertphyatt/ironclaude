# v1.1.15 Forgiving Codex Model Ladder — Requirements

> **Created:** 2026-09-30
> **Scope mode:** hold

## Operator directives

- Codex reported: "this turn is running gpt-6-sol, but the installed advisor broker accepts only gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol, and gpt-6-astra … enforced review blocks execution."
- Operator: "We are using v6 now" and "can we make it so that it is more forgiving?"
- Chose "A: tier-name + installed list (Rec.)" over "B: config ladder + lenient" and "C: just add gpt-6 names".
- Approved the design ("Yes, write it up (Rec.)") over "Broker only".
- Chose "Yes, v1.1.15 (Rec.)": version bump, CHANGELOG and README, commit locally, then push and deploy on an explicit go.
- Standing preference from memory: aliases or tier rules over hard-pinned model versions. A requester that isn't a Codex model shape still fails closed.

## Facts (verified)

- `~/.codex/config.toml` has `model = "gpt-6-sol"`.
- `~/.codex/models_cache.json` is JSON with a top-level `models` array of objects that have `slug`. Its slugs are gpt-6-astra, gpt-6-sol, gpt-6-luna, gpt-reserve, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, gpt-5.5 and codex-auto-review. There is no gpt-6-terra.
- `worker/mcp-servers/state-manager/src/tools/advisor-review.ts:18-34` hard-codes `REQUESTER_MODELS` and `REVIEWER_BY_REQUESTER` to gpt-5.6-luna/terra/sol and gpt-6-astra.
- `commander/src/ironclaude/provider_config.py:19-29` `EXPECTED_MODELS["codex"]` and `:122` require an exact match. `config.py:79-82` has the same defaults.

## Requirements

- R1 Model shape: a Codex tier model is a slug matching `^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$`. Tiers are ranked luna < terra < sol < astra and paired haiku→luna, sonnet→terra, opus→sol, fable→astra. Versions compare numerically, component by component (6 > 5.6).
- R2 Installed list: read `models_cache.json` from `$CODEX_HOME`, or `~/.codex` when it is unset, and keep the slugs matching R1. If the file is missing, unreadable, malformed, or has no matching slugs, use the built-in fallback: gpt-6-luna, gpt-5.6-terra, gpt-6-sol, gpt-6-astra.
- R3 Broker requester: `run_codex_advisor_review` accepts any `requester_model` matching R1, at any version, whether or not it is installed, and rejects anything else. The existing provider-authenticated turn-metadata check is unchanged.
- R4 Broker reviewer:
  - `same` means the requester model itself.
  - `one-up` means the newest installed model at the next tier above the requester's. If that tier has none, move up again. At the ceiling (astra), use the newest installed astra.
  - If nothing qualifies, use the built-in fallback's model for that tier.
- R5 Commander validation: `providers.clients.codex.models` must have exactly the four tiers, and each value must match R1 with that tier's tier word, at any version. It no longer has to equal a fixed map. `providers.clients.claude.models` stays an exact match.
- R6 Commander defaults: the default Codex tier values are unchanged. They stay gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol and gpt-6-astra in `config.py` and in the `EXPECTED_MODELS` defaults. Commander does not read `models_cache.json`. The operator chose "Relax check only (Rec.)" over static gpt-6 defaults and cache-derived defaults: the cache approach meant machine-dependent reads at module load and heavy test churn, and Commander's Codex client is disabled by default. A config that names gpt-6 models is accepted under R5.
- R3a Broker schema: the tool's `inputSchema.requester_model` publishes a regex `pattern` for R1 instead of a fixed `enum`. The old enum would let the client reject `gpt-6-sol` before the broker sees it.
- R7 Docs and skills that name exact Codex slugs as the ladder describe the tier-word rule instead: AGENTS.md, CODEX_SETUP.md, config/ironclaude.json.example, the skills, the templates and README.
- R8 Release v1.1.15: bump every version manifest, and add CHANGELOG and README entries. Run the full commander pytest and the state-manager vitest. Commit locally with no trailers and tag it. Push and deploy only on an explicit operator go. Deploy means rebuilding the state-manager dist into both plugin caches and restarting Commander.

### R9 — Codex package layout (added: operator "Add to v1.1.15 (Rec.)", then approved the layout design "Yes, write it up (Rec.)")

**Facts:**
- A ChatGPT.app update (Codex 0.159.2) removed `Resources/codex` and its sibling `codex-code-mode-host`.
- It added the package `Resources/codex-cli/`:
  - `codex-package.json` = `{"layoutVersion": 1, "entrypoint": "bin/codex", ...}`;
  - `bin/codex` is a sh wrapper that execs `../CodexCLI.app/Contents/MacOS/codex`;
  - `bin/codex-code-mode-host` is the only companion;
  - `CodexCLI.app/Contents/MacOS/codex` is the real binary, with no companion beside it.
- Codex Desktop exports `CODEX_CLI_PATH` pointing at the CodexCLI.app binary.
- The broker fails with `codex-runtime-preflight: source-missing` (`codex-runtime-preflight.mjs:51-56,108,119-120`; `advisor-review.ts:466`).

**Requirements:**
- R9.1: A shared rule, `resolvePackageEntrypoint(launcher)`:
  - realpath the launcher and walk up at most 6 parent directories for `codex-package.json`;
  - if it parses with `layoutVersion === 1` and a string `entrypoint` that is relative with no `..` segment, and `<pkgRoot>/<entrypoint>` is an executable regular file, return that path;
  - otherwise return the launcher unchanged.
- R9.2: The preflight's `resolveCodexPath` applies R9.1 to both the PATH-found candidate and an explicit `--codex-path`. With the new layout, check mode reports `healthy` with reason `source-is-destination`.
- R9.3: The broker's `resolveCodexExecutable` applies R9.1 to the PATH-found candidate. When PATH yields none and `env.CODEX_CLI_PATH` is an absolute executable path, it uses that (also through R9.1). The broker launches the resolved entrypoint, and its preflight equality checks keep holding.
- R9.4: Nothing inside the signed `CodexCLI.app` is created or modified: no repair symlink there.
- R9.5: Each of these keeps today's behaviour and error:
  - no manifest within 6 levels;
  - a `layoutVersion` other than 1;
  - an absolute or `..` entrypoint;
  - a missing or non-executable entrypoint;
  - malformed JSON.
- R9.6: Tests use fixtures for the old and new layouts plus every R9.5 negative case, in `commander/tests/test_codex_runtime_preflight.py` and `advisor-review.test.ts`. README and CODEX_SETUP note the new layout and how to repair a stale `~/.local/bin/codex` symlink.

## Non-goals

- Claude model handling.
- Any new config key.
- Contacting OpenAI to list models: only the local cache is read.
- Changing the turn-metadata authentication.
