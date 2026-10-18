# Handoff Recovery Fixes (L-H1) Implementation Plan

> **Goal:** Make the staged Claude↔Codex worker provider handoff safe to commit. Every failure path ends with the worker running again, the handoff moving forward, or the work preserved through the existing dead-session path. Native-evidence detection never blocks ordinary worker handling, and every lock left by a dead process is reclaimed.
> **Requirements:** `docs/plans/2026-10-06-handoff-recovery-fixes-requirements.md` (H1–H16)
> **Design:** `docs/plans/2026-10-06-handoff-recovery-fixes-design.md`
> **Architecture:**
> - Commander (`commander/src/ironclaude`):
>   - `main.py` daemon: the gate splits into `_maybe_start_handoff` and `_handoff_suppresses`, plus a startup sweep and post-stop preservation routing.
>   - `worker_quota.py` native reader.
>   - `worker_handoff.py` controller: release, forward retry, pre-transfer bootstrap stop, post-transfer recovery as the target, preserve, abort and lease rebuild.
>   - `worker_handoff_process.py` process proofs, including `proved_absent` for a deleted worktree.
>   - `worker_registry.py`: CAS release, dead-owner lease reaping and the bounded-wait acquire.
>   - `provider_state.py` / `db.py`: usage-quarantine expiry.
>   - `orchestrator_mcp.py`: the Brain wrapper wait and the `abort_handoff` route.
>   - `workspace_handoff_authority.py` / `workspace_client.py`: grant reaping and the three new private commands.
> - Workspace manager (`worker/mcp-servers/workspace-manager/src`):
>   - `handoff.ts`: process identity, dead-claim reaping and `releaseHandoff`.
>   - `cli.ts`: the `release-handoff`, `reap-stale-mutations` and `finish-handoff-after-target` internal commands.
>   - `integration.ts`: input validation before the mutation claim.
> - Hook tests: two scripts move from `rg` to `grep`.
> **Tech Stack:**
> - Python 3 with pytest. `psutil>=5.9.0` is already a declared dependency in `commander/pyproject.toml`, so this is not a blocker.
> - TypeScript with better-sqlite3, vitest 3 (`node_modules/.bin/vitest` exists), `tsc` (`node_modules/.bin/tsc` exists), esbuild through `npm run build`, and `vite-node` (`node_modules/.bin/vite-node` exists).
> - bash hook test scripts.
> **Loop: full** — brainstorm (design reused as-is, operator decision 2026-10-09 "Use for planning (Rec.)"), then this plan, then execute every task with the per-task code review, then the final verification task (Task 17), then a final Fable review before any commit. This loop precedes the Codex spawn fix ("L-H1 first (Rec.)"). Scope hold: implement H1–H16 plus the operator-approved additions:
> - H5a (Task 12): a dead source releases.
> - H5b (Task 16): a consumed exhaustion event is not re-claimed.
> - H6a (Tasks 13 and 15): recovery after ownership moved to the target.
> - H6b (Tasks 5, 7 and 15): a finish after the target commits accepts a HEAD that fast-forwards from the granted HEAD, only while the target owns the assignment.
> - The amended H6 retry (Task 13): no re-read of the stopped source's log.
> - H7a and H7b (Task 13): launch-intent-only and deleted-worktree proofs.
> - The H9 validity rule (Task 11).
>
> Accepted limitations (no code in this loop):
> - **L1:** the reader's other mid-scan `unknown` outcomes still hold the cursor.
> - **L2:** a bootstrap with only a launch intent recorded (no PID snapshot) plus a worktree deleted outside IronClaude still holds abort and preserve, because H7a's proof needs the directory.
> - **L3:** a live target that rewrites history between dispatch and finish stays `handoff_pending`; non-descendant finishes stay refused.
>
> No extra features, tooling or harnesses.

## Execution invariants

1. **Paths and shell:**
   - Every path is absolute, or the command uses `git -C /Users/roberthyatt/Code/ironclaude`. Shell state does not persist between commands.
   - The shell is zsh with `nomatch`. Quote every glob except the `for` loop in Task 17, whose three patterns all match.
   - No foreground `sleep`.
   - No `2>/dev/null` on any evidence command.
2. **Commands:**
   - Commander tests: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q <files or -k>`.
   - Workspace-manager tests: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager <files>`.
   - Type check: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json`. `src/__tests__` is inside `include`, so test files are type-checked too.
   - Dist rebuild: `npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager run build`. The `build` script is `tsc && npm run bundle`.
3. **No live state:**
   - Never restart the Commander. It runs as an editable install from `commander/src`.
   - Never touch `~/.ironclaude`, `~/.claude/ironclaude-workspaces.db` or any other live database.
   - Tests use `tmp_path` and temporary directories only. The startup sweep test injects a fake workspace client.
4. **Staging:**
   - No commits.
   - Stage only each task's `allowed_files`, with `git -C /Users/roberthyatt/Code/ironclaude add <paths>`.
   - `docs/` is gitignored, so docs need `git add -f`.
   - Never `git add README.md` before Task 17. It holds this effort's unstaged handoff README hunk, and Task 17 re-stages it explicitly.
5. **TDD per task:** RED test step, then run (the named tests must FAIL for the stated reason; record the actual pass/fail counts), then GREEN code, then run (record actual counts), then stage.
   - Expected results name test ids and reasons. They never predict totals; record the actual counts.
6. **Edits:**
   - Every code step is "replace exactly" with an anchor copied from the source as it stands **after all earlier tasks**. Anchors are unique. A multi-line anchor is used wherever a single line repeats.
   - Where a step says "replace the block from A through B", A and B are unique lines. Read the file and copy that exact block into the Edit `old_string`.
   - Where a step says `replace_all`, the expected occurrence count is stated. Confirm it with `grep -c -F` before editing.
7. **Process identity representation (H8, H10, H11):**
   - A process start time is **integer epoch seconds, floored**:
     - Python: `int(psutil.Process(pid).create_time())`;
     - TypeScript: `ps -o lstart= -p <pid>` run with `TZ=UTC LC_ALL=C`, parsed as UTC.
   - `workspace_mutations.process_identity` is the string `"<pid>:<start_epoch_seconds>"`, for example `"12345:1791201600"`.
   - A bare `"<pid>"` is the legacy form. It blocks while that PID exists and is reaped once the PID is gone.
   - Any other text is unproved and always blocks. No migration is needed: the existing `process_identity TEXT` column carries the format, and `db.ts` keeps migrations `[1..7]`.
   - Commander `worker_operations` keeps its existing `owner_pid`/`owner_started` (float `create_time()`) columns and `_operation_owner_status`.
8. **Grounded symbol decisions:**
   - **Gate split names:** the design's `maybe_start_handoff` / `handoff_suppresses` are implemented as the private daemon methods `_maybe_start_handoff` / `_handoff_suppresses`, alongside `_native_handoff_gate` in `main.py`, which they replace.
   - **Released disposition:** `worker_handoffs` has no disposition column. A released row gets `phase='released'`, `completed_at` set and `evidence_json["disposition"]="released"`. `effective_worker_tier` reads only `phase='completed'` rows.
   - **Bounded wait:** the design's "bounded-wait acquire" is `WorkerRegistry.acquire_worker_operation_waiting`, used through `worker_operation(..., wait_seconds=5.0)`.
   - **Unchanged-log short-circuit (H3):** it runs only when the caller passes `expected_file_identity`, which both production callers (daemon and controller) do. `test_same_inode_rewrite_of_cursor_boundary_is_rejected` passes no identity, so it keeps exercising the boundary-hash check unchanged. With an identity, a same-size, same-inode rewrite is not re-read, as H3 specifies.
   - **Reset-time validity (H9):** a reset time that is not later than its quota event's time counts as absent, so `worker_usage_quarantine_default_seconds` (default 18000) applies. Task 11 tests a realistic reset after the event and a realistic stale reset before it.
   - **Released after the transfer (H6a):** disposition `released_to_target`. The worker keeps the target runtime and the workspace row is finished.
   - **Process absence without a worktree (H7b):** `NativeProcessAdapter.proved_absent`.
   - **Dead-owner reaping (H10):** reaping keys only on owner death. No runtime-snapshot condition applies, per the design's "one dead-owner rule for every lock".
9. **Final review gate:** H16's "final Fable review passes" is an orchestrator gate after Task 17, before any commit. It is not a task step.

---

## Task 1: Native reader skips bad lines, waits on torn lines, short-circuits unchanged logs, counts both Claude windows, reports reset time (H2, H3, H4; H9 reader input)

**Depends on:** none

**Files:**
- `commander/src/ironclaude/worker_quota.py`
- `commander/tests/test_worker_quota.py`

### Step 1: RED — replace the old oversize test and add reader tests

In `commander/tests/test_worker_quota.py`, replace exactly:

```python
def test_truncated_and_oversized_record_hold_cursor(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    first = _read(path)
    with path.open("ab") as stream:
        stream.write(b'{"type":"assistant"')
    assert _resume(path, first)["state"] == "unknown"
    assert _resume(path, first)["cursor"] == first["cursor"]
    path.write_bytes(b"x" * (256 * 1024 + 1) + b"\n")
    assert _read(path)["state"] == "unknown"
```

with:

```python
def test_torn_final_line_stays_unknown_then_exhausts_once_complete(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude_success())
    first = _read(path)
    with path.open("ab") as stream:
        stream.write(json.dumps(_claude(timestamp=T2)).encode())
    held = _resume(path, first)
    assert held["state"] == "unknown" and held["reason"] == "incomplete_native_record"
    assert held["cursor"] == first["cursor"]
    with path.open("ab") as stream:
        stream.write(b"\n")
    assert _resume(path, first)["state"] == "exhausted"


def test_oversize_line_is_skipped_and_following_quota_exhausts(tmp_path):
    path = tmp_path / "claude.jsonl"
    oversize = b'{"type":"user","pad":"' + b"x" * (300 * 1024) + b'"}\n'
    path.write_bytes(oversize + json.dumps(_claude()).encode() + b"\n")
    result = _read(path)
    assert result["state"] == "exhausted"
    assert result["cursor"] == path.stat().st_size
    assert result["reader_state"]["skipped_records"] == 1


def test_oversize_skip_cursor_resumes_across_calls(tmp_path):
    path = tmp_path / "claude.jsonl"
    path.write_bytes(b"x" * (256 * 1024 + 1) + b"\n")
    first = _read(path)
    assert first["state"] == "ordinary" and first["reason"] == "native_log_eof"
    assert first["cursor"] == path.stat().st_size
    assert first["reader_state"]["skipped_records"] == 1
    assert first["reader_state"]["last_line_size"] == 256 * 1024
    _append(path, _claude())
    second = _resume(path, json.loads(json.dumps(first)))
    assert second["state"] == "exhausted"
    assert second["cursor"] == path.stat().st_size


def test_torn_oversize_line_waits_without_advancing(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude_success())
    first = _read(path)
    with path.open("ab") as stream:
        stream.write(b"x" * (256 * 1024 + 10))
    held = _resume(path, first)
    assert held["state"] == "unknown" and held["reason"] == "incomplete_native_record"
    assert held["cursor"] == first["cursor"]


@pytest.mark.parametrize("bad", [b"{bad}\n", b"[1, 2]\n", b"\x80\x81\n"])
def test_unparseable_line_is_skipped_and_counted(tmp_path, bad):
    path = tmp_path / "claude.jsonl"
    path.write_bytes(bad + json.dumps(_claude()).encode() + b"\n")
    result = _read(path)
    assert result["state"] == "exhausted"
    assert result["cursor"] == path.stat().st_size
    assert result["reader_state"]["skipped_records"] == 1


def test_unchanged_log_with_known_identity_is_not_reopened(tmp_path, monkeypatch):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    first = _read(path)
    opened = []
    real_open = open

    def counting_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(quota_module, "open", counting_open, raising=False)
    again = _read(path, cursor=first["cursor"], reader_state=first["reader_state"],
                  expected_file_identity=first["file_identity"])
    assert opened == []
    assert again["state"] == "exhausted" and again["cursor"] == first["cursor"]
    assert again["event_key"] == first["event_key"]
    _append(path, _claude_success(timestamp=T2))
    grown = _read(path, cursor=first["cursor"], reader_state=first["reader_state"],
                  expected_file_identity=first["file_identity"])
    assert opened == [path]
    assert grown["state"] == "available"


@pytest.mark.parametrize("window", ["five_hour", "seven_day"])
def test_both_claude_limit_windows_exhaust(tmp_path, window):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude(quotaLimits={**_claude()["quotaLimits"], "rateLimitType": window}))
    result = _read(path)
    assert result["state"] == "exhausted"
    assert result["limit_type"] == window


