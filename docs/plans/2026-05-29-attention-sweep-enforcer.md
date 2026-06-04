# Attention Sweep Enforcer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add two hooks to the Brain's Claude Code session — a PostToolUse arm hook and a PreToolUse gate hook — that enforce periodic full-fleet sweeps before any gated worker action.

**Architecture:** Split hook design. `attention-sweep-arm.sh` fires PostToolUse on `get_worker_status` and arms the flag only if the response is a valid JSON array (confirmed successful fleet query). `attention-sweep-enforcer.sh` fires PreToolUse on all tools and blocks gated worker actions if the flag is missing or older than 3 minutes. Both registered in the Brain's `~/.ironclaude/brain/.claude/settings.json`.

**Tech Stack:** Bash, jq, standard POSIX utilities (`find`, `touch`, `rm`)

---

## Task 1: Create attention-sweep-arm.sh (PostToolUse arm hook)

**Files:**
- Create: `~/.claude/ironclaude-hooks/attention-sweep-arm.sh`

No tests required: bash hook script; manually verified per design testing strategy.

**Step 1: Create the arm hook script**

Create `~/.claude/ironclaude-hooks/attention-sweep-arm.sh` with this exact content:

```bash
#!/bin/bash
# attention-sweep-arm.sh — PostToolUse hook
# Arms the sweep gate ONLY when get_worker_status(all) returns a valid fleet list.
# Arming happens PostToolUse (not PreToolUse) so we can verify the response
# before granting sweep authority. A failed get_worker_status must NOT arm the gate.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "attention-sweep-arm"

INPUT=$(cat)
init_session_id

# Flag file path (session-scoped, shared with attention-sweep-enforcer.sh)
FLAG_DIR="/tmp/ic"
SWEEP_FLAG="$FLAG_DIR/sweep-armed-$SESSION_TAG"

# Only relevant for get_worker_status — registered with precise matcher,
# but guard defensively in case matcher config changes.
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
if [[ "$TOOL_NAME" != "mcp__orchestrator__get_worker_status" ]]; then
  log_hook "attention-sweep-arm" "Allowed" "not get_worker_status — pass through"
  exit 0
fi

# Single-worker queries do not arm the gate
WORKER_ID=$(echo "$INPUT" | jq -r '.tool_input.worker_id // empty' 2>/dev/null || true)
if [ -n "$WORKER_ID" ]; then
  log_hook "attention-sweep-arm" "Allowed" "get_worker_status(single) — sweep NOT armed"
  exit 0
fi

# Arm only if the response is a JSON array (valid fleet list)
# A failed MCP call returns an error object or string, not an array.
IS_ARRAY=$(echo "$INPUT" | jq -e '.tool_response | arrays' 2>/dev/null || true)
if [ -n "$IS_ARRAY" ]; then
  mkdir -p "$FLAG_DIR"
  touch "$SWEEP_FLAG"
  log_hook "attention-sweep-arm" "Allowed" "get_worker_status(all) response validated — sweep gate armed"
else
  log_hook "attention-sweep-arm" "Allowed" "get_worker_status(all) response was not a list — sweep NOT armed (daemon error?)"
fi

exit 0
```

**Step 2: Make the script executable**

Run:
```bash
chmod +x ~/.claude/ironclaude-hooks/attention-sweep-arm.sh
```

Expected: no output (success)

**Step 3: Verify script syntax**

Run:
```bash
bash -n ~/.claude/ironclaude-hooks/attention-sweep-arm.sh
```

Expected: no output (clean parse)

---

## Task 2: Create attention-sweep-enforcer.sh (PreToolUse gate hook)

**Files:**
- Create: `~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh`

No tests required: bash hook script; manually verified per design testing strategy.

**Step 1: Create the gate hook script**

Create `~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh` with this exact content:

```bash
#!/bin/bash
# attention-sweep-enforcer.sh — PreToolUse hook
# Blocks gated worker actions unless a full-fleet get_worker_status() call
# successfully returned within the last 3 minutes. The flag is armed PostToolUse
# by attention-sweep-arm.sh — only a confirmed successful response arms the gate.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "attention-sweep-enforcer"

INPUT=$(cat)
init_session_id

# Fail-closed on TOOL_NAME parse: can't route without knowing the tool name.
# Do NOT use || true here — if jq fails, block with a clear error.
TOOL_NAME=""
if ! TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null); then
  block_pretooluse "attention-sweep-enforcer" \
    "Hook parse error — jq failed to parse hook input. Check jq installation."
fi

# Flag file path (session-scoped, shared with attention-sweep-arm.sh)
FLAG_DIR="/tmp/ic"
SWEEP_FLAG="$FLAG_DIR/sweep-armed-$SESSION_TAG"
SWEEP_TTL=3

# get_worker_status: pass through — arming is handled PostToolUse by attention-sweep-arm.sh
if [[ "$TOOL_NAME" == "mcp__orchestrator__get_worker_status" ]]; then
  log_hook "attention-sweep-enforcer" "Allowed" "get_worker_status — arm handled PostToolUse"
  exit 0
fi

# PASS-THROUGH: query/read tools don't require a sweep
if [[ "$TOOL_NAME" == mcp__orchestrator__get_* ]]; then
  log_hook "attention-sweep-enforcer" "Allowed" "query tool bypass"
  exit 0
fi

# GATED action tools: require a recent full-fleet sweep
case "$TOOL_NAME" in
  mcp__orchestrator__send_to_worker|\
  mcp__orchestrator__send_keys_to_worker|\
  mcp__orchestrator__approve_plan|\
  mcp__orchestrator__reject_plan|\
  mcp__orchestrator__kill_worker|\
  mcp__orchestrator__spawn_worker|\
  mcp__orchestrator__spawn_workers)
    # No sweep at all
    if [ ! -f "$SWEEP_FLAG" ]; then
      block_pretooluse "attention-sweep-enforcer" \
        "Attention Sweep required — call get_worker_status() for all workers before acting on one."
    fi
    # Sweep too old (TTL expired)
    STALE=$(find "$SWEEP_FLAG" -mmin "+${SWEEP_TTL}" 2>/dev/null || true)
    if [ -n "$STALE" ]; then
      rm -f "$SWEEP_FLAG"
      block_pretooluse "attention-sweep-enforcer" \
        "Attention Sweep expired (>${SWEEP_TTL}m) — call get_worker_status() for all workers to re-arm."
    fi
    # Sweep fresh — allow (flag retained for subsequent actions within TTL)
    log_hook "attention-sweep-enforcer" "Allowed" "$TOOL_NAME — sweep check passed"
    exit 0
    ;;
esac

# Default: allow (hook is additive enforcement, not deny-by-default)
log_hook "attention-sweep-enforcer" "Allowed" "tool not gated"
exit 0
```

**Step 2: Make the script executable**

Run:
```bash
chmod +x ~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh
```

Expected: no output (success)

**Step 3: Verify script syntax**

Run:
```bash
bash -n ~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh
```

Expected: no output (clean parse)

---

## Task 3: Register both hooks in Brain's settings.json

**Files:**
- Modify: `~/.ironclaude/brain/.claude/settings.json`

No tests required: JSON config; manually verified by observing hooks fire in Brain session.

**Step 1: Read current settings.json**

Read `~/.ironclaude/brain/.claude/settings.json` to confirm current state.

Expected current content:
```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash $HOME/.ironclaude/brain/hooks/block-push.sh"}]},
      {"matcher": "", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/memory-search-enforcer.sh"}]},
      {"matcher": "", "hooks": [{"type": "command", "command": "bash $HOME/.claude/ironclaude-hooks/wiki-synthesis-enforcer.sh"}]}
    ]
  }
}
```

**Step 2: Write the updated settings.json**

Replace the file content with:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.ironclaude/brain/hooks/block-push.sh"
          }
        ]
      },
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/memory-search-enforcer.sh"
          }
        ]
      },
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/wiki-synthesis-enforcer.sh"
          }
        ]
      },
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/attention-sweep-enforcer.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "mcp__orchestrator__get_worker_status",
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/attention-sweep-arm.sh"
          }
        ]
      }
    ]
  }
}
```

**Step 3: Verify JSON is valid**

Run:
```bash
python3 -m json.tool ~/.ironclaude/brain/.claude/settings.json > /dev/null
```

Expected: no output (valid JSON)
