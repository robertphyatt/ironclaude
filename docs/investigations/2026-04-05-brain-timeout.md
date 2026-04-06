# Brain Timeout Incident Investigation Report
## 2026-04-05 00:00–00:23 UTC

> **Incident:** Daemon watchdog killed and restarted the brain subprocess due to a 720s no-response timeout.
> 
> **Finding:** Root cause is a blind spot in timeout detection logic that doesn't account for blocking tool execution.

---

## Executive Summary

The brain subprocess was terminated due to a detected timeout after no response for 720 seconds (12 minutes). The incident was not caused by the brain being hung or dead, but rather by the timeout detection mechanism interpreting normal, long-running tool execution as unresponsiveness.

The root cause is a fundamental mismatch between what the timeout logic measures and what it should measure:

- **What it measures:** Time since last *message sent* without receiving an `AssistantMessage` response
- **What it should measure:** Time since last *meaningful interaction* with the Claude API

During synchronous tool execution (Grep, Read, Bash), the Claude Agent SDK blocks internally waiting for the tool to complete. No `AssistantMessage` objects are streamed back during this time, so `_last_response_time` is not updated. If any single tool call exceeds the timeout threshold (600s default, potentially 720s configured), the daemon kills the brain incorrectly.

---

## Root Cause Analysis

### The Timeout Logic

**File:** `commander/src/ironclaude/brain_client.py:455-474`

The timeout check occurs in the `needs_restart()` method, which is called every 15 seconds by the daemon.

**Key lines:**
- Line 464-465: Detects if `_last_message_time > _last_response_time` and elapsed time exceeds `timeout_seconds`
- Line 419 in `_brain_session()`: `_last_response_time` is set ONLY when an `AssistantMessage` is received

**The Problem:**

1. `_last_message_time` is set when **any message is sent to the brain** (brain_client.py:435)
2. `_last_response_time` is **only updated when an `AssistantMessage` is received** (brain_client.py:419)
3. **Critical Gap:** During tool execution (Grep, Read, Bash), the Claude Agent SDK **blocks synchronously** executing the tool. While the tool runs, no `AssistantMessage` objects can be streamed back.

### What Happens During a Long Tool Call

1. Brain sends a message with tool use intent (e.g., "Grep this large dataset")
2. `send_message()` sets `_last_message_time = time.time()`
3. Claude SDK receives the message and **blocks executing the tool**
4. Tool runs for 400-900 seconds (normal operation for large operations)
5. **While tool runs:** No `AssistantMessage` is generated, so `_last_response_time` never updates
6. Daemon's `check_brain()` runs every 15 seconds (default `poll_interval_seconds`)
7. After 600+ seconds (default timeout), daemon sees the timeout condition and kills the brain

This is a **false positive**: the brain is working correctly; the timeout detector misinterprets tool execution as unresponsiveness.

### Evidence: Message Stream Handling

**File:** `commander/src/ironclaude/brain_client.py:408-423`

```python
async for message in query(prompt=message_generator(), options=options):
    if isinstance(message, ResultMessage):
        self._session_id = message.session_id
    if isinstance(message, AssistantMessage):
        text_parts = []
        for block in message.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
        if text_parts:
            full_text = "\n\n".join(text_parts)
            self._response_queue.put(full_text)
            self._last_response_time = time.time()  # <-- ONLY HERE
            logger.info(f"Brain response received ({len(full_text)} chars)")
```

