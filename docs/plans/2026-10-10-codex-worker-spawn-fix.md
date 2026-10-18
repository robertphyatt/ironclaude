# Codex Worker Spawn Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make a Commander-spawned or resumed Codex worker start reliably, locally and on a remote host. It must submit its profile turn, bind professional mode to its own Codex thread, and receive its objective. A spawn that fails before the worker is registered must release its worker-id reservation. Claude workers keep today's behaviour.

**Requirements:** `docs/plans/2026-10-10-codex-worker-spawn-fix-design.md` (R1–R11)

Loop: full — worker lifecycle and workspace-reservation release (git/worktree operations), a hook shared by both clients, and several Commander files.

**Architecture:** The changes live in the Codex branches of `OrchestratorTools`:
- `_wait_for_ready` gains the compacted marker match and the Folder-access trust answer.
- `_dispatch_worker_communication_profile` gains a conditional second Enter.
- `spawn_worker`, `_start_batch_item` and `resume_session` send the Codex profile turn before PM activation and bind identity to the dispatch time.
- `_activate_pm_remote` accepts a remote id file only when its remote mtime is at or after that time.
- One helper, `_release_unstarted_spawn`, releases a reservation whose spawn failed before the worker was registered.

`worker/hooks/state-activator.sh` claims only its own client's pending marker. It uses a PPID helper factored out of `session-init.sh` into `hook-logger.sh`.

**Tech stack:** Python 3 (Commander, pytest), Bash hooks (`make test-hooks`), tmux, Codex CLI, workspace-manager CLI (Node, unchanged).

## Execution invariants

- **Shell state.** Shell state does not persist between steps. Use absolute paths and do not carry variables across steps.
- **Working directories.** The Bash cwd may be `commander/`. Run pytest as `cd /Users/roberthyatt/Code/ironclaude/commander && ...` and git as `git -C /Users/roberthyatt/Code/ironclaude ...`.
- **Shell rules.**
  - zsh `nomatch` is on, so quote globs.
  - Foreground `sleep` is blocked. Run long commands with `run_in_background` and wait for the completion notification.
- **Git.**
  - `docs/` is gitignored, so stage doc files with `git add -f`.
  - Never use `git stash`.
  - Never pattern-kill. Kill only exact verified PIDs or exact tmux session names.
- **Evidence.**
  - Never append `2>/dev/null` to an evidence command.
  - Record expected counts from the actual run, because the planning stage cannot run tests.
  - RED steps name the tests that must fail and say why. GREEN steps require those tests to pass.
- **pytest.** Always run `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest`.
- **Protected state.**
  - Never touch the live Commander DB (`commander/data/db/ironclaude.db`) or `~/.config/superpowers`.
  - Unit and hook tests use tmp dirs and fakes only.
- **Staging.** Stage only the files the current task lists. The index already holds staged files from another loop; leave them alone.

## Source facts this plan relies on

Every fact below was read from the current source.

- **`_wait_for_ready`** (`orchestrator_mcp.py:2322-2383`).
  - The marker check is `marker in output` (`:2347`).
  - The Codex branch compacts `lower` (`:2353`) and answers only `doyoutrustthecontentsofthisdirectory` (`:2356`).
  - The hooks answer is gated on `codex_trust_dismissed` (`:2360-2372`).
  - It returns True when the banner is newer than `hooksneedreview` (`:2373-2376`), even in marker mode.
- **`_dispatch_worker_communication_profile`** (`:2385-2406`).
  - It sends the invocation once through `tmux.send_keys`, which types the text, waits 0.2 s and sends Enter (`tmux_manager.py:363-375`).
  - It then waits for `PROFILE_READY_MARKER` (`communication_profiles.py:31`, `"IC_LOSSLESS_AI_MESSAGES_ACTIVE"`) after the log offset.
- **tmux helpers.**
  - `send_raw_keys` sends keys without Enter (`tmux_manager.py:377-386`); `["Enter"]` is already used at `orchestrator_mcp.py:6074`.
  - `capture_pane` raises on local failure, because it passes `check=True` (`tmux_manager.py:432-441`).
- **Codex screens.**
  - In the 2026-10-09 probe, the submitted mention stays in the transcript, and the composer becomes `› Ask Codex to do anything` (`…/codex-repro/run-v2-B/snap-dispatch-enter2.txt`).
  - The picker shows `enter insert` (`snap-dispatch-typed.txt`).
  - The trust modal shows `Folder access` and `Trust this folder?` (`snap-modal-1-trust.txt`).
  - In `pane.log`, the banner is drawn before the modal and redrawn after it.
- **`spawn_worker`** (`:4519-5228`).
  - Readiness and the Fable retry run at `:4886-5011`, and Stages 4-5 activation and identity at `:5032-5085`.
  - `not_before` is `launch_started_at` (`:4870`, `:4955`, `:5054-5057`).
  - Bind is at `:5087-5117` and profile dispatch at `:5119-5130`.
  - Registry and objective (Stage 6) are at `:5164-5210`.
- **Remote activation** (`_activate_pm_remote`, `:2728-2801`).
  - It reads `~/.claude/ironclaude-session-<pid>.id` with `tmux.read_file` (`tmux_manager.py:545-549`, which runs `cat` remotely through `_run`, `:272-280`).
  - It accepts any 36-character content, with no time check.
  - Codex candidate PIDs come from `_remote_descendant_pids` (`:2706-2726`), which calls `self._ssh_manager.run_argv`.
- **Remote command quoting.** `SSHConnectionManager.run_argv` (`ssh_manager.py:112-128`) quotes `argv[0]` with `_quote_remote_path` (`:17-31`) and every later argument with `shlex.join`. A `~` argument is therefore never expanded. So the R10 stat runs under `sh -c` and builds the path from `$HOME`.
- **Remote stat flags.** `stat -c %Y` (GNU/busybox) is tried first. GNU `stat -f` means file-system mode and would print file-system text before failing over. On macOS, `-c` is rejected without stdout, and `stat -f %m` then runs.
- **Test doubles.** `ProbeResult(returncode, stdout, stderr)` (`provider_capabilities.py:14`) is the existing `run_argv` test double (`test_orchestrator_mcp.py:30`, `:8394-8397`).
- **Resume** (`resume_session`, `:6511-6658`).
  - Order today: spawn, readiness (`:6576`), activation with `not_before = launch_started_at` (`:6562`, `:6593-6599`), identity check, then profile dispatch (`:6622-6626`) and registration.
  - The identity check already requires `pm["session_uuid"] == session_id` (`:6612-6620`).
  - Its `finally` kills the session on every failure (`:6639-6641`).
- **Batch path.** The batch item function is `_start_batch_item` (`:5230-5432`); there is no `_spawn_one_activated`.
  - Its `failed()` closure (`:5238-5252`) builds failures.
  - `spawn_workers` kills a failed item's session at `:5804-5810`.
  - Batch launch failures are at `:5730-5756`.
  - Batch registry and objective failures are at `:5839-5876`.
- **Workspace release.**
  - `workspace_client.abandon` and `reap` (`workspace_client.py:231-232`, `:243-244`) invoke the workspace-manager CLI.
  - The CLI `abandon` reads `owner_session_id` with `requiredString` (`cli.ts:291-296`), which throws on null or empty (`cli.ts:66-69`). `getWorkspaceAssignment` also compares owners with `!==` (`workspace-service.ts:509-516`). **So `abandon` cannot release an unbound reservation (owner null).**
  - The CLI `reap` takes only `repository_path` and `workspace_guid` (`cli.ts:333-336`). It runs the owner-agnostic `reapLeakedAssignment` (`workspace-service.ts:922-967`), which rescues a present worktree through `rescueAbandon` (`:724-742`) and tombstones the row.
  - `main.py:491-524` already uses `abandon(rescue)` for owned rows and `reap` for ownerless rows.
- **Retry after release.** `reserveWorkerWorktree` refuses a second non-terminal reservation for the same `worker_id` (`workspace-service.ts:422-428`). Abandoned and cleaned rows are excluded, and each spawn uses a fresh `uuid4` guid (`orchestrator_mcp.py:3251`). So a released reservation lets a same-id retry allocate.
- **Hooks.**
  - `state-activator.sh:45-64` claims every `ironclaude-ppid-pending-*` marker.
  - `session-init.sh:327-366` derives `REAL_PPID`.
  - The state-manager wrapper keys its marker to `process.ppid` (`worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js:16`).
  - `~/.claude/ironclaude-mcp-state-manager.log:43628-43629` shows `PPID=32293` and `pending marker written: …/ironclaude-ppid-pending-32293` at 00:21:50Z, about 7 s after the probe started.
- **Hook launch shape.** Both clients load `worker/hooks/hooks.json`. SessionStart runs `bash ${CLAUDE_PLUGIN_ROOT}/hooks/session-init.sh` (`hooks.json:10`) and UserPromptSubmit runs `bash $HOME/.claude/ironclaude-hooks/state-activator.sh` (`hooks.json:168`). Both use the same `bash <script>` shape, so `$PPID` means the same parent in both hooks. In the probe, the Codex marker and id file were both keyed to 32293.
- **Hook tests.** `make test-hooks` runs every `worker/hooks/test-*.sh`, `worker/hooks/tests/test-*.sh` and `commander/hooks/tests/test-*.sh` (`Makefile:40-44`).
- **Test fixtures** (`commander/tests/test_orchestrator_mcp.py:90-298`).
  - Autouse fixtures: `default_workspace_client`, a MagicMock whose `discover_installed_plugin_root` returns `/installed/<provider>`; `default_pm_readback`, a lambda **without** `not_before`; and `default_instruction_preflight`.
  - `mock_tmux.capture_pane` returns `""`.
  - `_provider_handle()` defaults to Codex.
- **Live-run constraints.**
  - `discover_installed_plugin_root` makes a live worker run the **installed** plugin, both hooks and workspace-manager. R6 is therefore not exercised by the live run until that plugin is redeployed.
  - macOS limits tmux socket paths to about 104 bytes, so the scratchpad path is too long for `TMUX_TMPDIR`.

## Release boundary for R7

A pre-objective failure releases the reservation through `_release_unstarted_spawn`. This applies to every failure exit after reservation and before the worker is registered:
- launch;
- readiness, including the Fable-retry `NoCapabilityAvailable` exit at `:4929-4930`, which today returns a bare `{"error": ...}`;
- activation and identity;
- profile dispatch.

Two kinds of exit keep today's `assignment_preserved: True` contract:
- **Bind failures.** The server-side owner is unknown, and may be a different session, which `test_bind_rejects_response_owned_by_different_native_session` models. The exit keeps its `retry_bind` recovery.
- **Stage 6 failures** (registry write or objective send). A registry row may already point at the assignment.

The helper releases as follows:
- **Owner set:** `abandon(mode="rescue")` with that owner.
- **Owner None:** `reap`.
- **On success:** `assignment_preserved: False`, `assignment_released: True` and no `recovery`.
- **On release error:** it logs a warning and returns the unchanged failure plus `release_error`.

In every path, `error` and `failure_phase` are unchanged. The session is always killed before the release.

---

## Task 1: Compacted marker match and Folder-access trust in `_wait_for_ready` (R3, R4)

**Files:** `commander/src/ironclaude/orchestrator_mcp.py`, `commander/tests/test_worker_adapter.py`

**Step 1.1 — Add the RED tests.** In `commander/tests/test_worker_adapter.py`, insert after the line `    assert tmux.sent == [""]   # claude trust dismissal unchanged` (end of `test_wait_for_ready_claude_unchanged`):

```python


from ironclaude.communication_profiles import PROFILE_READY_MARKER

_CODEX_BANNER = ">_ OpenAI Codex (v0.162.0-alpha.17.2)\n› Ask Codex to do anything\n"
# Codex 0.162 "Folder access" modal. Cursor moves split its words in the pane
# log, as in the 2026-10-09 probe's pane.log.
_FOLDER_ACCESS_MODAL = (
    "\x1b[30;3HFolder\x1b[30;10Haccess\n"
    "\x1b[32;3HTrust\x1b[32;9Hthis\x1b[32;14Hfolder?"
    "\x1b[32;22HCodex can read files here.\n"
    "\x1b[35;1H› 1. Trust and continue\n  2. Quit\n"
    "  enter continue · esc quit\n"
)


def test_wait_for_ready_marker_matches_whitespace_split_marker(tmp_path):
    split = "• IC_LOSSLESS_AI_MESSAGES\n  _ACTIVE\n"
    tools = _tools_with_tmux(tmp_path, _FakeTmux([split], sanitize=True))
    assert tools._wait_for_ready(
        "ic-w", timeout=2, client="codex", marker=PROFILE_READY_MARKER,
    ) is True


def test_wait_for_ready_marker_absent_does_not_match(tmp_path):
    partial = "IC_LOSSLESS_AI_MESSAGES IC LOSSLESS AI MESSAGES ACTIVE\n"
    tools = _tools_with_tmux(tmp_path, _FakeTmux([partial] * 5, sanitize=True))
    assert tools._wait_for_ready(
        "ic-w", timeout=2, client="codex", marker=PROFILE_READY_MARKER,
    ) is False


def test_wait_for_ready_marker_mode_ignores_codex_banner(tmp_path):
    tools = _tools_with_tmux(
        tmp_path, _FakeTmux([_CODEX_BANNER] * 5, sanitize=True),
    )
    assert tools._wait_for_ready(
        "ic-w", timeout=2, client="codex", marker=PROFILE_READY_MARKER,
    ) is False


def test_wait_for_ready_codex_answers_folder_access_then_waits_for_redraw(tmp_path):
    modal = _CODEX_BANNER + _FOLDER_ACCESS_MODAL
    tmux = _FakeTmux([modal, modal, modal + _CODEX_BANNER], sanitize=True)
    tools = _tools_with_tmux(tmp_path, tmux)
    assert tools._wait_for_ready("ic-w", timeout=10, client="codex") is True
    assert tmux.sent == [""]
    assert tmux.raw_sent == []
    assert tmux.read_count == 3


def test_wait_for_ready_codex_folder_access_enables_hooks_answer(tmp_path):
    modal = _CODEX_BANNER + _FOLDER_ACCESS_MODAL
    hooks = modal + "Hooks need review\n2. Trust all and continue\n"
    tmux = _FakeTmux([modal, hooks, hooks + _CODEX_BANNER], sanitize=True)
    tools = _tools_with_tmux(tmp_path, tmux)
    assert tools._wait_for_ready("ic-w", timeout=10, client="codex") is True
    assert tmux.sent == [""]
    assert tmux.raw_sent == [["2"]]
```

