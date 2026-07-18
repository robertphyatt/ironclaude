# Boy Scout Rule and Hermetic Test Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Add a scope-aware Boy Scout Rule to every IronClaude behavioral-instruction path, make the five restricted-runner tests hermetic, and document direct OpenAI Codex support without implying Commander support.

**Architecture:** Treat the Boy Scout Rule as one semantic concept propagated through current instructions, generated worker instructions, professional-mode activation, and Brain instructions. Replace live tmux and TCP resources in tests with existing dependency seams and an in-memory socket pair; do not change production runtime code or weaken tests into skips.

**Tech Stack:** Markdown instruction files, Python 3.11, pytest 8, `unittest.mock`, Python `socket.socketpair`, Git.

---

## Task 1: Propagate and guard the Boy Scout Rule

**Files:**
- Create: `commander/tests/test_boy_scout_directive.py`
- Modify: `CLAUDE.md:50-55`
- Modify: `worker/CLAUDE.md:71-74`
- Modify: `commander/CLAUDE.md:67-71`
- Modify: `.claude/rules/behavioral.md:74-79`
- Modify: `commander/src/ironclaude/templates/worker_claude_md.md:67-71`
- Modify: `worker/skills/activate-professional-mode/SKILL.md:45-262`
- Modify: `commander/src/brain/rules/behavioral.md:40-72`
- Modify: `commander/src/brain/orchestrator_claude.md:5-10`
- Modify: `commander/tests/test_worker_claude_md_template.py:18-36`
- Modify: `commander/tests/test_advisor_fallback_directive.py:28-46`

### Step 1: Write the propagation guard (RED)

Create `commander/tests/test_boy_scout_directive.py`:

```python
"""Presence guard for the scope-aware Boy Scout Rule on every instruction surface."""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = "Boy Scout Rule"
DIRECTIVE_BODY = """\
    - Never dismiss an evidence-backed defect because it is pre-existing, adjacent, or outside the immediate change
    - If cleanup is safe, relevant, and within the authorized task scope, fix it through the active workflow and verify the result
    - If cleanup would materially expand scope, change behavior, require destructive action, affect external systems, or require new authority, describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding
    - If cleanup is blocked or unsafe, record the finding and explain the constraint instead of suppressing it
    - Do not use this rule to justify speculative refactoring or unrequested features"""
COMPACT_DIRECTIVE = (
    "11. **Boy Scout Rule** — Never dismiss an evidence-backed defect because it is pre-existing. "
    "Clean it up when it is safe, relevant, and within authorized task scope; otherwise describe "
    "the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding."
)
DETECTION_ROW = (
    "| 11 | Boy Scout Rule | Instructions not to ignore evidence-backed pre-existing or adjacent "
    "defects; clean them up when within authorized task scope, ask permission before scope expansion, "
    "destructive action, or external-system effects after presenting finding/evidence/scope/risk, and "
    "record blocked or unsafe findings instead of suppressing them |"
)
BRAIN_DIRECTIVE = (
    "24. **Boy Scout Rule — Leave It Better Than You Found It** — Never dismiss an evidence-backed "
    "defect because it is pre-existing or adjacent. If cleanup is safe, relevant, and within the "
    "confirmed directive, spawn or guide a worker through the full PM workflow and verify it. If "
    "cleanup expands the directive, changes behavior, is destructive, affects an external system, "
    "or requires new authority, describe the finding, evidence, proposed cleanup scope, and risk, "
    "then request operator permission before proceeding. If cleanup is blocked or unsafe, record "
    "the finding and constraint instead of suppressing it. Do not use this rule for speculative "
    "refactoring or unrequested features."
)
BRAIN_FIX_BUGS = """\
16. **Fix Bugs Immediately — Don't Just Report Them**
   - When you discover a bug within a confirmed directive, determine the architecture-appropriate fix, spawn a worker, and get it done
   - Tell the operator what you found and what you're doing about it
   - If the fix falls outside the confirmed directive, follow the Boy Scout Rule: describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before expanding scope"""
BRAIN_FIX_BEFORE_REPORTING = (
    "20. **Fix Before Reporting** — Within a confirmed directive, attempt the fix before reporting "
    "(spawn a worker, run an authorized command, or restart an in-scope service). If the action falls "
    "outside the confirmed directive, is destructive, affects an external system, or otherwise requires "
    "new authority, pin a decision-format permission request describing the finding, evidence, proposed "
    "cleanup scope, and risk before acting. Never silently suppress a blocked or unsafe finding."
)

DIRECT_SURFACES = [
    "CLAUDE.md",
    "worker/CLAUDE.md",
    "commander/CLAUDE.md",
    ".claude/rules/behavioral.md",
    "commander/src/ironclaude/templates/worker_claude_md.md",
]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


def test_directive_in_every_direct_behavioral_surface():
    for relative_path in DIRECT_SURFACES:
        text = _read(relative_path)
        assert MARKER in text, f"Missing {MARKER} in {relative_path}"
        assert DIRECTIVE_BODY in text, f"Incomplete {MARKER} in {relative_path}"


def test_directive_in_brain_surfaces():
    behavioral = _read("commander/src/brain/rules/behavioral.md")
    identity = _read("commander/src/brain/orchestrator_claude.md")
    assert BRAIN_FIX_BUGS in behavioral
    assert BRAIN_FIX_BEFORE_REPORTING in behavioral
    assert BRAIN_DIRECTIVE in behavioral
    assert MARKER in identity
    assert "18 behavioral directives" not in identity


def test_activation_skill_propagates_all_boy_scout_paths():
    text = _read("worker/skills/activate-professional-mode/SKILL.md")
    assert COMPACT_DIRECTIVE in text
    assert text.count(DIRECTIVE_BODY) == 2
    assert DETECTION_ROW in text
    assert "Concept 11 (Boy Scout Rule):" in text
    assert "11 concepts" in text
    assert "(11 principles)" in text
    assert "11-principle template" in text
```

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests/test_boy_scout_directive.py -q
```

Expected: FAIL because the directive and activation propagation paths do not exist yet.

### Step 2: Add the canonical scope-aware directive to direct surfaces

Append this block at the next numbered position in each direct surface (number 10 in `CLAUDE.md`, 13 in `worker/CLAUDE.md`, 11 in `commander/CLAUDE.md`, 12 in `.claude/rules/behavioral.md`, and 12 in `worker_claude_md.md`):

```markdown
N. **Boy Scout Rule — Leave It Better Than You Found It**
    - Never dismiss an evidence-backed defect because it is pre-existing, adjacent, or outside the immediate change
    - If cleanup is safe, relevant, and within the authorized task scope, fix it through the active workflow and verify the result
    - If cleanup would materially expand scope, change behavior, require destructive action, affect external systems, or require new authority, describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding
    - If cleanup is blocked or unsafe, record the finding and explain the constraint instead of suppressing it
    - Do not use this rule to justify speculative refactoring or unrequested features
