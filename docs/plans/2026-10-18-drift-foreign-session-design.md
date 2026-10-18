# Drift Check: Recognize Another Session's Work Design

> **Created:** 2026-10-18
> **Status:** Design Complete

Loop: full — guard/hook code shared by every session in a checkout.

## Summary

On 2026-10-18 the Feature flipper session (roleplaying-agents, `1d0b6602`) was blocked at Stop with EXECUTION DRIFT on `tools/portrait_gen/eye_gates.py`, `tools/portrait_gen/verify_loopc.py` and `tools/portrait_gen/tests/test_verify_loopc.py`. The Portrait session (`d855cc6f`) had changed them in-plan (professional mode on, `executing`, the files in its Task 1/Task 3 `allowed_files`; one subagent used a Bash `python3 - <<'EOF'` script). Neither cross-session exclusion fired: the writer log records only file-tool writes, and Portrait had never had a drift ledger.

Investigation with the Portrait session (operator: "we have proven that you can talk with the other sessions. Let's have you and portrait work out what happened vs what should have happened"):

- **What happened.** Portrait's process started ~2026-10-13 (its transcript dates from 2026-08-23; it was resumed many times). Hook scripts run fresh on every call (`hooks.json` invokes `bash $HOME/.claude/ironclaude-hooks/<script>.sh`), but hook registrations load when the process starts. Portrait never had the Bash drift-window registrations, so its shell commands opened no windows and wrote no ledger. VERIFIED by Portrait's probe: at 15:08:56, with every gate true (professional mode on, executing, in-progress row 13823 in current wave 1, toplevel `/Users/roberthyatt/Code/roleplaying-agents`, a `touch`), no drift window appeared; after the operator ran `/reload-plugins` in that session (~15:30, "Reloaded: 2 plugins · 28 skills · 10 agents · 30 hooks"), the same probe at 15:33:25 created `ironclaude-drift-window-d855cc6f-….main.toolu_011YpkXFXMVjfgZfD2FcmRWr.txt`.
- **What should have happened.** Portrait's in-plan shell edits land in its own ledger; Feature flipper's drift check finds them there (rule (b)) and does not block.

Operator decisions (2026-10-18): keep other sessions' context ("without needing to clear the context from the other sessions, I don't want that!") → `/reload-plugins`, not restarts; scope "Identity compare + deploy note (Recommended)".

## Requirements

1. **R1 — compare foreign-ledger roots by file identity.** `execution-drift-check.sh:446` compares `<ledger>.root` to this root as a string. The writer-log rule (a) already matches by identity, per the 2026-10-03 operator decision to compare paths by identity because `code`/`Code` (and symlink) spellings differ. Use `[ "$ledger_root" -ef "$root" ]` (empty or nonexistent → no match).
2. **R2 — deploy steps name `/reload-plugins`.** The 1.2.0 CHANGELOG `Deploy:` line and the README say that open Claude Code sessions must run `/reload-plugins` after an IronClaude update, or they keep running the hook registrations they started with (context is kept).

## Testing Strategy

RED first, in `worker/hooks/tests/test-execution-drift-check.sh`: case 54 — another live session's in-progress ledger records this repository through a symlinked path (same directory, different spelling); this session's write of that file does not block (rc 0). It fails today (string compare → rc 2) and passes with R1. Existing cases 46 (same-spelling positive) and 47 (`/nonexistent/other-repo` negative) keep the rule bounded. `EXPECTED_PASSES` 53 → 54. Then `make test-hooks`.

## Implementation Notes

- No change to Portrait or playtest beyond the operator's `/reload-plugins` (playtest `7b03ab92` still needs it).
- Backlog (not this loop): a stale-hook-registration warning; the post-deploy stale `index.lock` at 15:15 (not from IronClaude hooks — see memory `project_backlog_git_index_lock_recurs`).
