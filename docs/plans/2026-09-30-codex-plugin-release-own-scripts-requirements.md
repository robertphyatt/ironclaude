# Repo-Owned Codex Plugin Release Scripts — Requirements

> **Created:** 2026-09-30
> **Scope mode:** hold

## Operator directives

- The operator said: "reactivate pm and do this now — The Makefile will need a follow-up fix." This refers to the broken `make codex-plugin-release`.
- The operator chose "A: repo-owned scripts (Rec.)" over "B: drop both steps" and "C: locate Codex's, fall back".
- The operator approved the design with "Yes, write it up (Rec.)", keeping the validator.

## Facts (verified 2026-09-30)

- `Makefile:24-28` runs `python3 $(HOME)/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py worker` and `.../validate_plugin.py worker`. Both files are absent.
  - `~/.codex/skills/.system` now holds only imagegen, openai-docs, review-agent, skill-creator and skill-installer.
  - The only surviving copy of plugin-creator, `~/.codex/.tmp/plugins/.agents/skills/plugin-creator`, holds only `create_basic_plugin.py`.
- Episodic memory has no record of what the removed scripts did.
- `worker/.codex-plugin/plugin.json` version format is `<X.Y.Z>+codex.<YYYYMMDDHHMMSS>` (currently `1.1.15+codex.20260930172837`). Releases generate the stamp with `date -u +%Y%m%d%H%M%S`. The file contains a `—` JSON escape.
- `commander/tests/test_version_consistency.py` accepts `<release>(+codex.[a-z0-9-]+)?` and requires every declared version to match.
- `commander/tests/test_executing_plans_skill.py:257-290` asserts this `make -n codex-plugin-release` order: preflight repair, `update_plugin_cachebuster.py worker`, `npm run bundle`, `validate_plugin.py worker`, `codex plugin add ironclaude@ironclaude --json`.
- `worker/skills/executing-plans/SKILL.md:668` says "applies the plugin-creator cachebuster before the final build". Test `:229` pins "cachebuster before the final build".

## Requirements

- **R1 — Stamper.** Add `worker/scripts/codex-plugin-cachebuster.mjs <pluginDir> [--stamp <14 digits>]`.
  - It reads `<pluginDir>/.codex-plugin/plugin.json`.
  - It requires `version` to match `^\d+\.\d+\.\d+(\+codex\.\d{14})?$`.
  - It rewrites only the version string to `<X.Y.Z>+codex.<stamp>`. The stamp is the current UTC time as `YYYYMMDDHHMMSS`, or the `--stamp` value.
  - The rewrite is an exact text replacement. Every other byte of the file is unchanged.
  - It prints one JSON object `{"previous": ..., "version": ...}` and exits 0.
  - On a missing file, unparseable JSON, a malformed version or a malformed `--stamp`, it exits non-zero, writes nothing, and prints an error.
- **R2 — Validator.** Add `worker/scripts/validate-codex-plugin.mjs <pluginDir>`. It reports every failed check rather than stopping at the first. The checks are:
  - the manifest parses;
  - `name === "ironclaude"`;
  - `version` matches `^\d+\.\d+\.\d+\+codex\.\d{14}$`;
  - `skills` is a relative path inside `pluginDir` naming a directory with at least one `*/SKILL.md`;
  - every `mcpServers` entry has an `args[0]` that is a relative path inside `pluginDir` to an existing file;
  - `mcp-servers/state-manager/dist/index.js` exists.

  It prints `{"valid": bool, "errors": [...]}` and exits 0 only when every check passes, 1 otherwise.
- **R3 — Makefile.** `codex-plugin-release` calls the two repo scripts instead of the removed Python scripts, with its order unchanged: preflight, then stamper, then tsc and bundle, then validator, then `codex plugin add`.
- **R4 — Docs.** In the executing-plans SKILL, "plugin-creator cachebuster" becomes "IronClaude cachebuster". The pinned phrase "cachebuster before the final build" is kept.
- **R5 — Tests.**
  - Add pytest coverage for R1 and R2 using temporary fixtures, with one case per failure mode.
  - Update the `make -n` ordering test to the new script names.

## Non-goals

- No version bump or release in this effort. A plain local commit is acceptable.
- No change to `codex-plugin-install`, the preflight script, or the deploy procedure beyond R3.
- No rediscovery of Codex's removed tooling.
