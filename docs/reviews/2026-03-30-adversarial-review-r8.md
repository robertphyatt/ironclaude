# Adversarial Security Review R8

> **Date:** 2026-03-30
> **Reviewer:** Claude (Directive #226)
> **Scope:** All files in `commander/src/ironclaude/` and `commander/tests/`
> **Previous Rounds:** R1-R7 (all fixes verified)

## Executive Summary

Tenth security assessment (eighth adversarial review) of the ironclaude codebase. Three new findings identified: 0 HIGH, 0 MEDIUM, 3 LOW. All five R7 fixes are correctly implemented with no regressions or bypasses.

The findings are defense-in-depth gaps rather than directly exploitable vulnerabilities. The most impactful is an incomplete application of the R7 L1 mrkdwn escaping fix — `format_heartbeat` embeds brain-controlled worker descriptions in Slack messages without calling `_escape_mrkdwn()`, allowing the brain to inject Slack formatting and link syntax into heartbeat notifications. The second finding is a missing URL scheme check on redirect targets in the SSRF protection chain — `_resolve_and_validate()` validates IP addresses but does not enforce http/https schemes, though the `requests` library rejects unsupported schemes at the transport layer. The third finding is an unbounded file read in `read_log_tail` that loads entire log files into memory before extracting the last N lines, creating a memory exhaustion vector for long-running worker sessions.

This review also audited all 19 test files for testing theatre. Two testing gaps were identified (see Testing Theatre section).

---

## Findings

### L1: Slack mrkdwn Injection in `format_heartbeat`

**Severity:** LOW
**File:** `commander/src/ironclaude/notifications.py:51`
**Context:** Requires brain to control worker objective text containing mrkdwn characters

#### Description

The R7 L1 fix added `_escape_mrkdwn()` (lines 12-16) and applied it to three notification formatters: `format_worker_spawned` (line 24), `format_worker_completed` (line 29), and `format_worker_failed` (line 39). However, `format_heartbeat` (lines 44-54) was not updated. It embeds the worker `description` field — which stores the brain-controlled objective text — directly into a Slack mrkdwn message without escaping:

```python
def format_heartbeat(workers: list[dict]) -> str:
    # ...
    for w in workers:
        desc = w.get("description") or "no task"
        if len(desc) > 60:
            desc = desc[:60] + "..."
        stage = w.get("workflow_stage") or "unknown"
        lines.append(f'• {w["id"]} — "{desc}" ({stage})')
    return "\n".join(lines)
```

The `description` originates from the worker registry database, populated by `register_worker()` in `orchestrator_mcp.py:875` with the brain-controlled `objective` parameter. The heartbeat message is posted directly to Slack at `main.py:873`:

```python
self.slack.post_message(format_heartbeat(worker_details))
```

The `_escape_mrkdwn()` function escapes `&`, `<`, `>` — preventing `<url|label>` link injection and `<@USER_ID>` user mentions. Without it, the brain can inject these constructs into heartbeat messages.

#### Attack Scenario

1. Brain spawns a worker with objective: `Fix bug <https://evil.com|click for details> cc <@U_ADMIN>`
2. Worker is registered with this objective as its description.
3. Every heartbeat interval (default 900s), `post_heartbeat()` calls `format_heartbeat()`.
4. The description is embedded unescaped in the Slack message.
5. Slack renders: a clickable link to `evil.com` and a mention that pings `U_ADMIN`.

#### Proof of Concept

```python
from ironclaude.notifications import format_heartbeat

workers = [
    {
        "id": "w1",
        "description": "Fix <https://evil.com|see details> <@U_ADMIN>",
        "workflow_stage": "executing",
    }
]
msg = format_heartbeat(workers)
# msg contains unescaped mrkdwn — Slack renders clickable link and user mention
assert "<https://evil.com" in msg  # Not escaped
```

#### Suggested Fix

Apply `_escape_mrkdwn()` to the description before embedding:

```python
from ironclaude.notifications import _escape_mrkdwn

# notifications.py:51 — escape description
desc = _escape_mrkdwn(w.get("description") or "no task")
```

---

### L2: Missing Scheme Validation on Redirect Targets

**Severity:** LOW
**File:** `commander/src/ironclaude/research_mcp.py:82-117`
**Context:** Requires attacker-controlled server to return redirect with non-HTTP scheme

#### Description

The `_resolve_and_validate()` function (lines 82-117) resolves DNS, validates that all resolved IPs are public, and constructs a URL with the resolved IP in place of the hostname. However, it does not check the URL scheme. The initial request URL is validated by `_validate_url()` (line 193 in `web_fetch()`), which checks for `http`/`https` schemes. But redirect targets in `_safe_get()` (line 133) go through `_resolve_and_validate()` without scheme validation:

```python
# _safe_get(), line 133 — redirect hop
if is_redirect:
    resolved_url, hostname = _resolve_and_validate(url)
    # _resolve_and_validate does NOT check url scheme
```

A redirect to a non-HTTP scheme (e.g., `ftp://`, `gopher://`) would pass `_resolve_and_validate()` if the hostname resolves to a public IP. The request would then fail at the `requests.get()` transport layer, which rejects unsupported schemes.

For schemes with no hostname (e.g., `file:///etc/passwd`, `data:text/plain,...`), `parsed.hostname` returns `None`, causing `socket.getaddrinfo(None, port)` to resolve to loopback addresses (127.0.0.1, ::1), which are blocked by the IP validation. So these schemes are effectively blocked by the IP check.

#### Attack Scenario

1. Brain calls `web_fetch("http://safe.example.com/page")`.
2. Initial request passes `_validate_url` (http scheme, public IP).
3. Server responds: `302 Location: ftp://evil.com:21/exfiltrate`.
4. `_resolve_and_validate("ftp://evil.com:21/exfiltrate")` resolves evil.com, validates IP is public — passes.
5. `requests.get("ftp://93.184.216.34:21/exfiltrate")` — requests raises `InvalidSchema` error.
6. Error is caught and returned as `{"error": "..."}` — no data exfiltration occurs.

The attack fails at step 5 because the `requests` library does not support FTP. However, the validation layer should reject non-HTTP schemes explicitly rather than relying on the transport library as the sole defense.

#### Proof of Concept

```python
from unittest.mock import patch, MagicMock
from ironclaude.research_mcp import ResearchTools

tools = ResearchTools()
public_addr = [(2, 1, 6, "", ("93.184.216.34", 80))]
mock_redirect = MagicMock()
mock_redirect.status_code = 302
mock_redirect.headers = {"Location": "ftp://evil.com/exfiltrate"}

with patch("socket.getaddrinfo", return_value=public_addr):
    with patch("ironclaude.research_mcp.requests.get", return_value=mock_redirect) as mock_get:
        result = tools.web_fetch("http://example.com/page")

# _resolve_and_validate does NOT reject ftp:// — requests.get is called
# (it will fail at transport level, but validation should catch it first)
assert mock_get.call_count == 2  # Initial + redirect attempt
```

#### Suggested Fix

Add scheme validation to `_resolve_and_validate()`:

```python
def _resolve_and_validate(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked URL scheme: {parsed.scheme!r}")
    # ... rest of function unchanged
```

---

### L3: Unbounded Log File Read in `read_log_tail`

**Severity:** LOW
**File:** `commander/src/ironclaude/tmux_manager.py:129`
**Context:** Requires a long-running worker session with high output volume

#### Description

The `read_log_tail()` method reads the **entire** log file into memory before extracting the last N lines:

```python
def read_log_tail(self, name: str, lines: int = 20) -> str:
    log_path = self.get_log_path(name)
    try:
        with open(log_path) as f:
            all_lines = f.readlines()  # Loads entire file
        return _strip_ansi("".join(all_lines[-lines:]))
    except FileNotFoundError:
        return f"No log file found for {name}"
```

Log files are created by `tmux pipe-pane` (line 82) which continuously appends all terminal output to the file. The `cleanup_old_logs()` method (line 35) removes files older than 7 days but does not limit the size of active log files. A worker session producing sustained high-volume output (e.g., a build process, large test suite, or verbose logging) could generate a multi-GB log file over hours or days.

Callers that load these logs:
- `_call_grader()` at `orchestrator_mcp.py:262,278` — reads 200 lines, but `f.readlines()` loads the full file first
- `_handle_detail()` at `main.py:838` — reads worker log on `/detail` command
- `_handle_log()` at `main.py:852` — reads worker log on `/log` command

The grader is the highest-risk caller because it polls `read_log_tail` in a tight loop (every 2 seconds for up to 120 seconds) during grading.

#### Attack Scenario

1. Brain spawns a worker with an objective that generates high-volume terminal output.
2. Worker runs for hours, tmux `pipe-pane` appends all output to the log file.
3. Log file grows to 2+ GB.
4. Brain calls `kill_worker()` → grader invokes `_call_grader()`.
5. `_call_grader()` calls `read_log_tail(self._grader_session, lines=200)` — but this is the grader's own log, not the worker's. The grader session log is periodically truncated via `/clear`.
6. More realistically: operator runs `/detail w1` or `/log w1` on the high-output worker.
7. `read_log_tail` loads the entire multi-GB file into memory → OOM or severe memory pressure.

#### Proof of Concept

```python
import os
import tempfile
from ironclaude.tmux_manager import TmuxManager

# Create a large log file simulating high-output worker
with tempfile.TemporaryDirectory() as log_dir:
    tmux = TmuxManager(log_dir=log_dir)
    log_path = os.path.join(log_dir, "ic-w1.log")

    # Write 1GB of log data
    with open(log_path, "w") as f:
        line = "x" * 200 + "\n"
        for _ in range(5_000_000):  # ~1GB
            f.write(line)

    # This loads the entire 1GB file into memory
    result = tmux.read_log_tail("ic-w1", lines=20)
    # Memory usage spikes to ~1GB+ before returning 20 lines
```

#### Suggested Fix

Use `collections.deque` with `maxlen` to read only the last N lines without loading the entire file, or use `seek` from the end of the file:

```python
import collections

def read_log_tail(self, name: str, lines: int = 20) -> str:
    log_path = self.get_log_path(name)
    try:
        with open(log_path) as f:
            tail = collections.deque(f, maxlen=lines)
        return _strip_ansi("".join(tail))
    except FileNotFoundError:
        return f"No log file found for {name}"
```

`collections.deque(f, maxlen=N)` iterates the file line-by-line and retains only the last N lines, using O(N) memory regardless of file size.

---

## Testing Theatre

Two testing gaps identified across the test suite:

### TT1: No Mrkdwn Injection Tests for `format_heartbeat`

**File:** `commander/tests/test_notifications.py`

The `TestMrkdwnInjectionPrevention` class (lines 140-156) tests that `_escape_mrkdwn()` is applied in `format_worker_spawned`, `format_worker_completed`, and `format_worker_failed`. However, there are no corresponding injection tests for `format_heartbeat`. The `TestSystemNotifications` class tests heartbeat formatting with normal descriptions, truncation, and None handling (lines 36-68), but never passes descriptions containing mrkdwn special characters (`<`, `>`, `&`, `*`, `_`). This gives false confidence that heartbeat notifications handle untrusted input safely, when in fact `format_heartbeat` does not call `_escape_mrkdwn()` at all.

### TT2: No Scheme Validation Tests for Redirect Targets

**File:** `commander/tests/test_research_mcp.py`

The `TestWebFetchSSRF` class (lines 118-208) tests that non-HTTP schemes (`ftp://`) are blocked on the initial URL. The `TestWebFetchRedirectValidation` and `TestWebFetchDNSPinnedRedirects` classes test redirect handling for private IPs, DNS pinning, and redirect caps. However, no test sends a redirect `Location` header with a non-HTTP scheme (e.g., `ftp://`, `gopher://`, `file://`). This means the scheme validation gap in `_resolve_and_validate()` for redirect targets has no test coverage — a regression could silently re-open the gap.

---

## R7 Fix Verification

All five R7 fixes verified as correctly implemented with no regressions:

| R7 ID | Fix | Status | Verification |
|-------|-----|--------|-------------|
| M1 | DNS rebinding on redirect hops — `_resolve_and_validate()` for redirect targets | VERIFIED | research_mcp.py:133 — `resolved_url, hostname = _resolve_and_validate(url)` called when `is_redirect=True`, with `Host` header set at line 135 |
| M2 | Grader grade injection — nonce delimiter | VERIFIED | orchestrator_mcp.py:265 — `nonce = secrets.token_hex(8)`, line 270 appends `GRADER_RESPONSE_{nonce}` to prompt, line 276 constructs delimiter, lines 288-292 only search for JSON after delimiter position |
| L1 | Slack mrkdwn injection — `_escape_mrkdwn()` | VERIFIED (partial) | notifications.py:12-16 — `_escape_mrkdwn()` escapes `&`/`<`/`>`, applied in `format_worker_spawned` (line 24), `format_worker_completed` (line 29), `format_worker_failed` (line 39). NOT applied in `format_heartbeat` (line 51) — see Finding L1 |
| L2 | Slack search query injection — date regex | VERIFIED | slack_interface.py:23 — `_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")`, applied at lines 94-97 with `ValueError` on mismatch |
| L3 | /tmp decision file injection — permissions + ownership | VERIFIED | protocol.py:32 — `Path(decisions_dir).mkdir(parents=True, exist_ok=True, mode=0o700)`, lines 53-55 — `file_stat.st_uid != current_uid` check skips foreign-owned files |

---

## Methodology

- **Full source read** of all 15 Python modules in `commander/src/ironclaude/` (~3,100 lines)
- **Full test read** of all 19 test files in `commander/tests/` via subagent (~4,800 lines)
- **R7 fix verification** with line-number precision for all 5 findings
- **Ten attack surface categories** audited: notification system escaping, SSRF redirect chain, resource exhaustion, race conditions, information disclosure, database safety, config manipulation, brain client parsing, protocol integrity, testing theatre
- **Additional deep-dive areas** audited: JSON deserialization trust boundaries, symlink attacks, log injection/tmux log parsing, re-entrancy in polling loops, environment variable trust, URL scheme handling
- **False positive filtering**:
  - `_call_grader` batch mode always falls back to individual grading: logic/performance bug (batch regex matches single objects, not arrays) — NOT a security vulnerability; individual fallback works correctly
  - `format_worker_checkin` unescaped `log_tail` (line 106): NOT exploitable via Slack — check-in messages go to brain via `brain.send_message()` (main.py:825), not to Slack; mrkdwn is not rendered in brain messages
  - `send_keys` without `-l` flag: intentional design — tmux only interprets key names when the entire argument matches (e.g., `"Enter"`); multi-word objectives/messages are sent as literal character sequences
  - Legacy `_handle_spawn_worker` (main.py:633) bypasses grader: dead code path — no current code writes `spawn_worker` decisions to the decisions directory; reachable only via locally-injected decision files, defended by R7 L3 ownership check
  - Operator message forwarding f-string quote mismatch (main.py:302): NOT exploitable — operator is trusted (R6 L1 operator_user_id check in slack_commands.py:48 prevents third-party messages)
  - `_resolve_and_validate` IPv6 URL malformation: functionality bug (IPv6 addresses lack brackets in constructed URL) — fails harmlessly with requests parse error, not a security issue
  - Game tool symlink attack (`game_screenshot` at orchestrator_mcp.py:1329): NOT exploitable — game tools are blocked by brain tool guard (brain_client.py:218); requires process-level access to MCP subprocess stdin
  - Environment variable trust: env vars set at daemon startup via `load_config()` (config.py:49-73), not modifiable at runtime by brain or workers

---

## Risk Matrix

| ID | Severity | Exploitability | Impact | Requires |
|----|----------|----------------|--------|----------|
| L1 | LOW | Easy | Slack formatting manipulation, link/mention injection in heartbeat | Brain-controlled objective text with mrkdwn characters (`<`, `>`, `&`) |
| L2 | LOW | Hard | Defense-in-depth gap in redirect scheme validation | Attacker-controlled server returning non-HTTP redirect; mitigated by requests library rejecting unsupported schemes |
| L3 | LOW | Medium | Memory exhaustion (OOM) on daemon or grader process | Long-running worker with high terminal output volume; operator triggers via `/detail` or `/log` command |
