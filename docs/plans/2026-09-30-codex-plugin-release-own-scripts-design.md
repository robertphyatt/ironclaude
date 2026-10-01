# Repo-Owned Codex Plugin Release Scripts — Design

> **Created:** 2026-09-30
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-30-codex-plugin-release-own-scripts-requirements.md

## Summary

`make codex-plugin-release` broke because a ChatGPT.app/Codex update removed the two plugin-creator scripts it called: a cachebuster and a validator. This design replaces them with two small Node scripts owned by the repo, in `worker/scripts/`, next to `codex-runtime-preflight.mjs`. A Codex update can no longer break the release target, and the target keeps its guarantees:
- a fresh stamp before the final build;
- validation of the bytes that will be installed;
- the install itself.

## Components

### 1. `worker/scripts/codex-plugin-cachebuster.mjs`

- **Usage:** `node worker/scripts/codex-plugin-cachebuster.mjs <pluginDir> [--stamp YYYYMMDDHHMMSS]`.
- **Steps:**
  1. Read `<pluginDir>/.codex-plugin/plugin.json` as text and `JSON.parse` it.
  2. Require `version` to match `^(\d+\.\d+\.\d+)(\+codex\.\d{14})?$`.
  3. Compute the stamp: `--stamp`, which must be exactly 14 digits, or the current UTC time formatted as `YYYYMMDDHHMMSS`.
  4. Compute `next = <release>+codex.<stamp>`.
  5. Replace the exact substring `"version": "<previous>"`.
     - It must occur exactly once; otherwise the script errors.
     - A full re-serialize would turn the file's `—` escape into a literal dash.
  6. Write the file back and print `{"previous","version"}`.
- **Errors:** every error path prints `{"error": "..."}` to stderr, exits 1 and leaves the file untouched.

### 2. `worker/scripts/validate-codex-plugin.mjs`

- **Usage:** `node worker/scripts/validate-codex-plugin.mjs <pluginDir>`.
- It collects every error before reporting.
- **Checks:**
  - the manifest parses;
  - `name === "ironclaude"`;
  - the version is stamped (`^\d+\.\d+\.\d+\+codex\.\d{14}$`);
  - `skills` is a relative path, resolves inside `pluginDir`, is a directory, and has at least one immediate child directory containing `SKILL.md`;
  - `mcpServers` is an object, and each entry's `args[0]` is a relative string that resolves inside `pluginDir` to an existing regular file;
  - `mcp-servers/state-manager/dist/index.js` is an existing regular file.
- **Containment:** a path counts as inside `pluginDir` when `path.relative(root, resolved)` does not start with `..` and is not absolute.
- **Output:** `{"valid", "errors"}` on stdout; exit 0 if valid, else 1.

### 3. Makefile

Only the two `python3 …plugin-creator…` lines change:

```
codex-plugin-release: codex-runtime-preflight
	node worker/scripts/codex-plugin-cachebuster.mjs worker
	cd worker/mcp-servers/state-manager && npm exec -- tsc --noEmit && npm run bundle
	node worker/scripts/validate-codex-plugin.mjs worker
	codex plugin add ironclaude@ironclaude --json
```

The Makefile comment "repair the host companion, cachebust, build, validate, then install the same bytes" stays accurate.

### 4. Docs

`worker/skills/executing-plans/SKILL.md:668`: "applies the plugin-creator cachebuster before the final build" becomes "applies the IronClaude cachebuster before the final build".

## Testing Strategy

New file `commander/tests/test_codex_plugin_release_scripts.py`, which runs `node` against `tmp_path` fixtures.

- **Stamper:**
  - an unstamped `X.Y.Z` gets `+codex.<stamp>`;
  - an existing stamp is replaced;
  - every other byte is unchanged, including a `—` escape;
  - without `--stamp`, the stamp is 14 digits within ±1 day of UTC now;
  - each of these fails with exit 1 and leaves the file unchanged: malformed version, malformed `--stamp`, missing file, bad JSON.
- **Validator:**
  - a good fixture passes;
  - each check fails on its own fixture: bad name, unstamped version, `skills` escaping the plugin, `skills` with no `SKILL.md`, an MCP `args[0]` that is missing, an absolute `args[0]`, a missing `dist/index.js`, bad JSON;
  - multiple errors are all reported.
- **Ordering:** update the token list in `test_executing_plans_skill.py:282-288` to `codex-plugin-cachebuster.mjs worker` and `validate-codex-plugin.mjs worker`.
- **Suites:** commander pytest.

## Implementation Notes

- Node ESM, matching the preflight script's style.
- Keep scope to the target. No release or version bump.
