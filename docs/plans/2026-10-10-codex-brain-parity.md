# Codex Brain Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** The Commander's Codex Brain runs the same Brain restriction hooks as the Claude Brain, worker hooks no longer apply to it, and the Commander proves those hooks are trusted, running and shipped from the repo before the Brain acts.

**Requirements:** `docs/plans/2026-10-10-codex-brain-parity-design.md` (section `## Requirements`, R1–R11)

**Architecture:**
- The Brain rule set lives in `commander/hooks/` plus `worker/hooks/brain-gated-actions.sh`.
- The Claude Brain registers it in `~/.ironclaude/brain/.claude/settings.json` through the Commander's template sync.
- The Codex Brain registers it in the trusted plugin `worker/hooks/hooks.json`, behind the Codex-Brain-only dispatcher `codex-brain-hooks.sh`.
- Worker hooks stand down in the Codex Brain inside `run_hook`.
- The Commander warms up Codex hook trust, checks hook provenance, and requires a UserPromptSubmit canary before the Codex Brain may run.

**Tech Stack:** Bash hooks with jq, bash test suites (`make test-hooks`), Python 3 and pytest (`commander/`), Codex CLI 0.162.

## Execution invariants

- **Shell state does not persist between steps.** Every path is an absolute literal.
  - REPO = `/Users/roberthyatt/Code/ironclaude`
  - PROBE = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe`. Only Task 14 uses it, after the restart, and only through `mkdir`, `git init`, `codex exec` and `grep`. No Write-tool files go there.
- **Where things run.** pytest runs as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`. git runs as `git -C /Users/roberthyatt/Code/ironclaude …`.
- **zsh has `nomatch`.** Quote globs. Prefer `find` to shell globs in evidence commands.
- **Foreground `sleep` is blocked.** Split waits into separate steps.
- **`docs/` is gitignored.** Stage plan and findings artifacts with `git add -f`.
- **Evidence commands.** No `2>/dev/null` on evidence commands, and no `head` truncation on absence checks.
- **Expected values.** An expected count or name that a probe discovers is recorded from the actual run. It is never assumed.
- **Brain identity.** The Codex Brain is `IC_ROLE=brain` plus `IRONCLAUDE_CLIENT=codex`. A Codex worker never has `IC_ROLE=brain`; the Commander sets `IC_ROLE=worker` for workers.
- **Hook stdin.** A hook may exit before reading stdin. Existing hooks already do this, for example `brain-orchestrator-guard.sh:7-9`.
- **Never pattern-kill processes.** Kill only by exact tmux session name or exact PID.
- **Commits.** Never commit or push. Staging only.

## Verified source facts

**Hook infrastructure (`worker/hooks/`)**
- `hook-logger.sh:666-674`: `run_hook HOOK` sets `set -euo pipefail` and an ERR trap.
- Every hook registered in `hooks.json` calls `run_hook` except `get-back-to-work-claude.sh`, which has 0 calls. Verified with `grep -c "run_hook "`.
- `get-back-to-work-claude.sh:16-19` returns early under `GBTW_TEST_MODE`. `:22-28` detects Codex from the plugin root.
- `codex-brain-gated-actions.sh` is the stateful Brain gate.
  - Fallback (`:10`) and main path (`:63`) check identity with `IC_ROLE=brain && IRONCLAUDE_CLIENT=codex`.
  - Its memory arm glob `mcp__episodic?memory__*` already matches both `-` and `_`.
  - Gated set: `:100-118`. Fallback gated set: `:15-45`.
- `hooks.json` group 0 of PreToolUse runs that gate with the long matcher at `:41`.
- Event group counts today: PreToolUse 7, PostToolUse 7, UserPromptSubmit 2, SessionStart 3, SubagentStop 1, Stop 1.
- `test-codex-brain-gated-actions.sh`:
  - `:74-75` asserts that the Claude client is inactive.
  - `:297` selects the group-0 matcher by `contains("codex-brain-gated-actions.sh")`.
  - `:333-354` holds the no-logger cases.
- `bash-readonly-guard.sh:98-104`: `is_readonly_research_bash CMD` is the worker read-only predicate. It admits cat/head/tail/wc/grep/rg/find/ls/diff/cd, rejects metacharacters, and rejects find write actions.

**Brain hook scripts**
- `commander/hooks/brain-orchestrator-guard.sh`: blocks `Edit|Write|NotebookEdit|MultiEdit` (`:15-20`). Bash (`.command`) is limited to a git allowlist (`:23-85`). It does nothing unless `IC_ROLE=brain` (`:7-9`).
- `commander/hooks/brain-task-gate.sh`: gates only `Agent`/`Task` (`:41`) and allows `ironclaude:search-conversations`. Its fallback is at `:19-32`.
- `commander/hooks/tests/test-brain-task-gate.sh` is the bash test pattern used by every new commander hook suite. It uses a temp HOME and copies the hook and `hook-logger.sh`.
- Local-only scripts. Contents were read in full:
  - `~/.ironclaude/brain/hooks/block-push.sh`: Bash only, `.tool_input.command`.
  - `~/.ironclaude/brain/hooks/block-pin-enforcer.sh`
  - `~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh`: flag `/tmp/ic/sweep-armed-$SESSION_TAG`, TTL 3 min.
  - `~/.claude/ironclaude-hooks/attention-sweep-arm.sh`

**Commander (`commander/src/ironclaude/main.py`)**
- `_sync_brain_settings_hooks` is at `:5700-5768`. It appends PreToolUse entries only.
- Call order in `main()`:
  - the sync is called at `:5863-5868`;
  - `_deploy_worker_hooks(repo_root)` at `:5887`;
  - `SlackBot` is built at `:5899`;
  - `TmuxManager` at `:5931`;
  - the Brain is constructed at `:5934-5940` and started at `:5945-5949`.
- `repo_root` is `commander/`.
- Template: `commander/src/brain/brain_settings_hooks.json`. Its tests are in `commander/tests/test_daemon.py`:
  - `:2290-2387`: append, corrupt, template and missing-file cases;
  - `:5695-5722`: the template-lists-three-gates test.

**Codex Brain client (`commander/src/ironclaude/codex_brain_client.py`)**
- `_BRAIN_GATE_ROOT = Path("/tmp/ic/codex-brain-gate")` is at `:53`.
- `_spawn_env` (`:902-913`) sets `IC_ROLE`, `IRONCLAUDE_CLIENT=codex` and `IRONCLAUDE_BRAIN_GATE_SESSION`.
- `start()` (`:1048-1162`) sets `_running=True`, then calls `send_message(system_prompt)` (`:1158-1162`).
- `_fail_start` (`:342-347`) queues `[CODEX BRAIN ERROR] …` and shuts down.
- The test pattern for `start()` is `test_codex_brain_client.py:1068-1124`.

**Claude Brain client (`commander/src/ironclaude/brain_client.py`)**
- `start()` sets `os.environ["IC_ROLE"] = "brain"` (`:327`). The client has no gate-session env.
- `__init__` is at `:230-299`.

**Orchestrator and tmux**
- `OrchestratorTools(registry, tmux, db_conn=…, config=…, ssh_manager=…)` is built the same way in `main.py:1676`.
- `_wait_for_ready(session, timeout, client="codex")` (`orchestrator_mcp.py:2322-2393`) answers the folder-trust prompt and "Trust all and continue".
- `_codex_worker_command` (`:564-571`) reads the Codex path from `config.providers.clients.codex.path`, defaulting to `codex`.
- `TmuxManager.spawn_session/has_session/kill_session` are at `tmux_manager.py:306,326,352`.

**Codex hook behaviour**
- Trust is stored per entry under the key `ironclaude@ironclaude:hooks/hooks.json:<event>:<group>:<handler>`.
- A prior probe (`docs/plans/2026-10-02-codex-hook-probe-findings.md:100-117`) captured a `Bash` payload with `command`. The existing guard code handles `exec_command` with `cmd` (`professional-mode-guard.sh:214-221`).
- `codex app-server --help` (0.162.0-alpha.17.2) does not list `--dangerously-bypass-hook-trust`. Plain `codex` and `codex exec` do.
- UserPromptSubmit and PreToolUse both fire in the Brain's app-server. Task 1 re-verifies the evidence.

---

## Task 1: Record the hook evidence the design relies on

**Files:**
- Create: `docs/plans/2026-10-10-codex-brain-parity-probe-findings.md`

No tests are required: this task re-verifies existing evidence with read-only commands and writes it to a findings doc. It runs no live probe. The operator decided this on 2026-10-11, on Opus's recommendation: `codex app-server` (0.162.0-alpha.17.2) does not list `--dangerously-bypass-hook-trust`, and the guard blocks scratch probe files that aren't in the plan. Instead, the Brain hooks match every known tool shape (Tasks 3, 5, 6), and Task 14's live probe is the proof against real payloads.

**Step 1.** Confirm that only `state-activator.sh`, a UserPromptSubmit hook, writes the state cache.
```bash
grep -n -e "state-cache" /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/session-init.sh
```
Expected (measured 2026-10-11):
- `state-activator.sh:217:STATE_CACHE="$HOME/.claude/ironclaude-state-cache-${SESSION_TAG}.json"`
- `session-init.sh:284`, a `find … -delete` line.

**Step 2.** Read the Brain's state-cache timestamp.
```bash
grep -o -e "\"timestamp\": *\"[^\"]*\"" /Users/roberthyatt/.claude/ironclaude-state-cache-01a127b6-a9dc-78f1-9daf-b204d6bd771c.json
```
Expected (measured): `"timestamp": "2026-10-10T22:09:07Z"`. If the file has since been rotated away, record "rotated", cite this plan's measured value, and continue.

**Step 3.** Find the Brain's turn start in the same second.
```bash
grep -o -e "2026-10-10T22:09:0[0-9.]*Z" /Users/roberthyatt/.codex/sessions/2026/10/10/rollout-2026-10-10T15-27-24-01a127b6-a9dc-78f1-9daf-b204d6bd771c.jsonl
```
Expected (measured): the first line is `2026-10-10T22:09:07.629Z`.

**Step 4.** Confirm that PreToolUse plugin hooks run in the Brain's app-server.
```bash
grep -c "Command blocked by PreToolUse hook" /Users/roberthyatt/.codex/sessions/2026/10/10/rollout-2026-10-10T15-27-24-01a127b6-a9dc-78f1-9daf-b204d6bd771c.jsonl
```
Expected (measured 2026-10-11): `1`.

**Step 5.** Write `docs/plans/2026-10-10-codex-brain-parity-probe-findings.md` with the Step 1–4 outputs verbatim and these conclusions:
- UserPromptSubmit fires inside the Codex Brain's app-server for a trusted entry.
- PreToolUse fires there too.
- Not proven: turn 1, and new entries before they are trusted. Task 12's fail-closed canary covers both.
- No payload probe was run. The hooks match every known shape, the `spawn_agent` payload remains uncaptured, and Task 14 is the live proof.