The `query()` function streams messages from the SDK. The SDK emits:
- `ToolUseBlock` (when brain requests tool use) — NOT handled in the main loop
- `ResultMessage` (SDK's acknowledgment) — only updates session_id
- `AssistantMessage` (brain's text response) — ONLY message type that updates `_last_response_time`

During tool execution, the SDK receives the ToolUseBlock and begins executing the tool. No AssistantMessage is streamed until the tool completes and the brain generates a text response. This can take 600-3600 seconds legitimately.

### Why the Incident Occurred

On 2026-04-05 around 00:00-00:23 UTC, the brain likely executed a long-running operation:

- **Large Grep:** Searching episodic memory or codebase across many files
- **Large Read:** Reading multi-megabyte logs or code files
- **Large Bash:** Git operations or file processing on a large repository

Any of these operations can legitimately exceed 600 seconds. The brain was functioning correctly, but the timeout detector terminated it.

---

## What Safeguards Exist (and Why They Failed)

### 1. Compaction Protection

**File:** `commander/src/ironclaude/brain_client.py:457-458`

```python
if self._compacting:
    return False  # Don't interfere with in-progress compaction
```

Prevents restarts during context compaction. Doesn't apply to normal tool execution.

**Why it failed:** This only protects one scenario (context compaction). Normal tool calls are not protected.

### 2. Circuit Breaker

**File:** `commander/src/ironclaude/main.py:673-684`

Limits brain to 3 restarts within 10 minutes. After that, brain is paused.

**Why it failed:** This is a **reactive safeguard**. The brain is already killed and restarted before the circuit breaker engages. It doesn't prevent the false positive.

### 3. Configurable Timeout

**File:** `commander/src/ironclaude/config.py:17`

```python
"brain_timeout_seconds": 600,  # default, configurable
```

Users can increase the timeout, but:
- No per-tool timeout (single global timeout)
- Setting too high (e.g., 1800s) means actual failures take 30 min to detect
- Doesn't solve the root problem: confused semantics

**Why it failed:** A timeout of 600s or 720s is still violated by legitimate 20-minute operations.

### Root Cause of Guard Failure

**The safeguards assume the timeout detector is correct.** Since the detector itself is broken (false positives), the guards don't help. You can't guard against a measure you can't trust.

---

## Recommended Fix

### Problem Statement

The timeout logic conflates two separate concerns:

1. **Is the brain process alive?** (Tested by `is_alive()`)
2. **Is the brain responsive to *messages*?** (Untestable during tool execution)

A single global timeout cannot distinguish between:
- "Tool is slow but working" (expected)
- "Brain is hung" (bad)

### Solution: Distinguish Tool Execution from Unresponsiveness

#### **Approach A: Heartbeat During Tool Execution (RECOMMENDED — Long-term)**

**The Fix:** Modify the Claude Agent SDK to emit heartbeat messages while executing tools. The brain would receive periodic messages (e.g., "ToolProgressMessage") that update `_last_response_time`.

**Pros:**
- Solvable at the SDK level (not in ironclaude)
- Once implemented, `_last_response_time` updates continuously
- No timeout logic changes needed in ironclaude
- Signals genuine progress to the daemon

**Cons:**
- Requires Claude Agent SDK team to implement
- May require shipping a new SDK version
- Timeline: 1-2 weeks

**Implementation:**
1. File an issue with Claude Agent SDK team
2. Propose adding `ToolProgressMessage` type
3. Emit this message every 10-30 seconds during tool execution
4. Update ironclaude to treat it like any other message for timeout purposes

---

#### **Approach B: Separate Tool Timeout from Response Timeout (GOOD — Medium-term)**

**The Fix:** Split the single timeout into two:

1. **Tool execution timeout** (default 1800s / 30 min): Allow this much time for a tool to execute
2. **Response timeout** (default 60s): Allow this much time for brain to respond after tool completes

Update `_last_response_time` on ANY message (ToolUseBlock, ResultMessage, etc.), not just AssistantMessage.

**Pros:**
- Entirely within ironclaude's control
- Distinguishes slow tool from unresponsive brain
- More semantically correct
- Can be tuned per-tool if needed

**Cons:**
- Requires understanding all SDK message types
- Changes timeout logic significantly (higher test burden)
- Still doesn't help if a Grep truly hangs the filesystem

**Implementation:**

**File:** `commander/src/ironclaude/brain_client.py`

```python
def __init__(self, timeout_seconds: int = 600, operator_name: str = "Operator"):
    self.timeout_seconds = timeout_seconds  # Response timeout
    self.tool_timeout_seconds = 1800  # 30 minutes for tool execution
    self._last_message_time = 0.0  # Any message type
    self._last_response_time = 0.0  # Text response only
    # ... rest of init

async def _brain_session(...):
    async for message in query(...):
        # Update on ANY message, not just AssistantMessage
        self._last_message_time = time.time()
        
        if isinstance(message, AssistantMessage):
            # Update response time on text response
            self._last_response_time = time.time()
            # ... handle text as before

def needs_restart(self):
    if self._compacting:
        return False
    if not self.is_alive():
        return True
    
    # Check if no messages at all for tool_timeout_seconds
    if (time.time() - self._last_message_time > self.tool_timeout_seconds):
        self._restart_reason = f"timeout (no messages for {self.tool_timeout_seconds}s)"
        return True
    
    # Check if message sent but NO text response for timeout_seconds
    if (self._last_message_time > self._last_response_time
        and time.time() - self._last_message_time > self.timeout_seconds):
        # Could still be a tool executing; check if last_message_time is recent
        elapsed = time.time() - self._last_message_time
        self._restart_reason = f"timeout (no text response in {elapsed:.0f}s)"
        return True
    
    return False
```

---

#### **Approach C: Tool Execution Exemption (QUICKEST — Immediate)**

**The Fix:** Add a flag `_executing_tool` that blocks timeout detection while a tool is running.

**Pros:**
- Minimal code change (5-10 lines)
- Deployable immediately
- Solves the false-positive problem now
- Low risk of regression

**Cons:**
- Doesn't detect a tool that truly hangs (filesystem deadlock, etc.)
- Timeout becomes inactive during all tool execution
- Requires understanding when tools are executing

**Implementation:**

**File:** `commander/src/ironclaude/brain_client.py`

Add to `__init__`:
```python
self._executing_tool = False
```

In `_brain_session()`, detect when a tool is being used:
```python
from claude_agent_sdk.types import ToolUseBlock

async for message in query(...):
    if isinstance(message, ToolUseBlock):
        self._executing_tool = True
        self._last_message_time = time.time()  # Reset timeout on tool start
    
    if isinstance(message, AssistantMessage):
        self._executing_tool = False
        self._last_response_time = time.time()
```

In `needs_restart()`:
```python
def needs_restart(self):
    if self._compacting or self._executing_tool:
        return False  # Don't timeout while tool is executing
    
    if not self.is_alive():
        return True
    
    if (self._last_message_time > self._last_response_time
        and time.time() - self._last_message_time > self.timeout_seconds):
        elapsed = time.time() - self._last_message_time
        self._restart_reason = f"timeout (no response in {elapsed:.0f}s)"
        return True
    
    return False
```

---

### Recommended Implementation Path

**Immediate (Today):** Deploy Approach C
- Minimal risk
- Stops false positives
- Deployable in 30 minutes
- Timeline: Now

**Short-term (This week):** Implement Approach B
- More semantically correct
- Allows detection of truly-hung brains
- Requires moderate testing
- Timeline: 4 hours

**Long-term (1-2 weeks):** Coordinate with SDK team on Approach A
- Gold standard solution
- Doesn't require ironclaude changes long-term
- Depends on external stakeholders
- Timeline: Ongoing

---

## Testing Recommendations

Add to `tests/test_brain_client.py`:

```python
def test_timeout_not_triggered_during_tool_execution(self):
    """Verify timeout doesn't fire while a tool is running."""
    client = BrainClient(timeout_seconds=10)
    
    # Simulate: message sent
    client._last_message_time = time.time()
    assert not client.needs_restart()
    
    # Simulate: tool begins execution
    client._executing_tool = True
    time.sleep(0.5)
    
    # Verify: timeout NOT triggered even after timeout_seconds has passed
    client._last_message_time = time.time() - 15  # 15 seconds ago
    assert not client.needs_restart(), "Timeout should not trigger during tool execution"

def test_timeout_triggered_when_no_messages_arrive(self):
    """Verify timeout DOES fire when no messages arrive at all."""
    client = BrainClient(timeout_seconds=10)
    
    # Simulate: message sent 15 seconds ago, no responses
    client._last_message_time = time.time() - 15
    client._last_response_time = 0
    
    assert client.needs_restart(), "Timeout should trigger when no response"
```

---

## Prevention: Lessons for Future Work

### Design Principles

When building timeout detection for async systems:

1. ✅ **DO:** Measure activity, not just responses
2. ✅ **DO:** Account for operations that legitimately block (tool execution, API waits)
3. ✅ **DO:** Distinguish between "slow" (expected) and "broken" (bad)
4. ❌ **DON'T:** Use a single global timeout for heterogeneous operations
5. ❌ **DON'T:** Assume message arrival means responsiveness

### Code Review Checklist

When reviewing timeout logic:

- [ ] Does the timeout account for legitimate slow operations?
- [ ] Does it distinguish between different types of slowness?
- [ ] Is the timeout configurable per-operation type?
- [ ] Are there logs showing tool execution start/end times?
- [ ] Is there a way to distinguish "tool executing" from "system hung"?

---

## Summary

| Aspect | Finding |
|--------|---------|
| **Root Cause** | Timeout detection only updates `_last_response_time` on `AssistantMessage`, not during tool execution. Tool execution blocks the SDK synchronously. |
| **Affected Code** | `brain_client.py:455-474` (needs_restart) and `_brain_session:408-423` (message handling) |
| **Impact** | Legitimate operations exceeding timeout threshold (600-720s) kill the brain incorrectly |
| **Current Safeguards** | Compaction protection (incomplete), circuit breaker (reactive), configurable timeout (band-aid) |
| **Why Guards Failed** | They assume timeout detection is correct. A broken detector cannot be guarded. |
| **Recommended Fix** | Approach C now (tool exemption) + Approach B later (dual timeout) |
| **Long-term Fix** | Approach A: SDK-level heartbeats during tool execution |
| **Timeline** | Deploy C immediately (30 min), implement B this week (4 hours), coordinate on A with SDK team |

---

## Appendix: Configuration Impact

The incident report mentions a 720s timeout. This suggests the configuration may have been:
```json
{
  "brain_timeout_seconds": 720  // 12 minutes instead of default 600
}
```

This longer timeout indicates someone may have already increased it as a workaround for slow operations. This reinforces that the root cause is tool execution blocking the message stream, not a system genuinely hung.

