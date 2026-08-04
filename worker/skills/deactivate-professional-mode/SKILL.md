---
name: deactivate-professional-mode
description: Disable workflow discipline
---

# Deactivate Professional Mode

## Purpose

Disable professional-mode workflow discipline after an explicit human request.

**Warning:** This removes safety guardrails. Only use when the user explicitly
wants unrestricted code changes.

## Provider-native state-manager calls

- Claude Code:
  `mcp__plugin_ironclaude_state-manager__get_professional_mode`.
- Codex: Codex `state-manager` `get_professional_mode`.

Use only the active client's provider-native `get_professional_mode`. Never
substitute a database read or infer state from a failed tool call.

## Human-only invocation

This skill can only proceed when the human directly entered one of these
invocations:

- `/deactivate-professional-mode`
- `/ironclaude:deactivate-professional-mode`
- `$ironclaude:deactivate-professional-mode` in Codex

The UserPromptSubmit hook owns the state transition. The agent only verifies
and reports the result.

## Process

### Step 1: Enforce the human-only guard

If this skill was triggered programmatically, by a subagent, by another skill,
or on the agent's own initiative, display:

```text
REFUSED: Only humans can deactivate professional mode.

Use a direct human invocation:
/deactivate-professional-mode
/ironclaude:deactivate-professional-mode
$ironclaude:deactivate-professional-mode (Codex)

The agent cannot and will not deactivate its own guardrails.
```

Then STOP.

### Step 2: Verify deactivation state

Call the active client's provider-native `get_professional_mode` once.

A trustworthy result contains:

- `professional_mode` equal to exactly `off`, `on`, or `undecided`
- a non-empty `client`
- a non-empty `session_id`

A tool error, rejected request, missing field, malformed payload, or unknown
state is not a trustworthy result.

### Step 3: Display exactly one outcome

**If state is 'off' (confirmed success):**

Display:

```text
Professional mode deactivation confirmed.

The human-initiated hook set professional_mode='off' for the verified session.
The agent did not deactivate its own guardrails.

IronClaude professional-mode workflow enforcement is now DISABLED for the
verified session.

Other user, project, sandbox, and approval controls remain in force.

To re-enable: /activate-professional-mode
```

Do not claim a workflow-stage value: active tasks cause the hook to preserve
the existing workflow stage.

**If state is 'on' or 'undecided' (confirmed failure):**

Display:

```text
⚠️ Professional mode deactivation FAILED.

The state-manager returned professional_mode='<exact state>' for the verified
session, so the requested deactivation was not confirmed.
```

**If verification is unavailable or untrustworthy (status unknown):**

Display:

```text
⚠️ PROFESSIONAL MODE DEACTIVATION STATUS UNKNOWN.

The state check did not return a trustworthy professional-mode result.
Exact verification result: <exact tool error or bounded exact malformed result>

This does not prove that the hook failed. Do not report deactivation as failed,
and do not claim that professional mode is enabled or disabled.
```

`Missing or invalid Codex thread_source` is a verification identity error. It
means the state-manager could not bind the request to a trusted session; it is
not evidence that the hook's earlier database update failed.

Do not provide database mutation commands solely because verification was
unavailable.

### Step 4: STOP

Do not attempt further actions. Do not try to call `set_professional_mode` with value 'off' — the MCP state machine blocks agent self-deactivation by design.

## Key Principles

- **Human action required**: only the human can request deactivation
- **Provider-native verification**: use the active client's state-manager tool
- **Three outcomes**: confirmed off, confirmed not-off, or status unknown
- **No false failure**: verification errors never prove transition failure
- **No identity bypass**: never weaken or work around state-manager binding
- **Easy reactivation**: remind the user how to restore professional mode