**Step 6.** Stage the findings.
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-10-codex-brain-parity-probe-findings.md
```
Expected: no output.

---

## Task 2: Adopt the four local-only Brain scripts (R2)

**Files:**
- Create: `commander/hooks/block-push.sh`
- Create: `commander/hooks/block-pin-enforcer.sh`
- Create: `commander/hooks/attention-sweep-enforcer.sh`
- Create: `commander/hooks/attention-sweep-arm.sh`

No tests are required in this task. It is a byte-identical adoption. Tasks 3 and 4 add the tests.

**Step 1.** Copy the scripts.
```bash
cp -p /Users/roberthyatt/.ironclaude/brain/hooks/block-push.sh /Users/roberthyatt/.ironclaude/brain/hooks/block-pin-enforcer.sh /Users/roberthyatt/.claude/ironclaude-hooks/attention-sweep-enforcer.sh /Users/roberthyatt/.claude/ironclaude-hooks/attention-sweep-arm.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/
```
Expected: no output.

**Step 2.** Verify each copy is byte-identical.
```bash
cmp /Users/roberthyatt/.ironclaude/brain/hooks/block-push.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/block-push.sh && cmp /Users/roberthyatt/.ironclaude/brain/hooks/block-pin-enforcer.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/block-pin-enforcer.sh && cmp /Users/roberthyatt/.claude/ironclaude-hooks/attention-sweep-enforcer.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/attention-sweep-enforcer.sh && cmp /Users/roberthyatt/.claude/ironclaude-hooks/attention-sweep-arm.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/attention-sweep-arm.sh && echo identical
```
Expected: `identical`.

**Step 3.** Stage the scripts.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/block-push.sh commander/hooks/block-pin-enforcer.sh commander/hooks/attention-sweep-enforcer.sh commander/hooks/attention-sweep-arm.sh
```
Expected: no output.

---

## Task 3: block-push accepts Codex shell payloads (R2)

**Files:**
- Modify: `commander/hooks/block-push.sh`
- Test (create): `commander/hooks/tests/test-block-push.sh`

**Step 1 (RED).** Create `commander/hooks/tests/test-block-push.sh`:
```bash
#!/bin/bash
# block-push.sh blocks git push from the Brain for Claude (Bash/.command) and Codex
# (Bash/.command, exec_command/.cmd as string or argv array) shell payloads.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # -> commander/
HOOK="$ROOT_DIR/hooks/block-push.sh"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
feed() {  # feed TOOL_NAME TOOL_INPUT_JSON -> exit code
  jq -cn --arg t "$1" --argjson i "$2" '{session_id:"bp-test",tool_name:$t,tool_input:$i}' \
    | bash "$HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "Claude Bash git push blocked"            "2" "$(feed Bash '{"command":"git push origin main"}')"
assert_eq "Bash leading-space git push blocked"     "2" "$(feed Bash '{"command":"  git push"}')"
assert_eq "Bash git -C path push blocked"           "2" "$(feed Bash '{"command":"git -C /tmp/r push origin main"}')"
assert_eq "Codex exec_command string push blocked"  "2" "$(feed exec_command '{"cmd":"git push origin main"}')"
assert_eq "Codex exec_command argv push blocked"    "2" "$(feed exec_command '{"cmd":["git","push","origin","main"]}')"
assert_eq "Bash git status allowed"                 "0" "$(feed Bash '{"command":"git status"}')"
assert_eq "exec_command git status allowed"         "0" "$(feed exec_command '{"cmd":"git status"}')"
assert_eq "git push-like word not blocked"          "0" "$(feed Bash '{"command":"git pushd-notes"}')"
assert_eq "non-shell tool ignored"                  "0" "$(feed Read '{"file_path":"/tmp/x"}')"
assert_eq "near-miss tool name ignored"             "0" "$(feed exec_commandx '{"cmd":"git push origin main"}')"
assert_eq "exec_command without cmd allowed"        "0" "$(feed exec_command '{}')"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 2.** Run RED.
```bash
bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-block-push.sh
```
Expected: exits 1, with exactly three failures: `Bash git -C path push blocked`, `Codex exec_command string push blocked`, `Codex exec_command argv push blocked`.

**Step 3 (GREEN).** Replace `commander/hooks/block-push.sh` with:
```bash
#!/bin/bash
# block-push.sh — PreToolUse hook: blocks git push in Brain session.
# Claude sends Bash with tool_input.command; Codex sends Bash (.command) or
# exec_command (.cmd, a string or an argv array).

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

case "$TOOL_NAME" in
  Bash|exec_command) ;;
  *) exit 0 ;;
esac

COMMAND=$(echo "$INPUT" | jq -r '(.tool_input.command // .tool_input.cmd // "") | if type == "array" then join(" ") else . end' 2>/dev/null || true)

if echo "$COMMAND" | grep -qE '^\s*git\s+(-C\s+\S+\s+)?push\b'; then
  echo "git push is blocked for Brain session. Commits are staged — Robert will push." >&2
  exit 2
fi

exit 0
```

**Step 4.** Run GREEN (same command as Step 2). Expected: `11 passed, 0 failed`.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/block-push.sh commander/hooks/tests/test-block-push.sh
```

---

## Task 4: Characterization tests for the adopted, unchanged scripts (R2)

**Files:**
- Test (create): `commander/hooks/tests/test-attention-sweep.sh`
- Test (create): `commander/hooks/tests/test-block-pin-enforcer.sh`

The scripts are unchanged, so there is no RED step: these tests characterize existing behaviour. Each assertion is falsifiable. For example, the "spawn without sweep blocked" check fails if the enforcer stops gating, and the "single-worker query does not arm" check fails if the arm ignores `worker_id`.

**Step 1.** Create `commander/hooks/tests/test-attention-sweep.sh`:
```bash
#!/bin/bash
# attention-sweep-enforcer.sh + attention-sweep-arm.sh: gated worker actions need a
# full-fleet get_worker_status within 3 minutes (flag /tmp/ic/sweep-armed-<session>).
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
TEST_ROOT=$(mktemp -d)
SCRIPT_DIR="$TEST_ROOT/hooks"
mkdir -p "$SCRIPT_DIR"
cp "$ROOT_DIR/hooks/attention-sweep-enforcer.sh" "$ROOT_DIR/hooks/attention-sweep-arm.sh" "$REPO_ROOT/worker/hooks/hook-logger.sh" "$SCRIPT_DIR/"
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude"
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
SESSION="ic-sweep-test-$$-$(date +%s)"
FLAG="/tmp/ic/sweep-armed-$SESSION"
trap 'rm -rf "$TEST_ROOT" "$FLAG"' EXIT
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
run() {  # run SCRIPT TOOL TOOL_INPUT_JSON -> exit code
  jq -cn --arg s "$SESSION" --arg t "$2" --argjson i "$3" '{session_id:$s,tool_name:$t,tool_input:$i}' \
    | bash "$SCRIPT_DIR/$1" >/dev/null 2>&1
  printf '%s' "$?"
}
rm -f "$FLAG"
assert_eq "spawn without sweep blocked" "2" "$(run attention-sweep-enforcer.sh mcp__orchestrator__spawn_worker '{}')"
assert_eq "get_* query passes" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__get_task_ledger '{}')"
assert_eq "ungated tool passes" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__wiki_query '{}')"
run attention-sweep-arm.sh mcp__orchestrator__get_worker_status '{"worker_id":"w1"}' >/dev/null
assert_eq "single-worker query does not arm" "2" "$(run attention-sweep-enforcer.sh mcp__orchestrator__send_to_worker '{}')"
run attention-sweep-arm.sh mcp__orchestrator__get_worker_status '{}' >/dev/null
assert_eq "full sweep arms send_to_worker" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__send_to_worker '{}')"
assert_eq "flag retained within TTL" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__kill_worker '{}')"
touch -t 200001010000 "$FLAG"
assert_eq "expired sweep blocked" "2" "$(run attention-sweep-enforcer.sh mcp__orchestrator__spawn_worker '{}')"
if [ -e "$FLAG" ]; then fail "expired flag removed"; else pass "expired flag removed"; fi
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 2.** Create `commander/hooks/tests/test-block-pin-enforcer.sh`:
```bash
#!/bin/bash
# block-pin-enforcer.sh: PostToolUse reminder when update_ledger records blocked tasks.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/block-pin-enforcer.sh"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
out() { jq -cn --arg t "$1" --argjson i "$2" '{session_id:"bpe",tool_name:$t,tool_input:$i}' | bash "$HOOK"; }
OUT=$(out mcp__orchestrator__update_ledger '{"tasks":[{"id":"t7","status":"blocked"},{"id":"t8","status":"in_progress"}]}')
if printf '%s' "$OUT" | jq -e '.systemMessage | contains("BLOCKED TASK PIN REQUIRED") and contains("t7") and (contains("t8") | not)' >/dev/null; then pass "blocked task reminder names only t7"; else fail "blocked task reminder" "$OUT"; fi
OUT=$(out mcp__orchestrator__update_ledger '{"tasks":[{"id":"t8","status":"in_progress"}]}')
if [ -z "$OUT" ]; then pass "no blocked tasks -> no output"; else fail "no blocked tasks" "$OUT"; fi
OUT=$(out mcp__orchestrator__get_task_ledger '{"tasks":[{"id":"t7","status":"blocked"}]}')
if [ -z "$OUT" ]; then pass "other tool ignored"; else fail "other tool ignored" "$OUT"; fi
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 3.** Run both suites.
```bash
bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-attention-sweep.sh && bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-block-pin-enforcer.sh
```
Expected: `8 passed, 0 failed` then `3 passed, 0 failed`.

**Step 4.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/tests/test-attention-sweep.sh commander/hooks/tests/test-block-pin-enforcer.sh
```

---

## Task 5: brain-orchestrator-guard covers Codex payloads (R3)

**Files:**
- Modify: `commander/hooks/brain-orchestrator-guard.sh`
- Test (create): `commander/hooks/tests/test-brain-orchestrator-guard.sh`

Codex has no Read, Grep or Glob tool, so for `IRONCLAUDE_CLIENT=codex` the shell also admits the read-only commands the Claude Brain reaches through those tools. It uses the shared `is_readonly_research_bash` from `bash-readonly-guard.sh`, which is deployed beside this script in `~/.claude/ironclaude-hooks`. If that file is missing, the guard falls back to git-only.

**Step 1 (RED).** Create `commander/hooks/tests/test-brain-orchestrator-guard.sh`:
```bash
#!/bin/bash
# brain-orchestrator-guard.sh on Claude (Edit/Write/Bash .command) and Codex
# (apply_patch, Bash .command, exec_command .cmd) Brain payloads.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/with" "$TEST_ROOT/without"
cp "$ROOT_DIR/hooks/brain-orchestrator-guard.sh" "$REPO_ROOT/worker/hooks/bash-readonly-guard.sh" "$TEST_ROOT/with/"
cp "$ROOT_DIR/hooks/brain-orchestrator-guard.sh" "$TEST_ROOT/without/"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
feed() {  # feed DIR ROLE CLIENT TOOL TOOL_INPUT_JSON -> exit code
  jq -cn --arg t "$4" --argjson i "$5" '{session_id:"bog",tool_name:$t,tool_input:$i}' \
    | IC_ROLE="$2" IRONCLAUDE_CLIENT="$3" bash "$TEST_ROOT/$1/brain-orchestrator-guard.sh" >/dev/null 2>&1
  printf '%s' "$?"
}
# Claude Brain (behaviour unchanged)
assert_eq "claude Edit blocked"            "2" "$(feed with brain "" Edit '{"file_path":"/tmp/x"}')"
assert_eq "claude Bash git status allowed" "0" "$(feed with brain "" Bash '{"command":"git status"}')"
assert_eq "claude Bash git push blocked"   "2" "$(feed with brain "" Bash '{"command":"git push"}')"
assert_eq "claude Bash cat blocked"        "2" "$(feed with brain "" Bash '{"command":"cat /etc/hosts"}')"
# Codex Brain
assert_eq "codex apply_patch blocked"          "2" "$(feed with brain codex apply_patch '{"command":"*** Begin Patch"}')"
assert_eq "codex exec git status allowed"      "0" "$(feed with brain codex exec_command '{"cmd":"git status"}')"
assert_eq "codex exec git push blocked"        "2" "$(feed with brain codex exec_command '{"cmd":"git push origin main"}')"
assert_eq "codex exec argv git push blocked"   "2" "$(feed with brain codex exec_command '{"cmd":["git","push"]}')"
assert_eq "codex exec rg allowed"              "0" "$(feed with brain codex exec_command '{"cmd":"rg -n parity /tmp"}')"
assert_eq "codex Bash cat allowed"             "0" "$(feed with brain codex Bash '{"command":"cat /etc/hosts"}')"
assert_eq "codex exec rm blocked"              "2" "$(feed with brain codex exec_command '{"cmd":"rm -rf /tmp/x"}')"
assert_eq "codex exec find -delete blocked"    "2" "$(feed with brain codex exec_command '{"cmd":"find /tmp -delete"}')"
assert_eq "codex exec piped cat blocked"       "2" "$(feed with brain codex exec_command '{"cmd":"cat a | sh"}')"
assert_eq "codex cat without predicate file blocked" "2" "$(feed without brain codex exec_command '{"cmd":"cat /etc/hosts"}')"
assert_eq "codex exec without cmd blocked (fail closed)" "2" "$(feed with brain codex exec_command '{}')"
assert_eq "codex near-miss tool name passes"   "0" "$(feed with brain codex apply_patchx '{"command":"*** Begin Patch"}')"
# Non-Brain sessions pass through
assert_eq "worker apply_patch passes"          "0" "$(feed with worker codex apply_patch '{"command":"*** Begin Patch"}')"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 2.** Run RED.
```bash
bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh
```
Expected: exits 1. The failures are the Codex cases the current guard does not recognise. Record the exact FAIL lines. At minimum they include `codex apply_patch blocked`, `codex exec git push blocked`, `codex exec rm blocked` and `codex Bash cat allowed`.

