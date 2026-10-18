# Redeploy Local Main: Log (2026-10-02)

**Outcome: PASS.** Local `main` (`d3dae6a`) is deployed to both clients, and Commander and Codex have restarted. Nothing was pushed or committed.

## 1. Hooks

`make deploy-hooks` printed the expected lines:
- `Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks`
- `Deployed 30 hooks to /Users/roberthyatt/.claude/ironclaude-hooks`

## 2–3. Codex cache

- `codex plugin add ironclaude@ironclaude --json` installed to `installedPath=/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`.
- `diff -rq worker <cache> -x node_modules -x __pycache__ -x .DS_Store` printed nothing and exited 0.
- `rules/scope-fidelity.md` is present in the cache.

## 4–6. Commander

**Before the restart:**
- The daemon was PID 52042.
- The Brain was PID 91060, a child of 52042. The incident memory had 52052, so the Brain had been restarted since then.

**Stop:** `make stop` printed "Daemon (PID 52042) stopped". `ps -p 52042,91060` found neither process (exit 1).

**Relaunch** (detached, with the Claude Code environment variables stripped):
- `make` is PID 17126, with PPID 1 and PGID 17126, so it is detached.
- The daemon is PID 17509, which is also the value in `/tmp/ic-daemon.pid`.
- The Brain is PID 17515, a child of 17509.
- `grep -n -e Traceback -e ERROR /tmp/ironclaude-restart-launch.log` found nothing (exit 1).

## 7–8. Codex (ChatGPT.app)

**Before:** 45 PIDs matched `/Applications/ChatGPT.app` (list in the scratchpad file `chatgpt-pids-before.txt`). They included:
- main 11763;
- app-server 12615;
- a stale `codex exec-server` 15518;
- cua_node MCP servers.

**Quit:** the operator closed ChatGPT.app themselves before the planned `osascript` quit. A re-check found no surviving ChatGPT process; the only match was the grep itself. **No PID was killed.**

**Relaunch:** `open -a /Applications/ChatGPT.app` exited 0. The new main is PID 17755. The new app-server is PID 17961, a child of 17755.