def test_claude_exhaustion_reports_native_reset_time(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    assert _read(path)["reset_at"] == 1791277200
    _append(path, _claude_success(timestamp=T2))
    assert _read(path)["reset_at"] is None


@pytest.mark.parametrize("primary,secondary,expected", [
    ({"used_percent": 100, "resets_at": 1791589397}, None, 1791589397),
    ({"resets_at": 1791500000}, {"resets_at": 1791589397}, 1791589397),
    ({"used_percent": 100}, None, None),
])
def test_codex_exhaustion_reports_latest_window_reset_when_present(tmp_path, primary, secondary, expected):
    path = tmp_path / "codex.jsonl"
    count = _codex_count("rate_limit_reached", 20)
    count["payload"]["rate_limits"].update({"primary": primary, "secondary": secondary})
    _write(path, _codex_meta(), _codex("task_started", turn_id="quota-turn"), count)
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["reset_at"] == expected
```

Test coverage notes:
- **H4 negative:** an unknown `rateLimitType` stays `unknown`. The existing `test_claude_ambiguous_quota_is_unknown[...future...]` covers it and stays unchanged.
- **H3 boundary-hash path:** the existing `test_same_inode_rewrite_of_cursor_boundary_is_rejected` passes no `expected_file_identity`, so it still exercises the hash check and stays unchanged.

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_quota.py`

Expected: FAIL: test_oversize_line_is_skipped_and_following_quota_exhausts, test_oversize_skip_cursor_resumes_across_calls, test_unparseable_line_is_skipped_and_counted[*] (unknown, no skip), test_unchanged_log_with_known_identity_is_not_reopened (file opened), test_both_claude_limit_windows_exhaust[five_hour] (unknown), test_claude_exhaustion_reports_native_reset_time and test_codex_exhaustion_reports_latest_window_reset_when_present[*] (KeyError reset_at); torn-line and seven_day cases may already pass as guards; record actual counts

### Step 3: GREEN — `worker_quota.py`

1. Replace exactly `CLAUDE_RATE_LIMIT_TYPES = frozenset({"seven_day"})` with:

```python
CLAUDE_RATE_LIMIT_TYPES = frozenset({"five_hour", "seven_day"})
```

2. Replace exactly:

```python
def _result(state, reason, reader):
    if reader["latest_state"] != "available":
        reader["observed_model"] = None
    return {
        "state": state, "status": "rejected" if state == "exhausted" else state,
        "exhausted": state == "exhausted", "client": reader["client"],
        "native_session_id": reader["native_session_id"],
        "generation": reader["generation"], "cursor": reader["cursor"],
        "file_identity": reader["file_identity"], "reason": reason,
        "event_key": reader["event_key"], "limit_type": reader["limit_type"],
        "event_timestamp": reader["event_timestamp"],
        "observed_model": reader["observed_model"] if state == "available" else None,
        "reader_state": reader.copy(),
    }
```

with:

```python
def _result(state, reason, reader):
    if reader["latest_state"] != "available":
        reader["observed_model"] = None
    return {
        "state": state, "status": "rejected" if state == "exhausted" else state,
        "exhausted": state == "exhausted", "client": reader["client"],
        "native_session_id": reader["native_session_id"],
        "generation": reader["generation"], "cursor": reader["cursor"],
        "file_identity": reader["file_identity"], "reason": reason,
        "event_key": reader["event_key"], "limit_type": reader["limit_type"],
        "event_timestamp": reader["event_timestamp"],
        "reset_at": reader["reset_at"] if state == "exhausted" else None,
        "observed_model": reader["observed_model"] if state == "available" else None,
        "reader_state": reader.copy(),
    }


def _skip_record(reader, stream, start, end):
    """Advance past one complete but unusable record (H2).

    The boundary proof keeps at most the trailing MAX_RECORD_BYTES of the record,
    so a resumed read can re-hash exactly those bytes before the cursor.
    """
    size = min(end - start, MAX_RECORD_BYTES)
    stream.seek(end - size)
    reader["last_line_hash"] = hashlib.sha256(stream.read(size)).hexdigest()
    reader["last_line_size"] = size
    reader["cursor"] = end
    reader["skipped_records"] += 1


def _codex_reset_at(limits):
    """Latest positive integer native reset time across Codex windows, if present (H9)."""
    resets = [window.get("resets_at") for window in (limits.get("primary"), limits.get("secondary"))
              if isinstance(window, dict)]
    valid = [value for value in resets if type(value) is int and value > 0]
    return max(valid) if valid else None
```

3. Replace exactly:

```python
            or (state.get("last_line_hash") is not None and not isinstance(state["last_line_hash"], str))):
```

with:

```python
            or (state.get("last_line_hash") is not None and not isinstance(state["last_line_hash"], str))
            or (state.get("skipped_records") is not None
                and (type(state["skipped_records"]) is not int or state["skipped_records"] < 0))
            or (state.get("reset_at") is not None
                and (type(state["reset_at"]) is not int or state["reset_at"] <= 0))):
```

4. Replace exactly:

```python
        "last_line_size": None, "last_line_hash": None,
    }
```

with:

```python
        "last_line_size": None, "last_line_hash": None,
        "skipped_records": 0, "reset_at": None,
    }
```

5. **H3 short-circuit.** Replace exactly:

```python
    try:
        with open(path, "rb") as stream:
```

with:

```python
    if (expected_file_identity is not None and isinstance(reader_state, dict) and cursor > 0
            and _valid_reader_state(reader_state)
            and all(reader_state.get(key) == value for key, value in (
                ("client", client), ("native_session_id", native_session_id),
                ("workspace_path", workspace_path), ("generation", generation),
                ("applicable_limit_id", applicable_limit_id),
                ("file_identity", expected_file_identity), ("cursor", cursor)))):
        try:
            current = os.stat(path)
        except OSError:
            current = None
        if (current is not None and f"{current.st_dev}:{current.st_ino}" == expected_file_identity
                and current.st_size == cursor):
            # H3: an unchanged log (same identity and size as the cursor) is not re-read.
            reader.update(reader_state)
            return _result(reader["latest_state"], "native_log_unchanged", reader)
    try:
        with open(path, "rb") as stream:
```

6. **H2 skip-and-advance.** Replace exactly:

```python
                if len(line) > MAX_RECORD_BYTES or not line.endswith(b"\n"):
                    return _result("unknown", "incomplete_native_record", reader)
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return _result("unknown", "malformed_native_record", reader)
                if not isinstance(record, dict):
                    return _result("unknown", "malformed_native_record", reader)
```

with:

```python
                if len(line) > MAX_RECORD_BYTES:
                    # Consume the rest of the oversize record in bounded chunks. A record
                    # without its newline yet is torn: stay unknown, do not advance.
                    while not line.endswith(b"\n"):
                        line = stream.readline(MAX_RECORD_BYTES + 1)
                        if not line:
                            return _result("unknown", "incomplete_native_record", reader)
                    _skip_record(reader, stream, start, stream.tell())
                    continue
                if not line.endswith(b"\n"):
                    return _result("unknown", "incomplete_native_record", reader)
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    _skip_record(reader, stream, start, end)
                    continue
                if not isinstance(record, dict):
                    _skip_record(reader, stream, start, end)
                    continue
```

7. Codex usage-limit exhaustion: `replace_all`, 2 occurrences. Confirm with `grep -c -F 'reader["limit_type"] = "usage_limit_exceeded"' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/worker_quota.py`, which must print `2`. Replace each occurrence of:

```python
                            reader["limit_type"] = "usage_limit_exceeded"
```

with:

```python
                            reader["limit_type"] = "usage_limit_exceeded"
                            reader["reset_at"] = None
```

8. Codex availability. Replace exactly:

```python
                                reader["limit_type"] = None
```

with:

```python
                                reader["limit_type"] = None
                                reader["reset_at"] = None
```

9. Codex token_count reached. Replace exactly:

```python
                            reader["limit_type"] = reached
```

with:

```python
                            reader["limit_type"] = reached
                            reader["reset_at"] = _codex_reset_at(limits)
```

10. Claude. Replace exactly:

```python
                            reader["limit_type"] = record["quotaLimits"]["rateLimitType"] if quota else None
```

with:

```python
                            reader["limit_type"] = record["quotaLimits"]["rateLimitType"] if quota else None
                            reader["reset_at"] = record["quotaLimits"]["resetsAt"] if quota else None
```

### Step 4: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_quota.py`

Expected: all tests in tests/test_worker_quota.py pass; record actual counts (test_worker_handoff_daemon.py is rewritten in Task 2; do not run it here)

`tests/test_worker_handoff_daemon.py::test_changed_native_log_warns_once_without_rewinding_trusted_cursor[malformed-...]` encodes the old malformed-line behaviour, which is why that file is not run in this task.

### Step 5: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_quota.py commander/tests/test_worker_quota.py`

Expected: exit 0

---

## Task 2: Detection only starts a handoff; only an unpaused handoff row suppresses lifecycle (H1)

**Depends on:** Task 1, Task 7. Task 7 is listed so that no Commander test run overlaps an in-progress edit to the TypeScript source, which the storage-backed tests execute through vite-node, or to the workspace modules those tests import.

**Files:**
- `commander/src/ironclaude/main.py`
- `commander/src/ironclaude/worker_registry.py`
- `commander/tests/test_worker_handoff_daemon.py`
- `commander/tests/test_worker_handoff.py`

Unknown evidence never notifies (H1), so the gate no longer records unknown-observation incidents. `WorkerRegistry.record_native_quota_unknown_observation` and its `unknown_observation` persistence have exactly one caller in `commander/` (the old gate in `main.py`), and only the daemon tests rewritten below read `unknown_observation`. This task therefore removes the method.

### Step 1: RED — rewrite `commander/tests/test_worker_handoff_daemon.py`

Overwrite the whole file with the content below. Tests whose behaviour does not change are carried verbatim. Tests that encoded "unknown evidence suppresses and notifies" are flipped (H1). New tests cover:
- path-less and remote exclusion before any read, with no notifications;
- suppression only by an unpaused row;
- a rejected exhaustion no longer suppressing the dead-session branch;
- removal of the unknown-observation recorder.

```python
"""Native quota detection only starts handoffs; only an unpaused handoff row suppresses lifecycle."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock
import uuid

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.worker_handoff import HandoffHold, WorkerHandoffController
from ironclaude.worker_quota import read_native_quota


def _daemon(tmp_path, client="claude"):
    registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    native_id = str(uuid.uuid4())
    registry.register_worker("w1", "claude-opus", "ic-w1", repo=str(tmp_path),
                             description="unfinished", client=client,
                             model="opus" if client == "claude" else "gpt-5.6-sol",
                             native_session_id=native_id)
    registry.set_worker_workspace("w1", {
        "workspace_guid": str(uuid.uuid4()), "repository_identity": "repository",
        "worktree_path": str(tmp_path), "branch": "work", "base_commit": "base",
        "integration_target": "main",
    })
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon.registry = registry
    daemon._native_log_root = tmp_path / "home"
    daemon.slack = MagicMock()
    daemon.brain = MagicMock()
    daemon.tmux = MagicMock()
    daemon.tmux.log_dir = str(tmp_path / "logs")
    daemon.config = {}
    daemon._get_orchestrator = MagicMock()
    daemon._prompt_store = MagicMock(return_value=None)
    daemon._worker_idle_since = {}
    daemon._resolve_worker_ssh = MagicMock(return_value=(None, None))
    return daemon, native_id


def _claude_quota(native_id, cwd, *, event_uuid="quota", timestamp="2026-10-05T12:00:00Z"):
    return {"session_id": native_id, "sessionId": native_id, "cwd": str(cwd),
            "timestamp": timestamp, "type": "assistant", "uuid": event_uuid,
            "error": "rate_limit", "isApiErrorMessage": True, "apiErrorStatus": 429,
            "quotaLimits": {"status": "rejected", "rateLimitType": "seven_day",
                            "resetsAt": 1, "unifiedRateLimitFallbackAvailable": False,
                            "overageStatus": "rejected", "isUsingOverage": False},
            "message": {"role": "assistant", "model": "<synthetic>",
                        "usage": {"output_tokens": 1}}}


def test_missing_native_log_does_not_suppress_dead_session_handling(tmp_path):
    daemon, _ = _daemon(tmp_path)
    _dead_worker_seams(daemon)
    daemon.check_workers()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry.get_native_quota_evidence("w1") is None
    assert not any("native quota" in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)


def test_path_less_worker_is_excluded_without_notification(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    daemon.registry._conn.execute("UPDATE workers SET workspace_path=NULL WHERE id='w1'")
    daemon.registry._conn.commit()
    _dead_worker_seams(daemon)
    daemon.check_workers()
    assert daemon.registry.get_native_quota_evidence("w1") is None
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    assert not any("native quota" in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)


def test_remote_worker_is_excluded_before_any_read_without_notification(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    daemon.registry._conn.execute("UPDATE workers SET machine='remote-host' WHERE id='w1'")
    daemon.registry._conn.commit()
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1") is None
    daemon._get_orchestrator.assert_not_called()
    daemon.slack.post_message.assert_not_called()
    daemon.brain.send_message.assert_not_called()
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False


def test_pending_handoff_reconciles_outside_running_enumeration_once(tmp_path):
    daemon, _ = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    daemon.registry.claim_worker_handoff(worker, generation=1)
    facade = daemon._get_orchestrator.return_value
    facade.reconcile_worker_handoff.return_value = {
        "status": "pending", "phase": "claimed", "reason": "awaiting_readback",
    }
    daemon.check_workers()
    daemon.check_workers()
    assert facade.reconcile_worker_handoff.call_count == 2
    assert daemon.slack.post_message.call_count == 1
    assert daemon.registry.get_worker("w1")["status"] == "handoff_pending"
    daemon.tmux.has_session.assert_not_called()


def test_busy_reconciler_does_not_consume_later_hold_notification(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    facade = daemon._get_orchestrator.return_value
    facade.reconcile_worker_handoff.side_effect = [
        {"status": "pending", "phase": "claimed", "reason": "handoff_reconcile_owned"},
        {"status": "held", "phase": "claimed", "reason": "opposite_capability_unavailable"},
    ]
    daemon.check_workers()
    daemon.slack.post_message.assert_not_called()
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    assert "opposite_capability_unavailable" in daemon.slack.post_message.call_args.args[0]


def test_held_worker_satisfies_confirmed_directive_without_replacement_prompt(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry._conn.execute(
        "INSERT INTO directives (source_ts, source_text, interpretation, status) "
        "VALUES ('1', 'work', 'work', 'confirmed')"
    )
    daemon.registry._conn.execute("UPDATE workers SET description='#1 unfinished' WHERE id='w1'")
    daemon.registry._conn.commit()
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    daemon._db = daemon.registry._conn
    daemon._directive_reminder_sent = {}
    daemon.check_confirmed_directives()
    daemon.brain.send_message.assert_not_called()


def test_stale_running_snapshot_never_reads_or_mutates_successor(tmp_path):
    daemon, _ = _daemon(tmp_path)
    old = daemon.registry.get_worker("w1")
    daemon.registry.get_running_workers = lambda: [old]
    daemon.registry.set_worker_provider("w1", "codex", "gpt-5.6-sol")
    daemon.check_workers()
    daemon.tmux.has_session.assert_not_called()
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry.get_worker("w1")["client"] == "codex"


def test_ordinary_unmanaged_worker_reaches_old_completion_branch(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.set_worker_provider("w1", "ollama", "local")
    daemon.tmux.has_session.return_value = False
    daemon._finalize_and_release_worker = MagicMock(return_value=None)
    daemon._drive_finalization_recovery = MagicMock(return_value="held")
    daemon._session_died_notified = set()
    daemon.registry.get_events_for_worker = MagicMock(return_value=[])
    daemon._finalize_marker_seen = {}
    daemon._finalize_drift_retry = {}
    daemon._finalize_failure_count = {}
    daemon._finalize_recovery_alerted = set()
    daemon._finalize_failure_alerted = set()
    daemon._commit_failure_alerted = set()
    daemon._resolve_worker_prompt = MagicMock()
    daemon.check_workers()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once()


def test_unknown_evidence_has_no_incident_recorder(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert not hasattr(WorkerRegistry, "record_native_quota_unknown_observation")
    assert "unknown_observation" not in daemon.registry.get_native_quota_evidence("w1")["verdict"]


def test_handoff_suppression_requires_unpaused_unfinished_row(tmp_path):
    daemon, _ = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    assert daemon._handoff_suppresses(worker) is False
    daemon.registry.claim_worker_handoff(worker, generation=1)
    assert daemon._handoff_suppresses(worker) is True
    daemon.registry.pause_worker_handoff("w1", 1, "opposite_capability_unavailable")
    assert daemon._handoff_suppresses(worker) is False


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("reason", ["operator_paused_pm", "workspace_handoff_protocol_unavailable"])
def test_confirmed_native_quota_starts_handoff_without_suppressing_lifecycle(tmp_path, client, reason):
    daemon, native_id = _daemon(tmp_path, client)
    root = daemon._native_log_root
    if client == "claude":
        path = root / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [_claude_quota(native_id, tmp_path, event_uuid="turn")]
    else:
        path = root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "task_started", "turn_id": "t"}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": "rate_limit_reached"},
                     "info": {"total_token_usage": {"output_tokens": 7}}}}]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if reason != "operator_paused_pm":
        packet = read_native_quota(
            path, client=client, native_session_id=native_id,
            workspace_path=str(tmp_path), generation="1",
        )
        daemon.registry.record_native_quota_verdict(
            "w1", 1, packet, snapshot=daemon.registry.get_worker("w1"),
        )
    daemon._get_orchestrator.return_value.handoff_worker.return_value = {
        "status": "rejected", "reason": reason,
    }
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    packet = daemon._get_orchestrator.return_value.handoff_worker.call_args.args[1]
    assert packet["state"] == "exhausted"
    assert packet["native_session_id"] == native_id
    assert daemon.registry.get_worker("w1")["status"] == "running"
    assert daemon.registry.get_worker_handoff("w1") is None
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False
    if reason == "operator_paused_pm":
        daemon.slack.post_message.assert_not_called()
    else:
        assert daemon.slack.post_message.call_count == 1
        assert "Workspace assignment hold is unproved" in daemon.slack.post_message.call_args.args[0]


def test_rejected_exhaustion_does_not_suppress_dead_session_handling(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    daemon._get_orchestrator.return_value.handoff_worker.return_value = {
        "status": "rejected", "reason": "operator_paused_pm",
    }
    _dead_worker_seams(daemon)
    daemon.check_workers()
    daemon._get_orchestrator.return_value.handoff_worker.assert_called_once()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )


def test_new_native_success_clears_only_matching_usage_incident(tmp_path):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    quota = _claude_quota(native_id, tmp_path)
    path.write_text(json.dumps(quota) + "\n")
    first = read_native_quota(path, client="claude", native_session_id=native_id,
                              workspace_path=str(tmp_path), generation="1")
    daemon.registry.record_native_quota_verdict("w1", 1, first,
                                                 snapshot=daemon.registry.get_worker("w1"))
    state = ProviderState(daemon.registry._conn)
    key = f"{native_id}:{first['file_identity']}:1"
    state.mark_unavailable("local", "claude", "worker", "opus", "usage", "native_usage_exhausted",
                           usage_generation=key, usage_cursor=first["cursor"],
                           usage_event_key=first["event_key"])
    state.mark_unavailable("local", "claude", "worker", "sonnet", "usage", "other_worker",
                           usage_generation="other", usage_cursor=1, usage_event_key="other")
    success = {"session_id": native_id, "sessionId": native_id, "cwd": str(tmp_path),
               "timestamp": "2026-10-05T12:00:01Z", "type": "assistant", "uuid": "success",
               "message": {"role": "assistant", "model": "claude-opus-4-5", "stop_reason": "end_turn",
                           "usage": {"output_tokens": 2}}}
    with path.open("a") as stream:
        stream.write(json.dumps(success) + "\n")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert state.unavailable_reason("local", "claude", "worker", "opus") is None
    assert state.unavailable_reason("local", "claude", "worker", "sonnet")["reason"] == "other_worker"
    daemon._get_orchestrator.assert_not_called()


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("fresh", [True, False])
def test_successful_other_native_session_recovers_only_newer_provider_quota(tmp_path, client, fresh):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path, client)
    state = ProviderState(daemon.registry._conn)
    predecessor = str(uuid.uuid4())
    key = ("local", client, "worker", state.worker_usage_scope_tier("worker", "opus"))
    # The exhausted predecessor has stopped. This is independent genuine native
    # success from another managed worker using the same scoped provider.
    state.mark_unavailable(*key, "usage", "stopped predecessor quota",
                           usage_generation=f"{predecessor}:old-file:1", usage_cursor=900,
                           usage_event_key="old-quota", usage_event_timestamp="2026-10-05T12:00:00Z")
    state.mark_unavailable("local", client, "worker", "sonnet", "usage", "other tier")
    state.set_current_client("worker", "codex")
    minute = "01" if fresh else "00"
    when = f"2026-10-05T12:{minute}:00Z"
    if client == "claude":
        path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [{"session_id": native_id, "sessionId": native_id, "cwd": str(tmp_path),
                 "timestamp": when, "type": "assistant", "uuid": "fresh-success",
                 "message": {"role": "assistant", "model": "claude-opus-4-5", "stop_reason": "end_turn",
                             "usage": {"output_tokens": 2}}}]
    else:
        path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
                {"type": "event_msg", "timestamp": when,
                 "payload": {"type": "task_started", "turn_id": "success-turn"}},
                {"type": "turn_context", "timestamp": when,
                 "payload": {"turn_id": "success-turn", "model": "gpt-5.6-sol"}},
                {"type": "response_item", "timestamp": when,
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "completed work"}]}},
                {"type": "event_msg", "timestamp": when,
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": None},
                     "info": {"total_token_usage": {"output_tokens": 2}}}},
                {"type": "event_msg", "timestamp": when,
                 "payload": {"type": "task_complete", "turn_id": "success-turn"}}]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert state.is_available(*key) is fresh
    assert not state.is_available("local", client, "worker", "sonnet")
    assert state.get_current_client("worker") == "codex"
    daemon._get_orchestrator.assert_not_called()
    daemon.slack.post_message.assert_not_called()


@pytest.mark.parametrize("client,native_model", [
    ("claude", "claude-sonnet-4-5"), ("claude", None),
    ("codex", "gpt-5.6-terra"), ("codex", None),
])
def test_native_success_cannot_clear_other_model_quarantine(tmp_path, client, native_model):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path, client)
    state = ProviderState(daemon.registry._conn)
    key = ("local", client, "worker", state.worker_usage_scope_tier("worker", "opus"))
    state.mark_unavailable(*key, "usage", "prior worker quota",
        usage_generation=f"{uuid.uuid4()}:old:1", usage_cursor=100,
        usage_event_key="old-quota", usage_event_timestamp="2026-10-05T12:00:00Z")
    when = "2026-10-05T12:01:00Z"
    if client == "claude":
        path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
        message = {"role": "assistant", "stop_reason": "end_turn", "usage": {"output_tokens": 2}}
        if native_model is not None:
            message["model"] = native_model
        rows = [{"session_id": native_id, "sessionId": native_id, "cwd": str(tmp_path),
                 "timestamp": when, "type": "assistant", "uuid": "success", "message": message}]
    else:
        path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        context = {"turn_id": "success"}
        if native_model is not None:
            context["model"] = native_model
        rows = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}}]
        if native_model is None:
            rows.extend([
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "task_started", "turn_id": "quota"}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": "rate_limit_reached"},
                     "info": {"total_token_usage": {"output_tokens": 7}}}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "task_complete", "turn_id": "quota"}},
            ])
        rows.extend([
            {"type": "event_msg", "timestamp": when,
             "payload": {"type": "task_started", "turn_id": "success"}},
            {"type": "turn_context", "timestamp": when, "payload": context},
            {"type": "response_item", "timestamp": when,
             "payload": {"type": "message", "role": "assistant", "content": [
                 {"type": "output_text", "text": "completed work"}]}},
            {"type": "event_msg", "timestamp": when,
             "payload": {"type": "token_count", "rate_limits": {
                 "limit_id": "codex", "rate_limit_reached_type": None},
                 "info": {"total_token_usage": {"output_tokens": 9}}}},
            {"type": "event_msg", "timestamp": when,
             "payload": {"type": "task_complete", "turn_id": "success"}},
        ])
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    parser = read_native_quota(path, client=client, native_session_id=native_id,
                               workspace_path=str(tmp_path), generation="1")
    assert parser["state"] == ("ordinary" if client == "claude" and native_model is None
                               else "available")
    assert parser["observed_model"] == native_model
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert not state.is_available(*key)
    daemon._get_orchestrator.assert_not_called()


def test_later_codex_success_reopens_capacity_after_other_session_quota(tmp_path):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path, "codex")
    path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": native_id, "cwd": str(tmp_path)}}) + "\n")

    def append_success(turn, timestamp):
        rows = [{"type": "event_msg", "timestamp": timestamp,
                 "payload": {"type": "task_started", "turn_id": turn}},
                {"type": "turn_context", "timestamp": timestamp,
                 "payload": {"turn_id": turn, "model": "gpt-5.6-sol"}},
                {"type": "response_item", "timestamp": timestamp,
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "native success"}]}},
                {"type": "event_msg", "timestamp": timestamp,
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": None}, "info": None}},
                {"type": "event_msg", "timestamp": timestamp,
                 "payload": {"type": "task_complete", "turn_id": turn}}]
        with path.open("a") as stream:
            stream.write("".join(json.dumps(row) + "\n" for row in rows))

    append_success("first", "2026-10-05T12:01:00Z")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    first = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    state = ProviderState(daemon.registry._conn)
    key = ("local", "codex", "worker", state.worker_usage_scope_tier("worker", "opus"))
    state.mark_unavailable(*key, "usage", "another worker exhausted at T2",
                           usage_generation=f"{uuid.uuid4()}:other-file:1", usage_cursor=50,
                           usage_event_key="other-quota", usage_event_timestamp="2026-10-05T12:02:00Z")
    # Re-observing the earlier successful EOF cannot erase the newer incident.
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert not state.is_available(*key)
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "event_msg", "timestamp": "2026-10-05T12:02:30Z",
            "payload": {"type": "error", "message": "native request failed"}}) + "\n")
    assert read_native_quota(path, client="codex", native_session_id=native_id,
        workspace_path=str(tmp_path), generation="1")["state"] == "unknown"
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1")["verdict"]["state"] == "unknown"
    assert not state.is_available(*key)
    daemon.slack.post_message.assert_not_called()
    append_success("later", "2026-10-05T12:03:00Z")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    last = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert last["event_timestamp"] > first["event_timestamp"]
    assert last["event_key"] != first["event_key"]
    assert state.is_available(*key)
    daemon._get_orchestrator.assert_not_called()


def _ordinary_native_log(daemon, native_id, client):
    if client == "claude":
        path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [{"type": "user", "session_id": native_id, "sessionId": native_id,
                 "cwd": daemon.registry.get_worker("w1")["workspace_path"],
                 "timestamp": "2026-10-05T12:00:00Z", "message": {"role": "user"}}]
    else:
        path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type": "session_meta", "payload": {
            "id": native_id, "cwd": daemon.registry.get_worker("w1")["workspace_path"]}},
            {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
             "payload": {"type": "task_started", "turn_id": "ordinary"}}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _dead_worker_seams(daemon):
    daemon.tmux.has_session.return_value = False
    daemon._finalize_and_release_worker = MagicMock(return_value=None)
    daemon._drive_finalization_recovery = MagicMock(return_value="held")
    daemon._session_died_notified = set()
    daemon.registry.get_events_for_worker = MagicMock(return_value=[])
    daemon._finalize_marker_seen = {}
    daemon._finalize_drift_retry = {}
    daemon._finalize_failure_count = {}
    daemon._finalize_recovery_alerted = set()
    daemon._finalize_failure_alerted = set()
    daemon._commit_failure_alerted = set()
    daemon._resolve_worker_prompt = MagicMock()


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_managed_ordinary_native_evidence_reaches_terminal_branch(tmp_path, client):
    daemon, native_id = _daemon(tmp_path, client)
    _ordinary_native_log(daemon, native_id, client)
    _dead_worker_seams(daemon)
    daemon.check_workers()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry.get_native_quota_evidence("w1")["verdict"]["state"] == "ordinary"


def test_native_reader_state_resumes_after_new_daemon_instance(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["cursor"] > 0 and saved["reader_state"]["cursor"] == saved["cursor"]
    with path.open("a") as stream:
        stream.write(json.dumps(_claude_quota(native_id, tmp_path,
                                              timestamp="2026-10-05T12:00:01Z")) + "\n")
    restarted = IroncladeDaemon.__new__(IroncladeDaemon)
    restarted.registry = daemon.registry
    restarted._native_log_root = daemon._native_log_root
    restarted.slack = MagicMock(); restarted.brain = MagicMock()
    restarted._get_orchestrator = MagicMock()
    restarted._get_orchestrator.return_value.handoff_worker.return_value = {
        "status": "rejected", "reason": "operator_paused_pm",
    }
    restarted._maybe_start_handoff(restarted.registry.get_worker("w1"))
    packet = restarted._get_orchestrator.return_value.handoff_worker.call_args.args[1]
    assert packet["state"] == "exhausted" and packet["cursor"] > saved["cursor"]
    assert packet["file_identity"] == saved["file_identity"]


@pytest.mark.parametrize("change", ["truncated", "replaced"])
def test_changed_native_log_never_rewinds_trusted_cursor_or_notifies(tmp_path, change):
    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    trusted = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert trusted["cursor"] > 0
    if change == "truncated":
        path.write_text("")
    else:
        path.unlink()
        path.write_text("{}\n")
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["cursor"] == trusted["cursor"]
    assert saved["reader_state"] == trusted["reader_state"]
    assert saved["state"] == "ordinary"
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False
    daemon.slack.post_message.assert_not_called()
    daemon.brain.send_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_malformed_native_line_is_skipped_and_cursor_advances(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    trusted = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    with path.open("a") as stream:
        stream.write("{bad}\n")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["state"] == "ordinary"
    assert saved["cursor"] == path.stat().st_size > trusted["cursor"]
    assert saved["reader_state"]["skipped_records"] == 1
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_bounded_scan_persists_forward_cursor_until_current_eof(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    import ironclaude.main as daemon_module

    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    row = {"type": "user", "session_id": native_id, "sessionId": native_id,
           "cwd": str(tmp_path), "timestamp": "2026-10-05T12:00:01Z",
           "message": {"role": "user"}}
    with path.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
        stream.write(json.dumps(row) + "\n")
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: next(clock))
    cursors = []
    for _ in range(3):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
        saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
        assert saved["state"] == "unknown"
        assert saved["reason"] == "native_quota_log_scan_budget_exceeded"
        cursors.append(saved["cursor"])
    assert cursors == sorted(set(cursors))
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    final = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert final["state"] == "ordinary" and final["cursor"] == cursors[-1]
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_blocked_finalization_cannot_claim_handoff_or_mutate_successor(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    _dead_worker_seams(daemon)
    source = daemon.registry.get_worker("w1")
    observed = []
    def blocked_grade(_worker_id, *, reason, terminal):
        with pytest.raises(RuntimeError, match="operation"):
            daemon.registry.claim_worker_handoff(source, generation=1)
        observed.append((reason, terminal))
        return None
    daemon._finalize_and_release_worker.side_effect = blocked_grade
    daemon.check_workers()
    assert observed == [("session ended", True)]
    assert daemon.registry.get_worker_handoff("w1") is None
    assert daemon.registry.get_worker("w1")["native_session_id"] == native_id


@pytest.mark.parametrize("appearance", ["ordinary", "quota"])
def test_missing_native_log_records_nothing_until_file_appears(tmp_path, appearance):
    daemon, native_id = _daemon(tmp_path)
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1") is None
    path = _ordinary_native_log(daemon, native_id, "claude")
    if appearance == "quota":
        with path.open("a") as stream:
            stream.write(json.dumps(_claude_quota(native_id, tmp_path,
                                                  timestamp="2026-10-05T12:00:01Z")) + "\n")
        daemon._get_orchestrator.return_value.handoff_worker.return_value = {
            "status": "rejected", "reason": "operator_paused_pm",
        }
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    daemon.slack.post_message.assert_not_called()
    if appearance == "ordinary":
        current = daemon.registry.get_native_quota_evidence("w1")["verdict"]
        assert current["state"] == "ordinary" and current["file_identity"]
        daemon._get_orchestrator.assert_not_called()
    else:
        packet = daemon._get_orchestrator.return_value.handoff_worker.call_args.args[1]
        assert packet["state"] == "exhausted" and packet["file_identity"]


def test_missing_log_then_large_first_file_resumes_across_restart(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    import ironclaude.main as daemon_module

    daemon, native_id = _daemon(tmp_path)
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1") is None
    path = _ordinary_native_log(daemon, native_id, "claude")
    row = {"type": "user", "session_id": native_id, "sessionId": native_id,
           "cwd": str(tmp_path), "timestamp": "2026-10-05T12:00:01Z",
           "message": {"role": "user"}}
    with path.open("a") as stream:
        stream.write("".join(json.dumps(row) + "\n" for _ in range(3)))
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: next(clock))
    cursors = []
    for attempt in range(4):
        if attempt == 1:
            restarted = IroncladeDaemon.__new__(IroncladeDaemon)
            restarted.registry = daemon.registry
            restarted._native_log_root = daemon._native_log_root
            restarted.slack = daemon.slack
            restarted.brain = daemon.brain
            restarted._get_orchestrator = daemon._get_orchestrator
            daemon = restarted
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
        saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
        assert saved["state"] == "unknown"
        assert saved["reason"] == "native_quota_log_scan_budget_exceeded"
        assert saved["file_identity"] and saved["reader_state"]["cursor"] == saved["cursor"]
        cursors.append(saved["cursor"])
    assert cursors == sorted(set(cursors))
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    final = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert final["state"] == "ordinary" and final["cursor"] == cursors[-1]
    path.unlink()
    path.write_text("{}\n")
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    replaced = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert replaced["cursor"] == final["cursor"]
    assert replaced["reader_state"] == final["reader_state"]
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_unproved_completed_successor_tier_records_unknown_without_suppressing(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    worker = daemon.registry.get_worker("w1")
    daemon.registry._conn.execute(
        "INSERT INTO worker_handoffs(worker_id,generation,phase,snapshot_json,evidence_json,completed_at) "
        "VALUES (? ,1,'completed',?,?,datetime('now'))",
        ("w1", json.dumps(daemon.registry._snapshot(worker)), json.dumps({
            "assignment_finished": True, "target_tier": "invalid",
            "target_runtime": {key: worker[key] for key in (
                "client", "model", "native_session_id", "tmux_session", "machine")},
        })),
    )
    daemon.registry._conn.commit()
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    verdict = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert (verdict["state"], verdict["status"], verdict["exhausted"]) == ("unknown", "unknown", False)
    assert verdict["reason"] == "worker_effective_tier_unproved"
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


@pytest.mark.parametrize("evidence_kind", ["error", "token_count"])
def test_stale_turn_native_evidence_cannot_claim_or_stop_current_codex_worker(tmp_path, evidence_kind):
    daemon, native_id = _daemon(tmp_path, "codex")
    path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
         "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:00:00.500Z",
         "payload": {"type": "error", "turn_id": "t1", "message": "usage exhausted",
                     "codex_error_info": "usage_limit_exceeded"}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:00:01Z",
         "payload": {"type": "task_complete", "turn_id": "t1"}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:01:00Z",
         "payload": {"type": "task_started", "turn_id": "t2"}},
    ]
    if evidence_kind == "error":
        rows.append({"type": "event_msg", "timestamp": "2026-10-05T12:02:00Z",
                     "payload": {"type": "error", "turn_id": "t1", "message": "usage exhausted",
                                 "codex_error_info": "usage_limit_exceeded"}})
    else:
        rows.append({"type": "event_msg", "timestamp": "2026-10-05T12:02:00Z",
                     "payload": {"type": "token_count", "turn_id": "t1",
                                 "rate_limits": {"limit_id": "codex",
                                                 "rate_limit_reached_type": "rate_limit_reached"},
                                 "info": {"total_token_usage": {"output_tokens": 7}}}})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    worker = daemon.registry.get_worker("w1")
    actual = read_native_quota(path, client="codex", native_session_id=native_id,
                               workspace_path=str(tmp_path), generation="1")
    assert actual["state"] == "unknown" and actual["event_key"]
    assert actual["file_identity"] and actual["cursor"] > 0
    daemon._maybe_start_handoff(worker)
    assert daemon._handoff_suppresses(worker) is False
    assert daemon.registry.get_native_quota_evidence("w1")["verdict"]["state"] == "unknown"
    daemon._get_orchestrator.assert_not_called()
    process, runtime = MagicMock(), MagicMock()
    tools = SimpleNamespace(registry=daemon.registry, tmux=daemon.tmux,
                            _workspace_client=MagicMock(), _ssh_manager=None)
    controller = WorkerHandoffController(tools, process=process, runtime=runtime,
                                         native_log_root=daemon._native_log_root)
    forged = {**actual, "state": "exhausted", "status": "rejected", "exhausted": True}
    with pytest.raises(HandoffHold, match="native_quota_evidence_changed"):
        controller._validate_quota(worker, 1, forged)
    assert daemon.registry.get_worker_handoff("w1") is None
    assert daemon.registry.get_worker("w1")["status"] == "running"
    process.stop_and_prove.assert_not_called()
    runtime.bootstrap.assert_not_called()


def test_dead_started_operation_warns_once_across_daemon_restart_without_effects(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    worker = daemon.registry.get_worker("w1")
    lease = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry.mark_worker_operation_effects_started(lease, worker)
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    daemon.check_workers()
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    warning = daemon.slack.post_message.call_args.args[0]
    assert lease["nonce"] in warning and "worker_mutation" in warning
    assert native_id in warning and "effects_readback_required" in warning
    assert "unfinished and preserved" in warning
    assert daemon.brain.send_message.call_count == 1
    assert daemon.registry.get_worker("w1")["status"] == "running"
    assert daemon.registry.get_worker_handoff("w1") is None
    daemon.tmux.has_session.assert_not_called()
    daemon.tmux.send_keys.assert_not_called()
    restarted = IroncladeDaemon.__new__(IroncladeDaemon)
    restarted.__dict__.update(daemon.__dict__)
    restarted.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    restarted.slack = MagicMock()
    restarted.brain = MagicMock()
    restarted.check_workers()
    restarted.slack.post_message.assert_not_called()
    restarted.brain.send_message.assert_not_called()
    assert restarted.registry.get_worker("w1")["status"] == "running"


def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    worker = daemon.registry.get_worker("w1")
    old = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    old_registry = daemon.registry
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))

    def handoff(worker_id, verdict):
        assert verdict["state"] == "exhausted"
        daemon.registry.claim_worker_handoff(daemon.registry.get_worker(worker_id), generation=1)
        return {"status": "pending", "phase": "claimed", "reason": "target_preflight_pending"}

    daemon._get_orchestrator.return_value.handoff_worker.side_effect = handoff
    daemon.check_workers()
    assert daemon.registry.get_worker("w1")["status"] == "handoff_pending"
    assert daemon.registry.get_worker_handoff("w1")["phase"] == "claimed"
    assert daemon.registry._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None
    with pytest.raises(RuntimeError, match="missing"):
        old_registry.release_worker_operation("w1", old["nonce"], worker)
    daemon.tmux.has_session.assert_not_called()


def test_dead_started_operation_warns_even_when_native_log_missing(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    lease = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry.mark_worker_operation_effects_started(lease, worker)
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    daemon.check_workers()
    daemon.check_workers()
    warnings = [call.args[0] for call in daemon.slack.post_message.call_args_list]
    assert sum(lease["nonce"] in warning for warning in warnings) == 1
    assert native_id in next(warning for warning in warnings if lease["nonce"] in warning)
    assert daemon.registry.get_worker("w1")["status"] == "running"
    daemon.tmux.has_session.assert_not_called()
    daemon.tmux.send_keys.assert_not_called()
    daemon._get_orchestrator.assert_not_called()
```

### Step 2: RED — update the gate call sites in `commander/tests/test_worker_handoff.py`

Edit 1. Replace exactly:

```python
        for _ in range(2):
            assert daemon._native_handoff_gate(registry.get_worker("w1")) is True
        assert result_box["result"] == {"status": "rejected", "worker_id": "w1",
```

with:

```python
        for _ in range(2):
            daemon._maybe_start_handoff(registry.get_worker("w1"))
        assert daemon._handoff_suppresses(registry.get_worker("w1")) is False
        assert result_box["result"] == {"status": "rejected", "worker_id": "w1",
```

Edit 2. Replace exactly:

```python
    assert daemon._native_handoff_gate(registry.get_worker("w1")) is True
    result = result_box["result"]
```

with:

```python
    daemon._maybe_start_handoff(registry.get_worker("w1"))
    result = result_box["result"]
```

Edit 3. Replace exactly:

```python
        assert daemon._native_handoff_gate(worker) is True
        assert result_box["result"]["status"] == "held", result_box["result"]
```

with:

```python
        daemon._maybe_start_handoff(worker)
        assert daemon._handoff_suppresses(registry.get_worker("w1")) is False
        assert result_box["result"]["status"] == "held", result_box["result"]
```

Edit 4. Replace exactly:

```python
        assert daemon._native_handoff_gate(worker) is True
        assert state.unavailable_reason("local", target_client, "worker", "opus") is not None
```

with:

```python
        daemon._maybe_start_handoff(worker)
        assert state.unavailable_reason("local", target_client, "worker", "opus") is not None
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_daemon.py tests/test_worker_handoff.py`

Expected: FAIL: daemon tests calling _maybe_start_handoff/_handoff_suppresses (AttributeError), including test_unknown_evidence_has_no_incident_recorder; test_missing_native_log_does_not_suppress_dead_session_handling, test_path_less_worker_is_excluded_without_notification, test_rejected_exhaustion_does_not_suppress_dead_session_handling (old gate suppresses); test_full_storage_backed_native_handoff_both_directions[*] (AttributeError); record actual counts

### Step 4: GREEN — `main.py`

1. **Replace the gate.** Replace the whole block that starts at the unique line `    def _native_handoff_gate(self, worker: dict) -> bool:` and runs up to, but not including, the unique line `    def _notify_worker_handoff(self, worker: dict, result: dict) -> None:`. This is currently lines 1689–1859, including the blank separator line. Read the block and copy it exactly as `old_string`. The replacement ends with one blank line, so the separation before `_notify_worker_handoff` is kept:

```python
    def _handoff_suppresses(self, worker: dict) -> bool:
        """Return true only while a durable, unpaused, unfinished handoff row exists (H1)."""
        if not isinstance(self.registry, WorkerRegistry):
            return False
        row = self.registry.get_worker_handoff(worker["id"])
        return bool(row and row["completed_at"] is None and not row["pause_reason"])

    def _maybe_start_handoff(self, worker: dict) -> None:
        """Start a provider handoff only on a positive native exhaustion verdict (H1).

        Never suppresses ordinary lifecycle handling and never notifies for
        unknown evidence. Remote and path-less workers return before any read.
        """
        from ironclaude.orchestrator_mcp import _WORKER_TYPE_TIER
        from ironclaude.worker_quota import read_native_quota

        if (worker.get("client") not in {"claude", "codex"}
                or worker.get("type") not in _WORKER_TYPE_TIER
                or not worker.get("model") or not worker.get("workspace_guid")
                or worker.get("machine") or not worker.get("workspace_path")):
            return
        try:
            uuid.UUID(worker["native_session_id"])
        except (TypeError, ValueError, AttributeError):
            return
        worker_id = worker["id"]
        latest = self.registry.get_worker_handoff(worker_id)
        generation = 1 if latest is None else latest["generation"] + 1
        prior = self.registry.get_native_quota_evidence(worker_id)
        prior_verdict = prior["verdict"] if prior and prior["generation"] == generation else None
        native_id = worker["native_session_id"]
        root = getattr(self, "_native_log_root", Path.home())
        if worker["client"] == "claude":
            paths = list((root / ".claude/projects").glob(f"*/{native_id}.jsonl"))
        else:
            paths = list((root / ".codex/sessions").glob(f"**/*{native_id}*.jsonl"))
        if len(paths) != 1 or not paths[0].is_file():
            return
        established = prior_verdict and prior_verdict.get("file_identity")
        prior_state = prior_verdict.get("reader_state") if established else None
        cursor = prior_verdict.get("cursor", 0) if established else 0
        identity = prior_verdict.get("file_identity") if established else None
        started = time.monotonic()
        while True:
            verdict = read_native_quota(
                paths[0], client=worker["client"], native_session_id=native_id,
                workspace_path=worker["workspace_path"], generation=str(generation),
                cursor=cursor, reader_state=prior_state,
                expected_file_identity=identity,
            )
            if verdict.get("reason") != "native_log_requires_cursor_continuation":
                break
            if time.monotonic() - started > 15:
                verdict["state"] = "unknown"
                verdict["reason"] = "native_quota_log_scan_budget_exceeded"
                break
            cursor = verdict["cursor"]
            prior_state = verdict["reader_state"]
            identity = verdict["file_identity"]
        effective_tier = self.registry.effective_worker_tier(worker)
        if effective_tier is None:
            verdict["state"] = "unknown"
            verdict["status"] = "unknown"
            verdict["exhausted"] = False
            verdict["reason"] = "worker_effective_tier_unproved"
        if verdict["state"] == "unknown":
            # Unknown evidence never suppresses and never notifies. Persist only
            # forward progress on the same file so a trusted cursor never rewinds.
            if (verdict.get("file_identity")
                    and isinstance(verdict.get("reader_state"), dict)
                    and verdict["reader_state"].get("cursor") == verdict.get("cursor")
                    and (prior_verdict is None or not prior_verdict.get("file_identity")
                         or (verdict["file_identity"] == prior_verdict.get("file_identity")
                             and verdict["cursor"] > prior_verdict.get("cursor", -1)))):
                try:
                    self.registry.record_native_quota_verdict(
                        worker_id, generation, verdict, snapshot=worker)
                except RuntimeError:
                    return
            return
        if verdict["state"] == "exhausted":
            try:
                self.registry.record_native_quota_verdict(
                    worker_id, generation, verdict, snapshot=worker,
                )
            except RuntimeError:
                return
            tools = self._get_orchestrator()
            if tools is None:
                if not prior_verdict or prior_verdict.get("event_key") != verdict.get("event_key"):
                    message = (f"Worker {worker_id} ({worker['client']} {worker['model']}, native {native_id}) "
                               "remains unfinished; confirmed native quota exhausted but "
                               "the handoff controller is unavailable.")
                    self.slack.post_message(message)
                    self.brain.send_message(message)
                return
            result = tools.handoff_worker(worker_id, verdict)
            if result.get("status") == "rejected" and result.get("reason") == "operator_paused_pm":
                return
            if (result.get("status") == "rejected"
                    and result.get("reason") not in {
                        "handoff_claim_failed", "native_quota_evidence_invalid",
                        "native_quota_evidence_changed",
                    }):
                try:
                    first = self.registry.record_native_handoff_rejection(
                        worker_id, generation, result.get("reason") or "source_preflight_failed",
                        snapshot=worker,
                    )
                except RuntimeError:
                    return
                if first:
                    message = (f"Worker {worker_id} ({worker['client']} {worker['model']}, native {native_id}) "
                               f"remains unfinished; handoff readiness unproved: "
                               f"{result.get('reason') or 'source_preflight_failed'}. "
                               "Workspace assignment hold is unproved.")
                    self.slack.post_message(message)
                    self.brain.send_message(message)
                return
            self._notify_worker_handoff(worker, result)
            return
        try:
            self.registry.record_native_quota_verdict(worker_id, generation, verdict, snapshot=worker)
        except RuntimeError:
            return
        if verdict["state"] == "available":
            if _native_availability_matches_worker_model(
                    worker, effective_tier, verdict.get("observed_model")):
                from ironclaude.provider_state import ProviderState
                provider_state = ProviderState(self.registry._conn)
                recovery_tiers = (effective_tier, provider_state.worker_usage_scope_tier(
                    "worker", effective_tier))
                for recovery_tier in dict.fromkeys(recovery_tiers):
                    provider_state.recover_usage_quarantine(
                        "local", worker["client"], "worker", recovery_tier,
                        native_generation=f"{native_id}:{verdict['file_identity']}:{generation}",
                        native_cursor=verdict["cursor"], native_event_key=verdict["event_key"],
                        native_state="available",
                        native_event_timestamp=verdict.get("event_timestamp"),
                    )
```

2. In `_confirm_and_kill_stuck_worker`, replace exactly:

```python
            self._warn_worker_operation_blocker(worker)
            if self._native_handoff_gate(worker):
                return
```

with:

```python
            self._warn_worker_operation_blocker(worker)
            self._maybe_start_handoff(worker)
            if self._handoff_suppresses(worker):
                return
```

3. In `check_workers`, replace exactly:

```python
                self._warn_worker_operation_blocker(current)
                if self._native_handoff_gate(current):
                    continue
```

with:

```python
                self._warn_worker_operation_blocker(current)
                self._maybe_start_handoff(current)
                if self._handoff_suppresses(current):
                    continue
```

4. In `worker_registry.py`, delete the whole method: the block that starts at the unique line `    def record_native_quota_unknown_observation(` and runs up to, but not including, the unique line `    def get_running_workers(self) -> list[dict]:`. This is currently lines 764–796. Before deleting, confirm with `grep -rn --include="*.py" "record_native_quota_unknown_observation" /Users/roberthyatt/Code/ironclaude/commander` that the only remaining occurrence is the definition itself. Keep the blank-line separation before `get_running_workers`.

### Step 5: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_daemon.py tests/test_worker_handoff.py tests/test_worker_quota.py`

Expected: all pass; record actual counts

Then confirm that no gate references remain.

Command: `grep -rn --include="*.py" -e "_native_handoff_gate" -e "record_native_quota_unknown_observation" /Users/roberthyatt/Code/ironclaude/commander`

Expected: no output

### Step 6: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/ironclaude/worker_registry.py commander/tests/test_worker_handoff_daemon.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 3: The two hook tests use `grep`, not `rg` (H15)

**Depends on:** none

**Files:**
- `worker/hooks/tests/test-gbtw-continuation-judge.sh`
- `worker/hooks/tests/test-last-assistant-text.sh`

### Step 1: RED — run both suites as they stand

Command: `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh`

Expected: with rg absent from PATH: 'rg: command not found' and failures; record actual (if rg is on PATH, record that it passes and continue)

Command: `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh`

Expected: with rg absent from PATH: case 5 fails; record actual

The edit removes the `rg` dependency either way.

### Step 2: GREEN — replace `rg` with `grep`

In `worker/hooks/tests/test-gbtw-continuation-judge.sh`, make the four `replace_all` edits below. Confirm each count first with `grep -c -F` on the pattern. Each pattern is copied without escaping; the fourth one begins with a literal pipe character followed by a space.

Edit A: replace every `"$(rg -q '` with `"$(grep -q '`. Expected count: 4 (lines 148, 149, 151, 154).

```text
"$(rg -q '
```

Edit B: replace every `"$(rg -q -F "` with `"$(grep -q -F -- "`. Expected count: 2 (lines 150, 152).

```text
"$(rg -q -F "
```

Edit C: replace every `"$(rg -qF '` with `"$(grep -qF '`. Expected count: 8 (lines 178, 191, 192, 195, 205, 206, 209, 210).

```text
"$(rg -qF '
```

Edit D: replace every occurrence of the pattern below with the same text where `rg` becomes `grep`. Expected count: 2 (lines 185, 186).

```text
| rg -qF '
```

Replacement for Edit D:

```text
| grep -qF '
```

`grep -q` with a basic regular expression keeps the semantics of the one regex pattern, `gate_deferral.*Judge whether independent authorized work remains`. `--` protects the fixed-string operands that come from variables.

In `worker/hooks/tests/test-last-assistant-text.sh`, replace exactly:

```bash
  && rg -qF 'evaluation unavailable' "$E2E_WARNING" 2>/dev/null; then
```

with:

```bash
  && grep -qF 'evaluation unavailable' "$E2E_WARNING" 2>/dev/null; then
```

This `2>/dev/null` is pre-existing test code, not an evidence command, and stays.

### Step 3: Verify that no `rg` remains, then run

Command: `grep -c -w rg /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh`

Expected: 0 for both files

Command: `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh`

Expected: exit 0 with 0 fail; record the actual results line

Command: `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh`

Expected: exit 0, Results: 7 pass, 0 fail; record the actual line

### Step 4: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/tests/test-gbtw-continuation-judge.sh worker/hooks/tests/test-last-assistant-text.sh`

Expected: exit 0

---

## Task 4: Workspace claims record PID plus start time; dead claims are reaped; `reap-stale-mutations` (H11 TypeScript, H12 command)

**Depends on:** none

**Files:**
- `worker/mcp-servers/workspace-manager/src/handoff.ts`
- `worker/mcp-servers/workspace-manager/src/cli.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/handoff.test.ts`

### Step 1: RED — tests

In `src/__tests__/handoff.test.ts`:

1. Replace exactly `import { beginHandoff, finishHandoff, transferHandoff, withAssignmentMutation } from '../handoff.js';` with:

```ts
import { beginHandoff, currentProcessIdentity, finishHandoff, processStartEpoch, reapStaleMutations, transferHandoff, withAssignmentMutation } from '../handoff.js';
import { createInternalCommandDependencies } from '../cli.js';
```

2. Replace exactly:

```ts
  it.each(['expired', 'invalid-expiry', 'rebase-apply', 'primary', 'integration', 'stale-mutation', 'live-head'] as const)('refuses %s authority/evidence', (kind) => {
```

with:

```ts
  it.each(['expired', 'invalid-expiry', 'rebase-apply', 'primary', 'integration', 'live-head'] as const)('refuses %s authority/evidence', (kind) => {
```

3. Delete exactly this line, including its newline. It is the H11 inversion of the old `handoff.test.ts:72` assertion; the new reaping tests below replace it.

```ts
    if (kind === 'stale-mutation') f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity,acquired_at) VALUES(?, 'rescue', 'stale', 'dead-process', '2000-01-01')").run(f.guid);
```

4. Insert helpers before the line `describe('assignment handoff', () => {`. Replace exactly `describe('assignment handoff', () => {` with:

```ts
type Fixture = ReturnType<typeof fixture>;
function act(f: Fixture, operation: 'begin' | 'finalize' | 'sync'): unknown {
  if (operation === 'begin') return beginHandoff(f.db, f.input);
  if (operation === 'finalize') {
    const index = git(f.assignment.worktree_path, 'write-tree'); const head = git(f.assignment.worktree_path, 'rev-parse', 'HEAD');
    return finalizeCommanderLocalCommit(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER, message: 'test', canonicalBranch: f.assignment.branch, localRef: `refs/heads/${f.assignment.branch}`, stagedTree: index, parentOid: head });
  }
  return syncWorktreeToTarget(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER });
}
function attempt(fn: () => unknown): string {
  try { fn(); return 'ok'; } catch (error) { return error instanceof Error ? error.message : String(error); }
}

describe('assignment handoff', () => {
```

5. Insert the new tests before the existing test `  it('rejects duplicate begin and transfer, unauthorized destination and finish replay', () => {`. Replace that exact line with:

```ts
  it('records process identity as pid plus integer UTC start epoch', () => {
    const now = Math.floor(Date.now() / 1000);
    const started = processStartEpoch(process.pid);
    expect(started).not.toBeNull();
    expect(started!).toBeLessThanOrEqual(now);
    expect(started!).toBeGreaterThanOrEqual(now - Math.ceil(process.uptime()) - 2);
    expect(currentProcessIdentity()).toBe(`${process.pid}:${started}`);
    expect(processStartEpoch(2147483647)).toBeNull();
  });

  it.each([
    ['dead pid with start time', '2147483647:1'],
    ['dead legacy pid', '2147483647'],
    ['same pid with another start time', `${process.pid}:1`],
  ])('reaps a %s claim so begin, finalize and sync proceed', (_label, identity) => {
    for (const operation of ['begin', 'finalize', 'sync'] as const) {
      const f = fixture(); if (operation === 'begin') f.grant();
      f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'stale', ?)").run(f.guid, identity);
      expect(attempt(() => act(f, operation))).not.toMatch(/already claimed|during assignment mutation/);
      expect(f.db.prepare("SELECT 1 FROM workspace_mutations WHERE nonce = 'stale'").get()).toBeUndefined();
      f.db.close();
    }
  });

  it.each(['live', 'legacy-live', 'unparseable'] as const)('keeps a %s claim blocking begin, finalize and sync', (kind) => {
    const identity = kind === 'live' ? currentProcessIdentity() : kind === 'legacy-live' ? `${process.pid}` : 'dead-process';
    for (const operation of ['begin', 'finalize', 'sync'] as const) {
      const f = fixture(); if (operation === 'begin') f.grant(); const before = f.snapshot();
      f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'held', ?)").run(f.guid, identity);
      expect(attempt(() => act(f, operation))).toMatch(operation === 'begin' ? /during assignment mutation/ : /already claimed/);
      expect(f.snapshot()).toEqual(before);
      expect(f.db.prepare("SELECT 1 FROM workspace_mutations WHERE nonce = 'held'").get()).toBeDefined();
      f.db.close();
    }
  });

  it('reap-stale-mutations removes only proved-dead claims', () => {
    const f = fixture(); const secondGuid = randomUUID();
    f.service.reserveWorkerWorktree({ repositoryPath: f.root, workspaceGuid: secondGuid, workerId: 'second-worker' });
    f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'dead', '2147483647:1')").run(f.guid);
    f.db.prepare("INSERT INTO workspace_mutations(workspace_guid,operation,nonce,process_identity) VALUES(?, 'rescue', 'live', ?)").run(secondGuid, currentProcessIdentity());
    expect(reapStaleMutations(f.db)).toEqual([f.guid]);
    expect(f.db.prepare('SELECT nonce FROM workspace_mutations').all()).toEqual([{ nonce: 'live' }]);
    expect(createInternalCommandDependencies(f.db)['reap-stale-mutations']!({})).toEqual({ protocol_version: 1, reaped: [] });
    f.db.close();
  });

  it('rejects duplicate begin and transfer, unauthorized destination and finish replay', () => {
```

### Step 2: Run RED

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager src/__tests__/handoff.test.ts`

Expected: FAIL: missing exports currentProcessIdentity/processStartEpoch/reapStaleMutations and undefined 'reap-stale-mutations'; reap cases fail with 'already claimed'/'during assignment mutation'; record actual

### Step 3: GREEN — `handoff.ts`

1. Replace exactly `import Database from 'better-sqlite3';` with:

```ts
import Database from 'better-sqlite3';
import { spawnSync } from 'node:child_process';
```

2. Replace exactly `function row(db: Database.Database, guid: string): HandoffRow | undefined {` with:

```ts
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
let cachedProcessIdentity: string | undefined;
function processExists(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ESRCH') return false;
    if (code === 'EPERM') return true;
    throw error;
  }
}
/**
 * Start time of `pid` as integer epoch seconds (floor), or null when no such process exists.
 * Commander compares the same representation: int(psutil.Process(pid).create_time()).
 * `ps` runs with TZ=UTC so the wall-clock text converts without local-time DST ambiguity.
 */
export function processStartEpoch(pid: number): number | null {
  if (!processExists(pid)) return null;
  const result = spawnSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8', env: { ...process.env, LC_ALL: 'C', TZ: 'UTC' } });
  if (result.error) throw result.error;
  const text = String(result.stdout ?? '').trim();
  if (text === '') { if (!processExists(pid)) return null; throw new Error(`Process ${pid} start time is unavailable`); }
  const match = /^[A-Z][a-z]{2}\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s+(\d{4})$/.exec(text);
  const month = match ? MONTHS.indexOf(match[1]) : -1;
  if (!match || month < 0) throw new Error(`Process ${pid} start time is unparseable: ${text}`);
  return Math.floor(Date.UTC(Number(match[6]), month, Number(match[2]), Number(match[3]), Number(match[4]), Number(match[5])) / 1000);
}
/** Durable claim identity "<pid>:<start epoch seconds>"; a bare "<pid>" is the legacy form. */
export function currentProcessIdentity(): string {
  if (cachedProcessIdentity === undefined) {
    let started: number | null = null;
    try { started = processStartEpoch(process.pid); } catch { started = null; }
    cachedProcessIdentity = started === null ? `${process.pid}` : `${process.pid}:${started}`;
  }
  return cachedProcessIdentity;
}
/** live: same PID incarnation; dead: PID gone or reused; unproved: anything else, which still blocks. */
export function mutationOwnerStatus(identity: string): 'live' | 'dead' | 'unproved' {
  const match = /^(\d+)(?::(\d+))?$/.exec(identity);
  if (!match) return 'unproved';
  const pid = Number(match[1]);
  if (!Number.isSafeInteger(pid) || pid <= 0) return 'unproved';
  let started: number | null;
  try { started = processStartEpoch(pid); } catch { return 'unproved'; }
  if (started === null) return 'dead';
  if (match[2] === undefined) return 'unproved';
  return started === Number(match[2]) ? 'live' : 'dead';
}
function row(db: Database.Database, guid: string): HandoffRow | undefined {
```

3. Replace exactly `export function beginHandoff(db: Database.Database, input: HandoffInput): void {` with:

```ts
function beginHandoffOnce(db: Database.Database, input: HandoffInput): void {
```

4. Replace exactly `export function transferHandoff(db: Database.Database, input: TransferHandoffInput): void {` with:

```ts
export function beginHandoff(db: Database.Database, input: HandoffInput): void {
  try { beginHandoffOnce(db, input); } catch (error) {
    if (!(error instanceof Error) || error.message !== 'Handoff cannot begin during assignment mutation' || !reapDeadAssignmentMutation(db, input.workspaceGuid)) throw error;
    beginHandoffOnce(db, input);
  }
}
export function transferHandoff(db: Database.Database, input: TransferHandoffInput): void {
```

5. Replace exactly `export function withAssignmentMutation<T>(db: Database.Database, workspaceGuid: string, operation: string, fn: () => T): T {` with:

```ts
function insertMutationClaim(db: Database.Database, workspaceGuid: string, operation: string, nonce: string): boolean {
  let inserted = false;
  db.transaction(() => { assertNoPendingHandoff(db, workspaceGuid);
    try { db.prepare('INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) VALUES (?, ?, ?, ?)').run(workspaceGuid, operation, nonce, currentProcessIdentity()); inserted = true; } catch { inserted = false; } }).immediate();
  return inserted;
}
/** Reaps the claim on one assignment only when its owner is proved dead (H11). */
export function reapDeadAssignmentMutation(db: Database.Database, workspaceGuid: string): boolean {
  const claim = db.prepare('SELECT nonce, process_identity FROM workspace_mutations WHERE workspace_guid = ?').get(workspaceGuid) as { nonce: string; process_identity: string } | undefined;
  if (!claim || mutationOwnerStatus(claim.process_identity) !== 'dead') return false;
  return db.prepare('DELETE FROM workspace_mutations WHERE workspace_guid = ? AND nonce = ? AND process_identity = ?').run(workspaceGuid, claim.nonce, claim.process_identity).changes === 1;
}
/** Startup sweep (H12): reaps every proved-dead claim; live and unproved claims stay. */
export function reapStaleMutations(db: Database.Database): string[] {
  const claims = db.prepare('SELECT workspace_guid FROM workspace_mutations ORDER BY workspace_guid').all() as { workspace_guid: string }[];
  return claims.filter((claim) => reapDeadAssignmentMutation(db, claim.workspace_guid)).map((claim) => claim.workspace_guid);
}
export function withAssignmentMutation<T>(db: Database.Database, workspaceGuid: string, operation: string, fn: () => T): T {
```

6. Replace exactly the following two lines (inside `withAssignmentMutation`):

```ts
  db.transaction(() => { assertNoPendingHandoff(db, workspaceGuid);
    try { db.prepare('INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) VALUES (?, ?, ?, ?)').run(workspaceGuid, operation, nonce, `${process.pid}`); } catch { throw new Error('Assignment mutation is already claimed'); } }).immediate();
```

with:

```ts
  if (!insertMutationClaim(db, workspaceGuid, operation, nonce)
    && !(reapDeadAssignmentMutation(db, workspaceGuid) && insertMutationClaim(db, workspaceGuid, operation, nonce))) {
    throw new Error('Assignment mutation is already claimed');
  }
```

### Step 4: GREEN — `cli.ts`

1. Replace exactly `import { beginHandoff, finishHandoff, transferHandoff, type HandoffInput, type TransferHandoffInput } from './handoff.js';` with:

```ts
import { beginHandoff, finishHandoff, reapStaleMutations, transferHandoff, type HandoffInput, type TransferHandoffInput } from './handoff.js';
```

2. Replace exactly:

```ts
  'handoff-status'?: InternalDependency;
}
```

with:

```ts
  'handoff-status'?: InternalDependency;
  'reap-stale-mutations'?: InternalDependency;
}
```

3. Replace exactly `export const INTERNAL_COMMAND_NAMES = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile', 'cleanup', 'sync', 'reap', 'configure-shared-resources', 'list-shared-resources', 'list-surfaced-orphans', 'reap-orphans', 'resolve-orphan', 'begin-handoff', 'transfer-handoff', 'finish-handoff', 'handoff-status'] as const;` with:

```ts
export const INTERNAL_COMMAND_NAMES = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile', 'cleanup', 'sync', 'reap', 'configure-shared-resources', 'list-shared-resources', 'list-surfaced-orphans', 'reap-orphans', 'resolve-orphan', 'begin-handoff', 'transfer-handoff', 'finish-handoff', 'handoff-status', 'reap-stale-mutations'] as const;
```

4. Replace exactly `    case 'handoff-status': return dependencies['handoff-status']?.(args) ?? (() => { throw new Error('handoff-status dependency is unavailable'); })();` with:

```ts
    case 'handoff-status': return dependencies['handoff-status']?.(args) ?? (() => { throw new Error('handoff-status dependency is unavailable'); })();
    case 'reap-stale-mutations': return dependencies['reap-stale-mutations']?.(args) ?? (() => { throw new Error('reap-stale-mutations dependency is unavailable'); })();
```

5. Replace exactly:

```ts
    'handoff-status': (args) => handoffStatus(db, requiredString(args, 'workspace_guid')),
  };
```

with:

```ts
    'handoff-status': (args) => handoffStatus(db, requiredString(args, 'workspace_guid')),
    'reap-stale-mutations': () => ({ protocol_version: 1, reaped: reapStaleMutations(db) }),
  };
```

### Step 5: Run GREEN

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager src/__tests__/handoff.test.ts src/__tests__/cli.test.ts`

Expected: all pass; record actual counts

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json`

Expected: exit 0, no output

### Step 6: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/handoff.ts worker/mcp-servers/workspace-manager/src/cli.ts worker/mcp-servers/workspace-manager/src/__tests__/handoff.test.ts`

Expected: exit 0

---

## Task 5: `release-handoff` finishes a source hold only while the source owns the assignment; `finish-handoff-after-target` finishes after the target commits (H5, H6a, H6b TypeScript)

**Depends on:** Task 4

The invariant is the assignment owner: release is allowed from `granted` or `begun` while the assignment is still owned by the source. A target owner recorded by authorization (before the transfer completes, H6a) does not block it, and the recorded target is left as is. After `transferHandoff` moves ownership, release is refused. H5's pre-stop rule "refused if a target was ever recorded" stays enforced by the Commander controller (`release_paused`) and the registry (`release_worker_handoff`), in Task 12.

H6b adds `finishHandoffAfterTarget` and the internal command `finish-handoff-after-target`. The minimal surface is one `allowDescendantHead` flag on the existing private `assertEvidence`:
- **What it accepts:** a live worktree HEAD equal to the HEAD recorded at grant, or one that descends from it. Descent is checked with the workspace manager's existing `isAncestor` helper in `git.ts`, which runs `git merge-base --is-ancestor`.
- **Owner check:** the variant only runs for phase `transferred`, where `assertEvidence` requires the assignment owner to be the recorded target. Any other owner is refused.
- **Non-descendant HEAD:** refused.
- **Normal path unchanged:** `finishHandoff` and `finish-handoff` keep the exact-HEAD check.
- **Callers:** only H6a's retry-forward and preserve-as-target paths (Task 15).

**Files:**
- `worker/mcp-servers/workspace-manager/src/handoff.ts`
- `worker/mcp-servers/workspace-manager/src/cli.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/handoff.test.ts`

### Step 1: RED — tests

1. In `src/__tests__/handoff.test.ts`, replace exactly `import { beginHandoff, currentProcessIdentity, finishHandoff, processStartEpoch, reapStaleMutations, transferHandoff, withAssignmentMutation } from '../handoff.js';` with:

```ts
import { beginHandoff, currentProcessIdentity, finishHandoff, finishHandoffAfterTarget, processStartEpoch, reapStaleMutations, releaseHandoff, transferHandoff, withAssignmentMutation } from '../handoff.js';
```

2. Replace exactly `  it('rejects duplicate begin and transfer, unauthorized destination and finish replay', () => {` with:

```ts
  it('finish-after-target accepts the granted HEAD and a fast-forward of it while the target owns the assignment', () => {
    for (const commitAfterTransfer of [false, true]) {
      const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
      transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
      if (commitAfterTransfer) {
        git(f.assignment.worktree_path, 'commit', '-m', 'target work after transfer');
        expect(() => finishHandoff(f.db, f.input)).toThrow(/live Git evidence/);
      }
      finishHandoffAfterTarget(f.db, f.input);
      expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('finished');
      f.db.close();
    }
  });

  it('finish-after-target refuses a non-descendant HEAD and any owner other than the target', () => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
    git(f.assignment.worktree_path, 'commit', '--amend', '-m', 'rewritten granted head');
    expect(() => finishHandoffAfterTarget(f.db, f.input)).toThrow(/live Git evidence/);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('transferred');
    f.db.close();
    const g = fixture(); g.grant(); beginHandoff(g.db, g.input);
    transferHandoff(g.db, { ...g.input, targetOwnerSessionId: TARGET });
    g.db.prepare('UPDATE assignments SET owner_session_id = ? WHERE workspace_guid = ?').run(OWNER, g.guid);
    expect(() => finishHandoffAfterTarget(g.db, g.input)).toThrow(/not active owner/);
    g.db.close();
    const h = fixture(); h.grant(); beginHandoff(h.db, h.input);
    expect(() => finishHandoffAfterTarget(h.db, h.input)).toThrow(/capability or generation/);
    expect((h.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(h.guid) as { phase: string }).phase).toBe('begun');
    h.db.close();
  });

  it('release-handoff finishes an unclaimed begun hold while the source still owns the assignment', () => {
    const f = fixture(); f.grant(); f.db.prepare('UPDATE workspace_handoffs SET target_owner_session_id = NULL WHERE workspace_guid = ?').run(f.guid);
    beginHandoff(f.db, f.input); const before = f.snapshot();
    const release = { workspaceGuid: f.guid, generation: 1, capabilityToken: f.input.capabilityToken, sourceOwnerSessionId: OWNER };
    expect(() => releaseHandoff(f.db, { ...release, capabilityToken: 'wrong' })).toThrow(/capability/);
    expect(() => releaseHandoff(f.db, { ...release, generation: 2 })).toThrow(/capability or generation/);
    releaseHandoff(f.db, release);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('finished');
    expect(() => releaseHandoff(f.db, release)).not.toThrow();
    expect(f.snapshot()).toEqual(before);
    expect(attempt(() => syncWorktreeToTarget(f.db, { repositoryPath: f.root, workspaceGuid: f.guid, providerRootSessionId: OWNER }))).not.toMatch(/handoff is pending/);
    f.db.close();
  });

  it('release-handoff finishes an authorized but untransferred hold and keeps the recorded target', () => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    const release = { workspaceGuid: f.guid, generation: 1, capabilityToken: f.input.capabilityToken, sourceOwnerSessionId: OWNER };
    releaseHandoff(f.db, release);
    expect(f.db.prepare('SELECT phase, target_owner_session_id FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid))
      .toEqual({ phase: 'finished', target_owner_session_id: TARGET });
    expect((f.db.prepare('SELECT owner_session_id FROM assignments WHERE workspace_guid = ?').get(f.guid) as { owner_session_id: string }).owner_session_id).toBe(OWNER);
    f.db.close();
  });

  it('release-handoff refuses once ownership moved to the target', () => {
    const f = fixture(); f.grant(); beginHandoff(f.db, f.input);
    transferHandoff(f.db, { ...f.input, targetOwnerSessionId: TARGET });
    const release = { workspaceGuid: f.guid, generation: 1, capabilityToken: f.input.capabilityToken, sourceOwnerSessionId: OWNER };
    expect(() => releaseHandoff(f.db, release)).toThrow(/no longer owns/);
    expect((f.db.prepare('SELECT phase FROM workspace_handoffs WHERE workspace_guid = ?').get(f.guid) as { phase: string }).phase).toBe('transferred');
    f.db.close();
  });

  it('release-handoff CLI validates input before touching the database', () => {
    const untouchable = new Proxy({}, { get: () => { throw new Error('database touched'); } });
    const release = createInternalCommandDependencies(untouchable as never)['release-handoff']!;
    expect(() => release({ workspace_guid: randomUUID(), capability_token: 't', source_owner_session_id: OWNER })).toThrow('generation must be a positive integer');
    expect(() => release({ workspace_guid: 'not-a-guid', generation: 1, capability_token: 't', source_owner_session_id: OWNER })).toThrow('Malformed handoff release input');
    expect(() => release({ workspace_guid: randomUUID(), generation: 1, capability_token: 't', source_owner_session_id: OWNER })).toThrow('database touched');
  });

  it('rejects duplicate begin and transfer, unauthorized destination and finish replay', () => {
```

### Step 2: Run RED

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager src/__tests__/handoff.test.ts`

Expected: FAIL: releaseHandoff and finishHandoffAfterTarget not exported and 'release-handoff' dependency undefined; record actual

### Step 3: GREEN — `handoff.ts`

1. Replace exactly `export interface TransferHandoffInput extends HandoffInput { targetOwnerSessionId: string; }` with:

```ts
export interface TransferHandoffInput extends HandoffInput { targetOwnerSessionId: string; }
export interface ReleaseHandoffInput { workspaceGuid: string; generation: number; capabilityToken: string; sourceOwnerSessionId: string; }
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
```

2. Replace exactly `export function assertNoPendingHandoff(db: Database.Database, workspaceGuid: string): void {` with:

```ts
/**
 * H5/H6a: finish a granted or begun source hold, only while the assignment owner is still the
 * source. A recorded (authorized) target owner does not block release before the transfer
 * lands; the owner-still-source check is the invariant. Replaying a finished release is a no-op.
 */
export function releaseHandoff(db: Database.Database, input: ReleaseHandoffInput): void {
  if (!UUID_PATTERN.test(input.workspaceGuid) || !UUID_PATTERN.test(input.sourceOwnerSessionId) || !Number.isInteger(input.generation) || input.generation <= 0 || input.capabilityToken.length === 0) throw new Error('Malformed handoff release input');
  db.transaction(() => {
    const handoff = row(db, input.workspaceGuid);
    if (!handoff || handoff.generation !== input.generation) {
      if (handoff && handoff.phase !== 'finished') throw new Error('Handoff capability or generation is invalid');
      return;
    }
    if (!equalHash(handoff.capability_token_hash, input.capabilityToken)) throw new Error('Handoff capability or generation is invalid');
    if (handoff.source_owner_session_id !== input.sourceOwnerSessionId) throw new Error('Handoff source owner mismatch');
    if (handoff.phase === 'finished') return;
    const assignment = db.prepare('SELECT owner_session_id FROM assignments WHERE workspace_guid = ?').get(input.workspaceGuid) as { owner_session_id: string | null } | undefined;
    if (!assignment || assignment.owner_session_id !== input.sourceOwnerSessionId) throw new Error('Handoff source no longer owns assignment; release refused');
    if (handoff.phase !== 'granted' && handoff.phase !== 'begun') throw new Error('Handoff phase cannot be released');
    if (db.prepare("UPDATE workspace_handoffs SET phase = 'finished', updated_at = datetime('now') WHERE workspace_guid = ? AND generation = ? AND phase IN ('granted', 'begun')").run(input.workspaceGuid, input.generation).changes !== 1) throw new Error('Handoff release changed concurrently');
  }).immediate();
}
export function assertNoPendingHandoff(db: Database.Database, workspaceGuid: string): void {
```

3. Replace exactly `import { discoverRepository, listWorktrees, runGit, worktreeHead } from './git.js';` with:

```ts
import { discoverRepository, isAncestor, listWorktrees, runGit, worktreeHead } from './git.js';
```

4. Replace exactly `function assertEvidence(db: Database.Database, input: HandoffInput, handoff: HandoffRow, phase: string): void {` with:

```ts
function assertEvidence(db: Database.Database, input: HandoffInput, handoff: HandoffRow, phase: string, allowDescendantHead = false): void {
```

5. Replace exactly `  for (const [key, expected] of fields) if (handoff[key] !== expected || assignment[key] !== expected) throw new Error('Handoff durable evidence mismatch');` with:

```ts
  for (const [key, expected] of fields) if (handoff[key] !== expected || (assignment[key] !== expected && !(allowDescendantHead && key === 'current_head'))) throw new Error('Handoff durable evidence mismatch');
```

6. Replace exactly ``  if (!observed || observed.branch !== `refs/heads/${input.expectedBranch}` || worktreeHead(input.expectedWorktreePath) !== input.expectedCurrentHead) throw new Error('Handoff live Git evidence mismatch');`` with:

```ts
  const liveHead = observed ? worktreeHead(input.expectedWorktreePath) : '';
  // H6b: only the after-target finish accepts a HEAD that fast-forwards from the granted HEAD.
  if (!observed || observed.branch !== `refs/heads/${input.expectedBranch}` || (liveHead !== input.expectedCurrentHead && !(allowDescendantHead && isAncestor(input.expectedWorktreePath, input.expectedCurrentHead, liveHead)))) throw new Error('Handoff live Git evidence mismatch');
```

7. Replace exactly `export function finishHandoff(db: Database.Database, input: HandoffInput): void {` with:

```ts
/**
 * H6b: finish after the target committed. Phase must be transferred, so assertEvidence requires
 * the assignment owner to be the recorded target; the live HEAD may equal or descend from the
 * granted HEAD. The normal finishHandoff keeps the exact-HEAD check.
 */
export function finishHandoffAfterTarget(db: Database.Database, input: HandoffInput): void {
  db.transaction(() => { const handoff = row(db, input.workspaceGuid); if (!handoff) throw new Error('Handoff not found'); assertEvidence(db, input, handoff, 'transferred', true);
    if (db.prepare("UPDATE workspace_handoffs SET phase = 'finished', updated_at = datetime('now') WHERE workspace_guid = ? AND phase = 'transferred'").run(input.workspaceGuid).changes !== 1) throw new Error('Handoff finish changed concurrently'); }).immediate();
}
export function finishHandoff(db: Database.Database, input: HandoffInput): void {
```

### Step 4: GREEN — `cli.ts`

1. Replace exactly `import { beginHandoff, finishHandoff, reapStaleMutations, transferHandoff, type HandoffInput, type TransferHandoffInput } from './handoff.js';` with:

```ts
import { beginHandoff, finishHandoff, finishHandoffAfterTarget, reapStaleMutations, releaseHandoff, transferHandoff, type HandoffInput, type ReleaseHandoffInput, type TransferHandoffInput } from './handoff.js';
```

2. Replace exactly:

```ts
  'reap-stale-mutations'?: InternalDependency;
}
```

with:

```ts
  'reap-stale-mutations'?: InternalDependency;
  'release-handoff'?: InternalDependency;
  'finish-handoff-after-target'?: InternalDependency;
}
```

3. In the `INTERNAL_COMMAND_NAMES` line, replace exactly `'handoff-status', 'reap-stale-mutations'] as const;` with `'handoff-status', 'reap-stale-mutations', 'release-handoff', 'finish-handoff-after-target'] as const;`.

4. Replace exactly `    case 'reap-stale-mutations': return dependencies['reap-stale-mutations']?.(args) ?? (() => { throw new Error('reap-stale-mutations dependency is unavailable'); })();` with:

```ts
    case 'reap-stale-mutations': return dependencies['reap-stale-mutations']?.(args) ?? (() => { throw new Error('reap-stale-mutations dependency is unavailable'); })();
    case 'release-handoff': return dependencies['release-handoff']?.(args) ?? (() => { throw new Error('release-handoff dependency is unavailable'); })();
    case 'finish-handoff-after-target': return dependencies['finish-handoff-after-target']?.(args) ?? (() => { throw new Error('finish-handoff-after-target dependency is unavailable'); })();
```

5. Replace exactly `    'reap-stale-mutations': () => ({ protocol_version: 1, reaped: reapStaleMutations(db) }),` with:

```ts
    'reap-stale-mutations': () => ({ protocol_version: 1, reaped: reapStaleMutations(db) }),
    'release-handoff': (args) => {
      const input: ReleaseHandoffInput = {
        workspaceGuid: requiredString(args, 'workspace_guid'), generation: requiredPositiveInteger(args, 'generation'),
        capabilityToken: requiredString(args, 'capability_token'), sourceOwnerSessionId: requiredString(args, 'source_owner_session_id'),
      };
      releaseHandoff(db, input); return handoffStatus(db, input.workspaceGuid);
    },
    'finish-handoff-after-target': (args) => {
      const input = handoffInput(args); finishHandoffAfterTarget(db, input); return safeHandoffResult(db, input);
    },
```

### Step 5: Run GREEN

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager src/__tests__/handoff.test.ts src/__tests__/cli.test.ts`

Expected: all pass; record actual counts

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json`

Expected: exit 0

### Step 6: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/handoff.ts worker/mcp-servers/workspace-manager/src/cli.ts worker/mcp-servers/workspace-manager/src/__tests__/handoff.test.ts`

Expected: exit 0

---

## Task 6: Stale workspace tests updated; private CLI input validated before the mutation claim (H14)

**Depends on:** Task 5

**Files:**
- `worker/mcp-servers/workspace-manager/src/integration.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`

### Step 1: RED — tests

1. In `db.test.ts`, make a `replace_all` edit. Confirm `grep -c -F 'toEqual([1, 2, 3, 4, 5, 6]);' <file>` prints `3`. Replace each `toEqual([1, 2, 3, 4, 5, 6]);` with `toEqual([1, 2, 3, 4, 5, 6, 7]);`.

2. In `tool-dispatch.test.ts`, replace exactly `const INTERNAL_COMMANDS = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile', 'cleanup', 'sync', 'reap', 'configure-shared-resources', 'list-shared-resources', 'list-surfaced-orphans', 'reap-orphans', 'resolve-orphan'] as const;` with:

```ts
const INTERNAL_COMMANDS = ['allocate', 'bind', 'finalize', 'abandon', 'reconcile', 'cleanup', 'sync', 'reap', 'configure-shared-resources', 'list-shared-resources', 'list-surfaced-orphans', 'reap-orphans', 'resolve-orphan', 'begin-handoff', 'transfer-handoff', 'finish-handoff', 'handoff-status', 'reap-stale-mutations', 'release-handoff', 'finish-handoff-after-target'] as const;
```

3. Replace exactly:

```ts
    'resolve-orphan': vi.fn().mockReturnValue({ results: [] }),
  };
}
```

with:

```ts
    'resolve-orphan': vi.fn().mockReturnValue({ results: [] }),
    'begin-handoff': vi.fn().mockReturnValue({ phase: 'begun' }),
    'transfer-handoff': vi.fn().mockReturnValue({ phase: 'transferred' }),
    'finish-handoff': vi.fn().mockReturnValue({ phase: 'finished' }),
    'handoff-status': vi.fn().mockReturnValue({ present: false }),
    'reap-stale-mutations': vi.fn().mockReturnValue({ reaped: [] }),
    'release-handoff': vi.fn().mockReturnValue({ phase: 'finished' }),
    'finish-handoff-after-target': vi.fn().mockReturnValue({ phase: 'finished' }),
  };
}
```

4. Replace exactly `  it('rejects direct-human authority consumption from a Codex subagent identity', () => {` with:

```ts
  it('validates private finalization input before claiming an assignment mutation', () => {
    const untouchable = new Proxy({}, { get: () => { throw new Error('database touched'); } });
    const dependencies = createInternalCommandDependencies(untouchable as never);
    expect(() => dependencies.finalize({ command: { repositoryPath: '/repo' } }))
      .toThrow('Commander finalization input is malformed');
    expect(() => dependencies.finalize({ command: {
      repositoryPath: '/repo', workspaceGuid: 'workspace', providerRootSessionId: 'root', message: 'reviewed',
      canonicalBranch: 'ironclaude/workspace', localRef: 'refs/heads/ironclaude/workspace', stagedTree: 'tree', parentOid: 'parent',
    } })).toThrow('database touched');
  });

  it('rejects direct-human authority consumption from a Codex subagent identity', () => {
```

The forbidden-name checks (`push`, `commit-and-push`, `integrate`, `issue-human-intent`) in "keeps hook issuance out of the general CLI and exposes no internal push command" stay unchanged.

### Step 2: Run RED

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager src/__tests__/db.test.ts src/__tests__/tool-dispatch.test.ts`

Expected: FAIL: 'rejects forged review authority fields at the private CLI boundary' (db.transaction is not a function) and 'validates private finalization input before claiming an assignment mutation' (database touched); db.test.ts and list tests pass; record actual counts

### Step 3: GREEN — `integration.ts`

Replace exactly `export function finalizeCommanderLocalCommit(db: Database.Database, input: CommanderLocalCommitInput, hooks?: FinalizationHooks): FinalizationResult { return withAssignmentMutation(db, input.workspaceGuid, 'finalize-commander-local-commit', () => finalizeCommanderLocalCommitCore(db, input, hooks)); }` with:

```ts
export function finalizeCommanderLocalCommit(db: Database.Database, input: CommanderLocalCommitInput, hooks?: FinalizationHooks): FinalizationResult { requireCommanderFinalizationInput(input); requireMessage(input.message); return withAssignmentMutation(db, input.workspaceGuid, 'finalize-commander-local-commit', () => finalizeCommanderLocalCommitCore(db, input, hooks)); }
```

### Step 4: Run GREEN

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager src/__tests__/db.test.ts src/__tests__/tool-dispatch.test.ts src/__tests__/handoff.test.ts src/__tests__/cli.test.ts`

Expected: all pass; record actual counts

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json`

Expected: exit 0

### Step 5: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`

Expected: exit 0

---

## Task 7: Commander grant reaps a dead claim; the client exposes `release-handoff`, `reap-stale-mutations` and `finish-handoff-after-target`; cross-language identity proof (H11 Python, H5/H6b/H12 transport)

**Depends on:** Task 6

**Files:**
- `commander/src/ironclaude/workspace_handoff_authority.py`
- `commander/src/ironclaude/workspace_client.py`
- `commander/tests/test_workspace_handoff_authority.py`
- `commander/tests/test_workspace_client.py`

### Step 1: RED — tests

1. In `test_workspace_handoff_authority.py`, replace exactly:

```python
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from ironclaude.workspace_handoff_authority import WorkspaceHandoffAuthority, WorkspaceHandoffAuthorityError
```

with:

```python
import sqlite3
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import psutil
import pytest

from ironclaude.workspace_handoff_authority import (
    WorkspaceHandoffAuthority, WorkspaceHandoffAuthorityError, mutation_owner_status,
)

_LIVE_START = int(psutil.Process(os.getpid()).create_time())
```

2. Replace exactly `def test_database_removed_after_presence_check_is_not_recreated(tmp_path, monkeypatch):` with:

```python
@pytest.mark.parametrize("identity,blocks", [
    (f"{os.getpid()}:{_LIVE_START}", True),
    (f"{os.getpid()}", True),
    ("p", True),
    ("2147483647:1", False),
    ("2147483647", False),
    (f"{os.getpid()}:{_LIVE_START - 1}", False),
])
def test_grant_reaps_only_a_proved_dead_mutation_claim(tmp_path, identity, blocks):
    path = _db(tmp_path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO workspace_mutations VALUES (?, 'x', 'n', ?, 'now')",
                (SCOPE["workspace_guid"], identity))
    con.commit(); con.close()
    authority = WorkspaceHandoffAuthority(path)
    if blocks:
        with pytest.raises(WorkspaceHandoffAuthorityError, match="mutation is active"):
            authority.grant(**SCOPE)
    else:
        assert authority.grant(**SCOPE).phase == "granted"
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM workspace_mutations").fetchone()[0] == (1 if blocks else 0)
    con.close()


def test_python_and_typescript_share_mutation_identity_representation(tmp_path):
    root = Path(__file__).parents[2] / "worker/mcp-servers/workspace-manager"
    vite = root / "node_modules/.bin/vite-node"
    primary = tmp_path / "primary"
    primary.mkdir()
    for argv in (["git", "init", "-b", "main", str(primary)],
                 ["git", "-C", str(primary), "config", "user.email", "test@example.com"],
                 ["git", "-C", str(primary), "config", "user.name", "Test"]):
        subprocess.run(argv, check=True, capture_output=True)
    (primary / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "base"], check=True, capture_output=True)
    workspace_db = tmp_path / "workspace.db"

    def cli(command, payload):
        result = subprocess.run(
            [str(vite), "--script", str(root / "src/cli.ts"), command, json.dumps(payload)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "WORKSPACE_MANAGER_DB_PATH": str(workspace_db)})
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    guids = [str(uuid.uuid4()) for _ in range(2)]
    for index, guid in enumerate(guids):
        cli("allocate", {"repository_path": str(primary), "workspace_guid": guid,
                         "worker_id": f"worker-{index}", "integration_target": "main"})
    live, dead = f"{os.getpid()}:{_LIVE_START}", f"{os.getpid()}:{_LIVE_START - 1}"
    assert (mutation_owner_status(live), mutation_owner_status(dead)) == ("live", "dead")
    con = sqlite3.connect(workspace_db)
    con.execute("INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) "
                "VALUES (?, 'live', 'live', ?)", (guids[0], live))
    con.execute("INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) "
                "VALUES (?, 'dead', 'dead', ?)", (guids[1], dead))
    con.commit(); con.close()
    assert cli("reap-stale-mutations", {}) == {"protocol_version": 1, "reaped": [guids[1]]}
    con = sqlite3.connect(workspace_db)
    assert con.execute("SELECT nonce FROM workspace_mutations").fetchall() == [("live",)]
    con.close()


def test_database_removed_after_presence_check_is_not_recreated(tmp_path, monkeypatch):
```

3. In `test_workspace_client.py`, replace exactly `def test_cleanup_forwards_payload_into_cli_argv(tmp_path: Path):` with:

```python
@pytest.mark.parametrize("method,command", [
    ("release_handoff", "release-handoff"),
    ("reap_stale_mutations", "reap-stale-mutations"),
    ("finish_handoff_after_target", "finish-handoff-after-target"),
])
def test_recovery_handoff_methods_use_exact_private_commands(tmp_path: Path, method, command):
    response = {"protocol_version": 1}
    runner = Mock(return_value=completed(json.dumps(response) + "\n"))
    client = WorkspaceClient(tmp_path, runner=runner)
    payload = {"workspace_guid": "w", "generation": 2, "capability_token": "secret",
               "source_owner_session_id": "source"}
    assert getattr(client, method)(payload) == response
    assert runner.call_args.args[0][2] == command
    assert json.loads(runner.call_args.args[0][3]) == payload
    assert runner.call_args.kwargs["shell"] is False


def test_cleanup_forwards_payload_into_cli_argv(tmp_path: Path):
```

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_workspace_handoff_authority.py tests/test_workspace_client.py`

Expected: FAIL: ImportError mutation_owner_status at collection of test_workspace_handoff_authority.py; test_recovery_handoff_methods_use_exact_private_commands[*] AttributeError; record actual

### Step 3: GREEN — `workspace_handoff_authority.py`

1. Replace exactly:

```python
import sqlite3
import uuid
```

with:

```python
import sqlite3
import uuid

import psutil
```

2. Replace exactly:

```python
class WorkspaceHandoffAuthorityError(RuntimeError):
    pass
```

with:

```python
class WorkspaceHandoffAuthorityError(RuntimeError):
    pass


def mutation_owner_status(identity: object) -> str:
    """Classify workspace_mutations.process_identity "<pid>:<start epoch seconds>" (H11).

    The start time is int(psutil create_time()), the same integer workspace-manager
    writes from `ps -o lstart=` in UTC. A bare "<pid>" is the legacy form: it blocks
    while that PID exists. Anything else is unproved and blocks.
    """
    if not isinstance(identity, str):
        return "unproved"
    pid_text, separator, start_text = identity.partition(":")
    if not pid_text.isdigit() or (separator and not start_text.isdigit()):
        return "unproved"
    pid = int(pid_text)
    if pid <= 0:
        return "unproved"
    try:
        started = int(psutil.Process(pid).create_time())
    except psutil.NoSuchProcess:
        return "dead"
    except (psutil.Error, OSError, OverflowError, ValueError):
        return "unproved"
    if not separator:
        return "unproved"
    return "live" if started == int(start_text) else "dead"
```

3. Replace exactly:

```python
            mutation = con.execute("SELECT 1 FROM workspace_mutations WHERE workspace_guid = ?", (workspace_guid,)).fetchone()
            if mutation:
                raise WorkspaceHandoffAuthorityError("assignment mutation is active")
```

with:

```python
            mutation = con.execute(
                "SELECT nonce, process_identity FROM workspace_mutations WHERE workspace_guid = ?",
                (workspace_guid,)).fetchone()
            if mutation and not (
                    mutation_owner_status(mutation["process_identity"]) == "dead"
                    and con.execute(
                        "DELETE FROM workspace_mutations WHERE workspace_guid=? AND nonce=? "
                        "AND process_identity=?",
                        (workspace_guid, mutation["nonce"], mutation["process_identity"]),
                    ).rowcount == 1):
                raise WorkspaceHandoffAuthorityError("assignment mutation is active")
```

### Step 4: GREEN — `workspace_client.py`

1. Replace exactly `    "begin-handoff", "transfer-handoff", "finish-handoff", "handoff-status",` with:

```python
    "begin-handoff", "transfer-handoff", "finish-handoff", "handoff-status",
    "release-handoff", "reap-stale-mutations", "finish-handoff-after-target",
```

2. Replace exactly:

```python
    def handoff_status(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        """Read held generation after uncertain transport; callers must not replay transfer."""
        return self._invoke("handoff-status", payload, **transport)
```

with:

```python
    def handoff_status(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        """Read held generation after uncertain transport; callers must not replay transfer."""
        return self._invoke("handoff-status", payload, **transport)

    def release_handoff(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        """Finish a source hold while the source still owns the assignment."""
        return self._invoke("release-handoff", payload, **transport)

    def reap_stale_mutations(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        """Startup sweep: reap workspace mutation claims whose owner process is proved dead."""
        return self._invoke("reap-stale-mutations", payload, **transport)

    def finish_handoff_after_target(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        """H6b: finish while the target owns the assignment; HEAD may fast-forward from the grant."""
        return self._invoke("finish-handoff-after-target", payload, **transport)
```

### Step 5: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_workspace_handoff_authority.py tests/test_workspace_client.py`

Expected: all pass including the vite-node cross-language test; record actual counts

### Step 6: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/workspace_handoff_authority.py commander/src/ironclaude/workspace_client.py commander/tests/test_workspace_handoff_authority.py commander/tests/test_workspace_client.py`

Expected: exit 0

---

## Task 8: Dead owners' worker-operation leases are reaped in both phases; startup-sweep registry function (H10, H12 registry)

**Depends on:** Task 2

**Files:**
- `commander/src/ironclaude/worker_registry.py`
- `commander/tests/test_worker_handoff_registry.py`
- `commander/tests/test_worker_handoff_daemon.py`

### Step 1: RED — registry tests

In `commander/tests/test_worker_handoff_registry.py`:

1. Replace exactly:

```python
@pytest.mark.parametrize("phase,owner,changed", [
    ("effects_started", "dead", False),
    ("claimed", "live", False),
    ("claimed", "unproved", False),
    ("claimed", "dead", True),
])
def test_handoff_claim_refuses_uncertain_or_foreign_operation(tmp_path, phase, owner, changed):
```

with:

```python
@pytest.mark.parametrize("phase,changed", [
    ("effects_started", False), ("claimed", True), ("effects_started", True),
])
def test_handoff_claim_reaps_dead_owner_operation_in_any_phase(tmp_path, phase, changed):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    if phase == "effects_started":
        first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    if changed:
        first._conn.execute("UPDATE workers SET model='changed' WHERE id='w1'")
    first._conn.commit()
    restarted = _registry(tmp_path)
    assert restarted.claim_worker_handoff(_snapshot(restarted), generation=1)["phase"] == "claimed"
    assert restarted._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None
    events = restarted._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped'").fetchall()
    assert [json.loads(row["details"])["phase"] for row in events] == [phase]
    assert json.loads(events[0]["details"])["nonce"] == lease["nonce"]


@pytest.mark.parametrize("phase,owner,changed", [
    ("claimed", "live", False),
    ("claimed", "unproved", False),
    ("effects_started", "live", False),
    ("effects_started", "unproved", False),
])
def test_handoff_claim_refuses_uncertain_or_foreign_operation(tmp_path, phase, owner, changed):
```

2. Replace exactly:

```python
def test_dead_owner_started_lease_keeps_effects_uncertain_and_warns_once(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    with pytest.raises(RuntimeError, match="unreconciled"):
        second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    blocker = second.claim_worker_operation_recovery_warning(_snapshot(second))
    assert blocker["nonce"] == lease["nonce"]
    assert blocker["phase"] == "effects_started"
    assert blocker["disposition"] == "effects_readback_required"
    third = _registry(tmp_path)
    assert third.claim_worker_operation_recovery_warning(_snapshot(third)) is None
    with pytest.raises(RuntimeError, match="operation"):
        third.claim_worker_handoff(_snapshot(third), generation=1)
```

with:

```python
def test_dead_owner_started_lease_is_reaped_with_event_and_no_warning(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    assert second.claim_worker_operation_recovery_warning(_snapshot(second)) is None
    replacement = second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    assert replacement["nonce"] != lease["nonce"]
    details = second._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped' AND worker_id='w1'"
    ).fetchone()["details"]
    assert json.loads(details) == {"nonce": lease["nonce"], "operation": "worker_mutation",
                                   "phase": "effects_started", "owner_pid": 2147483647,
                                   "owner_started": 0}
    with pytest.raises(RuntimeError, match="missing"):
        first.release_worker_operation("w1", lease["nonce"], source)


def test_same_pid_with_different_start_time_is_dead_and_reaped(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute("UPDATE worker_operations SET owner_started=1.0 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    replacement = second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    assert replacement["nonce"] != lease["nonce"]


def test_reap_dead_worker_operations_keeps_live_and_unproved_leases(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    for worker_id in ("w2", "w3"):
        registry.register_worker(worker_id, "claude-opus", f"ic-{worker_id}", client="claude",
                                 model="claude-opus", native_session_id=str(uuid.uuid4()))
    for worker_id in ("w1", "w2", "w3"):
        snapshot = registry.get_worker(worker_id)
        lease = registry.acquire_worker_operation(snapshot, operation="worker_mutation")
        registry.mark_worker_operation_effects_started(lease, snapshot)
    registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w3'")
    registry._conn.commit()
    assert _registry(tmp_path).reap_dead_worker_operations() == 1
    remaining = [row["worker_id"] for row in registry._conn.execute(
        "SELECT worker_id FROM worker_operations ORDER BY worker_id").fetchall()]
    assert remaining == ["w2", "w3"]
```

3. Replace exactly:

```python
def test_foreign_snapshot_cannot_claim_operation_recovery_warning(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
```

with:

```python
def test_foreign_snapshot_cannot_claim_operation_recovery_warning(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w1'")
```

4. Replace exactly:

```python
def test_changed_current_runtime_warns_old_lease_scope_but_cannot_recover(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.execute("UPDATE workers SET model='successor-model' WHERE id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    current = _snapshot(second)
    with pytest.raises(RuntimeError, match="unreconciled"):
        second.acquire_worker_operation(current, operation="worker_mutation")
    blocker = second.claim_worker_operation_recovery_warning(current)
    assert blocker["nonce"] == lease["nonce"]
    assert blocker["snapshot"]["model"] == source["model"]
    assert blocker["disposition"] == "runtime_scope_changed"
    assert second.claim_worker_operation_recovery_warning(current) is None
```

with:

```python
def test_changed_current_runtime_unproved_owner_warns_old_lease_scope_but_cannot_recover(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w1'")
    first._conn.execute("UPDATE workers SET model='successor-model' WHERE id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    current = _snapshot(second)
    with pytest.raises(RuntimeError, match="unreconciled"):
        second.acquire_worker_operation(current, operation="worker_mutation")
    blocker = second.claim_worker_operation_recovery_warning(current)
    assert blocker["nonce"] == lease["nonce"]
    assert blocker["snapshot"]["model"] == source["model"]
    assert blocker["disposition"] == "runtime_scope_changed"
    assert second.claim_worker_operation_recovery_warning(current) is None


def test_dead_owner_lease_with_changed_runtime_is_reaped(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.execute("UPDATE workers SET model='successor-model' WHERE id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    current = _snapshot(second)
    assert second.claim_worker_operation_recovery_warning(current) is None
    assert second.acquire_worker_operation(current, operation="worker_mutation")["worker_id"] == "w1"
```

### Step 2: RED — invert the two daemon lease tests

In `commander/tests/test_worker_handoff_daemon.py`, as Task 2 left it:

Edit 1. Replace the whole function `test_dead_started_operation_warns_once_across_daemon_restart_without_effects` (from its `def` line through the line `    assert restarted.registry.get_worker("w1")["status"] == "running"` that ends it, immediately before `def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):`) with:

```python
def test_dead_started_operation_is_reaped_and_reaches_ordinary_handling(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    worker = daemon.registry.get_worker("w1")
    lease = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry.mark_worker_operation_effects_started(lease, worker)
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    _dead_worker_seams(daemon)
    daemon.check_workers()
    reaped = daemon.registry._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped' AND worker_id='w1'"
    ).fetchall()
    assert len(reaped) == 1 and json.loads(reaped[0]["details"])["nonce"] == lease["nonce"]
    assert not any("effects_readback_required" in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    assert daemon.registry._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None
```

Edit 2. Replace the whole function `test_dead_started_operation_warns_even_when_native_log_missing` (from its `def` line to the end of the file) with:

```python
def test_dead_started_operation_is_reaped_even_when_native_log_missing(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    lease = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry.mark_worker_operation_effects_started(lease, worker)
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    _dead_worker_seams(daemon)
    daemon.check_workers()
    assert not any(lease["nonce"] in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry._conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='worker_operation_reaped'").fetchone()[0] == 1
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff_daemon.py`

Expected: FAIL: test_handoff_claim_reaps_dead_owner_operation_in_any_phase[*], test_dead_owner_started_lease_is_reaped_with_event_and_no_warning, test_same_pid_with_different_start_time_is_dead_and_reaped, test_reap_dead_worker_operations_keeps_live_and_unproved_leases (AttributeError), test_dead_owner_lease_with_changed_runtime_is_reaped, and both inverted daemon tests; record actual counts

### Step 4: GREEN — `worker_registry.py`

1. Replace the whole method from `    def _reconcile_dead_claimed_operation(self, worker_id: str, snapshot: dict) -> None:` through its final line `            raise RuntimeError("worker operation claim changed")`, which immediately precedes `    @staticmethod` / `    def _operation_owner_status(row) -> str:`. The replacement:

```python
    def _reap_dead_owner_operation(self, worker_id: str) -> bool:
        """Inside BEGIN IMMEDIATE, reap a lease whose owner PID incarnation is proved dead (H10).

        Phases claimed and effects_started reap alike; a live or unproved owner still
        blocks. Returns True when a row was reaped and False when no lease exists.
        """
        prior = self._conn.execute(
            "SELECT * FROM worker_operations WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if prior is None:
            return False
        if (prior["phase"] not in {"claimed", "effects_started"}
                or not self._operation_owner_proved_dead(prior)):
            raise RuntimeError("unreconciled worker operation blocks handoff")
        removed = self._conn.execute(
            "DELETE FROM worker_operations WHERE worker_id=? AND nonce=? AND phase=? "
            "AND owner_pid=? AND owner_started=?",
            (worker_id, prior["nonce"], prior["phase"], prior["owner_pid"], prior["owner_started"]),
        )
        if removed.rowcount != 1:
            raise RuntimeError("worker operation claim changed")
        # log_event() commits; a raw insert keeps the reap and its event in one transaction.
        self._conn.execute(
            "INSERT INTO events (event_type, worker_id, details) VALUES (?, ?, ?)",
            ("worker_operation_reaped", worker_id, json.dumps({
                "nonce": prior["nonce"], "operation": prior["operation"], "phase": prior["phase"],
                "owner_pid": prior["owner_pid"], "owner_started": prior["owner_started"],
            }, sort_keys=True)),
        )
        return True

    def reap_dead_worker_operations(self) -> int:
        """Startup sweep (H12): reap every dead-owner lease; live and unproved leases stay."""
        reaped = 0
        for row in self._conn.execute("SELECT worker_id FROM worker_operations").fetchall():
            try:
                with self._immediate():
                    if self._reap_dead_owner_operation(row["worker_id"]):
                        reaped += 1
            except RuntimeError:
                continue
        return reaped
```

2. Make a `replace_all` edit. Confirm `grep -c -F 'self._reconcile_dead_claimed_operation(worker["id"], snapshot)' <file>` prints `2`. Replace each `self._reconcile_dead_claimed_operation(worker["id"], snapshot)` with `self._reap_dead_owner_operation(worker["id"])`.

3. Replace exactly:

```python
            owner_status = self._operation_owner_status(row)
            if owner_status == "live" or (same_runtime and row["phase"] == "claimed"
                                          and owner_status == "dead"):
                return None
```

with:

```python
            owner_status = self._operation_owner_status(row)
            if owner_status in {"live", "dead"}:
                # A live owner is still working; a proved-dead owner is reaped at the
                # next acquire or by the startup sweep. Only an unproved owner warns.
                return None
```

### Step 5: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff_daemon.py tests/test_worker_handoff.py`

Expected: all pass; record actual counts

### Step 6: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_registry.py commander/tests/test_worker_handoff_registry.py commander/tests/test_worker_handoff_daemon.py`

Expected: exit 0

---

## Task 9: Brain-side operations wait briefly on a busy worker (H13)

**Depends on:** Task 8

**Files:**
- `commander/src/ironclaude/worker_registry.py`
- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/tests/test_worker_handoff_registry.py`

### Step 1: RED — tests

In `commander/tests/test_worker_handoff_registry.py`:

1. Replace exactly:

```python
import json
import sqlite3
import uuid
```

with:

```python
import json
import sqlite3
from types import SimpleNamespace
import uuid
```

2. Replace exactly `def test_legacy_operation_lease_migrates_as_effects_started(tmp_path):` with:

```python
def _brain_wait_clock(monkeypatch, on_sleep=None):
    import ironclaude.worker_registry as registry_module
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds
        if on_sleep is not None:
            on_sleep(len(sleeps))

    monkeypatch.setattr(registry_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(registry_module.time, "monotonic", lambda: clock["now"])
    return clock, sleeps


def test_brain_wrapper_waits_for_live_daemon_lease_then_proceeds(tmp_path, monkeypatch):
    from ironclaude.orchestrator_mcp import _hold_worker_operation
    daemon_registry = _registry(tmp_path)
    _worker(daemon_registry)
    lease = daemon_registry.acquire_worker_operation(_snapshot(daemon_registry), operation="worker_mutation")
    brain_registry = _registry(tmp_path)

    def daemon_finishes(count):
        if count == 3:
            daemon_registry.release_worker_operation("w1", lease["nonce"], _snapshot(daemon_registry))

    clock, sleeps = _brain_wait_clock(monkeypatch, daemon_finishes)
    calls = []
    guarded = _hold_worker_operation(lambda self, worker_id: calls.append(worker_id) or "done")
    assert guarded(SimpleNamespace(registry=brain_registry), "w1") == "done"
    assert calls == ["w1"]
    assert sleeps == [0.2, 0.2, 0.2]


def test_brain_wrapper_reports_busy_after_five_seconds(tmp_path, monkeypatch):
    from ironclaude.orchestrator_mcp import _hold_worker_operation
    daemon_registry = _registry(tmp_path)
    _worker(daemon_registry)
    daemon_registry.acquire_worker_operation(_snapshot(daemon_registry), operation="worker_mutation")
    brain_registry = _registry(tmp_path)
    clock, sleeps = _brain_wait_clock(monkeypatch)
    calls = []
    guarded = _hold_worker_operation(lambda self, worker_id: calls.append(worker_id))
    with pytest.raises(RuntimeError, match=r"worker w1 is busy \(daemon cycle in progress\); retry shortly"):
        guarded(SimpleNamespace(registry=brain_registry), "w1")
    assert calls == []
    assert set(sleeps) == {0.2}
    assert 5.0 - 1e-6 <= clock["now"] <= 5.2 + 1e-6


def test_brain_wrapper_does_not_wait_on_unproved_owner(tmp_path, monkeypatch):
    from ironclaude.orchestrator_mcp import _hold_worker_operation
    first = _registry(tmp_path)
    _worker(first)
    first.acquire_worker_operation(_snapshot(first), operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w1'")
    first._conn.commit()
    clock, sleeps = _brain_wait_clock(monkeypatch)
    guarded = _hold_worker_operation(lambda self, worker_id: "done")
    with pytest.raises(RuntimeError, match="unreconciled"):
        guarded(SimpleNamespace(registry=_registry(tmp_path)), "w1")
    assert sleeps == []


def test_legacy_operation_lease_migrates_as_effects_started(tmp_path):
```

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py -k brain_wrapper`

Expected: FAIL: test_brain_wrapper_waits_for_live_daemon_lease_then_proceeds (immediate unreconciled error) and test_brain_wrapper_reports_busy_after_five_seconds (wrong message); test_brain_wrapper_does_not_wait_on_unproved_owner passes as guard; record actual counts

### Step 3: GREEN — `worker_registry.py`

1. Replace exactly:

```python
import sqlite3
import uuid
from contextlib import contextmanager
```

with:

```python
import sqlite3
import time
import uuid
from contextlib import contextmanager
```

2. Replace exactly `    def mark_worker_operation_effects_started(self, lease: dict, snapshot: dict) -> None:` with:

```python
    def acquire_worker_operation_waiting(self, snapshot: dict, *, operation: str,
                                         wait_seconds: float, poll_seconds: float = 0.2) -> dict:
        """Wait briefly while another live process (the daemon cycle) holds this lease (H13)."""
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                return self.acquire_worker_operation(snapshot, operation=operation)
            except RuntimeError as exc:
                if (str(exc) != "unreconciled worker operation blocks handoff"
                        or not self._operation_held_by_live_peer(snapshot.get("id"))):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"worker {snapshot.get('id')} is busy (daemon cycle in progress); retry shortly"
                    ) from exc
                time.sleep(poll_seconds)

    def _operation_held_by_live_peer(self, worker_id) -> bool:
        row = self._conn.execute(
            "SELECT owner_token, owner_pid, owner_started FROM worker_operations WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        return (row is not None and row["owner_token"] != self._owner_token
                and self._operation_owner_status(row) == "live")

    def mark_worker_operation_effects_started(self, lease: dict, snapshot: dict) -> None:
```

3. Replace exactly:

```python
    def worker_operation(self, snapshot: dict, *, operation: str):
        lease = self.acquire_worker_operation(snapshot, operation=operation)
```

with:

```python
    def worker_operation(self, snapshot: dict, *, operation: str, wait_seconds: float = 0.0):
        lease = (self.acquire_worker_operation_waiting(snapshot, operation=operation,
                                                       wait_seconds=wait_seconds)
                 if wait_seconds > 0 else self.acquire_worker_operation(snapshot, operation=operation))
```

### Step 4: GREEN — `orchestrator_mcp.py`

Replace exactly `        with self.registry.worker_operation(snapshot, operation="worker_mutation"):` with:

```python
        with self.registry.worker_operation(snapshot, operation="worker_mutation", wait_seconds=5.0):
```

### Step 5: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff.py tests/test_orchestrator_mcp.py`

Expected: all pass; record actual counts

### Step 6: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_registry.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_handoff_registry.py`

Expected: exit 0

---

## Task 10: The Commander sweeps dead-owner rows at startup (H12)

**Depends on:** Task 9. Task 7 (the client method) is reached transitively.

**Files:**
- `commander/src/ironclaude/main.py`
- `commander/tests/test_worker_handoff_daemon.py`
- `commander/tests/test_daemon.py`

### Step 1: RED — tests

1. In `commander/tests/test_worker_handoff_daemon.py`, replace exactly `def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):` with:

```python
def test_startup_sweep_reaps_dead_owner_rows_and_keeps_live_ones(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.register_worker("w2", "claude-opus", "ic-w2", repo=str(tmp_path), client="claude",
                                    model="opus", native_session_id=str(uuid.uuid4()))
    dead_lease = daemon.registry.acquire_worker_operation(
        daemon.registry.get_worker("w1"), operation="worker_mutation")
    live_owner = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    live_owner.acquire_worker_operation(live_owner.get_worker("w2"), operation="worker_mutation")
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    workspace = daemon._get_orchestrator.return_value._workspace_client
    workspace.discover_installed_plugin_root.return_value = "/installed/plugin"
    workspace.reap_stale_mutations.return_value = {"protocol_version": 1, "reaped": []}
    daemon._sweep_dead_owner_leases()
    rows = [row["worker_id"] for row in daemon.registry._conn.execute(
        "SELECT worker_id FROM worker_operations ORDER BY worker_id").fetchall()]
    assert rows == ["w2"]
    details = daemon.registry._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped'").fetchone()["details"]
    assert json.loads(details)["nonce"] == dead_lease["nonce"]
    workspace.reap_stale_mutations.assert_called_once_with({}, plugin_root="/installed/plugin")


def test_startup_sweep_tolerates_installed_workspace_without_command(tmp_path):
    daemon, _ = _daemon(tmp_path)
    workspace = daemon._get_orchestrator.return_value._workspace_client
    workspace.discover_installed_plugin_root.return_value = "/installed/plugin"
    workspace.reap_stale_mutations.side_effect = RuntimeError(
        "Unknown internal workspace command: reap-stale-mutations")
    daemon._sweep_dead_owner_leases()
    assert workspace.reap_stale_mutations.call_count == 2


def test_run_sweeps_dead_owner_leases_before_first_cycle(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon._running = False
    daemon._sweep_dead_owner_leases = MagicMock()
    daemon.run()
    daemon._sweep_dead_owner_leases.assert_called_once_with()


def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):
```

2. In `commander/tests/test_daemon.py` (the `TestOperatorFastLane` monkeypatch list, so that `run()` never reaches the real workspace sweep), replace exactly `                     "_sweep_expired_push_requests"):` with:

```python
                     "_sweep_expired_push_requests", "_sweep_dead_owner_leases"):
```

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_daemon.py -k "startup_sweep or run_sweeps"`

Expected: FAIL: test_startup_sweep_reaps_dead_owner_rows_and_keeps_live_ones and test_startup_sweep_tolerates_installed_workspace_without_command (AttributeError _sweep_dead_owner_leases); test_run_sweeps_dead_owner_leases_before_first_cycle (mock never called); record actual counts

### Step 3: GREEN — `main.py`

1. Replace exactly:

```python
    def run(self):
        """Main daemon loop.
```

with:

```python
    def _sweep_dead_owner_leases(self) -> None:
        """Startup sweep (H12): reap dead-owner worker_operations and workspace_mutations rows.

        Best-effort: the installed workspace-manager dist may predate the
        reap-stale-mutations command, and the daemon must still start.
        """
        if isinstance(self.registry, WorkerRegistry):
            try:
                reaped = self.registry.reap_dead_worker_operations()
            except Exception as exc:  # noqa: BLE001 - startup sweep is best-effort
                logger.warning("Dead-owner worker operation sweep failed: %s", exc)
            else:
                if reaped:
                    logger.info("Reaped %d dead-owner worker operation lease(s) at startup", reaped)
        tools = self._get_orchestrator()
        workspace = getattr(tools, "_workspace_client", None) if tools is not None else None
        if workspace is None:
            return
        for client in ("claude", "codex"):
            try:
                root = workspace.discover_installed_plugin_root(client)
                result = workspace.reap_stale_mutations({}, plugin_root=root)
            except Exception as exc:  # noqa: BLE001 - startup sweep is best-effort
                logger.warning("Workspace mutation sweep via %s plugin failed: %s", client, exc)
                continue
            if result.get("reaped"):
                logger.info("Reaped dead workspace mutation claims at startup: %s", result["reaped"])
            return

    def run(self):
        """Main daemon loop.
```

2. Replace exactly:

```python
        fast_interval = self.config.get("fast_poll_interval_seconds", 3)
        last_slow = time.monotonic() - poll_interval
```

with:

```python
        fast_interval = self.config.get("fast_poll_interval_seconds", 3)
        self._sweep_dead_owner_leases()
        last_slow = time.monotonic() - poll_interval
```

### Step 4: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_daemon.py tests/test_daemon.py`

Expected: all pass; record actual counts

### Step 5: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_worker_handoff_daemon.py commander/tests/test_daemon.py`

Expected: exit 0

---

## Task 11: Usage quarantines expire at the native reset or the configured default (H9)

**Depends on:** Task 10. The Commander tasks form one chain, so no task's test run overlaps another task's edits.

**Files:**
- `commander/src/ironclaude/db.py`
- `commander/src/ironclaude/provider_state.py`
- `commander/src/ironclaude/worker_handoff.py`
- `commander/tests/test_provider_state.py`
- `commander/tests/test_worker_handoff.py`

H9 validity rule: a reset time that is not later than its quota event's time counts as absent, and the default applies. The controller test exercises a realistic reset after the event, a missing reset, and a realistic reset before the event. The storage-backed fixtures in `_write_quota` (`resetsAt: 1`) fall under the same rule, so they stay quarantined for the default.

### Step 1: RED — tests

1. In `commander/tests/test_provider_state.py`, replace exactly:

```python
from ironclaude.db import init_db
from ironclaude.provider_state import ProviderState
```

with:

```python
from ironclaude.db import init_db
from ironclaude.provider_state import ProviderState, WORKER_USAGE_ACCOUNT_TIER


def test_native_usage_quarantine_expires_at_reset_time_and_requarantines(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account = ("local", "claude", "worker", WORKER_USAGE_ACCOUNT_TIER)
    worker = ("local", "claude", "worker", "opus")
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:1",
                           usage_cursor=10, usage_event_key="e1",
                           usage_event_timestamp="2026-10-06T12:00:00Z", usage_expires_at=1_000.0)
    assert not state.is_available(*worker, now=999.0)
    assert state.is_available(*worker, now=1_000.0)
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:2",
                           usage_cursor=20, usage_event_key="e2",
                           usage_event_timestamp="2026-10-06T13:00:00Z", usage_expires_at=5_000.0)
    assert not state.is_available(*worker, now=1_001.0)
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:0",
                           usage_cursor=5, usage_event_key="e0",
                           usage_event_timestamp="2026-10-06T11:00:00Z", usage_expires_at=99_999.0)
    assert state.is_available(*worker, now=5_000.0)


def test_usage_quarantine_without_reset_uses_default(tmp_path, monkeypatch):
    import ironclaude.provider_state as provider_state_module
    monkeypatch.setattr(provider_state_module.time, "time", lambda: 10_000.0)
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account = ("local", "codex", "worker", WORKER_USAGE_ACCOUNT_TIER)
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:1",
                           usage_cursor=10, usage_event_key="e1",
                           usage_event_timestamp="2026-10-06T12:00:00Z")
    assert not state.is_available("local", "codex", "worker", "opus", now=10_000.0 + 17_999)
    assert state.is_available("local", "codex", "worker", "opus", now=10_000.0 + 18_000)


def test_legacy_usage_row_without_expiry_expires_after_default(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    state._conn.execute(
        "INSERT INTO provider_capability_state (host, client, role, tier, available, category, reason, updated_at) "
        "VALUES ('local', 'claude', 'worker', ?, 0, 'usage', 'legacy quota', '2000-01-01 00:00:00')",
        (WORKER_USAGE_ACCOUNT_TIER,))
    state._conn.commit()
    assert state.is_available("local", "claude", "worker", "opus")
    state._conn.execute("UPDATE provider_capability_state SET updated_at=datetime('now')")
    state._conn.commit()
    assert not state.is_available("local", "claude", "worker", "opus")


def test_expiry_never_reopens_non_usage_unavailability(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    state.mark_unavailable("local", "claude", "worker", "opus", "auth", "probe failed")
    assert not state.is_available("local", "claude", "worker", "opus", now=10**12)
```

2. In `commander/tests/test_worker_handoff.py`, replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
@pytest.mark.parametrize("reset_at,expected", [
    # Event 2026-10-05T12:00:00Z = 1791201600. A reset 21 h later is valid; a reset
    # three hours before the event is not later than it, so it counts as absent (H9 validity).
    (1791277200, 1791277200.0), (None, 1_000_060.0), (1791190800, 1_000_060.0),
])
def test_source_quarantine_expires_at_native_reset_or_configured_default(tmp_path, monkeypatch, reset_at, expected):
    import ironclaude.worker_handoff as handoff_module
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    state = ProviderState(registry._conn)
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(),
                            _ssh_manager=None, _provider_router=lambda: (None, None, state, None),
                            _config={"worker_usage_quarantine_default_seconds": 60})
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock())
    monkeypatch.setattr(handoff_module.time, "time", lambda: 1_000_000.0)
    quota = {"state": "exhausted", "event_key": "quota-event", "file_identity": "1:2", "cursor": 10,
             "event_timestamp": "2026-10-05T12:00:00+00:00", "reset_at": reset_at}
    controller._quarantine_validated_source(worker, 1, quota)
    row = registry._conn.execute(
        "SELECT usage_expires_at FROM provider_capability_state WHERE client='claude' AND tier=?",
        (state.worker_usage_scope_tier("worker", "opus"),)).fetchone()
    assert row[0] == expected


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_provider_state.py tests/test_worker_handoff.py -k "expire or expiry or quarantine"`

Expected: FAIL: new provider-state tests (unexpected keyword usage_expires_at/now; legacy row unavailable) and test_source_quarantine_expires_at_native_reset_or_configured_default[*] (no such column usage_expires_at); record actual counts

### Step 3: GREEN — `db.py`

1. Replace exactly:

```python
    usage_event_timestamp TEXT,
    observed_at TEXT,
```

with:

```python
    usage_event_timestamp TEXT,
    usage_expires_at REAL,
    observed_at TEXT,
```

2. Replace exactly:

```python
    ("usage_event_timestamp", "TEXT"),
    ("capability_fingerprint", "TEXT"),
```

with:

```python
    ("usage_event_timestamp", "TEXT"),
    ("usage_expires_at", "REAL"),
    ("capability_fingerprint", "TEXT"),
```

### Step 4: GREEN — `provider_state.py`

1. Replace exactly:

```python
import sqlite3
from datetime import datetime, timezone
```

with:

```python
import sqlite3
import time
from datetime import datetime, timezone
```

2. Replace exactly `WORKER_USAGE_ACCOUNT_TIER = "__worker_usage_account__"` with:

```python
WORKER_USAGE_ACCOUNT_TIER = "__worker_usage_account__"
# H9: a usage quarantine without a native reset time expires after this default.
DEFAULT_USAGE_QUARANTINE_SECONDS = 18000
```

3. Replace exactly:

```python
    def is_available(self, host: str, client: str, role: str, tier: str) -> bool:
        row = self._conn.execute(
            """
            SELECT available FROM provider_capability_state
            WHERE host=? AND client=? AND role=? AND tier=?
            """,
            (host, client, role, tier),
        ).fetchone()
        if row is not None and not bool(row[0]):
            return False
        if role != "worker" or tier == WORKER_USAGE_ACCOUNT_TIER:
            return True
        account_row = self._conn.execute(
            """SELECT available FROM provider_capability_state
               WHERE host=? AND client=? AND role=? AND tier=?
                 AND category IN ('usage', 'quota', 'rate_limit')""",
            (host, client, role, WORKER_USAGE_ACCOUNT_TIER),
        ).fetchone()
        return account_row is None or bool(account_row[0])
```

with:

```python
    def is_available(self, host: str, client: str, role: str, tier: str, *,
                     now: float | None = None) -> bool:
        current = time.time() if now is None else now
        row = self._conn.execute(
            """
            SELECT available, category, usage_expires_at, updated_at FROM provider_capability_state
            WHERE host=? AND client=? AND role=? AND tier=?
            """,
            (host, client, role, tier),
        ).fetchone()
        if row is not None and not bool(row[0]) and not self._usage_expired(row[1], row[2], row[3], current):
            return False
        if role != "worker" or tier == WORKER_USAGE_ACCOUNT_TIER:
            return True
        account_row = self._conn.execute(
            """SELECT available, category, usage_expires_at, updated_at FROM provider_capability_state
               WHERE host=? AND client=? AND role=? AND tier=?
                 AND category IN ('usage', 'quota', 'rate_limit')""",
            (host, client, role, WORKER_USAGE_ACCOUNT_TIER),
        ).fetchone()
        return (account_row is None or bool(account_row[0])
                or self._usage_expired(account_row[1], account_row[2], account_row[3], current))

    @staticmethod
    def _usage_expired(category, expires_at, updated_at, now: float) -> bool:
        """An expired usage quarantine only re-enables trying the client (H9)."""
        if category not in USAGE_QUARANTINE_CATEGORIES:
            return False
        if expires_at is not None:
            return float(expires_at) <= now
        try:
            updated = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return updated.timestamp() + DEFAULT_USAGE_QUARANTINE_SECONDS <= now
```

4. Replace exactly:

```python
        usage_event_timestamp: str | None = None,
    ) -> None:
        usage_event_timestamp = _normalized_timestamp(usage_event_timestamp)
```

with:

```python
        usage_event_timestamp: str | None = None,
        usage_expires_at: float | None = None,
    ) -> None:
        usage_event_timestamp = _normalized_timestamp(usage_event_timestamp)
        expires_at = None
        if category in USAGE_QUARANTINE_CATEGORIES:
            expires_at = (float(usage_expires_at) if usage_expires_at is not None
                          else time.time() + DEFAULT_USAGE_QUARANTINE_SECONDS)
```

5. Replace exactly:

```python
                    (host, client, role, tier, available, category, reason,
                     usage_generation, usage_cursor, usage_event_key, usage_event_timestamp)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
```

with:

```python
                    (host, client, role, tier, available, category, reason,
                     usage_generation, usage_cursor, usage_event_key, usage_event_timestamp,
                     usage_expires_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
```

6. Replace exactly:

```python
                    updated_at=datetime('now')
                """,
                (host, client, role, tier, category, reason, usage_generation, usage_cursor,
                 usage_event_key, usage_event_timestamp),
```

with:

```python
                    usage_expires_at=CASE WHEN excluded.category NOT IN ('usage', 'quota', 'rate_limit')
                                          THEN provider_capability_state.usage_expires_at
                                          WHEN excluded.usage_generation IS NOT NULL
                                               AND provider_capability_state.usage_event_timestamp IS NOT NULL
                                               AND (excluded.usage_event_timestamp IS NULL
                                                    OR excluded.usage_event_timestamp <= provider_capability_state.usage_event_timestamp)
                                          THEN provider_capability_state.usage_expires_at
                                          ELSE excluded.usage_expires_at END,
                    updated_at=datetime('now')
                """,
                (host, client, role, tier, category, reason, usage_generation, usage_cursor,
                 usage_event_key, usage_event_timestamp, expires_at),
```

7. Replace exactly `                       usage_generation=NULL, usage_cursor=NULL, usage_event_key=NULL,` with:

```python
                       usage_generation=NULL, usage_cursor=NULL, usage_event_key=NULL,
                       usage_expires_at=NULL,
```

### Step 5: GREEN — `worker_handoff.py`

1. Replace exactly `from ironclaude.workspace_handoff_authority import WorkspaceHandoffAuthority` with:

```python
from ironclaude.workspace_handoff_authority import WorkspaceHandoffAuthority
from ironclaude.provider_state import DEFAULT_USAGE_QUARANTINE_SECONDS
from datetime import datetime
```

2. Replace exactly:

```python
                               usage_cursor=quota["cursor"], usage_event_key=quota["event_key"],
                               usage_event_timestamp=quota.get("event_timestamp"))
```

with:

```python
                               usage_cursor=quota["cursor"], usage_event_key=quota["event_key"],
                               usage_event_timestamp=quota.get("event_timestamp"),
                               usage_expires_at=self._quarantine_expires_at(quota))

    def _quarantine_expires_at(self, quota: dict) -> float:
        """Native reset time when it follows the event, else the configured default (H9)."""
        config = getattr(self.tools, "_config", None)
        default = (config.get("worker_usage_quarantine_default_seconds", DEFAULT_USAGE_QUARANTINE_SECONDS)
                   if isinstance(config, dict) else DEFAULT_USAGE_QUARANTINE_SECONDS)
        reset_at = quota.get("reset_at")
        try:
            event = datetime.fromisoformat(
                str(quota.get("event_timestamp")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            event = None
        if type(reset_at) is int and reset_at > 0 and (event is None or reset_at > event):
            return float(reset_at)
        return time.time() + float(default)
```

### Step 6: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_provider_state.py tests/test_provider_router.py tests/test_worker_handoff.py tests/test_worker_handoff_daemon.py`

Expected: all pass (fixtures with resetsAt 1 fall back to the 18000 s default and stay unavailable); record actual counts

The fixtures in question are the storage-backed `fresh_quota_before_stop` and `fable_fallback` scenarios and `test_paused_preeffect_retry_requarantines_newer_exhaustion`.

### Step 7: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/db.py commander/src/ironclaude/provider_state.py commander/src/ironclaude/worker_handoff.py commander/tests/test_provider_state.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 12: A handoff paused before the source stop releases automatically (H5, H5a)

**Depends on:** Task 11. Tasks 5, 7 and 9 are reached transitively.

**Files:**
- `commander/src/ironclaude/worker_registry.py`
- `commander/src/ironclaude/worker_handoff.py`
- `commander/tests/test_worker_handoff_registry.py`
- `commander/tests/test_worker_handoff.py`

### Step 1: RED — registry tests

In `commander/tests/test_worker_handoff_registry.py`, replace exactly `def test_claim_rejects_unknown_concrete_client(tmp_path):` with:

```python
def test_release_returns_prestop_pause_to_running_with_released_disposition(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    with pytest.raises(RuntimeError, match="pause changed"):
        registry.release_worker_handoff("w1", 1, "other_reason")
    row = registry.release_worker_handoff("w1", 1, "native_quota_evidence_changed")
    assert row["phase"] == "released" and row["completed_at"]
    assert json.loads(row["evidence_json"])["disposition"] == "released"
    worker = registry.get_worker("w1")
    assert worker["status"] == "running"
    assert registry.effective_worker_tier(worker) == "opus"
    registry.update_worker_status("w1", "completed")
    assert registry.get_worker("w1")["status"] == "completed"


@pytest.mark.parametrize("recorded", ["target_id", "stop_intent", "source_stop_proof", "bootstrap_intent", "phase"])
def test_prestop_release_refused_once_target_or_stop_recorded(tmp_path, recorded):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    if recorded == "phase":
        registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    else:
        registry.record_worker_handoff_evidence("w1", 1, "claimed", {recorded: "recorded"})
    registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    with pytest.raises(RuntimeError, match="release refused"):
        registry.release_worker_handoff("w1", 1, "native_quota_evidence_changed")
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    assert registry.get_worker_handoff("w1")["completed_at"] is None


def test_post_stop_release_refuses_once_target_owns_runtime(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    registry.pause_worker_handoff("w1", 1, "target_resume_identity_unproved")
    with pytest.raises(RuntimeError, match="release refused"):
        registry.release_worker_handoff("w1", 1, "target_resume_identity_unproved", after_stop=True)
    assert registry.get_worker("w1")["status"] == "handoff_pending"


def test_claim_rejects_unknown_concrete_client(tmp_path):
```

### Step 2: RED — controller tests

In `commander/tests/test_worker_handoff.py`:

1. Replace exactly:

```python
def _pm_db(path, source):
```

with:

```python
def _scope(worker, head="head"):
    return {"workspace_guid": worker["workspace_guid"], "generation": 1,
            "repository_identity": worker["workspace_repository_identity"], "worker_id": worker["id"],
            "source_owner_session_id": worker["native_session_id"],
            "expected_worktree_path": worker["workspace_path"], "expected_branch": worker["workspace_branch"],
            "expected_base_commit": worker["workspace_base_commit"], "expected_current_head": head,
            "expected_integration_target": worker["workspace_integration_target"]}


def _released_workspace():
    workspace = MagicMock()
    workspace.release_handoff.return_value = {"protocol_version": 1, "present": True, "phase": "finished"}
    return workspace


def _pm_db(path, source):
```

2. Replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
def test_paused_retry_releases_when_source_quota_evidence_changes(tmp_path):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    verdict = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                workspace_path=worker["workspace_path"], generation="1")
    scope = _scope(worker)
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "quota_verdict": verdict, "scope": scope, "transport": {},
        "capability_token": "token", "grant_proved": True})
    registry.pause_worker_handoff("w1", 1, "opposite_capability_unavailable")
    workspace = _released_workspace()
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace,
                            _ssh_manager=None,
                            _provider_router=lambda: (None, None, ProviderState(registry._conn), None))
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock(), native_log_root=home,
                                         pm_db_path=tmp_path / "missing-pm.db")
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held"
    workspace.release_handoff.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    success = {"session_id": worker["native_session_id"], "sessionId": worker["native_session_id"],
               "cwd": worker["workspace_path"], "timestamp": "2026-10-05T12:00:01Z",
               "type": "assistant", "uuid": "recovered",
               "message": {"role": "assistant", "model": "claude-opus", "stop_reason": "end_turn",
                           "usage": {"output_tokens": 2}}}
    with path.open("a") as stream:
        stream.write(json.dumps(success) + "\n")
    released = controller.retry_paused_preflight("w1")
    assert released["status"] == "released" and released["phase"] == "released", released
    assert registry.get_worker("w1")["status"] == "running"
    workspace.release_handoff.assert_called_once_with({
        "workspace_guid": scope["workspace_guid"], "generation": 1, "capability_token": "token",
        "source_owner_session_id": worker["native_session_id"]})


@pytest.mark.parametrize("session_alive", [True, False])
def test_prestop_retry_releases_when_source_session_is_gone(tmp_path, session_alive):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    verdict = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                workspace_path=worker["workspace_path"], generation="1")
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "quota_verdict": verdict, "scope": _scope(worker), "transport": {},
        "capability_token": "token", "grant_proved": True})
    registry.pause_worker_handoff("w1", 1, "opposite_capability_unavailable")
    workspace = _released_workspace()
    tmux = MagicMock()
    tmux.has_session.return_value = session_alive
    tools = SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None,
                            _provider_router=lambda: (None, None, ProviderState(registry._conn), None))
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock(), native_log_root=home,
                                         pm_db_path=tmp_path / "missing-pm.db")
    result = controller.retry_paused_preflight("w1")
    tmux.has_session.assert_called_with("ic-w1")
    if session_alive:
        assert result["status"] == "held"
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        workspace.release_handoff.assert_not_called()
    else:
        assert result["status"] == "released"
        assert registry.get_worker("w1")["status"] == "running"
        workspace.release_handoff.assert_called_once()


@pytest.mark.parametrize("reason", ["native_quota_evidence_changed", "source_process_changed_before_stop"])
def test_prestop_pause_with_nonretryable_reason_releases_automatically(tmp_path, reason):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "scope": _scope(worker), "transport": {}, "capability_token": "token"})
    registry.pause_worker_handoff("w1", 1, reason)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    assert controller.retry_paused_preflight("w1")["status"] == "released"
    assert registry.get_worker("w1")["status"] == "running"
    assert json.loads(registry.get_worker_handoff("w1")["evidence_json"])["disposition"] == "released"
    workspace.release_handoff.assert_called_once()