**Step 3 (GREEN).** Replace `commander/hooks/brain-orchestrator-guard.sh:1-24` (header through the `CMD=` line) with:
```bash
#!/bin/bash
# brain-orchestrator-guard.sh — PreToolUse hook for IronClaude Brain session only.
# Hard-blocks mutation tools (Claude Edit/Write/NotebookEdit/MultiEdit, Codex
# apply_patch) and non-allowlisted shell commands (Claude Bash; Codex Bash or
# exec_command). Workers (IC_ROLE=worker) and direct sessions pass through.
# Codex has no Read/Grep/Glob tools, so for IRONCLAUDE_CLIENT=codex the shell also
# admits the read-only research commands Claude reaches through those tools.

# Only apply restrictions to the Brain session.
if [ "$IC_ROLE" != "brain" ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Block mutation tools unconditionally
case "$TOOL_NAME" in
  Edit|Write|NotebookEdit|MultiEdit|apply_patch)
    echo "BLOCKED — Brain cannot use mutation tools. Route through workers." >&2
    exit 2
    ;;
esac

# Block non-allowlisted shell commands
if [ "$TOOL_NAME" = "Bash" ] || [ "$TOOL_NAME" = "exec_command" ]; then
  CMD=$(echo "$INPUT" | jq -r '(.tool_input.command // .tool_input.cmd // "") | if type == "array" then join(" ") else . end' 2>/dev/null || true)
```
Then, directly after the existing metachar/newline block (the `fi` that closes "Shell chaining/redirection is not allowed"), insert:
```bash

  # Codex read parity: admit read-only research commands (fail closed to git-only
  # when the shared predicate file is missing).
  if [ "${IRONCLAUDE_CLIENT:-}" = "codex" ] && [ -f "$SCRIPT_DIR/bash-readonly-guard.sh" ]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/bash-readonly-guard.sh"
    if is_readonly_research_bash "$CMD"; then
      exit 0
    fi
  fi
```
The rest of the file is unchanged.

**Step 4.** Run GREEN (same command as Step 2). Expected: `17 passed, 0 failed`.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/brain-orchestrator-guard.sh commander/hooks/tests/test-brain-orchestrator-guard.sh
```

---

## Task 6: brain-task-gate blocks Codex spawn_agent (R3)

**Files:**
- Modify: `commander/hooks/brain-task-gate.sh`
- Modify: `commander/hooks/tests/test-brain-task-gate.sh`

**Step 1 (RED).** In `test-brain-task-gate.sh`, insert before the line `# a non-subagent tool is not gated at all`:
```bash
# Codex: the Brain has no memory-search agent type, so every spawn_agent is blocked.
assert_eq "spawn_agent blocked"                   "2" "$(feed spawn_agent OMIT)"
assert_eq "spawn_agent search-conversations blocked" "2" "$(feed spawn_agent ironclaude:search-conversations)"
assert_eq "near-miss spawn_agents not gated"       "0" "$(feed spawn_agents OMIT)"
```
Then insert before `printf '\n%d passed, %d failed\n'`:
```bash
assert_eq "no-logger: spawn_agent blocked" "2" "$(nofeed spawn_agent general-purpose)"
```

**Step 2.** Run RED.
```bash
bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-task-gate.sh
```
Expected: exits 1 with three failures: `spawn_agent blocked`, `spawn_agent search-conversations blocked`, `no-logger: spawn_agent blocked`.

**Step 3 (GREEN).** In `commander/hooks/brain-task-gate.sh`:

(a) Header comment line 2: change `(registered with matcher "Agent|Task")` to `(registered with matcher "Agent|Task" on Claude, "spawn_agent" on Codex)`.

(b) In the fallback block, replace `if [[ "$_TN" == "Agent" || "$_TN" == "Task" ]]; then` with:
```bash
  if [[ "$_TN" == "spawn_agent" ]]; then
    echo "✗ [brain-task-gate]: Blocked - IronClaude hook-logger.sh missing; enforcement cannot run. Run 'make deploy-hooks', then retry." >&2
    exit 2
  fi
  if [[ "$_TN" == "Agent" || "$_TN" == "Task" ]]; then
```

(c) Replace the block `# Only gate subagent dispatches; …` through `fi` (`:40-43`) with:
```bash
# Codex's native subagent tool: the Codex Brain has no memory-search agent type
# (it searches episodic memory through MCP directly), so every spawn is blocked.
if [[ "$TOOL_NAME" == "spawn_agent" ]]; then
  block_pretooluse "brain-task-gate" "BLOCKED — the Brain must not spawn subagents.

Act directly: call the orchestrator/MCP tools yourself, and search episodic memory with the episodic-memory MCP tools."
fi

# Only gate subagent dispatches; everything else passes untouched.
if [[ "$TOOL_NAME" != "Agent" && "$TOOL_NAME" != "Task" ]]; then
  exit 0
fi
```

**Step 4.** Run GREEN (same command as Step 2). Expected: `13 passed, 0 failed`.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/brain-task-gate.sh commander/hooks/tests/test-brain-task-gate.sh
```

---

## Task 7: Codex Brain dispatcher and worker-hook stand-down (R5, R6)

**Files:**
- Create: `worker/hooks/codex-brain-hooks.sh`
- Modify: `worker/hooks/hook-logger.sh:666-674` (`run_hook`)
- Modify: `worker/hooks/get-back-to-work-claude.sh` (after `:19`)
- Modify: `worker/hooks/codex-brain-gated-actions.sh` (add `IC_BRAIN_HOOK=1` declaration)
- Test (create): `worker/hooks/tests/test-codex-brain-stand-down.sh`

**Step 1 (RED).** Create `worker/hooks/tests/test-codex-brain-stand-down.sh`:
```bash
#!/bin/bash
# Worker hooks stand down in the Commander's Codex Brain (IC_ROLE=brain +
# IRONCLAUDE_CLIENT=codex); Brain hooks run through codex-brain-hooks.sh.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> worker/hooks
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude" "$TEST_ROOT/brainhooks"
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
PAYLOAD='{"session_id":"sd-test","tool_name":"exec_command","tool_input":{"cmd":"rg -n x /tmp"}}'

