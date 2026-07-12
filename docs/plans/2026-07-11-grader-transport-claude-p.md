# Grader Transport → `claude -p` (tool-free) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Replace the inline Opus grader's fragile tmux-pane-scraping transport with a per-grade, **tool-free** `claude -p` headless subprocess using schema-validated JSON output and a hard subprocess timeout — fixing both the false-F grades and the ~20-minute daemon/brain freeze, while preserving Claude Max billing.

**Architecture:** `_call_grader(system_prompt, user_prompt, batch)` keeps its signature and return contract; only its internals change — it now runs `claude -p --output-format json --json-schema <verdict>` as a subprocess (prompt via stdin, system prompt via `--system-prompt-file`, `--model <grader>[1m]`, tool-free), wrapped in `subprocess.run(timeout=…)` so a hung grade is killed deterministically. All the persistent-tmux machinery is deleted. Callers and the shadow grader are untouched.

**Tech Stack:** Python, `subprocess`, Claude Code CLI (`claude -p`), pytest.

**Design doc:** docs/plans/2026-07-11-grader-transport-claude-p-design.md

**Key decisions (from brainstorming, some refining the design doc):**
- **Tool-free grader** (operator decision): the grader gets NO Read/Bash tools and evaluates from the inline evidence callers already pass (objective + evidence + v1.0.19 capped log excerpt). Zero tool calls ⇒ the professional-mode-guard hook (active in `grader_home`, fresh `undecided` session) has nothing to block. This supersedes the design doc's `--allowedTools "Read,Bash,…"`.
  - **Consequence (must be handled — tier-up review C1):** THREE grader system prompts currently instruct tool use and rely on the grader fetching diffs/test output with tools (they are NOT inlined): `orchestrator_mcp.py:2219` (spawn), `:2907` (approve_plan), `:3523` (kill_worker). Going tool-free REQUIRES editing all three to remove the "use Read and Bash to investigate…" instruction and tell the grader to evaluate ONLY from the evidence in the prompt. The operator accepted the resulting **reduced rigor** (the grader no longer independently reads a diff to "verify, don't trust"; it trusts the inline objective/evidence/log). We do NOT inline the diffs (out of scope for this fix).
  - **Consequence (document — tier-up review M7):** with a tool-free grader, the shadow-concordance Slack report's "Opus tool calls" column is now permanently empty (correct — the grader makes no tool calls). Note this in the CHANGELOG.
- Hard timeout via `subprocess.run(timeout=…)`; `GRADER_TIMEOUT_SECONDS` 600 → **120**.
- Preserve Claude Max billing: **strip `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`** from the subprocess env.
- Verify exact CLI flag surface (`claude -p --help`) during execution — flags like `--json-schema`, `--system-prompt-file`, the tool-disable flag, and the JSON envelope field holding the schema output (`structured_output`) must be confirmed against the installed `claude` version before finalizing the code.

---

## Task 1: Rewrite `_call_grader` to a tool-free `claude -p` subprocess (atomic refactor)

This is one atomic refactor: rewrite `_call_grader`, delete the tmux transport functions, drop the timeout, and replace the transport tests in the same commit so the suite is green at the end. (It cannot be split — deleting the transport functions breaks their tests, so the test surgery must land with the impl.)

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Delete: `commander/tests/test_orchestrator_debug_log.py` (obsolete — tests the deleted `_do_grader_send_and_poll`)

**Step 0: Confirm the CLI surface (RESEARCH — HARD GATE, do this first).**
Run `claude -p --help` (read-only) and confirm the real names of: `--output-format json`, `--json-schema <json>` (and the envelope field that carries the validated object — expected `structured_output`), `--system-prompt-file <path>`, `--model`, `--dangerously-skip-permissions`, and the flag that yields a **tool-free** run (e.g. `--allowedTools ""` or equivalent). This is a HARD GATE: the unit tests mock `subprocess.run`, so a wrong flag/field name is NOT caught by them — only the **required smoke test in Step 6b** catches it. Reflect the confirmed names in the code; do NOT guess.