```

Keep each file's existing ordering and wording unchanged outside the insertion.

### Step 3: Add all professional-mode activation propagation paths

In `worker/skills/activate-professional-mode/SKILL.md`:

1. Add compact directive 11 after compact directive 10:

```markdown
11. **Boy Scout Rule** — Never dismiss an evidence-backed defect because it is pre-existing. Clean it up when it is safe, relevant, and within authorized task scope; otherwise describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding.
```

2. Add full-template directive 11 using the canonical five-bullet block from Step 2.
3. Change the single `(10 principles)` occurrence to `(11 principles)`.
4. Change the single `10 concepts` occurrence to `11 concepts`.
5. Change every `10-principle template` occurrence to `11-principle template`.
6. Add semantic-detection row:

```markdown
| 11 | Boy Scout Rule | Instructions not to ignore evidence-backed pre-existing or adjacent defects; clean them up when within authorized task scope, ask permission before scope expansion, destructive action, or external-system effects after presenting finding/evidence/scope/risk, and record blocked or unsafe findings instead of suppressing them |
```

7. Add canonical append block after Concept 10:

```markdown
Concept 11 (Boy Scout Rule):
```
```markdown
N. **Boy Scout Rule — Leave It Better Than You Found It**
    - Never dismiss an evidence-backed defect because it is pre-existing, adjacent, or outside the immediate change
    - If cleanup is safe, relevant, and within the authorized task scope, fix it through the active workflow and verify the result
    - If cleanup would materially expand scope, change behavior, require destructive action, affect external systems, or require new authority, describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding
    - If cleanup is blocked or unsafe, record the finding and explain the constraint instead of suppressing it
    - Do not use this rule to justify speculative refactoring or unrequested features
```

### Step 4: Align Brain behavior and remove the stale directive count

In `commander/src/brain/rules/behavioral.md`, amend directive 16 so it no longer claims authority outside a confirmed directive:

```markdown
16. **Fix Bugs Immediately — Don't Just Report Them**
   - When you discover a bug within a confirmed directive, determine the architecture-appropriate fix, spawn a worker, and get it done
   - Tell the operator what you found and what you're doing about it
   - If the fix falls outside the confirmed directive, follow the Boy Scout Rule: describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before expanding scope
```

Replace directive 20 with:

```markdown
20. **Fix Before Reporting** — Within a confirmed directive, attempt the fix before reporting (spawn a worker, run an authorized command, or restart an in-scope service). If the action falls outside the confirmed directive, is destructive, affects an external system, or otherwise requires new authority, pin a decision-format permission request describing the finding, evidence, proposed cleanup scope, and risk before acting. Never silently suppress a blocked or unsafe finding.
```

Append Brain directive 24:

```markdown
24. **Boy Scout Rule — Leave It Better Than You Found It** — Never dismiss an evidence-backed defect because it is pre-existing or adjacent. If cleanup is safe, relevant, and within the confirmed directive, spawn or guide a worker through the full PM workflow and verify it. If cleanup expands the directive, changes behavior, is destructive, affects an external system, or requires new authority, describe the finding, evidence, proposed cleanup scope, and risk, then request operator permission before proceeding. If cleanup is blocked or unsafe, record the finding and constraint instead of suppressing it. Do not use this rule for speculative refactoring or unrequested features.
```

In `commander/src/brain/orchestrator_claude.md`, replace the stale counted description with:

```markdown
- `behavioral.md` — behavioral directives (challenge plans, no sycophancy, scientific debugging, Boy Scout Rule cleanup, large-file decomposition)
```

### Step 5: Update the worker-template inventory test

Rename `test_template_contains_all_nine_directives` to `test_template_contains_all_twelve_directives`, update its docstring to say 12, and replace `expected` with:

```python
expected = [
    "Challenge Assumptions",
    "Verify with Evidence",
    "Refuse Impossible Requests",
    "Persistent Questioning",
    "No Premature Optimization",
    "Search Before Guessing",
    "Subagent Discipline",
    "No Sycophantic Responses",
    "Handle Large Files with Decomposition",
    "Compressed Output",
    "Right-Size Every Subagent",
    "Boy Scout Rule",
]
```

In `commander/tests/test_advisor_fallback_directive.py`, update its count pins and comments from 10 to 11 concepts/principles/templates. Do not alter Advisor Fallback semantics.

### Step 6: Run focused propagation tests (GREEN)

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests/test_boy_scout_directive.py commander/tests/test_worker_claude_md_template.py commander/tests/test_advisor_fallback_directive.py -q
```

Expected: all tests pass.

### Step 7: Stage Task 1 files

Run:

```bash
git add CLAUDE.md worker/CLAUDE.md commander/CLAUDE.md .claude/rules/behavioral.md commander/src/ironclaude/templates/worker_claude_md.md worker/skills/activate-professional-mode/SKILL.md commander/src/brain/rules/behavioral.md commander/src/brain/orchestrator_claude.md commander/tests/test_boy_scout_directive.py commander/tests/test_worker_claude_md_template.py commander/tests/test_advisor_fallback_directive.py
```

Expected: only Task 1 files are newly staged; `AGENTS.md` remains untracked.

---

## Task 2: Replace privileged test resources with hermetic seams

**Files:**
- Modify: `commander/tests/test_kill_orphan_workers.py:1-81`
- Modify: `commander/tests/test_wiki_server.py:31-52`

### Step 1: Reproduce the restricted-runner failures (RED)

Run with the absolute interpreter path so the command stays in the restricted runner:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests/test_kill_orphan_workers.py commander/tests/test_wiki_server.py::test_wiki_prefix_no_slash_redirects -q
```

Expected: 5 failures—four tmux `Operation not permitted` errors and one `socket.bind()` `PermissionError`.

### Step 2: Replace live tmux integration with deterministic contract tests

Replace `commander/tests/test_kill_orphan_workers.py` with:

```python
"""Hermetic contract tests for _kill_orphan_workers selection behavior."""
from unittest.mock import MagicMock

from ironclaude.main import _kill_orphan_workers
from ironclaude.tmux_manager import TmuxManager
from ironclaude.worker_registry import WorkerRegistry


def _dependencies(sessions: list[str], registered: tuple[str, ...] = ()):
    tmux = MagicMock(spec=TmuxManager)
    tmux.list_sessions.return_value = sessions
    registry = MagicMock(spec=WorkerRegistry)
    registry.get_running_workers.return_value = [
        {"tmux_session": name} for name in registered
    ]
    return tmux, registry


class TestKillOrphanWorkers:
    def test_kills_unregistered_session(self):
        tmux, registry = _dependencies(["ic-test-orphan-unregistered"])

        _kill_orphan_workers(tmux, registry)

        tmux.list_sessions.assert_called_once_with(prefix="ic-")
        tmux.kill_session.assert_called_once_with("ic-test-orphan-unregistered")

    def test_preserves_registered_session(self):
        name = "ic-test-orphan-registered"
        tmux, registry = _dependencies([name], registered=(name,))

        _kill_orphan_workers(tmux, registry)

        tmux.kill_session.assert_not_called()

    def test_preserves_brain_session(self):
        tmux, registry = _dependencies(["ic-brain"])

        _kill_orphan_workers(tmux, registry)

        tmux.kill_session.assert_not_called()

    def test_requests_only_ic_prefix_sessions(self):
        tmux, registry = _dependencies([])

        _kill_orphan_workers(tmux, registry)

        tmux.list_sessions.assert_called_once_with(prefix="ic-")
        tmux.kill_session.assert_not_called()
```

### Step 3: Replace the TCP listener with a socket-pair request harness

Replace `test_wiki_prefix_no_slash_redirects` in `commander/tests/test_wiki_server.py` with:

```python
def test_wiki_prefix_no_slash_redirects():
    """GET /wiki returns 301 to /wiki/ without binding a network port."""
    import socket
    from types import SimpleNamespace

    import wiki_server

    client, handler_socket = socket.socketpair()
    try:
        client.sendall(
            b"GET /wiki HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n\r\n"
        )
        client.shutdown(socket.SHUT_WR)
        server = SimpleNamespace(server_name="localhost", server_port=80)
        wiki_server.WikiHandler(handler_socket, ("local", 0), server)
        handler_socket.shutdown(socket.SHUT_WR)

        response = b""
        while chunk := client.recv(4096):
            response += chunk
    finally:
        client.close()
        handler_socket.close()

    headers = response.decode("iso-8859-1").split("\r\n\r\n", 1)[0]
    assert headers.startswith("HTTP/1.0 301 ")
    assert "\r\nLocation: /wiki/\r\n" in f"\r\n{headers}\r\n"
