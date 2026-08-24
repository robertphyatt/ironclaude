# Codex deactivate-deadlock — Findings

> **Created:** 2026-08-21
> **Verdict: H-D — the deactivate branch never fired.** A trailing `&#x20;` (an HTML-encoded
> space that Codex Desktop appended to the skill-link message) survives `state-activator.sh`'s
> whitespace-only trim and breaks the `\)$`-anchored codex-link regex, so `DEACTIVATE_REQUEST`
> stayed false and no `UPDATE sessions SET professional_mode='off'` ever ran.
> Strictly read-only investigation; nothing was changed.

## Root cause (H-D, definitive)

The operator's exact message for the 21:51 deactivate turn (codex transcript
`~/.codex/sessions/2026/08/02/rollout-2026-08-02T12-15-29-019fc3b0-…jsonl`, **line 44212**,
`role:user` `input_text`):

```
[$ironclaude:deactivate-professional-mode](/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.6+codex.20260821201512/skills/deactivate-professional-mode/SKILL.md)&#x20;
```

Note the trailing **`&#x20;`** (the HTML entity for U+0020 space) after the closing `)`. In
`worker/hooks/state-activator.sh`:
- `TRIMMED_PROMPT` (:46) = `sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'` — trims only literal
  whitespace. `&#x20;` is six literal characters (`& # x 2 0 ;`), NOT whitespace, so it survives.
  After trimming the newline, `TRIMMED_PROMPT` ends with `…SKILL.md)&#x20;`.
- `CODEX_DEACTIVATE_LINK_RE` (:144) =
  `^\[\$ironclaude:deactivate-professional-mode\]\(/[^)[:cntrl:]]*/skills/deactivate-professional-mode/SKILL\.md\)$`
  — anchored on `\)$` (the string must END at the closing paren). `…SKILL.md)&#x20;` has six more
  characters after `)`, so the regex does NOT match.
- The other two deactivate gates also miss: the `:138` slash regex matches only `/deactivate…`
  (not a `[...]` markdown link), and the `:148` exact check requires
  `TRIMMED_PROMPT == '$ironclaude:deactivate-professional-mode'` (it is the whole link+`&#x20;`).

All three conditions false → `DEACTIVATE_REQUEST="false"` → the `if [ "$DEACTIVATE_REQUEST" =
"true" ]` block (:153-179) is skipped → **no `UPDATE`, no audit row, no warning**. The agent then
verified `get_professional_mode` → `on` and correctly reported "deactivation FAILED" (line 44224).

## Evidence ruling out the other hypotheses

| Hypothesis | Ruled out by |
|---|---|
| **H-A** (hook never runs in codex) | `hooks.json:145-150` wires `bash $HOME/.claude/ironclaude-hooks/state-activator.sh` on `UserPromptSubmit`; `~/.codex/config.toml:200-208` wires `hooks.json:user_prompt_submit:0/1/2`. **A1:** `~/.claude/ironclaude-state-cache-019fc3b0-…json` (written ONLY by state-activator.sh :214-220) exists with `timestamp 2026-08-21T21:52:50Z` — the hook demonstrably ran on that session's prompts. Refuted. |
| **H-B** (UPDATE hits 0 rows — key mismatch) | The `sessions` row EXISTS under the exact full-id key: `SELECT … WHERE terminal_session LIKE '%019fc3b0%'` → `019fc3b0-… | on | 2026-08-21 21:43:37`. The hook's `SAFE_SESSION` = that full id (the state-cache filename proves it), so an UPDATE WOULD have matched (1 row). Refuted — the UPDATE simply never ran. |
| **H-C** (split-DB) | `grep STATE_MANAGER_DB_PATH ~/.codex/config.toml` → not set → the codex `state-manager` MCP's `getDbPath()` (state-manager `src/db.ts:30-35`) resolves the SAME `~/.claude/ironclaude.db` the hook writes. Refuted. |
| Corroboration | `audit_log` has many `hook:state-activator | professional_mode_off | on→off` rows for this session but the LAST is **2026-08-16 19:14** — **none on 2026-08-21** (success path never ran). The "Deactivation UPDATE affected 0 rows" warning count in the transcript is **0** (consistent with branch-never-fired; and it's stdout-only anyway). Both consistent with H-D, not H-B. |

Deactivation genuinely worked for this session before (audit rows through Aug 16) — so this is a
regression in *input handling*, not a never-worked path. (The 08-13/08-14 `daemon:brain_init`
`professional_mode_off` rows are a different actor and unrelated.)

## Impact

The `sessions.professional_mode` UPDATE is gated behind `DEACTIVATE_REQUEST`, and the same
trailing-`&#x20;` artifact would also break the HUMAN_OPERATION exact-string matches
(state-activator.sh:55/:60 for `/commit`, `/commit-and-push`, `/push`, etc., and the `:148` exact
deactivate string) — any Codex-Desktop skill-link/command that arrives with a trailing `&#x20;`
(or another appended entity) silently no-ops the hook. Combined with Bug 1 (push-from-primary-
checkout unsupported), this is the hard deadlock: the escape hatch is broken by input encoding.

## Recommended fix (follow-up loop)

Make `state-activator.sh`'s prompt normalization tolerant of Codex-Desktop's trailing HTML
entities before the gates run. Options (for the fix loop to weigh):
1. Decode/strip trailing HTML space entities (`&#x20;`, `&#32;`, `&nbsp;`, `&#160;`) in
   `TRIMMED_PROMPT` after the whitespace trim (smallest, targeted).
2. Relax `CODEX_DEACTIVATE_LINK_RE` (and the HUMAN_OPERATION exact-string matches) to tolerate
   trailing whitespace/entities — e.g. match the link as a prefix, or add `(&#x20;|[[:space:]])*$`.
   MUST NOT widen to admit prose/embedded links (keep the existing "prose cannot deactivate"
   guarantee — the codex regex is deliberately exact).
Add a hook test seeding a `.prompt` with a trailing `&#x20;` and asserting deactivation fires; the
same normalization must be verified for the commit/push HUMAN_OPERATION matches. Deploy path: the
hook runs from the stable dir `~/.claude/ironclaude-hooks/` (`make deploy-hooks`) AND the codex
plugin cache — both must be refreshed, or codex keeps running the old `state-activator.sh`.

## Open / not covered
- Whether every Codex-Desktop skill-link arrives with the trailing `&#x20;`, or only when the
  operator's text had a trailing space, is not established here — the fix should tolerate it
  regardless. Bug 1 (unassigned-primary push) remains a separate loop.