# Probe Brain hook: records env + stdin, exits 3.
cat > "$TEST_ROOT/brainhooks/probe-hook.sh" <<EOF
#!/bin/bash
printf 'IC_BRAIN_HOOK=%s\n' "\${IC_BRAIN_HOOK:-}" > "$TEST_ROOT/probe.out"
cat >> "$TEST_ROOT/probe.out"
exit 3
EOF
dispatch() {  # dispatch ROLE CLIENT ARG -> exit code
  rm -f "$TEST_ROOT/probe.out"
  printf '%s' "$PAYLOAD" | IC_ROLE="$1" IRONCLAUDE_CLIENT="$2" IC_BRAIN_HOOK_DIR="$TEST_ROOT/brainhooks" \
    bash "$DIR/codex-brain-hooks.sh" "$3" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "dispatcher runs target for Codex Brain" "3" "$(dispatch brain codex probe-hook.sh)"
if grep -qx 'IC_BRAIN_HOOK=1' "$TEST_ROOT/probe.out" && grep -q '"sd-test"' "$TEST_ROOT/probe.out"; then pass "target sees IC_BRAIN_HOOK=1 and payload"; else fail "target env/payload"; fi
assert_eq "dispatcher no-op for Claude Brain" "0" "$(dispatch brain claude probe-hook.sh)"
if [ -e "$TEST_ROOT/probe.out" ]; then fail "Claude Brain must not run target"; else pass "Claude Brain did not run target"; fi
assert_eq "dispatcher no-op for Codex worker" "0" "$(dispatch worker codex probe-hook.sh)"
if [ -e "$TEST_ROOT/probe.out" ]; then fail "Codex worker must not run target"; else pass "Codex worker did not run target"; fi
assert_eq "dispatcher blocks missing target" "2" "$(dispatch brain codex absent-hook.sh)"
assert_eq "dispatcher blocks path traversal" "2" "$(dispatch brain codex ../probe-hook.sh)"

# run_hook stand-down
cat > "$TEST_ROOT/uses-run-hook.sh" <<EOF
#!/bin/bash
source "$DIR/hook-logger.sh"
run_hook "uses-run-hook"
exit 7
EOF
rh() { IC_ROLE="$1" IRONCLAUDE_CLIENT="$2" IC_BRAIN_HOOK="$3" bash "$TEST_ROOT/uses-run-hook.sh" </dev/null >/dev/null 2>&1; printf '%s' "$?"; }
assert_eq "run_hook stands down in Codex Brain" "0" "$(rh brain codex "")"
assert_eq "run_hook runs Brain hooks in Codex Brain" "7" "$(rh brain codex 1)"
assert_eq "run_hook runs in Codex worker" "7" "$(rh worker codex "")"
assert_eq "run_hook runs in Claude Brain" "7" "$(rh brain claude "")"

# Real worker guard: blocks in a Codex worker session (no DB here), stands down in the Codex Brain.
pmg() { printf '%s' "$PAYLOAD" | IC_ROLE="$1" IRONCLAUDE_CLIENT=codex bash "$DIR/professional-mode-guard.sh" >/dev/null 2>&1; printf '%s' "$?"; }
assert_eq "professional-mode-guard stands down in Codex Brain" "0" "$(pmg brain)"
if [ "$(pmg worker)" != "0" ]; then pass "professional-mode-guard still enforces for Codex worker"; else fail "professional-mode-guard enforces for Codex worker" "exit 0"; fi

# Stop hook (does not use run_hook)
OUT=$(printf '{"session_id":"sd-test"}' | IC_ROLE=brain IRONCLAUDE_CLIENT=codex bash "$DIR/get-back-to-work-claude.sh"); RC=$?
assert_eq "GBTW stands down in Codex Brain (rc)" "0" "$RC"
assert_eq "GBTW stands down in Codex Brain (no output)" "" "$OUT"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 2.** Run RED.
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-codex-brain-stand-down.sh
```
Expected: exits 1. The failures include `dispatcher runs target for Codex Brain`, `run_hook stands down in Codex Brain` and `professional-mode-guard stands down in Codex Brain`. Record every FAIL line. Also record the value `pmg worker` produces, which must be non-zero: the negative control is valid only if the guard blocks a Codex worker in this fixture.

**Step 3 (GREEN).** Make four changes.

(a) Create `worker/hooks/codex-brain-hooks.sh`:
```bash
#!/bin/bash
# codex-brain-hooks.sh <script.sh> — runs one Brain restriction hook for the
# Commander's Codex Brain only (IC_ROLE=brain + IRONCLAUDE_CLIENT=codex).
# The Codex Brain loads this plugin's trusted hooks.json, so its Brain hooks are
# registered there behind this dispatcher; every other session exits 0 here
# without reading the payload. The Claude Brain runs the same scripts from its
# project settings.json (commander/src/brain/brain_settings_hooks.json).
if [ "${IC_ROLE:-}" != "brain" ] || [ "${IRONCLAUDE_CLIENT:-}" != "codex" ]; then
  exit 0
fi

TARGET="${1:-}"
HOOK_DIR="${IC_BRAIN_HOOK_DIR:-$HOME/.claude/ironclaude-hooks}"
if [[ ! "$TARGET" =~ ^[A-Za-z0-9][A-Za-z0-9-]*\.sh$ ]] || [ ! -f "$HOOK_DIR/$TARGET" ]; then
  echo "✗ [codex-brain-hooks]: Blocked - Brain hook '${TARGET}' is missing from ${HOOK_DIR}; enforcement cannot run. Restart the Commander to redeploy Brain hooks." >&2
  exit 2
fi

# Brain hooks call run_hook; this marks them as Brain hooks so the Codex-Brain
# worker stand-down in hook-logger.sh does not skip them.
export IC_BRAIN_HOOK=1
exec bash "$HOOK_DIR/$TARGET"
```

(b) In `worker/hooks/hook-logger.sh`, change `run_hook` to:
```bash
run_hook() {
  local hook="$1"

  # Worker hooks do not apply to the Commander's Codex Brain, which runs the
  # Brain restriction set instead (codex-brain-hooks.sh sets IC_BRAIN_HOOK=1).
  if [ "${IC_ROLE:-}" = "brain" ] && [ "${IRONCLAUDE_CLIENT:-}" = "codex" ] && [ "${IC_BRAIN_HOOK:-}" != "1" ]; then
    exit 0
  fi

  # Enable strict mode
  set -euo pipefail

  # Set up error trap with stack trace
  trap '_hook_error_handler "'"$hook"'" "$LINENO" "$BASH_COMMAND" "${FUNCNAME[*]:-main}" "${BASH_LINENO[*]:-0}"' ERR
}
```
Also update the comment above it (`:662-665`), adding the line `# Exits 0 for worker hooks in the Commander's Codex Brain (see body).`.

(c) In `worker/hooks/get-back-to-work-claude.sh`, insert after the `GBTW_TEST_MODE` block (after `:19`):
```bash

# Worker Stop enforcement does not apply to the Commander's Codex Brain.
if [ "${IC_ROLE:-}" = "brain" ] && [ "${IRONCLAUDE_CLIENT:-}" = "codex" ]; then
  exit 0
fi
```

(d) In `worker/hooks/codex-brain-gated-actions.sh`, insert after line 2 (the header comment):
```bash
# A Brain hook: exempt from the Codex-Brain worker stand-down in run_hook.
export IC_BRAIN_HOOK=1
```

**Step 4.** Run GREEN and the gated-actions suite.
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-codex-brain-stand-down.sh && bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-codex-brain-gated-actions.sh
```
Expected: `16 passed, 0 failed`, then `ALL CODEX BRAIN GATE TESTS PASSED`.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/codex-brain-hooks.sh worker/hooks/hook-logger.sh worker/hooks/get-back-to-work-claude.sh worker/hooks/codex-brain-gated-actions.sh worker/hooks/tests/test-codex-brain-stand-down.sh
```

---

## Task 8: brain-gated-actions enforces for both Brains (R1)

**Files:**
- Rename: `worker/hooks/codex-brain-gated-actions.sh` → `worker/hooks/brain-gated-actions.sh`
- Rename: `worker/hooks/test-codex-brain-gated-actions.sh` → `worker/hooks/test-brain-gated-actions.sh`
- Modify: `worker/hooks/hooks.json` (PreToolUse group 0 command only)
- Modify: `commander/src/ironclaude/codex_brain_client.py:327` (comment)
- Modify: `commander/tests/test_codex_brain_client.py:1478` (docstring)

**Step 1.** Rename both files.
```bash
git -C /Users/roberthyatt/Code/ironclaude mv worker/hooks/codex-brain-gated-actions.sh worker/hooks/brain-gated-actions.sh && git -C /Users/roberthyatt/Code/ironclaude mv worker/hooks/test-codex-brain-gated-actions.sh worker/hooks/test-brain-gated-actions.sh
```
Expected: no output.

**Step 2 (RED).** Edit `worker/hooks/test-brain-gated-actions.sh`:
- `:5` becomes `HOOK="$SCRIPT_DIR/brain-gated-actions.sh"`.
- Replace `:74-75` (`assert_rc "Claude client inactive" 0 …`) with:
  ```bash
  assert_rc "Claude Brain gated before startup" 2 run_case \
    "mcp__orchestrator__spawn_worker" '{}' "claude" "brain"
  rm -rf "$STATE_DIR"
  ```
- After the `GATED_ACTIONS` loop (after `:128`), add:
  ```bash
  rm -rf "$STATE_DIR"
  assert_rc "AskUserQuestion needs startup" 2 run_case "AskUserQuestion" '{}' "claude" "brain"
  run_case "mcp__orchestrator__get_operator_messages" '{"hours_back":48}' "claude" "brain"
  run_case "mcp__orchestrator__update_ledger" '{}' "claude" "brain"
  run_case "mcp__episodic-memory__search" '{"query":"parity"}' "claude" "brain"
  run_case "mcp__orchestrator__wiki_query" '{"keywords":"parity"}' "claude" "brain"
  assert_rc "Claude Brain AskUserQuestion allowed after gates" 0 run_case "AskUserQuestion" '{}' "claude" "brain"
  assert_rc "Claude Brain AskUserQuestion consumes arms" 2 run_case "AskUserQuestion" '{}' "claude" "brain"
  rm -rf "$STATE_DIR"
  arm_startup
  ```
- `:297`: change `contains("codex-brain-gated-actions.sh")` to `contains("codex-brain-hooks.sh brain-gated-actions.sh")`.
- `:333` and `:346`: change `codex-brain-gated-actions.sh` to `brain-gated-actions.sh`.
- Replace `:353-354` with:
  ```bash
  assert_rc "no-logger: gated action blocked (claude brain)" 2 run_nolog \
    "mcp__orchestrator__spawn_worker" "claude" "brain"
  assert_rc "no-logger: AskUserQuestion blocked (brain)" 2 run_nolog "AskUserQuestion"
  assert_rc "no-logger: gated action allowed (worker)" 0 run_nolog \
    "mcp__plugin_ironclaude_orchestrator__spawn_worker" "codex" "worker"
  ```
- The final messages change `CODEX BRAIN GATE` to `BRAIN GATE`.

**Step 3.** Run RED.
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh
```
Expected: exits 1. The failures include `Claude Brain gated before startup`, `AskUserQuestion needs startup`, `exact hook matcher missing`, `no-logger: gated action blocked (claude brain)` and `no-logger: AskUserQuestion blocked (brain)`. Record every FAIL line.

**Step 4 (GREEN).** In `worker/hooks/brain-gated-actions.sh`:
- Line 2 becomes `# Brain parity gate (Claude and Codex Brains) for the intended BrainClient._tool_guard_logic rules: lookback, memory + wiki before each action, ledger staleness, game-tool ban.`
- Both identity checks (`:10` in the fallback and the main-path check) become `if [ "${IC_ROLE:-}" != "brain" ]; then`.
- In the fallback `case` list, add `AskUserQuestion|\` before `mcp__plugin_ironclaude_orchestrator__spawn_worker|\`.
- In the main `KIND="action"` case list, add `AskUserQuestion|\` before `mcp__plugin_ironclaude_orchestrator__spawn_worker|\`.
- Replace every `"codex-brain-gated-actions"` hook-name string with `"brain-gated-actions"`, including `run_hook`, `block_pretooluse` and the fallback message.

In `worker/hooks/hooks.json`, change only the PreToolUse group-0 handler command to:
```
"command": "bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh brain-gated-actions.sh"
```
The matcher is unchanged.

In `codex_brain_client.py:327` and `test_codex_brain_client.py:1478`, replace `codex-brain-gated-actions.sh:63-65` with `brain-gated-actions.sh`.

**Step 5.** Run GREEN, then confirm the old name is gone.
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh
```
Expected: `ALL BRAIN GATE TESTS PASSED`.
```bash
grep -rn codex-brain-gated-actions /Users/roberthyatt/Code/ironclaude/worker/hooks /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/commander/tests /Users/roberthyatt/Code/ironclaude/commander/hooks
```
Expected: no output, exit 1.

**Step 6.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/brain-gated-actions.sh worker/hooks/test-brain-gated-actions.sh worker/hooks/hooks.json commander/src/ironclaude/codex_brain_client.py commander/tests/test_codex_brain_client.py
```

---

## Task 9: Register the Codex Brain hooks in the plugin and add the canary (R5, R8)

**Files:**
- Create: `commander/hooks/brain-hooks-canary.sh`
- Modify: `worker/hooks/hooks.json` (append only)
- Test (create): `commander/hooks/tests/test-brain-hooks-canary.sh`
- Test (create): `worker/hooks/tests/test-hooks-json-brain-entries.sh`
- Test (modify): `worker/hooks/tests/test-execution-drift-check.sh:117-131`

`test-execution-drift-check.sh` checks 8 and 9 currently locate the drift entries at `PreToolUse[-1]` and `PostToolUse[-1]`. "Last" only recorded where that deploy appended them: Codex trust keys are positional, and `execution-drift-check.sh` has no ordering logic. Appending the Brain groups makes them no longer last. The checks therefore locate the entry by command and assert the real invariant instead: registered exactly once, with a bounded matcher.

**Step 1 (RED).** Create `worker/hooks/tests/test-hooks-json-brain-entries.sh`:
```bash
#!/bin/bash
# The Codex Brain's restriction hooks are registered at the END of each event in
# the plugin hooks.json (append-only keeps existing Codex trust keys), each behind
# codex-brain-hooks.sh, and every dispatched script ships from the repo.
set -u
WORKER_HOOKS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$WORKER_HOOKS/../.." && pwd)"
HJ="$WORKER_HOOKS/hooks.json"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
D='bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh'
EXPECTED_PRE=$(jq -cn --arg d "$D" '[
  {matcher:"Bash|exec_command", s:"block-push.sh"},
  {matcher:"mcp__orchestrator__.*", s:"wiki-synthesis-enforcer.sh"},
  {matcher:"mcp__orchestrator__.*", s:"attention-sweep-enforcer.sh"},
  {matcher:"Agent|Task|spawn_agent", s:"brain-task-gate.sh"},
  {matcher:"Edit|Write|NotebookEdit|MultiEdit|Bash|exec_command|apply_patch", s:"brain-orchestrator-guard.sh"}
] | map({hooks:[{type:"command",command:($d + " " + .s)}], matcher:.matcher})')
EXPECTED_POST=$(jq -cn --arg d "$D" '[
  {matcher:"mcp__orchestrator__get_worker_status", s:"attention-sweep-arm.sh"},
  {matcher:"mcp__orchestrator__update_ledger", s:"block-pin-enforcer.sh"}
] | map({hooks:[{type:"command",command:($d + " " + .s)}], matcher:.matcher})')
EXPECTED_UPS=$(jq -cn --arg d "$D" '[{hooks:[{type:"command",command:($d + " brain-hooks-canary.sh")}]}]')
ACTUAL_PRE=$(jq -c '.hooks.PreToolUse[-5:]' "$HJ")
ACTUAL_POST=$(jq -c '.hooks.PostToolUse[-2:]' "$HJ")
ACTUAL_UPS=$(jq -c '.hooks.UserPromptSubmit[-1:]' "$HJ")
[ "$(jq -S . <<<"$ACTUAL_PRE")" = "$(jq -S . <<<"$EXPECTED_PRE")" ] && pass "PreToolUse ends with the 5 Brain entries" || fail "PreToolUse Brain tail" "$ACTUAL_PRE"
[ "$(jq -S . <<<"$ACTUAL_POST")" = "$(jq -S . <<<"$EXPECTED_POST")" ] && pass "PostToolUse ends with the 2 Brain entries" || fail "PostToolUse Brain tail" "$ACTUAL_POST"
[ "$(jq -S . <<<"$ACTUAL_UPS")" = "$(jq -S . <<<"$EXPECTED_UPS")" ] && pass "UserPromptSubmit ends with the canary" || fail "UserPromptSubmit canary tail" "$ACTUAL_UPS"
G0=$(jq -r '.hooks.PreToolUse[0].hooks[0].command' "$HJ")
[ "$G0" = "$D brain-gated-actions.sh" ] && pass "group 0 dispatches brain-gated-actions" || fail "group 0 command" "$G0"
while IFS= read -r script; do
  if [ -f "$REPO_ROOT/commander/hooks/$script" ] || [ -f "$WORKER_HOOKS/$script" ]; then pass "ships $script"; else fail "ships $script" "not in commander/hooks or worker/hooks"; fi
done < <(jq -r '.hooks[][] .hooks[].command | select(startswith("bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh ")) | split(" ") | last' "$HJ")
if jq -e '[.hooks[][] | select(.matcher == "")] | length == 0' "$HJ" >/dev/null; then pass "no empty matchers"; else fail "no empty matchers"; fi
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```
Then create `commander/hooks/tests/test-brain-hooks-canary.sh`:
```bash
#!/bin/bash
# brain-hooks-canary.sh writes /tmp/ic/codex-brain-gate/<gate session>/hooks-live.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/brain-hooks-canary.sh"
SESSION="canary-test-$$-$(date +%s)"
trap 'rm -rf "/tmp/ic/codex-brain-gate/$SESSION"' EXIT
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
printf '{}' | IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"; RC=$?
[ "$RC" = "0" ] && pass "canary exits 0" || fail "canary exits 0" "rc=$RC"
[ -f "/tmp/ic/codex-brain-gate/$SESSION/hooks-live" ] && pass "marker written" || fail "marker written"
printf '{}' | IRONCLAUDE_BRAIN_GATE_SESSION="../escape" bash "$HOOK"; RC=$?
[ "$RC" = "0" ] && pass "invalid session exits 0" || fail "invalid session exits 0" "rc=$RC"
[ ! -e "/tmp/ic/codex-brain-gate/../escape" ] && pass "invalid session writes nothing" || fail "invalid session writes nothing"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 2.** Run RED.
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-hooks-json-brain-entries.sh; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
```
Expected:
- The first suite fails the three tail checks. `group 0 dispatches brain-gated-actions` and `ships brain-gated-actions.sh` pass, since Task 8 landed. Record whether `no empty matchers` passes.
- The second suite fails `canary exits 0` (rc=127, missing file) and `marker written`.
- The drift suite still passes (`Results: N pass, 0 fail`). Record N.

Empty matchers: record which groups have `""` (`jq -c '[.hooks | to_entries[] | .key as $e | .value | to_entries[] | select(.value.matcher == "") | {e: $e, i: .key}]'`). SessionStart, UserPromptSubmit, Stop and SubagentStop entries carry no matcher key at all, so if `no empty matchers` already passes in RED, record that. The check guards only against the new entries adding `""`.

**Step 3 (GREEN).** Make three changes: (a) and (b) below, then the check, then (c).

(a) Create `commander/hooks/brain-hooks-canary.sh`:
```bash
#!/bin/bash
# brain-hooks-canary.sh — UserPromptSubmit hook for the Commander's Codex Brain.
# Proves the Brain's restriction hooks are trusted and running: the Commander
# requires this marker after the Brain's first turn starts and refuses to run the
# Brain without it (codex_brain_client.py, _await_hooks_canary).
GATE_SESSION="${IRONCLAUDE_BRAIN_GATE_SESSION:-}"
case "$GATE_SESSION" in
  ""|*[!A-Za-z0-9._-]*|.*) exit 0 ;;
