# v1.2.0 Security Fixes Remediation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Before deploy and tag: the Codex Brain's extra-server start check ignores servers Codex reports as `disabled`; expanding brace groups are refused by both Brain git checks, `brain-orchestrator-guard.sh` and the shared read-only predicate; globs are refused in Brain git commands; then rerun the suites, update the release notes and run the post-deploy live checks.

**Requirements:** `docs/plans/2026-10-18-v120-security-fixes-remediation-design.md` (requirements 1a-R, 1b-R and 1c-R). It is both the requirements file and the design file.

**Architecture:**
- **1a-R.** In `_verify_orchestrator_mcp` (`commander/src/ironclaude/codex_brain_client.py:961-968`), `extra` counts a server outside `_TOOL_SERVERS` unless `server.get("runtimeStatus") == "disabled"`. `_install_verified_tool_inventory` (`:913-932`) reads only `_TOOL_SERVERS`, so nothing else changes.
- **1b-R / 1c-R, Python.** Each check gains a module-level `_EXPANDING_BRACE_RE = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")` and `_GLOB_CHARACTERS = ("*", "?", "[")`, applied right after the existing metacharacter check: `codex_brain_client._git_command_allowed` (`:153-154`) returns `False`; `BrainClient._tool_guard_logic` Bash branch (`brain_client.py:442-444`) returns `(False, "Shell brace expansion is not allowed in git commands.")` or `(False, "Glob characters are not allowed in git commands.")`. Both modules already import `re`.
- **1b-R / 1c-R, hook.** In `commander/hooks/brain-orchestrator-guard.sh`, an expanding-brace check right after the chaining check (`:32-35`), before the Codex research predicate (`:37-45`) and before `-C` normalization (`:47-48`); `RAW_CMD="$CMD"` saved just before normalization; a glob check on `RAW_CMD` right before `# Extract git subcommand` (`:63`), after the must-be-git check.
- **1b-R, shared predicate.** `worker/hooks/bash-readonly-guard.sh` `_has_blocked_metachars` (`:13-18`) gains the same brace regex. It serves the worker read-only stages (`professional-mode-guard.sh:689-754`), the worker `git add` check (`:872`), the review allowlist, `workspace-path-adapter.sh:29` and the Codex Brain research path. The commander-only transport check (`professional-mode-guard.sh:314`) runs before any of these.
- **Deploy path (for Task 3).** `make deploy-hooks` copies `worker/hooks/*.sh`; `commander/hooks/*.sh` reach `~/.claude/ironclaude-hooks/` through `_sync_brain_settings_hooks` at Commander start.

**Tech Stack:** Python and pytest (Commander); bash and jq (hooks); vitest (state-manager, regression only).

