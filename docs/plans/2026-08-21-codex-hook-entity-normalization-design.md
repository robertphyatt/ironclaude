# Codex hook trailing-entity normalization (deactivate/commit/push fix) Design

> **Created:** 2026-08-21
> **Status:** Design Complete
> **Scope mode:** selective (single choke-point fix + test; no widening of the exact-match guard)

## Summary

Codex Desktop delivers a skill-link/command prompt with a trailing `&#x20;` (an HTML-encoded
space) appended after the link — proven in
[[2026-08-21-codex-deactivate-deadlock-findings]] (committed `ae08357`): the operator's
`$ironclaude:deactivate-professional-mode` arrived as `[$ironclaude:deactivate-professional-mode](…/SKILL.md)&#x20;`.
`worker/hooks/state-activator.sh` computes `TRIMMED_PROMPT` (`:46`) by stripping only literal
`[[:space:]]`, so the six-character `&#x20;` survives, and every hook command gate then misses:
the `\)$`-anchored codex-link regex (`:144`), the exact deactivate string (`:148`), and the
HUMAN_OPERATION exact command matches for `commit`/`commit-and-push`/`push`/`use-primary-checkout`/
`return-to-managed-worktree` (`:55`, `:60`). Result: `DEACTIVATE_REQUEST`/`HUMAN_OPERATION` stay
unset, the `sessions` UPDATE / server-held intent never happens, and the operator is deadlocked
(the escape hatch and the git commands silently no-op in Codex).

**Fix (Approach A):** normalize `TRIMMED_PROMPT` once — after the existing whitespace trim, also
consume a trailing run of HTML space-entities interleaved with whitespace, end-anchored. Because
all gates read the same `TRIMMED_PROMPT`, this single change fixes deactivate AND
commit/push/activate in Codex, and it cannot admit prose (the remainder of the string must still
exactly match a link regex or an exact command). This closes the "no operator intervention / no
deadlocks" violation for the codex hook path.

## Architecture

Extend the `TRIMMED_PROMPT` computation at `state-activator.sh:46`. Today:
```bash
TRIMMED_PROMPT=$(printf '%s' "$USER_PROMPT" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
```
New: after the whitespace trim, iteratively strip a **trailing** run of the HTML space-entities
`&#x20;`, `&#32;`, `&nbsp;`, `&#160;`, `&#xa0;` (hex case-insensitive) plus any interleaved
whitespace, so `…SKILL.md)&#x20;` → `…SKILL.md)` and `…)&#x20; \n` → `…)`. Concretely, a second
`sed -E` pass with an end-anchored alternation repeated to fixpoint, e.g.
`:a; s/(&#x20;|&#32;|&nbsp;|&#x?[0]*(20|A0|a0);|[[:space:]])+$//; ta` (final form verified against
`sed` during writing-plans). Leading normalization is unchanged.

Key properties:
- **One choke point.** All command gates (`:138`/`:144`/`:148` deactivate, `:55`/`:60`
  HUMAN_OPERATION) consume `TRIMMED_PROMPT`, so normalizing it fixes every codex hook command; no
  per-gate edits.
- **Cannot widen the guard.** Only a TRAILING run is stripped, and every gate is still
  `^…$`-anchored / exact-equality: a prose message containing `&#x20;` mid-string, or extra text
  after the link, still fails to match. The "prose/quoting/prefix-suffix cannot deactivate"
  guarantee (state-activator.sh:141-143) is preserved.
- **Space entities only.** We do NOT decode arbitrary HTML entities (that could alter the link
  path or admit unintended input); we strip only the enumerated trailing space entities.

## Components

- `worker/hooks/state-activator.sh` — the `TRIMMED_PROMPT` normalization (:46). Single edit.
- `worker/hooks/tests/test-professional-mode-deactivation.sh` — add cases: (a) a codex link prompt
  with a trailing `&#x20;` DEACTIVATES (currently fails); (b) NEGATIVE — a prose message that merely
  contains the link plus other text, or `&#x20;` mid-string, still does NOT deactivate (guard not
  widened); (c) a HUMAN_OPERATION case: `$ironclaude:commit&#x20;` (or the codex commit link) sets
  `HUMAN_OPERATION=commit` / issues the intent, proving the shared fix covers commit/push.

## Data Flow

Codex `user_prompt_submit` → hook stdin `.prompt` = `[$ironclaude:…](…/SKILL.md)&#x20;` →
`USER_PROMPT` → normalized `TRIMMED_PROMPT` (trailing `&#x20;` stripped) = `[$ironclaude:…](…/SKILL.md)`
→ the `:144` regex matches → `DEACTIVATE_REQUEST=true` → `UPDATE sessions … professional_mode='off'`
→ `get_professional_mode` reads `off`. Same path unblocks the HUMAN_OPERATION intent for
commit/push.

## Error Handling

- A prompt with only whitespace/entities → strips to empty; the empty-prompt `exit 0` (:38-40)
  path is unchanged (nothing to do).
- A `&#x20;` in the middle of a longer message → not at end-of-string → not stripped → the anchored
  gates still fail (correct: prose does not trigger commands).
- The entity list is fixed and space-only; an unrecognized entity is left intact (fails the gate,
  as today) rather than decoded.

## Testing Strategy

`worker/hooks/tests/test-professional-mode-deactivation.sh` (bash hook test; the existing suite
seeds a prompt JSON and asserts the `sessions.professional_mode` result). RED→GREEN: add the
trailing-`&#x20;` deactivate case (fails on current `state-activator.sh`, passes after the trim
change); add the negative prose case (must stay non-deactivating both before and after); add the
HUMAN_OPERATION `commit` trailing-`&#x20;` case. Run the full hook suite; then the state-manager,
workspace-manager, and commander suites for no regression.

## Implementation Notes

- **Deploy (local, no push):** `state-activator.sh` runs from the stable dir
  `~/.claude/ironclaude-hooks/` (`make deploy-hooks`) AND the codex plugin cache
  (`~/.codex/plugins/cache/ironclaude/ironclaude/1.1.6+codex.<buster>/hooks/`) — BOTH must be
  refreshed or Codex keeps running the old hook. This is the same "committed ≠ deployed to the
  codex cache" gap seen this session ([[project_local_deploy_facts]]).
- Selective scope: no change to the gate regexes/exact-match logic, the deactivate DB write, or
  the HUMAN_OPERATION intent machinery — only the shared input normalization.
- Bug 1 (unassigned-primary push lane) is a SEPARATE later loop; push authority was deliberately
  deferred (managed-worktree-only). Human commits, no push.