esac
mkdir -p "/tmp/ic/codex-brain-gate/$GATE_SESSION" && touch "/tmp/ic/codex-brain-gate/$GATE_SESSION/hooks-live"
exit 0
```

(b) In `worker/hooks/hooks.json`, append these entries to the END of each array. Do not reorder or edit any existing entry.
- **PreToolUse:** add 5 groups, in this order. Each handler command is `bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh <script>`.

  | Matcher | Script |
  |---|---|
  | `Bash\|exec_command` | `block-push.sh` |
  | `mcp__orchestrator__.*` | `wiki-synthesis-enforcer.sh` |
  | `mcp__orchestrator__.*` | `attention-sweep-enforcer.sh` |
  | `Agent\|Task\|spawn_agent` | `brain-task-gate.sh` |
  | `Edit\|Write\|NotebookEdit\|MultiEdit\|Bash\|exec_command\|apply_patch` | `brain-orchestrator-guard.sh` |

- **PostToolUse:** add 2 groups:

  | Matcher | Script |
  |---|---|
  | `mcp__orchestrator__get_worker_status` | `attention-sweep-arm.sh` |
  | `mcp__orchestrator__update_ledger` | `block-pin-enforcer.sh` |

- **UserPromptSubmit:** add 1 group with no matcher: `{"hooks":[{"type":"command","command":"bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh brain-hooks-canary.sh"}]}`.

Each group has the shape `{"hooks":[{"type":"command","command":"…"}],"matcher":"…"}`, which is the existing file's key order.

Check that the append breaks the old drift locator. This proves check (c) is needed and that its replacement can fail:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
```
Expected: exits 1, with `FAIL  8 …` and `FAIL  9 …` lines and no other FAIL lines.

(c) In `worker/hooks/tests/test-execution-drift-check.sh`:
- Line 117 becomes `# 8-9. hooks.json: each drift entry is registered exactly once and its matcher is bounded.`
- Line 118 becomes:
  ```bash
  POST_M=$(jq -r '[.hooks.PostToolUse[] | select(.hooks[0].command | contains("execution-drift-check.sh"))] | if length == 1 then .[0].matcher else "" end' "$HOOKS_DIR/hooks.json")
  ```
- Line 119 becomes:
  ```bash
  PRE_M=$(jq -r '[.hooks.PreToolUse[] | select(.hooks[0].command | contains("execution-drift-check.sh"))] | if length == 1 then .[0].matcher else "" end' "$HOOKS_DIR/hooks.json")
  ```
- In the four `ok`/`bad` labels on lines 122, 124, 128 and 130, change `is last and bounded` to `is registered once and bounded`.

Deleting the entry (length 0) or duplicating it (length 2) yields `""`, so the check fails.

**Step 4.** Run GREEN (same command as Step 2). Expected: the first two suites print `0 failed`, and the drift suite prints `Results: N pass, 0 fail`, with the same N recorded in Step 2.

**Step 5.** One-time append-only check against HEAD.
```bash
python3 -c 'import json,subprocess;h=json.loads(subprocess.check_output(["git","-C","/Users/roberthyatt/Code/ironclaude","show","HEAD:worker/hooks/hooks.json"]));c=json.load(open("/Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json"));g0="bash $HOME/.claude/ironclaude-hooks/codex-brain-hooks.sh brain-gated-actions.sh";bad=[(e,i) for e,gs in h["hooks"].items() for i,g in enumerate(gs) if c["hooks"][e][i]!=(dict(g,hooks=[dict(g["hooks"][0],command=g0)]) if (e,i)==("PreToolUse",0) else g)];print("bad",bad);print({e:(len(h["hooks"][e]),len(c["hooks"][e])) for e in h["hooks"]})'
```
Expected:
```
bad []
{'SessionStart': (3, 3), 'PreToolUse': (7, 12), 'PostToolUse': (7, 9), 'UserPromptSubmit': (2, 3), 'SubagentStop': (1, 1), 'Stop': (1, 1)}
```
The dict's key order follows HEAD's file. Compare the values, not the key order.

**Step 6.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/hooks/brain-hooks-canary.sh worker/hooks/hooks.json commander/hooks/tests/test-brain-hooks-canary.sh worker/hooks/tests/test-hooks-json-brain-entries.sh worker/hooks/tests/test-execution-drift-check.sh
```

---

## Task 10: The Claude Brain's settings sync owns its hooks; the Claude Brain gets a gate session (R1, R4)

**Files:**
- Modify: `commander/src/ironclaude/main.py:5700-5768` (`_sync_brain_settings_hooks`) and `main()` call order (`:5859-5887`)
- Modify: `commander/src/brain/brain_settings_hooks.json`
- Modify: `commander/src/ironclaude/brain_client.py` (`__init__`, `start()`)
- Test: `commander/tests/test_daemon.py` (`:2290-2387`, `:5695-5722`)
- Test: `commander/tests/test_brain_client_gate_session.py` (create)

**Step 1 (RED).** Make these test changes.

(a) In `test_daemon.py`, replace `test_sync_appends_preserves_all_deploys_and_is_idempotent` (`:2290-2326`) with:
```python
    def test_sync_owns_brain_hooks_repoints_retires_preserves_foreign(self, tmp_path):
        import json as _json
        import ironclaude.main as _m
        from ironclaude.main import _sync_brain_settings_hooks

        repo_template = (
            Path(_m.__file__).resolve().parents[2] / "src" / "brain" / "brain_settings_hooks.json"
        )
        template_path = tmp_path / "brain_settings_hooks.json"
        template_path.write_text(repo_template.read_text())
        template = _json.loads(repo_template.read_text())
        original = self._real_settings()
        foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /opt/foreign/audit.sh"}]}
        original["hooks"]["PreToolUse"].append(foreign)
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(_json.dumps(original))
        hooks_src = tmp_path / "src_hooks"
        (hooks_src / "tests").mkdir(parents=True)
        for name in ("block-push.sh", "attention-sweep-enforcer.sh", "brain-hooks-canary.sh"):
            (hooks_src / name).write_text("#!/bin/bash\nexit 0\n")
        (hooks_src / "tests" / "test-x.sh").write_text("#!/bin/bash\n")
        hooks_dst = tmp_path / "dst_hooks"

        _sync_brain_settings_hooks(str(template_path), str(settings_path), str(hooks_src), str(hooks_dst))

        result = _json.loads(settings_path.read_text())
        for event in ("PreToolUse", "PostToolUse"):
            cmds = [h["command"] for e in result["hooks"][event] for h in e["hooks"]]
            expected = [h["command"] for e in template[event] for h in e["hooks"]]
            if event == "PreToolUse":
                expected = ["bash /opt/foreign/audit.sh"] + expected
            assert cmds == expected
        text = settings_path.read_text()
        assert ".ironclaude/brain/hooks/" not in text
        assert "memory-search-enforcer.sh" not in text
        assert "startup-lookback-enforcer.sh" not in text
        # every top-level commander hook script deployed; tests/ not
        assert sorted(p.name for p in hooks_dst.iterdir()) == [
            "attention-sweep-enforcer.sh", "block-push.sh", "brain-hooks-canary.sh",
        ]
        # idempotent: a second run leaves the file byte-identical
        before = settings_path.read_text()
        _sync_brain_settings_hooks(str(template_path), str(settings_path), str(hooks_src), str(hooks_dst))
        assert settings_path.read_text() == before