def test_prestop_release_refused_once_target_recorded(tmp_path):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "scope": _scope(worker), "transport": {}, "capability_token": "token",
        "target_id": str(uuid.uuid4())})
    registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held"
    assert held["release_refused"] == "handoff_target_or_source_stop_recorded"
    workspace.release_handoff.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"


@pytest.mark.parametrize("target_owner,source_owner_matches,accepted", [
    (None, True, True), (str(uuid.uuid4()), True, True),
    (str(uuid.uuid4()), False, False), (None, False, False),
])
def test_released_generation_lets_next_handoff_preflight_proceed(tmp_path, target_owner, source_owner_matches, accepted):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    worktree = Path(worker["workspace_path"])
    worktree.mkdir(parents=True)
    for argv in (["git", "init", "-b", "main", str(worktree)],
                 ["git", "-C", str(worktree), "config", "user.email", "test@example.com"],
                 ["git", "-C", str(worktree), "config", "user.name", "Test"]):
        subprocess.run(argv, check=True, capture_output=True)
    (worktree / "AGENTS.md").write_text("rules\n"); (worktree / "CLAUDE.md").write_text("rules\n")
    subprocess.run(["git", "-C", str(worktree), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "base"], check=True, capture_output=True)
    workspace = MagicMock()
    workspace.discover_installed_plugin_root.return_value = "/plugin"
    workspace.handoff_status.return_value = {
        "protocol_version": 1, "present": True, "phase": "finished", "generation": 1,
        "target_owner_session_id": target_owner,
        "source_owner_session_id": worker["native_session_id"] if source_owner_matches else str(uuid.uuid4())}
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    if accepted:
        scope, _transport, _head = controller._source_preflight(worker, 2)
        assert scope["generation"] == 2
    else:
        with pytest.raises(HandoffHold, match="workspace_handoff_protocol_unavailable"):
            controller._source_preflight(worker, 2)


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

