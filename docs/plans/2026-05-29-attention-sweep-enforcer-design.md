# Attention Sweep Enforcer Design

> **Created:** 2026-05-29
> **Status:** Design Revised

## Summary

The Brain receives check-in notifications for individual workers and can respond to just that one worker — repeatedly — while other workers sit at prompts waiting for input for 30–90+ minutes. This is the "tunnel vision" problem: the Brain gets locked into a single-worker conversation loop without ever scanning the full worker fleet.

This design adds two hooks to the Brain's Claude Code session that enforce periodic full-fleet sweeps before any gated worker action. Before sending a message, approving a plan, killing, or spawning a worker, the Brain must have successfully received a response from `get_worker_status()` (with no `worker_id`) within the last 3 minutes.

## Architecture

**Split hook design**: Arming and gating are two separate hook events handled by two separate scripts:

- **PostToolUse** (`attention-sweep-arm.sh`): Fires after `get_worker_status` completes. Checks the response — if it is a valid JSON array (successful fleet query), touches the flag. If the response is an error, does not arm.
- **PreToolUse** (`attention-sweep-enforcer.sh`): Fires before any gated worker action. Checks flag existence and freshness. Blocks if missing or expired.

**Why arming must happen PostToolUse, not PreToolUse:**

PreToolUse hooks fire before the tool executes and have no access to the tool result. If arming happened in PreToolUse, the gate would be armed regardless of whether `get_worker_status` succeeds or fails. When the daemon is down or the MCP connection drops, `get_worker_status` returns an error — but a PreToolUse hook would have already touched the flag. The Brain then calls a gated action, the hook sees a fresh flag and passes, and that action also fails (daemon still down). More dangerously, if the daemon briefly recovers, the Brain proceeds with gated actions based on a sweep that never returned valid data.

PostToolUse hooks receive the complete tool response. Arming only after confirming the response is a JSON array (the success shape for `get_worker_status(all)`) ensures the flag represents an actual, validated fleet query — not a failed one. This is the only architectural position that can enforce "the Brain has a current, valid view of the fleet."

**Flag lifecycle:**
```
[get_worker_status(all) called]      → PreToolUse: gating hook passes through (not an arm event)
[get_worker_status response: list]   → PostToolUse: arm hook touches flag
[get_worker_status response: error]  → PostToolUse: arm hook does NOT touch flag
[gated action, flag < 3m old]        → PreToolUse: ALLOW (flag retained)
[gated action, flag > 3m old]        → PreToolUse: rm flag; BLOCK (expired message)
[gated action, no flag]              → PreToolUse: BLOCK (missing message)
```

**TTL: 3 minutes.** The fastest worker check-in cadence is 2 minutes (brainstorming phase). A 3-minute TTL means the worst-case gap between a worker blocking and the sweep forcing Brain attention is approximately 5 minutes — one full brainstorming cycle plus margin. A 5-minute TTL would allow two consecutive brainstorming check-in cycles to be missed before enforcement fires.

## Components

### New file: `~/.claude/ironclaude-hooks/attention-sweep-arm.sh`

PostToolUse hook. Registered with precise matcher `mcp__orchestrator__get_worker_status` (only fires for this one tool).

