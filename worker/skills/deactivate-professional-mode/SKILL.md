---
name: deactivate-professional-mode
description: Disable workflow discipline
---

# Deactivate Professional Mode

## Purpose

Disable professional mode workflow discipline. Use this when you want Claude to work freely without planning/review enforcement.

**Warning:** This removes safety guardrails. Only use when you explicitly want unrestricted code changes.

**Human-only invocation:** This skill can ONLY be invoked via user prompt (the human typing /deactivate-professional-mode). If Claude detects this skill was triggered programmatically (e.g., via a Skill tool call from another skill or from Claude's own initiative), it must refuse and explain that only humans can deactivate professional mode.

Deactivation transitions from any state ('undecided' or 'on') to 'off'. Once off, all tool restrictions are lifted.

## When to Use

- Quick prototyping or experimentation
- Working on personal projects without review requirements
- Explicitly requested by user

**Do NOT use:**
- By default (professional mode should be the default working state)
- Without explicit user request
- "To make things easier" - discipline is the feature, not a bug

## Process

### Step 1: Human-only guard

**IMPORTANT:** Claude is forbidden by design from deactivating professional mode. This prevents the AI from disabling its own guardrails.

If Claude detects this skill was triggered programmatically (from another skill, from a subagent, or from Claude's own initiative rather than the user typing /deactivate-professional-mode), display:

```
REFUSED: Only humans can deactivate professional mode.

This skill can only be invoked by the user typing /deactivate-professional-mode.
Claude cannot and will not deactivate its own guardrails.
```

Then STOP.

### Step 2: Verify deactivation state

Call `mcp__plugin_ironclaude_state-manager__get_professional_mode` to check if the state-activator hook already set professional mode to 'off'.

### Step 3: Display result

**If state is 'off' (success):**

Display:
```
Professional mode deactivation confirmed.

The state-activator hook detected /deactivate-professional-mode in your message
and set professional_mode='off' and workflow_stage='idle' in the database.
This was a human-initiated action handled by the hook system, not by Claude.

Professional mode is now DISABLED:
✓ Claude can make code changes directly
✓ Git write operations permitted
✓ No workflow enforcement

⚠️  Safety guardrails removed — proceed with caution

To re-enable: /activate-professional-mode
```

**If state is still 'on' or 'undecided' (failure):**

Display:
```
⚠️ Professional mode deactivation FAILED.

The state-activator hook did not successfully update the database.
Use the manual sqlite commands below to deactivate, then restart
Claude Code or start a new conversation.
```

**Always display (on both success and failure paths):**

```
---
Manual sqlite commands:

# Deactivate for current session:
sqlite3 ~/.claude/ironclaude.db "UPDATE sessions SET professional_mode='off', workflow_stage='idle', updated_at=datetime('now') WHERE terminal_session='$CLAUDE_SESSION_ID';"

# Deactivate for ALL sessions:
sqlite3 ~/.claude/ironclaude.db "UPDATE sessions SET professional_mode='off', workflow_stage='idle', updated_at=datetime('now');"
```

### Step 4: STOP

Do not attempt any further actions. Do not try to call `set_professional_mode` with value 'off' — the MCP state machine blocks Claude from setting 'off' by design.

## Key Principles

- **User action required**: Only the user can disable professional mode (by design)
- **Verify, don't assume**: Always check state via MCP after the hook fires
- **Always show sqlite**: Manual commands displayed on every invocation
- **Clear warning**: Make it clear that guardrails will be removed
- **Easy reactivation**: Remind user how to restore professional mode
