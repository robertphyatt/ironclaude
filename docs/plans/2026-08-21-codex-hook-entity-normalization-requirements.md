# Codex hook trailing-entity normalization Requirements (operator-approved)

> **Created:** 2026-08-21
> **Source:** operator directive — the Codex deactivate/commit-push escape hatch is
> broken by a trailing `&#x20;`, causing a hard deadlock. "We can't be having
> operators having to intervene or deadlocks. I was extremely clear on this." Chose
> "Bug 2 first: the deactivate escape." Root cause proven in
> `2026-08-21-codex-deactivate-deadlock-findings.md` (committed `ae08357`, H-D).
> Human commits, no push. Scope mode: selective.

## Problem

Codex Desktop appends a trailing `&#x20;` (HTML-encoded space) to a skill-link/command
prompt. `worker/hooks/state-activator.sh:46` computes `TRIMMED_PROMPT` by stripping only
literal `[[:space:]]`, so the six-character `&#x20;` survives. Every hook command gate that
reads `TRIMMED_PROMPT` then misses: the codex deactivate link regex (`:145`, anchored
`\)$`), the exact deactivate string (`:148`), and the HUMAN_OPERATION exact/`link` matches
for `commit`/`commit-and-push`/`push`/`use-primary-checkout`/`return-to-managed-worktree`
(`:60` dollar form, `:65` `CODEX_COMMAND_LINK_RE`). Result: `professional_mode` is never set
`off` and no server-held git intent is issued — the operator is deadlocked in Codex.

## Approved scope

Normalize `TRIMMED_PROMPT` once (Approach A) so a trailing run of enumerated HTML
space-entities (interleaved with whitespace) is stripped after the existing whitespace
trim. Because every codex command gate reads the same `TRIMMED_PROMPT`, this single change
fixes deactivate AND commit/push in Codex, and it cannot widen the guard (each gate stays
`^…$`-anchored / exact-equality; only a TRAILING entity run is removed).

- **R1 — normalize `TRIMMED_PROMPT` (state-activator.sh:46).** After the leading/trailing
  whitespace trim, strip an end-anchored run of `&#x20;`, `&#32;`, `&nbsp;`, `&#160;`,
  `&#xa0;`, `&#xA0;`, or `[[:space:]]`. One `sed -E` substitution with an end-anchored
  alternation and a `+` quantifier (single `s///`, NO `:a … ta` label/branch, for BSD/macOS
  `sed` safety). Space entities only — do NOT decode arbitrary HTML entities.

- **R2 — one choke point, no per-gate edits.** Do not change the deactivate regex/exact
  string (`:138`/`:144`/`:148`), the HUMAN_OPERATION matchers (`:55`/`:60`/`:65`), the
  deactivate DB write, or the intent machinery. The `/deactivate…` slash grep (`:138`)
  reads raw `$USER_PROMPT` (the Claude path; Claude does not append `&#x20;`) and is left
  unchanged — R1 fixes only the codex-affected `TRIMMED_PROMPT` gates.

- **R3 — falsifiable hook tests** in `worker/hooks/tests/test-professional-mode-deactivation.sh`:
  - (a) POSITIVE deactivate: the codex deactivate Markdown link with a trailing `&#x20;`
    DEACTIVATES (`assert_deactivates`). Fails on current hook (RED), passes after R1 (GREEN).
  - (b) NEGATIVE deactivate (guard not widened): the same link with a ` now&#x20;` suffix
    still does NOT deactivate (`assert_ignored`) — stripping the trailing entity must not
    rescue a prose-suffixed link.
  - (c) POSITIVE HUMAN_OPERATION: the codex `commit-and-push` Markdown link with a trailing
    `&#x20;`, delivered with `hook_event_name:"UserPromptSubmit"`, reaches the
    HUMAN_OPERATION block — asserted by the presence of the block-only stdout string
    `Human intent issuance runtime is unavailable` (state-activator.sh:112, reachable ONLY
    inside `if [ -n "$HUMAN_OPERATION" ]` at :74). RED on current hook, GREEN after R1. No
    `node`/stub or real workspace-manager needed (the block is entered with an empty `cwd`).
  - (d) NEGATIVE HUMAN_OPERATION: the same `commit-and-push` link with a ` now&#x20;` suffix
    does NOT reach the block (`assert_not_contains` that same string) — proves the
    normalization does not admit a prose-suffixed command.

- **R4 — no regression.** The full hook suite
  (`worker/hooks/tests/test-professional-mode-deactivation.sh`) passes, including every
  existing deactivate/ignore case (`:88`–`:118`). Regression scope is bounded to the two
  bash files: `git diff --staged --name-only` MUST list ONLY `worker/hooks/state-activator.sh`
  and `worker/hooks/tests/test-professional-mode-deactivation.sh`. No TypeScript
  (state-manager/workspace-manager) or Python (commander) source is touched, so those
  suites cannot regress from this change — proving the staged file set is the no-regression
  evidence for them (stronger than running unrelated, slow/flaky suites). This satisfies the
  design's broader "run … for no regression" note by evidence rather than by execution.

- **R5 — staging only (PM on).** `git add -f` both files (repo-root-anchored `git -C`).
  Professional mode blocks the commit; the human commits. No push.

## Deploy (post-commit operator step, documented — not a plan task)

`state-activator.sh` runs from the stable dir `~/.claude/ironclaude-hooks/`
(`make deploy-hooks`) AND the codex plugin cache
`~/.codex/plugins/cache/ironclaude/ironclaude/1.1.6+codex.<buster>/hooks/`. BOTH must be
refreshed after commit or Codex keeps running the old hook (committed ≠ deployed). Deploy
writes outside the repo and follows the human commit, so it is surfaced to the operator
after the loop, not executed as a plan task.

## Non-goals

- Bug 1 — the unassigned-primary PUSH lane. A separate later loop; push authority was
  deliberately deferred (managed-worktree-only).
- Changing the deactivate DB write, the HUMAN_OPERATION intent machinery, or the `:138`
  slash grep.
- Decoding arbitrary HTML entities (only the enumerated trailing space entities).
