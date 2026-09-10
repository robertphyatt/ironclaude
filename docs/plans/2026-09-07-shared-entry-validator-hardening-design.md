# Shared-Entry Validator Hardening Design

> **Created:** 2026-09-07
> **Status:** Design Complete
> **Scope mode:** hold (fixed — this fix only)

## Summary

Harden workspace-manager `git.ts` `isSafeSharedEntry` so it rejects entries containing a newline (`\n`), carriage return (`\r`), or leading/trailing ASCII whitespace, and add a bounding test. This closes a verified defect (blind Fable diff review of the worktree-shared-resources self-serve change): the validator admits control chars and surrounding whitespace, so `addSharedResourceEntries` — whose write is line-based (`git.ts:230`, `` `${entry}\n` ``) — can persist a smuggled multi-line or padded entry that the "rejected entries are never written" contract says it must refuse.

## Root cause (verified against current source)

`isSafeSharedEntry` (`git.ts:236-245`) checks: empty, `!`/`#` prefix, `/`-prefix/absolute, trailing `/`, backslash, glob chars `[*?[\]]`, and any `..` path segment after `split('/')`. It does **not** check for `\n`, `\r`, or leading/trailing whitespace. Consequences, traced:

- `"ok\n../escape"` → `split('/')` yields `["ok\n..", "escape"]`; neither segment `=== '..'`, so it passes. `addSharedResourceEntries` writes it as two physical lines → `readSharedResourceConfig` (`git.ts:187-193`, splits on `/\r?\n/`, trims) returns `["ok", "../escape"]`. A `..` line is now persisted in config. `linkSharedResources` re-validates on read (`git.ts:274-277`) and refuses it — so there is **no path escape** (Important, not Critical) — but the config carries a permanently-erroring line, the returned `added`/`entries` misreport (the single joined string vs. the two persisted lines), and dedup breaks on retry (the `present` set holds trimmed single lines, so `present.has("ok\n../escape")` is always false → re-appended every call).
- `" models"` (leading space) → passes; written padded; live relink `path.join(primary, " models")` resolves to an absent source → `relinked: {}`, while the next allocation links `models` fine — a confusing, silent divergence.

No existing test bounds this: `git.test.ts`'s reject case uses `['../escape','/abs','a*','trailing/','!neg','#comment']` — no control-char/whitespace member.

## Architecture

Single-function change in `git.ts`, reusing the existing validator's structure. Add two rejection clauses to `isSafeSharedEntry` before it returns `true`:

1. Reject if the entry differs from its trimmed form (leading/trailing ASCII whitespace): `if (entry !== entry.trim()) return false;`
2. Reject any ASCII control character, which covers `\n`, `\r`, and tab: `if (/[\x00-\x1f\x7f]/.test(entry)) return false;`

Both are pure, allocation-free predicate additions. No change to `addSharedResourceEntries`, `linkSharedResources`, callers, or the write format. The validator remains the single choke point (it already gates both the write in `addSharedResourceEntries` and the read-time relink in `linkSharedResources`), so tightening it fixes every consumer at once.

## Components

- `worker/mcp-servers/workspace-manager/src/git.ts` — `isSafeSharedEntry`: add the whitespace and control-char rejections.
- `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts` — extend the existing "rejects unsafe entries" coverage with control-char and surrounding-whitespace members (`"ok\nescape"`, `"a\rb"`, `" models"`, `"models "`) asserting each is in `rejected` and NOT written to config, plus assert a clean entry with no surrounding whitespace still passes (guards against over-rejection).

## Data Flow

Unchanged. Entries flow Brain → orchestrator `configure_shared_resources` → WorkspaceClient → cli.js `configure-shared-resources` → `WorkspaceService.configureSharedResources` → `addSharedResourceEntries(isSafeSharedEntry gate)`. The only change is which entries the gate admits.

## Error Handling

A rejected entry is reported in the `rejected` array and never written — the existing contract, now honored for control-char/whitespace inputs too. No new error types; no raising.

## Testing Strategy

TDD in `git.test.ts`: add the new reject members (RED against the current validator — they currently pass validation and get written), then implement the two clauses (GREEN). Include a positive control (a clean entry still accepted) so the new clauses cannot over-reject. Run `npm test -- git.test`, then the full workspace-manager suite, and rebuild the bundle (the validator lives in the bundled `cli.js`).

## Implementation Notes

- Bundle: `isSafeSharedEntry` is compiled into `dist/{index,cli,hook-intent}.js`; `npm run build` after the source change and stage the three bundles alongside `git.ts`.
- Project-agnostic; no roleplaying-agents/pf2e specifics.
- This is a follow-up hardening on top of the (staged, uncommitted) worktree-shared-resources self-serve change; both land together for the operator's single commit.