```
`Path` is imported at `test_daemon.py:12`. The fixture `_real_settings()` (`:2272-2288`) contains only owned scripts: block-push and block-pin under the legacy `$HOME/.ironclaude/brain/hooks`, plus memory-search, wiki-synthesis and the attention-sweep pair. The only foreign command is therefore the one the test adds.

(b) Replace `test_brain_settings_hooks_template_lists_all_three_gates` (`:5695-5722`) with:
```python
def test_brain_settings_hooks_template_is_the_brain_rule_set():
    """The template is the single source of truth for the Claude Brain's IronClaude
    hooks: exactly the Brain rule set, each command under $HOME, retired enforcers absent."""
    import json as _json
    from pathlib import Path
    template = _json.loads(
        (Path(__file__).resolve().parents[1] / "src" / "brain" / "brain_settings_hooks.json").read_text()
    )
    def scripts(event):
        return [
            (e.get("matcher"), h["command"].rsplit("/", 1)[-1])
            for e in template.get(event, []) for h in e["hooks"]
        ]
    assert scripts("PreToolUse") == [
        ("Bash", "block-push.sh"),
        ("", "wiki-synthesis-enforcer.sh"),
        ("", "attention-sweep-enforcer.sh"),
        ("Agent|Task", "brain-task-gate.sh"),
        ("", "brain-gated-actions.sh"),
    ]
    assert scripts("PostToolUse") == [
        ("mcp__orchestrator__get_worker_status", "attention-sweep-arm.sh"),
        ("mcp__orchestrator__update_ledger", "block-pin-enforcer.sh"),
    ]
    for event in ("PreToolUse", "PostToolUse"):
        for e in template[event]:
            for h in e["hooks"]:
                assert h["command"].startswith("bash $HOME/.claude/ironclaude-hooks/"), h["command"]
```

(c) Create `commander/tests/test_brain_client_gate_session.py`:
```python
"""The Claude Brain exports IRONCLAUDE_BRAIN_GATE_SESSION so brain-gated-actions.sh
can keep per-Brain gate state, and resets the startup lookback markers on start."""
import os
from unittest.mock import patch

from ironclaude.brain_client import BrainClient


def test_gate_session_is_unique_and_safe():
    a, b = BrainClient(), BrainClient()
    assert a._brain_gate_session != b._brain_gate_session
    assert a._brain_gate_session.isalnum()