**Execution invariants:**
- **Shell and paths.** Shell state does not persist between steps; every step uses absolute paths. The repo root is `/Users/roberthyatt/Code/ironclaude`; git runs as `git -C /Users/roberthyatt/Code/ironclaude`. The shell is zsh with `nomatch`, so globs are quoted. Foreground `sleep` is blocked; to wait, use a Monitor until-loop.
- **Python.** Commander tests run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest ...`. Give a full-suite Bash call `timeout: 600000`.
- **Hook tests.** `bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh`; `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-bash-readonly-guard.sh`; all hook suites: `cd /Users/roberthyatt/Code/ironclaude && make test-hooks` (60 files).
- **Logs and staging.** Each run writes its own log under `/private/tmp/claude-502/taf3/` with an `r` prefix. Evidence commands never use `2>/dev/null`. `docs/` is gitignored, so `docs/plans` files are staged with `git add -f`. `worker/.codex-plugin/plugin.json` is modified and unstaged and is not part of this plan; never stage it.
- **Prohibited actions.** Never write `~/.codex/config.toml`. Never commit, tag or push; the operator does those.
- **Expectations and stops.** RED expectations name exactly the tests that fail, derived by tracing the current code. If a RED run differs, STOP and report; do not adjust the tests. Any GREEN failure is a real STOP. Counts marked "derived" come from arithmetic on this plan's tests; the Commander baseline is 4126 (the CHANGELOG `Suites: commander 4126,` line); a different passed count with 0 failed is recorded, not a STOP.
- **State-manager load rule.** If the ONLY failure is `runCodexAdvisorReview > kills a real SIGTERM-resistant parent and grandchild process group`: copy the log to `<log>.loaded` and append `uptime`; wait with a Monitor until-loop until the 1-minute load is below 8 (at most 30 minutes, else STOP for the operator); rerun once and judge on the rerun. Never kill other processes.
- **Orchestrator-only steps** have no shell command and are run by the main session.

---

## Task 1: Disabled servers, expanding braces and Brain git globs (1a-R, 1b-R, 1c-R)

**Files:** Modify `commander/src/ironclaude/codex_brain_client.py`, `commander/src/ironclaude/brain_client.py`, `commander/hooks/brain-orchestrator-guard.sh`, `worker/hooks/bash-readonly-guard.sh`, `commander/tests/test_codex_brain_client.py`, `commander/tests/test_brain_client.py`, `commander/hooks/tests/test-brain-orchestrator-guard.sh`, `worker/hooks/tests/test-bash-readonly-guard.sh`.

**Step 1: RED tests**

(a) `commander/tests/test_codex_brain_client.py`, replace

```python
    'git commit --am""end -m x',
]
_GIT_ALLOWED_FORMS = [
    "git diff",
```

with

```python
    'git commit --am""end -m x',
    # Shell expansion rewrites an approved command (design 2026-10-18 v120-security-fixes-remediation
    # 1b-R expanding braces, 1c-R globs).
    "git diff {--output=/tmp/x,}",
    "git log {--output=/tmp/x,-1}",
    "git diff *",
    "git log -- '*.py'",
    "git diff ?",
    "git diff [a]",
    "git log HEAD~{1..2}",
]
_GIT_ALLOWED_FORMS = [
    "git diff",
    # Braces without , or .. do not expand (1b-R).
    "git log HEAD@{1}",
    "git diff @{u}",
```

(b) Same file, `TestOrchestratorStartupReadiness.test_verify_rejects_servers_outside_brain_set`: replace

```python
                    "node_repl": {"tools": {"js": {"name": "js"}}},
                },
                None,
            ),
        )

        assert client._verify_orchestrator_mcp() == (
            "Codex Brain MCP inventory has servers outside the Brain set: node_repl, zeta_tools"
        )
```

with

```python
                    "node_repl": {"tools": {"js": {"name": "js"}}},
                    # Any status other than "disabled" still counts (1a-R).
                    "zeta_live": {"runtimeStatus": "ready", "tools": {}},
                },
                None,
            ),
        )

        assert client._verify_orchestrator_mcp() == (
            "Codex Brain MCP inventory has servers outside the Brain set: "
            "node_repl, zeta_live, zeta_tools"
        )
```

(c) Same class, insert immediately before `    def test_verify_passes_with_every_brain_server(self, monkeypatch):`:

```python
    def test_verify_skips_disabled_servers_outside_brain_set(self, monkeypatch):
        """Design 2026-10-18 v120-security-fixes-remediation 1a-R: Codex lists trim-disabled servers
        with runtimeStatus "disabled" and no tools (codex-cli 0.163.0-alpha.10.2). They run nothing,
        so they are not extras; the rows above with no status or "ready" still fail (fail closed)."""
        client = CodexBrainClient()
        orchestrator = {name: {"name": name} for name in self.REQUIRED}
        monkeypatch.setattr(client, "_await_orchestrator_ready", lambda timeout: None)
        monkeypatch.setattr(
            client,
            "_list_mcp_server_inventory",
            lambda: (
                {
                    "orchestrator": {"tools": orchestrator},
                    "episodic-memory": {"tools": {}},
                    "node_repl": {"name": "node_repl", "runtimeStatus": "disabled", "tools": {}},
                    "computer-use": {"name": "computer-use", "runtimeStatus": "disabled", "tools": {}},
                },
                None,
            ),
        )

        assert client._verify_orchestrator_mcp() is None
        assert set(client._verified_tool_inventory) == {"orchestrator", "episodic-memory"}

```

(d) `commander/tests/test_brain_client.py`, class `TestBrainGitWriteGuard`: replace

```python
        ('git commit --am""end -m x', "amend"),
    ])
