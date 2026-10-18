# Re-stage Gitignored Paths Design

> **Created:** 2026-10-16
> **Status:** Design Complete

Loop: full. The change touches git staging, which the scope rule treats as a risk area, though it is small: about 2 files and 30 lines.

## Summary

The cumulative re-stage script in `worker/skills/executing-plans/SKILL.md` stages each allowed path with `git add -- <path>`, without `-f`. The block is marked `# BEGIN/END EXACT ALLOWED FILES RESTAGING` and now sits in Step 5 (uncommitted change `2026-10-16-restage-at-wave-boundary`).

In this repo `docs/` is gitignored, so every plan's own `docs/plans/*` files are ignored paths. A path counts as ignored even after it is in the index. When the script was first run for real on 2026-10-16, git refused the first docs path ("The following paths are ignored by one of your .gitignore files: docs … Use -f if you really want to add them") and the script exited 1.

Episodic memory gives two operator positions on force-adding:
- **For docs:** on 2026-06-27 and 2026-08-17 the operator approved `git add -f` for `docs/plans` artifacts.
- **Against secrets:** on 2026-04-03 the operator corrected an agent for staging the gitignored `commander/.env`, which held secrets, and said it must never be tracked.

## Requirements

- **R1:** in the script's staging loop, handle each path by its state:
  - **In the index already** (in the script's `tracked` set from `git ls-files`): stage it with `git add -f`.
  - **Untracked and not ignored:** stage it with plain `git add`.
  - **Untracked and ignored:** skip it and print a notice to stderr naming the path. Never force-add it.

  Ignore status comes from `git check-ignore -q`: exit 0 means ignored, 1 means not ignored, and anything else raises an error. *(Operator 2026-10-16: "Fix now (Recommended)", then "C: force only tracked (Recommended)".)*
- **R2:** the item's prose in Step 5 gains one sentence describing R1.
- **R3:** `worker/hooks/tests/test-skill-handoffs-restaging.sh` gains a scenario with a `.gitignore` that ignores `ignored/`:
  - **Ignored, tracked, listed:** a tracked file under `ignored/` is modified and listed. It gets staged.
  - **Ignored, never tracked, listed:** an untracked file under `ignored/` is listed. It is skipped, stays out of the index, and stderr names it.
  - **Ignored, tracked, unlisted:** a tracked, modified file under `ignored/` is not listed. It stays unstaged.
  - The script exits 0.

**Rejected alternative:** `git add -f` on every listed path. It would let a plan that lists a gitignored secret such as `.env` force it into the index, against the 2026-04-03 correction.

**Non-goals:** the rest of the script's validation, the guard, and the Step 5 placement.

## Testing Strategy

- The new hook-test scenario is written first and fails before the fix: the script exits 1 on the ignored tracked path.
- The existing hook-test cases still pass, unchanged.
- The commander test `test_execution_complete_restage_command_stages_from_non_root_cwd` still passes.
- Regression: `make test-hooks` and the commander suite.