Logic:
1. Parse `tool_input.worker_id` — if non-empty, this was a single-worker query; exit without arming
2. Parse `tool_response` — check if it is a JSON array using `jq -e 'arrays'`
3. If array: `touch "$SWEEP_FLAG"` → log "sweep gate armed"
4. If not array: log warning "response was error — sweep NOT armed"; exit 0 (don't block)

### New file: `~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh`

PreToolUse hook. Registered with `"matcher": ""` (fires on all tools, filters internally).

Logic:
1. **Parse TOOL_NAME** — fail-closed on jq failure (block with error message; can't route without tool name)
2. **get_worker_status pass-through** — arming is PostToolUse; PreToolUse just passes through
3. **Query pass-through** — `mcp__orchestrator__get_*` tools bypass unconditionally
4. **Gate** — case match on gated tools:
   - No flag → `block_pretooluse` "Attention Sweep required — call get_worker_status() for all workers before acting on one."
   - Flag older than 3 minutes → `rm flag`; `block_pretooluse` "Attention Sweep expired (>3m) — call get_worker_status() for all workers to re-arm."
   - Flag fresh → exit 0 (allow; flag retained)
5. **Default** — exit 0

**Gated tools:**
- `mcp__orchestrator__send_to_worker`
- `mcp__orchestrator__send_keys_to_worker`
- `mcp__orchestrator__approve_plan`
- `mcp__orchestrator__reject_plan`
- `mcp__orchestrator__kill_worker`
- `mcp__orchestrator__spawn_worker`
- `mcp__orchestrator__spawn_workers`

### Modified file: `~/.ironclaude/brain/.claude/settings.json`

Add one PreToolUse entry and one PostToolUse entry:

```json
PreToolUse (matcher: ""):
  bash $HOME/.claude/ironclaude-hooks/attention-sweep-enforcer.sh

PostToolUse (matcher: "mcp__orchestrator__get_worker_status"):
  bash $HOME/.claude/ironclaude-hooks/attention-sweep-arm.sh
```

The PostToolUse entry uses a precise matcher rather than `""` with internal filtering — the response-parsing logic is only relevant for this specific tool.

## Data Flow

```
ARMING PATH:
──────────────────────────────────────────────────────────────
Brain calls get_worker_status(worker_id=None)
  ↓
[PreToolUse] attention-sweep-enforcer.sh
  → tool == get_worker_status → pass through (exit 0)
  ↓
[MCP executes] orchestrator returns list of workers (or error)
  ↓
[PostToolUse] attention-sweep-arm.sh
  → parse worker_id → empty? (full sweep)
      → yes: parse tool_response
          → JSON array? yes → touch flag → "sweep gate armed"
          → JSON array? no  → "response was error, sweep NOT armed" (exit 0)
      → no: single-worker query → pass through, no arm (exit 0)

GATING PATH:
──────────────────────────────────────────────────────────────
Brain calls send_to_worker (or other gated tool)
  ↓
[PreToolUse] attention-sweep-enforcer.sh
  → TOOL_NAME parse fails? → block_pretooluse "Hook parse error..."
  → tool in gated set?
      → yes: flag exists?
          → no:  block_pretooluse "Attention Sweep required..."
          → yes: find -mmin +3 returns flag?
              → yes: rm flag; block_pretooluse "Attention Sweep expired..."
              → no:  exit 0 (allow, flag retained)
  ↓
[MCP executes]
```

## Error Handling

| Failure | Behavior | Rationale |
|---------|----------|-----------|
| `TOOL_NAME` jq parse failure | `block_pretooluse` with error message | Fail-closed — can't route without tool name; surface the bug immediately |
| `WORKER_ID` jq parse failure | treat as single-worker (don't arm) | Fail-open — ambiguous input → conservative: don't grant sweep authority |
| `tool_response` not a JSON array | PostToolUse: don't arm, log warning | Correct behavior: only arm on confirmed successful fleet query |
| `find` failure on TTL check | `\|\| true`; STALE empty → treated as fresh | Fail-open — prefer allow over spurious blocks on `find` failure |
| `/tmp/ic/` missing | `mkdir -p` before `touch` | Standard across all enforcer hooks |
| `run_hook` at top of each script | `set -euo pipefail` + ERR trap | Standard hook pattern |

## Testing Strategy

Manual verification in a Brain session:

1. **No sweep → block**: Call `send_to_worker` without any prior sweep → expect "Attention Sweep required" block.
2. **Failed sweep does NOT arm**: Call `get_worker_status` when daemon is offline (error response) → call `send_to_worker` → expect "Attention Sweep required" block (flag was not armed).
3. **Successful sweep arms gate**: Call `get_worker_status()` with daemon up → call `send_to_worker` → expect allow.
4. **Single-worker query does NOT arm**: Call `get_worker_status(worker_id="w1")` → call `send_to_worker` → expect "Attention Sweep required" block.
5. **Flag survives multiple actions**: After sweep, call `send_to_worker` × 3 → all three pass (flag retained).
6. **TTL expiry**: After sweep, simulate 3-minute elapsed time via `touch -t` on flag → call `send_to_worker` → expect "Attention Sweep expired" block.
7. **Re-arm after expiry**: After expiry block, call `get_worker_status()` → next gated action passes.
8. **Batch spawn covered**: Call `get_worker_status()`, then `spawn_worker` × 4 within 3 minutes → all four pass.

## Implementation Notes

- TTL hardcoded as `3` (minutes). No config layer — YAGNI.
- `find "$FLAG" -mmin "+3"` for TTL check (cross-platform macOS/Linux).
- Flag retained on allowed actions — the sweep grants a 3-minute window covering multiple sequential actions.
- `spawn_workers` (batch) gated alongside `spawn_worker` — matches `memory-search-enforcer` pattern.
- Batch spawn behavior (one sweep covering spawn 1 through spawn 4) is intentional. Spawning is fast; conditions don't meaningfully change between consecutive spawns of a planned batch. Requiring a re-sweep between each spawn would be disruptive without providing additional safety.
- `TOOL_NAME` parse failure fails closed; `WORKER_ID` parse failure fails open — these have different safety semantics. Without `TOOL_NAME`, no routing decision is possible. Without `WORKER_ID`, the conservative default (don't grant sweep authority) is the safe choice.