3. In the storage-backed scenario, replace exactly:

```python
        assert workspace.handoff_status({"workspace_guid":assignment["workspace_guid"]})["phase"] == "begun"
        return
    if not opposite_available:
```

with:

```python
        assert workspace.handoff_status({"workspace_guid":assignment["workspace_guid"]})["phase"] == "begun"
        released = tools.retry_worker_handoff_preflight("w1")
        assert released["status"] == "released" and released["phase"] == "released", released
        assert registry.get_worker("w1")["status"] == "running"
        assert tmux.panes["ic-w1"] is root and child.gone is False
        released_status = workspace.handoff_status({"workspace_guid": assignment["workspace_guid"]})
        assert released_status["phase"] == "finished" and released_status["target_owner_session_id"] is None
        assert registry.effective_worker_tier(registry.get_worker("w1")) == "opus"
        return
    if not opposite_available:
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff.py`

Expected: FAIL: registry release tests (AttributeError release_worker_handoff); test_paused_retry_releases_when_source_quota_evidence_changes, test_prestop_retry_releases_when_source_session_is_gone[False] and test_prestop_pause_with_nonretryable_reason_releases_automatically[*] (stay held); test_prestop_release_refused_once_target_recorded (KeyError release_refused); test_released_generation_lets_next_handoff_preflight_proceed accepted cases (workspace_handoff_protocol_unavailable); test_full_storage_backed_native_handoff_both_directions[claude-codex-False-True-late_recovery] (still held); record actual counts