**Step 1: Write the new `_call_grader` tests (RED).** In `test_orchestrator_mcp.py`, add a new test class `TestCallGraderSubprocess` that mocks `subprocess.run`:
```python
class TestCallGraderSubprocess:
    def _envelope(self, verdict):
        import json
        return json.dumps({"structured_output": verdict, "result": "", "session_id": "x"})

    def test_returns_verdict_from_structured_output(self, tools):
        verdict = {"grade": "D", "approved": False, "feedback": "no"}
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=self._envelope(verdict), stderr="")
            r = tools._call_grader("sys", "usr")
        assert r["grade"] == "D" and r["approved"] is False and r["feedback"] == "no"

    def test_timeout_returns_f_bounded(self, tools):
        import subprocess as sp
        with patch("ironclaude.orchestrator_mcp.subprocess.run", side_effect=sp.TimeoutExpired("claude", 120)):
            r = tools._call_grader("sys", "usr")
        assert r["grade"] == "F" and r["approved"] is False

    def test_nonzero_exit_returns_f(self, tools):
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            r = tools._call_grader("sys", "usr")
        assert r["grade"] == "F"

    def test_missing_structured_output_returns_f(self, tools):
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout='{"result":"hi"}', stderr="")
            r = tools._call_grader("sys", "usr")
        assert r["grade"] == "F"

    def test_batch_returns_list(self, tools):
        arr = [{"grade": "A", "approved": True, "feedback": "ok"}]
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=self._envelope(arr), stderr="")
            r = tools._call_grader("sys", "usr", batch=True)
        assert isinstance(r, list) and r[0]["grade"] == "A"

    def test_strips_api_key_from_subprocess_env(self, tools, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-removed")
        verdict = {"grade": "A", "approved": True, "feedback": "ok"}
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=self._envelope(verdict), stderr="")
            tools._call_grader("sys", "usr")
        env = m.call_args.kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in env

    def test_prompt_passed_via_stdin_and_model_pinned(self, tools):
        verdict = {"grade": "A", "approved": True, "feedback": "ok"}
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=self._envelope(verdict), stderr="")
            tools._call_grader("SYSTEM", "USERPROMPT")
        assert m.call_args.kwargs.get("input") == "USERPROMPT"
        argv = m.call_args.args[0]
        assert argv[0].endswith("claude") and "-p" in argv
        assert any(tools._grader_model in a for a in argv)  # model pinned

    def test_sets_effort_level_env(self, tools):  # I6 — preserves effort coverage
        verdict = {"grade": "A", "approved": True, "feedback": "ok"}
        with patch("ironclaude.orchestrator_mcp.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=self._envelope(verdict), stderr="")
            tools._call_grader("sys", "usr")
        assert m.call_args.kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == tools._effort_level


class TestGraderPromptsToolFree:  # C1 — grader prompts must not instruct tool use
    def test_no_grader_system_prompt_instructs_read_and_bash(self):
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "ironclaude" / "orchestrator_mcp.py"
        assert "Read and Bash" not in src.read_text(), \
            "a grader system prompt still tells the tool-free grader to use Read/Bash"
```
Run:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest tests/test_orchestrator_mcp.py::TestCallGraderSubprocess -q
```
Expected: FAIL (new `_call_grader` internals not implemented; old impl calls tmux, not subprocess).

**Step 2: Rewrite `_call_grader` and add the verdict-schema + command helpers** in `orchestrator_mcp.py`. Replace the body of `_call_grader` (currently lines ~874-906) and add helpers. Reuse `self._grader_model`, `self._grader_home`, `self._effort_level`, `self._grader_lock`, and `ensure_brain_trusted` (already imported lazily in `_spawn_grader`). Sketch:
```python
import tempfile  # add at top if not present

_GRADER_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "approved": {"type": "boolean"},
        "feedback": {"type": "string"},
        "recommended_model": {"type": "string"},
    },
    "required": ["grade", "approved", "feedback"],
    "additionalProperties": False,
}