**Step 1.2 — RED run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worker_adapter.py -q -k wait_for_ready`

Expected: four tests fail.

| Test | Why it fails today |
|---|---|
| `test_wait_for_ready_marker_matches_whitespace_split_marker` | No compacted marker match, so the call times out to False. |
| `test_wait_for_ready_marker_mode_ignores_codex_banner` | The banner fallback returns True in marker mode. |
| `test_wait_for_ready_codex_answers_folder_access_then_waits_for_redraw` | It returns True on read 1 with no Enter. |
| `test_wait_for_ready_codex_folder_access_enables_hooks_answer` | Same: it returns True on read 1 with no Enter. |

`test_wait_for_ready_marker_absent_does_not_match` and the six existing `wait_for_ready` tests pass. Record the summary line.

**Step 1.3 — GREEN code.** In `commander/src/ironclaude/orchestrator_mcp.py` (`_wait_for_ready`), make three edits.

Edit 1a. Replace

```python
        Returns True if the client's ready indicator is found (claude: "ironclaude v";
        codex: the ">_ OpenAI Codex" welcome box), False on timeout. Dismisses the
        client's trust dialog by sending Enter (codex: only once — its prompt text
        persists in the tail after acceptance). Codex's subsequent hooks-review
        prompt selects "Trust all and continue" once with its raw numeric shortcut.
        """
```

with

```python
        Returns True if the client's ready indicator is found (claude: "ironclaude v";
        codex: the ">_ OpenAI Codex" welcome box drawn after any trust or hooks
        prompt), False on timeout. Dismisses the client's trust dialog by sending
        Enter (codex: only once — its prompt text persists in the tail after
        acceptance; both "Do you trust the contents of this directory?" and the
        0.162+ "Folder access / Trust this folder?" modal). Codex's subsequent
        hooks-review prompt selects "Trust all and continue" once with its raw
        numeric shortcut. With a marker, only the marker (also matched with
        whitespace removed, since cursor moves can split it) means ready.
        """
```

Edit 1b. Replace

```python
                if marker is not None and marker in output:
                    return True
```

with

```python
                if marker is not None and (
                    marker in output or marker in re.sub(r"\s+", "", output)
                ):
                    return True
```

Edit 1c. Replace

```python
                if client == "codex":
                    compact = re.sub(r"\s+", "", lower)
                    if (
                        not codex_trust_dismissed
                        and "doyoutrustthecontentsofthisdirectory" in compact
                    ):
                        self.tmux.send_keys(session_name, "", ssh_host=ssh_host)  # Enter accepts default "Yes"
                        codex_trust_dismissed = True
```

with

```python
                if client == "codex":
                    compact = re.sub(r"\s+", "", lower)
                    folder_index = max(
                        compact.rfind("folderaccess"),
                        compact.rfind("trustthisfolder"),
                    )
                    if not codex_trust_dismissed and (
                        "doyoutrustthecontentsofthisdirectory" in compact
                        or folder_index >= 0
                    ):
                        self.tmux.send_keys(session_name, "", ssh_host=ssh_host)  # Enter accepts default "Yes"
                        codex_trust_dismissed = True
```

Edit 1d. Replace

```python
                    ready_index = compact.rfind(">_openaicodex")
                    hooks_index = compact.rfind("hooksneedreview")
                    if ready_index > hooks_index:
                        return True
```

with

```python
                    ready_index = compact.rfind(">_openaicodex")
                    hooks_index = compact.rfind("hooksneedreview")
                    if marker is None and ready_index > max(hooks_index, folder_index):
                        return True
```

**Step 1.4 — GREEN run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_worker_adapter.py tests/test_communication_routing.py -q`
then
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "WaitForReady or ResumeSession or Codex"`

Expected: all pass, including the four RED tests. Record both summary lines.

**Step 1.5 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_adapter.py`
Expected: no output; `git -C /Users/roberthyatt/Code/ironclaude diff --cached --stat` lists both files.

---

## Task 2: Conditional second Enter for the Codex profile turn (R2, with R4 at dispatch)

**Files:** `commander/src/ironclaude/orchestrator_mcp.py`, `commander/tests/test_communication_routing.py`

**Step 2.1 — Add the RED tests.** In `commander/tests/test_communication_routing.py`:

Edit 2.1a. Replace the import line `from unittest.mock import MagicMock` with `from unittest.mock import MagicMock, call`.

Edit 2.1b. Insert immediately before `def test_profile_load_failure_returns_before_objective_delivery(tmp_path, monkeypatch):`:

```python
_CODEX_INVOCATION = "$ironclaude:write-lossless-ai-messages"
_CODEX_PICKER_SCREEN = (
    "› write-lossless-ai-message...  [Skill] Make AI-to-AI messages efficient\n\n"
    "  enter insert · esc close\n\n"
    f"› {_CODEX_INVOCATION}\n\n"
    "  GPT-5.6-Terra high · /repo\n"
)
_CODEX_UNSUBMITTED_SCREEN = (
    ">_ OpenAI Codex (v0.162.0-alpha.17.2)\n\n"
    f"› {_CODEX_INVOCATION}\n\n"
    "  GPT-5.6-Terra high · /repo\n"
)
# After submission the mention stays in the transcript; the composer is empty.
_CODEX_SUBMITTED_SCREEN = (
    f"› {_CODEX_INVOCATION}\n\n"
    f"• {PROFILE_READY_MARKER}\n\n"
    "  Worked for 6s\n\n"
    "› Ask Codex to do anything\n\n"
    "  GPT-5.6-Terra high · /repo · Write lossless AI messages\n"
)
_CODEX_FOLDER_ACCESS_SCREEN = (
    "  Folder access\n  /repo\n\n"
    "  Trust this folder? Codex can read, edit, and run files here.\n\n"
    "› 1. Trust and continue\n  2. Quit\n\n  enter continue · esc quit\n"
)


def _profile_tmux(screens):
    tmux = MagicMock()
    tmux.get_log_size.return_value = 17
    tmux.send_keys.return_value = True
    tmux.send_raw_keys.return_value = True
    tmux.capture_pane.side_effect = list(screens)
    tmux.read_log_since.return_value = f"new output\n{PROFILE_READY_MARKER}\n"
    return tmux


def _no_wait(monkeypatch):
    clock = iter((0, 1))
    monkeypatch.setattr(orchestrator_module.time, "time", lambda: next(clock))
    monkeypatch.setattr(orchestrator_module.time, "sleep", lambda _: None)


@pytest.mark.parametrize(
    "screen",
    [_CODEX_PICKER_SCREEN, _CODEX_UNSUBMITTED_SCREEN],
    ids=["picker", "mention"],
)
def test_codex_profile_sends_second_enter_while_unsubmitted(
    tmp_path, monkeypatch, screen,
):
    tmux = _profile_tmux(["", screen])
    tools = _tools(tmp_path, tmux)
    _no_wait(monkeypatch)

    assert tools._dispatch_worker_communication_profile("ic-w", "codex") is None
    tmux.send_keys.assert_called_once_with("ic-w", _CODEX_INVOCATION, ssh_host=None)
    tmux.send_raw_keys.assert_called_once_with("ic-w", ["Enter"], ssh_host=None)


def test_codex_profile_skips_second_enter_once_submitted(tmp_path, monkeypatch):
    tmux = _profile_tmux(["", _CODEX_SUBMITTED_SCREEN])
    tools = _tools(tmp_path, tmux)
    _no_wait(monkeypatch)

    assert tools._dispatch_worker_communication_profile("ic-w", "codex") is None
    tmux.send_raw_keys.assert_not_called()


def test_claude_profile_keeps_single_enter(tmp_path, monkeypatch):
    tmux = _profile_tmux([])
    tools = _tools(tmp_path, tmux)
    _no_wait(monkeypatch)

    assert tools._dispatch_worker_communication_profile("ic-w", "claude") is None
    tmux.send_keys.assert_called_once_with(
        "ic-w", "/write-lossless-ai-messages", ssh_host=None,
    )
    tmux.capture_pane.assert_not_called()
    tmux.send_raw_keys.assert_not_called()


def test_codex_profile_answers_folder_access_before_invocation(tmp_path, monkeypatch):
    tmux = _profile_tmux([_CODEX_FOLDER_ACCESS_SCREEN, _CODEX_SUBMITTED_SCREEN])
    tools = _tools(tmp_path, tmux)
    _no_wait(monkeypatch)

    assert tools._dispatch_worker_communication_profile("ic-w", "codex") is None
    assert tmux.send_keys.call_args_list == [
        call("ic-w", "", ssh_host=None),
        call("ic-w", _CODEX_INVOCATION, ssh_host=None),
    ]
    tmux.send_raw_keys.assert_not_called()


```