### Step 4: GREEN — `worker_registry.py`

1. Replace exactly:

```python
            "SELECT phase,evidence_json FROM worker_handoffs WHERE worker_id=? "
            "AND completed_at IS NOT NULL ORDER BY generation DESC LIMIT 1",
```

with:

```python
            "SELECT phase,evidence_json FROM worker_handoffs WHERE worker_id=? "
            "AND completed_at IS NOT NULL AND phase='completed' ORDER BY generation DESC LIMIT 1",
```

2. Replace exactly `    def complete_worker_handoff(self, worker_id: str, generation: int, previous_phase: str) -> dict:` with:

```python
    def release_worker_handoff(self, worker_id: str, generation: int, expected_pause_reason: str,
                               *, after_stop: bool = False) -> dict:
        """CAS a paused handoff back to running; the row completes with disposition released.

        Before the source stop (H5) release is refused once a target, a stop intent, or
        a stop proof was recorded. After the stop (H6/H7) it is refused once the target
        owns the runtime.
        """
        with self._immediate():
            handoff = self.get_worker_handoff(worker_id)
            if (not handoff or handoff["generation"] != generation or handoff["completed_at"]
                    or not expected_pause_reason or handoff["pause_reason"] != expected_pause_reason):
                raise RuntimeError("handoff generation, phase, or pause changed")
            evidence = json.loads(handoff["evidence_json"])
            stopped = (handoff["phase"] != "claimed" or bool(evidence.get("stop_intent"))
                       or bool(evidence.get("source_stop_proof")))
            if not after_stop and (stopped or evidence.get("target_id") or evidence.get("bootstrap_intent")):
                raise RuntimeError("handoff target or source stop recorded; release refused")
            if (handoff["phase"] in {"owner_transferred", "runtime_registered", "continuation_dispatched"}
                    or evidence.get("target_runtime")):
                raise RuntimeError("handoff target owns runtime; release refused")
            source = json.loads(handoff["snapshot_json"])
            current = self.get_worker(worker_id)
            if current is None or self._snapshot(current) != {**source, "status": "handoff_pending"}:
                raise RuntimeError("handoff source runtime changed")
            evidence.update({"disposition": "released", "released_from_phase": handoff["phase"],
                             "released_reason": expected_pause_reason})
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET phase='released', evidence_json=?, completed_at=datetime('now'), "
                "updated_at=datetime('now') WHERE worker_id=? AND generation=? AND completed_at IS NULL "
                "AND pause_reason=?",
                (json.dumps(evidence, sort_keys=True), worker_id, generation, expected_pause_reason),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff generation or phase changed")
            worker = self._conn.execute(
                "UPDATE workers SET status='running' WHERE id=? AND status='handoff_pending'", (worker_id,))
            if worker.rowcount != 1:
                raise RuntimeError("handoff worker runtime changed")
        return self.get_worker_handoff(worker_id)

    def complete_worker_handoff(self, worker_id: str, generation: int, previous_phase: str) -> dict:
```