```

### Step 4: Run the same restricted command (GREEN)

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests/test_kill_orphan_workers.py commander/tests/test_wiki_server.py::test_wiki_prefix_no_slash_redirects -q
```

Expected: `5 passed`; no tmux socket access and no TCP bind attempt.

### Step 5: Run both affected test files

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests/test_kill_orphan_workers.py commander/tests/test_wiki_server.py -q
```

Expected: `7 passed`.

### Step 6: Stage Task 2 files

Run:

```bash
git add commander/tests/test_kill_orphan_workers.py commander/tests/test_wiki_server.py
```

Expected: only the two hermetic test files are newly staged.

---

## Task 3: Document compatibility, verify the release, and prepare the amend

**Files:**
- Modify: `README.md:8-126`
- Modify: `CHANGELOG.md:16-39`

**No new executable-code tests required:** this task changes release documentation only. Existing version, plugin, hook, propagation, and full-suite checks validate the integrated release.

### Step 1: Make the direct OpenAI Codex compatibility boundary explicit

In `README.md`:

- Replace the first summary lines with:

```markdown
**Workflow discipline and multi-agent orchestration for Claude Code and OpenAI Codex.**

IronClaude adds workflow discipline to Claude Code and OpenAI Codex, plus multi-session Claude Code orchestration.
```

- Replace the Worker component lead with:

```markdown
- **Worker** -- A Claude Code and OpenAI Codex plugin that enforces disciplined development workflows (brainstorm, plan, execute) with review gates between every task
```
- Add this callout immediately after the Worker/Commander component list:

```markdown
> **Compatibility:** Direct Worker mode supports Claude Code and OpenAI Codex. Commander currently orchestrates Claude Code sessions only; Codex-backed Commander workers are not yet supported.
```

- Preserve the existing Worker quick-start heading so its published Markdown anchor remains stable. Replace its first paragraph with:

```markdown
The Worker enforces the brainstorm-plan-execute workflow on every code change in Claude Code or OpenAI Codex. Install it and activate professional mode to get disciplined single-session development.
```
- Keep Commander descriptions Claude Code-only.
- Add these v1.0.24 bullets:

```markdown
- New scope-aware Boy Scout Rule makes pre-existing defects actionable: clean them up when authorized, or present the finding, evidence, cleanup scope, and risk and ask permission before expanding scope.
- Commander tests no longer touch the operator's live tmux server or bind a TCP listener, so the complete suite runs hermetically in restricted environments without capability skips.
```

### Step 2: Update the v1.0.24 changelog entry

In `CHANGELOG.md`:

- Replace the v1.0.24 summary with:

```markdown
Teaches the Worker that plan/design/task-state artifacts on disk are already durable, adds native direct-mode OpenAI Codex packaging, introduces scope-aware Boy Scout cleanup guidance, makes restricted-runner tests hermetic, and hardens Commander Slack interactions around account switching and operator-decision links.
```
- Under `Added`, document the new directive and all propagation surfaces.
- Under `Changed`, state explicitly that direct Worker mode supports Claude Code and OpenAI Codex while Commander remains Claude Code-only.
- Under `Fixed`, document the four live-tmux tests and one TCP-binding test becoming hermetic without skips.

Use these exact entries:

```markdown
### Added
- **Scope-aware Boy Scout Rule.** Every current and generated behavioral-instruction surface now rejects “pre-existing” as a reason for silence: clean up evidence-backed defects within authorized scope; otherwise describe the finding, evidence, proposed cleanup scope, and risk and ask permission. Blocked or unsafe findings are recorded rather than suppressed.

