# Grader Transport Overhaul: `claude -p` Headless Subprocess Design

> **Created:** 2026-07-11
> **Status:** Design Complete
> **Scope mode:** selective (core: transport replacement; shadow/prompt-trim deferred)

## Summary

The inline Opus grader (`_call_grader` in `commander/src/ironclaude/orchestrator_mcp.py`)
is the single most-patched, least-reliable subsystem in IronClaude (~15 prior grader
directives). It drives a **persistent tmux Claude Code session** and reads the verdict by
**scraping the tmux pane** for a nonce delimiter + a strict single-line JSON regex. Two
systematic-debugging passes (evidence in `/tmp/ic/daemon.log` and
`/tmp/ic-logs/ic-grader.log`) confirmed two defects:

1. **False timeouts / wrong grades.** On a large grading prompt (~517k tokens / 15 pasted
   blocks) the nonce delimiter is collapsed/scrolled out of the 500-line pane capture
   window, so a **valid verdict the grader produced in ~38s is never parsed** → the poll
   loop runs the full `GRADER_TIMEOUT_SECONDS` and returns `F "Grader timed out after 600s"`.
   Proven: a real `{"grade":"D",…}` was found in the grader log while Slack reported F/timeout.

2. **The brain freezes.** `_call_grader` holds `_grader_lock` and blocks **synchronously**
   for up to `GRADER_TIMEOUT_SECONDS` (600s) **× a retry ≈ 1200s (20 min)** per grade.
   `kill_worker` / spawn / `approve_plan` / directive grading all call it synchronously, so
   the daemon/brain hangs (observed 11:15 freeze on 2026-07-11). Every prior "fix" *raised
   the timeout* (120→180→300→600) — which makes the freeze **longer**, not shorter.

This design **replaces the transport**: run one `claude -p` (headless "print" mode)
subprocess per grade, force **schema-validated JSON output**, and enforce a **hard
subprocess timeout**. This deletes the entire fragile stack (persistent session, nonce,
pane-scraping, ANSI handling, order-sensitive regex, zombie/respawn, PM-deactivation) and
fixes **both** defects at once: grades become reliable and fast (~40s), and a hung grade is
killed deterministically at ~120s instead of hanging 20 minutes. Claude Max billing is
preserved; the grader is deliberately **tool-starved** (evaluates only from inline evidence —
see the Shipped-decision note under Architecture).

## Architecture

Replace only the **internals** of `_call_grader(system_prompt, user_prompt, batch)`. Its
signature and return contract (`dict | list`) are unchanged, so **every caller and the
shadow-grader path are untouched.**

Per grade, run (conceptually):

```
claude -p \
  --system-prompt-file <avatar-skill temp file> \
  --output-format json \
  --json-schema <verdict schema> \
  --model '<grader_model>[1m]' \
  --dangerously-skip-permissions \
  --strict-mcp-config \                       # drop plugin MCP servers (no mcp__* mutators)
  --disallowedTools "Task,Bash,Read,Edit,Write,NotebookEdit,Grep,Glob,WebFetch,WebSearch,\
                     Skill,Workflow,ToolSearch,SendMessage,EnterWorktree,ExitWorktree,\
                     CronCreate,CronDelete,CronList,ScheduleWakeup,RemoteTrigger"
```

> **Shipped decision (differs from the first draft of this design):** the grader is
> **tool-starved**, NOT given read-only tools. An early draft proposed
> `--allowedTools "Read,Bash,Grep,Glob"` so the grader could investigate evidence itself.
> That was rejected because (a) the grader runs inside professional mode and any Read/Bash
> call would trip the PM-guard hooks, and (b) the grading prompt embeds worker-controlled
> content (the kill_worker log tail), so a tool-capable, permission-skipped grader is a
> prompt-injection surface. The grader therefore evaluates ONLY from the inline evidence in
> its prompt and makes exactly one tool call — `StructuredOutput` — to return the verdict.
> There is also **no retry** (a hung grade is a single hard `subprocess.run` kill, not
> retried): the old transport's retry was part of the ~1200s freeze this rewrite removes,
> and a failed grade is fail-closed (returns F; the brain can re-invoke).