### Step 5: GREEN — `worker_handoff.py`

1. Replace exactly:

```python
                          and status.get("target_owner_session_id") == worker["native_session_id"])
```

with:

```python
                          and (status.get("target_owner_session_id") == worker["native_session_id"]
                               # Released by this runtime (H5/H6a): the source still owned the
                               # assignment, whether or not a target had been authorized.
                               or status.get("source_owner_session_id") == worker["native_session_id"]))
```

2. Replace exactly:

```python
        status = {"status": "completed" if row["completed_at"] else "held" if row["pause_reason"] else "pending",
```

with:

```python
        if row["completed_at"]:
            state = "released" if row["phase"] == "released" else "completed"
        else:
            state = "held" if row["pause_reason"] else "pending"
        status = {"status": state,
```

3. Replace exactly:

```python
    def retry_paused_preflight(self, worker_id: str) -> dict:
        """Retry only a claimed pre-effect hold after fresh capability evidence."""
        row = self.registry.get_worker_handoff(worker_id)
        safe_reasons = {"opposite_capability_unavailable", "workspace_instructions_missing",
                        "workspace_handoff_protocol_unavailable", "native_readonly_or_resume_flags_unsupported",
                        "preflight_failed", "source_process_unproved", "source_process_native_binding_unproved",
                        "remote_native_process_proof_unavailable", "target_preflight_pending",
                        "native_quota_log_scan_budget_exceeded"}
        if (not row or row["phase"] != "claimed" or row["pause_reason"] not in safe_reasons):
            return self._status(worker_id)
        evidence = _evidence(row)
        if any(key.endswith("_intent") and value for key, value in evidence.items()):
            return self._status(worker_id)
```

with:

```python
    PREEFFECT_RETRY_REASONS = frozenset({
        "opposite_capability_unavailable", "workspace_instructions_missing",
        "workspace_handoff_protocol_unavailable", "native_readonly_or_resume_flags_unsupported",
        "preflight_failed", "source_process_unproved", "source_process_native_binding_unproved",
        "remote_native_process_proof_unavailable", "target_preflight_pending",
        "native_quota_log_scan_budget_exceeded",
    })

    @staticmethod
    def _source_stop_started(row: dict, evidence: dict) -> bool:
        """True once a source stop effect was attempted or proved (the H5/H6 boundary)."""
        return (row["phase"] != "claimed" or bool(evidence.get("stop_intent"))
                or bool(evidence.get("source_stop_proof")))

    def retry_paused_preflight(self, worker_id: str) -> dict:
        """Recover a paused handoff by how far it got: retry or release before the source stop."""
        row = self.registry.get_worker_handoff(worker_id)
        if (not row or row["completed_at"] or not row["pause_reason"]
                or row["pause_reason"] == "uncertain_external_effect"):
            return self._status(worker_id)
        evidence = _evidence(row)
        if self._source_stop_started(row, evidence):
            return self._status(worker_id)
        if (row["pause_reason"] not in self.PREEFFECT_RETRY_REASONS
                or any(key.endswith("_intent") and value for key, value in evidence.items())):
            # H5: a pre-effect pause that can never succeed on retry releases.
            return self.release_paused(worker_id)
```

4. Replace exactly:

```python
            except Exception as exc:
                if resumed:
                    reason = str(exc) if isinstance(exc, HandoffHold) else "preflight_failed"
                    return self._pause(worker_id, generation, reason)
                return self._status(worker_id)
            return self.reconcile(worker_id)
```

with:

```python
            except Exception as exc:
                if resumed:
                    reason = str(exc) if isinstance(exc, HandoffHold) else "preflight_failed"
                    return self._pause(worker_id, generation, reason)
                if ((isinstance(exc, HandoffHold) and str(exc) == "native_quota_evidence_changed")
                        or self._source_session_gone(source)):
                    # H5 triggers: the source's quota reset (its evidence changed), or the
                    # source session is gone, so a pre-effect retry can never succeed.
                    return self.release_paused(worker_id)
                return self._status(worker_id)
            return self.reconcile(worker_id)

    def _source_session_gone(self, source: dict) -> bool:
        """True only when tmux positively reports the source session absent."""
        try:
            return bool(source.get("tmux_session")) and not self.tools.tmux.has_session(source["tmux_session"])
        except Exception:
            return False

    def _release_workspace_hold(self, evidence: dict) -> None:
        """Finish the workspace source hold through the private release-handoff command."""
        token = evidence.get("capability_token")
        if not token:
            return
        scope = evidence["scope"]
        released = self.workspace.release_handoff({
            "workspace_guid": scope["workspace_guid"], "generation": scope["generation"],
            "capability_token": token, "source_owner_session_id": scope["source_owner_session_id"],
        }, **evidence["transport"])
        if (released.get("protocol_version") != 1
                or (released.get("present") is not False and released.get("phase") != "finished")):
            raise HandoffHold("assignment_release_unproved")

    def release_paused(self, worker_id: str) -> dict:
        """Return a handoff paused before the source stop to running (H5)."""
        row = self.registry.get_worker_handoff(worker_id)
        if not row or row["completed_at"] or not row["pause_reason"]:
            return self._status(worker_id)
        evidence = _evidence(row)
        if (self._source_stop_started(row, evidence) or evidence.get("target_id")
                or evidence.get("bootstrap_intent")):
            return {**self._status(worker_id), "release_refused": "handoff_target_or_source_stop_recorded"}
        with self._reconcile_scope(worker_id, row["generation"], allow_paused=True) as denial:
            if denial:
                return {**self._status(worker_id), "reason": denial}
            try:
                self._release_workspace_hold(evidence)
                self.registry.release_worker_handoff(worker_id, row["generation"], row["pause_reason"])
            except Exception:
                return self._status(worker_id)
        return self._status(worker_id)
```

### Step 6: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff.py tests/test_worker_handoff_daemon.py`

Expected: all pass including the storage-backed late_recovery scenario via real release-handoff; record actual counts

### Step 7: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_registry.py commander/src/ironclaude/worker_handoff.py commander/tests/test_worker_handoff_registry.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 13: After the source stop and before the transfer completes, retry forward or stop the bootstrap and preserve; an uncertain effect needs an explicit abort; a deleted worktree is proved by PID plus start time (H6 as amended, H6a pre-transfer, H7, H7a, H7b)

**Depends on:** Task 12

**Files:**
- `commander/src/ironclaude/worker_handoff.py`
- `commander/src/ironclaude/worker_registry.py`
- `commander/src/ironclaude/worker_handoff_process.py`
- `commander/tests/test_worker_handoff.py`
- `commander/tests/test_worker_handoff_registry.py`
- `commander/tests/test_worker_handoff_process.py`

What this task implements:
- **Post-stop retry (H6, amended):** a retryable post-stop pause retries forward. Any available client may be the target, including the source's own client once its H9 quarantine expires. The retry never re-reads the stopped source's native log.
- **Before the transfer completes (H6a):** release as the source and preserve. First, workspace readback runs: if the transfer actually landed, the handoff advances to `owner_transferred` and continues as post-transfer instead (Task 15). Otherwise any recorded or launched read-only bootstrap is stopped and proved gone; only an unprovable stop holds.
- **Abort (H7, H7a):** `abort_handoff` runs the same readback branch, then keeps H7's precondition verbatim: it is refused while a recorded bootstrap is alive or cannot be proven gone.
- **Deleted worktree (H7b):** when the worktree directory no longer exists, `NativeProcessAdapter.proved_absent` proves every recorded bootstrap process gone by PID plus start time. AccessDenied still blocks.

### Step 1: RED — registry test (same-client target runtime)

In `commander/tests/test_worker_handoff_registry.py`, replace exactly `def test_claim_rejects_unknown_concrete_client(tmp_path):` with:

```python
def test_runtime_replacement_allows_same_client_only_with_new_native_session(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    source = _snapshot(registry)
    registry.claim_worker_handoff(source, generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    with pytest.raises(RuntimeError, match="identity invalid"):
        registry.replace_worker_runtime("w1", 1, "owner_transferred", client="claude", model="claude-opus",
                                        native_session_id=source["native_session_id"],
                                        tmux_session="ic-w1-h1", machine=None)
    with pytest.raises(RuntimeError, match="identity invalid"):
        registry.replace_worker_runtime("w1", 1, "owner_transferred", client="claude", model="claude-opus",
                                        native_session_id=str(uuid.uuid4()),
                                        tmux_session="ic-w1-h1", machine="other-host")
    successor = str(uuid.uuid4())
    worker = registry.replace_worker_runtime("w1", 1, "owner_transferred", client="claude",
                                             model="claude-opus", native_session_id=successor,
                                             tmux_session="ic-w1-h1", machine=None)
    assert (worker["client"], worker["native_session_id"]) == ("claude", successor)


def test_claim_rejects_unknown_concrete_client(tmp_path):
```

### Step 2: RED — controller tests

In `commander/tests/test_worker_handoff.py`:

1. Replace exactly:

```python
def _released_workspace():
```

with:

```python
def _paused_after_stop(registry, phase, reason, **evidence):
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1)
    order = ("claimed", "source_stopped", "target_ready", "pm_prepared", "owner_transferred",
             "runtime_registered", "continuation_dispatched")
    for old, new in zip(order, order[1:]):
        if old == phase:
            break
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, phase, {
        "scope": _scope(worker), "transport": {}, "capability_token": "token", **evidence})
    registry.pause_worker_handoff("w1", 1, reason)


def _readback(scope, phase, target=None):
    return {"protocol_version": 1, "present": True, "phase": phase, "target_owner_session_id": target,
            "workspace_guid": scope["workspace_guid"], "generation": scope["generation"],
            "repository_identity": scope["repository_identity"], "worker_id": scope["worker_id"],
            "source_owner_session_id": scope["source_owner_session_id"],
            "worktree_path": scope["expected_worktree_path"], "branch": scope["expected_branch"],
            "base_commit": scope["expected_base_commit"], "current_head": scope["expected_current_head"],
            "integration_target": scope["expected_integration_target"]}


def _bootstrap_tree(worktree, name="ic-w1-h1-bootstrap"):
    ps, tmux = _HandoffPs(), _HandoffTmux()
    root = _HandoffProc(41000, ["zsh"], worktree)
    child = _HandoffProc(41001, ["claude", "--permission-mode", "plan", "--tools", ""], worktree, root)
    root._pool = child._pool = [root, child]
    ps.processes = {root.pid: root, child.pid: child}
    tmux.panes[name] = root
    snapshot = NativeProcessAdapter(tmux, psutil_module=ps).snapshot(name, str(worktree), "claude")
    return ps, tmux, json.loads(json.dumps(snapshot)), root, child


def _released_workspace():
```

2. Replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
def test_post_stop_permanent_failure_releases_for_preservation_not_completion(tmp_path):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "pm_readback_failed")
    workspace = _released_workspace()
    runtime = MagicMock()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=runtime, authority=MagicMock())
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True
    assert result["reason"] == "pm_readback_failed"
    assert registry.get_worker("w1")["status"] == "running"
    row = registry.get_worker_handoff("w1")
    assert row["phase"] == "released" and row["completed_at"] is not None
    evidence = json.loads(row["evidence_json"])
    assert evidence["disposition"] == "released" and evidence["released_from_phase"] == "source_stopped"
    workspace.release_handoff.assert_called_once()
    runtime.bootstrap.assert_not_called()


def test_post_stop_preservation_stops_live_bootstrap_then_releases(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    _paused_after_stop(registry, "target_ready", "pm_prepare_failed",
                       target_id=str(uuid.uuid4()), bootstrap_process=bootstrap)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    released = controller.retry_paused_preflight("w1")
    assert released["status"] == "released" and released["preserve"] is True
    assert root.gone is True and child.gone is True
    assert registry.get_worker("w1")["status"] == "running"
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["bootstrap_stopped"] is True and evidence["bootstrap_stop_intent"] is False
    workspace.release_handoff.assert_called_once()


def test_post_stop_preservation_holds_when_bootstrap_stop_is_unprovable(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    _paused_after_stop(registry, "target_ready", "pm_prepare_failed",
                       target_id=str(uuid.uuid4()), bootstrap_process=bootstrap)
    original = ps.Process
    ps.Process = lambda pid: (_ for _ in ()).throw(ps.AccessDenied(pid)) if pid == 41001 else original(pid)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held"
    assert held["preserve_refused"] == "target_bootstrap_stop_unproved"
    assert registry.get_worker_handoff("w1")["pause_reason"] == "pm_prepare_failed"
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    workspace.release_handoff.assert_not_called()


def test_authorized_but_untransferred_handoff_stops_bootstrap_and_releases_as_source(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "pm_prepared", "owner_transfer_unproved", target_id=target_id,
                       authorize_proved=True, bootstrap_process=bootstrap)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(registry.get_worker("w1")), "begun", target_id)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    released = controller.retry_paused_preflight("w1")
    assert released["status"] == "released" and released["preserve"] is True
    assert root.gone is True and child.gone is True
    assert registry.get_worker("w1")["status"] == "running"
    workspace.release_handoff.assert_called_once()


@pytest.mark.parametrize("via", ["retry", "abort"])
@pytest.mark.parametrize("reason", ["owner_transfer_unproved", "uncertain_external_effect"])
def test_landed_transfer_advances_to_post_transfer_instead_of_releasing(tmp_path, reason, via):
    registry = _registry(tmp_path)
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "pm_prepared", reason, target_id=target_id,
                       authorize_proved=True, transfer_intent=True)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(registry.get_worker("w1")), "transferred", target_id)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    result = (controller.retry_paused_preflight("w1") if via == "retry"
              else controller.abort_handoff("w1"))
    assert "transfer_landed" in result.values()
    row = registry.get_worker_handoff("w1")
    assert (row["phase"], row["pause_reason"], row["completed_at"]) == ("owner_transferred", None, None)
    assert json.loads(row["evidence_json"])["transfer_intent"] is False
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    workspace.release_handoff.assert_not_called()


def test_previously_released_workspace_row_is_not_a_landed_transfer(tmp_path):
    registry = _registry(tmp_path)
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "pm_prepared", "owner_transfer_unproved", target_id=target_id,
                       authorize_proved=True)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(registry.get_worker("w1")), "finished", target_id)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True
    assert registry.get_worker_handoff("w1")["phase"] == "released"
    assert registry.get_worker("w1")["status"] == "running"
    workspace.release_handoff.assert_called_once()


def test_post_stop_retry_never_rereads_the_stopped_source_log(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "opposite_capability_unavailable")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the post-stop retry must not read the stopped source's native log")

    monkeypatch.setattr("ironclaude.worker_handoff.read_native_quota", forbidden)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    controller._validate_quota = forbidden
    controller._target = MagicMock(return_value=(SimpleNamespace(
        client="codex", model="gpt-5.6-sol", effective_tier="opus"), "opus"))
    controller._start_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    assert controller._target.call_args.kwargs == {"allow_source_client": True}
    controller._start_target.assert_called_once()
    assert registry.get_worker_handoff("w1")["pause_reason"] is None


def test_uncertain_external_effect_stays_paused_without_release(tmp_path):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "uncertain_external_effect")
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    for _ in range(2):
        assert controller.retry_paused_preflight("w1")["status"] == "held"
    workspace.release_handoff.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"


@pytest.mark.parametrize("bootstrap_state", ["alive", "unproved", "gone", "gone_worktree_removed"])
def test_abort_handoff_requires_recorded_bootstrap_proved_gone(tmp_path, bootstrap_state):
    import shutil
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    _paused_after_stop(registry, "target_ready", "uncertain_external_effect",
                       target_id=str(uuid.uuid4()), bootstrap_process=bootstrap)
    if bootstrap_state.startswith("gone"):
        root.gone = child.gone = True
        if bootstrap_state == "gone_worktree_removed":
            shutil.rmtree(worktree)
    elif bootstrap_state == "unproved":
        # Root exited; the child's identity cannot be read, so it is not proved gone.
        root.gone = True
        original = ps.Process
        ps.Process = lambda pid: (_ for _ in ()).throw(ps.AccessDenied(pid)) if pid == 41001 else original(pid)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    result = controller.abort_handoff("w1")
    if bootstrap_state.startswith("gone"):
        assert result["status"] == "released"
        assert registry.get_worker("w1")["status"] == "running"
        workspace.release_handoff.assert_called_once()
    else:
        assert result["status"] == "held"
        assert result["abort_refused"] == "target_bootstrap_alive_or_unproved"
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        workspace.release_handoff.assert_not_called()


def test_post_stop_retry_accepts_source_client_once_its_quarantine_expires(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "opposite_capability_unavailable")
    raw = {"clients": {"claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}},
                       "codex": {"enabled": True, "path": "codex", "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol", "fable": "gpt-6-astra"}}},
           "roles": {role: {"preferred": "claude", "clients": ["claude", "codex"]} for role in ("brain", "worker", "grader", "advisor")},
           "shadow_mode": False}
    config = parse_provider_config(raw)
    state = ProviderState(registry._conn)
    caps = CapabilityRegistry(state)

    class Probe:
        def probe_local(self, config, client, role, tier):
            ready = client == "claude"
            return ClientCapability("local", client, role, tier, ready, ready, ready, ready, ready)

    monkeypatch.setattr("ironclaude.provider_capabilities.CapabilityProbe", Probe)
    account = ("local", "claude", "worker", state.worker_usage_scope_tier("worker", "opus"))
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:1",
                           usage_cursor=1, usage_event_key="quota",
                           usage_event_timestamp="2026-10-05T12:00:00Z", usage_expires_at=4_000_000_000.0)
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None,
                            _provider_router=lambda: (ProviderRouter(config, state, caps), config, state, caps))
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    controller._start_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held" and held["reason"] == "opposite_capability_unavailable"
    controller._start_target.assert_not_called()
    registry._conn.execute("UPDATE provider_capability_state SET usage_expires_at=1 WHERE client='claude' AND tier=?",
                           (account[3],))
    registry._conn.commit()
    source = json.loads(registry.get_worker_handoff("w1")["snapshot_json"])
    with pytest.raises(HandoffHold, match="opposite_capability_unavailable"):
        controller._target(source)
    controller.retry_paused_preflight("w1")
    row = registry.get_worker_handoff("w1")
    assert row["pause_reason"] is None
    assert json.loads(row["evidence_json"])["target_client"] == "claude"
    controller._start_target.assert_called_once()


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

3. In `commander/tests/test_worker_handoff_process.py`, replace exactly `def test_stop_signals_only_recorded_owned_tree_then_proves(tmp_path):` with:

```python
def test_proved_absent_accepts_gone_tree_without_worktree(tmp_path):
    import shutil
    worktree = tmp_path / "wt"
    worktree.mkdir()
    adapter, root, child = _adapter(worktree)
    snapshot = adapter.snapshot("ic-1-h1-bootstrap", worktree, "claude")
    root.gone = child.gone = True
    shutil.rmtree(worktree)
    with pytest.raises(FileNotFoundError):
        adapter.verify_stopped(snapshot)
    assert adapter.proved_absent(snapshot) is True


def test_proved_absent_refuses_live_or_unreadable_identity(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    snapshot = adapter.snapshot("ic-1-h1-bootstrap", tmp_path, "claude")
    assert adapter.proved_absent(snapshot) is False
    root.gone = True
    original = adapter._psutil.Process
    adapter._psutil.Process = lambda pid: ((_ for _ in ()).throw(adapter._psutil.AccessDenied(pid))
                                           if pid == child.pid else original(pid))
    with pytest.raises(ProcessProofError):
        adapter.proved_absent(snapshot)


def test_stop_signals_only_recorded_owned_tree_then_proves(tmp_path):
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py tests/test_worker_handoff_registry.py tests/test_worker_handoff_process.py`