```

with

```python
        ('git commit --am""end -m x', "amend"),
        # Shell expansion rewrites an approved command (design 2026-10-18
        # v120-security-fixes-remediation 1b-R expanding braces, 1c-R globs).
        ("git diff {--output=/tmp/x,}", "brace"),
        ("git log {--output=/tmp/x,-1}", "brace"),
        ("git diff *", "glob"),
        ("git log -- '*.py'", "glob"),
        ("git diff ?", "glob"),
        ("git diff [a]", "glob"),
        ("git log HEAD~{1..2}", "brace"),
    ])
```

and replace

```python
        "git commit -m x",
    ])
    def test_listing_and_staging_allowed(self, command):
```

with

```python
        "git commit -m x",
        # Braces without , or .. do not expand (1b-R).
        "git log HEAD@{1}",
        "git diff @{u}",
    ])
    def test_listing_and_staging_allowed(self, command):
```

(e) `commander/hooks/tests/test-brain-orchestrator-guard.sh`, insert immediately before the line `assert_eq "claude Bash git diff allowed"                "0" "$(feed with brain "" Bash '{"command":"git diff"}')"`:

```bash
# Shell expansion rewrites an approved command (design 2026-10-18 v120-security-fixes-remediation)
assert_eq "claude Bash git diff brace --output blocked" "2" "$(feed with brain "" Bash '{"command":"git diff {--output=/tmp/x,}"}')"
assert_eq "codex exec git diff brace --output blocked"  "2" "$(feed with brain codex exec_command '{"cmd":"git diff {--output=/tmp/x,}"}')"
assert_eq "codex exec git -C brace blocked"             "2" "$(feed with brain codex exec_command '{"cmd":"git -C {.,diff,--output=/tmp/x} status"}')"
assert_eq "claude Bash git diff glob blocked"           "2" "$(feed with brain "" Bash '{"command":"git diff *"}')"
assert_eq "claude Bash git diff ? blocked"              "2" "$(feed with brain "" Bash '{"command":"git diff ?"}')"
assert_eq "claude Bash git log brace range blocked"     "2" "$(feed with brain "" Bash '{"command":"git log {1..2}"}')"
assert_eq "codex exec find brace -delete blocked"       "2" "$(feed with brain codex exec_command '{"cmd":"find /tmp {-delete,}"}')"
assert_eq "claude Bash git log HEAD@{1} allowed"        "0" "$(feed with brain "" Bash '{"command":"git log HEAD@{1}"}')"
```

(f) `worker/hooks/tests/test-bash-readonly-guard.sh`, insert immediately before the line `# Non-allowlisted commands -> BLOCK`:

```bash
# Expanding brace groups -> BLOCK (design 2026-10-18 v120-security-fixes-remediation 1b-R)
block "find /tmp {-delete,}"
block "ls {a,b}"
block "cat f{1..3}"
# Braces without , or .. do not expand -> ALLOW (bounds the rule)
allow "cat a{b}"

```

and insert immediately after the line `git_block() { if is_readonly_git "$1"; then echo "FAIL expected BLOCK: $1"; fail=1; fi; }`:

```bash
git_block "git diff {--output=/tmp/x,}"
```

**Step 2: Run RED**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py::TestGitCommandAllowedWriteForms tests/test_codex_brain_client.py::TestOrchestratorStartupReadiness tests/test_brain_client.py::TestBrainGitWriteGuard -q > /private/tmp/claude-502/taf3/r1-red.log 2>&1; echo "rc=$?"; rg -n '^FAILED' /private/tmp/claude-502/taf3/r1-red.log; tail -n 1 /private/tmp/claude-502/taf3/r1-red.log; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh > /private/tmp/claude-502/taf3/r1-hook-red.log 2>&1; echo "hook_rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf3/r1-hook-red.log; tail -n 1 /private/tmp/claude-502/taf3/r1-hook-red.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-bash-readonly-guard.sh > /private/tmp/claude-502/taf3/r1-pred-red.log 2>&1; echo "pred_rc=$?"; rg -n -e '^FAIL' -e 'ALL PASS' -e '^FAILURES' /private/tmp/claude-502/taf3/r1-pred-red.log
```

Expected (derived by tracing the current code):
- `rc=1` with exactly 15 failed: `test_write_forms_declined` for the 7 new refused rows (the Codex check returns True for each today); `test_write_forms_refused` for the same 7 rows (the Claude check allows them today); `test_verify_skips_disabled_servers_outside_brain_set` (the check returns `servers outside the Brain set: computer-use, node_repl`). The edited reject test passes (today every extra counts, including `zeta_live`); `test_matches_claude_brain_guard` and the allowed rows pass.
- `hook_rc=1`; FAIL lines for exactly the 7 new blocked rows; last line `26 passed, 7 failed`.
- `pred_rc=1`; FAIL lines `FAIL expected BLOCK:` for `find /tmp {-delete,}`, `ls {a,b}`, `cat f{1..3}` and `git diff {--output=/tmp/x,}`; then `FAILURES`.

**Step 3: GREEN code**

(a) `commander/src/ironclaude/codex_brain_client.py`, replace

```python
_SHELL_METACHARACTERS = (";", "|", "&", "&&", "||", "$(", "`", "\n", "\r", ">", "<", "$")
```

with

```python
_SHELL_METACHARACTERS = (";", "|", "&", "&&", "||", "$(", "`", "\n", "\r", ">", "<", "$")
# The shell expands these after the check approves the command (design 2026-10-18
# v120-security-fixes-remediation): a brace group with , or .. ({--output=x,}) and a glob that
# matches a file named like an option. @{u} and HEAD@{1} do not expand and stay allowed.
_EXPANDING_BRACE_RE = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
_GLOB_CHARACTERS = ("*", "?", "[")
```

and replace

```python
    if any(c in cmd for c in _SHELL_METACHARACTERS):
        return False
    if "-c " in cmd:
        return False