def _grader_env(self) -> dict:
    env = dict(os.environ)
    # Keep Claude Max subscription billing — never let a stray key switch to metered API.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["CLAUDE_CODE_EFFORT_LEVEL"] = self._effort_level
    return env

def _call_grader(self, system_prompt: str, user_prompt: str, batch: bool = False) -> dict | list:
    schema = ({"type": "array", "items": _GRADER_VERDICT_SCHEMA} if batch else _GRADER_VERDICT_SCHEMA)
    from ironclaude.main import ensure_brain_trusted
    with self._grader_lock:
        try:
            ensure_brain_trusted(self._grader_home)
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as sf:
                sf.write(system_prompt)
                sysfile = sf.name
            try:
                cmd = [
                    "claude", "-p",
                    "--system-prompt-file", sysfile,
                    "--output-format", "json",
                    "--json-schema", json.dumps(schema),
                    "--model", f"{self._grader_model}[1m]",
                    "--dangerously-skip-permissions",
                    "--allowedTools", "",   # TOOL-FREE — confirm exact flag via `claude -p --help`
                ]
                proc = subprocess.run(
                    cmd, input=user_prompt, cwd=self._grader_home, env=self._grader_env(),
                    capture_output=True, text=True, timeout=self.GRADER_TIMEOUT_SECONDS,
                )
            finally:
                try:
                    os.unlink(sysfile)
                except OSError:
                    pass
        except subprocess.TimeoutExpired:
            self._last_grader_delta = ""
            logger.warning("Grader subprocess timed out after %ds", self.GRADER_TIMEOUT_SECONDS)
            return self._grader_failure(batch, f"Grader timed out after {self.GRADER_TIMEOUT_SECONDS}s")
        except Exception as exc:  # noqa: BLE001 — a grader failure must never crash the caller
            self._last_grader_delta = ""
            return self._grader_failure(batch, f"Grader subprocess error: {exc}")

        self._last_grader_delta = ""  # no pane to scrape; tool-free grader makes no tool calls
        if proc.returncode != 0:
            return self._grader_failure(batch, f"Grader exited {proc.returncode}: {proc.stderr[:300]}")
        try:
            envelope = json.loads(proc.stdout)
            out = envelope.get("structured_output")
        except (json.JSONDecodeError, AttributeError):
            out = None
        if out is None:
            return self._grader_failure(batch, f"Grader produced no structured_output: {proc.stdout[:300]}")
        if batch:
            return out if isinstance(out, list) else self._grader_failure(True, "Grader batch output not a list")
        return {"grade": out.get("grade", "F"), "approved": out.get("approved", False),
                "feedback": out.get("feedback", ""), **({"recommended_model": out["recommended_model"]} if "recommended_model" in out else {})}

def _grader_failure(self, batch: bool, feedback: str):
    f = {"grade": "F", "approved": False, "feedback": feedback}
    return [f] if batch else f