- **Prompt over stdin** (`input=user_prompt`) — the grading prompt can be ~500k tokens and
  cannot go on argv. The system prompt (avatar skill) goes via `--system-prompt-file`.
- **`subprocess.run(..., timeout=GRADER_TIMEOUT_SECONDS)`** — the caller enforces the
  timeout by killing the subprocess (Claude Code `-p` has no built-in timeout flag; this is
  the sanctioned pattern). Deterministic, no poll loop.
- **`--output-format json --json-schema`** → the response envelope contains a
  `structured_output` field holding the grader's schema-validated verdict
  `{grade, approved, feedback, recommended_model?}`. No nonce, no delimiter, no regex.
- **`cwd=grader_home`** (`~/.ironclaude/grader`) with trust pre-injected (as today) +
  `--dangerously-skip-permissions` so no permission/trust prompt blocks a non-interactive run.
- **Billing guard:** strip `ANTHROPIC_API_KEY` (and `ANTHROPIC_AUTH_TOKEN`) from the
  subprocess env so a stray key cannot silently switch from the Claude Max OAuth
  subscription to metered API billing (verified precedence: subscription OAuth is used
  unless an API key/token env var is present).

**Deleted** (all tmux-transport machinery): `_ensure_grader`, `_spawn_grader`,
`_is_grader_alive`, `_do_grader_send_and_poll`, `_wait_for_grader_clear`, the
`GRADER_RESPONSE_{nonce}` scheme, `_grader_session`, zombie detection, per-spawn log
truncation, and the `_deactivate_pm_via_sqlite` call on the grader session.

## Components

`commander/src/ironclaude/orchestrator_mcp.py`:

- **`_call_grader(system_prompt, user_prompt, batch=False)`** — rewritten internals:
  1. Write `system_prompt` to a temp file (avatar skill).
  2. Build the verdict JSON-schema: single object for normal mode; a JSON **array** of
     verdict objects for `batch=True`.
  3. `subprocess.run([...], input=user_prompt, cwd=grader_home, env=<key-stripped>,
     capture_output=True, text=True, timeout=self.GRADER_TIMEOUT_SECONDS)`.
  4. Parse the envelope JSON; return `structured_output` — mapped to the existing
     `{grade, approved, feedback}` dict, or the `verdicts` list unwrapped from the
     object-wrapped batch schema (`{"verdicts": [...]}`).
  5. On `TimeoutExpired` / nonzero exit / missing `structured_output` → return
     `{"grade":"F","approved":False,"feedback":"<reason>"}` (or a length-1 F list for batch).
     No retry — a single hard-kill attempt (fail-closed; the brain can re-invoke).
- **`GRADER_TIMEOUT_SECONDS`**: 600 → **120** (typical grade ~40s; 120 = safety headroom;
  now a hard kill, not a false poll).
- **`_grader_lock`**: retained as a simple serialize/throttle. Its original purpose
  (preventing interleaving in the shared tmux log) is **obsolete** with per-process
  isolation; it now only caps concurrent Opus subprocesses. (Could be relaxed to a small
  semaphore later; keep as-is for a minimal diff.)
- **Grader command builder** — a small helper mirroring today's `make_opus_command` idea but
  for `-p` (model, effort via `CLAUDE_CODE_EFFORT_LEVEL` env, flags).

`commander/tests/test_orchestrator_mcp.py`: the grader tests that mock `tmux.send_keys` /
`capture_pane` are rewritten to mock `subprocess.run`.

Unchanged: all `_call_grader` callers (kill_worker, spawn, approve_plan, directive grading),
the shadow grader (`_shadow_grader` / `_fire_shadow_thread` / `_run_shadow_and_report`), and
`_parse_tool_calls_from_delta` / concordance logic (the shadow report's "Opus tool calls"
signal will now come from the `-p` JSON envelope's message list instead of `_last_grader_delta`
— see Implementation Notes).

## Data Flow

