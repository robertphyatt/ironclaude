# Redeploy Local Main Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Redeploy local `main` (`d3dae6a`) to both clients, then restart Commander and Codex. No push.

**Requirements:** `docs/plans/2026-10-02-redeploy-local-main-design.md` (its `## Requirements` section; this is a light loop)

**Architecture:** One operational task that runs the existing deploy and restart commands in order. Each step checks its own result.

**Tech Stack:** make, the codex CLI, macOS `osascript` and `open`.

**Loop:** light. The task changes no code. It runs existing deploy and restart commands, touches no risk area, and writes one log file.

**Execution invariants:**
- Use absolute paths.
- Shell state does not persist between steps.
- Kill only exact, verified PIDs. Never pattern-kill.
- Use no foreground `sleep`. Re-check across steps instead.

No tests required: these are operational commands, and no code changes.

---

## Task 1: Redeploy and restart

**Files:**
- Create: `docs/plans/2026-10-02-redeploy-local-main-log.md`

**Step 1: Deploy the hooks**

Run:
```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```

Expected: the last two lines start with `Deployed to plugin cache` and `Deployed 30 hooks to /Users/roberthyatt/.claude/ironclaude-hooks`.

**Step 2: Refresh the Codex plugin cache**

Run:
```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: JSON with `installedPath` under `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/`.

**Step 3: Check that the Codex cache matches the repo**

Run:
```bash
diff -rq /Users/roberthyatt/Code/ironclaude/worker <installedPath from Step 2> -x node_modules -x __pycache__ -x .DS_Store
```

Expected: no output, exit 0. The new `rules/scope-fidelity.md` must be present in the cache. A missing file would print `Only in`.

**Step 4: Stop Commander**

Run:
```bash
make -C /Users/roberthyatt/Code/ironclaude/commander stop
```

Then confirm that PID 52042 has exited: `ps -p 52042` must list no process.

**Step 5: Relaunch Commander, detached and with the Claude Code environment variables stripped**

Run:
```bash
env -u CLAUDE_CODE_BRIDGE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_CODE_SESSION_ID -u CLAUDE_EFFORT -u CLAUDE_PID -u CLAUDECODE nohup python3 -c "import os; os.setsid(); os.execvp('make',['make','-C','/Users/roberthyatt/Code/ironclaude/commander','run'])" >/tmp/ironclaude-restart-launch.log 2>&1 & disown
```

**Step 6: Verify Commander**

Check all of the following:
- `/tmp/ic-daemon.pid` holds a PID other than 52042, and that process is running.
- A Brain child process of that daemon is running.
- `grep -n -e Traceback -e ERROR /tmp/ironclaude-restart-launch.log` finds nothing.

**Step 7: Quit Codex (ChatGPT.app) gracefully**

1. Record the PIDs: `pgrep -l -x ChatGPT`, plus its app-server descendants via `ps -o pid,ppid,command`.
2. Quit the app: `osascript -e 'tell application "ChatGPT" to quit'`.
3. Re-check the recorded PIDs. If any survive, kill only those exact PIDs and record each one.

**Step 8: Relaunch Codex and verify**

Run:
```bash
open -a /Applications/ChatGPT.app
```

Then check:
- A new `ChatGPT` main PID is running.
- A new app-server child is running.

**Step 9: Write the log**

Write `docs/plans/2026-10-02-redeploy-local-main-log.md` with:
- every command's result;
- the old and new PIDs;
- any PID killed after the graceful quit;
- the `diff -rq` result.

Then stage it:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-02-redeploy-local-main-log.md
```