def test_start_exports_gate_session_and_resets_lookback(tmp_path, monkeypatch):
    import ironclaude.brain_client as module
    monkeypatch.setattr(module, "_BRAIN_GATE_ROOT", tmp_path)
    client = BrainClient()
    state = tmp_path / client._brain_gate_session
    state.mkdir()
    for marker in ("lookback-slack", "lookback-ledger", "memory-armed"):
        (state / marker).touch()
    # setenv records the originals so teardown restores them (start() mutates os.environ).
    monkeypatch.setenv("IC_ROLE", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_SESSION", "restored-by-monkeypatch")
    with patch.object(BrainClient, "_kill_brain_subprocess"), \
         patch.object(BrainClient, "discover_episodic_memory_path", side_effect=RuntimeError("stop")):
        try:
            client.start("prompt", cwd=str(tmp_path))
        except RuntimeError:
            pass
    assert os.environ["IRONCLAUDE_BRAIN_GATE_SESSION"] == client._brain_gate_session
    assert not (state / "lookback-slack").exists()
    assert not (state / "lookback-ledger").exists()
    assert (state / "memory-armed").exists()
```

**Step 2.** Run RED.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py tests/test_brain_client_gate_session.py -q -k "sync_owns or brain_rule_set or gate_session or start_exports"
```
Expected: 4 failed. Record the summary line.

**Step 3 (GREEN).** Make four changes.

(a) Replace `commander/src/brain/brain_settings_hooks.json` with:
```json
{
  "_comment": "Single source of truth for the Claude Brain's IronClaude hooks in ~/.ironclaude/brain/.claude/settings.json. _sync_brain_settings_hooks() runs at daemon start: it deploys every commander/hooks/*.sh to ~/.claude/ironclaude-hooks/, removes every settings entry whose script is listed here or retired (memory-search-enforcer.sh, startup-lookback-enforcer.sh: brain-gated-actions.sh owns those rules), keeps foreign entries, and appends these. brain-orchestrator-guard stays in settings.local.json. The Codex Brain registers the same scripts in worker/hooks/hooks.json behind codex-brain-hooks.sh.",
  "PreToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/block-push.sh"}]},
    {"matcher": "", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/wiki-synthesis-enforcer.sh"}]},
    {"matcher": "", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/attention-sweep-enforcer.sh"}]},
    {"matcher": "Agent|Task", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/brain-task-gate.sh"}]},
    {"matcher": "", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/brain-gated-actions.sh"}]}
  ],
  "PostToolUse": [
    {"matcher": "mcp__orchestrator__get_worker_status", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/attention-sweep-arm.sh"}]},
    {"matcher": "mcp__orchestrator__update_ledger", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/block-pin-enforcer.sh"}]}
  ]
}
```

(b) In `main.py`, replace `_sync_brain_settings_hooks` (`:5700-5768`) with the following, adding `import copy` to the imports:
```python
# Brain hook scripts the settings sync once registered whose rules brain-gated-actions.sh now owns.
_RETIRED_BRAIN_HOOK_SCRIPTS = frozenset({"memory-search-enforcer.sh", "startup-lookback-enforcer.sh"})
_HOOK_SCRIPT_RE = re.compile(r"([A-Za-z0-9._-]+\.sh)\s*$")


def _hook_script_name(command: str) -> str | None:
    """The script basename a hook command runs (its trailing *.sh token), else None."""
    m = _HOOK_SCRIPT_RE.search(command or "")
    return m.group(1) if m else None


def _sync_brain_settings_hooks(template_path: str, settings_path: str,
                               hooks_src_dir: str, hooks_dst_dir: str) -> None:
    """Make the template the source of truth for the Claude Brain's IronClaude hooks.

    Deploys every top-level commander/hooks/*.sh to hooks_dst_dir. In settings.json
    PreToolUse/PostToolUse, removes each handler whose script is in the template or
    retired (this also repoints legacy ~/.ironclaude/brain/hooks/ commands), drops
    groups left empty, keeps foreign handlers in place, then appends the template's
    groups. Idempotent: an unchanged result is not rewritten. A corrupt settings.json
    is left for a human to repair. settings.local.json is untouched."""
    try:
        with open(template_path) as f:
            template = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("brain_settings_hooks template unavailable (%s); skipping sync", e)
        return
    events = ("PreToolUse", "PostToolUse")
    if not any(template.get(event) for event in events):
        return

    os.makedirs(hooks_dst_dir, exist_ok=True)
    for name in sorted(os.listdir(hooks_src_dir)):
        src = os.path.join(hooks_src_dir, name)
        if name.endswith(".sh") and os.path.isfile(src):
            shutil.copy2(src, os.path.join(hooks_dst_dir, name))

    owned = set(_RETIRED_BRAIN_HOOK_SCRIPTS)
    for event in events:
        for entry in template.get(event, []):
            for hook in entry.get("hooks", []):
                script = _hook_script_name(hook.get("command", ""))
                if script is None:
                    continue
                owned.add(script)
                if not (os.path.isfile(os.path.join(hooks_src_dir, script))
                        or os.path.isfile(os.path.join(hooks_dst_dir, script))):
                    logger.warning("brain settings hook script missing at source: %s", script)

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    except json.JSONDecodeError as e:
        logger.error(
            "brain settings.json is corrupt (%s); skipping hook sync to avoid clobbering "
            "existing guardrail hooks at %s", e, settings_path,
        )
        return
    hooks = settings.setdefault("hooks", {})
    changed = False
    for event in events:
        current = hooks.get(event, [])
        kept = []
        for entry in current:
            handlers = [
                h for h in entry.get("hooks", [])
                if _hook_script_name(h.get("command", "")) not in owned
            ]
            if handlers:
                kept.append({**entry, "hooks": handlers})
        merged = kept + copy.deepcopy(template.get(event, []))
        if merged != current:
            hooks[event] = merged
            changed = True
    if changed:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
        logger.info("Synced brain settings hooks into %s", settings_path)
```

(c) In `main()`, move the `_deploy_worker_hooks(repo_root)` call and its 2-line comment (`:5885-5887`) to just before the `# SF2/R5a: register …` comment (`:5859`). That way `brain-gated-actions.sh`, a worker hook, is deployed before the sync checks for it. Replace the `# SF2/R5a …` comment (`:5859-5862`) with:
```python
    # Claude Brain hook registration: brain_settings_hooks.json is the source of truth
    # for the Brain's IronClaude hooks in settings.json (see _sync_brain_settings_hooks).
```

(d) In `brain_client.py`:
- Add `import uuid` to the imports.
- After `from ironclaude.slack_interface import parse_reply_to_marker`, add a module constant:
  ```python
  # Per-Brain gate state for brain-gated-actions.sh (shared root with the Codex Brain).
  _BRAIN_GATE_ROOT = Path("/tmp/ic/codex-brain-gate")
  ```
- In `__init__`, after `self._lookback_ledger: bool = False` (`:279`), add `self._brain_gate_session = uuid.uuid4().hex`.
- In `start()`, directly after `os.environ["IC_ROLE"] = "brain"` (`:327`), add:
  ```python
          os.environ["IRONCLAUDE_BRAIN_GATE_SESSION"] = self._brain_gate_session
          for marker in ("lookback-slack", "lookback-ledger"):
              try:
                  (_BRAIN_GATE_ROOT / self._brain_gate_session / marker).unlink()
              except FileNotFoundError:
                  pass
              except OSError as exc:
                  logger.warning("Failed to reset Brain gate marker %s: %s", marker, exc)
  ```
  `brain_client.py:34` defines `logger = logging.getLogger("ironclaude.brain")`.
- In `start()`, delete the legacy cleanup loop at `:320-324`:
  ```python
          for f in glob("/tmp/ic/lookback-slack-*") + glob("/tmp/ic/lookback-ledger-*"):
              try:
                  os.remove(f)
              except OSError:
                  pass
  ```
  Only the retired `startup-lookback-enforcer.sh` writes `/tmp/ic/lookback-*`: its `:27-28` are the only writers in `commander/` and `worker/hooks/`. Keep `from glob import glob`, because `:221` still uses it. No test references these paths.

**Step 4.** Run GREEN, then the sync and brain tests.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_daemon.py tests/test_brain_client_gate_session.py tests/test_brain_client.py -q
```
Expected: all pass. Record the summary. If `tests/test_brain_client.py` does not exist, drop it from the command and record that.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/brain/brain_settings_hooks.json commander/src/ironclaude/brain_client.py commander/tests/test_daemon.py commander/tests/test_brain_client_gate_session.py
```

---

## Task 11: Provenance check and retirement of the untrusted project hooks file (R9, R10)

**Files:**
- Modify: `commander/src/ironclaude/main.py` (new functions after `_sync_brain_settings_hooks`; calls in `main()`)
- Test (create): `commander/tests/test_brain_hook_provenance.py`

**Step 1 (RED).** Create `commander/tests/test_brain_hook_provenance.py`:
```python
"""R9: every Brain hook must run a script shipped from the repo. R10: the Codex
Brain's never-trusted project .codex/hooks.json is retired."""
import json

from ironclaude.main import _brain_hook_provenance_errors, _retire_codex_brain_project_hooks

H = "bash $HOME/.claude/ironclaude-hooks/"
D = H + "codex-brain-hooks.sh "


def _fixture(tmp_path, settings_cmds, plugin_cmds):
    commander_hooks = tmp_path / "commander_hooks"
    worker_hooks = tmp_path / "worker_hooks"
    commander_hooks.mkdir()
    worker_hooks.mkdir()
    (commander_hooks / "block-push.sh").write_text("")
    (worker_hooks / "brain-gated-actions.sh").write_text("")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "", "hooks": [{"type": "command", "command": c}]} for c in settings_cmds]}}))
    plugin = tmp_path / "hooks.json"
    plugin.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": c}]} for c in plugin_cmds],
        "SessionStart": [{"hooks": [{"type": "command",
                                     "command": "bash ${CLAUDE_PLUGIN_ROOT}/hooks/episodic-memory-sync.sh --background"}]}],
    }}))
    return [str(settings), str(tmp_path / "missing-local.json")], str(plugin), [str(commander_hooks), str(worker_hooks)]


def test_clean_configuration_has_no_errors(tmp_path):
    args = _fixture(tmp_path, [H + "block-push.sh"], [D + "brain-gated-actions.sh"])
    assert _brain_hook_provenance_errors(*args) == []


def test_settings_script_outside_repo_is_reported(tmp_path):
    args = _fixture(tmp_path, ["bash $HOME/.ironclaude/brain/hooks/local-only.sh"], [])
    errors = _brain_hook_provenance_errors(*args)
    assert len(errors) == 1 and "local-only.sh" in errors[0]


def test_plugin_dispatch_outside_repo_is_reported(tmp_path):
    args = _fixture(tmp_path, [], [D + "not-shipped.sh"])
    errors = _brain_hook_provenance_errors(*args)
    assert len(errors) == 1 and "not-shipped.sh" in errors[0]


def test_corrupt_settings_is_reported(tmp_path):
    settings_paths, plugin, dirs = _fixture(tmp_path, [], [])
    (tmp_path / "settings.json").write_text("{not json")
    errors = _brain_hook_provenance_errors(settings_paths, plugin, dirs)
    assert len(errors) == 1 and "settings.json" in errors[0]


def test_retire_renames_project_hooks(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text("{}")
    _retire_codex_brain_project_hooks(str(tmp_path))
    assert not (codex_dir / "hooks.json").exists()
    assert (codex_dir / "hooks.json.retired").read_text() == "{}"
    _retire_codex_brain_project_hooks(str(tmp_path))  # absent file: no-op
    assert (codex_dir / "hooks.json.retired").read_text() == "{}"
```

**Step 2.** Run RED.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_hook_provenance.py -q
```
Expected: a collection error (ImportError on `_brain_hook_provenance_errors`). Record the summary.

**Step 3 (GREEN).** Add to `main.py`, after `_sync_brain_settings_hooks`:
```python
_CODEX_BRAIN_DISPATCHER = "codex-brain-hooks.sh"


def _brain_hook_provenance_errors(settings_paths: list[str], plugin_hooks_path: str,
                                  repo_hook_dirs: list[str]) -> list[str]:
    """R9: every Brain hook must run a script shipped from the repo.

    Checks every command in the Claude Brain's settings files (a missing file is fine)
    and every Codex Brain dispatch in the plugin hooks.json against the *.sh files in
    repo_hook_dirs. Returns one human-readable error per violation."""
    shipped = {
        name for d in repo_hook_dirs if os.path.isdir(d)
        for name in os.listdir(d) if name.endswith(".sh")
    }
    errors: list[str] = []

    def check(source: str, event: str, command: str) -> None:
        script = _hook_script_name(command)
        if script is None or script not in shipped:
            errors.append(f"{source} {event}: {command!r} does not run a script shipped from the repo")

    for path in settings_paths:
        try:
            with open(path) as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            errors.append(f"{path}: unreadable ({e})")
            continue
        for event, entries in (data.get("hooks") or {}).items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    check(path, event, hook.get("command", ""))

    try:
        with open(plugin_hooks_path) as f:
            plugin = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        errors.append(f"{plugin_hooks_path}: unreadable ({e})")
        return errors
    for event, entries in (plugin.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if f"/{_CODEX_BRAIN_DISPATCHER} " in command:
                    check(plugin_hooks_path, event, command)
    return errors


def _retire_codex_brain_project_hooks(brain_cwd: str) -> None:
    """R10: the Codex Brain's project .codex/hooks.json was never trusted and never ran;
    its hooks now come from the IronClaude plugin. Rename it so a later 'Trust all' in
    the Brain directory cannot make every Brain hook run twice."""
    path = os.path.join(brain_cwd, ".codex", "hooks.json")
    if os.path.isfile(path):
        os.replace(path, path + ".retired")
        logger.info("Retired untrusted Codex Brain project hooks: %s", path)
```
In `main()`, directly after `slack = SlackBot(...)` (`:5899`), insert:
```python
    _retire_codex_brain_project_hooks(brain_cwd)
    provenance_errors = _brain_hook_provenance_errors(
        [os.path.join(brain_cwd, ".claude", "settings.json"),
         os.path.join(brain_cwd, ".claude", "settings.local.json")],
        os.path.join(os.path.dirname(repo_root), "worker", "hooks", "hooks.json"),
        [os.path.join(repo_root, "hooks"), os.path.join(os.path.dirname(repo_root), "worker", "hooks")],
    )
    if provenance_errors:
        message = ("Commander not started: Brain hooks must run scripts shipped from the repo.\n"
                   + "\n".join(provenance_errors))
        logger.error(message)
        slack.post_message(message)
        sys.exit(1)
```

**Step 4.** Run GREEN.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_hook_provenance.py tests/test_daemon.py -q
```
Expected: all pass. Record the summary.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_brain_hook_provenance.py
```

---

## Task 12: Trust warm-up and canary gate (R7, R8)

**Files:**
- Modify: `commander/src/ironclaude/codex_brain_client.py` (constants near `:53`, `start()` `:1158-1162`, new `_await_hooks_canary`)
- Modify: `commander/src/ironclaude/main.py` (new `_warm_up_codex_hook_trust`; call before `brain.start`)
- Test (create): `commander/tests/test_codex_brain_hooks_canary.py`
- Test (create): `commander/tests/test_codex_hook_trust_warmup.py`

**Step 1 (RED).** Create `commander/tests/test_codex_brain_hooks_canary.py`:
```python
"""R8: the Codex Brain may run only after its UserPromptSubmit canary proves the
Brain restriction hooks are trusted and running."""
from unittest.mock import MagicMock

from ironclaude.codex_brain_client import CodexBrainClient


class _FakeProc:
    pid = 4321
    stdin = MagicMock()
    stdout = MagicMock()
    stderr = MagicMock()

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def _client(monkeypatch, tmp_path, write_canary):
    import ironclaude.codex_brain_client as module
    monkeypatch.setattr(module, "_BRAIN_GATE_ROOT", tmp_path)
    monkeypatch.setattr(module, "_HOOKS_CANARY_TIMEOUT", 0.3)
    client = CodexBrainClient()
    monkeypatch.setattr(client, "_runtime_preflight", lambda: {
        "schema_version": 1, "status": "healthy", "reason": "destination-equivalent",
        "source_companion": "/s", "destination_companion": "/d",
    })
    monkeypatch.setattr(client, "_preflight_orchestrator", lambda: None)
    monkeypatch.setattr(client, "_reader_loop", lambda: None)
    monkeypatch.setattr(client, "_stderr_loop", lambda: None)
    monkeypatch.setattr(client, "_verify_orchestrator_mcp", lambda: None)
    responses = [{"result": {}}, {"result": {"thread": {"id": "thread-1"}}}]
    monkeypatch.setattr(client, "_await_response",
                        lambda request_id, timeout: responses.pop(0) if responses else None)

    def write(payload):
        if payload.get("method") == "turn/start" and write_canary:
            marker = tmp_path / client._brain_gate_session / "hooks-live"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        return True

    monkeypatch.setattr(client, "_write", write)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: _FakeProc())
    return client


def test_canary_present_keeps_brain_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, write_canary=True)
    client.start("system prompt", cwd="/tmp")
    assert client._running is True
    assert client.restart_reason == ""


def test_canary_absent_stops_brain_visibly(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, write_canary=False)
    client.start("system prompt", cwd="/tmp")
    assert client._running is False
    assert "hooks are not running" in client.restart_reason
    assert any("[CODEX BRAIN ERROR]" in r for r in client.get_pending_responses())


def test_stale_canary_from_earlier_start_does_not_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, write_canary=False)
    stale = tmp_path / client._brain_gate_session / "hooks-live"
    stale.parent.mkdir(parents=True)
    stale.touch()
    client.start("system prompt", cwd="/tmp")
    assert client._running is False
```
Then create `commander/tests/test_codex_hook_trust_warmup.py`:
```python
"""R7: before the Codex Brain starts, a throwaway Codex TUI in a dedicated directory
answers Codex's hook-trust prompt, then is killed by its exact session name."""
from unittest.mock import MagicMock

from ironclaude.main import _HOOK_TRUST_WARMUP_SESSION, _warm_up_codex_hook_trust


def test_warmup_spawns_waits_and_kills(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = MagicMock()
    tools._wait_for_ready.return_value = False  # readiness is not the success signal
    _warm_up_codex_hook_trust(tools, tmux, "/opt/codex", str(tmp_path), timeout=5)
    tmux.spawn_session.assert_called_once_with(
        _HOOK_TRUST_WARMUP_SESSION, "exec /opt/codex --no-daemon", cwd=str(tmp_path))
    tools._wait_for_ready.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, timeout=5, client="codex")
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)


def test_warmup_kills_even_when_wait_raises(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = MagicMock()
    tools._wait_for_ready.side_effect = RuntimeError("boom")
    _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5)
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)


def test_warmup_replaces_leftover_session_and_skips_wait_on_spawn_failure(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = True
    tmux.spawn_session.return_value = False
    tools = MagicMock()
    _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5)
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)
    tools._wait_for_ready.assert_not_called()
```

**Step 2.** Run RED.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_hooks_canary.py tests/test_codex_hook_trust_warmup.py -q
```
Expected:
- The canary tests fail: `_HOOKS_CANARY_TIMEOUT` is missing, so `monkeypatch.setattr` raises AttributeError.
- The warm-up module fails with an ImportError.

Record the summary.

**Step 3 (GREEN).** Make two changes.