```
(A single bounded retry on timeout/failure is OPTIONAL — decide during implementation; if kept, wrap the subprocess call once more. Keep total bounded.)

**Step 3: Change the timeout constant.** `orchestrator_mcp.py:417` `GRADER_TIMEOUT_SECONDS = 600` → `GRADER_TIMEOUT_SECONDS = 120`.

**Step 3b: Make the grader system prompts tool-free (C1).** In `orchestrator_mcp.py`, edit ALL THREE grader system prompts so they no longer tell the (now tool-free) grader to use tools, and instead say to evaluate only from the inline evidence. Locations (confirm by `grep -n "Read and Bash" commander/src/ironclaude/orchestrator_mcp.py`):
- `:2219` (spawn_worker): replace "You may use Read and Bash to investigate before responding." → "Evaluate ONLY from the evidence in this prompt; you have no tools."
- `:2907` (approve_plan): same replacement.
- `:3523` (kill_worker): replace "…evaluate log evidence from that excerpt rather than re-reading the log file. You may still use Read and Bash to investigate other evidence (diffs, test output)." → "…evaluate ONLY from the evidence in this prompt (objective, evidence, and the log excerpt). You have no tools."

After this step, `grep -n "Read and Bash" commander/src/ironclaude/orchestrator_mcp.py` must return nothing (guarded by `TestGraderPromptsToolFree`).

**Step 4: Delete the obsolete tmux-transport code** in `orchestrator_mcp.py`: `_ensure_grader`, `_spawn_grader`, `_is_grader_alive`, `_do_grader_send_and_poll`, `_wait_for_grader_clear`, `_grader_ready`, the **`_grader_session = "ic-grader"` attr (~:427, M-1)**, the **`_GRADER_DEBUG` / `_GRADER_DEBUG_LOG` module globals** (used only by the deleted poll fn, M-2), and the nonce/`secrets` usage (`grep -n secrets` first — the only use is the grader nonce at ~:755; then remove `import secrets`). Also delete **`_deactivate_pm_via_sqlite`** (def ~:1856) — its only runtime caller is `_spawn_grader:723` (verified: the `:327`/`:356` hits are comment references inside a *different* function, which stays). Keep `_grader_lock`, `_grader_model`, `_grader_home`, `_effort_level`, `_parse_tool_calls_from_delta`, the separate mirror-PM-deactivation function at ~:327-356, and the shadow-grader path.

**Step 5: Delete/retarget the obsolete transport tests** in `test_orchestrator_mcp.py` so the suite is green. **Locate each by exact name (`grep -n`), NOT by class grouping** — the tests are spread across several classes (this was tier-up review I5). Delete these (they test deleted functions):
- In `TestPersistentGrader`: `test_ensure_grader_spawns_session`, `test_ensure_grader_noop_if_ready`, `test_ensure_grader_returns_false_on_spawn_failure`, `test_ensure_grader_kills_zombie_and_respawns`, `test_ensure_grader_resets_ready_flag_on_dead_process`, `test_ensure_grader_truncates_log_before_spawn`, `test_ensure_grader_deactivates_pm_after_ready`, `test_ensure_grader_fails_if_pm_deactivation_fails`, `test_wait_for_grader_clear_detects_prompt`, `test_wait_for_grader_clear_times_out`, `test_deactivate_pm_via_sqlite` (C3 — its target helper is deleted in Step 4), and the old `_call_grader` poll/clear/nonce tests: `test_call_grader_waits_for_clear_completion`, `test_call_grader_reads_json_from_log`, `test_call_grader_sends_clear_after_response`, `test_call_grader_returns_f_on_timeout`, `test_call_grader_retries_once_on_timeout`, `test_call_grader_fails_on_double_timeout`, `test_call_grader_fails_if_grader_not_available`, `test_call_grader_handles_unescaped_quotes_in_json`, `test_call_grader_ignores_grade_injection_before_nonce_delimiter`.
- In `TestEffortLevel` (NOT TestPersistentGrader — I5): `test_grader_spawn_includes_effort_level`, `test_grader_spawn_uses_effort_override` (they test `_spawn_grader`; effort coverage is preserved by the new `test_sets_effort_level_env`).
- In `TestCallGraderLocking`: `test_grader_ready_reset_on_timeout` (mocks `_do_grader_send_and_poll`). **KEEP** its sibling `test_grader_lock_attribute_exists` in the same class — do NOT delete it.
- The whole `TestDoGraderSendAndPoll` class.

**Retarget the batch tests (C2).** `TestCallGraderBatch` (~`test_orchestrator_mcp.py:6299-6331`) does NOT mock `_do_grader_send_and_poll`; it mocks `_ensure_grader`/`_wait_for_grader_clear`/`tmux.capture_pane` and patches `secrets.token_hex` (which breaks once `import secrets` is removed). Rewrite these two batch tests to mock `subprocess.run` returning an envelope whose `structured_output` is a JSON **array**, and drop all tmux/`secrets` mocking.

**Step 5b: Delete the second obsolete test file (C1 from round 2).** `commander/tests/test_orchestrator_debug_log.py` is a single class `TestDoGraderDebugLog` that tests the deleted `_do_grader_send_and_poll` (line 42), patches the deleted `_GRADER_DEBUG`/`_GRADER_DEBUG_LOG` (29-30) and `_wait_for_grader_clear` (40) and `secrets.token_hex` (41). Delete the whole file:
```bash
git rm commander/tests/test_orchestrator_debug_log.py
```

**Step 6: Run the FULL commander suite (GREEN at THIS task's boundary — round-2 I-1).** Run the whole suite here (not just one file) so an obsolete-test breakage cannot hide until Task 2:
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest -q
```
Expected: green (or only the known pre-existing thread-leak flake `test_send_to_worker_mcp_wrapper_returns_str_on_rejection`, which passes standalone). New subprocess tests green; obsolete transport tests removed; all `_mock_grader_approve`/caller tests unaffected (they mock `_call_grader`).

