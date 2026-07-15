# d1374 — Suppress "Waiting on Robert" Heartbeat When No Operator Action Is Needed — Design

> **Created:** 2026-07-14
> **Status:** Design Complete

## Summary

The daemon's heartbeat (and its companion one-time Slack alert) surfaces a "⏳ *WAITING ON {operator_name}:*" section whenever `DaemonState._operator_waits` is non-empty. That dict is populated by `_maybe_capture_operator_wait()` in `src/ironclaude/main.py`, which pre-filters Brain status text with a broad regex (`_AWAITING_PHRASE_RE`) and then asks an LLM grader to classify whether the Brain is genuinely blocked on the operator.

Confirmed root cause (log evidence, `/tmp/ic/daemon.log:14222`): `operator_wait recorded for d1362: Waiting for the heartbeat labels to become idle.` — the grader classified ordinary narration about an *autonomous system condition* (a heartbeat label becoming idle, no human judgment involved) as `awaiting_operator: true`. This produced exactly the confusing "Waiting on Robert" heartbeat Robert observed on 2026-07-14 while workers were simply running (`ts=1784055455`, his reaction: "What does this even mean? This is confusing").

Because this signal is LLM-classified free text, no amount of prompt tuning drives its false-positive rate to zero — a differently-worded status update can trip it again. The fix therefore adds a deterministic, always-correct second signal (directives in `pending_confirmation` status) and OR's it with a **tightened** version of the existing classifier, rather than relying on classifier precision alone.

## Architecture

Two independent signals feed the same display, and the display fires only when their union is non-empty:

1. **Deterministic — `pending_confirmation` directive count.** New query in `post_heartbeat()`, same pattern as the existing `unworked` directive count at `main.py:2459-2461`. A directive in this status is, by construction, waiting on Robert's 👍/👎/🤔 Slack reaction — no classification needed, cannot false-positive.
2. **Classifier — tightened `_operator_waits`.** Same capture path (`_maybe_capture_operator_wait` → regex pre-filter → grader), but `_AWAITING_OP_SYSTEM`'s instructions are corrected: today the prompt only asks "is the Brain reporting it is waiting on the operator?" — it never asks the grader to distinguish a **decision/judgment call** ("approve the migration?") from **narration about a system condition resolving on its own** ("waiting for the heartbeat labels to become idle", "worker is waiting on tests to pass"). The fix adds that distinction plus the confirmed false-positive as a negative example.

`format_heartbeat()` in `notifications.py` already correctly suppresses the whole waits block when its inputs are empty (verified: `if waits or commander_waits:` at `notifications.py:108`) — no change needed there. The change is entirely in what feeds `waits` into it: today only `_operator_waits`; after the fix, `_operator_waits` merged with the new pending-confirmation entries.

**Explicitly deferred (not in this design):** correlating the Brain's pin-message/ledger `blocked` + `escalation_ts` escalation convention (`orchestrator_mcp.py:3134-3145`, `:3989-4031`) into this display. It isn't wired into any Slack-facing code today and would require new logic to map a specific wait to a specific pinned message/ledger task — a separate, larger change than this directive's stated scope.

## Components

- `src/ironclaude/main.py`
  - `_AWAITING_OP_SYSTEM` (lines 77-83): revise prompt to require a genuine operator decision/judgment call, explicitly excluding autonomous/system-state narration, with the d1362 phrasing as a negative example.
  - `post_heartbeat()` (lines 2421-2454): add a `pending_confirmation` count/list query (mirrors the existing `unworked` query at 2459-2461); merge its entries into the dict passed as `waits` to `format_heartbeat()`.
- `src/ironclaude/notifications.py`
  - No functional change expected to `format_heartbeat()` itself — its existing empty-check and per-entry rendering already handle an arbitrary `waits` dict. Confirm during implementation that a directive-sourced entry (no `worker_id` in the traditional sense) renders sensibly (e.g. key `d{directive_id}`, question = directive interpretation summary).
- **File-conflict check:** worker d1364-restart-loop-fix is editing `src/ironclaude/orchestrator_mcp.py` and `tests/test_orchestrator_mcp.py`. This design touches `main.py` and `notifications.py` (and their tests) exclusively — no file overlap, safe to execute in parallel.

## Data Flow

1. `post_heartbeat()` fires on its interval (unchanged timing).
2. It queries `directives` for `status='pending_confirmation'` (new) and merges those into a working `waits` dict alongside `dict(self._operator_waits)` (existing, now backed by the tightened classifier).
3. `format_heartbeat(waits=merged_dict, ...)` renders the WAITING ON section only if `merged_dict` (or `commander_waits`) is non-empty — same suppression logic as today, now fed by a trustworthy union instead of classifier-only state.
4. The one-time Slack alert in `_maybe_capture_operator_wait()` is unaffected in trigger mechanics (still fires once per new classified wait) but benefits from the same tightened classifier, so it stops firing on the same false-positive pattern.

## Error Handling

- If the new `pending_confirmation` query fails (locked db, etc.), follow the existing pattern at `main.py:2456-2470` (wrap in try/except, log a warning, don't block the rest of `post_heartbeat`) rather than letting it crash the heartbeat.
- Classifier tightening is a prompt-only change — existing fail-safe behavior (`_maybe_capture_operator_wait`'s try/except around `self._grader.grade(...)`, defaulting to "not captured" on any error) is unchanged.

## Testing Strategy

- `tests/test_main_validate.py` (`TestOperatorWaits`): add a case asserting that a grader-mocked response matching the d1362 pattern ("waiting for X to become idle" / a system-condition narration) does NOT populate `_operator_waits` once the tightened prompt's contract is exercised via a mocked grader returning `awaiting_operator: False` for that phrasing — the existing suite already validates true/false branch behavior structurally (`test_awaiting_phrase_but_grader_says_no_falls_through`), so this is a mock-input addition, not new plumbing.
- New test: `post_heartbeat` with a `pending_confirmation` directive present and `_operator_waits` empty → heartbeat still renders the WAITING ON section, sourced from the directive.
- New test: neither signal present → WAITING ON section is fully suppressed (regression guard for the exact reported symptom).
- `tests/test_notifications.py`: confirm `format_heartbeat` renders a directive-sourced entry sensibly (add one test case with a synthetic directive-shaped `waits` entry) — only if the entry shape requires any change; if the existing dict shape already fits, no new test needed there beyond documenting the shape via the main.py-side tests.
- No regression to the existing `[ACTION REQUIRED]` daemon→Brain nudge flow (`main.py:2287`, `main.py:2496`) — that is a distinct, unrelated mechanism (daemon reminding the Brain, not Brain reporting to the operator) and this change does not touch it; existing tests for it are untouched.

## Implementation Notes

- Scope is `hold`: this directive's stated problem only. Pin/ledger escalation correlation is future work, not this change.
- Per Robert's follow-up instruction, the implementation plan's final step (after tests pass and the fix is committed) must call `restart_daemon(directive_id=1374)` so the daemon picks up the new code and the directive is atomically marked complete before the SIGHUP-triggered restart (per the d1364 restart-loop-fix convention — pass `directive_id` so completion is recorded atomically).