(a) In `codex_brain_client.py`, after `_BRAIN_GATE_ROOT` (`:53`), add:
```python
# R8: brain-hooks-canary.sh (a UserPromptSubmit Brain hook) writes this marker when the
# Brain's turn starts; without it the Brain's restriction hooks are untrusted or missing.
_HOOKS_CANARY_MARKER = "hooks-live"
_HOOKS_CANARY_TIMEOUT = 30.0
```
Replace `start()`'s tail (`:1161-1162`) with:
```python
        if system_prompt:
            canary = _BRAIN_GATE_ROOT / self._brain_gate_session / _HOOKS_CANARY_MARKER
            try:
                canary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._fail_start(f"Codex Brain hooks canary could not be reset: {exc}")
                return
            self.send_message(system_prompt)
            if not self._await_hooks_canary(canary):
                self._fail_start(
                    "Codex Brain hooks are not running (no UserPromptSubmit canary within "
                    f"{_HOOKS_CANARY_TIMEOUT:.0f}s): the Brain's restriction hooks are untrusted "
                    "or missing, so the Brain was stopped. Restart the Commander to re-run the "
                    "hook-trust warm-up."
                )
```
Add the method after `start()`:
```python
    def _await_hooks_canary(self, canary: Path) -> bool:
        deadline = time.monotonic() + _HOOKS_CANARY_TIMEOUT
        while time.monotonic() < deadline:
            if canary.exists():
                return True
            time.sleep(0.1)
        return canary.exists()
```

(b) In `main.py`, add after `_retire_codex_brain_project_hooks`:
```python
_HOOK_TRUST_WARMUP_SESSION = "ic-codex-hook-trust"


def _warm_up_codex_hook_trust(tools, tmux, codex_path: str, workdir: str, timeout: int = 90) -> None:
    """R7: Codex runs a plugin hook only once it is trusted, and the Brain's app-server
    cannot show the trust prompt. Open a throwaway Codex TUI in a dedicated directory
    (never the Brain's cwd) so _wait_for_ready answers "Trust all and continue" as it
    does for workers, then kill it by exact session name. Success is proven by the
    Brain's canary (codex_brain_client._await_hooks_canary), not by readiness here."""
    if tmux.has_session(_HOOK_TRUST_WARMUP_SESSION):
        tmux.kill_session(_HOOK_TRUST_WARMUP_SESSION)
    command = f"exec {shlex.quote(codex_path)} --no-daemon"
    if not tmux.spawn_session(_HOOK_TRUST_WARMUP_SESSION, command, cwd=workdir):
        logger.error("Codex hook-trust warm-up could not start")
        return
    try:
        ready = tools._wait_for_ready(_HOOK_TRUST_WARMUP_SESSION, timeout=timeout, client="codex")
        logger.info("Codex hook-trust warm-up finished ready=%s", ready)
    except Exception as exc:  # noqa: BLE001 — the canary decides; never block start-up here
        logger.error("Codex hook-trust warm-up failed: %s", exc)
    finally:
        tmux.kill_session(_HOOK_TRUST_WARMUP_SESSION)
```
In `main()`, directly before `ensure_brain_trusted(brain_cwd)` inside the "Start brain" `try` (`:5947`), insert:
```python
        if getattr(brain, "client_name", None) == "codex":
            from ironclaude.orchestrator_mcp import OrchestratorTools
            warmup_dir = os.path.expanduser("~/.ironclaude/codex-hook-trust")
            os.makedirs(warmup_dir, exist_ok=True)
            codex_path = (config.get("providers", {}).get("clients", {})
                          .get("codex", {}).get("path", "codex"))
            _warm_up_codex_hook_trust(
                OrchestratorTools(registry, tmux, db_conn=conn, config=config, ssh_manager=ssh_manager),
                tmux, codex_path, warmup_dir,
            )
```

**Step 4.** Run GREEN, plus the existing client suite.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_hooks_canary.py tests/test_codex_hook_trust_warmup.py tests/test_codex_brain_client.py -q
```
Expected: all pass. Record the summary.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_client.py commander/src/ironclaude/main.py commander/tests/test_codex_brain_hooks_canary.py commander/tests/test_codex_hook_trust_warmup.py
```

---

## Task 13: Full suites (R11)

**Files:** none modified. This is a verification task only. The machine plan lists `docs/plans/2026-10-10-codex-brain-parity.md` as `allowed_files`, because `create_plan` requires a non-empty list. That file is not edited.

**Step 1.** Run the hook suites.
```bash
make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-brain-parity-hooks.log 2>&1; echo "test-hooks exit=$?"
```
Expected: `test-hooks exit=0`. On failure, `grep -n FAIL /private/tmp/ironclaude-brain-parity-hooks.log` and stop.

**Step 2.** Run the full Commander suite (background; Bash timeout 1800000).
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-brain-parity-full.log 2>&1; echo "pytest exit=$?"
```
Then run `tail -n 5 /private/tmp/ironclaude-brain-parity-full.log`. Expected: the only failure allowed is `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd` (L-T2). Record the summary line.

---

## Task 14: Deploy, restart the Commander, and live acceptance (R11)

**Files:**
- Modify: `worker/.codex-plugin/plugin.json` (cachebuster, rewritten by the release)
- Create: `docs/plans/2026-10-10-codex-brain-parity-live-verification.md`

There are no unit tests in this task: it deploys and verifies live. Each check names the hook it expects, so a missing or wrong hook fails that check.

**Step 1.** Release the Codex plugin, so the cache gets the new `hooks.json` and dispatcher.
```bash
make -C /Users/roberthyatt/Code/ironclaude codex-plugin-release
```
Expected: exits 0 and prints the installed plugin root. Record it.

**Step 2.** Verify the Codex cache carries the Brain entries.
```bash
find /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude -path '*/hooks/hooks.json' -newer /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-10-codex-brain-parity.md -exec grep -c codex-brain-hooks.sh {} +
```
Expected: one path with count `9` (group 0 + 5 Pre + 2 Post + 1 UPS).

**Step 3.** Deploy the hook scripts to the stable dir and the Claude cache.
```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```
Expected: `Deployed N hooks to /Users/roberthyatt/.claude/ironclaude-hooks`. Record N.

**Step 4.** Find the Claude cache version.
```bash
ls -1 /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude
```
Record the newest version, V. Then copy the hooks file into it:
```bash
cp /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/V/hooks/hooks.json
```
Here V is the literal from the previous command. Then remove the stale renamed script from the stable dir:
```bash
rm /Users/roberthyatt/.claude/ironclaude-hooks/codex-brain-gated-actions.sh
```

**Step 5.** Stop the Commander.
```bash
make -C /Users/roberthyatt/Code/ironclaude/commander stop
```
Expected: a clean stop.

**Step 6.** Relaunch it detached, with the Claude Code environment stripped. This is a normal Bash call, not `run_in_background`.
```bash
env -u CLAUDECODE -u CLAUDE_CODE_BRIDGE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_CODE_SESSION_ID -u CLAUDE_EFFORT -u CLAUDE_PID nohup python3 -c "import os; os.setsid(); os.execvp('make',['make','-C','/Users/roberthyatt/Code/ironclaude/commander','run'])" >/tmp/ironclaude-restart-launch.log 2>&1 & disown
```

**Step 7 (a separate step, for the wait).** Check the start-up log.
```bash
grep -n -e "Codex hook-trust warm-up" -e "Brain SDK client started client=codex" -e "Brain SDK client not started" -e "hooks are not running" -e "Brain hooks must run scripts shipped" /tmp/ic/daemon.log
```
Expected after the restart:
- a `Codex hook-trust warm-up finished` line;
- `Brain SDK client started client=codex`;
- no `hooks are not running` and no provenance failure.

If the Brain line is not there yet, repeat this step.

**Step 8.** Verify the canary marker.
```bash
find /tmp/ic/codex-brain-gate -name hooks-live -newer /tmp/ironclaude-restart-launch.log
```
Expected: exactly one path.

**Step 9.** Verify the Claude Brain settings after the sync.
```bash
jq -r '.hooks[][] .hooks[].command' /Users/roberthyatt/.ironclaude/brain/.claude/settings.json
```
Expected: exactly the 7 template commands, in template order. No `.ironclaude/brain/hooks/`, no `memory-search-enforcer`, no `startup-lookback-enforcer`.

Then check the retired file:
```bash
ls /Users/roberthyatt/.ironclaude/brain/.codex
```
Expected: `hooks.json.retired` and no `hooks.json`.

**Step 10.** Find the Brain's start-up rollout.
```bash
find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /tmp/ironclaude-restart-launch.log
```
Record the paths. Identify the Brain's file with `grep -l '"cwd":"/Users/roberthyatt/.ironclaude/brain"'` on each path.

**Step 11.** Check the Brain rollout (BR = the Step 10 Brain path).
```bash
grep -c professional-mode-guard BR
```
Expected: `0`.
Codex rollouts record MCP calls as `"server":"orchestrator","tool":"<name>"`, never as `mcp__orchestrator__<name>`. The 10-04 Brain rollout has 58 such records, 12 of them `get_worker_status`. The broken 10-10 15:27 rollout has 0, so this check can fail.
```bash
grep -o '"server":"orchestrator","tool":"[a-z_]*"' BR
```
Expected: one or more lines from the start-up lookback (for example `get_operator_messages`, `get_task_ledger`, `update_ledger`). Record the distinct names.

**Step 11b.** Acceptance for design Testing item 1. It needs an input the executor cannot supply itself. Ask the operator, with AskUserQuestion, to post "what unfinished work is sitting, idle, right now?" in Slack, and to confirm once they have. Then run:
```bash
grep -c '"server":"orchestrator","tool":"get_worker_status"' BR
```
Expected: `1` or more.

Then check for the worker block again:
```bash
grep -c professional-mode-guard BR
```
Expected: `0`.

If the operator defers the question, record "Step 11b pending operator question" in the verification doc. Do not substitute another check.

**Step 12.** Negative probe through Codex, with the Brain identity and real trust (no bypass). Create the probe repo:
```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo && git -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo init -q
```
Then run (Bash timeout 600000):
```bash
env IC_ROLE=brain IRONCLAUDE_CLIENT=codex IRONCLAUDE_BRAIN_GATE_SESSION=parity-probe codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo --skip-git-repo-check -s workspace-write --json "Do these in order. Do not retry anything that is blocked. 1) Run the shell command: git push origin main 2) Create the file x.txt containing hi with your file-editing tool. 3) Spawn exactly one subagent whose only task is to reply OK. 4) Run the shell command: cat /etc/hosts. Then reply DONE." > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brain-turn.jsonl 2>&1; echo "exit=$?"
```

**Step 13.** Read the probe's evidence. Check the blocks:
```bash
grep -o -e 'block-push' -e 'brain-orchestrator-guard' -e 'Brain cannot use mutation tools' -e 'brain-task-gate' -e 'professional-mode-guard' -e 'git push is blocked' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brain-turn.jsonl
```
Then check that the file was not created:
```bash
ls /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo
```
Expected:
- The push is blocked, by `git push is blocked` or `brain-orchestrator-guard`.
- The edit is blocked (`Brain cannot use mutation tools`).
- The subagent is blocked (`brain-task-gate`).
- There is no `professional-mode-guard`.
- `x.txt` is absent.

The `cat` step's success is recorded from the JSON, as read parity. Record the matched lines verbatim.

**Step 14.** Write `docs/plans/2026-10-10-codex-brain-parity-live-verification.md` with the recorded output of Steps 1–13, then stage the doc and the cachebuster.
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-10-codex-brain-parity-live-verification.md && git -C /Users/roberthyatt/Code/ironclaude add worker/.codex-plugin/plugin.json
```
Expected: no output.