Expected: FAIL: test_post_stop_permanent_failure_releases_for_preservation_not_completion, test_post_stop_preservation_stops_live_bootstrap_then_releases and test_authorized_but_untransferred_handoff_stops_bootstrap_and_releases_as_source (stay held), test_landed_transfer_advances_to_post_transfer_instead_of_releasing[*] (no transfer_landed; row stays at pm_prepared; abort variants AttributeError abort_handoff), test_previously_released_workspace_row_is_not_a_landed_transfer (stays held), test_post_stop_retry_never_rereads_the_stopped_source_log (no forward retry), test_abort_handoff_requires_recorded_bootstrap_proved_gone[*] (AttributeError abort_handoff), test_post_stop_retry_accepts_source_client_once_its_quarantine_expires (no forward retry), test_runtime_replacement_allows_same_client_only_with_new_native_session (same client refused), test_proved_absent_accepts_gone_tree_without_worktree and test_proved_absent_refuses_live_or_unreadable_identity (AttributeError proved_absent); test_uncertain_external_effect_stays_paused_without_release passes as guard; test_post_stop_preservation_holds_when_bootstrap_stop_is_unprovable fails on KeyError preserve_refused; record actual counts

### Step 4: GREEN — `worker_registry.py` and `worker_handoff_process.py`

1. In `worker_handoff_process.py`, replace exactly `    def verify_stopped(self, snapshot, *, ssh_host=None) -> dict:` with:

```python
    def proved_absent(self, snapshot) -> bool:
        """H7b: prove every recorded process gone by PID plus start time, without the worktree.

        Used only when the worktree directory no longer exists, so verify_stopped's
        workspace resolution cannot run. AccessDenied or an unreadable identity raises.
        """
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("processes"), list) or not snapshot["processes"]:
            raise ProcessProofError("invalid process snapshot")
        required = {"pid", "creation_time", "ppid", "cwd", "uid", "cmdline"}
        for row in snapshot["processes"]:
            if (not isinstance(row, dict) or set(row) != required or not isinstance(row["pid"], int)
                    or not isinstance(row["creation_time"], (int, float))):
                raise ProcessProofError("invalid process identity snapshot")
        self._remote_guard(snapshot.get("ssh_host"))
        return all(self._same_process(item) is None for item in snapshot["processes"])

    def verify_stopped(self, snapshot, *, ssh_host=None) -> dict:
```

2. In `worker_registry.py`, replace exactly:

```python
            if client == source["client"] or native_session_id == source["native_session_id"] or machine != source["machine"]:
```

with:

```python
            # H6: after a post-stop retry the source's own client may be the target,
            # always with a new native session on the same host.
            if native_session_id == source["native_session_id"] or machine != source["machine"]:
```

### Step 5: GREEN — `worker_handoff.py`

1. Replace exactly `    def _target(self, worker: dict):` with:

```python
    def _target(self, worker: dict, *, allow_source_client: bool = False):
```

2. Replace exactly:

```python
            if client == worker["client"]:
                continue
```

with:

```python
            if client == worker["client"] and not allow_source_client:
                continue
```

3. Replace exactly `            handle = router.resolve("worker", tier, [host], excluded_clients=(worker["client"],))` with:

```python
            handle = router.resolve("worker", tier, [host],
                                    excluded_clients=() if allow_source_client else (worker["client"],))
```

4. Replace exactly `        if handle.client == worker["client"] or handle.host != host:` with:

```python
        if (handle.client == worker["client"] and not allow_source_client) or handle.host != host:
```

5. Replace exactly:

```python
        "native_quota_log_scan_budget_exceeded",
    })
```

with:

```python
        "native_quota_log_scan_budget_exceeded",
    })
    POST_STOP_RETRY_REASONS = frozenset({
        "opposite_capability_unavailable", "target_preflight_pending",
        "native_readonly_or_resume_flags_unsupported", "workspace_quiescence_unproved",
        "source_stop_unproved", "handoff_reconcile_owner_unproved",
    })
    POST_TRANSFER_PHASES = ("owner_transferred", "runtime_registered", "continuation_dispatched")
```

6. Replace exactly:

```python
        if (not row or row["completed_at"] or not row["pause_reason"]
                or row["pause_reason"] == "uncertain_external_effect"):
            return self._status(worker_id)
        evidence = _evidence(row)
        if self._source_stop_started(row, evidence):
            return self._status(worker_id)
```

with:

```python
        if not row or row["completed_at"] or not row["pause_reason"]:
            return self._status(worker_id)
        evidence = _evidence(row)
        if row["pause_reason"] == "uncertain_external_effect":
            # H7: the pause stays, unless readback proves the transfer landed (H6a),
            # which is how _reconcile_intent resolves a pending transfer_intent.
            if row["phase"] == "pm_prepared" and evidence.get("target_id"):
                return self._settle_landed_transfer(worker_id)
            return self._status(worker_id)
        if self._source_stop_started(row, evidence):
            # H6: retry forward while a forward path can still exist; otherwise
            # release and hand the worker to the dead-session preserve path.
            if row["pause_reason"] in self.POST_STOP_RETRY_REASONS:
                return self._retry_forward(worker_id)
            return self._preserve_after_stop(worker_id)
```

7. Replace exactly `    def _release_workspace_hold(self, evidence: dict) -> None:` with:

```python
    def _retry_forward(self, worker_id: str) -> dict:
        """Resume a post-stop hold forward; any available client, including the source's, may be the target.

        H6 (amended): never re-reads the stopped source's native log; the source
        client's availability comes from the H9 quarantine expiry via the router.
        """
        row = self.registry.get_worker_handoff(worker_id)
        if not row or row["completed_at"] or not row["pause_reason"]:
            return self._status(worker_id)
        generation, phase = row["generation"], row["phase"]
        evidence = _evidence(row)
        source = json.loads(row["snapshot_json"])
        with self._reconcile_scope(worker_id, generation, allow_paused=True) as denial:
            if denial:
                return {**self._status(worker_id), "reason": denial}
            target = None
            if not evidence.get("target_id") and not evidence.get("bootstrap_intent"):
                try:
                    target, _tier = self._target(source, allow_source_client=True)
                except Exception:
                    return self._status(worker_id)
            try:
                self.registry.resume_worker_handoff(worker_id, generation, phase)
                if target is not None:
                    self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                        "target_client": target.client, "target_model": target.model,
                        "target_tier": target.effective_tier})
            except Exception:
                return self._status(worker_id)
            return self._reconcile_owned(worker_id)

    def _bootstrap_proven_gone(self, evidence: dict) -> bool:
        """A recorded target bootstrap counts as gone only on PID plus start-time proof (H7).

        H7b: when the worktree directory no longer exists, every recorded process must be
        proved absent by PID plus start time. H7a: with only a launch intent and no
        recorded process, the bootstrap tmux session must be absent and no process may
        remain associated with the worktree.
        """
        snapshot = evidence.get("bootstrap_process")
        if isinstance(snapshot, dict):
            try:
                if not Path(evidence["scope"]["expected_worktree_path"]).is_dir():
                    return bool(self.process.proved_absent(snapshot))
                return bool(self.process.verify_stopped(snapshot).get("stopped"))
            except Exception:
                return False
        name = evidence.get("bootstrap_intent")
        if name:
            try:
                if self.tools.tmux.has_session(name):
                    return False
                return bool(self.process.assert_quiescent(
                    evidence["scope"]["expected_worktree_path"]).get("quiescent"))
            except Exception:
                return False
        return True

    def _stop_bootstrap(self, worker_id: str, generation: int, phase: str, reason: str,
                        evidence: dict) -> bool:
        """H6a: stop and prove gone any recorded or launched read-only bootstrap.

        Mirrors _resume_target's bootstrap stop (intent recorded before the signal,
        proof after). The pause is lifted only for the evidence writes and is always
        restored with the same reason. Only an unprovable stop returns False.
        """
        snapshot = evidence.get("bootstrap_process") if isinstance(evidence.get("bootstrap_process"), dict) else None
        if snapshot is None and evidence.get("bootstrap_intent"):
            name = evidence["bootstrap_intent"]
            try:
                if self.tools.tmux.has_session(name):
                    snapshot = self.process.snapshot(name, evidence["scope"]["expected_worktree_path"],
                                                     evidence.get("target_client"))
            except Exception:
                return False
        if snapshot is None:
            return self._bootstrap_proven_gone(evidence)
        if self._bootstrap_proven_gone({**evidence, "bootstrap_process": snapshot}):
            return True
        try:
            self.registry.resume_worker_handoff(worker_id, generation, phase)
            try:
                self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                    "bootstrap_process": snapshot, "bootstrap_stop_intent": True})
                proof = self.process.stop_and_prove(snapshot)
                if not proof.get("stopped"):
                    raise HandoffHold("bootstrap_stop_unproved")
                self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                    "bootstrap_stop_intent": False, "bootstrap_stopped": True,
                    "bootstrap_stop_proof": proof})
            finally:
                self.registry.pause_worker_handoff(worker_id, generation, reason)
        except Exception:
            return False
        return True

    def _transfer_landed(self, phase: str, evidence: dict):
        """H6a readback: True when ownership already moved to the recorded target, None if unreadable."""
        if phase != "pm_prepared" or not evidence.get("target_id"):
            return False
        try:
            state = self._workspace_readback(evidence)
        except Exception:
            return None
        # Only "transferred" proves the transfer landed. At pm_prepared a "finished" row can only
        # come from an earlier release, so it falls through to the source release (a no-op replay).
        return (state.get("phase") == "transferred"
                and state.get("target_owner_session_id") == evidence["target_id"])

    def _advance_landed_transfer(self, worker_id: str, generation: int, reason: str,
                                 refusal_key: str) -> dict:
        """The transfer actually landed: continue as post-transfer, as _reconcile_intent does."""
        try:
            self.registry.resume_worker_handoff(worker_id, generation, "pm_prepared")
            self.registry.advance_worker_handoff(worker_id, generation, "pm_prepared", "owner_transferred",
                                                 evidence={"transfer_intent": False})
        except Exception as exc:
            return {**self._status(worker_id), refusal_key: str(exc) or "advance_failed"}
        return {**self._status(worker_id), refusal_key: "transfer_landed"}

    def _settle_landed_transfer(self, worker_id: str) -> dict:
        """Advance an uncertain pm_prepared pause only when readback proves the transfer landed."""
        row = self.registry.get_worker_handoff(worker_id)
        with self._reconcile_scope(worker_id, row["generation"], allow_paused=True) as denial:
            if denial:
                return {**self._status(worker_id), "reason": denial}
            if self._transfer_landed(row["phase"], _evidence(row)) is True:
                return self._advance_landed_transfer(worker_id, row["generation"], row["pause_reason"],
                                                     "settled")
        return self._status(worker_id)

    def _release_after_stop(self, worker_id: str, refusal_key: str) -> dict:
        """Release a post-stop hold as the source once the bootstrap is settled (H6, H6a, H7)."""
        row = self.registry.get_worker_handoff(worker_id)
        if not row or row["completed_at"] or not row["pause_reason"]:
            return {**self._status(worker_id), refusal_key: "no_paused_handoff"}
        generation, phase, reason = row["generation"], row["phase"], row["pause_reason"]
        evidence = _evidence(row)
        abort = refusal_key == "abort_refused"
        with self._reconcile_scope(worker_id, generation, allow_paused=True) as denial:
            if denial:
                return {**self._status(worker_id), refusal_key: denial}
            landed = self._transfer_landed(phase, evidence)
            if landed is None:
                return {**self._status(worker_id), refusal_key: "workspace_readback_unproved"}
            if landed:
                return self._advance_landed_transfer(worker_id, generation, reason, refusal_key)
            if phase in self.POST_TRANSFER_PHASES:
                return {**self._status(worker_id), refusal_key: "post_transfer_release_unavailable"}
            if abort:
                if not self._bootstrap_proven_gone(evidence):
                    return {**self._status(worker_id), refusal_key: "target_bootstrap_alive_or_unproved"}
            elif not self._stop_bootstrap(worker_id, generation, phase, reason, evidence):
                return {**self._status(worker_id), refusal_key: "target_bootstrap_stop_unproved"}
            try:
                self._release_workspace_hold(evidence)
                self.registry.release_worker_handoff(worker_id, generation, reason, after_stop=True)
            except Exception as exc:
                return {**self._status(worker_id), refusal_key: str(exc) or "release_failed"}
        return self._status(worker_id)

    def _preserve_after_stop(self, worker_id: str) -> dict:
        """H6/H6a: forward is impossible for good; release so the daemon preserves the work."""
        result = self._release_after_stop(worker_id, "preserve_refused")
        if result.get("status") == "released":
            return {**result, "preserve": True}
        return result

    def abort_handoff(self, worker_id: str) -> dict:
        """H7 operator abort: release a paused handoff once any recorded bootstrap is proved gone."""
        return self._release_after_stop(worker_id, "abort_refused")

    def _release_workspace_hold(self, evidence: dict) -> None:
```

### Step 6: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py tests/test_worker_handoff_registry.py tests/test_worker_handoff_process.py tests/test_worker_handoff_daemon.py`

Expected: all pass; record actual counts

### Step 7: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_handoff.py commander/src/ironclaude/worker_registry.py commander/src/ironclaude/worker_handoff_process.py commander/tests/test_worker_handoff.py commander/tests/test_worker_handoff_registry.py commander/tests/test_worker_handoff_process.py`

Expected: exit 0

---

## Task 14: A new controller rebuilds the bootstrap lease from persisted identity (H8)

**Depends on:** Task 13

**Files:**
- `commander/src/ironclaude/worker_handoff_process.py`
- `commander/src/ironclaude/worker_handoff.py`
- `commander/tests/test_worker_handoff_process.py`
- `commander/tests/test_worker_handoff.py`

### Step 1: RED — adapter tests

In `commander/tests/test_worker_handoff_process.py`:

1. Replace exactly:

```python
import os
import signal
```

with:

```python
import json
import os
import signal
```

2. Replace exactly `def test_stop_signals_only_recorded_owned_tree_then_proves(tmp_path):` with:

```python
def test_restarted_adapter_adopts_recorded_readonly_bootstrap(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    child._cmdline = ["claude", "--permission-mode", "plan", "--tools", ""]
    snapshot = json.loads(json.dumps(adapter.snapshot("ic-1-h1-bootstrap", tmp_path, "claude")))
    restarted = NativeProcessAdapter(_Tmux(root.pid), psutil_module=adapter._psutil)
    with pytest.raises(ProcessProofError, match="readonly native ownership"):
        restarted.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])
    restarted.adopt_readonly_bootstrap(snapshot)
    assert restarted.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])["quiescent"]


@pytest.mark.parametrize("change", ["reused_pid", "writable_producer", "exited"])
def test_restarted_adapter_refuses_unproved_bootstrap_identity(tmp_path, change):
    adapter, root, child = _adapter(tmp_path)
    child._cmdline = ["claude", "--permission-mode", "plan", "--tools", ""]
    snapshot = json.loads(json.dumps(adapter.snapshot("ic-1-h1-bootstrap", tmp_path, "claude")))
    restarted = NativeProcessAdapter(_Tmux(root.pid), psutil_module=adapter._psutil)
    if change == "reused_pid":
        child._created = 9.0
    elif change == "writable_producer":
        snapshot["readonly_bootstrap"] = False
    else:
        child.gone = True
    with pytest.raises(ProcessProofError):
        restarted.adopt_readonly_bootstrap(snapshot)
    with pytest.raises(ProcessProofError):
        restarted.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])


def test_stop_signals_only_recorded_owned_tree_then_proves(tmp_path):
```

### Step 2: RED — controller restart test

In `commander/tests/test_worker_handoff.py`, replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
def test_restart_after_target_ready_rebuilds_bootstrap_lease_and_transfers(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    worktree = Path(source["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared")):
        registry.advance_worker_handoff("w1", 1, old, new)
    target_id = str(uuid.uuid4())
    registry.record_worker_handoff_evidence("w1", 1, "pm_prepared", {
        "scope": _scope(source), "transport": {}, "capability_token": "token",
        "authorize_proved": True, "target_id": target_id, "bootstrap_process": bootstrap})
    workspace = MagicMock()
    workspace.transfer_handoff.return_value = {"protocol_version": 1, "phase": "transferred",
                                               "target_owner_session_id": target_id}
    restarted = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(),
        authority=MagicMock(), pm_db_path=tmp_path / "pm.db")
    restarted._resume_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    restarted.reconcile("w1")
    row = registry.get_worker_handoff("w1")
    assert row["phase"] == "owner_transferred" and row["pause_reason"] is None
    workspace.transfer_handoff.assert_called_once()
    restarted._resume_target.assert_called_once()
    assert root.gone is False and child.gone is False


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_process.py tests/test_worker_handoff.py -k "adopt or restarted or rebuilds_bootstrap"`

Expected: FAIL: adapter tests (AttributeError adopt_readonly_bootstrap) and test_restart_after_target_ready_rebuilds_bootstrap_lease_and_transfers (pauses at pm_prepared on 'lacks readonly native ownership proof'); record actual counts

### Step 4: GREEN — `worker_handoff_process.py`

Replace exactly `    def assert_quiescent(self, workspace_path, *, allowed_pids=(), ssh_host=None, _ignore=frozenset(), _source_lease=False) -> dict:` with:

```python
    def adopt_readonly_bootstrap(self, snapshot, *, ssh_host=None) -> None:
        """Rebuild a read-only bootstrap lease from persisted identity after a restart (H8).

        Re-proves that every recorded PID still has its recorded start time, that the
        pane tree is unchanged, and that its producer is still the read-only bootstrap.
        """
        self._validate_snapshot(snapshot, ssh_host)
        if snapshot.get("readonly_bootstrap") is not True:
            raise ProcessProofError("recorded bootstrap is not read-only")
        if any(self._same_process(item) is None for item in snapshot["processes"]):
            raise ProcessProofError("recorded bootstrap process exited or PID was reused")
        identities = {(item["pid"], item["creation_time"]) for item in snapshot["processes"]}
        lease = {"client": snapshot["client"], "pane_pid": snapshot["pane_pid"], "identities": identities}
        if lease not in self._readonly_leases:
            self._readonly_leases.append(lease)
        if not self._readonly_identity_is_live(next(iter(identities))):
            self._readonly_leases.remove(lease)
            raise ProcessProofError("recorded bootstrap no longer proves read-only ownership")

    def assert_quiescent(self, workspace_path, *, allowed_pids=(), ssh_host=None, _ignore=frozenset(), _source_lease=False) -> dict:
```

### Step 5: GREEN — `worker_handoff.py`

Replace exactly `        allowed = [item["pid"] for item in evidence["bootstrap_process"]["processes"]]` with:

```python
        # H8: a new controller has no in-memory lease; rebuild it from the persisted identity.
        self.process.adopt_readonly_bootstrap(evidence["bootstrap_process"])
        allowed = [item["pid"] for item in evidence["bootstrap_process"]["processes"]]
```

### Step 6: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_process.py tests/test_worker_handoff.py`

Expected: all pass; record actual counts

This includes the storage-backed normal scenarios, where the adapter adopts the lease it already holds without changing anything.

### Step 7: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_handoff_process.py commander/src/ironclaude/worker_handoff.py commander/tests/test_worker_handoff_process.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 15: After the transfer, retry forward or preserve as the target (H6a post-transfer, H6b)

**Depends on:** Task 14

**Files:**
- `commander/src/ironclaude/worker_handoff.py`
- `commander/src/ironclaude/worker_registry.py`
- `commander/tests/test_worker_handoff.py`
- `commander/tests/test_worker_handoff_registry.py`

What this task implements (H6a, after ownership moved to the target):
- **Retry forward wherever possible.** At `owner_transferred` a stale or half-started resume session is settled first:
  - if `inspect_resumed` confirms it, `_reconcile_intent` registers it and advances;
  - otherwise a durable `stale_resume_stop_intent` is recorded first (as `_stop_bootstrap` does); then the session is stopped and proved gone, and `resume_intent` is cleared, so `resume()` does not refuse the existing session name forever (`target_resume_session_already_exists`).
- **Retried proofs.** At `runtime_registered` an undelivered dispatch (tmux refused the keys) is replayed. At `continuation_dispatched` an unfinished finish whose readback still shows `transferred` is retried through `finish-handoff-after-target` (H6b, from Task 5, client method from Task 7), and `_reconcile_intent` then completes the handoff.
- **Finishing after the target commits (H6b).** Both the finish retry and the preserve-as-target finish use the after-target variant. It accepts a live HEAD that fast-forwards from the granted HEAD while the target owns the assignment. An end-to-end test runs the real workspace CLI: the target commits after the transfer, the exact-HEAD finish is refused, and the preserve path finishes, releases to the target and preserves.
- **When forward is impossible for good** (any other post-transfer reason, or an operator abort):
  1. any target session that received no continuation is stopped and proved gone, with a durable `target_session_stop_intent` recorded before the signal;
  2. the registry adopts the TARGET runtime through `replace_worker_runtime(..., "owner_transferred", ...)`, exactly as `_register_target` does, when it still holds the source runtime;
  3. the workspace row is finished through `finish_handoff`;
  4. the handoff is released with disposition `released_to_target` and `preserve`.
- **Why the target runtime is adopted:** preservation after the transfer needs the target runtime and a finished workspace row.
  - `_registry_workspace_assignment` derives `owner_session_id` from the worker's `native_session_id`.
  - `assertNoPendingHandoff` blocks every assignment mutation while the row is `transferred`.
  - The dead-session finalize's `has_session` defence requires the target session to be stopped.
- **No reverting to the source:** no primitive exists, and consumed human intents cannot be restored.

### Step 1: RED — registry test

In `commander/tests/test_worker_handoff_registry.py`, replace exactly:

```python
def test_post_stop_release_refuses_once_target_owns_runtime(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    registry.pause_worker_handoff("w1", 1, "target_resume_identity_unproved")
    with pytest.raises(RuntimeError, match="release refused"):
        registry.release_worker_handoff("w1", 1, "target_resume_identity_unproved", after_stop=True)
    assert registry.get_worker("w1")["status"] == "handoff_pending"
```

with:

```python
def test_post_stop_release_after_transfer_adopts_target_runtime(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    target_id = str(uuid.uuid4())
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-6-sol",
                                    native_session_id=target_id, tmux_session="ic-w1-h1", machine=None)
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "target_tier": "sonnet", "assignment_finished": True})
    registry.pause_worker_handoff("w1", 1, "target_runtime_changed")
    row = registry.release_worker_handoff("w1", 1, "target_runtime_changed", after_stop=True,
                                          disposition="released_to_target")
    assert json.loads(row["evidence_json"])["disposition"] == "released_to_target"
    worker = registry.get_worker("w1")
    assert (worker["status"], worker["client"], worker["native_session_id"]) == ("running", "codex", target_id)
    # The adopted target runtime keeps its proven tier, not the source type's tier (opus).
    assert registry.effective_worker_tier(worker) == "sonnet"


def test_post_transfer_release_refuses_an_unexpected_runtime(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-6-sol",
                                    native_session_id=str(uuid.uuid4()), tmux_session="ic-w1-h1", machine=None)
    registry._conn.execute("UPDATE workers SET model='foreign' WHERE id='w1'")
    registry._conn.commit()
    registry.pause_worker_handoff("w1", 1, "target_runtime_changed")
    with pytest.raises(RuntimeError, match="runtime changed"):
        registry.release_worker_handoff("w1", 1, "target_runtime_changed", after_stop=True,
                                        disposition="released_to_target")
    assert registry.get_worker("w1")["status"] == "handoff_pending"
```

### Step 2: RED — controller tests

In `commander/tests/test_worker_handoff.py`, replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
def test_stale_resume_session_is_stopped_then_resumed_forward(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, _session_tree, root, child = _bootstrap_tree(worktree, name="ic-w1-h1")
    tmux.has_session = lambda name, ssh_host=None: name in tmux.panes and not tmux.panes[name].gone
    _paused_after_stop(registry, "owner_transferred", "target_resume_session_already_exists",
                       target_id=str(uuid.uuid4()), target_client="claude", target_model="claude-opus",
                       resume_intent="ic-w1-h1", resume_started_at=123.0, bootstrap_stopped=True)
    runtime = MagicMock()
    runtime.inspect_resumed.return_value = False
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=MagicMock(), _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=runtime, authority=MagicMock())
    controller._resume_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    assert root.gone is True and child.gone is True
    row = registry.get_worker_handoff("w1")
    evidence = json.loads(row["evidence_json"])
    assert row["pause_reason"] is None and evidence["resume_intent"] is False
    assert evidence["stale_resume_stop_intent"] is False
    assert evidence["stale_resume_process"]["session_name"] == "ic-w1-h1"
    assert evidence["stale_resume_stop_proof"]["stopped"] is True
    controller._resume_target.assert_called_once()


def test_pending_stale_resume_stop_intent_self_heals_on_reconcile(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, stale, root, child = _bootstrap_tree(worktree, name="ic-w1-h1")
    root.gone = child.gone = True
    source = registry.get_worker("w1")
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "scope": _scope(source), "transport": {}, "capability_token": "token",
        "target_id": str(uuid.uuid4()), "target_client": "claude", "target_model": "claude-opus",
        "resume_intent": "ic-w1-h1", "resume_started_at": 123.0, "bootstrap_stopped": True,
        "stale_resume_stop_intent": True, "stale_resume_process": stale})
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=MagicMock(), _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    controller._resume_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.reconcile("w1")
    row = registry.get_worker_handoff("w1")
    evidence = json.loads(row["evidence_json"])
    assert evidence["stale_resume_stop_intent"] is False and evidence["resume_intent"] is False
    assert evidence["stale_resume_stop_proof"]["stopped"] is True
    assert row["pause_reason"] is None
    controller._resume_target.assert_called_once()


def test_undelivered_continuation_dispatch_is_retried_forward(tmp_path):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "runtime_registered", "continuation_dispatch_unproved",
                       target_id=str(uuid.uuid4()), target_session="ic-w1-h1", dispatch_intent=True)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    controller._dispatch = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    row = registry.get_worker_handoff("w1")
    assert row["pause_reason"] is None
    assert json.loads(row["evidence_json"])["dispatch_intent"] is False
    controller._dispatch.assert_called_once()


def test_permanent_failure_after_transfer_preserves_as_the_target(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "owner_transferred", "target_runtime_changed", target_id=target_id,
                       target_client="codex", target_model="gpt-5.6-sol", bootstrap_stopped=True)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(source), "transferred", target_id)
    workspace.finish_handoff_after_target.return_value = {"protocol_version": 1, "phase": "finished"}
    tmux = MagicMock()
    tmux.has_session.return_value = False
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True
    worker = registry.get_worker("w1")
    assert (worker["status"], worker["client"], worker["native_session_id"], worker["tmux_session"]) == (
        "running", "codex", target_id, "ic-w1-h1")
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["disposition"] == "released_to_target" and evidence["assignment_finished"] is True
    workspace.finish_handoff_after_target.assert_called_once_with({**_scope(source), "capability_token": "token"})
    workspace.finish_handoff.assert_not_called()
    workspace.release_handoff.assert_not_called()


def test_finish_retry_uses_after_target_finish_then_completes(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    target_id = str(uuid.uuid4())
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-5.6-sol",
                                    native_session_id=target_id, tmux_session="ic-w1-h1", machine=None)
    for old, new in (("owner_transferred", "runtime_registered"), ("runtime_registered", "continuation_dispatched")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, "continuation_dispatched", {
        "scope": _scope(source), "transport": {}, "capability_token": "token", "target_id": target_id,
        "finish_intent": True})
    registry.pause_worker_handoff("w1", 1, "assignment_finish_unproved")
    workspace = MagicMock()
    workspace.handoff_status.side_effect = [_readback(_scope(source), "transferred", target_id),
                                            _readback(_scope(source), "finished", target_id)]
    workspace.finish_handoff_after_target.return_value = {"protocol_version": 1, "phase": "finished"}
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    assert controller.retry_paused_preflight("w1")["status"] == "completed"
    assert registry.get_worker("w1")["status"] == "running"
    workspace.finish_handoff_after_target.assert_called_once()
    workspace.finish_handoff.assert_not_called()


