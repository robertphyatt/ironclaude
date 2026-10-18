# Lock-Free Read-Only Git Findings

## Probe

```
files=60000
flag_status_rewrote_index=no
env_status_rewrote_index=no
plain_status_rewrote_index=yes
env_diff_rewrote_index=yes env_diff_lines=0
flag_diff_rewrote_index=no flag_diff_lines=60000
kill_delay=0.02 lock=absent
kill_delay=0.05 lock=absent
kill_delay=0.1 lock=absent
kill_delay=0.2 lock=absent
kill_delay=0.4 lock=absent
```

## roleplaying-agents timing

```
rpa_rc=0
real 2.15
user 0.32
sys 3.91
25142 /private/tmp/claude-502/taf4/t1-rpa-status.out
```

## Diff verdict

diff_flag=SKIP (unsafe)

env_diff_rewrote_index=yes, flag_diff_rewrote_index=no, but flag_diff_lines=60000 differs from env_diff_lines=0: the flag stops the write but changes which files diff lists.

Note: no kill_delay line showed lock=present (the kill window was missed); recorded, not a STOP.

## Remediation notes

workspace-manager suite (Task 3): 553 passed, 1 skipped, 0 failed in both runs; vitest exited 1 on the known pre-existing `[vitest-worker]: Timeout calling "onTaskUpdate"` unhandled errors (5, then 3 on rerun), the same timeout recorded since v1.1.12; operator accepted 2026-10-18 ("Accept and record").

## Release commit message addition

IronClaude's read-only `git status` never takes the index lock. The drift hooks, which run on nearly every shell command, use `git --no-optional-locks status`, and workspace-manager and the Commander run their git with `GIT_OPTIONAL_LOCKS=0`, so a status read killed mid-run (a hook, a timeout, an interrupted turn) can no longer leave a stale `.git/index.lock` that blocks every session's `git add` in a shared checkout. Read-only `git diff` still refreshes the index under the lock, because turning that refresh off would change which files it reports. Writes still take the lock for one short command.
