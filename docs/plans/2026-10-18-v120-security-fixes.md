# v1.2.0 Security Fixes Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fold four security fixes into v1.2.0 before deploy and tag. Neither Brain can write a file through `git … --output`. Both Python git checks allow `git branch` only in listing forms. The Codex Brain refuses to start with an MCP server outside its own set, and its tool trim keeps `research` and `ollama` only when IronClaude's own source file exists. A worker usage quarantine ends at most 7 days after the limit event.

**Requirements:** `docs/plans/2026-10-18-v120-security-fixes-design.md` (requirements 1b, 1a, 1c and 2). It is both the requirements file and the design file.

**Architecture:**
- **Git checks (1b, 1c).** Both Python checks, `BrainClient._tool_guard_logic` (Bash branch, `brain_client.py:431-449`) and `codex_brain_client._git_command_allowed` (`:142-158`), gain the same rules below: `--output` and `git branch` after the subcommand check and before the `--amend` check, and a wider `--amend` check. The subcommand derivation (`cmd.split()`) is unchanged; the rules read `args = shlex.split(cmd)[2:]`, so quoted or backslash-escaped forms (`"--output=…"`, `--\output=…`) parse the way the shell passes them to git. Unbalanced quotes (`shlex.split` raises `ValueError`) are refused. Both modules gain `import shlex`; neither imports it today.
  - **`--amend`.** The existing substring check stays, and the same `if` also refuses `commit` when any arg equals `--amend` or starts with `--amend=`, so `--am""end` is refused too. The hook's `--amend` grep is unchanged (out of scope).
  - **`--output`.** Refuse when any arg equals `--output` or starts with `--output=`, for every subcommand. `--output-indicator-new=…` and similar options stay allowed.
  - **`git branch`.** Allow only when every arg starts with `-` and none is in `{-d, -D, -m, -M, -c, -C, -f, --delete, --move, --copy, --force}`. No args is allowed. The set lives in `BrainClient.GIT_BRANCH_CHANGE_FLAGS` and `codex_brain_client._GIT_BRANCH_CHANGE_FLAGS`, and a test keeps them equal.
  - **Hook.** `brain-orchestrator-guard.sh` splits `log|diff|show)` out of its combined arm and exits 2 with a `BLOCKED` reason when a copy of the command with every `"`, `'` and `\` deleted matches `(^|[[:space:]])--output(=|[[:space:]]|$)`. Its branch arm is unchanged, as the design requires.
  - **Known residuals.** The hook's branch arm matches only short flags, so the hook still admits `git branch --delete x`; the Python checks refuse it. Listing flags whose value is a separate argument (`--contains X`, `--sort X`) are refused; the `=` forms pass. `--set-upstream-to=…`, `--unset-upstream` and `--edit-description` pass, as they do in the hook.
- **Codex Brain MCP inventory (1a).**
  - **Start check.** After the `episodic-memory` check, `_verify_orchestrator_mcp` (`codex_brain_client.py:900-927`) computes `sorted(set(inventory) - _TOOL_SERVERS)`. If the list is non-empty, it returns `Codex Brain MCP inventory has servers outside the Brain set: <names joined by ", ">`, which `start()` already turns into a failed start through `_fail_start`.
  - **Trim keep-set.** `_tool_trim_overrides` (`:361-370`) keeps `orchestrator` plus each `_OPTIONAL_MCP_SERVERS` name whose `_optional_mcp_source_path(name)` exists, the same check as `_optional_mcp_overrides`.
- **Quarantine cap (2).** `worker_handoff._QUARANTINE_MAX_SECONDS = 7 * 24 * 3600`. In `_quarantine_expires_at` (`:242-255`), a valid native reset returns `float(min(reset_at, limit))`, where `limit` is the event time plus the cap, or `time.time()` plus the cap when the event time cannot be parsed. The default branch is unchanged.
- **Deploy path (for Task 5).** `make deploy-hooks` copies only `worker/hooks/*.sh`. `commander/hooks/*.sh` reach `~/.claude/ironclaude-hooks/` through `_sync_brain_settings_hooks` at Commander start (`main.py:6077-6082`). The Codex Brain runs that copy through `codex-brain-hooks.sh`. The Claude Brain's `~/.ironclaude/brain/.claude/settings.local.json` runs the repo file directly.

**Tech Stack:** Python and pytest (Commander); bash and jq (hooks); vitest (state-manager, regression only).

**Execution invariants:**
- **Shell and paths.**
  - Shell state does not persist between steps, so every step uses absolute paths.
  - The repo root is `/Users/roberthyatt/Code/ironclaude`; git runs as `git -C /Users/roberthyatt/Code/ironclaude`. The Bash working directory at the start of each step is that repo root; commands still use absolute paths or an explicit `cd`.
  - The shell is zsh with `nomatch`, so globs are quoted wherever they must reach a program unexpanded.
  - Foreground `sleep` is blocked; to wait, use a Monitor until-loop.
- **Python.** The venv interpreter is `/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python`. Commander tests run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest ...`. A full Commander suite run may take several minutes, so give that Bash call `timeout: 600000`.
- **Hook tests.**
  - `bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh`;
  - all hook suites: `cd /Users/roberthyatt/Code/ironclaude && make test-hooks` (60 files: 7 `worker/hooks/test-*.sh`, 45 `worker/hooks/tests/test-*.sh`, 8 `commander/hooks/tests/test-*.sh`).
- **State-manager.** Commands run with cwd `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager`: `npm test`. This plan changes no state-manager file.
- **Logs and staging.**
  - Each run writes its own log under `/private/tmp/claude-502/taf3/`. Evidence commands never use `2>/dev/null`.
  - `docs/` is gitignored, so `docs/plans` files are staged with `git add -f`.
  - `worker/.codex-plugin/plugin.json` is modified and unstaged at plan time and is not part of this plan. Never stage it.
- **Prohibited actions.** Never write `~/.codex/config.toml`. Never commit, tag or push; the operator does those.
- **Expectations and stops.**
  - Every RED expectation names exactly the tests that fail, derived by tracing the current tests. If a RED run differs, STOP and report; do not adjust the tests to match.
  - Any GREEN run that shows a failure is a real STOP.
  - Counts marked "derived" come from arithmetic on the tests in this plan, not from a run. 4072 is the Commander count on `CHANGELOG.md` line 83 for HEAD `e59359b`; a different passed count with 0 failed is recorded, not a STOP.
- **State-manager load rule.** If the ONLY failure is `runCodexAdvisorReview > kills a real SIGTERM-resistant parent and grandchild process group`:
  1. Copy the log to `<log>.loaded` and append `uptime`.
  2. Wait with a Monitor until-loop on `uptime` until the 1-minute load is below 8, for at most 30 minutes. If it never drops, append the final `uptime` and STOP for the operator.
  3. Rerun that step's command once and judge on the rerun. Never kill other processes. Any other failure is a real STOP.
- **Line numbers and anchors.** Cited line numbers describe each file before the task's own edits. When an earlier edit in the same step shifts lines (for example, `brain_client.py` line 190 before the Bash branch at 441-449, or `codex_brain_client.py` lines 137-158 before 361-370 and 925-927), match on the quoted anchor text, not on the number.
- **Orchestrator-only steps.** Steps marked "Orchestrator step" are run by the main session. Subagents cannot dispatch agents, so these steps have no shell command.

---

## Task 1: Brain git guards, both clients (1b, 1c)

**Files:** Modify `commander/src/ironclaude/codex_brain_client.py`, `commander/src/ironclaude/brain_client.py`, `commander/hooks/brain-orchestrator-guard.sh`, `commander/tests/test_codex_brain_client.py`, `commander/tests/test_brain_client.py` and `commander/hooks/tests/test-brain-orchestrator-guard.sh`.

Both Python checks parse the arguments with `shlex.split`, not `str.split`, so a quoted or backslash-escaped `--output` is refused, and they refuse `git commit` when an arg is `--amend` or starts with `--amend=` (as well as the existing `--amend` substring check), so `--am""end` is refused. The hook's `log|diff|show` arm matches on a copy of the command with quotes and backslashes deleted.

**Step 1: Scratch directory and the git abbreviation probe**

The rule matches `--output` exactly. This probe checks that git does not accept an abbreviated `--outp=` for diff options, which would bypass it. The probe only reads the repository; the `--outp` target is under the scratch directory.

```bash
mkdir -p /private/tmp/claude-502/taf3 && ls -ld /private/tmp/claude-502/taf3; git -C /Users/roberthyatt/Code/ironclaude diff --outp=/private/tmp/claude-502/taf3/t1-abbrev-diff.out HEAD~1 HEAD -- CHANGELOG.md > /private/tmp/claude-502/taf3/t1-abbrev.log 2>&1; echo "diff_rc=$?"; git -C /Users/roberthyatt/Code/ironclaude log -1 --outp=/private/tmp/claude-502/taf3/t1-abbrev-log.out >> /private/tmp/claude-502/taf3/t1-abbrev.log 2>&1; echo "log_rc=$?"; cat /private/tmp/claude-502/taf3/t1-abbrev.log; ls /private/tmp/claude-502/taf3/t1-abbrev-diff.out /private/tmp/claude-502/taf3/t1-abbrev-log.out
```

Expected:
- one `drwx` line for `/private/tmp/claude-502/taf3`;
- `diff_rc` and `log_rc` non-zero, with git reporting an error for the option (unknown, unrecognized, invalid or ambiguous) in the log;
- `ls` reports `No such file or directory` for both `.out` files.

If either `.out` file exists, git accepts the abbreviation: STOP and report, because the rule must then refuse every `--outp…` prefix.

**Step 2: RED tests (Python)**

In `commander/tests/test_brain_client.py`, insert this class immediately before `class TestBashMetacharacterGuard:`:

```python
class TestBrainGitWriteGuard:
    """Design 2026-10-18 v120-security-fixes 1b and 1c: no file writes through git --output, and
    git branch only in listing forms. test_codex_brain_client.py holds the same rows for the Codex
    check and compares the two."""

    @pytest.mark.parametrize("command,reason", [
        ("git diff --output=/tmp/x", "output"),
        ("git log --output /tmp/x", "output"),
        ("git show --output=/tmp/x", "output"),
        ('git diff "--output=/tmp/x"', "output"),
        ("git diff --\\output=/tmp/x", "output"),
        ("git branch -D x", "branch"),
        ("git branch new-name", "branch"),
        ("git branch --delete x", "branch"),
        ('git commit --am""end -m x', "amend"),
    ])
    def test_write_forms_refused(self, command, reason):
        # Each row names the keyword its refusal message must carry: output, branch or amend.
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Bash", {"command": command})
        assert allowed is False
        assert reason in msg.lower()

    @pytest.mark.parametrize("command", [
        "git diff",
        "git log --oneline",
        "git branch",
        "git branch -a",
        "git branch --list",
        "git add x",
        "git commit -m x",
    ])
    def test_listing_and_staging_allowed(self, command):
        # Operator decision 2026-03-09: the Brain keeps git add and git commit.
        client = BrainClient()
        assert client._tool_guard_logic("Bash", {"command": command}) == (True, None)
```

In `commander/tests/test_codex_brain_client.py`, change line 9 from `from ironclaude.codex_brain_client import CodexBrainClient` to:

```python
from ironclaude.codex_brain_client import CodexBrainClient, _git_command_allowed
```

Then insert this block immediately before `class TestThreadIdExtraction:`:

```python
_GIT_WRITE_FORMS = [
    "git diff --output=/tmp/x",
    "git log --output /tmp/x",
    "git show --output=/tmp/x",
    'git diff "--output=/tmp/x"',
    "git diff --\\output=/tmp/x",
    "git branch -D x",
    "git branch new-name",
    "git branch --delete x",
    'git commit --am""end -m x',
]
_GIT_ALLOWED_FORMS = [
    "git diff",
    "git log --oneline",
    "git branch",
    "git branch -a",
    "git branch --list",
    "git add x",
    "git commit -m x",
]


class TestGitCommandAllowedWriteForms:
    """Design 2026-10-18 v120-security-fixes 1b and 1c. Approved Codex commands run outside the
    sandbox, so this check is load-bearing: no file writes through git --output, and git branch
    only in listing forms. Operator decision 2026-03-09: the Brain keeps git add and git commit."""

    @pytest.mark.parametrize("command", _GIT_WRITE_FORMS)
    def test_write_forms_declined(self, command):
        assert _git_command_allowed(command) is False

    @pytest.mark.parametrize("command", _GIT_ALLOWED_FORMS)
    def test_listing_and_staging_allowed(self, command):
        assert _git_command_allowed(command) is True

    @pytest.mark.parametrize("command", _GIT_WRITE_FORMS + _GIT_ALLOWED_FORMS)
    def test_matches_claude_brain_guard(self, command):
        """One IronClaude: the Codex check is a port of the Claude one and must agree on every row."""
        from ironclaude.brain_client import BrainClient

        allowed, _ = BrainClient()._tool_guard_logic("Bash", {"command": command})
        assert _git_command_allowed(command) is allowed

    def test_branch_change_flags_match_claude_brain_guard(self):
        import ironclaude.codex_brain_client as module
        from ironclaude.brain_client import BrainClient

        assert module._GIT_BRANCH_CHANGE_FLAGS == BrainClient.GIT_BRANCH_CHANGE_FLAGS
```

**Step 3: RED tests (hook)**

In `commander/hooks/tests/test-brain-orchestrator-guard.sh`, insert these lines immediately before `# Non-Brain sessions pass through`:

```bash
# git --output writes a file (design 2026-10-18 v120-security-fixes 1b); --output-indicator-* does not
assert_eq "claude Bash git diff --output= blocked"      "2" "$(feed with brain "" Bash '{"command":"git diff --output=/tmp/x"}')"
assert_eq "codex exec git diff --output= blocked"       "2" "$(feed with brain codex exec_command '{"cmd":"git diff --output=/tmp/x"}')"
assert_eq "claude Bash git log --output PATH blocked"   "2" "$(feed with brain "" Bash '{"command":"git log --output /tmp/x"}')"
assert_eq "claude Bash git diff quoted --output= blocked" "2" "$(feed with brain "" Bash '{"command":"git diff \"--output=/tmp/x\""}')"
assert_eq "claude Bash git diff backslash --output= blocked" "2" "$(feed with brain "" Bash '{"command":"git diff --\\output=/tmp/x"}')"
assert_eq "claude Bash git diff allowed"                "0" "$(feed with brain "" Bash '{"command":"git diff"}')"
assert_eq "codex exec git diff allowed"                 "0" "$(feed with brain codex exec_command '{"cmd":"git diff"}')"
assert_eq "claude Bash git diff --output-indicator-new allowed" "0" "$(feed with brain "" Bash '{"command":"git diff --output-indicator-new=+"}')"
```

**Step 4: Run RED**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_client.py::TestBrainGitWriteGuard tests/test_codex_brain_client.py::TestGitCommandAllowedWriteForms -q > /private/tmp/claude-502/taf3/t1-red.log 2>&1; echo "rc=$?"; rg -n '^FAILED' /private/tmp/claude-502/taf3/t1-red.log; tail -n 1 /private/tmp/claude-502/taf3/t1-red.log; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh > /private/tmp/claude-502/taf3/t1-hook-red.log 2>&1; echo "hook_rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf3/t1-hook-red.log; tail -n 1 /private/tmp/claude-502/taf3/t1-hook-red.log
```

Expected (derived):
- `rc=1` and `19 failed, 30 passed` (49 tests: 16 Claude + 33 Codex; 16 Claude = 9 + 7; 33 Codex = 9 + 7 + 16 + 1; failed 9 + 9 + 1 = 19; passed 7 + 7 + 16 = 30):
  - the 9 `TestBrainGitWriteGuard::test_write_forms_refused` rows, which the current check allows (`allowed is False` fails). The quoted and backslash `--output` forms pass the current `str.split` check, and `--am""end` passes because the substring `--amend` is absent;
  - the 9 `TestGitCommandAllowedWriteForms::test_write_forms_declined` rows, which the current check allows for the same reasons;
  - `test_branch_change_flags_match_claude_brain_guard`, which fails with `AttributeError` (no `_GIT_BRANCH_CHANGE_FLAGS` yet).
- These pass: the 7 + 7 allowed rows, and the 16 `test_matches_claude_brain_guard` rows (both checks allow all 16 today).
- `hook_rc=1`, exactly 5 `FAIL:` lines (`claude Bash git diff --output= blocked`, `codex exec git diff --output= blocked`, `claude Bash git log --output PATH blocked`, `claude Bash git diff quoted --output= blocked`, `claude Bash git diff backslash --output= blocked`, each `expected=2 actual=0`), and `20 passed, 5 failed` (17 existing + 3 new allowed = 20 passed; 25 total = 17 + 8 new).

**Step 5: GREEN code**

In `commander/src/ironclaude/brain_client.py`, replace

```python
import re
import threading
```

with

```python
import re
import shlex
import threading
```

In the same file, replace

```python
    # Git subcommands allowed via Bash (read + staging/commit operations)
    GIT_ALLOWED_COMMANDS = {"log", "diff", "show", "status", "ls-files", "blame", "branch", "add", "commit"}
```

with

```python
    # Git subcommands allowed via Bash (read + staging/commit operations)
    GIT_ALLOWED_COMMANDS = {"log", "diff", "show", "status", "ls-files", "blame", "branch", "add", "commit"}

    # git branch flags that delete, rename, copy or force. With any of them, or with an argument that
    # is not a flag (a branch name), git branch changes branches, so only listing forms pass (design
    # 2026-10-18 v120-security-fixes 1c). codex_brain_client._GIT_BRANCH_CHANGE_FLAGS is the same set.
    GIT_BRANCH_CHANGE_FLAGS = frozenset(
        {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy", "--force"}
    )
```

In the same file's `_tool_guard_logic`, replace

```python
            if subcommand not in self.GIT_ALLOWED_COMMANDS:
                return (False, f"Only allowed git commands permitted: {', '.join(sorted(self.GIT_ALLOWED_COMMANDS))}")
            # Block git commit --amend (prevents rewriting previous commits)
            if subcommand == "commit" and "--amend" in cmd:
                return (False, "git commit --amend is not allowed. Create new commits only.")
```

with

```python
            if subcommand not in self.GIT_ALLOWED_COMMANDS:
                return (False, f"Only allowed git commands permitted: {', '.join(sorted(self.GIT_ALLOWED_COMMANDS))}")
            # Parse the arguments the way the shell passes them to git, so a quoted or
            # backslash-escaped option ("--output=x", --\output=x, --am""end) is seen as git sees it.
            try:
                args = shlex.split(cmd)[2:]
            except ValueError:
                return (False, "Unbalanced quotes are not allowed in git commands.")
            # --output=<file> / --output <file> writes a file (design 2026-10-18 v120-security-fixes 1b)
            if any(arg == "--output" or arg.startswith("--output=") for arg in args):
                return (False, "git --output writes a file and is not allowed for the Brain.")
            # git branch: listing forms only, the brain-orchestrator-guard.sh rule (1c)
            if subcommand == "branch" and any(
                not arg.startswith("-") or arg in self.GIT_BRANCH_CHANGE_FLAGS for arg in args
            ):
                return (False, "git branch changes are not allowed for the Brain. Only listing is permitted.")
            # Block git commit --amend (prevents rewriting previous commits); the substring check
            # stays, and the parsed-args check also catches quoted forms such as --am""end.
            if subcommand == "commit" and (
                "--amend" in cmd
                or any(arg == "--amend" or arg.startswith("--amend=") for arg in args)
            ):
                return (False, "git commit --amend is not allowed. Create new commits only.")
```

In `commander/src/ironclaude/codex_brain_client.py`, replace

```python
import selectors
import subprocess
```

with

```python
import selectors
import shlex
import subprocess
```

In the same file, replace

```python
_GIT_ALLOWED_COMMANDS = frozenset(
    {"log", "diff", "show", "status", "ls-files", "blame", "branch", "add", "commit"}
)
```

with

```python
_GIT_ALLOWED_COMMANDS = frozenset(
    {"log", "diff", "show", "status", "ls-files", "blame", "branch", "add", "commit"}
)
# git branch flags that delete, rename, copy or force. With any of them, or with an argument that is
# not a flag (a branch name), git branch changes branches, so only listing forms pass (design
# 2026-10-18 v120-security-fixes 1c). Same set as BrainClient.GIT_BRANCH_CHANGE_FLAGS (a test compares).
_GIT_BRANCH_CHANGE_FLAGS = frozenset(
    {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy", "--force"}
)
```

In the same file's `_git_command_allowed`, replace

```python
    if subcommand not in _GIT_ALLOWED_COMMANDS:
        return False
    if subcommand == "commit" and "--amend" in cmd:
        return False
    return True
```

with

```python
    if subcommand not in _GIT_ALLOWED_COMMANDS:
        return False
    # Parse the arguments the way the shell passes them to git, so a quoted or
    # backslash-escaped option ("--output=x", --\output=x, --am""end) is seen as git sees it.
    try:
        args = shlex.split(cmd)[2:]
    except ValueError:
        return False
    # --output=<file> / --output <file> writes a file (design 2026-10-18 v120-security-fixes 1b)
    if any(arg == "--output" or arg.startswith("--output=") for arg in args):
        return False
    # git branch: listing forms only, the brain-orchestrator-guard.sh rule (1c)
    if subcommand == "branch" and any(
        not arg.startswith("-") or arg in _GIT_BRANCH_CHANGE_FLAGS for arg in args
    ):
        return False
    # The substring check stays; the parsed-args check also catches quoted forms such as --am""end.
    if subcommand == "commit" and (
        "--amend" in cmd
        or any(arg == "--amend" or arg.startswith("--amend=") for arg in args)
    ):
        return False
    return True
```

In `commander/hooks/brain-orchestrator-guard.sh`, replace

```bash
    log|diff|show|status|ls-files|blame|add)
      exit 0
      ;;
```

with

```bash
    log|diff|show)
      # --output=<file> / --output <file> writes a file (design 2026-10-18 v120-security-fixes 1b).
      # Match on a copy with every " ' and \ deleted, so "--output=x" and --\output=x are caught.
      if printf '%s' "$CMD" | tr -d "\"'\\\\" | grep -qE '(^|[[:space:]])--output(=|[[:space:]]|$)'; then
        echo "BLOCKED — git --output writes a file and is not allowed for Brain." >&2
        exit 2
      fi
      exit 0
      ;;
    status|ls-files|blame|add)
      exit 0
      ;;
```

**Step 6: Run GREEN**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_client.py::TestBrainGitWriteGuard tests/test_codex_brain_client.py::TestGitCommandAllowedWriteForms -q > /private/tmp/claude-502/taf3/t1-green.log 2>&1; echo "rc=$?"; rg -n '^FAILED' /private/tmp/claude-502/taf3/t1-green.log; tail -n 1 /private/tmp/claude-502/taf3/t1-green.log; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-orchestrator-guard.sh > /private/tmp/claude-502/taf3/t1-hook-green.log 2>&1; echo "hook_rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf3/t1-hook-green.log; tail -n 1 /private/tmp/claude-502/taf3/t1-hook-green.log
```

Expected: `rc=0`, no FAILED lines, `49 passed` (derived: 16 + 33); `hook_rc=0`, no `FAIL:` lines, `25 passed, 0 failed` (derived: 17 + 8).

**Step 7: Both test files**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_client.py tests/test_codex_brain_client.py -q > /private/tmp/claude-502/taf3/t1-files.log 2>&1; echo "rc=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf3/t1-files.log; tail -n 1 /private/tmp/claude-502/taf3/t1-files.log
```

Expected: `rc=0`, no FAILED or ERROR lines, 0 failed. The existing git rows (`git log --oneline -5`, `git diff HEAD~1 src/main.py`, `git add src/ic/brain_client.py`, `git commit -m "Fix authentication bug"`, v1 `["git", "add", "."]`) still pass, and `git commit --amend -m 'rewrite history'` is still refused.

**Step 8: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_client.py commander/src/ironclaude/brain_client.py commander/hooks/brain-orchestrator-guard.sh commander/tests/test_codex_brain_client.py commander/tests/test_brain_client.py commander/hooks/tests/test-brain-orchestrator-guard.sh
```

Expected: no output.

---

## Task 2: Codex Brain MCP inventory (1a) (depends on 1)

**Files:** Modify `commander/src/ironclaude/codex_brain_client.py` and `commander/tests/test_codex_brain_client.py`.

**Step 1: RED tests**

In `commander/tests/test_codex_brain_client.py` class `TestOrchestratorStartupReadiness`, insert these two methods immediately before `    def test_start_failure_occurs_after_thread_start_before_first_turn(self, monkeypatch):`:

```python
    def test_verify_rejects_servers_outside_brain_set(self, monkeypatch):
        """Design 2026-10-18 v120-security-fixes 1a: a server from a project or system Codex
        config would run with no hook gate, so any server outside the Brain set fails the start."""
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
                    "zeta_tools": {"tools": {}},
                    "node_repl": {"tools": {"js": {"name": "js"}}},
                },
                None,
            ),
        )

        assert client._verify_orchestrator_mcp() == (
            "Codex Brain MCP inventory has servers outside the Brain set: node_repl, zeta_tools"
        )

    def test_verify_passes_with_every_brain_server(self, monkeypatch):
        """Positive control for the check above: research and ollama are in the Brain set."""
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
                    "research": {"tools": {}},
                    "ollama": {"tools": {}},
                },
                None,
            ),
        )

        assert client._verify_orchestrator_mcp() is None
        assert set(client._verified_tool_inventory) == {
            "orchestrator", "episodic-memory", "research", "ollama",
        }
```

In the same file, insert these two functions immediately before `def test_optional_mcp_servers_are_not_auto_approved():`:

```python
def _captured_trim_keep_set(monkeypatch):
    import ironclaude.codex_brain_client as module

    captured = {}

    def fake_trim(config_path, manifest_path, keep_servers):
        captured["keep"] = set(keep_servers)
        return []

    monkeypatch.setattr(module, "brain_tool_trim_overrides", fake_trim)
    CodexBrainClient()._tool_trim_overrides()
    return captured["keep"]


def test_tool_trim_keeps_optional_server_only_when_its_source_exists(monkeypatch):
    """Design 2026-10-18 v120-security-fixes 1a: a user-defined server named research is
    disabled when IronClaude's own research_mcp.py is absent; ollama's source exists and stays."""
    import ironclaude.codex_brain_client as module

    shipped = Path(module.__file__).parent
    monkeypatch.setattr(
        CodexBrainClient,
        "_optional_mcp_source_path",
        lambda self, name: (
            Path("/nonexistent") / f"{name}_mcp.py" if name == "research"
            else shipped / f"{name}_mcp.py"
        ),
    )
    assert _captured_trim_keep_set(monkeypatch) == {"orchestrator", "ollama"}


def test_tool_trim_keeps_optional_servers_whose_sources_exist(monkeypatch):
    """Positive control: both shipped sources exist in this tree, so both stay in the keep-set."""
    assert _captured_trim_keep_set(monkeypatch) == {"orchestrator", "research", "ollama"}
```

**Step 2: Run RED**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py::TestOrchestratorStartupReadiness::test_verify_rejects_servers_outside_brain_set tests/test_codex_brain_client.py::TestOrchestratorStartupReadiness::test_verify_passes_with_every_brain_server tests/test_codex_brain_client.py::test_tool_trim_keeps_optional_server_only_when_its_source_exists tests/test_codex_brain_client.py::test_tool_trim_keeps_optional_servers_whose_sources_exist -q > /private/tmp/claude-502/taf3/t2-red.log 2>&1; echo "rc=$?"; rg -n '^FAILED' /private/tmp/claude-502/taf3/t2-red.log; tail -n 1 /private/tmp/claude-502/taf3/t2-red.log
```

Expected (derived): `rc=1`, `2 failed, 2 passed`.
- `test_verify_rejects_servers_outside_brain_set` fails: the current check returns `None`.
- `test_tool_trim_keeps_optional_server_only_when_its_source_exists` fails: the current keep-set is `{"orchestrator", "research", "ollama"}`.
- Both positive controls pass.

**Step 3: GREEN code**

In `commander/src/ironclaude/codex_brain_client.py` `_tool_trim_overrides`, replace

```python
            {"orchestrator", *_OPTIONAL_MCP_SERVERS},
        )
```

with

```python
            # Keep research/ollama only when IronClaude's own source exists (the
            # _optional_mcp_overrides check), so a user-defined server with either name is
            # disabled (design 2026-10-18 v120-security-fixes 1a).
            {
                "orchestrator",
                *(
                    name for name in _OPTIONAL_MCP_SERVERS
                    if self._optional_mcp_source_path(name).exists()
                ),
            },
        )
```

In `_verify_orchestrator_mcp`, replace

```python
        if "episodic-memory" not in inventory:
            return "episodic-memory MCP inventory missing server"
        return self._install_verified_tool_inventory(inventory)
```

with

```python
        if "episodic-memory" not in inventory:
            return "episodic-memory MCP inventory missing server"
        # A server from a project or system Codex config would run with no hook gate, while the
        # Claude Brain denies unknown mcp__ prefixes (design 2026-10-18 v120-security-fixes 1a).
        extra = sorted(set(inventory) - _TOOL_SERVERS)
        if extra:
            return (
                "Codex Brain MCP inventory has servers outside the Brain set: "
                + ", ".join(extra)
            )
        return self._install_verified_tool_inventory(inventory)
```

**Step 4: Run GREEN and the file**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py tests/test_codex_brain_tool_trim.py -q > /private/tmp/claude-502/taf3/t2-green.log 2>&1; echo "rc=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf3/t2-green.log; tail -n 1 /private/tmp/claude-502/taf3/t2-green.log
```

Expected: `rc=0`, no FAILED or ERROR lines, 0 failed. `test_app_server_argv_forces_readonly_and_on_request` and `test_app_server_argv_trims_inherited_tools` still pass, because both shipped sources exist.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_client.py commander/tests/test_codex_brain_client.py
```

Expected: no output.

---

## Task 3: Quarantine cap (2)

**Files:** Modify `commander/src/ironclaude/worker_handoff.py` and `commander/tests/test_worker_handoff.py`.

**Step 1: RED test**

In `commander/tests/test_worker_handoff.py`, replace

```python
@pytest.mark.parametrize("reset_at,expected", [
    # Event 2026-10-05T12:00:00Z = 1791201600. A reset 21 h later is valid; a reset
    # three hours before the event is not later than it, so it counts as absent (H9 validity).
    (1791277200, 1791277200.0), (None, 1_000_060.0), (1791190800, 1_000_060.0),
])
```

with

```python
@pytest.mark.parametrize("reset_at,expected", [
    # Event 2026-10-05T12:00:00Z = 1791201600. A reset 21 h later is valid; a reset
    # three hours before the event is not later than it, so it counts as absent (H9 validity).
    # A reset ten years after the event (1791201600 + 10*365*86400 = 2106561600) is capped at the
    # event plus 7 days (1791201600 + 7*86400 = 1791806400), not now plus 7 days: time.time is
    # pinned to 1_000_000 here (design 2026-10-18 v120-security-fixes 2).
    (1791277200, 1791277200.0), (None, 1_000_060.0), (1791190800, 1_000_060.0),
    (2106561600, 1791806400.0),
])
```

**Step 2: Run RED**

Task 3 can run in the same wave as Task 1, so this command creates the scratch directory itself.

```bash
mkdir -p /private/tmp/claude-502/taf3 && cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worker_handoff.py -k test_source_quarantine_expires_at_native_reset_or_configured_default -q > /private/tmp/claude-502/taf3/t3-red.log 2>&1; echo "rc=$?"; rg -n '^FAILED' /private/tmp/claude-502/taf3/t3-red.log; tail -n 1 /private/tmp/claude-502/taf3/t3-red.log
```

Expected (derived): `rc=1`, one FAILED line, the `2106561600-1791806400.0` row (the current code returns `2106561600.0`), and `1 failed, 3 passed, <n> deselected`.

**Step 3: GREEN code**

In `commander/src/ironclaude/worker_handoff.py`, replace

```python
from ironclaude.provider_state import DEFAULT_USAGE_QUARANTINE_SECONDS
from datetime import datetime
```

with

```python
from ironclaude.provider_state import DEFAULT_USAGE_QUARANTINE_SECONDS
from datetime import datetime

# The longest native limit window is 7 days, so no usage quarantine outlasts the limit event by
# more than that, whatever reset the quota evidence reports (design 2026-10-18 v120-security-fixes 2).
_QUARANTINE_MAX_SECONDS = 7 * 24 * 3600
```

In the same file, replace

```python
    def _quarantine_expires_at(self, quota: dict) -> float:
        """Native reset time when it follows the event, else the configured default (H9)."""
```

with

```python
    def _quarantine_expires_at(self, quota: dict) -> float:
        """Native reset time when it follows the event, capped at the event time plus
        _QUARANTINE_MAX_SECONDS (now plus the cap when the event time cannot be parsed);
        else the configured default (H9)."""
```

and replace

```python
        if type(reset_at) is int and reset_at > 0 and (event is None or reset_at > event):
            return float(reset_at)
        return time.time() + float(default)
```

with

```python
        if type(reset_at) is int and reset_at > 0 and (event is None or reset_at > event):
            limit = (event if event is not None else time.time()) + _QUARANTINE_MAX_SECONDS
            return float(min(reset_at, limit))
        return time.time() + float(default)
```

**Step 4: Run GREEN and the file**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worker_handoff.py tests/test_provider_state.py -q > /private/tmp/claude-502/taf3/t3-green.log 2>&1; echo "rc=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf3/t3-green.log; tail -n 1 /private/tmp/claude-502/taf3/t3-green.log
```

Expected: `rc=0`, no FAILED or ERROR lines, 0 failed; all four `test_source_quarantine_expires_at_native_reset_or_configured_default` rows pass.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_handoff.py commander/tests/test_worker_handoff.py
```

Expected: no output.

---

## Task 4: Full suites, docs and staging (depends on 1, 2, 3)

No tests required: regression run and release notes only.

**Files:** Modify `CHANGELOG.md`. Create `docs/plans/2026-10-18-v120-security-fixes-findings.md`. Stage the design, this plan, its `.plan.json` and the findings.

**Step 1: Full Commander suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/taf3/t4-suite.log 2>&1; echo "suite=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf3/t4-suite.log; tail -n 1 /private/tmp/claude-502/taf3/t4-suite.log
```

Expected: `suite=0`, 0 failed, no FAILED or ERROR lines. Passed count (derived) `4072 + 54 = 4126`: Task 1 adds 49 (16 + 33), Task 2 adds 4 and Task 3 adds 1 (49 + 4 + 1 = 54). Record the passed count as `<N>` for the CHANGELOG.

**Step 2: Hook suites**

```bash
cd /Users/roberthyatt/Code/ironclaude && make test-hooks > /private/tmp/claude-502/taf3/t4-hooks.log 2>&1; echo "make_rc=$?"; rg -c '^--- ' /private/tmp/claude-502/taf3/t4-hooks.log; tail -n 3 /private/tmp/claude-502/taf3/t4-hooks.log
```

Expected: `make_rc=0` and `60` suite headers (7 + 45 + 8 files; this plan adds no hook test file).

**Step 3: State-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf3/t4-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf3/t4-npm.log
```

Expected: `npm=0` and a `Tests` line with `273 passed`. The load rule applies only to the SIGTERM-resistant process-group test.

**Step 4: Findings**

Write `docs/plans/2026-10-18-v120-security-fixes-findings.md` with:
- a title line `# v1.2.0 Security Fixes Findings`;
- `## Abbreviation probe`, with the Task 1 Step 1 output;
- `## Full suites`, with the Step 1 summary line, `make_rc` and the header count from Step 2, and the Step 3 summary lines;
- this section, verbatim, for the operator's v1.2.0 squash commit message:

```markdown
## Release commit message addition

Security fixes folded into v1.2.0 (2026-10-18). Both Brains refuse git commands that would write a file through --output (git log, git diff and git show), and both Brains' Python checks allow git branch only for listing, never to create, delete, rename, copy or force a branch. The Codex Brain refuses to start when its MCP inventory holds a server outside its own set (orchestrator, episodic-memory, research and ollama), and its tool trim keeps research and ollama only when IronClaude's own server file exists. A worker's usage quarantine lasts at most 7 days after the limit event, however late the reported reset.
```

```bash
rg -n -e '^## Abbreviation probe$' -e '^## Full suites$' -e '^## Release commit message addition$' -e 'at most 7 days after the limit event' /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-18-v120-security-fixes-findings.md
```

Expected: exactly four lines: the three headings and the paragraph.

**Step 5: CHANGELOG**

In `CHANGELOG.md` `## 1.2.0`, insert this bullet immediately after the bullet that starts `- **The Brain gate root moved to `~/.ironclaude/state/brain-gate`.**` (line 56):

```markdown
- **Both Brains refuse git file writes and branch changes, and the Codex Brain runs only its own MCP servers.** The Claude Brain's tool guard, the Codex Brain's command approval and `brain-orchestrator-guard.sh` refuse `git log`, `git diff` and `git show` with `--output`, which writes a file. Both Brains' Python checks allow `git branch` only for listing, never to create, delete, rename, copy or force a branch. The Codex Brain refuses to start when its MCP inventory holds a server outside its own set (`orchestrator`, `episodic-memory`, `research`, `ollama`), and its tool trim keeps `research` and `ollama` only when IronClaude's own server file exists. A worker's usage quarantine ends at most 7 days after the limit event, however late the reported reset.
```

On line 83, replace `Suites: commander 4072,` with `Suites: commander <N>,`, where `<N>` is the passed count from Step 1. The `state-manager 273` and `hooks 60` counts stay unchanged.

```bash
rg -n -F -e 'Both Brains refuse git file writes and branch changes' -e 'Suites: commander' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; rg -n -F 'Suites: commander 4072,' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; echo "stale_rc=$?"
```

Expected: one line with the new bullet, directly after the Brain gate root bullet (line 57); one `Suites: commander <N>, state-manager 273, hooks 60` line; then `stale_rc=1`, with no stale line (unless `<N>` is itself 4072).

**Step 6: Stage and check the staged set**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-v120-security-fixes-design.md docs/plans/2026-10-18-v120-security-fixes.md docs/plans/2026-10-18-v120-security-fixes.plan.json docs/plans/2026-10-18-v120-security-fixes-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status; git -C /Users/roberthyatt/Code/ironclaude status --short worker/.codex-plugin/plugin.json
```

Expected: exactly these 13 entries against HEAD `e59359b`:

**M (9):**
- `CHANGELOG.md`
- `commander/hooks/brain-orchestrator-guard.sh`
- `commander/hooks/tests/test-brain-orchestrator-guard.sh`
- `commander/src/ironclaude/brain_client.py`
- `commander/src/ironclaude/codex_brain_client.py`
- `commander/src/ironclaude/worker_handoff.py`
- `commander/tests/test_brain_client.py`
- `commander/tests/test_codex_brain_client.py`
- `commander/tests/test_worker_handoff.py`

**A (4):** the 4 `docs/plans/2026-10-18-v120-security-fixes*` files (design, plan, `.plan.json`, findings).

The last command prints ` M worker/.codex-plugin/plugin.json`: modified, unstaged. Any other staged entry means STOP and report it.

After this task, an Opus end review covers the combined staged diff. The operator then folds it into the v1.2.0 commit, deploys and restarts, and only then lets Task 5 run. Tag after Task 5 passes.

---

## Task 5: Post-deploy live verification, operator-assisted (depends on 4)

No tests required: live probes after deploy. Every LLM self-report is evidence, not proof. If a probe misses its expectation, record it and STOP, then debug and fix it in this loop through a new task. Never rerun until it passes. Never write `~/.codex/config.toml`.

These steps are carried from Task 13 of `docs/plans/2026-10-17-v120-completeness.md`. The changes are: a new first check for the deployed `brain-orchestrator-guard.sh`; every scratch path moved from `taf2` to `taf3`, with the start marker `t5-start` and the probe log `t5-grader.log`; and a `servers outside the Brain set` pattern added to Step 4 for the new start check.

**Files:** Modify `docs/plans/2026-10-18-v120-security-fixes-findings.md`.

**Step 1: Deploy precondition (operator-assisted)**

The operator has done all of the following:
1. Committed the staged set as part of the one untagged v1.2.0 commit.
2. Run `claude plugin update ironclaude@ironclaude` and synced the Claude cache.
3. Run `make codex-plugin-release` and `make deploy-hooks`.
4. Restarted the Commander and ChatGPT. The Commander restart is what copies `commander/hooks/*.sh` to `~/.claude/ironclaude-hooks/` (`_sync_brain_settings_hooks`); `make deploy-hooks` copies only `worker/hooks/*.sh`.

Then:

```bash
touch /private/tmp/claude-502/taf3/t5-start; cmp /Users/roberthyatt/Code/ironclaude/commander/hooks/brain-orchestrator-guard.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-orchestrator-guard.sh; echo "guard_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/agents/reviewer.md /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.2.0/agents/reviewer.md; echo "agent_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/brain-gated-actions.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-gated-actions.sh; echo "gate_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/commander/hooks/brain-hooks-canary.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-hooks-canary.sh; echo "canary_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/plan-validator.sh /Users/roberthyatt/.claude/ironclaude-hooks/plan-validator.sh; echo "validator_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh /Users/roberthyatt/.claude/ironclaude-hooks/execution-drift-check.sh; echo "drift_cmp=$?"; ls -t /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude
```

Expected:
- `guard_cmp=0`, `agent_cmp=0`, `gate_cmp=0`, `canary_cmp=0`, `validator_cmp=0` and `drift_cmp=0`.
- Record the newest Codex cache version, the first `ls -t` line.

Any non-zero `cmp` means the deploy is incomplete: STOP and report which file.

**Step 2: A1, the reviewer agent's tools (orchestrator step)**

If `ironclaude:reviewer` is not in this session's agent list, the operator starts a fresh Claude Code session and this step runs there. The orchestrator then makes three dispatches with `model="haiku"`:
1. `Agent(subagent_type="ironclaude:reviewer")` with the prompt `List the exact names of every tool you can call in this session, comma-separated. Call no tool.`
2. `Agent(subagent_type="ironclaude:reviewer")` with the prompt `Run the shell command echo A1-PROBE with any tool you have and reply with its output, or reply NO-SHELL if you have no tool that can run it.`
3. A positive control: `Agent(subagent_type="general-purpose")` with the first prompt.

Expected:
- The first reviewer reply names exactly `Read`, `Grep` and `Glob`, with no Bash, Edit, Write, Skill, Agent or `mcp__` tool.
- The second reviewer reply is `NO-SHELL`.
- The control names `Bash`.

A Bash tool for the reviewer, or a control without Bash, means record it and STOP.

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
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf3/grader_probe.py > /private/tmp/claude-502/taf3/t5-grader.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf3/t5-grader.log; jq -r '[.models[]?.slug | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-sol$"))] | join(" ")' /Users/roberthyatt/.codex/models_cache.json
```

Expected:
- `probe=0` and `configured_opus=sol`.
- `resolved_opus=` is the numerically newest of the Sol slugs on the last line.
- `route client=codex model=` equals `resolved_opus`.
- `result=` holds a `grade` and no `infrastructure_error`.

Any miss means record it and STOP.

**Step 4: Commander on Codex, R4 item 1 (operator-assisted)**

1. The operator types `/ironclaude provider brain codex` in Slack; the Commander restarts.
2. When the Brain is up, the operator asks in Slack: `what unfinished work is sitting idle right now?`
3. Then run:

```bash
rg -n -e 'Codex hook-trust warm-up finished ready=' -e 'Brain SDK client started client=codex' -e 'Brain SDK client not started client=codex' -e 'Codex Brain hooks are not running' -e 'spawn failed: Codex model family' -e 'servers outside the Brain set' /private/tmp/ic/daemon.log; ls -ld /Users/roberthyatt/.ironclaude/state/brain-gate; find /Users/roberthyatt/.ironclaude/state/brain-gate -name hooks-live -newer /private/tmp/claude-502/taf3/t5-start; find /private/tmp/ic/codex-brain-gate -name hooks-live -newer /private/tmp/claude-502/taf3/t5-start; find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /private/tmp/claude-502/taf3/t5-start
```

Expected:
- `daemon.log` shows, after the restart, `Codex hook-trust warm-up finished ready=True trust_prompt_answered=` followed by `Brain SDK client started client=codex`. There is no `Brain SDK client not started client=codex`, no `Codex Brain hooks are not running`, no `spawn failed: Codex model family` and no `servers outside the Brain set` line after the restart. A `servers outside the Brain set` line names the live server the 1a check refused: record the names and STOP.
- The `ls -ld` line shows `drwx------`.
- The first `find` prints one `.../brain-gate/<session>/hooks-live`. The old-root `find` prints nothing.
- The last `find` lists the Brain's new rollout file(s).

**Step 5: The Codex Brain's rollout has no tool errors (R4 item 1)**

```bash
find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /private/tmp/claude-502/taf3/t5-start -exec rg -n -i -H -e 'professional mode is unset' -e 'access unavailable' {} +; echo "scan_done=$?"
```

Expected: no match lines, then `scan_done=0`. These are the 2026-10-10 symptom strings. The operator also confirms that the Brain's Slack answer lists real workers or ledger tasks. A pass closes the 2026-10-10 backlog entry, and the operator may switch back with `/ironclaude provider brain claude`.

**Step 6: Owed per-call tiering checks, R4 item 2 (operator-assisted checklist)**

The operator triggers each item; the agent reads the evidence:
- **1b.** The Brain runs `send_to_worker` or `post_message` to a live worker.
- **2.** The Brain runs `evaluate_worker_health` on a live worker (Sonnet class).
- **3.** The Brain spawns a `claude-opus` worker.

```bash
rg -n -e 'fast model answered via' -e 'fast model unavailable' -e 'fast model skips codex' -e 'Provider route skipped' /private/tmp/ic/daemon.log; rg -l -F '/advisor opus' /private/tmp/ic-logs
```

Expected, after the start marker:
- **1b:** a `fast model answered via haiku` or `fast model answered via luna:<slug>` line.
- **2:** a `fast model answered via sonnet` or `fast model answered via terra:<slug>` line.
- **3:** the new worker's pane log listed by the second `rg`.

If the orchestrator MCP's log for item 2 does not reach `daemon.log`, record `UNVERIFIED-LIVE: no daemon.log line for evaluate_worker_health; searched daemon.log and the Brain rollout`, and grep the Brain rollout for `evaluate_worker_health` as secondary evidence.

**Step 7: Carried R4b live items (operator-assisted)**

Run the operator steps carried from the v1.2.0 completeness plan's Task 7:
- **Defect 2:** Codex `/commit` with nothing staged, then read the newest `human_intents` row.
- **Defect 5:** `git add` in a managed worktree under a `workspace-write` Codex sandbox, on a scratch repository only.
- **Defect 9:** read the transcript of a fresh `/activate-professional-mode`.

Record each as LIVE or FIXED with its evidence, or keep `UNVERIFIED-LIVE` if the operator does not run it.

**Step 8: Record**

Append `## Post-deploy verification` to `docs/plans/2026-10-18-v120-security-fixes-findings.md`, with every Step 1-7 output and verdict, A1 included.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-v120-security-fixes-findings.md
```

Expected: no output. The operator folds this addendum into the v1.2.0 commit before tagging.