### Changed
- **Direct OpenAI Codex compatibility is explicit.** Direct Worker mode supports Claude Code and OpenAI Codex. Commander continues to orchestrate Claude Code sessions only; Codex-backed Commander workers are not included in v1.0.24.

### Fixed
- **Restricted-runner tests are hermetic.** Four orphan-worker tests now exercise tmux-selection contracts through deterministic doubles instead of the operator's live server, and the wiki redirect test uses `socket.socketpair()` instead of binding a TCP listener. Coverage is preserved without environment-dependent skips.
```

### Step 3: Run focused release checks

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests/test_boy_scout_directive.py commander/tests/test_worker_claude_md_template.py commander/tests/test_advisor_fallback_directive.py commander/tests/test_version_consistency.py commander/tests/test_kill_orphan_workers.py commander/tests/test_wiki_server.py -q
```

Expected: all focused tests pass.

Validate the Codex plugin manifest:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py worker
```

Expected: validation succeeds with no errors.

### Step 4: Run all hook test scripts

Run each of these exact scripts:

```bash
bash worker/hooks/test-bg-detection.sh
bash worker/hooks/test-config-guard-integration.sh
bash worker/hooks/test-config-guard.sh
bash worker/hooks/test-guard-security.sh
bash worker/hooks/test-poll-dedup.sh
bash worker/hooks/tests/test-antipattern-lexicon.sh
bash worker/hooks/tests/test-bash-readonly-guard.sh
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
bash worker/hooks/tests/test-gbtw-inflight.sh
bash worker/hooks/tests/test-gbtw-waiting.sh
bash worker/hooks/tests/test-sad-antipattern.sh
```

Expected: all 11 scripts exit 0.

### Step 5: Run the complete Commander suite in the restricted runner

Run:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest commander/tests -q
```

Expected: `1969 passed, 1 skipped, 0 failed`.

### Step 6: Stage release documentation

Run:

```bash
git add README.md CHANGELOG.md
```

Expected: release documentation is staged; `AGENTS.md` remains untracked.

### Step 7: Review staged scope and prepare the final local amend

Run:

```bash
git status --short
git diff --cached --stat
git diff --cached --check
git log --oneline --decorate -2
```

Expected:
- Only approved source, test, documentation, design, and plan files are staged.
- `AGENTS.md` remains `??` and unstaged.
- No whitespace errors.
- `main` is one unpushed v1.0.24 commit ahead of `origin/main` before the amend.

After Task 3 passes review and `executing-plans` reports execution complete/disabled, the main session performs this already-authorized release handoff outside execution mode. Do not push.

### Post-execution release handoff: inspect the exact staged tree

Run:

```bash
git status --short
git diff --cached --stat
git diff --cached --check
git diff --cached --name-only
```

Expected: only approved source, test, documentation, design, and plan files are staged; `AGENTS.md` remains untracked; no whitespace errors.

### Post-execution release handoff: amend the existing v1.0.24 commit locally

Run:

```bash
git commit --amend -m "v1.0.24: workflow durability, Codex compatibility, and reliability hardening" -m "Add durable workflow enforcement and native direct-mode OpenAI Codex packaging; introduce scope-aware Boy Scout cleanup guidance across every instruction surface; harden Commander login and operator-wait behavior; and make tmux/wiki tests hermetic in restricted runners." -m "Verification: full Commander suite, focused propagation/version tests, all hook suites, and Codex plugin validation." -m "Commander remains Claude Code-only. No push performed."
```

Expected: the previous local v1.0.24 commit is replaced by one amended v1.0.24 commit containing all staged changes.

### Post-execution release handoff: verify branch state and no push

Run:

```bash
git rev-list --count origin/main..HEAD
git log --oneline --decorate -2
git status --short
```

Expected: ahead count is exactly `1`; `HEAD` is the amended v1.0.24 commit; `origin/main` remains at v1.0.23; only `?? AGENTS.md` remains in the worktree. Do not run `git push`.