```

with

```python
    if any(c in cmd for c in _SHELL_METACHARACTERS):
        return False
    if _EXPANDING_BRACE_RE.search(cmd) or any(c in cmd for c in _GLOB_CHARACTERS):
        return False
    if "-c " in cmd:
        return False
```

(b) Same file, `_verify_orchestrator_mcp`, replace

```python
        extra = sorted(set(inventory) - _TOOL_SERVERS)
```

with

```python
        # Codex also lists trim-disabled servers, as runtimeStatus "disabled" with no tools; they run
        # nothing. A missing or other status still counts (design 2026-10-18
        # v120-security-fixes-remediation 1a-R).
        extra = sorted(
            name for name, server in inventory.items()
            if name not in _TOOL_SERVERS and server.get("runtimeStatus") != "disabled"
        )
```

(c) `commander/src/ironclaude/brain_client.py`, replace

```python
            if any(c in cmd for c in _SHELL_METACHARACTERS):
                return (False, "Shell metacharacters are not allowed in git commands.")
```

with

```python
            if any(c in cmd for c in _SHELL_METACHARACTERS):
                return (False, "Shell metacharacters are not allowed in git commands.")
            # The shell expands these after the check approves the command (design 2026-10-18
            # v120-security-fixes-remediation); @{u} and HEAD@{1} do not expand and stay allowed.
            if re.search(r"\{[^{}]*(?:,|\.\.)[^{}]*\}", cmd):
                return (False, "Shell brace expansion is not allowed in git commands.")
            if any(c in cmd for c in ("*", "?", "[")):
                return (False, "Glob characters are not allowed in git commands.")
```

(d) `commander/hooks/brain-orchestrator-guard.sh`, replace

```bash
    echo "BLOCKED — Shell chaining/redirection is not allowed for Brain." >&2
    exit 2
  fi
```

with

```bash
    echo "BLOCKED — Shell chaining/redirection is not allowed for Brain." >&2
    exit 2
  fi

  # A brace group with , or .. expands after this check approves the command, e.g.
  # git diff {--output=x,} or find /tmp {-delete,}. Checked on the raw command, before the
  # research predicate and -C normalization (design 2026-10-18 v120-security-fixes-remediation).
  if printf '%s' "$CMD" | grep -qE '\{[^{}]*(,|\.\.)[^{}]*\}'; then
    echo "BLOCKED — Shell brace expansion is not allowed for Brain." >&2
    exit 2
  fi
```

replace

```bash
  # Normalize: strip -C <path> so downstream subcommand extraction works unchanged
```

with

```bash
  # The raw command, kept for checks that -C normalization must not hide.
  RAW_CMD="$CMD"
  # Normalize: strip -C <path> so downstream subcommand extraction works unchanged
```

and replace

```bash
  # Extract git subcommand
```

with

```bash
  # A glob can hand git a file named like an option (git diff * with a file named --output=x).
  case "$RAW_CMD" in
    *[\*\?\[]*)
      echo "BLOCKED — Glob characters are not allowed in Brain git commands." >&2
      exit 2
      ;;
  esac

  # Extract git subcommand
