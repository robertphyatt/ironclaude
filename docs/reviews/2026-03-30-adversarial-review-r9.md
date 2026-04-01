# Adversarial Security Review R9

> **Date:** 2026-03-30
> **Reviewer:** Claude (Directive #228)
> **Scope:** All files in `commander/src/ironclaude/` and `commander/tests/`, worker hooks, plan mode documentation
> **Previous Rounds:** R1-R8 (all fixes verified)

## Executive Summary

Eleventh security assessment (ninth adversarial review) of the ironclaude codebase. Three new findings identified: 1 MEDIUM, 2 LOW. All three R8 fixes are correctly implemented with no regressions or bypasses.

The most impactful finding is a gap in the professional-mode-guard hook's review gate enforcement: the `review_pending` check blocks Edit, Write, Bash, and NotebookEdit but omits MultiEdit, allowing a worker to modify files during code review without being blocked. The hook correctly includes MultiEdit in other enforcement sections (docs/ whitelist, file access whitelist, workflow stage check), making this an inconsistent omission rather than a design decision. The second finding is an incomplete application of the R8 L3 memory-exhaustion fix: `OrchestratorTools.get_worker_log()` has its own fallback log reader that still uses `f.readlines()`, separate from the fixed `TmuxManager.read_log_tail()`. The third finding is a defense-in-depth gap where `ExitPlanMode` is not blocked by the hook, falling through to the "tool not handled" allow path.

This review includes a dedicated plan mode documentation assessment and testing theatre audit.

---

## Findings

### M1: MultiEdit Bypasses `review_pending` Gate

**Severity:** MEDIUM
**File:** `worker/hooks/professional-mode-guard.sh:289`
**Context:** Requires professional mode active, workflow stage `executing`, and `review_pending=1`

#### Description

The `review_pending` gate at lines 286-300 blocks write tools when code review has been submitted but not yet completed. The condition at line 289 checks for Edit, Write, Bash, and NotebookEdit:

```bash
if [ "$REVIEW_PENDING" = "1" ]; then
    if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then
      block_pretooluse "professional-mode-guard" "BLOCKED — CODE REVIEW PENDING
```

However, MultiEdit is not included in this condition. MultiEdit IS included in other enforcement sections of the same hook:

- **Line 111:** `if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]; then` (docs/ whitelist)
- **Line 166:** `if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then` (workflow stage check)
- **Line 220:** `if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]` (allowed_files check)

When `review_pending=1`, MultiEdit enters the outer block at line 166 (since it IS listed there), passes the `workflow=executing` check at line 178, passes the allowed_files check at line 220, but then the `review_pending` check at line 289 does not include it. Execution falls through to line 302:

```bash
log_hook "professional-mode-guard" "Allowed" "access check passed"
exit 0
```

#### Attack Scenario

1. Worker is in `executing` stage working on a task wave.
2. Worker calls `submit_task` via the state manager MCP — `review_pending` is set to `1`.
3. Code review skill is supposed to evaluate the changes before the next task begins.
4. Worker uses MultiEdit instead of Edit to modify files in the current wave's allowed list.
5. MultiEdit passes the allowed_files check but bypasses the review_pending gate.
6. Changes made via MultiEdit are not reviewed before the worker continues.

#### Impact

Bypasses the mandatory code review gate between tasks. The review gate is a key enforcement mechanism — it ensures every task's changes are evaluated before the next task begins. A worker (or a model that has been prompt-injected) could sneak in additional changes after submitting work for review, and these changes would not be caught by the code review skill.

The file access whitelist (line 220) still applies, so MultiEdit can only modify files already in the current wave's allowed list. The bypass is specific to the review gate, not to file scope.

#### Suggested Fix

Add `"$TOOL_NAME" == "MultiEdit"` to the review_pending condition at line 289:

```bash
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then
```

---

### L1: Unbounded `f.readlines()` in `get_worker_log` Fallback

**Severity:** LOW
**File:** `commander/src/ironclaude/orchestrator_mcp.py:1214-1218`
**Context:** Requires `capture_pane` to fail (dead session) with a large worker log file

#### Description

The R8 L3 fix correctly applied `collections.deque` to `TmuxManager.read_log_tail()` at `tmux_manager.py:131`. However, `OrchestratorTools.get_worker_log()` has its own separate fallback log reader that still uses `f.readlines()`:

```python
def get_worker_log(self, worker_id: str, lines: int = 50) -> str:
    session_name = f"ic-{worker_id}"
    try:
        result = self.tmux.capture_pane(session_name, lines=lines)
        self._write_brain_contact(worker_id)
        return result
    except subprocess.CalledProcessError:
        pass
    log_path = self.tmux.get_log_path(session_name)
    try:
        with open(log_path) as f:
            all_lines = f.readlines()  # Loads entire file into memory
        self._write_brain_contact(worker_id)
        return _strip_ansi("".join(all_lines[-lines:]))
    except FileNotFoundError:
        raise ValueError(f"No log file found for worker '{worker_id}'")
```

The primary path (`capture_pane` at line 1208) is safe — it reads directly from the tmux pane. But when `capture_pane` fails (e.g., the session crashed or was killed), the fallback at line 1215 reads the entire log file into memory before extracting the last N lines.

This function is called by the brain via the MCP tool `get_worker_log` (line 1502-1504), and by the daemon via `_handle_log` (main.py:852) and `_handle_detail` (main.py:838). The brain calls it frequently when reviewing worker output.

#### Attack Scenario

1. Brain spawns a worker with an objective that generates high-volume terminal output.
2. Worker runs for hours; tmux `pipe-pane` appends all output to the log file (multi-GB).
3. Worker crashes (OOM, context limit, etc.) — tmux session dies.
4. Brain calls `get_worker_log(worker_id)` to review what happened.
5. `capture_pane` raises `CalledProcessError` (session is dead).
6. Fallback reads the entire multi-GB file into memory → OOM on the MCP subprocess.

#### Suggested Fix

Replace `f.readlines()` with `collections.deque`:

```python
import collections

log_path = self.tmux.get_log_path(session_name)
try:
    with open(log_path) as f:
        tail = collections.deque(f, maxlen=lines)
    self._write_brain_contact(worker_id)
    return _strip_ansi("".join(tail))
except FileNotFoundError:
    raise ValueError(f"No log file found for worker '{worker_id}'")
```

Or delegate to the already-fixed `self.tmux.read_log_tail()`:

```python
except subprocess.CalledProcessError:
    self._write_brain_contact(worker_id)
    result = self.tmux.read_log_tail(session_name, lines=lines)
    if result.startswith("No log file found"):
        raise ValueError(result)
    return result
```

---

### L2: `ExitPlanMode` Not Blocked by Professional Mode Guard

**Severity:** LOW
**File:** `worker/hooks/professional-mode-guard.sh:98-107`
**Context:** Professional mode active (ON)

#### Description

The hook explicitly blocks `EnterPlanMode` at line 98:

```bash
EnterPlanMode)
    block_pretooluse "professional-mode-guard" "BLOCKED — USE BRAINSTORMING INSTEAD
...
```

However, `ExitPlanMode` is not handled anywhere in the hook. It is not matched by the `Read|Grep|Glob` allow case (line 89), the `Skill` allow case (line 94), or the `EnterPlanMode` block case (line 98). It is also not an Edit/Write/MultiEdit/Bash/NotebookEdit tool, so it bypasses all write-tool enforcement (lines 110-303). Execution falls through to line 306-310:

```bash
# Tool not handled by this hook — allow.
log_hook "professional-mode-guard" "Allowed" "tool not handled"
exit 0
```

In Claude Code's built-in plan mode, `ExitPlanMode` signals that the planning phase is complete and execution should begin. Calling it while IronClaude's state machine is in a non-executing stage could confuse the model's internal representation of its workflow state, though all write tools remain gated by the hook regardless.

#### Impact

Defense-in-depth gap. Since `EnterPlanMode` is blocked, calling `ExitPlanMode` is semantically nonsensical — there is no plan mode to exit. However, if a model somehow believes it is in plan mode (e.g., after context compaction loses the hook rejection context), successfully calling `ExitPlanMode` could lead it to believe it has execution authorization when the state machine disagrees. The actual write-tool enforcement prevents any real bypass — the model would generate text claiming to execute but all Edit/Write/Bash calls would still be blocked.

#### Suggested Fix

Block `ExitPlanMode` alongside `EnterPlanMode`:

```bash
EnterPlanMode|ExitPlanMode)
    block_pretooluse "professional-mode-guard" "BLOCKED — USE BRAINSTORMING INSTEAD

EnterPlanMode and ExitPlanMode are disabled when professional mode is active.

Call the Skill tool with:
  skill: \"ironclaude:brainstorming\"

Do NOT use EnterPlanMode or ExitPlanMode. Use the brainstorming skill for all design work."
    ;;
```

---

## Plan Mode Documentation Assessment

### Files Reviewed

1. **CLAUDE.md** (lines 50-64) — "Plan Mode Replacement" section
2. **README.md** (lines 38-42) — "vs. Claude Code Plan Mode" section
3. **worker/hooks/professional-mode-guard.sh** (lines 98-107) — EnterPlanMode blocking logic

### Assessment

**Clarity:** The documentation is clear and accurate. CLAUDE.md's comparison table (lines 56-62) is an effective quick-reference that maps each Claude Code concept to its IronClaude equivalent. The table correctly shows that IronClaude has capabilities with no Claude Code equivalent (code review after every task, file access whitelist per task).

**Completeness:** Both documents explain the core concept: IronClaude's 3-phase workflow replaces the 2-phase built-in plan mode. The key sentence "When professional mode is active, EnterPlanMode is blocked by hooks — this is intentional, not a bug" (CLAUDE.md:52) preempts confusion.

**Hook Error Message:** The block message at lines 99-106 is clear and actionable:
```
BLOCKED — USE BRAINSTORMING INSTEAD

EnterPlanMode is disabled when professional mode is active.

Call the Skill tool with:
  skill: "ironclaude:brainstorming"

Do NOT use EnterPlanMode. Use the brainstorming skill for all design work.
```

The message provides the exact tool call the model should use instead. The final "Do NOT" line reinforces the block.

**Gap: ExitPlanMode not addressed.** Neither CLAUDE.md, README.md, nor the hook error message mention `ExitPlanMode`. The documentation only discusses `EnterPlanMode` blocking. A model encountering `ExitPlanMode` in its tool list would have no guidance that this tool is also not part of the IronClaude workflow. See Finding L2 above.

**Edge Cases:**

- **Model sees both toolsets:** A model with both the built-in plan mode tools and IronClaude skills could be confused about which to use. The CLAUDE.md line "Work WITH this system, not against it" addresses this, but only if the model reads the full section.
- **Compaction context loss:** If the CLAUDE.md context is lost during compaction, the model might revert to using built-in plan mode tools. The hook enforcement catches `EnterPlanMode` in this case, and the error message re-educates the model.
- **Worker vs. Brain:** The plan mode replacement applies to workers (where professional mode is active), not to the brain. This distinction is implicit but not explicitly stated in the documentation.

**README accuracy:** The README (line 42) accurately states "Key additions over plan mode: review gates between every task, file access restrictions per task wave, MCP-backed state persistence across sessions, structured plan format (JSON + markdown), and wave-based parallel execution with dependency graphs." These are all verifiable in the codebase.

---

## Testing Theatre

### TT1: Tautological MCP Server Inclusion Test

**File:** `commander/tests/test_brain_client.py:822-832`
**Class:** `TestMCPDiscovery`

The test `test_brain_session_includes_research_mcp_in_mcp_servers` claims to verify that the `mcp_servers` dict in `_brain_session` includes a 'research' key:

```python
def test_brain_session_includes_research_mcp_in_mcp_servers(self):
    """The mcp_servers dict in _brain_session includes a 'research' key."""
    client = BrainClient()
    client._research_mcp_path = "/fake/research_mcp.py"
    client._ollama_mcp_path = "/fake/ollama_mcp.py"
    client._episodic_memory_path = "/fake/memory.js"
    # We can't easily inspect the mcp_servers dict without running
    # _brain_session, so verify the paths are set and will be used
    assert client._research_mcp_path is not None
    assert client._ollama_mcp_path is not None
```

The test sets `_research_mcp_path` to `/fake/research_mcp.py` on line 825, then asserts `_research_mcp_path is not None` on line 830. This assertion can never fail — it verifies a value set three lines earlier. The test does not call `_brain_session()`, does not inspect the `mcp_servers` dict, and cannot detect a regression where `_brain_session` fails to include the research MCP in its server configuration. The comment on line 828 acknowledges this limitation but the test provides false confidence regardless.

### TT2: No Test for `get_worker_log` Unbounded Read

**File:** `commander/tests/test_orchestrator_mcp.py:169-179`

The `test_get_worker_log_reads_file` test exercises the fallback path of `get_worker_log()` with a 5-line file. The test verifies that the correct lines are returned and ANSI codes are stripped, but does not test memory behavior with large files. More critically, there is no test that verifies the fallback uses bounded memory — a test with a large file would fail BEFORE a fix and pass AFTER, making it a valid regression test. The R8 L3 fix added `collections.deque` to `TmuxManager.read_log_tail()`, and corresponding tests exist, but the separate `get_worker_log` fallback reader has no equivalent coverage.

---

## R8 Fix Verification

All three R8 fixes verified as correctly implemented with no regressions:

| R8 ID | Fix | Status | Verification |
|-------|-----|--------|-------------|
| L1 | Slack mrkdwn injection in `format_heartbeat` — apply `_escape_mrkdwn()` | VERIFIED | notifications.py:49 — `desc = _escape_mrkdwn(w.get("description") or "no task")` applied BEFORE the truncation at line 50. Tests at test_notifications.py:157-179 cover `<script>`, `&`, `<url\|label>`, and `<@USER>` injection in heartbeat descriptions |
| L2 | Missing scheme validation on redirect targets — add to `_resolve_and_validate` | VERIFIED | research_mcp.py:91-92 — `if parsed.scheme not in ('http', 'https'): raise ValueError(...)` at the top of `_resolve_and_validate()`. Tests at test_research_mcp.py:359-427 cover ftp, gopher, and file schemes on redirect targets with RED signal (`mock_get.call_count == 1`) |
| L3 | Unbounded log file read in `read_log_tail` — use `collections.deque` | VERIFIED | tmux_manager.py:131 — `tail = collections.deque(f, maxlen=lines)` replaces `f.readlines()`. `collections` import added at line 6. NOTE: The separate fallback reader in `orchestrator_mcp.py:1215` was NOT fixed — see Finding L1 above |

---

## False Positive Analysis

- **`format_plan_ready`, `format_blocked`, `format_task_progress` missing mrkdwn escaping:** These formatters accept brain-controlled strings and embed them in Slack messages without `_escape_mrkdwn()`. However, all three are imported in `main.py:33` but NEVER called anywhere in the codebase. They are dead code — latent injection vectors that would activate only if connected to a code path. Noted for awareness but not a finding.
- **`format_worker_checkin` unescaped `log_tail`:** Confirmed still NOT a Slack mrkdwn issue — `format_worker_checkin` output goes to `brain.send_message()` at main.py:825, not to Slack. Consistent with R8 false positive analysis.
- **Agent tool allowed through hook:** The Agent tool falls through to "tool not handled" allow at line 310. However, subagents spawned via Agent inherit the same hook enforcement — all tool calls from subagents go through the same PreToolUse hooks. No bypass.
- **`ExitPlanMode` as execution enabler:** While `ExitPlanMode` is allowed (Finding L2), all actual write operations (Edit, Write, MultiEdit, Bash, NotebookEdit) remain gated by the workflow stage check at line 178. The model cannot perform mutations by calling `ExitPlanMode` alone.
- **SQL injection in hook shell scripts:** The hook escapes session tags via `sed "s/'/''/g"` before embedding in single-quoted SQL literals. This is correct SQLite escaping. The `WAVE_NUM` variable receives the same treatment at line 223.

---

## Methodology

- **Full source read** of all 15 Python modules in `commander/src/ironclaude/` (~4,500 lines)
- **Full test read** of all 19 test files in `commander/tests/` (~4,800 lines)
- **Full hook read** of `worker/hooks/professional-mode-guard.sh` (311 lines) with control flow analysis for every tool name path
- **Documentation review** of CLAUDE.md, README.md, and hook error messages for plan mode replacement
- **R8 fix verification** with line-number precision for all 3 findings
- **Nine attack surface categories** audited: hook enforcement bypass (MultiEdit, ExitPlanMode, Agent), resource exhaustion (unbounded reads), mrkdwn injection, SQL injection in shell, SSRF chain, dead code analysis, race conditions, trust boundary verification, testing theatre
- **Control flow trace** through professional-mode-guard.sh for: MultiEdit, ExitPlanMode, Agent, Bash (non-executing), Edit (review_pending), confirming which paths block vs. allow

---

## Risk Matrix

| ID | Severity | Exploitability | Impact | Requires |
|----|----------|----------------|--------|----------|
| M1 | MEDIUM | Easy | Bypasses code review gate — worker can modify allowed files during review_pending | Professional mode active, executing stage, review_pending=1, model uses MultiEdit instead of Edit |
| L1 | LOW | Medium | Memory exhaustion (OOM) on MCP subprocess | Worker session dead (capture_pane fails) + large log file; brain or operator triggers via get_worker_log |
| L2 | LOW | Hard | Model state confusion — believes it exited plan mode | Professional mode active; mitigated by write-tool enforcement remaining active regardless |