**Step 2.2 — RED run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_communication_routing.py -q`

Expected: three tests fail.

| Test | Why it fails today |
|---|---|
| `test_codex_profile_sends_second_enter_while_unsubmitted[picker]` | `capture_pane` is never read and no raw Enter is sent. |
| `test_codex_profile_sends_second_enter_while_unsubmitted[mention]` | Same. |
| `test_codex_profile_answers_folder_access_before_invocation` | No trust Enter is sent before the invocation. |

`test_codex_profile_skips_second_enter_once_submitted`, `test_claude_profile_keeps_single_enter` and all existing tests pass. Record the summary line.

**Step 2.3 — GREEN code.** In `commander/src/ironclaude/orchestrator_mcp.py`, replace

```python
    def _dispatch_worker_communication_profile(
        self, session_name: str, client: str, *, ssh_host: str | None = None,
        remote_log_dir: str | None = None,
    ) -> str | None:
        """Invoke managed-worker profile and reject markers that predate dispatch."""
        # IRONCLAUDE_LLM_PATH: managed_worker
        try:
            invocation = skill_invocation("managed_worker", client)
        except CommunicationProfileError as exc:
            return f"Communication-profile infrastructure error: {exc}"
        log_offset = self.tmux.get_log_size(
            session_name, ssh_host=ssh_host, remote_log_dir=remote_log_dir,
        )
        if not self.tmux.send_keys(session_name, invocation, ssh_host=ssh_host):
            return "Failed to deliver write-lossless-ai-messages activation"
        if not self._wait_for_ready(
```

with

```python
    def _codex_screen(self, session_name: str, ssh_host: str | None) -> str:
        """Rendered Codex pane text, or "" when it cannot be captured."""
        try:
            screen = self.tmux.capture_pane(session_name, ssh_host=ssh_host)
        except Exception:  # noqa: BLE001 — no screen means nothing to answer
            return ""
        return screen if isinstance(screen, str) else ""

    @staticmethod
    def _codex_skill_unsubmitted(screen: str, invocation: str) -> bool:
        """True while Codex shows its skill picker or the unsubmitted mention.

        The submitted mention stays in the transcript, so only the composer —
        the last line that starts with Codex's "›" prompt — is checked.
        """
        if "enter insert" in screen:
            return True
        prompts = [
            line.strip() for line in screen.splitlines()
            if line.strip().startswith("›")
        ]
        return bool(prompts) and invocation in prompts[-1]

    def _dispatch_worker_communication_profile(
        self, session_name: str, client: str, *, ssh_host: str | None = None,
        remote_log_dir: str | None = None,
    ) -> str | None:
        """Invoke managed-worker profile and reject markers that predate dispatch."""
        # IRONCLAUDE_LLM_PATH: managed_worker
        try:
            invocation = skill_invocation("managed_worker", client)
        except CommunicationProfileError as exc:
            return f"Communication-profile infrastructure error: {exc}"
        if client == "codex":
            # Codex 0.162 can draw its "Folder access" trust modal after the
            # banner that readiness accepts; answer it before typing the turn.
            screen = self._codex_screen(session_name, ssh_host)
            if "Folder access" in screen or "Trust this folder?" in screen:
                self.tmux.send_keys(session_name, "", ssh_host=ssh_host)
                time.sleep(1)
        log_offset = self.tmux.get_log_size(
            session_name, ssh_host=ssh_host, remote_log_dir=remote_log_dir,
        )
        if not self.tmux.send_keys(session_name, invocation, ssh_host=ssh_host):
            return "Failed to deliver write-lossless-ai-messages activation"
        # Codex's first Enter only inserts the skill from its picker; a second
        # Enter submits it. Send it only while the picker or the unsubmitted
        # mention is still on screen. Claude submits on the first Enter.
        if client == "codex" and self._codex_skill_unsubmitted(
            self._codex_screen(session_name, ssh_host), invocation,
        ):
            self.tmux.send_raw_keys(session_name, ["Enter"], ssh_host=ssh_host)
        if not self._wait_for_ready(
```

**Step 2.4 — GREEN run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_communication_routing.py tests/test_worker_adapter.py -q`
then
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "Batch or ManagedWorktree or ResumeSession or AdoptSession"`

Expected: all pass. Record both summary lines.

**Step 2.5 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_communication_routing.py`

---

## Task 3: Codex profile turn before PM activation; `not_before` = dispatch time (R1, R5)

**Files:** `commander/src/ironclaude/orchestrator_mcp.py`, `commander/tests/test_orchestrator_mcp.py`

**Step 3.1 — Add the RED tests** in `commander/tests/test_orchestrator_mcp.py`.

Edit 3.1a (`TestSingleWorkerManagedWorktree.test_reserves_before_launch_uses_worktree_cwd_then_binds_and_persists`, which runs as Codex). Replace

```python
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            order.append("spawn") or True
        )
        tools._read_pm_state_via_sqlite.side_effect = lambda *args, **kwargs: (
```

with

```python
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            order.append("spawn") or True
        )
        tools._activate_pm_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("activate")
        )
        tools._read_pm_state_via_sqlite.side_effect = lambda *args, **kwargs: (
```

Then replace

```python
        assert order == [
            "allocate", "spawn", "identity", "bind", "dispatch", "dispatch",
        ]
```

with

```python
        # Codex: the profile turn (first dispatch) precedes PM activation.
        assert order == [
            "allocate", "spawn", "dispatch", "activate", "identity", "bind",
            "dispatch",
        ]
```

Edit 3.1b. Insert a Claude-order guard in the same class, immediately before `    @pytest.mark.parametrize("phase", ["launch", "readiness", "activation", "bind", "dispatch"])`:

```python
    def test_claude_spawn_order_is_unchanged(
        self, tools, registry, mock_tmux, default_workspace_client,
    ):
        workspace = default_workspace_client
        self._prepare(tools, workspace, mock_tmux)
        tools._resolve_worker_client = MagicMock(return_value=_provider_handle(
            client="claude", model="sonnet",
        ))
        order = []
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            order.append("spawn") or True
        )
        tools._activate_pm_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("activate")
        )
        tools._read_pm_state_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("identity") or {
                "professional_mode": "on", "workflow_stage": "executing",
                "session_uuid": _VALID_NATIVE_UUID,
            }
        )
        workspace.bind.side_effect = lambda payload, **transport: (
            order.append("bind") or {
                **self._assignment(payload),
                "owner_session_id": payload["owner_session_id"],
            }
        )
        mock_tmux.send_keys.side_effect = lambda *args, **kwargs: (
            order.append(args[1]) or True
        )

        result = tools.spawn_worker(
            worker_id="w-claude-order", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )

        assert isinstance(result, str)
        assert order == [
            "spawn", "activate", "identity", "bind",
            "/write-lossless-ai-messages", "Task",
        ]
        tools._activate_pm_via_sqlite.assert_called_once_with(
            "ic-w-claude-order", timeout=300, max_retries=3, client="claude",
        )
        tools._read_pm_state_via_sqlite.assert_called_once_with(
            "ic-w-claude-order", client="claude",
        )

```

Edit 3.1c. Insert a remote-order test in `TestRemoteProviderLaunching`, immediately before `    def test_remote_spawn_failure_preserves_exception_without_cleanup(`:

```python
    @pytest.mark.parametrize(
        ("client", "model", "expected"),
        [
            ("codex", "gpt-5.6-terra", ["profile", "activate"]),
            ("claude", "sonnet", ["activate", "profile"]),
        ],
    )
    def test_remote_profile_turn_order_by_client(
        self, tools, registry, client, model, expected,
    ):
        order = []
        machine = self._machine({
            client: {"enabled": True, "path": f"/opt/{client}"},
        })
        self._prepare_remote_success(
            tools, machine, _provider_handle(client=client, model=model),
        )
        tools._activate_pm_remote.side_effect = lambda *args, **kwargs: (
            order.append("activate") or _VALID_NATIVE_UUID
        )
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: order.append("profile"),
        )

        result = tools.spawn_worker(
            worker_id=f"w-remote-order-{client}",
            worker_type="claude-sonnet",
            repo=self.REPO,
            objective="Task",
            machine="remote-worker",
        )

        assert isinstance(result, str)
        assert order == expected

```

Edit 3.1d. Insert a batch-order test in `TestProviderAwareBatchLifecycle`, immediately before `    def test_batch_codex_uses_provider_lifecycle_and_persists_provider(`:

```python
    @pytest.mark.parametrize(
        ("client", "model", "expected"),
        [
            ("codex", "gpt-5.6-terra", ["profile", "activate", "identity", "bind"]),
            ("claude", "sonnet", ["activate", "identity", "bind", "profile"]),
        ],
    )
    def test_batch_profile_turn_order_by_client(
        self, tools, default_workspace_client, client, model, expected,
    ):
        order = []
        tools._resolve_worker_client = MagicMock(return_value=_provider_handle(
            client=client, model=model,
        ))
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: order.append("profile"),
        )
        tools._activate_pm_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("activate")
        )
        tools._read_pm_state_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("identity") or {
                "professional_mode": "on", "workflow_stage": "idle",
                "session_uuid": _VALID_NATIVE_UUID,
            }
        )
        bind = default_workspace_client.bind.side_effect
        default_workspace_client.bind.side_effect = lambda payload, **transport: (
            order.append("bind") or bind(payload, **transport)
        )

        result = tools.spawn_workers([self._request()])

        assert result[0]["status"] == "spawned"
        assert order == expected

```

Edit 3.1e. Insert two boundary tests in `TestCodexLaunchBoundary`, immediately before `    def test_resume_launch_boundary_is_shared_by_activation_and_readback(`:

```python
    def test_codex_identity_boundary_is_profile_dispatch_time(
        self, tools, registry, mock_tmux,
    ):
        clock = itertools.count(1000.0, 1.0)
        marks = {}
        tools._resolve_worker_client = MagicMock(return_value=_provider_handle())
        tools._ensure_ssh_manager = MagicMock()
        tools._wait_for_ready = MagicMock(return_value=True)
        tools._activate_pm_via_sqlite = MagicMock(return_value=None)
        tools._read_pm_state_via_sqlite = MagicMock(return_value={
            "professional_mode": "on",
            "workflow_stage": "executing",
            "session_uuid": _VALID_NATIVE_UUID,
        })
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            marks.__setitem__("spawn", next(clock)) or True
        )
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: marks.__setitem__(
                "profile", next(clock),
            ),
        )
        _mock_grader_approve(tools)

        with patch(
            "ironclaude.orchestrator_mcp.time.time",
            side_effect=lambda: next(clock),
        ):
            tools.spawn_worker("w-profile-boundary", "claude-sonnet", "/tmp/repo", "Task")

        not_before = tools._activate_pm_via_sqlite.call_args.kwargs["not_before"]
        assert marks["spawn"] < not_before < marks["profile"]
        assert (
            tools._read_pm_state_via_sqlite.call_args.kwargs["not_before"]
            == not_before
        )

    def test_batch_codex_identity_boundary_is_profile_dispatch_time(
        self, tools, mock_tmux,
    ):
        clock = itertools.count(1000.0, 1.0)
        marks = {}
        tools._resolve_worker_client = MagicMock(return_value=_provider_handle())
        tools._wait_for_ready = MagicMock(return_value=True)
        tools._activate_pm_via_sqlite = MagicMock(return_value=None)
        tools._read_pm_state_via_sqlite = MagicMock(return_value={
            "professional_mode": "on",
            "workflow_stage": "executing",
            "session_uuid": _VALID_NATIVE_UUID,
        })
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            marks.__setitem__("spawn", next(clock)) or True
        )
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: marks.__setitem__(
                "profile", next(clock),
            ),
        )
        _mock_batch_approve(tools)

        with patch(
            "ironclaude.orchestrator_mcp.time.time",
            side_effect=lambda: next(clock),
        ):
            tools.spawn_workers([{
                "worker_id": "w-profile-boundary", "worker_type": "claude-sonnet",
                "repo": "/tmp/repo", "objective": "Task",
            }])

        not_before = tools._activate_pm_via_sqlite.call_args.kwargs["not_before"]
        assert marks["spawn"] < not_before < marks["profile"]
        assert (
            tools._read_pm_state_via_sqlite.call_args.kwargs["not_before"]
            == not_before
        )

```

**Step 3.2 — RED run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "reserves_before_launch or spawn_order_is_unchanged or profile_turn_order or identity_boundary_is_profile_dispatch_time"`

Expected failures:

| Test | Why it fails today |
|---|---|
| `test_reserves_before_launch_uses_worktree_cwd_then_binds_and_persists` | The profile turn comes after bind. |
| `test_remote_profile_turn_order_by_client[codex]` | Same ordering problem. |
| `test_batch_profile_turn_order_by_client[codex]` | Same ordering problem. |
| `test_codex_identity_boundary_is_profile_dispatch_time` | `not_before` is the launch time, which is before spawn. |
| `test_batch_codex_identity_boundary_is_profile_dispatch_time` | Same as above. |

The `[claude]` cases and `test_claude_spawn_order_is_unchanged` pass. Record the summary line.

**Step 3.3 — GREEN code, `spawn_worker`.** Apply these edits to `commander/src/ironclaude/orchestrator_mcp.py` in this order.

Edit 3.3a (Claude-only profile dispatch after bind). Replace

```python
        profile_error = self._dispatch_worker_communication_profile(
            session_name, worker_client, ssh_host=ssh_host,
            remote_log_dir=remote_log_dir,
        )
        if profile_error is not None:
            if ssh_host:
                cleanup_remote_session()
            else:
                self.tmux.kill_session(session_name, ssh_host=ssh_host)
            return self._workspace_failure(
                "dispatch", repo, assignment, profile_error,
            )

        # Stage 5.5: enable advisor if configured
```

with

```python
        if worker_client != "codex":
            profile_error = self._dispatch_worker_communication_profile(
                session_name, worker_client, ssh_host=ssh_host,
                remote_log_dir=remote_log_dir,
            )
            if profile_error is not None:
                if ssh_host:
                    cleanup_remote_session()
                else:
                    self.tmux.kill_session(session_name, ssh_host=ssh_host)
                return self._workspace_failure(
                    "dispatch", repo, assignment, profile_error,
                )

        # Stage 5.5: enable advisor if configured
```

Edit 3.3b (Codex profile turn before Stages 4-5). Replace

```python
        # Stages 4-5: activate professional mode and retain native identity.
        native_session_id = None
```

with

```python
        # Codex runs SessionStart, which writes the worker's own session-id file,
        # only on its first submitted turn. So for Codex the profile turn goes
        # first, and identity binding accepts only id files written after it.
        profile_dispatched_at = None
        if worker_client == "codex":
            profile_dispatched_at = time.time()
            profile_error = self._dispatch_worker_communication_profile(
                session_name, worker_client, ssh_host=ssh_host,
                remote_log_dir=remote_log_dir,
            )
            if profile_error is not None:
                if ssh_host:
                    cleanup_remote_session()
                else:
                    self.tmux.kill_session(session_name, ssh_host=ssh_host)
                return self._workspace_failure(
                    "dispatch", repo, assignment, profile_error,
                )

        # Stages 4-5: activate professional mode and retain native identity.
        native_session_id = None
```

Edit 3.3c. Replace

```python
            pm_identity_kwargs = (
                {"not_before": launch_started_at}
                if worker_client == "codex" else {}
            )
```

with

```python
            pm_identity_kwargs = (
                {"not_before": profile_dispatched_at}
                if worker_client == "codex" else {}
            )
```

Edit 3.3d (remove the now-unused launch time). Replace

```python
        # Stage 2: spawn tmux session
        launch_started_at = time.time() if not ssh_host else None
        success = self.tmux.spawn_session(session_name, cmd, cwd=workspace_cwd,
```

with

```python
        # Stage 2: spawn tmux session
        success = self.tmux.spawn_session(session_name, cmd, cwd=workspace_cwd,
```

Edit 3.3e. Replace

```python
                    launch_started_at = time.time() if not ssh_host else None
                    retry_success = self.tmux.spawn_session(
```

with

```python
                    retry_success = self.tmux.spawn_session(
```

**Step 3.4 — GREEN code, batch.**

Edit 3.4a (`_start_batch_item`, Claude-only profile dispatch after bind). Replace

```python
        profile_error = self._dispatch_worker_communication_profile(
            session_name, item["client"],
        )
        if profile_error is not None:
            return failed("dispatch", profile_error)
        return {
            **item,
            "assignment": bound_assignment,
```

with

```python
        if item["client"] != "codex":
            profile_error = self._dispatch_worker_communication_profile(
                session_name, item["client"],
            )
            if profile_error is not None:
                return failed("dispatch", profile_error)
        return {
            **item,
            "assignment": bound_assignment,
```

Edit 3.4b. Replace

```python
        pm_identity_kwargs = (
            {"not_before": item["launch_started_at"]}
            if item["client"] == "codex" else {}
        )
```

with

```python
        # Codex: profile turn before activation (see spawn_worker).
        profile_dispatched_at = None
        if item["client"] == "codex":
            profile_dispatched_at = time.time()
            profile_error = self._dispatch_worker_communication_profile(
                session_name, item["client"],
            )
            if profile_error is not None:
                return failed("dispatch", profile_error)
        pm_identity_kwargs = (
            {"not_before": profile_dispatched_at}
            if item["client"] == "codex" else {}
        )
```

Edit 3.4c. Replace

```python
                launch_started_at = time.time()
                retry_success = self.tmux.spawn_session(
                    session_name, retry_cmd, cwd=workspace_cwd,
                )
```

with

```python
                retry_success = self.tmux.spawn_session(
                    session_name, retry_cmd, cwd=workspace_cwd,
                )
```

Edit 3.4d. Replace

```python
                    "launch_used_claude_fable": False,
                    "launch_started_at": launch_started_at,
                }
```

with

```python
                    "launch_used_claude_fable": False,
                }
```

Edit 3.4e (`spawn_workers`). Replace

```python
            session_name = f"ic-{worker_id}"
            launch_started_at = time.time()
            success = self.tmux.spawn_session(
```

with

```python
            session_name = f"ic-{worker_id}"
            success = self.tmux.spawn_session(
```

Edit 3.4f. Replace

```python
                "launch_used_claude_fable": launch_used_claude_fable,
                "launch_started_at": launch_started_at,
                "assignment": assignment,
```

with

```python
                "launch_used_claude_fable": launch_used_claude_fable,
                "assignment": assignment,
```

Then run `grep -n "launch_started_at" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py`.
Expected: only the `resume_session` lines remain, at about `:6562` and `:6594`. Task 6 removes them.

**Step 3.5 — GREEN run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py tests/test_worker_adapter.py tests/test_communication_routing.py -q`

Expected: all pass, including the five RED tests. Record the summary line.

An existing test may fail because it asserts the old Codex order: profile after bind, nothing sent before activation, or `not_before` equal to the launch time. That is an expected consequence of R1/R5. Update it to the new Codex order, name it in the task evidence, and re-run. Claude-path assertions must not change. Any other failure means stop and report it.

**Step 3.6 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py`

---

## Task 4: Release the reservation on pre-registration spawn failure (R7)

**Files:** `commander/src/ironclaude/orchestrator_mcp.py`, `commander/tests/test_orchestrator_mcp.py`

**Step 4.1 — Add or update the RED tests** in `commander/tests/test_orchestrator_mcp.py`.

Edit 4.1a (`TestSingleWorkerManagedWorktree`). Replace the whole `test_post_reservation_failures_preserve_assignment_with_one_recovery_payload` test: its `@pytest.mark.parametrize("phase", ["launch", "readiness", "activation", "bind", "dispatch"])` line through its last line `            assert worker["workspace_guid"] == result["workspace"]["workspace_guid"]`. Replace it with:

```python
    @pytest.mark.parametrize("phase", ["bind", "dispatch"])
    def test_post_reservation_failures_preserve_assignment_with_one_recovery_payload(
        self, tools, registry, mock_tmux, default_workspace_client, phase,
    ):
        workspace = default_workspace_client
        self._prepare(tools, workspace, mock_tmux)
        if phase == "bind":
            workspace.bind.side_effect = RuntimeError("bind failed")
        else:
            # Profile delivery succeeds; only objective delivery fails after
            # registry persistence, preserving this legacy failure contract.
            mock_tmux.send_keys.side_effect = [True, False]

        result = tools.spawn_worker(
            worker_id=f"w-{phase}", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )

        assert result["failure_phase"] == phase
        assert result["assignment_preserved"] is True
        assert result["workspace"]["workspace_guid"]
        assert result["workspace"]["repository"] == "/tmp/repo"
        assert result["workspace"]["worktree_path"].startswith(
            "/tmp/repo/.ironclaude/worktrees/",
        )
        assert result["workspace"]["branch"].startswith("ironclaude/")
        assert result["workspace"]["lifecycle"] == "active"
        assert len(result["recovery"]) == 1
        assert set(result["recovery"]) <= {"retry_bind", "reconcile", "abandon"}
        workspace.abandon.assert_not_called()
        workspace.reap.assert_not_called()
        if phase == "dispatch":
            worker = registry.get_worker("w-dispatch")
            assert worker["workspace_guid"] == result["workspace"]["workspace_guid"]

    @pytest.mark.parametrize(
        ("phase", "failure_phase"),
        [
            ("launch", "launch"),
            ("readiness", "readiness"),
            ("activation", "activation"),
            ("identity", "activation"),
            ("profile", "dispatch"),
        ],
    )
    def test_pre_registration_failures_release_unbound_reservation(
        self, tools, registry, mock_tmux, default_workspace_client,
        phase, failure_phase,
    ):
        workspace = default_workspace_client
        self._prepare(tools, workspace, mock_tmux)
        if phase == "launch":
            mock_tmux.spawn_session.return_value = False
        elif phase == "readiness":
            tools._wait_for_ready.return_value = False
            mock_tmux.has_session.return_value = False
        elif phase == "activation":
            tools._activate_pm_via_sqlite.return_value = "activation failed"
        elif phase == "identity":
            tools._read_pm_state_via_sqlite.return_value = {
                "professional_mode": "off", "workflow_stage": "idle",
                "session_uuid": _VALID_NATIVE_UUID,
            }
        else:
            tools._dispatch_worker_communication_profile = MagicMock(
                return_value="profile failed",
            )

        result = tools.spawn_worker(
            worker_id=f"w-release-{phase}", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )

        assert result["failure_phase"] == failure_phase
        assert result["assignment_preserved"] is False
        assert result["assignment_released"] is True
        assert "recovery" not in result
        assert "release_error" not in result
        workspace.reap.assert_called_once_with(
            {
                "repository_path": "/tmp/repo",
                "workspace_guid": result["workspace"]["workspace_guid"],
            },
            plugin_root="/installed/codex",
        )
        workspace.abandon.assert_not_called()
        assert registry.get_worker(f"w-release-{phase}") is None

    def test_claude_profile_failure_abandons_bound_reservation_by_owner(
        self, tools, registry, mock_tmux, default_workspace_client,
    ):
        workspace = default_workspace_client
        self._prepare(tools, workspace, mock_tmux)
        tools._resolve_worker_client = MagicMock(return_value=_provider_handle(
            client="claude", model="sonnet",
        ))
        tools._dispatch_worker_communication_profile = MagicMock(
            return_value="profile failed",
        )

        result = tools.spawn_worker(
            worker_id="w-claude-profile", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )

        assert result["failure_phase"] == "dispatch"
        assert result["assignment_preserved"] is False
        assert result["assignment_released"] is True
        workspace.abandon.assert_called_once_with(
            {
                "repository_path": "/tmp/repo",
                "workspace_guid": result["workspace"]["workspace_guid"],
                "owner_session_id": _VALID_NATIVE_UUID,
                "mode": "rescue",
            },
            plugin_root="/installed/claude",
        )
        workspace.reap.assert_not_called()

    def test_release_error_is_logged_and_original_failure_returned(
        self, tools, mock_tmux, default_workspace_client, caplog,
    ):
        workspace = default_workspace_client
        self._prepare(tools, workspace, mock_tmux)
        tools._activate_pm_via_sqlite.return_value = "activation failed"
        workspace.reap.side_effect = RuntimeError("reap failed")

        result = tools.spawn_worker(
            worker_id="w-release-error", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )

        assert result["failure_phase"] == "activation"
        assert "activation failed" in result["error"]
        assert result["assignment_preserved"] is True
        assert result["release_error"] == "reap failed"
        assert set(result["recovery"]) == {"reconcile"}
        assert "reap failed" in caplog.text

    def test_same_worker_id_retry_succeeds_after_released_failure(
        self, tools, registry, mock_tmux, default_workspace_client,
    ):
        workspace = default_workspace_client
        self._prepare(tools, workspace, mock_tmux)
        live = {}

        def allocate(payload, **transport):
            # Mirrors reserveWorkerWorktree: one non-terminal row per worker_id.
            if payload["worker_id"] in live.values():
                raise RuntimeError(
                    "Worker is already reserved to a different managed workspace",
                )
            live[payload["workspace_guid"]] = payload["worker_id"]
            return self._assignment(payload)

        def reap(payload, **transport):
            live.pop(payload["workspace_guid"])
            return {"lifecycle_status": "cleaned"}

        workspace.allocate.side_effect = allocate
        workspace.reap.side_effect = reap
        workspace.reconcile.return_value = {"assignments": []}
        tools._activate_pm_via_sqlite.side_effect = ["activation failed", None]

        first = tools.spawn_worker(
            worker_id="w-retry-same", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )
        second = tools.spawn_worker(
            worker_id="w-retry-same", worker_type="claude-sonnet",
            repo="/tmp/repo", objective="Task",
        )

        assert first["failure_phase"] == "activation"
        assert isinstance(second, str) and "w-retry-same" in second
```

Edit 4.1b (`test_spawn_worker_codex_uses_mandatory_instruction_preflight`). Replace

```python
        assert result["failure_phase"] == "launch"
        assert result["assignment_preserved"] is True
        tools._ensure_worker_instructions.assert_called_once_with(
```

with

```python
        assert result["failure_phase"] == "launch"
        assert result["assignment_preserved"] is False
        tools._ensure_worker_instructions.assert_called_once_with(
```

Edit 4.1c (`test_spawn_worker_claude_instruction_preflight_failure_blocks_spawn`). Replace

```python
        assert result["failure_phase"] == "launch"
        assert result["assignment_preserved"] is True
        mock_tmux.spawn_session.assert_not_called()

    def test_spawn_worker_provider_unavailable_fails_closed_without_claude_fallback(
```

with

```python
        assert result["failure_phase"] == "launch"
        assert result["assignment_preserved"] is False
        mock_tmux.spawn_session.assert_not_called()

    def test_spawn_worker_provider_unavailable_fails_closed_without_claude_fallback(
```

Edit 4.1d (`test_remote_spawn_failure_preserves_exception_without_cleanup`). Replace

```python
        assert result["failure_phase"] == "launch"
        assert result["assignment_preserved"] is True
        mock_tmux.kill_session.assert_not_called()
        assert registry.get_worker("w-spawn-fail") is None
```

with

```python
        assert result["failure_phase"] == "launch"
        assert result["assignment_preserved"] is False
        tools._workspace_client.reap.assert_called_once_with(
            {
                "repository_path": self.REPO,
                "workspace_guid": result["workspace"]["workspace_guid"],
            },
            ssh_host="ssh-remote",
            remote_plugin_root="/installed/codex",
        )
        mock_tmux.kill_session.assert_not_called()
        assert registry.get_worker("w-spawn-fail") is None
```

Edit 4.1e (`test_missing_lossless_skill_cleans_single_worker_before_objective`). Replace

```python
        assert result["failure_phase"] == "dispatch"
        assert result["assignment_preserved"] is True
        mock_tmux.kill_session.assert_called_once_with(
            "ic-w-profile-missing", ssh_host=None,
```

with

```python
        assert result["failure_phase"] == "dispatch"
        assert result["assignment_preserved"] is False
        mock_tmux.kill_session.assert_called_once_with(
            "ic-w-profile-missing", ssh_host=None,
```

Edit 4.1f (`TestBatchManagedWorktree`). Replace

```python
    def test_batch_worktree_failed_sibling_preserves_only_its_assignment(
```

with

```python
    def test_batch_worktree_failed_sibling_releases_only_its_assignment(
```

and replace

```python
        assert failed["assignment_preserved"] is True
        assert len(failed["recovery"]) == 1
        assert set(failed["recovery"]) <= {"retry_bind", "reconcile", "abandon"}
        assert succeeded["worker_id"] == "batch-b"
        assert succeeded["status"] == "spawned"
        assert registry.get_worker("batch-a") is None
        assert registry.get_worker("batch-b")["workspace_guid"] != failed["workspace"]["workspace_guid"]
        workspace.abandon.assert_not_called()
```

with

```python
        assert failed["assignment_preserved"] is False
        assert failed["assignment_released"] is True
        assert "recovery" not in failed
        assert succeeded["worker_id"] == "batch-b"
        assert succeeded["status"] == "spawned"
        assert registry.get_worker("batch-a") is None
        assert registry.get_worker("batch-b")["workspace_guid"] != failed["workspace"]["workspace_guid"]
        workspace.reap.assert_called_once_with(
            {
                "repository_path": "/tmp/repo",
                "workspace_guid": failed["workspace"]["workspace_guid"],
            },
            plugin_root="/installed/codex",
        )
        workspace.abandon.assert_not_called()
```

Edit 4.1g (`test_batch_worktree_rejects_bind_response_with_changed_assignment_evidence`, the bind negative case). Replace

```python
        assert result["failure_phase"] == "bind"
        assert result["assignment_preserved"] is True
        assert set(result["recovery"]) == {"retry_bind"}
        assert field in result["error"]
```

with

```python
        assert result["failure_phase"] == "bind"
        assert result["assignment_preserved"] is True
        assert set(result["recovery"]) == {"retry_bind"}
        assert field in result["error"]
        workspace.reap.assert_not_called()
        workspace.abandon.assert_not_called()
```

Edit 4.1h (`test_batch_missing_lossless_skill_cleans_before_objective`). Replace

```python
        assert result[0]["failure_phase"] == "dispatch"
        assert result[0]["assignment_preserved"] is True
```

with

```python
        assert result[0]["failure_phase"] == "dispatch"
        assert result[0]["assignment_preserved"] is False
```

Edit 4.1i (`test_batch_instruction_preflight_failure_blocks_spawn`). Replace

```python
        assert result[0]["failure_phase"] == "launch"
        assert result[0]["assignment_preserved"] is True
        mock_tmux.spawn_session.assert_not_called()
```

with

```python
        assert result[0]["failure_phase"] == "launch"
        assert result[0]["assignment_preserved"] is False
        mock_tmux.spawn_session.assert_not_called()
```

Edit 4.1j (`test_batch_future_exception_isolated_by_result_slot`). Replace

```python
        assert result[0]["failure_phase"] == "readiness"
        assert result[0]["assignment_preserved"] is True
```

with

```python
        assert result[0]["failure_phase"] == "readiness"
        assert result[0]["assignment_preserved"] is False
```

Edit 4.1k (`test_batch_all_pre_spawn_failures_return_ordered_errors`). Replace `        assert all(item["assignment_preserved"] is True for item in result)` with `        assert all(item["assignment_preserved"] is False for item in result)`.

**Step 4.2 — RED run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "ManagedWorktree or InstructionPreflight or remote_spawn_failure or missing_lossless_skill or BatchManagedWorktree or ProviderAwareBatchLifecycle"`

Expected failures. In every case the failure path still returns `assignment_preserved: True` and never calls `reap` or `abandon`. One case differs: in the retry test, the second `allocate` raises.

- `test_pre_registration_failures_release_unbound_reservation`: all 5 params.
- `test_claude_profile_failure_abandons_bound_reservation_by_owner`.
- `test_release_error_is_logged_and_original_failure_returned`: it has no `release_error` key.
- `test_same_worker_id_retry_succeeds_after_released_failure`.
- `test_spawn_worker_codex_uses_mandatory_instruction_preflight`.
- `test_spawn_worker_claude_instruction_preflight_failure_blocks_spawn`.
- `test_remote_spawn_failure_preserves_exception_without_cleanup`.
- `test_missing_lossless_skill_cleans_single_worker_before_objective`.
- `test_batch_worktree_failed_sibling_releases_only_its_assignment`.
- `test_batch_missing_lossless_skill_cleans_before_objective`.
- `test_batch_instruction_preflight_failure_blocks_spawn` (both params).
- `test_batch_future_exception_isolated_by_result_slot`.
- `test_batch_all_pre_spawn_failures_return_ordered_errors`.

These pass:
- `test_post_reservation_failures_preserve_assignment_with_one_recovery_payload[bind|dispatch]`;
- `test_batch_worktree_rejects_bind_response_with_changed_assignment_evidence`;
- `test_bind_rejects_response_owned_by_different_native_session`.

Record the summary line.

**Step 4.3 — GREEN code: the helper.** In `commander/src/ironclaude/orchestrator_mcp.py`, replace

```python
            "assignment_preserved": True,
            "recovery": {recovery_name: payload},
        }
```

with

```python
            "assignment_preserved": True,
            "recovery": {recovery_name: payload},
        }

    def _release_unstarted_spawn(
        self,
        phase: str,
        repository: str,
        assignment: dict,
        error: object,
        transport: dict,
    ) -> dict:
        """Fail a spawn that never registered its worker and free its reservation.

        Callers kill the worker session first. A bound assignment is abandoned
        in rescue mode by its own owner. An unbound one (owner_session_id None)
        is released with the owner-free ``reap`` verb, because the CLI's
        ``abandon`` requires a non-empty owner. A release error is logged and the
        original failure is returned with the assignment still preserved.
        """
        failure = self._workspace_failure(phase, repository, assignment, error)
        owner = assignment.get("owner_session_id")
        try:
            if owner:
                self._workspace_client.abandon(
                    {
                        "repository_path": repository,
                        "workspace_guid": assignment["workspace_guid"],
                        "owner_session_id": owner,
                        "mode": "rescue",
                    },
                    **transport,
                )
            else:
                self._workspace_client.reap(
                    {
                        "repository_path": repository,
                        "workspace_guid": assignment["workspace_guid"],
                    },
                    **transport,
                )
        except Exception as exc:  # noqa: BLE001 — never mask the spawn failure
            logger.warning(
                "Releasing workspace %s after %s failure failed: %s",
                assignment.get("workspace_guid"), phase, exc,
            )
            failure["release_error"] = str(exc)
            return failure
        failure["assignment_preserved"] = False
        failure["assignment_released"] = True
        del failure["recovery"]
        return failure
```

**Step 4.4 — GREEN code: the `spawn_worker` exits.** Each site below already kills or cleans up the session, or has none, before it returns. Replace each `self._workspace_failure(` call with `self._release_unstarted_spawn(` and add `workspace_transport` as the last argument.

Edit 4.4a (Stage 0, two identical lines; use `replace_all: true`). Replace
`                return self._workspace_failure("launch", repo, assignment, instruction_error)`
with

```python
                return self._release_unstarted_spawn(
                    "launch", repo, assignment, instruction_error,
                    workspace_transport,
                )
```

Edit 4.4b. Replace

```python
            return self._workspace_failure(
                "launch", repo, assignment,
                f"Failed to spawn tmux session for worker '{worker_id}'",
            )
```

with

```python
            return self._release_unstarted_spawn(
                "launch", repo, assignment,
                f"Failed to spawn tmux session for worker '{worker_id}'",
                workspace_transport,
            )
```

Edit 4.4c. Replace

```python
                return self._workspace_failure(
                    "readiness", repo, assignment,
                    f"Codex MCP startup interrupted for worker '{worker_id}'.\nLast output:\n{log_tail}",
                )
```

with

```python
                return self._release_unstarted_spawn(
                    "readiness", repo, assignment,
                    f"Codex MCP startup interrupted for worker '{worker_id}'.\nLast output:\n{log_tail}",
                    workspace_transport,
                )
```

Edit 4.4d (Fable retry; the session was killed at `:4905`). Replace

```python
                    except NoCapabilityAvailable as exc:
                        return {"error": str(exc)}
```

with

```python
                    except NoCapabilityAvailable as exc:
                        return self._release_unstarted_spawn(
                            "readiness", repo, assignment, exc,
                            workspace_transport,
                        )
```

Edit 4.4e (two identical blocks; use `replace_all: true`). Replace

```python
                            return self._workspace_failure(
                                "readiness", repo, assignment, instruction_error,
                            )
```

with

```python
                            return self._release_unstarted_spawn(
                                "readiness", repo, assignment, instruction_error,
                                workspace_transport,
                            )
```

Edit 4.4f. Replace

```python
                        return self._workspace_failure(
                            "readiness", repo, assignment,
                            f"Failed to spawn tmux session for worker '{worker_id}' (opus retry)",
                        )
```

with

```python
                        return self._release_unstarted_spawn(
                            "readiness", repo, assignment,
                            f"Failed to spawn tmux session for worker '{worker_id}' (opus retry)",
                            workspace_transport,
                        )
```

Edit 4.4g. Replace

```python
                            return self._workspace_failure(
                                "readiness", repo, assignment,
                                f"Worker '{worker_id}' session died before ready on "
                                f"{machine or 'local'}.\nLast output:\n{retry_log_tail}",
                            )
```

with

```python
                            return self._release_unstarted_spawn(
                                "readiness", repo, assignment,
                                f"Worker '{worker_id}' session died before ready on "
                                f"{machine or 'local'}.\nLast output:\n{retry_log_tail}",
                                workspace_transport,
                            )
```

Edit 4.4h. Replace

```python
                            return self._workspace_failure(
                                "readiness", repo, assignment,
                                f"Worker '{worker_id}' not ready on {machine}.\nLast output:\n{retry_log_tail}",
                            )
```

with

```python
                            return self._release_unstarted_spawn(
                                "readiness", repo, assignment,
                                f"Worker '{worker_id}' not ready on {machine}.\nLast output:\n{retry_log_tail}",
                                workspace_transport,
                            )
```

Edit 4.4i. Replace

```python
                    return self._workspace_failure(
                        "readiness", repo, assignment,
                        f"Worker '{worker_id}' session died before ready on "
                        f"{machine or 'local'}.\nLast output:\n{log_tail}",
                    )
```

with

```python
                    return self._release_unstarted_spawn(
                        "readiness", repo, assignment,
                        f"Worker '{worker_id}' session died before ready on "
                        f"{machine or 'local'}.\nLast output:\n{log_tail}",
                        workspace_transport,
                    )
```

Edit 4.4j. Replace

```python
                    return self._workspace_failure(
                        "readiness", repo, assignment,
                        f"Worker '{worker_id}' not ready on {machine}.\nLast output:\n{log_tail}",
                    )
```

with

```python
                    return self._release_unstarted_spawn(
                        "readiness", repo, assignment,
                        f"Worker '{worker_id}' not ready on {machine}.\nLast output:\n{log_tail}",
                        workspace_transport,
                    )
```

Edit 4.4k. Replace

```python
                return self._workspace_failure(
                    "activation", repo, assignment,
                    f"PM activation failed for worker '{worker_id}': {reason}",
                )
```

with

```python
                return self._release_unstarted_spawn(
                    "activation", repo, assignment,
                    f"PM activation failed for worker '{worker_id}': {reason}",
                    workspace_transport,
                )
```

Edit 4.4l. Replace

```python
                return self._workspace_failure(
                    "activation", repo, assignment,
                    f"PM activation failed for worker '{worker_id}': {pm_failure}",
                )
```

with

```python
                return self._release_unstarted_spawn(
                    "activation", repo, assignment,
                    f"PM activation failed for worker '{worker_id}': {pm_failure}",
                    workspace_transport,
                )
```

Edit 4.4m. Replace

```python
                return self._workspace_failure(
                    "activation", repo, assignment,
                    f"PM identity verification failed for worker '{worker_id}'",
                )
```

with

```python
                return self._release_unstarted_spawn(
                    "activation", repo, assignment,
                    f"PM identity verification failed for worker '{worker_id}'",
                    workspace_transport,
                )
```

Edit 4.4n (both profile-dispatch exits from Task 3 are identical; use `replace_all: true`). At the Claude site, `assignment` is already the bound assignment, so this becomes an owner abandon. At the Codex site it is unbound, so this becomes a reap. Replace

```python
                return self._workspace_failure(
                    "dispatch", repo, assignment, profile_error,
                )
```

with

```python
                return self._release_unstarted_spawn(
                    "dispatch", repo, assignment, profile_error,
                    workspace_transport,
                )
```

Leave the bind exit (`"bind", repo, pending_assignment, exc`) and the Stage 6 exits (`return self._workspace_failure("dispatch", repo, assignment, exc)` and `"dispatch", repo, assignment,` / `f"Failed to dispatch objective to worker '{worker_id}'"`) unchanged.

**Step 4.5 — GREEN code: the batch exits.**

Edit 4.5a (`spawn_workers` instruction preflight). Replace

```python
                    **self._workspace_failure(
                        "launch", repo, assignment, instruction_error,
                    ),
```

with

```python
                    **self._release_unstarted_spawn(
                        "launch", repo, assignment, instruction_error,
                        reservation["transport"],
                    ),
```

Edit 4.5b. Replace

```python
                    **self._workspace_failure(
                        "launch", repo, assignment,
                        "Failed to spawn tmux session",
                    ),
```

with

```python
                    **self._release_unstarted_spawn(
                        "launch", repo, assignment,
                        "Failed to spawn tmux session",
                        reservation["transport"],
                    ),
```

Edit 4.5c. The release goes after the kill at `:5805`. Item failures from `_start_batch_item` and the future-exception wrapper both arrive here. Bind failures keep their `retry_bind` recovery. Replace

```python
                if outcome.get("failure"):
                    self.tmux.kill_session(session_name)
                    results[res_idx] = {
                        "worker_id": worker_id,
                        **outcome["failure"],
                    }
                    continue
```

with

```python
                if outcome.get("failure"):
                    self.tmux.kill_session(session_name)
                    failure = outcome["failure"]
                    if failure["failure_phase"] != "bind":
                        failure = self._release_unstarted_spawn(
                            failure["failure_phase"], req["repo"],
                            outcome["assignment"], failure["error"],
                            outcome["workspace_transport"],
                        )
                    results[res_idx] = {
                        "worker_id": worker_id,
                        **failure,
                    }
                    continue
```

Then run `grep -n -e "_release_unstarted_spawn(" -e "_workspace_failure(" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py`. Expected:
- Inside `spawn_worker`, the only remaining `_workspace_failure(` calls are the bind and Stage 6 exits.
- In `spawn_workers`, the remaining calls are the future-exception wrapper and the Stage 6 exit.

Record the output.

**Step 4.6 — GREEN run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py tests/test_worker_adapter.py tests/test_communication_routing.py tests/test_worker_finalize_release.py -q`

Expected: all pass, including every RED test from Step 4.2. Record the summary line.

Some other test may fail because it asserts the old preserved contract at a pre-registration exit, meaning a launch, readiness, activation or profile phase. Apply the same flip to it, name it in the task evidence, and re-run. Any other failure means stop and report.

**Step 4.7 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py`

---

## Task 5: Remote Codex binding by remote mtime (R10)

**Files:** `commander/src/ironclaude/orchestrator_mcp.py`, `commander/tests/test_orchestrator_mcp.py`

**Step 5.1 — Add the RED tests** in `commander/tests/test_orchestrator_mcp.py`.

Edit 5.1a (`TestActivatePmRemote`). Insert immediately before `    def test_pane_pid_none_includes_alive_status_and_log(self, tools, mock_tmux):`:

```python
    @staticmethod
    def _codex_remote(tools, mock_tmux, stat_result):
        native_uuid = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
        mock_tmux.list_pane_pid.return_value = "12345"
        mock_tmux.read_file.return_value = native_uuid
        mock_tmux.run_sqlite_query.return_value = ""
        ssh = MagicMock()
        ssh.run_argv.side_effect = lambda host, argv: (
            stat_result if argv[0] == "sh" else ProbeResult(1, "", "")
        )
        tools._ssh_manager = ssh
        return native_uuid, ssh

    def test_remote_codex_accepts_id_file_at_or_after_not_before(
        self, tools, mock_tmux,
    ):
        native_uuid, ssh = self._codex_remote(
            tools, mock_tmux, ProbeResult(0, "300\n", ""),
        )

        result = tools._activate_pm_remote(
            "ic-w1", "remote-host", timeout=2, client="codex", not_before=200.0,
        )

        assert result == native_uuid
        stat_argvs = [
            call.args[1] for call in ssh.run_argv.call_args_list
            if call.args[1][0] == "sh"
        ]
        assert stat_argvs[0][:2] == ["sh", "-c"]
        assert stat_argvs[0][-1] == "12345"
        assert "stat -c %Y" in stat_argvs[0][2]
        assert "stat -f %m" in stat_argvs[0][2]
        mock_tmux.run_sqlite_query.assert_called_once()

    @pytest.mark.parametrize(
        "stat_result",
        [
            ProbeResult(0, "100\n", ""),
            ProbeResult(127, "", "stat: not found"),
            ProbeResult(0, "not-a-time\n", ""),
        ],
        ids=["older", "no-stat", "garbled"],
    )
    def test_remote_codex_rejects_old_or_unstatable_id_file(
        self, tools, mock_tmux, stat_result,
    ):
        self._codex_remote(tools, mock_tmux, stat_result)

        result = tools._activate_pm_remote(
            "ic-w1", "remote-host", timeout=1, client="codex", not_before=200.0,
        )

        assert "timeout" in result
        mock_tmux.run_sqlite_query.assert_not_called()

```

Edit 5.1b (`TestRemoteProviderLaunching`). Insert immediately before `    def test_remote_spawn_failure_preserves_exception_without_cleanup(`:

```python
    @pytest.mark.parametrize("client", ["codex", "claude"])
    def test_remote_activation_not_before_is_profile_dispatch_time(
        self, tools, mock_tmux, client,
    ):
        clock = itertools.count(1000.0, 1.0)
        marks = {}
        machine = self._machine({
            client: {"enabled": True, "path": f"/opt/{client}"},
        })
        self._prepare_remote_success(tools, machine, _provider_handle(
            client=client,
            model="gpt-5.6-terra" if client == "codex" else "sonnet",
        ))
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            marks.__setitem__("spawn", next(clock)) or True
        )
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: marks.__setitem__(
                "profile", next(clock),
            ),
        )

        with patch(
            "ironclaude.orchestrator_mcp.time.time",
            side_effect=lambda: next(clock),
        ):
            result = tools.spawn_worker(
                worker_id=f"w-remote-nb-{client}",
                worker_type="claude-sonnet",
                repo=self.REPO,
                objective="Task",
                machine="remote-worker",
            )

        assert isinstance(result, str)
        kwargs = tools._activate_pm_remote.call_args.kwargs
        if client == "codex":
            assert marks["spawn"] < kwargs["not_before"] < marks["profile"]
        else:
            assert "not_before" not in kwargs

```

Edit 5.1c (`test_remote_same_host_retry_carries_new_provider_handle`). Replace

```python
        tools._activate_pm_remote.assert_called_once_with(
            "ic-w-retry", "ssh-remote", client="codex",
        )
```

with

```python
        tools._activate_pm_remote.assert_called_once()
        assert tools._activate_pm_remote.call_args.args == ("ic-w-retry", "ssh-remote")
        assert tools._activate_pm_remote.call_args.kwargs["client"] == "codex"
        assert isinstance(
            tools._activate_pm_remote.call_args.kwargs["not_before"], float,
        )
```

**Step 5.2 — RED run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "ActivatePmRemote or remote_activation_not_before or remote_same_host_retry"`

Expected failures:

| Test | Why it fails today |
|---|---|
| `test_remote_codex_accepts_id_file_at_or_after_not_before` | `_activate_pm_remote` has no `not_before` parameter, so the call raises TypeError. |
| `test_remote_codex_rejects_old_or_unstatable_id_file` (all 3 ids) | Same TypeError. |
| `test_remote_activation_not_before_is_profile_dispatch_time[codex]` | The spawn passes no `not_before`. |
| `test_remote_same_host_retry_carries_new_provider_handle` | Same: no `not_before` is passed. |

The `[claude]` param and the four existing `TestActivatePmRemote` tests pass. Record the summary line.

**Step 5.3 — GREEN code.** In `commander/src/ironclaude/orchestrator_mcp.py`:

Edit 5.3a (add the stat helper). Replace

```python
        return pids

    def _activate_pm_remote(
        self, session_name: str, ssh_host: str,
        timeout: int = 30, client: str = "claude",
    ) -> str | None:
        """Activate professional mode on a remote worker via sqlite3 over SSH.

        Returns the validated provider-native UUID on success, or a failure
        reason string.
        """
```

with

```python
        return pids

    def _remote_id_file_mtime(self, ssh_host: str, pid: str) -> float | None:
        """Remote mtime of ~/.claude/ironclaude-session-<pid>.id, or None.

        run_argv quotes every argument, so ``~`` would not expand; the path is
        built from $HOME inside ``sh -c``. GNU/busybox ``stat -c %Y`` is tried
        first (GNU ``-f`` is file-system mode), then BSD/macOS ``stat -f %m``.
        None — no stat, no file, or unparsable output — means reject the file.
        """
        if self._ssh_manager is None:
            return None
        script = (
            'f="$HOME/.claude/ironclaude-session-$1.id"; '
            'stat -c %Y "$f" 2>/dev/null || stat -f %m "$f"'
        )
        try:
            result = self._ssh_manager.run_argv(
                ssh_host, ["sh", "-c", script, "sh", str(pid)],
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip())
        except ValueError:
            return None

    def _activate_pm_remote(
        self, session_name: str, ssh_host: str,
        timeout: int = 30, client: str = "claude",
        not_before: float | None = None,
    ) -> str | None:
        """Activate professional mode on a remote worker via sqlite3 over SSH.

        With ``not_before`` (Codex), an id file is accepted only when its remote
        mtime is at or after that time; an unstatable file is rejected.
        Returns the validated provider-native UUID on success, or a failure
        reason string.
        """
```

Edit 5.3b. Replace

```python
                if content and len(content.strip()) == 36:
                    session_uuid = content.strip()
                    break
```

with

```python
                if content and len(content.strip()) == 36:
                    if not_before is not None:
                        mtime = self._remote_id_file_mtime(
                            ssh_host, candidate_pid,
                        )
                        if mtime is None or mtime < not_before:
                            continue
                    session_uuid = content.strip()
                    break
```

Edit 5.3c (`spawn_worker`; `profile_dispatched_at` was added in Task 3). Replace

```python
            remote_pm_result = self._activate_pm_remote(
                session_name, ssh_host, client=worker_client,
            )
```

with

```python
            remote_pm_kwargs = (
                {"not_before": profile_dispatched_at}
                if worker_client == "codex" else {}
            )
            remote_pm_result = self._activate_pm_remote(
                session_name, ssh_host, client=worker_client,
                **remote_pm_kwargs,
            )
```

**Step 5.4 — GREEN run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q`

Expected: all pass, including the RED tests. Record the summary line. Any other failure means stop and report it.

**Step 5.5 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py`

---

## Task 6: Codex resume follows the R1 order (R11)

**Files:** `commander/src/ironclaude/orchestrator_mcp.py`, `commander/tests/test_orchestrator_mcp.py`

**Step 6.1 — Add the RED tests.** In `TestResumeSession`, insert immediately before `    def test_mcp_wrapper_requires_and_forwards_client(self, tools):`. The existing `test_lifecycle_failure_kills_without_registration_or_success_event[uuid-mismatch]` already covers the fail-closed identity mismatch for Codex.

```python
    @pytest.mark.parametrize(
        ("client", "expected"),
        [
            ("codex", ["profile", "activate", "identity"]),
            ("claude", ["activate", "identity", "profile"]),
        ],
    )
    def test_resume_profile_turn_order_by_client(
        self, tools, mock_tmux, client, expected,
    ):
        self._prepare_success(tools, mock_tmux, client)
        order = []
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: order.append("profile"),
        )
        tools._activate_pm_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("activate")
        )
        tools._read_pm_state_via_sqlite.side_effect = lambda *args, **kwargs: (
            order.append("identity") or {
                "professional_mode": "on", "workflow_stage": "executing",
                "session_uuid": self.UUID,
            }
        )

        out = tools.resume_session(self.UUID, client, f"d-order-{client}", repo="/r")

        assert out["worker_id"] == f"d-order-{client}"
        assert order == expected

    def test_codex_resume_identity_boundary_is_profile_dispatch_time(
        self, tools, mock_tmux,
    ):
        clock = itertools.count(1000.0, 1.0)
        marks = {}
        self._prepare_success(tools, mock_tmux, "codex")
        mock_tmux.spawn_session.side_effect = lambda *args, **kwargs: (
            marks.__setitem__("spawn", next(clock)) or True
        )
        tools._dispatch_worker_communication_profile = MagicMock(
            side_effect=lambda *args, **kwargs: marks.__setitem__(
                "profile", next(clock),
            ),
        )

        with patch(
            "ironclaude.orchestrator_mcp.time.time",
            side_effect=lambda: next(clock),
        ):
            tools.resume_session(self.UUID, "codex", "d-nb", repo="/r")

        not_before = tools._activate_pm_via_sqlite.call_args.kwargs["not_before"]
        assert marks["spawn"] < not_before < marks["profile"]
        assert (
            tools._read_pm_state_via_sqlite.call_args.kwargs["not_before"]
            == not_before
        )

    def test_codex_resume_profile_failure_kills_before_activation(
        self, tools, registry, mock_tmux,
    ):
        self._prepare_success(tools, mock_tmux, "codex")
        tools._dispatch_worker_communication_profile = MagicMock(
            return_value="profile failed",
        )

        out = tools.resume_session(self.UUID, "codex", "d-profile-fail", repo="/r")

        assert out == {"error": "profile failed"}
        tools._activate_pm_via_sqlite.assert_not_called()
        mock_tmux.kill_session.assert_called_once_with("ic-d-profile-fail")
        assert registry.get_worker("d-profile-fail") is None

```

**Step 6.2 — RED run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "ResumeSession or resume_launch_boundary"`

Expected failures:

| Test | Why it fails today |
|---|---|
| `test_resume_profile_turn_order_by_client[codex]` | The profile turn comes after identity. |
| `test_codex_resume_identity_boundary_is_profile_dispatch_time` | `not_before` is the pre-spawn launch time. |
| `test_codex_resume_profile_failure_kills_before_activation` | Activation runs before the profile turn. |

The `[claude]` param, all existing `TestResumeSession` tests and `test_resume_launch_boundary_is_shared_by_activation_and_readback` pass. Record the summary line.

**Step 6.3 — GREEN code** (`resume_session`), in this order.

Edit 6.3a (Claude-only profile dispatch after identity). Replace

```python
            profile_error = self._dispatch_worker_communication_profile(
                target, client,
            )
            if profile_error is not None:
                return {"error": profile_error}

            self.registry.register_worker(
```

with

```python
            if client != "codex":
                profile_error = self._dispatch_worker_communication_profile(
                    target, client,
                )
                if profile_error is not None:
                    return {"error": profile_error}

            self.registry.register_worker(
```

Edit 6.3b (Codex profile turn before activation; the `finally` kills the session on failure). Replace

```python
            pm_identity_kwargs = (
                {"not_before": launch_started_at} if client == "codex" else {}
            )
```

with

```python
            # Codex writes its own id file only on its first submitted turn,
            # so the profile turn goes first (see spawn_worker).
            profile_dispatched_at = None
            if client == "codex":
                profile_dispatched_at = time.time()
                profile_error = self._dispatch_worker_communication_profile(
                    target, client,
                )
                if profile_error is not None:
                    return {"error": profile_error}
            pm_identity_kwargs = (
                {"not_before": profile_dispatched_at} if client == "codex" else {}
            )
```

Edit 6.3c. Replace

```python
        launch_started_at = time.time()
        success = self.tmux.spawn_session(target, cmd, cwd=repo)
```

with

```python
        success = self.tmux.spawn_session(target, cmd, cwd=repo)
```

The existing check `pm.get("session_uuid") != session_id` (`:6612-6620`) stays, and it is the fail-closed identity check required by R11.

Then run `grep -n "launch_started_at" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py`. Expected: no output.

**Step 6.4 — GREEN run.**
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py tests/test_communication_routing.py -q`

Expected: all pass. Record the summary line.

An existing resume test may fail because it asserts the old Codex order. Update it to the new order and name it in the evidence; Claude assertions must not change. Any other failure means stop and report.

**Step 6.5 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py`

---

## Task 7: `state-activator.sh` claims only its own PPID's marker (R6)

**Files:**
- `worker/hooks/hook-logger.sh`
- `worker/hooks/session-init.sh`
- `worker/hooks/state-activator.sh`
- `worker/hooks/tests/test-state-activator-ppid-scope.sh` (new)
- `worker/hooks/tests/test-state-activator-single-object.sh`

**Step 7.1 — Add the RED hook test.** Create `worker/hooks/tests/test-state-activator-ppid-scope.sh`:

```bash
#!/usr/bin/env bash
# test-state-activator-ppid-scope.sh - state-activator.sh claims only the pending
# PPID marker of its own client process (the PID session-init.sh writes as
# REAL_PPID). A marker for any other PID belongs to another session and is left
# untouched, so this session's id is never stamped onto another client's PID.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-sa-ppid-scope"
FOREIGN_PID=999999
PASSES=0
FAILS=0
EXPECTED_PASSES=4
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

touch "$TMP_HOME/.claude/ironclaude-ppid-pending-$FOREIGN_PID"
PAYLOAD=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:"hello"}')

# The wrapper's PID is the hook's parent PID, so the marker it plants is the
# hook's own. The hook is not the wrapper's last command, so bash cannot exec it.
HOME="$TMP_HOME" bash -c '
  printf "%s" "$$" > "$3"
  touch "$HOME/.claude/ironclaude-ppid-pending-$$"
  bash "$1/state-activator.sh" <<< "$2" > /dev/null 2>&1
  exit 0' _ "$HOOKS_DIR" "$PAYLOAD" "$TMP_ROOT/own-pid"
OWN_PID=$(cat "$TMP_ROOT/own-pid")

if [ ! -e "$TMP_HOME/.claude/ironclaude-ppid-pending-$OWN_PID" ]; then
  ok "1 own marker consumed"
else
  bad "1 own marker consumed"
fi
if [ "$(cat "$TMP_HOME/.claude/ironclaude-session-$OWN_PID.id" 2>/dev/null)" = "$SESSION" ]; then
  ok "2 own session file holds this session id"
else
  bad "2 own session file holds this session id"
fi
if [ -e "$TMP_HOME/.claude/ironclaude-ppid-pending-$FOREIGN_PID" ]; then
  ok "3 foreign marker untouched"
else
  bad "3 foreign marker untouched"
fi
if [ ! -e "$TMP_HOME/.claude/ironclaude-session-$FOREIGN_PID.id" ]; then
  ok "4 no session file written for the foreign PID"
else
  bad "4 no session file written for the foreign PID"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

Edit 7.1b (`worker/hooks/tests/test-state-activator-single-object.sh`). Test 6 planted the marker for PID 424242. After R6 that marker is foreign: it is skipped, so `mktemp` never runs and the ERR path is never reached. Replace

```bash
# 6. ERR path: a pending PPID marker in a read-only ~/.claude makes the unguarded
#    mktemp at state-activator.sh:57 fail under run_hook's ERR trap, which logs and
#    exits 1. The flush keeps exit code 1 and delivers the failure as context.
touch "$TMP_HOME/.claude/ironclaude-ppid-pending-424242"
chmod 555 "$TMP_HOME/.claude"
ERR_OUT=$(run_prompt "hello")
ERR_RC=$(cat "$TMP_ROOT/rc")
chmod 755 "$TMP_HOME/.claude"
```

with

```bash
# 6. ERR path: the hook's OWN pending PPID marker in a read-only ~/.claude makes
#    the unguarded mktemp in the marker handshake fail under run_hook's ERR trap,
#    which logs and exits 1. The flush keeps exit code 1 and delivers the failure
#    as context. The hook claims only its own marker, so a wrapper whose PID is
#    the hook's parent PID plants it; the hook is not the wrapper's last command.
ERR_PAYLOAD=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:"hello"}')
ERR_OUT=$(HOME="$TMP_HOME" bash -c '
  touch "$HOME/.claude/ironclaude-ppid-pending-$$"
  chmod 555 "$HOME/.claude"
  bash "$1/state-activator.sh" <<< "$2" 2>/dev/null
  echo $? > "$3"
  chmod 755 "$HOME/.claude"
  exit 0' _ "$HOOKS_DIR" "$ERR_PAYLOAD" "$TMP_ROOT/rc")
ERR_RC=$(cat "$TMP_ROOT/rc")
```

**Step 7.2 — RED run.**
`bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-ppid-scope.sh; echo "exit=$?"`

Expected: `FAIL  3 foreign marker untouched` and `FAIL  4 no session file written for the foreign PID`, because today's loop claims every marker. `PASS` for 1 and 2, `Results: 2 pass, 2 fail`, and `exit=1`.

Then run `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh; echo "exit=$?"`. Expected: `Results: 6 pass, 0 fail`, `exit=0`. The rewritten test 6 already passes, because its marker is the hook's own.

**Step 7.3 — GREEN code.**

Edit 7.3a (`worker/hooks/hook-logger.sh`). Replace

```bash
  SESSION_TAG="${CLAUDE_SESSION_ID:-none}"
}

# Client detection + skill-invocation rendering, shared by the enforcement hooks so
```

with

```bash
  SESSION_TAG="${CLAUDE_SESSION_ID:-none}"
}

# ic_real_ppid - Print the PID of the client process that runs this hook: the
# parent whose PID MCP wrappers see as process.ppid and key their files by.
# On MSYS2/Git Bash, $PPID=1 because the client isn't an MSYS process, and it
# spawns bash through an intermediate bash layer (bash → bash → claude.exe), so
# walk up the process tree skipping bash.exe intermediaries. Uses PowerShell
# Get-CimInstance (always available on Windows 10/11; wmic was removed on
# Windows 11 24H2+), with a wmic fallback for older Windows.
ic_real_ppid() {
  local bash_winpid real_ppid
  if [ -f /proc/$$/winpid ]; then
    bash_winpid=$(cat /proc/$$/winpid)
    # Guard: validate bash_winpid is an integer before interpolating into PowerShell/wmic
    if [[ ! "$bash_winpid" =~ ^[0-9]+$ ]]; then
      real_ppid="$PPID"
    else
      # Single PowerShell invocation to minimize startup latency (~1-2s).
      real_ppid=$(powershell.exe -NoProfile -Command '
        $cur = '"$bash_winpid"'
        do {
          $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur)
          if (-not $proc) { break }
          $cur = $proc.ParentProcessId
          $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur)
        } while ($parent -and $parent.Name -eq "bash.exe")
        Write-Output $cur
      ' 2>/dev/null | tr -d '\r\n') || true
      if [ -z "$real_ppid" ]; then
        # Fallback: try wmic for older Windows (pre-24H2) where PowerShell may lack Get-CimInstance
        real_ppid=$(MSYS_NO_PATHCONV=1 wmic process where "ProcessId=$bash_winpid" get ParentProcessId /value 2>/dev/null | sed -n 's/ParentProcessId=//p' | tr -d '\r\n') || true
      fi
      real_ppid="${real_ppid:-$PPID}"
    fi
  else
    real_ppid="$PPID"
  fi
  printf '%s' "$real_ppid"
}

# Client detection + skill-invocation rendering, shared by the enforcement hooks so
```

Edit 7.3b (`worker/hooks/session-init.sh`, currently `:327-366`). Replace

```bash
# ═══ PPID session file (MCP-only) ═══
# Write session ID to PPID-keyed file so MCP servers can resolve their session.
# MCP servers read this via process.ppid — both share the claude process as parent.
# NOTE: Hooks do NOT use this file. They read session_id from the JSON payload.
#
# On MSYS2/Git Bash, $PPID=1 because Claude Code isn't an MSYS process.
# Claude Code spawns bash through an intermediate bash layer
# (bash → bash → claude.exe), so we walk up the process tree skipping
# bash.exe intermediaries to find claude.exe's PID. This matches what
# Node.js process.ppid returns in the MCP wrapper.
#
# Uses PowerShell Get-CimInstance (always available on Windows 10/11).
# wmic was removed on Windows 11 24H2+.
if [ -f /proc/$$/winpid ]; then
  BASH_WINPID=$(cat /proc/$$/winpid)
  # Guard: validate BASH_WINPID is an integer before interpolating into PowerShell/wmic
  if [[ ! "$BASH_WINPID" =~ ^[0-9]+$ ]]; then
    REAL_PPID="$PPID"
  else
    # Walk up process tree, skipping bash.exe intermediaries, to find Claude Code PID.
    # Single PowerShell invocation to minimize startup latency (~1-2s).
    REAL_PPID=$(powershell.exe -NoProfile -Command '
      $cur = '"$BASH_WINPID"'
      do {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur)
        if (-not $proc) { break }
        $cur = $proc.ParentProcessId
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $cur)
      } while ($parent -and $parent.Name -eq "bash.exe")
      Write-Output $cur
    ' 2>/dev/null | tr -d '\r\n') || true
    if [ -z "$REAL_PPID" ]; then
      # Fallback: try wmic for older Windows (pre-24H2) where PowerShell may lack Get-CimInstance
      REAL_PPID=$(MSYS_NO_PATHCONV=1 wmic process where "ProcessId=$BASH_WINPID" get ParentProcessId /value 2>/dev/null | sed -n 's/ParentProcessId=//p' | tr -d '\r\n') || true
    fi
    REAL_PPID="${REAL_PPID:-$PPID}"
  fi
else
  REAL_PPID="$PPID"
fi
```

with

```bash
# ═══ PPID session file (MCP-only) ═══
# Write session ID to PPID-keyed file so MCP servers can resolve their session.
# MCP servers read this via process.ppid — both share the claude process as parent.
# NOTE: Hooks do NOT use this file. They read session_id from the JSON payload.
# ic_real_ppid (hook-logger.sh) resolves that parent, including on MSYS2/Git Bash.
REAL_PPID=$(ic_real_ppid)
```

The next line, `# ═══ Capture-then-adjudicate PPID file write ═══`, and everything after it stay unchanged.

Edit 7.3c (`worker/hooks/state-activator.sh`). Replace

```bash
# ═══ Pending PPID marker handshake ═══
# MCP wrappers write ironclaude-ppid-pending-{PPID} when no session file exists
# (e.g., after plugin reload). Write the session file so MCP retry loop can bind.
for marker in "$HOME/.claude"/ironclaude-ppid-pending-*; do
  [ -f "$marker" ] || continue
  ppid_val=$(basename "$marker" | sed 's/ironclaude-ppid-pending-//')
  # Validate ppid_val is numeric (defense against unexpected filenames)
  if ! [[ "$ppid_val" =~ ^[0-9]+$ ]]; then
    rm -f "$marker"
    continue
  fi
  ppid_file="$HOME/.claude/ironclaude-session-${ppid_val}.id"
  if [ ! -f "$ppid_file" ]; then
    TMP_PPID=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
    printf '%s' "$SESSION_TAG" > "$TMP_PPID"
    mv "$TMP_PPID" "$ppid_file"
    log_hook "state-activator" "PPID" "wrote session file from pending marker: $ppid_file"
  fi
  rm -f "$marker"
done
```

with

```bash
# ═══ Pending PPID marker handshake ═══
# MCP wrappers write ironclaude-ppid-pending-{PPID} when no session file exists
# (e.g., after plugin reload). Write the session file so MCP retry loop can bind.
# Claim only this hook's own client's marker ({PPID} = REAL_PPID, derived as
# session-init.sh derives it). Another PID's marker belongs to another session;
# claiming it would stamp this session's id onto that client's PID.
REAL_PPID=$(ic_real_ppid)
marker="$HOME/.claude/ironclaude-ppid-pending-${REAL_PPID}"
if [[ "$REAL_PPID" =~ ^[0-9]+$ ]] && [ -f "$marker" ]; then
  ppid_file="$HOME/.claude/ironclaude-session-${REAL_PPID}.id"
  if [ ! -f "$ppid_file" ]; then
    TMP_PPID=$(mktemp "$HOME/.claude/.ironclaude-session-XXXXXX")
    printf '%s' "$SESSION_TAG" > "$TMP_PPID"
    mv "$TMP_PPID" "$ppid_file"
    log_hook "state-activator" "PPID" "wrote session file from pending marker: $ppid_file"
  fi
  rm -f "$marker"
fi
```

**Step 7.4 — GREEN run.**
`bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-ppid-scope.sh; echo "exit=$?"`
Expected: `Results: 4 pass, 0 fail`, `exit=0`.

`bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-state-activator-single-object.sh; echo "exit=$?"`
Expected: `Results: 6 pass, 0 fail`, `exit=0`.

`make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-codex-spawn-fix-task5-hooks.log 2>&1; echo "exit=$?"`
Expected: `exit=0`. Record `tail -n 20 /private/tmp/ironclaude-codex-spawn-fix-task5-hooks.log`.

**Step 7.5 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hook-logger.sh worker/hooks/session-init.sh worker/hooks/state-activator.sh worker/hooks/tests/test-state-activator-ppid-scope.sh worker/hooks/tests/test-state-activator-single-object.sh`

---

## Task 8: Correct the repro-v2 findings (R8)

**Files:** `docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`

No tests required: documentation.

**Step 8.1.** Replace the headline bullet

```markdown
- **The id file takes about 125 s.** It appears even with no turn submitted, and is keyed to the pane PID.
```

with

```markdown
- **The id file at about 125 s was not the worker's.** Another session's prompt hook stamped it from the worker's pending marker (corrected conclusion 4).
```

**Step 8.2.** Replace conclusions 4, 5 and 7, using three separate edits. Conclusion 6 stays unchanged between them. The old texts are, in order:

```markdown
4. **Q2, id-file timing.** VERIFIED (B).
   - The in-tree id file appears about 125 s after launch with no submitted turn, keyed to the pane PID.
   - That leaves the Commander's 300 s window about 175 s of margin per attempt. Any other startup delay erodes it.
   - The "only after a submitted turn" hypothesis is REFUTED: SessionStart fired before any turn.
```

```markdown
5. **Cause of the ~2-minute delay.** UNRESOLVED (strong hypothesis). The global `~/.codex/config.toml` defines `[mcp_servers.node_repl]` (ChatGPT.app's `cua_node/bin/node_repl`) with `startup_timeout_sec = 120`. The ~114 s gap between readiness and the id file fits Codex waiting out that MCP startup timeout before firing SessionStart. This was not tested. The fix loop's first check should launch a worker with that server disabled (for example `-c mcp_servers.node_repl.enabled=false`) and time the id file.
```

```markdown
7. **Startup session id.** UNRESOLVED. Before the first turn, Codex's SessionStart `session_id` was not the worker's own thread id. It is rewritten to the real thread id once a turn runs. Binding PM activation to the startup id could bind the wrong session. In production the Claude variables are removed, so the startup value there is unknown and must be checked in the fix loop's live spawn.
```

The replacements are, in the same order:

```markdown
4. **Q2, id-file timing.** CORRECTED (2026-10-10).
   - The worker's state-manager wrapper started at about 7 s with `PPID=32293`. It found no session file and wrote `~/.claude/ironclaude-ppid-pending-32293` (`~/.claude/ironclaude-mcp-state-manager.log:43628-43629`).
   - The probe operator's own Claude session then ran `state-activator.sh` (UserPromptSubmit). Its handshake (`worker/hooks/state-activator.sh:48-64`) claims every global pending marker, so it wrote its own session id into `ironclaude-session-32293.id`. That write is the file seen at 125 s.
   - Current Codex runs SessionStart, and so `session-init.sh` and the worker's own id file, only on the first submitted turn. The worker's SessionStart rewrote the file with its thread id only after Enter 2.
   - The 125 s is therefore the timing of another session's prompt, not a Codex startup delay. The "only after a submitted turn" hypothesis holds.
```

```markdown
5. **Cause of the ~2-minute delay.** VOID. Corrected conclusion 4 shows there is no Codex startup delay to explain. The `node_repl` hypothesis is withdrawn, and no MCP server needs disabling.
```

```markdown
7. **Startup session id.** RESOLVED (2026-10-10). The first content, `7c7f63a0…`, was written by the operator's Claude session through the pending-marker handshake, not by Codex. The worker's own id file holds its Codex thread id only after its first submitted turn. Binding must therefore wait for that turn and accept only an id file written after it, and the handshake must claim only its own client's marker.
```

**Step 8.3.** In "Relation to the production failures (2026-10-06)":
- Replace `activation finished in about 2 minutes, which matches conclusion 4.` with `activation finished in about 2 minutes. Corrected conclusion 4 no longer explains that timing; this probe did not determine its cause.`
- Replace `The likeliest explanations are a longer startup wait (conclusion 5) or the id file going to a non-tree PID. That run is gone, so this is UNRESOLVED; the fix loop's live spawn decides it.` with `This fits corrected conclusion 4: the Commander activated before any turn was submitted, so the worker never ran SessionStart and never wrote its own id file. That run is gone, so its exact cause is not verified.`

**Step 8.4.** In "Inputs for the spawn-fix design", replace items 4 and 5:

```markdown
4. **Startup delay.** Confirm the `node_repl` 120 s hypothesis with a live spawn. If confirmed, disable non-worker MCP servers on the worker command line (`-c mcp_servers.node_repl.enabled=false` and the like), or otherwise bound startup.
5. **Session binding.** Verify that the id file's uuid is the worker's own Codex thread before binding. The id file can be rewritten after the first turn.
```

with

```markdown
4. **Spawn order (corrected).** No startup delay exists (corrected conclusion 4), so no MCP server needs disabling. For Codex, send the profile turn before waiting for the session id, because the worker writes its own id file only on its first submitted turn.
5. **Session binding (corrected).** Accept only an id file written after the profile turn was dispatched, and make `state-activator.sh` claim only its own client's pending marker. Then no other session can stamp the worker's PID.
```

**Step 8.5 — Verify.**
`grep -n -e "CORRECTED" -e "VOID" -e "RESOLVED (2026-10-10)" -e "corrected" /Users/roberthyatt/Code/ironclaude/docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`
Expected: at least 7 matching lines (headline, conclusions 4, 5 and 7, spawn 1, Inputs 4 and 5); record them.

`grep -c "node_repl" /Users/roberthyatt/Code/ironclaude/docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`
Expected: `1`, the withdrawn hypothesis named in conclusion 5. The old conclusion 5 text and the old Input 4 are gone. Record the actual count.

**Step 8.6 — Stage.**
`git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`

---

## Task 9: Full suites (R9)

**Depends on:** Tasks 6, 7 and 8 (6 chains after 1→2→3→4→5, which all edit `orchestrator_mcp.py`).

**Files:**
- Create: `docs/reviews/2026-10-10-codex-worker-spawn-fix-suites.md` (suite record; logs stay under `/private/tmp/ironclaude-codex-spawn-fix/`)

**Step 9.1.** `mkdir -p /private/tmp/ironclaude-codex-spawn-fix`

**Step 9.2 — Full Commander pytest.** Run in the background, because it can exceed 600 s:
`cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-codex-spawn-fix/pytest-full.log 2>&1; echo "pytest exit=$?"`

Wait for the completion notification, then run `tail -n 40 /private/tmp/ironclaude-codex-spawn-fix/pytest-full.log`.

Expected: the final summary line, recorded verbatim. The only acceptable failure is the pre-existing `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd` (L-T2). Any other failure blocks the task; investigate and report.

**Step 9.3 — Hook suites.**
`make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-codex-spawn-fix/test-hooks.log 2>&1; echo "test-hooks exit=$?"`
Expected: `test-hooks exit=0`. Record `tail -n 20 /private/tmp/ironclaude-codex-spawn-fix/test-hooks.log`.

**Step 9.4.** `git -C /Users/roberthyatt/Code/ironclaude diff --cached --stat`
Expected: the staged set includes the files from Tasks 1-8. Record it.

**Step 9.5 — Write the suite record.** Write `docs/reviews/2026-10-10-codex-worker-spawn-fix-suites.md` with these, all verbatim: the pytest final summary line and exit code, the test-hooks exit code and the last 20 log lines, and the staged-set stat.

**Step 9.6 — Stage the record.**
`git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-10-codex-worker-spawn-fix-suites.md`
Expected: no output.

---

## Task 10: Live Codex spawn through the product `spawn_worker` (R9)

**Files:**
- `docs/reviews/2026-10-10-codex-worker-spawn-fix-live_spawn.py` (evidence script, force-staged)
- `docs/reviews/2026-10-10-codex-worker-spawn-fix-live-verification.md`

The scratch repo, `run.log`, `start.ref`, the scratch DB, logs and `pane-final.txt` stay under the session scratchpad. Commands or the script create them, and they are not committed.

**What this exercises and what it does not.** The script constructs the real `OrchestratorTools` from the working-tree Commander source, as `orchestrator_mcp.main` does at `:8229-8271`. It wires in a scratch Commander DB, a scratch tmux log dir, real `TmuxManager`, `WorkerRegistry` and `WorkspaceClient`, and the operator's `config/ironclaude.json`, which routes workers to Codex. It then calls `spawn_worker` for a Codex worker on a scratch git repo. Two things differ from a production spawn:

- **Graders are stubbed to approve.** They are not under test, and the real grader would run `claude -p`.
- **The plugin is the installed one.** `discover_installed_plugin_root` makes the worker run the installed plugin's hooks and workspace-manager. R6 is therefore not exercised live until the plugin is redeployed. Another session's prompt can still stamp the worker's pending marker during the run. The check in Step 10.6 detects that case.

PM state is written to `~/.claude/ironclaude.db`, as in production. The live Commander DB `commander/data/db/ironclaude.db` is never opened.

tmux runs on a fresh server under `TMUX_TMPDIR=/private/tmp/ironclaude-codex-spawn-fix/tmux`. The scratchpad path is too long for a tmux socket. The fresh server inherits only the scrubbed environment, and every tmux command in this task uses the same `TMUX_TMPDIR` with `TMUX` unset.

**Step 10.1 — Prepare the scratch repo and directories.**
`mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo /private/tmp/ironclaude-codex-spawn-fix/tmux`
`git -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo init -q`
`git -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo -c user.name=live -c user.email=live@local commit -q --allow-empty -m init`
Expected: no output. `git -C …/repo log --oneline` shows one commit.

**Step 10.2 — List the variables to strip.**
`env | grep -E '^(CLAUDE|TMUX)' | cut -d= -f1`
Expected: a list of names only, never values. Record it in the evidence doc. Step 10.4 removes each one with `-u`.

**Step 10.3 — Write the script.** Write `/Users/roberthyatt/Code/ironclaude/docs/reviews/2026-10-10-codex-worker-spawn-fix-live_spawn.py`:

```python
"""Live check: spawn one Codex worker through OrchestratorTools.spawn_worker.

Run from commander/ with the commander venv. It uses a scratch Commander DB and
a scratch git repo, stubs only the graders, and prints one JSON evidence line
per stage. It kills only the exact session it created.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

LIVE = Path(
    "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/"
    "7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live"
)
TMUX_DIR = "/private/tmp/ironclaude-codex-spawn-fix/tmux"
REPO = LIVE / "repo"
WORKER_ID = "codexlive1"
SESSION = f"ic-{WORKER_ID}"
OBJECTIVE = "Live spawn check: reply with the single word ok and stop. Do not edit files."
OPERATOR_SESSION_ID = "7c7f63a0-e272-4e34-a05b-3d6ebc128c94"


def emit(stage, **data):
    print(json.dumps({"stage": stage, **data}, default=str), flush=True)


# Defense in depth: the launcher already strips these with env -u.
removed = sorted(
    key for key in os.environ
    if key.startswith("CLAUDE") or key in ("TMUX", "TMUX_PANE")
)
for key in removed:
    del os.environ[key]
if os.environ.get("TMUX_TMPDIR") != TMUX_DIR:
    sys.exit(f"TMUX_TMPDIR must be {TMUX_DIR}")
emit("env", removed=removed, tmux_tmpdir=os.environ["TMUX_TMPDIR"])

import ironclaude  # noqa: E402
from ironclaude.config import load_config  # noqa: E402
from ironclaude.db import init_db  # noqa: E402
from ironclaude.orchestrator_mcp import OrchestratorTools  # noqa: E402
from ironclaude.tmux_manager import TmuxManager  # noqa: E402
from ironclaude.worker_registry import WorkerRegistry  # noqa: E402

emit("import", ironclaude_file=ironclaude.__file__)
(LIVE / "logs").mkdir(parents=True, exist_ok=True)
cfg = load_config()
conn = init_db(str(LIVE / "commander-scratch.db"))
tools = OrchestratorTools(
    WorkerRegistry(conn), TmuxManager(log_dir=str(LIVE / "logs")),
    db_conn=conn, config=cfg,
)
approve = {
    "grade": "A", "approved": True,
    "feedback": "live verification stub", "confidence": "high",
}
tools._call_local_grader = lambda *args, **kwargs: dict(approve)
tools._call_grader = lambda *args, **kwargs: dict(approve)

started = time.time()
result = tools.spawn_worker(WORKER_ID, "claude-sonnet", str(REPO), OBJECTIVE)
emit("spawn", seconds=round(time.time() - started, 1), result=result)
worker = tools.registry.get_worker(WORKER_ID)
emit("worker", worker=worker)

native = worker.get("native_session_id") if worker else None
if native:
    db = sqlite3.connect(
        f"file:{Path.home() / '.claude' / 'ironclaude.db'}?mode=ro", uri=True,
    )
    row = db.execute(
        "SELECT professional_mode, workflow_stage FROM sessions"
        " WHERE terminal_session=?",
        (native,),
    ).fetchone()
    db.close()
    emit(
        "pm", native_session_id=native, row=row,
        is_operator_session=(native == OPERATOR_SESSION_ID),
    )

time.sleep(15)
screen = (
    tools.tmux.capture_pane(SESSION, lines=500)
    if tools.tmux.has_session(SESSION) else ""
)
(LIVE / "pane-final.txt").write_text(screen)
compact = "".join(screen.split())
emit(
    "pane",
    objective_seen="".join(OBJECTIVE.split()) in compact,
    marker_seen="IC_LOSSLESS_AI_MESSAGES_ACTIVE" in compact,
)

emit("kill", session=SESSION, killed=tools.tmux.kill_session(SESSION))
if worker and native and worker.get("workspace_guid"):
    transport = tools._workspace_transport(
        tools._workspace_client.discover_installed_plugin_root("codex"), None,
    )
    try:
        released = tools._workspace_client.abandon(
            {
                "repository_path": str(REPO),
                "workspace_guid": worker["workspace_guid"],
                "owner_session_id": native,
                "mode": "rescue",
            },
            **transport,
        )
        emit("release", lifecycle=released.get("lifecycle_status"))
    except Exception as exc:  # noqa: BLE001
        emit("release", error=str(exc))
```

**Step 10.4 — Check the import source, then run.**

First, `cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -c "import ironclaude; print(ironclaude.__file__)"`
Expected: `/Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/__init__.py`. If not, stop and report.

Then `touch /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/start.ref`.

Run in the background, because spawn can take up to `pm_timeout` = 300 s plus readiness. Add one `-u NAME` for every name Step 10.2 printed:
`cd /Users/roberthyatt/Code/ironclaude/commander && env -u TMUX -u TMUX_PANE <one -u per CLAUDE* name from Step 10.2> TMUX_TMPDIR=/private/tmp/ironclaude-codex-spawn-fix/tmux PYTHONUNBUFFERED=1 .venv/bin/python /Users/roberthyatt/Code/ironclaude/docs/reviews/2026-10-10-codex-worker-spawn-fix-live_spawn.py > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/run.log 2>&1; echo "live exit=$?"`

Wait for the completion notification, then run `cat /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/run.log`.

Expected:

| Stage | Expected output |
|---|---|
| `env` | `removed` is `[]`, because `env -u` already stripped them. |
| `import` | Path under `commander/src`. |
| `spawn` | `result` is the string `Worker 'codexlive1' spawned (...)`. |
| `worker` | `client` = `codex`, plus a UUID `native_session_id`. |
| `pm` | `row[0]` = `on` and `is_operator_session` = false. |
| `pane` | `objective_seen` = true and `marker_seen` = true. |
| `kill` | `killed` = true. |
| `release` | Lifecycle `abandoned`. |

If `spawn` returns an error dict, record it verbatim, run Step 10.7, and stop. Do not patch code in this task; report the failure phase. The known risk is a trust modal drawn after readiness; `pane-final.txt` shows it.

**Step 10.5 — Read-only DB check.** Use the `native_session_id` printed by the `pm` stage:
`sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude.db "SELECT terminal_session, professional_mode, workflow_stage FROM sessions WHERE terminal_session='<native_session_id>'"`
Expected: `<native_session_id>|on|…`.

Confirm the id is not this session's: compare it with `7c7f63a0-e272-4e34-a05b-3d6ebc128c94`, and with the executing session's id if that differs. Expected: different.

**Step 10.6 — Prove the id is the worker's own Codex thread.**
`find /Users/roberthyatt/.codex/sessions -name "*<native_session_id>*" -newer /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/start.ref`
Expected: exactly one rollout file, which shows the bound id is a Codex thread created during this run. If none is found, the id came from another writer: record it as a failure of the session-binding requirement.

**Step 10.7 — Cleanup and leftover state.** List or kill only exact names and PIDs.
- `env -u TMUX TMUX_TMPDIR=/private/tmp/ironclaude-codex-spawn-fix/tmux tmux has-session -t ic-codexlive1; echo "has-session exit=$?"`
  Expected: non-zero, because the session is gone. If it is still alive, run `env -u TMUX TMUX_TMPDIR=/private/tmp/ironclaude-codex-spawn-fix/tmux tmux kill-session -t ic-codexlive1`.
- `env -u TMUX TMUX_TMPDIR=/private/tmp/ironclaude-codex-spawn-fix/tmux tmux list-sessions; echo "list exit=$?"`
  Expected: non-zero (`no server running`), or no `ic-` sessions.
- `pgrep -lf codex-spawn-live`
  List only, never kill. Expected: no output. A survivor is killed only by its exact PID after `ps -p <pid> -o pid=,ppid=,command=` confirms it belongs to this run.
- `grep -n "codex-spawn-live" /Users/roberthyatt/.codex/config.toml`
  Record any trust row Codex added.
- `grep -c "codex-spawn-live" /Users/roberthyatt/.claude.json`
  Record the trust entry `ensure_worker_trusted` added.

**Step 10.8 — Evidence doc.** Write `docs/reviews/2026-10-10-codex-worker-spawn-fix-live-verification.md` with these sections:
- Date and Codex version (`codex --version`).
- What was exercised, and the stated gaps: stubbed graders; the installed plugin, so R6 is not live; and the scratch Commander DB.
- The environment names stripped (Step 10.2).
- Every `run.log` line, verbatim.
- The DB row (Step 10.5) and the rollout file (Step 10.6).
- The pane facts: objective seen, marker seen.
- Cleanup results and leftover state (Step 10.7).
- A one-line verdict per R9 item: PM `on` for the worker's own thread id; profile marker detected; objective delivered; cleanup by exact name.

**Step 10.9 — Stage the script and the evidence doc** (`docs/` is gitignored, so both need `-f`).
`git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-10-codex-worker-spawn-fix-live_spawn.py docs/reviews/2026-10-10-codex-worker-spawn-fix-live-verification.md`
Expected: no output. `git -C /Users/roberthyatt/Code/ironclaude diff --cached --stat` lists both files.

The final Fable review before commit (R9) runs after this plan, under the executing-plans workflow.
