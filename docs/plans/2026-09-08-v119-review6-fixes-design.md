# v1.1.9 6th-Review Fixes Design

> **Created:** 2026-09-08
> **Status:** Design Complete
> **Scope mode:** hold — the five 6th-Fable-review items, all fixed.

## Summary

The 6th Fable corpus review of v1.1.9 (`d88ba6e`, unpushed) returned SHIP with five
non-blocking observations. Each was verified against live source; all five are fixed
this loop.

- **(a) Codex-Brain gate fail-open — REAL (substantive).**
  `worker/hooks/codex-brain-gated-actions.sh:5` sources `hook-logger.sh` **unguarded**,
  so a missing logger makes the whole Codex-Brain gate fall through fail-**open** —
  the exact defect obs-3 closed for the three Claude gates, never applied to the Codex
  twin. Fix: selective fail-**closed** mirroring the hook's own logic.
- **(b) Undocumented block/allow tightening — minor doc.** Wiring memory-search into
  the settings-sync (backlog #1) deploys the current script, which gates
  `AskUserQuestion` (added since `747e19d`) — a real prod block/allow change the
  CHANGELOG bullet doesn't name. Fix: add a clause.
- **(c) Template-form drift risk — minor test.** No test pins the shipped
  `brain_settings_hooks.json` command form; a `~` vs `$HOME` drift would double-register
  memory-search (which `rm -f`s its arm flag per pass → blocks every gated action). Fix:
  assert every template command uses `$HOME/` (not `~`).
- **(d) Weak workflow-lookback guard — minor test.** `test_directive_workflow_read_arms_gate`'s
  positive assert `"hours_back=72" in text` is satisfied by `workflow.md:727` regardless
  of `:760`, so a `:760` regression to `hours_back=12` would pass. Fix: anchor on the
  `:760` line's full string.
- **(e) Corrupted design doc — REAL (bounded byte-level fix).**
  `docs/plans/2026-09-07-shared-entry-validator-hardening-design.md` (tracked, in the
  `d88ba6e` corpus) contains three **raw ASCII control bytes** — NUL (0x00), 0x1F, and
  0x7F — on line 25, inside a regex code span: the author typed the actual control chars
  instead of their escapes, so the byte sequence is `[` NUL `-` 0x1F 0x7F `]` where the
  readable intended text is `[\x00-\x1f\x7f]`. Git flags the file **binary** via its
  NUL-in-first-8000-bytes heuristic (NOT invalid UTF-8 — the file decodes fine, and its
  em-dashes/arrows are valid UTF-8, left untouched). Fix: byte-level replace of exactly
  those three control bytes with their escaped ASCII text.

## Architecture

One substantive guardrail-tighten (a), three doc/test hardenings (b/c/d), one
encoding repair (e). Only (a) changes a hook's behavior — and only in the
missing-logger branch, tightening open→closed (never loosening).

### (a) Codex gate selective fail-closed

`codex-brain-gated-actions.sh` currently: `source hook-logger.sh` (:5) → `run_hook`
→ read INPUT → `if IC_ROLE!=brain || IRONCLAUDE_CLIENT!=codex: exit 0` (:15-17) →
`case TOOL_NAME` classifying arm tools (memory/wiki/slack/ledger → touch state, exit 0),
game tools (block unconditionally), the `action` set (spawn_worker, spawn_workers,
approve_plan, reject_plan, send_to_worker, kill_worker, ollama pull/remove/create — both
`mcp__plugin_ironclaude_orchestrator__*` and `mcp__orchestrator__*` / `mcp__ollama__*`
variants), and `*) exit 0`.

Guard the source (mirror obs-3's pattern): on `source` failure, first preserve the
role/client early-exit (non-codex-brain → `exit 0`), then parse `tool_name` with raw
`jq` and block **only** the gated set — the full `action` set AND the game set (both
naming variants), with a loud stderr message + `exit 2`; arm tools, reads, and
everything else `exit 0`. The gated case list must be token-for-token identical to the
hook's own `action`+game cases. Hand-roll the `echo >&2; exit 2` (the `block_pretooluse`
helper is the missing piece). Extend `worker/hooks/test-codex-brain-gated-actions.sh`
with missing-logger cases: a gated action (codex-brain env) → 2; an arm/read tool
(codex-brain env) → 0; a gated action under non-codex-brain env → 0.

### (b) CHANGELOG clause

Extend the existing "Post-review hardening" bullet (CHANGELOG.md, `## 1.1.9`) to name
that deploying memory-search-enforcer also activates `AskUserQuestion` gating on the
Codex/Claude Brain in production.

### (c) Template-form guard

In the existing `test_brain_settings_hooks_template_lists_all_three_gates`
(test_daemon.py), add an assertion that every PreToolUse command starts with
`bash $HOME/.claude/ironclaude-hooks/` (no `~`), so a `~`-vs-`$HOME` drift that would
double-register a gate fails the test.

### (d) Workflow-lookback guard tightening

In `test_directive_workflow_read_arms_gate` (test_brain_doc_guards.py), replace the
bare `"hours_back=72" in text` with an anchor on the `:760` line, e.g. assert
`"get_operator_messages(limit=20, hours_back=72)` to read raw Slack messages." in text`
(keep the `"hours_back=24" not in text` assertion). This fails if `:760` regresses to
any non-72 value.

### (e) Design-doc control-byte repair

`docs/plans/2026-09-07-shared-entry-validator-hardening-design.md` line 25 reads (via
`cat -v`) `if (/[^@-^_^?]/.test(entry))` — but `^@`/`^_`/`^?` are `cat -v`'s rendering of
raw NUL (0x00), 0x1F, and 0x7F bytes; the actual byte sequence is `[` NUL `-` 0x1F 0x7F
`]`. The readable intended code span is `[\x00-\x1f\x7f]` (the design prose says "any
ASCII control character, which covers `\n`, `\r`, and tab"; the DEL byte 0x7F is present,
so `\x7f` is restored, not dropped to the shipped `[\x00-\x1f]`). Fix by a **byte-level**
replace of exactly the three control bytes with their escaped text — read the file as
bytes, `replace(b"[\x00-\x1f\x7f]", b"[\\x00-\\x1f\\x7f]")`, write back as bytes. The
valid em-dashes/arrows are left untouched (no whole-file rewrite). Two pitfalls the plan
must avoid: (1) transcribing `cat -v`'s `[^@-^_^?]` as literal ASCII ships a wrong
negated-character-class regex; (2) writing through an escape-interpreting path
re-corrupts the NUL — so the edit is `rb`→`wb`.

## Components

- `worker/hooks/codex-brain-gated-actions.sh` (a)
- `worker/hooks/test-codex-brain-gated-actions.sh` (a test)
- `CHANGELOG.md` (b)
- `commander/tests/test_daemon.py` (c — extend the template-coverage guard)
- `commander/tests/test_brain_doc_guards.py` (d)
- `docs/plans/2026-09-07-shared-entry-validator-hardening-design.md` (e)

## Data Flow

No runtime data-flow change. (a) only changes the missing-logger branch of one gate.

## Error Handling

(a) is an error-handling change (fail-open → fail-closed on missing dependency), loud
via stderr + exit 2, recovery operator-side (`make deploy-hooks`).

## Testing Strategy

- **(a):** extend the bash suite with the three missing-logger cases (RED before the
  guard: current hook fails open → gated action exits 0 where 2 is expected). `make
  test-hooks` runs it (worker/hooks/test-*.sh glob).
- **(c)/(d):** pytest RED→GREEN on the tightened assertions.
- **(b):** grep-verify the clause landed (doc prose, no test).
- **(e):** RED (measured now): the file contains a NUL byte (`grep -a -c -P "\x00"` → 1)
  / git treats it binary. GREEN after the byte-level fix: no NUL (`grep -a -c -P "\x00"`
  → 0), the literal escaped regex `/[\x00-\x1f\x7f]/.test(entry)` present
  (`grep -F`), and `isSafeSharedEntry` still present. No permanent test (static artifact).
  There is NO UnicodeDecodeError RED — the file decodes fine today.
- **Definitive gate:** full commander `pytest -q` `0 failed` + `make test-hooks` exit 0.

## Implementation Notes

- (a) gated case list = token-for-token the hook's `action`+game cases (both
  `mcp__plugin_ironclaude_orchestrator__*` and `mcp__orchestrator__*`/`mcp__ollama__*`
  variants); ground it against current source in writing-plans and mirror the obs-3
  fail-closed shape (`_IN=$(cat)`, raw jq, exit 2, then `exit 0` fallthrough).
- Ground the `test-codex-brain-gated-actions.sh` harness (how it sets IC_ROLE /
  IRONCLAUDE_CLIENT / IRONCLAUDE_BRAIN_GATE_SESSION, copies the hook) before writing
  the missing-logger case.
- (e): the fix is a byte-level `rb`→`wb` replace of the single corrupted line-25 regex
  span (`b"[\x00-\x1f\x7f]"` → `b"[\\x00-\\x1f\\x7f]"`); do NOT whole-file-rewrite and do
  NOT transcribe `cat -v`'s `[^@-^_^?]` literally. The `—`/`→` are valid and untouched.
- Boy-Scout note (out of scope, record only): the shipped validator `git.ts:245` uses
  `[\x00-\x1f]` (no `\x7f`) while this design doc intends `[\x00-\x1f\x7f]` — the shipped
  code admits DEL (0x7F). A separate backlog candidate; this loop only repairs the doc's
  bytes, not the shipped regex.
- Design/plan artifacts under repo-root `docs/plans/`.