```

(e) `worker/hooks/bash-readonly-guard.sh`, replace

```bash
  if printf '%s' "$cmd" | grep -qE '[;&|`<>]|\$\('; then return 0; fi
  return 1
}
```

with

```bash
  if printf '%s' "$cmd" | grep -qE '[;&|`<>]|\$\('; then return 0; fi
  # A brace group with , or .. expands after the check (find /tmp {-delete,}); design 2026-10-18
  # v120-security-fixes-remediation 1b-R. Braces without either (HEAD@{1}, -exec … {}) do not.
  if printf '%s' "$cmd" | grep -qE '\{[^{}]*(,|\.\.)[^{}]*\}'; then return 0; fi
  return 1
}
```

and replace line 12 of `worker/hooks/bash-readonly-guard.sh`, which reads exactly

```bash
# Blocks: ; & | ` $(  > <  and newline.
```

with

```bash
# Blocks: ; & | ` $(  > <  newline, and brace groups containing , or ..
```

**Step 4: Run GREEN**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py tests/test_codex_brain_tool_trim.py tests/test_brain_client.py -q > /private/tmp/claude-502/taf3/r1-green.log 2>&1; echo "rc=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf3/r1-green.log; tail -n 1 /private/tmp/claude-502/taf3/r1-green.log; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh > /private/tmp/claude-502/taf3/r1-hook-green.log 2>&1; echo "hook_rc=$?"; tail -n 1 /private/tmp/claude-502/taf3/r1-hook-green.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-bash-readonly-guard.sh > /private/tmp/claude-502/taf3/r1-pred-green.log 2>&1; echo "pred_rc=$?"; tail -n 1 /private/tmp/claude-502/taf3/r1-pred-green.log
```

Expected: `rc=0`, no FAILED or ERROR lines, 0 failed; `hook_rc=0` and `33 passed, 0 failed`; `pred_rc=0` and `ALL PASS`.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_client.py commander/src/ironclaude/brain_client.py commander/hooks/brain-orchestrator-guard.sh worker/hooks/bash-readonly-guard.sh commander/tests/test_codex_brain_client.py commander/tests/test_brain_client.py commander/hooks/tests/test-brain-orchestrator-guard.sh worker/hooks/tests/test-bash-readonly-guard.sh
```

Expected: no output.

---

## Task 2: Full suites, docs and staging (depends on 1)

No tests required: regression run and release notes only.

**Files:** Modify `CHANGELOG.md` and `docs/plans/2026-10-18-v120-security-fixes-findings.md`. Stage this plan, its `.plan.json` and the remediation design.

**Step 1: Full Commander suite** (Bash `timeout: 600000`)

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/taf3/r2-suite.log 2>&1; echo "suite=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf3/r2-suite.log; tail -n 1 /private/tmp/claude-502/taf3/r2-suite.log
```

Expected: `suite=0`, no FAILED or ERROR lines. Passed count (derived) `4126 + 28 = 4154`: Codex 18 parametrized rows (7 refused rows × `test_write_forms_declined` and `test_matches_claude_brain_guard`, 2 allowed rows × `test_listing_and_staging_allowed` and `test_matches_claude_brain_guard`) and 1 inventory test; Claude 9 rows (7 refused, 2 allowed). Record it as `<N>`.

**Step 2: Hook suites**

```bash
cd /Users/roberthyatt/Code/ironclaude && make test-hooks > /private/tmp/claude-502/taf3/r2-hooks.log 2>&1; echo "make_rc=$?"; rg -c '^--- ' /private/tmp/claude-502/taf3/r2-hooks.log; tail -n 3 /private/tmp/claude-502/taf3/r2-hooks.log
```

Expected: `make_rc=0` and `60` suite headers (the shared-predicate change runs under every worker hook suite here, including `test-guard-security.sh`).

