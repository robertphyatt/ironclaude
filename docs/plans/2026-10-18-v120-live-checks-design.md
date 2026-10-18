# v1.2.0 Live Checks Design

> **Created:** 2026-10-18
> **Status:** Design Complete

Loop: light — verification only: probes plus one findings addendum and one memory note; no code, hook, schema or git-operation change (~1 repo file). A failing probe is recorded and STOPs the task; its fix runs as its own full loop.

## Summary

v1.2.0 is squashed as `f53b78c` on origin `fe57e20`. It is deployed (Claude cache synced, `make codex-plugin-release` → `1.2.0+codex.20261018224918`, `make deploy-hooks`, Commander and ChatGPT restarted at 16:49) but not tagged or pushed. Before the lightweight tag, the operator asked for the live checks ("live check loop first"). These checks were Task 3 of `docs/plans/2026-10-18-v120-security-fixes-remediation.md`, paused when the lock and drift loops replaced that plan.

Operator decisions (2026-10-18):
- The security review's parser-gap findings stay out of v1.2.0. The operator chose "Backlog to run_git tool (Recommended)", which records them as test cases on the run_git argument-list tool backlog item. The three findings are git option abbreviations (`--amen`, `--del`), the Codex `execCommandApproval` argv join and re-split, and the `git branch` denylist.
- Push happens only on an explicit go. The tag is lightweight `v1.2.0`, created after the checks pass.

## Requirements

1. **R1 — run the approved live checks against the current deploy.** Run remediation Task 3 Steps 1-7 as written, with three changes:
   - a new start marker and log directory (`/private/tmp/claude-502/taf6/`), so log lines and files from the earlier 14:50 deploy cannot count as passes;
   - every `cmp` targets `f53b78c`'s files, including `execution-drift-check.sh` (`drift_cmp`), which today's fold changed;
   - the Codex cache listing must show `1.2.0+codex.20261018224918` first.
2. **R2 — record the checks.** Append a `## Post-deploy verification (f53b78c)` section to `docs/plans/2026-10-18-v120-security-fixes-findings.md` with every step's output and verdict (LIVE, FIXED, PASS, FAIL or UNVERIFIED-LIVE), then stage it with `git add -f`.
3. **R3 — record the backlog decision.** Add the three parser-gap findings as test cases to the run_git backlog memory note, and point MEMORY.md at it.
4. **R4 — failures stop the task.** A missed expectation is recorded and the task STOPs. It is never rerun until it passes. The fix becomes a new full loop folded into v1.2.0 before the tag.

## Operator-assisted steps

The operator performs:
- Step 4: in Slack, run `/ironclaude provider brain codex` and ask "what unfinished work is sitting idle right now?";
- Step 6: trigger tiering checks 1b, 2 and 3;
- Step 7: R4b items, defects 2, 5 and 9.

Steps 1-3 and 5 run in this session. Step 2 (A1) can run here because `ironclaude:reviewer` is in this session's agent list since `/reload-plugins`. An operator step left undone is recorded as `UNVERIFIED-LIVE`, not as a pass.

## Testing Strategy

No tests required: live verification of a deployed build. The expected results are those in remediation Task 3. Each expectation is falsifiable: a stale deploy fails `cmp`, an old log line predates the new marker, and a widened reviewer toolset names Bash.

## Implementation Notes

- Never write `~/.codex/config.toml` or `~/.claude/ironclaude-hooks-config.json`. Kill processes only by exact PID.
- After the findings are recorded: the operator folds the addendum into the v1.2.0 commit, then a lightweight tag `v1.2.0` is created, and a push happens only on go.
- The playtest session (`7b03ab92`) still needs `/reload-plugins` from the operator.
- Episodic memory: the 2026-10-10 "Commander non-functional with Codex" symptom ("professional mode is unset, worker-status access unavailable") was fixed by 2026-10-11, when the Brain answered via `get_worker_status`. The memory index entry is stale. Step 4/5 confirms the fix on `f53b78c`; a pass closes that entry. The 2026-10-11 F1 gap (Codex `collaboration.spawn_agent` not blocked by `brain-task-gate`) is not part of these checks and stays on its backlog.