**Step 6b: REQUIRED real-CLI smoke test (I4 — the ONLY check of the actual flags/field).** Because every unit test mocks `subprocess.run`, a wrong flag or envelope-field name would ship green. Run one real invocation and confirm a schema verdict comes back. Use the **configured grader model** (`self._grader_model`, e.g. `opus` — NOT an env var; round-2 M-3). Example (read-only; adapt to the confirmed flags from Step 0):
```bash
printf '%s' 'Objective: say ok. Evidence: done.' | claude -p --output-format json --json-schema '{"type":"object","properties":{"grade":{"type":"string","enum":["A","B","C","D","F"]},"approved":{"type":"boolean"},"feedback":{"type":"string"}},"required":["grade","approved","feedback"]}' --model opus --dangerously-skip-permissions
```
Expected: stdout is JSON whose `structured_output` (or the real field name) contains a `grade`/`approved`/`feedback` object; process exits promptly (not a 120s hang). If the flag/field names differ from the code, FIX the code and re-run Step 6. Do NOT proceed to Step 7 until a real verdict is observed.

**Step 7: Stage.**
```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 2: CHANGELOG + full verification

**Files:**
- Modify: `CHANGELOG.md`

**No tests required for the CHANGELOG edit** (documentation); verification runs the suites.

**Step 1: Add a CHANGELOG entry** (under `[Unreleased]` or a new patch heading — do NOT change version numbers unless the operator asks) describing: grader transport replaced with a tool-free `claude -p` subprocess + schema-validated JSON + hard 120s subprocess timeout; fixes the false-F grades (pane-scraping misread valid verdicts) and the ~20-min daemon/brain freeze (synchronous 600s block); Claude Max billing preserved; persistent-tmux grader machinery removed. **Behavior changes to call out:** the grader is now tool-free — it evaluates only from inline evidence (reduced rigor: no independent diff/test read), and the shadow-concordance report's "Opus tool calls" column is now always empty (M7). Deploy note: daemon code → **restart the daemon** (no plugin/skill/hook change).

**Step 2: Run the full commander test suite (no regressions).**
```bash
cd commander && PYTHONUNBUFFERED=1 python -m pytest -q
```
Expected: green (or only the known pre-existing thread-leak flake `test_send_to_worker_mcp_wrapper_returns_str_on_rejection`, which passes standalone).

**Step 3: Stage.**
```bash
git add CHANGELOG.md
```

---

## Notes for the operator (post-execution)

- **Deploy:** daemon code only → **restart the daemon**. No `make deploy-hooks` / plugin update needed.
- **Post-deploy validation:** the required Step 6b smoke test already proved the CLI flags at build time. After the daemon restart, trigger one real grade (spawn/approve/kill with evidence) and confirm a fast schema verdict returns (~40s, not a timeout) and billing stayed on Claude Max (no `ANTHROPIC_API_KEY` in the daemon env).
- **Out of scope (deferred):** Ollama-shadow fail-fast, the ~517k-token prompt trim, and the duplicate log handler.