**Step 3: State-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf3/r2-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf3/r2-npm.log
```

Expected: `npm=0` and `Tests  273 passed (273)`.

**Step 4: Findings**

In `docs/plans/2026-10-18-v120-security-fixes-findings.md`:
1. In the `## Release commit message addition` paragraph, replace `(git log, git diff and git show), and both` with `(git log, git diff and git show), and both Brains and the worker read-only check refuse brace groups the shell would expand (a comma or ..), and Brain git commands refuse glob characters, because the shell rewrites an approved command before git sees it; and both`.
2. In the same paragraph, replace `holds a server outside its own set (orchestrator, episodic-memory, research and ollama)` with `holds a running server outside its own set (orchestrator, episodic-memory, research and ollama; servers Codex reports as disabled do not count)`.
3. Append a section `## Remediation suites` with the Step 1 summary line, `make_rc` and the header count from Step 2, and the Step 3 summary lines.

```bash
rg -n -F -e 'refuse brace groups the shell would expand' -e 'servers Codex reports as disabled do not count' -e '## Remediation suites' /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-18-v120-security-fixes-findings.md
```

Expected: exactly two lines. `rg` prints a line once however many patterns match it: the paragraph line (both phrases) and the `## Remediation suites` heading.

**Step 5: CHANGELOG**

In `CHANGELOG.md`, in the bullet that starts `- **Both Brains refuse git file writes and branch changes`:
1. Replace `with `--output`, which writes a file.` with `with `--output`, which writes a file. Both Brains and the worker read-only check refuse brace groups the shell would expand (a comma or `..`), and Brain git commands refuse glob characters, because the shell rewrites an approved command before git sees it; `@{u}` and `HEAD@{1}` still work.`
2. Replace `holds a server outside its own set (`orchestrator`, `episodic-memory`, `research`, `ollama`)` with `holds a running server outside its own set (`orchestrator`, `episodic-memory`, `research`, `ollama`; servers Codex reports as disabled do not count)`.

Then replace `Suites: commander 4126,` with `Suites: commander <N>,` (Step 1 count).

```bash
rg -n -F -e 'refuse brace groups the shell would expand' -e 'servers Codex reports as disabled do not count' -e 'Suites: commander' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; rg -n -F 'Suites: commander 4126,' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; echo "stale_rc=$?"
```

Expected: four lines from the first `rg`: line 57 (the 1.2.0 bullet, both phrases), line 84 `Suites: commander <N>, state-manager 273, hooks 60`, and older releases' `Suites: commander` lines at 98 and 112; then `stale_rc=1` (unless `<N>` is itself 4126).