def test_target_commit_after_transfer_then_permanent_failure_finishes_and_preserves(tmp_path):
    primary = tmp_path / "primary"; primary.mkdir()
    for argv in (["git", "init", "-b", "main", str(primary)],
                 ["git", "-C", str(primary), "config", "user.email", "test@example.com"],
                 ["git", "-C", str(primary), "config", "user.name", "Test"]):
        subprocess.run(argv, check=True, capture_output=True)
    (primary / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "base"], check=True, capture_output=True)
    workspace_db = tmp_path / "workspace.db"
    runner, plugin_root = _native_runner(workspace_db)
    workspace = WorkspaceClient(plugin_root, runner=runner)
    source_id, target_id = str(uuid.uuid4()), str(uuid.uuid4())
    reserved = workspace.allocate({"repository_path": str(primary), "workspace_guid": str(uuid.uuid4()),
                                   "worker_id": "w1", "integration_target": "main"})
    assignment = workspace.bind({"repository_path": str(primary), "repository_identity": reserved["repository_identity"],
                                 "workspace_guid": reserved["workspace_guid"], "worker_id": "w1",
                                 "owner_session_id": source_id, "expected_lifecycle": "active",
                                 "expected_worktree_path": reserved["worktree_path"],
                                 "expected_branch": reserved["branch"], "expected_base_commit": reserved["base_commit"],
                                 "expected_current_head": reserved["current_head"]})
    worktree = Path(assignment["worktree_path"])
    registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    registry.register_worker("w1", "claude-opus", "ic-w1", repo=str(primary), description="original objective",
                             client="claude", model="opus", native_session_id=source_id)
    registry.set_worker_workspace("w1", assignment)
    worker = registry.get_worker("w1")
    head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    scope = {**_scope(worker, head), "repository_path": str(primary)}
    authority = WorkspaceHandoffAuthority(workspace_db)
    grant = authority.grant(workspace_guid=scope["workspace_guid"], generation=1,
                            repository_identity=scope["repository_identity"], worker_id="w1",
                            worktree_path=scope["expected_worktree_path"], branch=scope["expected_branch"],
                            base_commit=scope["expected_base_commit"], current_head=head,
                            integration_target=scope["expected_integration_target"],
                            source_owner_session_id=source_id)
    payload = {**scope, "capability_token": grant.capability_token}
    workspace.begin_handoff(payload)
    authority.authorize_target(workspace_guid=scope["workspace_guid"], generation=1,
                               capability_token=grant.capability_token, source_owner_session_id=source_id,
                               target_owner_session_id=target_id)
    workspace.transfer_handoff({**payload, "target_owner_session_id": target_id})
    # The target commits after the transfer, so the granted HEAD is no longer the live HEAD.
    (worktree / "target.txt").write_text("target work\n")
    subprocess.run(["git", "-C", str(worktree), "add", "target.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "target work"], check=True, capture_output=True)
    with pytest.raises(WorkspaceClientError, match="live Git evidence"):
        workspace.finish_handoff(payload)
    registry.claim_worker_handoff(worker, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "scope": scope, "transport": {}, "capability_token": grant.capability_token, "target_id": target_id,
        "target_client": "codex", "target_model": "gpt-5.6-sol", "target_tier": "opus", "bootstrap_stopped": True})
    registry.pause_worker_handoff("w1", 1, "target_runtime_changed")
    tmux = MagicMock()
    tmux.has_session.return_value = False
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=authority)
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True, result
    assert workspace.handoff_status({"workspace_guid": scope["workspace_guid"]})["phase"] == "finished"
    current = registry.get_worker("w1")
    assert (current["status"], current["client"], current["native_session_id"]) == ("running", "codex", target_id)
    assert json.loads(registry.get_worker_handoff("w1")["evidence_json"])["disposition"] == "released_to_target"


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff.py`

Expected: FAIL: test_post_stop_release_after_transfer_adopts_target_runtime (TypeError disposition / release refused; then tier opus instead of sonnet), test_post_transfer_release_refuses_an_unexpected_runtime (TypeError disposition), test_stale_resume_session_is_stopped_then_resumed_forward and test_undelivered_continuation_dispatch_is_retried_forward (not retry reasons; held with post_transfer_release_unavailable), test_permanent_failure_after_transfer_preserves_as_the_target, test_finish_retry_uses_after_target_finish_then_completes and test_target_commit_after_transfer_then_permanent_failure_finishes_and_preserves (held with post_transfer_release_unavailable), test_pending_stale_resume_stop_intent_self_heals_on_reconcile (stale_resume_stop_intent stays True: no reconcile handler); record actual counts

### Step 4: GREEN — `worker_registry.py`

1. Replace exactly:

```python
    def release_worker_handoff(self, worker_id: str, generation: int, expected_pause_reason: str,
                               *, after_stop: bool = False) -> dict:
```

with:

```python
    def release_worker_handoff(self, worker_id: str, generation: int, expected_pause_reason: str,
                               *, after_stop: bool = False, disposition: str = "released") -> dict:
```

2. Replace exactly:

```python
            if (handoff["phase"] in {"owner_transferred", "runtime_registered", "continuation_dispatched"}
                    or evidence.get("target_runtime")):
                raise RuntimeError("handoff target owns runtime; release refused")
            source = json.loads(handoff["snapshot_json"])
            current = self.get_worker(worker_id)
            if current is None or self._snapshot(current) != {**source, "status": "handoff_pending"}:
                raise RuntimeError("handoff source runtime changed")
            evidence.update({"disposition": "released", "released_from_phase": handoff["phase"],
                             "released_reason": expected_pause_reason})
```

with:

```python
            if disposition not in {"released", "released_to_target"}:
                raise ValueError("handoff release disposition invalid")
            source = json.loads(handoff["snapshot_json"])
            target = evidence.get("target_runtime")
            # H6a: once the target runtime is registered the worker keeps it, as
            # complete_worker_handoff expects.
            expected = ({**source, **target, "status": "handoff_pending", "finished_at": None}
                        if isinstance(target, dict) else {**source, "status": "handoff_pending"})
            current = self.get_worker(worker_id)
            if current is None or self._snapshot(current) != expected:
                raise RuntimeError("handoff source runtime changed")
            evidence.update({"disposition": disposition, "released_from_phase": handoff["phase"],
                             "released_reason": expected_pause_reason})
```

3. In the same method's docstring, replace exactly:

```python
        Before the source stop (H5) release is refused once a target, a stop intent, or
        a stop proof was recorded. After the stop (H6/H7) it is refused once the target
        owns the runtime.
```

with:

```python
        Before the source stop (H5) release is refused once a target, a stop intent, or
        a stop proof was recorded. After the transfer (H6a) the target runtime is kept
        and the disposition is released_to_target.
```

4. In `effective_worker_tier`, a `released_to_target` row proves the adopted target runtime's tier just as a completed row does. Plain `released` rows stay skipped. Replace exactly:

```python
            "SELECT phase,evidence_json FROM worker_handoffs WHERE worker_id=? "
            "AND completed_at IS NOT NULL AND phase='completed' ORDER BY generation DESC LIMIT 1",
```

with:

```python
            "SELECT phase,evidence_json FROM worker_handoffs WHERE worker_id=? "
            "AND completed_at IS NOT NULL AND (phase='completed' OR (phase='released' "
            "AND json_extract(evidence_json, '$.disposition')='released_to_target')) "
            "ORDER BY generation DESC LIMIT 1",
```

5. Replace exactly `            if (row["phase"] != "completed" or not evidence.get("assignment_finished")` with:

```python
            if (row["phase"] not in {"completed", "released"} or not evidence.get("assignment_finished")
```

### Step 5: GREEN — `worker_handoff.py`

1. Replace exactly:

```python
    POST_STOP_RETRY_REASONS = frozenset({
        "opposite_capability_unavailable", "target_preflight_pending",
        "native_readonly_or_resume_flags_unsupported", "workspace_quiescence_unproved",
        "source_stop_unproved", "handoff_reconcile_owner_unproved",
    })
```

with:

```python
    POST_STOP_RETRY_REASONS = frozenset({
        "opposite_capability_unavailable", "target_preflight_pending",
        "native_readonly_or_resume_flags_unsupported", "workspace_quiescence_unproved",
        "source_stop_unproved", "handoff_reconcile_owner_unproved",
        # H6a: after the transfer, a resume, dispatch or finish proof is retried forward.
        "target_resume_session_already_exists", "target_resume_launch_failed",
        "target_resume_readiness_unproved", "target_resume_identity_unproved",
        "bootstrap_stop_unproved", "continuation_dispatch_unproved", "assignment_finish_unproved",
    })
```

2. Replace exactly:

```python
            try:
                self.registry.resume_worker_handoff(worker_id, generation, phase)
                if target is not None:
                    self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                        "target_client": target.client, "target_model": target.model,
                        "target_tier": target.effective_tier})
            except Exception:
                return self._status(worker_id)
            return self._reconcile_owned(worker_id)
```

with:

```python
            try:
                self.registry.resume_worker_handoff(worker_id, generation, phase)
                if target is not None:
                    self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                        "target_client": target.client, "target_model": target.model,
                        "target_tier": target.effective_tier})
            except Exception:
                return self._status(worker_id)
            cleanup = self._post_transfer_cleanup(worker_id, generation, phase, row["pause_reason"], evidence)
            try:
                if cleanup is None:
                    raise HandoffHold(row["pause_reason"])
                if cleanup:
                    self.registry.record_worker_handoff_evidence(worker_id, generation, phase, cleanup)
            except Exception:
                # The session or workspace cannot be settled yet: the hold stays as it was.
                self.registry.pause_worker_handoff(worker_id, generation, row["pause_reason"])
                return self._status(worker_id)
            return self._reconcile_owned(worker_id)

    def _post_transfer_cleanup(self, worker_id: str, generation: int, phase: str, reason: str,
                               evidence: dict):
        """H6a: settle a half-started resume, an undelivered dispatch or an unfinished finish.

        Runs with the hold lifted, so a durable stop intent is recorded before any signal
        (module contract: every external effect has a durable intent). Returns the evidence
        update to record before retrying ({} for none), or None when the session or
        workspace cannot be settled yet (the caller restores the hold).
        """
        if phase == "owner_transferred" and evidence.get("resume_intent"):
            name = evidence["resume_intent"]
            try:
                if not self.tools.tmux.has_session(name):
                    return {"resume_intent": False, "stale_resume_stop_intent": False}
                if self.runtime.inspect_resumed(name, evidence["target_client"], evidence["target_id"],
                                                not_before=evidence["resume_started_at"]):
                    # _reconcile_intent registers the resumed session and advances.
                    return {"stale_resume_stop_intent": False}
                stale = self.process.snapshot(name, evidence["scope"]["expected_worktree_path"],
                                              evidence["target_client"])
                self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                    "stale_resume_process": stale, "stale_resume_stop_intent": True})
                proof = self.process.stop_and_prove(stale)
            except Exception:
                return None
            if not proof.get("stopped"):
                return None
            return {"resume_intent": False, "stale_resume_stop_intent": False,
                    "stale_resume_stop_proof": proof}
        if phase == "runtime_registered" and reason == "continuation_dispatch_unproved":
            # tmux refused the keys, so no continuation was delivered and it may be replayed.
            return {"dispatch_intent": False}
        if phase == "continuation_dispatched" and reason == "assignment_finish_unproved":
            try:
                state = self._workspace_readback(evidence)
                if state.get("phase") == "transferred":
                    # H6b: the target may have committed since the grant, so the retry
                    # uses the after-target finish; _reconcile_intent then reads back the
                    # finished row and completes the handoff.
                    finished = self.workspace.finish_handoff_after_target(
                        {**evidence["scope"], "capability_token": evidence["capability_token"]},
                        **evidence["transport"])
                    if finished.get("protocol_version") != 1 or finished.get("phase") != "finished":
                        return None
            except Exception:
                return None
            return {"finish_intent": True}
        return {}

    def _release_to_target(self, worker_id: str, generation: int, phase: str, reason: str,
                           evidence: dict, refusal_key: str) -> dict:
        """H6a: forward is impossible after the transfer; preserve the work as the target runtime."""
        name = evidence.get("target_session") or f"ic-{worker_id}-h{generation}"
        try:
            self.registry.resume_worker_handoff(worker_id, generation, phase)
        except Exception as exc:
            return {**self._status(worker_id), refusal_key: str(exc) or "resume_failed"}
        try:
            delivered = (phase == "continuation_dispatched" or evidence.get("dispatch_accepted")
                         or evidence.get("dispatch_intent"))
            if not delivered and self.tools.tmux.has_session(name):
                session = self.process.snapshot(name, evidence["scope"]["expected_worktree_path"],
                                                evidence["target_client"])
                # Durable intent before the signal, as _stop_bootstrap does.
                self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                    "target_session_process": session, "target_session_stop_intent": True})
                proof = self.process.stop_and_prove(session)
                if not proof.get("stopped"):
                    raise HandoffHold("target_session_stop_unproved")
                self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                    "target_session_stop_intent": False, "target_session_stop_proof": proof})
            elif evidence.get("target_session_stop_intent"):
                # An earlier stop attempt's session is gone now; settle its intent.
                self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                    "target_session_stop_intent": False})
            current = self.registry.get_worker(worker_id)
            if current["native_session_id"] != evidence["target_id"]:
                # Same adoption _register_target performs after a successful resume.
                self.registry.replace_worker_runtime(
                    worker_id, generation, "owner_transferred", client=evidence["target_client"],
                    model=evidence["target_model"], native_session_id=evidence["target_id"],
                    tmux_session=name, machine=current["machine"])
            state = self._workspace_readback(evidence)
            if state.get("phase") != "finished":
                # H6b: accepts a HEAD the target fast-forwarded after the transfer.
                finished = self.workspace.finish_handoff_after_target(
                    {**evidence["scope"], "capability_token": evidence["capability_token"]},
                    **evidence["transport"])
                if finished.get("protocol_version") != 1 or finished.get("phase") != "finished":
                    raise HandoffHold("assignment_finish_unproved")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                "assignment_finished": True})
        except Exception as exc:
            self.registry.pause_worker_handoff(worker_id, generation, reason)
            return {**self._status(worker_id), refusal_key: str(exc) or "release_to_target_failed"}
        self.registry.pause_worker_handoff(worker_id, generation, reason)
        try:
            self.registry.release_worker_handoff(worker_id, generation, reason, after_stop=True,
                                                 disposition="released_to_target")
        except Exception as exc:
            return {**self._status(worker_id), refusal_key: str(exc) or "release_failed"}
        return {**self._status(worker_id), "preserve": True}
```

3. Replace exactly:

```python
            if phase in self.POST_TRANSFER_PHASES:
                return {**self._status(worker_id), refusal_key: "post_transfer_release_unavailable"}
```

with:

```python
            if phase in self.POST_TRANSFER_PHASES:
                if abort:
                    if not self._bootstrap_proven_gone(evidence):
                        return {**self._status(worker_id), refusal_key: "target_bootstrap_alive_or_unproved"}
                elif not self._stop_bootstrap(worker_id, generation, phase, reason, evidence):
                    return {**self._status(worker_id), refusal_key: "target_bootstrap_stop_unproved"}
                return self._release_to_target(worker_id, generation, phase, reason, evidence, refusal_key)
```

4. Durable stop intents self-heal after a crash between intent and proof, following the module contract: every effect has an intent, and an uncertain outcome is resolved by readback. In `_reconcile_intent(self, worker_id, generation, phase, evidence)`, whose locals these are, replace exactly `        if phase == "owner_transferred" and evidence.get("bootstrap_stop_intent"):` with:

```python
        if evidence.get("stale_resume_stop_intent"):
            proof = self.process.verify_stopped(evidence["stale_resume_process"])
            if not proof.get("stopped"):
                raise HandoffHold("stale_resume_stop_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                "stale_resume_stop_intent": False, "resume_intent": False, "stale_resume_stop_proof": proof})
            return self.reconcile(worker_id)
        if evidence.get("target_session_stop_intent"):
            proof = self.process.verify_stopped(evidence["target_session_process"])
            if not proof.get("stopped"):
                raise HandoffHold("target_session_stop_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                "target_session_stop_intent": False, "target_session_stop_proof": proof})
            return self.reconcile(worker_id)
        if phase == "owner_transferred" and evidence.get("bootstrap_stop_intent"):
```

The anchor line is unique in `worker_handoff.py` (currently line 646), and no earlier task changes it.

An abort whose readback shows the transfer landed is not changed here. As in Task 13, it returns `transfer_landed` with the row advanced and unpaused at `owner_transferred`, exactly like the automatic path. The next reconcile's `_resume_target` stops the bootstrap and moves forward. An abort on a row that is already post-transfer goes through `_release_to_target` (edit 3).

### Step 6: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_registry.py tests/test_worker_handoff.py tests/test_worker_handoff_process.py tests/test_worker_handoff_daemon.py`

Expected: all pass; record actual counts

### Step 7: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_handoff.py commander/src/ironclaude/worker_registry.py commander/tests/test_worker_handoff.py commander/tests/test_worker_handoff_registry.py`

Expected: exit 0

---

## Task 16: The daemon preserves a released post-stop worker once; a released event is not re-claimed; `abort_handoff` is reachable (H6 daemon, H5b, H7 MCP)

**Depends on:** Task 15

**Files:**
- `commander/src/ironclaude/main.py`
- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/tests/test_worker_handoff_daemon.py`
- `commander/tests/test_worker_handoff.py`

### Step 1: RED — daemon tests

In `commander/tests/test_worker_handoff_daemon.py`, replace exactly `def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):` with:

```python
def test_post_stop_release_routes_through_dead_session_preserve_path_once(tmp_path):
    daemon, _ = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    daemon.registry.claim_worker_handoff(worker, generation=1)
    daemon.registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    daemon.registry.pause_worker_handoff("w1", 1, "pm_readback_failed")
    _dead_worker_seams(daemon)
    facade = daemon._get_orchestrator.return_value

    def release(worker_id):
        daemon.registry.release_worker_handoff(worker_id, 1, "pm_readback_failed", after_stop=True)
        return {"status": "released", "worker_id": worker_id, "generation": 1, "phase": "released",
                "reason": "pm_readback_failed", "preserve": True}

    facade.retry_worker_handoff_preflight.side_effect = release
    daemon.check_workers()
    daemon._finalize_and_release_worker.assert_called_once_with("w1", reason="session ended", terminal=True)
    daemon._drive_finalization_recovery.assert_called_once()
    assert daemon.slack.post_message.call_count == 1
    assert "pm_readback_failed" in daemon.slack.post_message.call_args.args[0]
    assert daemon.registry.get_worker("w1")["status"] == "running"
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    assert daemon.brain.send_message.call_count == 1


def test_prestop_release_result_does_not_preserve_or_notify(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    daemon.registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    _dead_worker_seams(daemon)
    facade = daemon._get_orchestrator.return_value

    def release(worker_id):
        daemon.registry.release_worker_handoff(worker_id, 1, "native_quota_evidence_changed")
        return {"status": "released", "worker_id": worker_id, "generation": 1, "phase": "released",
                "reason": "native_quota_evidence_changed"}

    facade.retry_worker_handoff_preflight.side_effect = release
    daemon.check_workers()
    daemon._finalize_and_release_worker.assert_not_called()
    daemon.slack.post_message.assert_not_called()
    assert daemon.registry.get_worker("w1")["status"] == "running"


def test_uncertain_effect_pause_stays_with_one_notification(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    daemon.registry.pause_worker_handoff("w1", 1, "uncertain_external_effect")
    facade = daemon._get_orchestrator.return_value
    facade.retry_worker_handoff_preflight.return_value = {
        "status": "held", "phase": "claimed", "reason": "uncertain_external_effect"}
    daemon.check_workers()
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    assert "uncertain_external_effect" in daemon.slack.post_message.call_args.args[0]
    assert daemon.registry.get_worker("w1")["status"] == "handoff_pending"


def test_released_handoff_event_is_not_reclaimed_until_fresh_exhaustion(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    verdict = read_native_quota(path, client="claude", native_session_id=native_id,
                                workspace_path=str(tmp_path), generation="1")
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1,
                                         initial_evidence={"quota_verdict": verdict})
    daemon.registry.pause_worker_handoff("w1", 1, "source_process_changed_before_stop")
    daemon.registry.release_worker_handoff("w1", 1, "source_process_changed_before_stop")
    facade = daemon._get_orchestrator.return_value
    facade.handoff_worker.return_value = {"status": "rejected", "reason": "operator_paused_pm"}
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    facade.handoff_worker.assert_not_called()
    with path.open("a") as stream:
        stream.write(json.dumps(_claude_quota(native_id, tmp_path, event_uuid="fresh",
                                              timestamp="2026-10-05T12:10:00Z")) + "\n")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    packet = facade.handoff_worker.call_args.args[1]
    assert packet["event_key"] != verdict["event_key"]
    assert packet["generation"] == "2"


def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):
```

### Step 2: RED — MCP abort test

In `commander/tests/test_worker_handoff.py`, replace exactly `def test_uncertain_bootstrap_is_held_without_relaunch(tmp_path):` with:

```python
def test_abort_handoff_is_reachable_through_wrapped_recovery_tool(tmp_path):
    registry = _registry(tmp_path)
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=1)
    registry.pause_worker_handoff("w1", 1, "uncertain_external_effect")
    tools = OrchestratorTools.__new__(OrchestratorTools)
    tools.registry = registry
    tools.tmux = MagicMock()
    tools._ssh_manager = None
    tools._workspace_client = MagicMock()
    with pytest.raises(RuntimeError, match="active handoff blocks worker operation"):
        tools.recover_worker_integration("w1", "status")
    result = tools.recover_worker_integration("w1", "abort_handoff")
    assert result["status"] == "released", result
    assert registry.get_worker("w1")["status"] == "running"
    tools._workspace_client.release_handoff.assert_not_called()


def test_uncertain_bootstrap_is_held_without_relaunch(tmp_path):
```

### Step 3: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_daemon.py tests/test_worker_handoff.py -k "preserve or prestop_release_result or uncertain_effect_pause or not_reclaimed or abort_handoff_is_reachable"`

Expected: FAIL: test_post_stop_release_routes_through_dead_session_preserve_path_once (finalize never called), test_released_handoff_event_is_not_reclaimed_until_fresh_exhaustion (handoff_worker called for the consumed event), test_abort_handoff_is_reachable_through_wrapped_recovery_tool (active handoff blocks worker operation); the prestop-release and uncertain-notification tests pass as guards; record actual counts

### Step 4: GREEN — `main.py`

1. Released-event guard. Replace exactly `            result = tools.handoff_worker(worker_id, verdict)` with:

```python
            if latest is not None and latest["phase"] == "released":
                consumed = json.loads(latest["evidence_json"]).get("quota_verdict") or {}
                if consumed.get("event_key") == verdict.get("event_key"):
                    # A released handoff already consumed this exhaustion event; only a
                    # fresh exhaustion record starts the next generation.
                    return
            result = tools.handoff_worker(worker_id, verdict)
```

2. Preservation helper. Replace exactly `    def _finalize_and_release_worker(self, worker_id: str, reason: str, terminal: bool):` with:

```python
    def _preserve_released_handoff_worker(self, worker_id: str, result: dict) -> None:
        """H6: route a released post-stop handoff through the dead-session preserve path once."""
        outcome = self._finalize_and_release_worker(worker_id, reason="session ended", terminal=True)
        disposition = self._drive_finalization_recovery(worker_id, outcome, terminal=True)
        if worker_id in self._session_died_notified:
            return
        message = (f"Worker {worker_id} provider handoff could not continue after the source stopped "
                   f"({result.get('reason') or 'handoff_forward_impossible'}); the handoff hold was "
                   f"released and the work is preserved through the session-ended path "
                   f"(finalization disposition={disposition}).")
        self.slack.post_message(message)
        self.brain.send_message(message)
        self._session_died_notified.add(worker_id)

    def _finalize_and_release_worker(self, worker_id: str, reason: str, terminal: bool):
```

3. In `check_workers`, replace exactly:

```python
            source = json.loads(handoff["snapshot_json"])
            self._notify_worker_handoff(source, result)
```

with:

```python
            source = json.loads(handoff["snapshot_json"])
            if result.get("status") == "released" and result.get("preserve"):
                self._preserve_released_handoff_worker(handoff["worker_id"], result)
                continue
            self._notify_worker_handoff(source, result)
```

### Step 5: GREEN — `orchestrator_mcp.py`

1. Replace exactly:

```python
    def guarded(self, worker_id, *args, **kwargs):
        worker = self.registry.get_worker(worker_id)
```

with:

```python
    def guarded(self, worker_id, *args, **kwargs):
        action = kwargs.get("action", args[0] if args else None)
        if fn.__name__ == "recover_worker_integration" and action == "abort_handoff":
            # H7: the operator abort is the release path for an active handoff, so the
            # active-handoff guard below must not refuse it.
            return fn(self, worker_id, *args, **kwargs)
        worker = self.registry.get_worker(worker_id)
```

2. Replace exactly `        if action not in self._RECOVERY_ACTIONS:` with:

```python
        if action == "abort_handoff":
            from ironclaude.worker_handoff import WorkerHandoffController
            return WorkerHandoffController(self).abort_handoff(worker_id)
        if action not in self._RECOVERY_ACTIONS:
```

3. Replace exactly `        editable 'active', preserving work). Authority — repository, workspace_guid, owner_session_id,` with:

```python
        editable 'active', preserving work), or 'abort_handoff' (release a paused provider
        handoff once any recorded target bootstrap is proved gone by PID plus start time).
        Authority — repository, workspace_guid, owner_session_id,
```

4. Replace exactly `        row to editable 'active', preserving work). Authority is derived from the persisted worker` with:

```python
        row to editable 'active', preserving work), or 'abort_handoff' (release a paused provider
        handoff after an uncertain external effect, refused while a recorded target bootstrap is
        alive or unproved). Authority is derived from the persisted worker
```

### Step 6: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff_daemon.py tests/test_worker_handoff.py tests/test_orchestrator_mcp.py tests/test_worker_handoff_registry.py`

Expected: all pass; record actual counts

### Step 7: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_handoff_daemon.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 17: Full verification, dist rebuild, README re-stage, verification record (H16)

**Depends on:** Task 3, Task 16. Every other task is reached transitively.

**Files:**
- `docs/reviews/2026-10-09-handoff-recovery-fixes-verification.md`
- `worker/mcp-servers/workspace-manager/dist/cli.js`
- `worker/mcp-servers/workspace-manager/dist/index.js`
- `worker/mcp-servers/workspace-manager/dist/hook-intent.js`
- `README.md`

No new tests. This task runs every existing check in full and records the actual results.

### Step 1: Log directory

Command: `mkdir -p /private/tmp/ironclaude-l-h1-verify`

Expected: exit 0

### Step 2: Every hook suite, one log each

Command: `cd /Users/roberthyatt/Code/ironclaude && for s in worker/hooks/test-*.sh worker/hooks/tests/test-*.sh commander/hooks/tests/test-*.sh; do bash "$s" > "/private/tmp/ironclaude-l-h1-verify/hook-$(basename "$s").log" 2>&1; echo "$s exit=$?"; done`

Expected: every suite prints exit=0 including test-gbtw-continuation-judge.sh and test-last-assistant-text.sh; record each line; any non-zero suite is recorded with its log tail and resolved before the final review

### Step 3: Full workspace-manager vitest

Command (Bash timeout 600000): `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/vitest run --root /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager > /private/tmp/ironclaude-l-h1-verify/vitest-full.log 2>&1; echo "exit=$?"`

Expected: Tests line shows 0 failed; the known vitest-worker 'Timeout calling "onTaskUpdate"' error during integration-core.test.ts may still appear as 'Errors 1 error' and make the exit status 1 (backlog, not a regression, recorded separately); anything else fails the step; record the Test Files, Tests and Errors lines and the exit status

### Step 4: `tsc`

Command: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/node_modules/.bin/tsc --noEmit -p /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/tsconfig.json > /private/tmp/ironclaude-l-h1-verify/tsc.log 2>&1; echo "exit=$?"`

Expected: exit=0 and empty log

### Step 5: Rebuild the dist bundles

Command: `npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager run build > /private/tmp/ironclaude-l-h1-verify/build.log 2>&1; echo "exit=$?"`

Expected: exit=0

### Step 6: Prove that the bundles carry the new commands

Command: `grep -c "release-handoff" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js`

Expected: at least 1

Command: `grep -c "reap-stale-mutations" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js`

Expected: at least 1

Command: `grep -c "begin-handoff" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js`

Expected: at least 1

Command: `grep -c "finish-handoff-after-target" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js`

Expected: at least 1

Command: `grep -c "lstart=" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js`

Expected: at least 1

The last check shows that the server bundle carries the PID-plus-start-time claim identity.

### Step 7: Full Commander pytest

Command (Bash timeout 600000; this took about 425 s on 2026-10-06): `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-l-h1-verify/pytest-full.log 2>&1; echo "exit=$?"`

Expected: the only failure, if any, is the known pre-existing tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd (not a regression, tracked as L-T2); record the actual summary line; any other failure is resolved before the final review

### Step 8: Write the verification record

Write `/Users/roberthyatt/Code/ironclaude/docs/reviews/2026-10-09-handoff-recovery-fixes-verification.md` with:
- every command from Steps 2–7, verbatim;
- each exit status;
- the summary lines from each log, quoted;
- the hook suite list with exits;
- the separately recorded `onTaskUpdate` note, if it occurred;
- the known pytest failure, if it occurred;
- the dist grep counts;
- a requirement table mapping H1–H15, H5a, H5b, H6a, H6b, H7a, H7b and the amended H6 and H9 rules to the Task and test names that cover each, plus the accepted limitations L1, L2 and L3.

State that H16's final Fable review follows this record and gates the commit.

### Step 9: Stage the record and the bundles

Command: `git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-09-handoff-recovery-fixes-verification.md`

Expected: exit 0

Command: `git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js`

Expected: exit 0

### Step 10: Re-stage README.md (only the handoff hunk remains)

Command: `git -C /Users/roberthyatt/Code/ironclaude diff --cached -- README.md`

Expected: empty output (the episodic section was committed in f57d69c); if anything prints, stop and report, do not stage

Command: `git -C /Users/roberthyatt/Code/ironclaude diff -- README.md`

Expected: only this effort's handoff hunk, containing 'Managed-worker capacity handoff'

Command: `git -C /Users/roberthyatt/Code/ironclaude add README.md`

Expected: exit 0

Command: `git -C /Users/roberthyatt/Code/ironclaude diff --cached -- README.md | grep -c "Managed-worker capacity handoff"`

Expected: at least 1

### Step 11: Final staged-set check

Command: `git -C /Users/roberthyatt/Code/ironclaude status --short`

Expected: every file this loop touched shows as staged with no unstaged remainder; record output; no commit (final Fable review gates it)