```
caller → _call_grader(system_prompt, user_prompt[, batch])
  write system_prompt → /tmp/…-grader-sysprompt.txt
  cmd = [claude, -p, --system-prompt-file …, --output-format json,
         --json-schema <object schema; batch wraps the array as {"verdicts":[...]}>,
         --model <m>[1m], --dangerously-skip-permissions, --strict-mcp-config,
         --disallowedTools "<all file/exec + agentic tools>"]
  proc = subprocess.run(cmd, input=user_prompt, cwd=grader_home,
                        env=strip_api_keys(os.environ), capture_output=True,
                        text=True, timeout=120)
  envelope = json.loads(proc.stdout)
  verdict  = envelope["structured_output"]     # schema-validated
  return {grade, approved, feedback}           # (or list for batch)
  ── on TimeoutExpired / nonzero / parse-fail → F(reason) ──
```

Worst case ≈ 120s (was ≈ 1200s); typical ≈ 40s → the synchronous call no longer freezes the
brain.

## Error Handling

- **`subprocess.TimeoutExpired`** → subprocess killed by `run()`; return F "grader timed out
  (120s)". Now rare and *real* (not a false poll-timeout).
- **Nonzero exit / empty stdout / missing `structured_output`** → F with `stderr` detail.
- **Malformed envelope JSON** → F with the raw head for debugging.
- **Retry:** none — a hung/failed grade is a single hard-kill attempt returning F
  (fail-closed; the brain can re-invoke). The old transport's retry was part of the
  ~1200s freeze this rewrite removes.
- **Billing safety:** if an `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` is present in the
  daemon env, it is **removed from the subprocess env** so grading always uses the Max
  subscription. (Optionally log a warning if one was present.)

## Testing Strategy

- **Unit (mock `subprocess.run`):**
  - Envelope with `structured_output={"grade":"D","approved":false,"feedback":"…"}` →
    `_call_grader` returns that verdict.
  - `subprocess.TimeoutExpired` raised → returns F, bounded (no 600s).
  - Nonzero exit / missing `structured_output` → returns F with detail.
  - `batch=True`: envelope with an array → returns the list.
  - Env guard: `ANTHROPIC_API_KEY` set in parent → assert it is absent from the `env=` passed
    to `subprocess.run`.
- **Rewrite** the existing tmux-mocking grader tests in `test_orchestrator_mcp.py`.
- **Smoke (manual/integration):** one real `claude -p` grade against a small fixture to
  confirm flags, schema output, and Max billing (no API key).
- **No regression** in callers: they consume the same `{grade, approved, feedback}` dict.

## Implementation Notes

- **Verified `claude -p` capabilities** (via claude-code-guide): one-shot headless query to
  stdout; `--output-format json` envelope with `result`/`session_id`/cost + `structured_output`
  when `--json-schema` is given; `--system-prompt-file` / `--append-system-prompt`; full tool
  use in headless mode gated by `--allowedTools` / `--dangerously-skip-permissions`;
  `--model` pinning incl. `[1m]`; **subscription-OAuth billing unless an API-key env var is
  set**; no hard-timeout flag (wrap in `subprocess` timeout); concurrent `-p` processes are
  safe. Confirm exact flag spellings against the installed `claude` version during planning
  (`claude -p --help`), since CLI surface evolves.
- **Tool-calls signal for the shadow report:** today `opus_tool_calls` come from
  `_parse_tool_calls_from_delta(self._last_grader_delta)`. With `-p`, extract tool calls from
  the JSON envelope's message array (or accept "no tool calls" as informational). Keep this a
  small, contained change; do not let it expand scope.
- **Prompt size:** passing the prompt via **stdin** avoids argv limits. This design does NOT
  trim the ~517k-token prompt (out of scope), but per-grade fresh context prevents the
  session-bloat/accumulation that was killing the persistent `[1m]` session.
- **Cost/latency tradeoff:** a fresh `-p` process re-sends the (large) avatar system prompt
  each grade instead of amortizing it across a persistent session. Prompt caching mitigates
  this; and the persistent session's "amortization" was itself the source of the bloat/death.
  Acceptable and intended.
- **Out of scope (deferred, per selective scope):** Ollama-shadow fail-fast (600s read
  timeout against `100.70.214.61`), the ~517k-token prompt trim, and the duplicate log
  handler (identical-ms doubled log lines = one process, two handlers). Track separately.
- **Deploy:** daemon code → **restart the daemon** after this ships (no hook/skill/plugin
  change). The change is confined to `orchestrator_mcp.py` + its tests.