**Step 6: Stage and check the staged set**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-v120-security-fixes-findings.md docs/plans/2026-10-18-v120-security-fixes-remediation-design.md docs/plans/2026-10-18-v120-security-fixes-remediation.md docs/plans/2026-10-18-v120-security-fixes-remediation.plan.json; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status; git -C /Users/roberthyatt/Code/ironclaude status --short worker/.codex-plugin/plugin.json
```

Expected: exactly 18 entries against HEAD `e59359b`:
- **M (11):** `CHANGELOG.md`, `commander/hooks/brain-orchestrator-guard.sh`, `commander/hooks/tests/test-brain-orchestrator-guard.sh`, `commander/src/ironclaude/brain_client.py`, `commander/src/ironclaude/codex_brain_client.py`, `commander/src/ironclaude/worker_handoff.py`, `commander/tests/test_brain_client.py`, `commander/tests/test_codex_brain_client.py`, `commander/tests/test_worker_handoff.py`, `worker/hooks/bash-readonly-guard.sh`, `worker/hooks/tests/test-bash-readonly-guard.sh`.
- **A (7):** `docs/plans/2026-10-18-v120-security-fixes-design.md`, `-findings.md`, `.md`, `.plan.json`, `-remediation-design.md`, `-remediation.md`, `-remediation.plan.json`.

The last command prints ` M worker/.codex-plugin/plugin.json`. Any other staged entry means STOP and report.

After this task, an Opus end review covers the combined staged diff. The operator then folds it into the v1.2.0 commit, deploys and restarts, and only then lets Task 3 run.

---

## Task 3: Post-deploy live verification, operator-assisted (depends on 2)

No tests required: live probes after deploy. Every LLM self-report is evidence, not proof. If a probe misses its expectation, record it and STOP, then debug and fix it in this loop through a new task. Never rerun until it passes. Never write `~/.codex/config.toml`.

These steps are Task 5 of `docs/plans/2026-10-18-v120-security-fixes.md`, with the start marker `r3-start`, the probe log `r3-grader.log`, a `cmp` of the deployed `bash-readonly-guard.sh`, and a Step 1 shell probe.

**Files:** Modify `docs/plans/2026-10-18-v120-security-fixes-findings.md`.

**Step 1: Deploy precondition (operator-assisted)**

The operator has: committed the staged set as part of the one untagged v1.2.0 commit; run `claude plugin update ironclaude@ironclaude` and synced the Claude cache; run `make codex-plugin-release` and `make deploy-hooks`; restarted the Commander and ChatGPT (the Commander restart copies `commander/hooks/*.sh` to `~/.claude/ironclaude-hooks/`). Then:

```bash
touch /private/tmp/claude-502/taf3/r3-start; cmp /Users/roberthyatt/Code/ironclaude/commander/hooks/brain-orchestrator-guard.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-orchestrator-guard.sh; echo "guard_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/bash-readonly-guard.sh /Users/roberthyatt/.claude/ironclaude-hooks/bash-readonly-guard.sh; echo "predicate_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/agents/reviewer.md /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.2.0/agents/reviewer.md; echo "agent_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/brain-gated-actions.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-gated-actions.sh; echo "gate_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/commander/hooks/brain-hooks-canary.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-hooks-canary.sh; echo "canary_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/plan-validator.sh /Users/roberthyatt/.claude/ironclaude-hooks/plan-validator.sh; echo "validator_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh /Users/roberthyatt/.claude/ironclaude-hooks/execution-drift-check.sh; echo "drift_cmp=$?"; ls -t /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude; bash -c 'printf "%s\n" HEAD@{1} {a,b}'
```

Expected: every `*_cmp=0`; record the first `ls -t` line; the last command prints `HEAD@{1}`, `a`, `b` on three lines (bash leaves `HEAD@{1}` literal and expands `{a,b}`, the premise of the targeted rule). Any non-zero `cmp` means the deploy is incomplete: STOP and report which file.

**Step 2: A1, the reviewer agent's tools (orchestrator step)**

If `ironclaude:reviewer` is not in this session's agent list, the operator starts a fresh Claude Code session and this step runs there. Three dispatches with `model="haiku"`:
1. `Agent(subagent_type="ironclaude:reviewer")`: `List the exact names of every tool you can call in this session, comma-separated. Call no tool.`
2. `Agent(subagent_type="ironclaude:reviewer")`: `Run the shell command echo A1-PROBE with any tool you have and reply with its output, or reply NO-SHELL if you have no tool that can run it.`
3. Control: `Agent(subagent_type="general-purpose")` with the first prompt.

Expected: the first reply names exactly `Read`, `Grep` and `Glob` (no Bash, Edit, Write, Skill, Agent or `mcp__` tool); the second is `NO-SHELL`; the control names `Bash`. Otherwise record and STOP.

**Step 3: A Codex grader on the resolved newest model (R6)**

```bash
cat > /private/tmp/claude-502/taf3/grader_probe.py <<'PY'
"""Post-deploy probe (design 2026-10-17 v120-completeness R6): the Codex grader runs on the newest
installed Sol from this Mac's models_cache.json. Side effect outside /private/tmp: _call_grader's
idempotent ensure_brain_trusted(~/.ironclaude/grader), the same write the Commander makes at start-up."""
import copy, json, sys
from unittest.mock import MagicMock
sys.path.insert(0, "/Users/roberthyatt/Code/ironclaude/commander/src")
from ironclaude.config import DEFAULTS
from ironclaude.db import init_db
from ironclaude.provider_config import local_codex_cache_path, resolve_local_codex_model
import ironclaude.orchestrator_mcp as omcp

config = copy.deepcopy(DEFAULTS)
config["providers"]["clients"]["codex"]["enabled"] = True
config["providers"]["roles"]["grader"] = {"preferred": "codex", "clients": ["codex"]}
configured = config["providers"]["clients"]["codex"]["models"]["opus"]
print("cache=" + local_codex_cache_path())
print("configured_opus=" + configured)
print("resolved_opus=" + resolve_local_codex_model(configured))
tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                               db_conn=init_db("/private/tmp/claude-502/taf3/grader-probe.db"), config=config)
tools._ensure_role_capabilities("grader", "opus")
handle = tools._provider_router()[0].resolve("grader", "opus", ["local"])
print(f"route client={handle.client} model={handle.model}")
print("result=" + json.dumps(tools._call_grader("You are a code grader. Grade the code A-F.",
                                                "def add(a, b):\n    return a + b\n")))
PY
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf3/grader_probe.py > /private/tmp/claude-502/taf3/r3-grader.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf3/r3-grader.log; jq -r '[.models[]?.slug | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-sol$"))] | join(" ")' /Users/roberthyatt/.codex/models_cache.json
```

Expected: `probe=0`, `configured_opus=sol`; `resolved_opus=` is the numerically newest Sol slug on the last line; `route client=codex model=` equals it; `result=` holds a `grade` and no `infrastructure_error`. Any miss: record and STOP.

**Step 4: Commander on Codex, R4 item 1 (operator-assisted)**

The operator types `/ironclaude provider brain codex` in Slack (the Commander restarts), then asks `what unfinished work is sitting idle right now?`. Then:

```bash
rg -n -e 'Codex hook-trust warm-up finished ready=' -e 'Brain SDK client started client=codex' -e 'Brain SDK client not started client=codex' -e 'Codex Brain hooks are not running' -e 'spawn failed: Codex model family' -e 'servers outside the Brain set' /private/tmp/ic/daemon.log; ls -ld /Users/roberthyatt/.ironclaude/state/brain-gate; find /Users/roberthyatt/.ironclaude/state/brain-gate -name hooks-live -newer /private/tmp/claude-502/taf3/r3-start; find /private/tmp/ic/codex-brain-gate -name hooks-live -newer /private/tmp/claude-502/taf3/r3-start; find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /private/tmp/claude-502/taf3/r3-start
```

Expected, after the restart: `Codex hook-trust warm-up finished ready=True trust_prompt_answered=` then `Brain SDK client started client=codex`; no `Brain SDK client not started client=codex`, `Codex Brain hooks are not running`, `spawn failed: Codex model family` or `servers outside the Brain set` line (a `servers outside the Brain set` line names a live server the start check refused: record the names and STOP). `ls -ld` shows `drwx------`. The first `find` prints one `.../brain-gate/<session>/hooks-live`; the old-root `find` prints nothing; the last `find` lists the Brain's new rollout file(s).

**Step 5: The Codex Brain's rollout has no tool errors (R4 item 1)**

```bash
find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /private/tmp/claude-502/taf3/r3-start -exec rg -n -i -H -e 'professional mode is unset' -e 'access unavailable' {} +; echo "scan_done=$?"
```

Expected: no match lines, then `scan_done=0`. The operator confirms the Brain's Slack answer lists real workers or ledger tasks. A pass closes the 2026-10-10 backlog entry; the operator may switch back with `/ironclaude provider brain claude`.

**Step 6: Owed per-call tiering checks, R4 item 2 (operator-assisted)**

The operator triggers: **1b** `send_to_worker` or `post_message` to a live worker; **2** `evaluate_worker_health` on a live worker; **3** spawn a `claude-opus` worker. Then:

```bash
rg -n -e 'fast model answered via' -e 'fast model unavailable' -e 'fast model skips codex' -e 'Provider route skipped' /private/tmp/ic/daemon.log; rg -l -F '/advisor opus' /private/tmp/ic-logs
```

Expected, after the start marker: **1b** a `fast model answered via haiku` or `fast model answered via luna:<slug>` line; **2** a `fast model answered via sonnet` or `fast model answered via terra:<slug>` line; **3** the new worker's pane log listed by the second `rg`. If item 2 does not reach `daemon.log`, record `UNVERIFIED-LIVE: no daemon.log line for evaluate_worker_health; searched daemon.log and the Brain rollout` and grep the rollout for `evaluate_worker_health` as secondary evidence.

**Step 7: Carried R4b live items (operator-assisted)**

- **Defect 2:** Codex `/commit` with nothing staged, then the newest `human_intents` row via `sqlite3 -readonly`.
- **Defect 5:** `git add` in a managed worktree under a `workspace-write` Codex sandbox, on a scratch repository only.
- **Defect 9:** the transcript of a fresh `/activate-professional-mode`.

Record each as LIVE or FIXED with evidence, or keep `UNVERIFIED-LIVE` if the operator does not run it.

**Step 8: Record**

Append `## Post-deploy verification` to `docs/plans/2026-10-18-v120-security-fixes-findings.md` with every Step 1-7 output and verdict, A1 included.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-v120-security-fixes-findings.md
```

Expected: no output. The operator folds this addendum into the v1.2.0 commit before tagging.
