# src/ic/main.py
"""Main entry point and loop for the IronClaude daemon."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import psutil
from datetime import datetime, timezone
from pathlib import Path

from ironclaude.config import load_config, load_machines_config, DEFAULTS, make_opus_command, effort_for_tier
from ironclaude.provider_config import _semantic_tier
from ironclaude.auth_relay import AuthRelay
from ironclaude.slack_interface import SlackBot, DIRECTIVE_STATUS_EMOJI, parse_reply_to_marker
from ironclaude.slack_commands import SlackSocketHandler, format_help_text
from ironclaude.db import (
    DIRECT_REPLY_FALLBACK_REASON,
    init_db,
    persist_operator_message_acknowledgement,
)
from ironclaude.tmux_manager import (
    PromptSignal,
    TmuxManager,
    _strip_ansi,
    detect_ask_user_menu,
    validate_prompt_candidate,
)
from ironclaude.brain_client import BrainClient, _NARRATION_PREFIX
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.protocol import read_pending_decisions, read_task_ledger, write_decision
from ironclaude.notifications import (
    format_worker_spawned, format_worker_completed, format_worker_failed,
    format_worker_session_ended_preserved,
    format_worker_idle, format_worker_idle_ttl_reaped, format_worker_checkin,
    format_heartbeat, format_brain_restarted, format_brain_compacted, format_brain_circuit_breaker,
    format_brain_capability_blocked,
    format_objective_received,
    format_task_progress, format_plan_ready, format_blocked,
    format_worker_heartbeat_stuck_slack,
    format_orphaned_orphans,
    _escape_mrkdwn,
)
from ironclaude.grader import LocalGrader, truncate_middle
from ironclaude.fable_availability import (
    resolve_worker_type as _resolve_fable_worker_type,
    resolve_advisor_model as _resolve_fable_advisor_model,
    is_fable_unavailable as _is_fable_unavailable,
)
from ironclaude.orchestrator_mcp import ensure_worker_trusted, WORKER_COMMANDS
from ironclaude.communication_profiles import (
    CommunicationProfileError,
    PROFILE_READY_MARKER,
    apply_communication_profile,
    skill_invocation,
)
from ironclaude.signal_forensics import _logged_kill
from ironclaude.plugins import PluginRegistry, discover_plugins


def _render_brain_system_prompt(prompt_path: str, config: dict) -> str:
    """Load, substitute, and profile the Brain prompt without an unprofiled fallback."""
    with open(prompt_path) as prompt_file:
        substituted = _substitute_prompt(prompt_file.read(), config)
    return apply_communication_profile("commander_brain", substituted)

logger = logging.getLogger("ironclaude")

_BRAIN_MSG_VALIDATION_SYSTEM = (
    "You validate Brain messages before they are posted to Slack.\n\n"
    "A valid Brain message must have BOTH of the following:\n"
    "1. A directive reference — any of: #N, dN, or 'directive N' (e.g. #1083, d1076, directive 42)\n"
    "2. A reason clause — text explaining what the Brain is reporting (status, update, result, error, etc.)\n\n"
    'Respond ONLY with valid JSON: {"valid": true} or {"valid": false, "reason": "..."}'
)
_BRAIN_MSG_SCHEMA = {
    "type": "object",
    "properties": {"valid": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["valid"],
}
_DIRECTIVE_REF_RE = re.compile(r'(?:#\d+|d\d+|directive\s+\d+)', re.IGNORECASE)
_BLOCKED_NO_DIRECTIVE = "no_directive_ref"
_BRAIN_POST_CHUNK = 39000   # keep each *Brain:* post under Slack's ~40000-char message limit

# --- Awaiting-operator surfacing ---------------------------------------------
# Cheap pre-check: only run the grader classifier on messages that look like the
# Brain is holding for the operator (avoids a grader call on every dropped message).
_AWAITING_PHRASE_RE = re.compile(
    r"holding|awaiting|waiting for|waiting on|slack response|"
    r"your (?:reply|response|input|decision)|pinned decision",
    re.IGNORECASE,
)
_NOT_AWAITING_RE = re.compile(
    r"(?:holding|waiting)\s+(?:for|on)\s+(?:the\s+)?"
    r"(?:subagent|sub-agent|worker|reviewer|fable|blind\s+review|tier[- ]?up|advisor)\b",
    re.IGNORECASE,
)
# R6: extract the directive id a Brain status refers to — `dN` (word-bounded) or
# `#N`. Used to de-duplicate operator_wait alerts per directive rather than per
# paraphrased question.
_DIRECTIVE_ID_RE = re.compile(r"\bd(\d+)\b|#(\d+)")


def _format_mem_line() -> str:
    """One-line memory status for the heartbeat: available / swap / top-3 RSS."""
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                mi = p.info["memory_info"]
                if mi is None:  # process_iter stores None for unreadable procs (macOS AccessDenied)
                    continue
                procs.append((mi.rss, p.info["name"]))
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
                pass
        procs.sort(reverse=True)
        top = ", ".join(f"{n}={rss/(1024**3):.1f}G" for rss, n in procs[:3])
        return (f"mem: {vm.available/(1024**3):.1f}G free / "
                f"swap {sw.used/(1024**3):.1f}G / top {top}")
    except Exception as exc:  # noqa: BLE001 - telemetry, never fatal
        return f"mem: unavailable ({exc})"


def _extract_directive_id(text: str) -> int | None:
    """Return the first directive id referenced in text (dN or #N), else None."""
    m = _DIRECTIVE_ID_RE.search(text)
    if not m:
        return None
    return int(m.group(1) or m.group(2))
_AWAITING_OP_SYSTEM = (
    "You classify a Brain status message into exactly one of three categories via the "
    "`waiting_on` field:\n"
    "- \"operator\": the Brain is waiting on THE HUMAN OPERATOR to make a decision or "
    "judgment call before work can continue.\n"
    "- \"brain\": the Brain (or a worker it orchestrates) is waiting on a BRAIN-SIDE "
    "action or approval — e.g. the Brain approving/rejecting a worker's plan or "
    "execution-mode menu, or a worker holding for the Brain's approve/reject decision. "
    "The Brain itself is the actor, not the human.\n"
    "- \"neither\": the message merely narrates an autonomous system condition that "
    "resolves on its own (tests finishing, a worker completing, a build, a timer, a "
    "subagent/review verdict).\n\n"
    "Examples waiting_on=operator: \"holding for your approval on the migration\", "
    "\"waiting on your decision: ship now or wait for review?\", \"pinned decision needed from you\".\n"
    "Examples waiting_on=brain: \"waiting for approval of the execution mode menu\", "
    "\"holding to approve worker d5's plan\", \"worker is waiting for me to approve its menu\".\n"
    "Examples waiting_on=neither: \"waiting for the heartbeat labels to become idle\", "
    "\"worker is waiting on tests to pass\", \"holding until the build finishes\", "
    "\"holding for the Fable review result\", \"waiting on subagent verdict\".\n\n"
    "For operator or brain, extract the worker id it is about (e.g. d1267; use null if "
    "it is the Brain itself) and a short paraphrase of what it is waiting for.\n\n"
    "Respond ONLY with valid JSON: {\"waiting_on\": \"operator\"|\"brain\"|\"neither\", "
    "\"worker_id\": \"...\"|null, \"question\": \"...\"|null}"
)
_AWAITING_OP_SCHEMA = {
    "type": "object",
    "properties": {
        "waiting_on": {"type": "string", "enum": ["operator", "brain", "neither"]},
        "worker_id": {"type": ["string", "null"]},
        "question": {"type": ["string", "null"]},
    },
    "required": ["waiting_on"],
}
_OPERATOR_WAIT_TTL_SECONDS = 600    # backstop: drop a wait the Brain stopped re-affirming
_OPERATOR_WAIT_MAX = 32            # bound the in-memory map

_PROMPT_WAITING_SYSTEM = (
    "Extract only a current unresolved worker question, approval, or authority request "
    "from the final terminal interaction block. Historical questions followed by an "
    "answer, progress, command/test output, completion, or a newer interaction are not "
    "current. Quoted questions in logs, plans, or reviews are not prompts. Return exact "
    "source text without paraphrase. If no current prompt exists, use kind=none.\n\n"
    "Return kind, the exact contiguous interaction_block, exact question, ordered option "
    "value/label pairs, and exact authority_text."
)
_PROMPT_WAITING_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["none", "question", "approval", "authority"]},
        "interaction_block": {"type": ["string", "null"]},
        "question": {"type": ["string", "null"]},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["value", "label"],
            },
        },
        "authority_text": {"type": ["string", "null"]},
    },
    "required": ["kind", "interaction_block", "question", "options", "authority_text"],
}

PROMPT_WAITING_CACHE_TTL = 120
PROMPT_WAITING_CACHE_MAX = 512
PROMPT_CAPTURE_LINES = 80
PROMPT_CAPTURE_CHARS = 8192


@dataclass(frozen=True)
class PromptDetection:
    signal: PromptSignal | None
    conclusive: bool

    @property
    def waiting(self) -> bool:
        return self.signal is not None
# Canonical UUID: 8-4-4-4-12 hex groups.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_SESSION_SWEEP_MAX_AGE_HOURS = 24


def _scan_session_id_files(claude_dir):
    """Partition ``claude_dir/ironclaude-session-<pid>.id`` files into
    ``(live_uuids, dead_files)``. A file whose pid is alive (``os.kill(pid, 0)``
    succeeds, or raises ``PermissionError`` — the process exists under another uid)
    contributes its UUID to the live set and is kept; a file whose pid is dead
    (``ProcessLookupError``) is returned for removal. Mirrors the ``kill -0`` liveness
    precedent in session-init.sh:288-302. Unix-only (``os.kill``)."""
    live_uuids: set[str] = set()
    dead_files: list[Path] = []
    try:
        candidates = list(claude_dir.glob("ironclaude-session-*.id"))
    except OSError:
        return live_uuids, dead_files
    for f in candidates:
        pid_str = f.name[len("ironclaude-session-"):-len(".id")]
        try:
            pid = int(pid_str)
        except ValueError:
            continue  # malformed name — leave it alone
        alive = True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True  # exists under another uid — fail-safe: protect
        except OSError:
            alive = True  # unknown error — fail-safe: protect
        if alive:
            try:
                uuid = f.read_text().strip()
            except OSError:
                continue
            if _UUID_RE.match(uuid):
                live_uuids.add(uuid)
        else:
            dead_files.append(f)
    return live_uuids, dead_files


def _sweep_stale_sessions(db_path, live_uuids, max_age_hours):
    """Delete ``idle``+``undecided`` sessions rows older than ``max_age_hours`` whose
    UUID is not in ``live_uuids``. Returns the count deleted. Filtering the live set in
    Python avoids the empty ``NOT IN ()`` SQL hazard."""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        cutoff = f"-{int(max_age_hours)} hours"
        rows = conn.execute(
            "SELECT terminal_session FROM sessions "
            "WHERE workflow_stage='idle' AND professional_mode='undecided' "
            "AND updated_at < datetime('now', ?)",
            (cutoff,),
        ).fetchall()
        stale = [(r[0],) for r in rows if r[0] not in live_uuids]
        if stale:
            conn.executemany("DELETE FROM sessions WHERE terminal_session=?", stale)
            conn.commit()
        return len(stale)
    finally:
        conn.close()


# --- Managed-worktree reaper --------------------------------------------------
# Releases LEAKED managed worktrees that dispatched workers left behind (finished
# workers whose workspace assignment was never released, or an assignment that
# never made it through `bind` and so carries no owner at all). Two separate
# SQLite databases are involved — the commander `workers` table and the
# workspace-manager `assignments` table — so the join happens in Python, never
# via ATTACH/cross-database SQL (mirrors the Python-side filtering already used
# by `_sweep_stale_sessions`).
#
# The reaper NEVER integrates and NEVER pushes. It only ever calls the
# workspace-manager client's `cleanup`, `abandon(mode='rescue')`, or `sync` —
# the same no-push transport every other release path in this codebase uses.

_WORKTREE_REAP_TTL_HOURS = 24
# A liveness signal independent of the TTL: an assignment row touched this
# recently is presumed to be under active use (bind/sync/finalize in flight)
# even if it would otherwise look TTL-eligible.
_ASSIGNMENT_RECENT_ACTIVITY_MINUTES = 30

_WORKTREE_CLEANUP_STATUSES = frozenset({"integrated", "abandoned"})
_WORKTREE_ABANDON_STATUSES = frozenset({"active", "ready_for_integration", "materialized"})


def _workspace_manager_db_path() -> str:
    """Resolve the workspace-manager SQLite path the same way the CLI does."""
    return os.environ.get("WORKSPACE_MANAGER_DB_PATH") or os.path.expanduser(
        "~/.claude/ironclaude-workspaces.db"
    )


def _load_workers_by_workspace_guid(commander_conn) -> dict[str, dict]:
    """Return {workspace_guid: worker_row} for every commander worker that has one.

    A commander `workers` row is only ever INSERTed after a successful `bind`
    (see orchestrator_mcp.py's spawn flow) — so every row returned here is
    guaranteed to correspond to an assignment with a bound, non-null
    owner_session_id. Pre-bind leaks (a worker that died before `bind`
    succeeded) never reach the commander `workers` table at all and so cannot
    appear here; they are found directly in the workspace-manager `assignments`
    table by `_find_leaked_worktrees` instead.
    """
    rows = commander_conn.execute(
        "SELECT * FROM workers WHERE workspace_guid IS NOT NULL AND workspace_guid != ''"
    ).fetchall()
    return {row["workspace_guid"]: dict(row) for row in rows}


def _worker_is_live(worker: dict | None, tmux) -> bool:
    """True if a matched commander worker is still running (status or tmux)."""
    if worker is None:
        return False
    if worker.get("status") == "running":
        return True
    tmux_session = worker.get("tmux_session")
    if tmux_session:
        return bool(tmux.has_session(tmux_session))
    return False


def _assignment_recently_active(assignment: dict, now: float, recent_minutes: int) -> bool:
    updated_at = assignment.get("updated_at")
    if not updated_at:
        return False
    try:
        updated_ts = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except (TypeError, ValueError):
        # Unparseable timestamp — fail safe toward "recent" (protect).
        return True
    return (now - updated_ts) < (recent_minutes * 60)


def _has_push_pending(disposition_json: str | None) -> bool:
    """True when the row still owes a push (mirrors TS decodePushDisposition's phase set)."""
    if not disposition_json:
        return False
    try:
        parsed = json.loads(disposition_json)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get("phase") in (
        "push-pending", "push-succeeded", "push-failed"
    )


def _is_protected(
    assignment: dict,
    worker: dict | None,
    tmux,
    locked_workspace_guids: set[str],
    now: float,
    recent_minutes: int = _ASSIGNMENT_RECENT_ACTIVITY_MINUTES,
) -> bool:
    """Conjunction of every liveness protect-condition. Errs toward PROTECT:
    any raised exception, or any single true condition, blocks the reap."""
    try:
        if _worker_is_live(worker, tmux):
            return True
        if _assignment_recently_active(assignment, now, recent_minutes):
            return True
        if assignment.get("workspace_guid") in locked_workspace_guids:
            return True
        if _has_push_pending(assignment.get("disposition")):
            return True
    except Exception as exc:  # noqa: BLE001 - fail-safe: any uncertainty protects
        logger.warning(
            "Worktree reaper: liveness check raised for %s — protecting: %s",
            assignment.get("workspace_guid"), exc,
        )
        return True
    return False


def _find_leaked_worktrees(
    commander_conn, workers_by_guid: dict[str, dict], ws_conn, ttl_hours: float,
) -> list[tuple[dict, dict | None]]:
    """Return [(assignment, worker_or_None), ...] candidates past their TTL.

    Three candidate classes, each computed in SQL against its own DB:
      1. finished commander workers (status completed/failed/killed, finished_at
         past TTL) joined to their assignment by workspace_guid — always owned.
      2. ownerless `active` assignments (owner_session_id empty) whose row is
         stale past TTL — a worker died between materialize and bind.
      3. `reserved` assignments stale past TTL — a worker died before ever
         materializing a worktree.
    Classes 2 and 3 never join to a commander worker row (register_worker only
    ever runs after a successful bind), so `worker` is None for them.
    """
    cutoff = f"-{int(ttl_hours)} hours"
    candidates: list[tuple[dict, dict | None]] = []

    finished_workers = commander_conn.execute(
        "SELECT * FROM workers WHERE status IN ('completed','failed','killed') "
        "AND finished_at IS NOT NULL AND finished_at < datetime('now', ?) "
        "AND workspace_guid IS NOT NULL AND workspace_guid != ''",
        (cutoff,),
    ).fetchall()
    for row in finished_workers:
        worker = dict(row)
        assignment_row = ws_conn.execute(
            "SELECT * FROM assignments WHERE workspace_guid = ? AND lifecycle_status != 'cleaned'",
            (worker["workspace_guid"],),
        ).fetchone()
        if assignment_row is None:
            continue
        candidates.append((dict(assignment_row), worker))

    ownerless_active = ws_conn.execute(
        "SELECT * FROM assignments WHERE lifecycle_status = 'active' "
        "AND (owner_session_id IS NULL OR owner_session_id = '') "
        "AND updated_at < datetime('now', ?)",
        (cutoff,),
    ).fetchall()
    for row in ownerless_active:
        assignment = dict(row)
        if assignment["workspace_guid"] not in workers_by_guid:
            candidates.append((assignment, None))

    reserved_orphans = ws_conn.execute(
        "SELECT * FROM assignments WHERE lifecycle_status = 'reserved' "
        "AND updated_at < datetime('now', ?)",
        (cutoff,),
    ).fetchall()
    for row in reserved_orphans:
        assignment = dict(row)
        if assignment["workspace_guid"] not in workers_by_guid:
            candidates.append((assignment, None))

    return candidates


def _release_leaked_assignment(
    workspace_client, assignment: dict, worker: dict, transport: dict,
) -> str:
    """Release one owned assignment via cleanup or abandon(rescue). Never
    integrates, never pushes. Returns the action taken (for logging/tests)."""
    payload = {
        "repository_path": worker["repo"],
        "workspace_guid": assignment["workspace_guid"],
        "owner_session_id": assignment["owner_session_id"],
    }
    if assignment["lifecycle_status"] in _WORKTREE_CLEANUP_STATUSES:
        workspace_client.cleanup(payload, **transport)
        return "cleanup"
    workspace_client.abandon({**payload, "mode": "rescue"}, **transport)
    return "abandon_rescue"


def _reap_ownerless_assignment(workspace_client, assignment: dict, transport: dict) -> dict:
    """Release one ownerless assignment via the owner-free `reap` CLI verb.

    `reap` does not require a stored `owner_session_id` match, so it can
    release a row that never completed `bind`. `repository_path` is derived
    from the assignment's `worktree_path` by splitting on the managed-worktree
    marker; raises ValueError if that marker is absent (an unmanaged or
    malformed `worktree_path`), letting the caller fall back to surfacing."""
    worktree_path = assignment.get("worktree_path") or ""
    marker = "/.ironclaude/worktrees/"
    if marker not in worktree_path:
        raise ValueError(f"cannot derive repository_path from worktree_path: {worktree_path!r}")
    repository_path = worktree_path.split(marker)[0]
    return workspace_client.reap(
        {"repository_path": repository_path, "workspace_guid": assignment["workspace_guid"]},
        **transport,
    )


def _live_worker_worktree_paths(commander_conn, tmux) -> list[str]:
    """Worktree paths of currently-live workers, for reaper protectedPaths."""
    commander_conn.row_factory = sqlite3.Row
    paths: list[str] = []
    for row in commander_conn.execute(
        "SELECT status, tmux_session, workspace_path FROM workers WHERE workspace_path IS NOT NULL"
    ).fetchall():
        worker = {"status": row["status"], "tmux_session": row["tmux_session"]}
        if _worker_is_live(worker, tmux) and row["workspace_path"]:
            paths.append(row["workspace_path"])
    return paths


def _managed_repositories(commander_conn, *, workspace_db_path: str | None = None) -> list[tuple[str, dict | None]]:
    """Distinct (repo_path, representative_worker_or_None) the daemon manages.
    workers rows give every repo ever spawned into (local or remote, keyed by
    (repo, machine)); the local workspace-manager DB adds repos the operator's
    own sessions used. None worker = local host."""
    seen: dict[tuple[str, str], dict | None] = {}
    commander_conn.row_factory = sqlite3.Row
    for row in commander_conn.execute(
        "SELECT * FROM workers WHERE repo IS NOT NULL AND repo != '' ORDER BY spawned_at DESC"
    ).fetchall():
        worker = dict(row)
        key = (worker["repo"], worker.get("machine") or "")
        seen.setdefault(key, worker if key[1] else None)
    try:
        ws = sqlite3.connect(workspace_db_path or _workspace_manager_db_path(), timeout=10)
        try:
            marker = "/.ironclaude/worktrees/"
            for (wt_path,) in ws.execute("SELECT worktree_path FROM assignments").fetchall():
                if wt_path and marker in wt_path:
                    seen.setdefault((wt_path.split(marker)[0], ""), None)
        finally:
            ws.close()
    except Exception as exc:
        logger.warning("Orphan reaper: could not read workspace-manager DB: %s", exc)
    return [(repo, worker) for (repo, _machine), worker in seen.items()]


def _reap_row_less_orphans(commander_conn, workspace_client, tmux, *,
                           workspace_db_path: str | None = None, resolve_transport=None,
                           ttl_hours: float = _WORKTREE_REAP_TTL_HOURS) -> dict:
    """Step-7 sweep. Per-repo try/except so one failing/unreachable repo never
    aborts the rest. Returns counts + preserved-unmerged names ('repo:branch')
    and per-orphan detail dicts (preserved_detail, muted entries excluded,
    each tagged with repository_path)."""
    if resolve_transport is None:
        resolve_transport = lambda _worker: {}  # noqa: E731
    protected = _live_worker_worktree_paths(commander_conn, tmux)
    summary = {"reaped": 0, "preservedDirty": 0, "preservedUnmerged": 0,
               "skippedLive": 0, "skippedYoung": 0, "errors": 0, "repo_failures": 0,
               "reapedWorktreeOnly": 0}
    preserved_unmerged: list[str] = []
    preserved_detail: list[dict] = []
    for repo_path, worker in _managed_repositories(commander_conn, workspace_db_path=workspace_db_path):
        transport = resolve_transport(worker) if worker is not None else {}
        try:
            res = workspace_client.reap_orphans(
                {"repository_path": repo_path, "protected_paths": protected, "ttl_hours": ttl_hours},
                **transport,
            )
        except Exception as exc:
            summary["repo_failures"] += 1
            logger.warning("Orphan reaper: %s failed: %s", repo_path, exc)
            continue
        for key in ("reaped", "preservedDirty", "preservedUnmerged", "skippedLive", "skippedYoung", "errors", "reapedWorktreeOnly"):
            summary[key] += len(res.get(key, []))
        names = list(res.get("preservedUnmerged", []))
        preserved_unmerged.extend(f"{repo_path}:{n}" for n in names)
        if "preservedDetail" in res:
            for detail in res.get("preservedDetail") or []:
                if detail.get("muted"):
                    continue
                tagged = dict(detail)
                tagged["repository_path"] = repo_path
                preserved_detail.append(tagged)
        else:
            # Back-compat: an older/stubbed reap_orphans response that only
            # carries preservedUnmerged branch names. Synthesize a minimal
            # detail per name so downstream count/surface logic has one
            # shape; the id mirrors the historical "repo:branch" string.
            for n in names:
                preserved_detail.append({
                    "id": f"{repo_path}:{n}", "guid": n, "branch": n,
                    "category": "genuinely-unmerged", "tip": "",
                    "worktreePresent": True, "evidence": "", "muted": False,
                    "repository_path": repo_path,
                })
        if res.get("reaped") or names or res.get("errors"):
            logger.info("Orphan reaper: %s -> %s", repo_path,
                        {k: len(res.get(k, [])) for k in ("reaped", "preservedDirty", "preservedUnmerged", "skippedLive", "skippedYoung", "errors", "reapedWorktreeOnly")})
            logger.debug("Orphan reaper: %s reaped=%s preservedUnmerged=%s preservedDirty=%s errors=%s",
                         repo_path, res.get("reaped"), names, res.get("preservedDirty"), res.get("errors"))
    summary["preserved_unmerged_names"] = preserved_unmerged
    summary["preserved_detail"] = preserved_detail
    return summary


def _reap_leaked_worktrees(
    commander_conn,
    workspace_client,
    tmux,
    *,
    workspace_db_path: str | None = None,
    resolve_transport=None,
    ttl_hours: float = _WORKTREE_REAP_TTL_HOURS,
    push_pending_alerted: set[str] | None = None,
) -> dict:
    """Periodic sweep: release LEAKED managed worktrees. Every maintenance pass
    re-evaluates every TTL-eligible row (no "seen" bookkeeping), so a backlog of
    pre-existing leaks from before this reaper existed is swept the same way as
    a freshly-leaked one — the TTL + liveness gate makes that safe.

    Scope is hard-limited to worker-owned assignments: an assignment whose
    owner_session_id equals its own workspace_guid is the operator's own
    primary-checkout session (workspace-manager mints workspace_guid ==
    owner_session_id for that case) and is never touched here.
    """
    counts = {"released": 0, "surfaced": 0, "protected": 0, "errors": 0, "push_pending": 0}
    if push_pending_alerted is None:
        push_pending_alerted = set()
    if resolve_transport is None:
        resolve_transport = lambda _worker: {}  # noqa: E731 - trivial local default
    seen_push_pending: set[str] = set()
    now = time.time()
    commander_conn.row_factory = sqlite3.Row
    try:
        workers_by_guid = _load_workers_by_workspace_guid(commander_conn)
    except Exception as exc:
        logger.warning("Worktree reaper: could not read commander workers: %s", exc)
        return counts

    db_path = workspace_db_path or _workspace_manager_db_path()
    try:
        ws_conn = sqlite3.connect(db_path, timeout=10)
        ws_conn.row_factory = sqlite3.Row
    except Exception as exc:
        logger.warning("Worktree reaper: could not open workspace-manager DB: %s", exc)
        return counts

    try:
        try:
            lock_rows = ws_conn.execute(
                "SELECT repository_identity, workspace_guid FROM integration_locks"
            ).fetchall()
        except Exception as exc:
            logger.warning("Worktree reaper: could not read workspace-manager state: %s", exc)
            return counts

        # A stale integration lock is surfaced (logged) — never force-deleted.
        for repo_identity, guid in lock_rows:
            logger.warning(
                "Worktree reaper: integration_locks row held for repo=%s workspace=%s — "
                "surfaced, not force-deleted", repo_identity, guid,
            )
        locked_workspace_guids = {row[1] for row in lock_rows}

        try:
            candidates = _find_leaked_worktrees(commander_conn, workers_by_guid, ws_conn, ttl_hours)
        except Exception as exc:
            logger.warning("Worktree reaper: candidate scan failed: %s", exc)
            return counts
    finally:
        ws_conn.close()

    for assignment, worker in candidates:
        owner = assignment.get("owner_session_id")
        guid = assignment.get("workspace_guid")

        # Scope: never the operator's own primary-checkout row.
        if owner and owner == guid:
            continue

        if _is_protected(
            assignment, worker, tmux, locked_workspace_guids, now,
        ):
            if _has_push_pending(assignment.get("disposition")):
                counts["push_pending"] += 1
                seen_push_pending.add(guid)
                if guid not in push_pending_alerted:
                    logger.warning(
                        "Worktree reaper: workspace=%s repo=%s is integrated with a pending "
                        "push — preserved, not reaped; complete the push (e.g. /push) to release it",
                        guid, assignment.get("repository_identity"),
                    )
                    push_pending_alerted.add(guid)
            else:
                counts["protected"] += 1
            continue

        if not owner or worker is None:
            # Structural gap: cleanup/abandon both require an exact, non-null
            # owner_session_id match at the workspace-manager CLI boundary. A
            # worker that died before `bind` succeeded leaves a permanently
            # ownerless assignment (reserved, materialized, or active) that
            # cleanup/abandon cannot release. Release it via the owner-free
            # `reap` verb instead; fall back to surfacing if repository_path
            # cannot be derived from worktree_path, or the reap call fails.
            try:
                _reap_ownerless_assignment(workspace_client, assignment, {})
                counts["released"] += 1
            except Exception:
                logger.warning(
                    "Worktree reaper: leaked ownerless assignment workspace=%s repo=%s "
                    "status=%s — surfaced, cannot release without a bound owner_session_id",
                    guid, assignment.get("repository_identity"), assignment.get("lifecycle_status"),
                )
                counts["surfaced"] += 1
            continue

        try:
            action = _release_leaked_assignment(
                workspace_client, assignment, worker, resolve_transport(worker),
            )
            logger.info(
                "Worktree reaper: released workspace=%s worker=%s via %s",
                guid, worker.get("id"), action,
            )
            counts["released"] += 1
        except Exception as exc:
            logger.warning(
                "Worktree reaper: release failed for workspace=%s worker=%s: %s",
                guid, worker.get("id"), exc,
            )
            counts["errors"] += 1

    # Re-arm the one-time WARNING: drop guids no longer push-pending this sweep so a
    # resolved-then-reused workspace_guid's next stuck episode warns again.
    push_pending_alerted.intersection_update(seen_push_pending)
    return counts


def _sync_idle_worktrees(
    commander_conn,
    workspace_client,
    *,
    workspace_db_path: str | None = None,
    resolve_transport=None,
    git_runner=subprocess.run,
) -> dict:
    """Periodic-sweep approximation of a pre-assignment sync hook.

    There is no true pre-assignment hook in main.py — worker spawn lives in
    orchestrator_mcp.spawn_worker, which allocates a fresh workspace at spawn
    time rather than drawing from a pre-synced pool. This sweep instead keeps
    any idle (not currently running) managed worktree that is still `active`
    fast-forwarded to its integration target, on the same hourly maintenance
    cadence as the rest of `_run_maintenance`, so a worktree that gets reused
    or inspected later starts from a fresher base. It is NOT a substitute for
    a real pre-assignment hook and never blocks or gates a worker spawn.
    """
    counts = {"synced": 0, "current": 0, "errors": 0}
    if resolve_transport is None:
        resolve_transport = lambda _worker: {}  # noqa: E731 - trivial local default

    commander_conn.row_factory = sqlite3.Row
    try:
        idle_workers = commander_conn.execute(
            "SELECT * FROM workers WHERE status != 'running' "
            "AND workspace_guid IS NOT NULL AND workspace_guid != ''"
        ).fetchall()
    except Exception as exc:
        logger.warning("Worktree sync sweep: could not read commander workers: %s", exc)
        return counts

    db_path = workspace_db_path or _workspace_manager_db_path()
    try:
        ws_conn = sqlite3.connect(db_path, timeout=10)
        ws_conn.row_factory = sqlite3.Row
    except Exception as exc:
        logger.warning("Worktree sync sweep: could not open workspace-manager DB: %s", exc)
        return counts

    try:
        for row in idle_workers:
            worker = dict(row)
            try:
                assignment_row = ws_conn.execute(
                    "SELECT * FROM assignments WHERE workspace_guid = ? AND lifecycle_status = 'active'",
                    (worker["workspace_guid"],),
                ).fetchone()
                if assignment_row is None:
                    continue
                assignment = dict(assignment_row)
                target_ref = assignment.get("integration_target")
                if not target_ref:
                    continue
                result = git_runner(
                    ["git", "-C", worker["repo"], "rev-parse", "--verify", f"{target_ref}^{{commit}}"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"git rev-parse {target_ref} failed: {result.stderr.strip()}"
                    )
                target_head = result.stdout.strip()
                if not target_head or target_head == assignment.get("current_head"):
                    counts["current"] += 1
                    continue
                workspace_client.sync(
                    {
                        "repository_path": worker["repo"],
                        "workspace_guid": assignment["workspace_guid"],
                        "owner_session_id": assignment["owner_session_id"],
                    },
                    **resolve_transport(worker),
                )
                counts["synced"] += 1
            except Exception as exc:
                logger.warning(
                    "Worktree sync sweep: sync failed for worker=%s: %s", worker.get("id"), exc,
                )
                counts["errors"] += 1
    finally:
        ws_conn.close()

    return counts


_LIMIT_COOLDOWN_S = 1800  # re-alert the SAME limit signal at most once per ~window
# separators seen in the wild: middle-dot, colon, hyphen, em-dash; apostrophe may be straight or curly
_ACCOUNT_LIMIT_RE = re.compile(r"you['’]?ve hit your limit(?:\s*[·:\-—]\s*(resets[^\n]*))?", re.IGNORECASE)
_WORKER_LIMIT_RE = re.compile(r"(?<!no )(?:session limit hit|rate-limit menu)", re.IGNORECASE)  # not "no session limit hit"


def detect_account_limit(text: str):
    """Return a human reset string if `text` signals an account/worker usage limit, else None.
    Shared signal set with the Fable-gate deferred usage detector ('hit your limit')."""
    if not text:
        return None
    m = _ACCOUNT_LIMIT_RE.search(text)
    if m:
        return (m.group(1) or "reset time unknown").strip()
    if _WORKER_LIMIT_RE.search(text):
        return "reset time unknown"
    return None


def log_worker_event(event_type: str, **fields) -> None:
    payload = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    logger.info(json.dumps(payload))


CHECKIN_CADENCE = {
    "idle": 60,
    "undecided": 60,
    "brainstorming": 120,
    "debugging": 120,
    "design_ready": 120,
    "design_marked_for_use": 120,
    "writing_plans": 300,
    "plan_ready": 300,
    "final_plan_prep": 300,
    "executing": 600,
    "reviewing": 600,
    "execution_complete": 900,
}
DEFAULT_CADENCE = 300

OSCILLATION_WINDOW = 900.0
OSCILLATION_THRESHOLD = 3
OSCILLATING_STAGES = frozenset({"executing", "reviewing"})
OSCILLATION_CADENCE = 900

STALENESS_ALERT_SECONDS = 1800
STALENESS_KILL_SECONDS = 3600
STALENESS_CHECK_INTERVAL = 60
STALENESS_LIVENESS_EXTENSION = 900

PM_GATE_STAGES = frozenset({"plan_ready", "design_ready"})
PM_GATE_SLACK_SECONDS = 1800
MAX_LIVENESS_DEFERRALS = 2

# Grace window (seconds) a pane-log mtime must exceed the idle-arm time by before
# it counts as fresh activity that disarms the idle-TTL reaper. Absorbs the
# clock/flush skew between arming and the first mtime read.
IDLE_ACTIVITY_GRACE_SECONDS = 5.0

# Cap on how many times the daemon will drive the plain-reconcile recovery
# for a worker stuck in finalization drift before it stops retrying and
# surfaces-and-holds (leaves the worker running, never abandons). The
# counter is CUMULATIVE, not a count of consecutive cycles: it accrues across
# every cycle the worker stays in drift and is cleared only when the drift
# recovery integrates or the worker leaves the running set (see the periodic
# non-running cleanup), so a worker cannot "reset" the cap by going idle.
FINALIZE_DRIFT_RETRY_CAP = 3
# Consecutive TERMINAL finalize failures outside the 'finalization' phase
# (authority/probe/abandon) tolerated before the daemon surfaces the stuck
# worker to the operator once. Surface-only: never completes or abandons.
FINALIZE_FAILURE_SURFACE_CAP = 3
# Terminal states a reconcile reaches when the work has landed. Mirrors
# OrchestratorTools._FINALIZATION_INTEGRATED_STATES (kept as a plain module
# constant so the daemon does not reach through the orchestrator handle, which
# is a MagicMock under test).
FINALIZATION_INTEGRATED_STATES = frozenset(
    {"cleaned", "pushed", "pushed-only", "integrated-local"}
)

# Event types that mark a finalization "settled" point for a worker: an
# integration landed, or the reviewed commit was reopened for edit. The
# marker-aware commit_worker surface below counts finalize_failed events
# SINCE the highest-id marker only, so a resolved-then-retried worker does
# not keep tripping the alert on stale pre-marker failures.
_FINALIZE_MARKER_EVENT_TYPES = frozenset({"finalize_integrated", "finalize_reopened"})


def max_marker_id(events: list[dict]) -> int:
    """Highest event id among finalize_integrated/finalize_reopened markers
    in `events`, or 0 if there is none."""
    ids = [
        e.get("id", 0) for e in events
        if e.get("event_type") in _FINALIZE_MARKER_EVENT_TYPES
    ]
    return max(ids) if ids else 0


def count_failed_since_marker(events: list[dict]) -> int:
    """Count of finalize_failed events strictly newer (higher id) than the
    latest integrate/reopen marker in `events`."""
    marker = max_marker_id(events)
    return sum(
        1 for e in events
        if e.get("event_type") == "finalize_failed" and e.get("id", 0) > marker
    )


STAGE_STALENESS_MULTIPLIER = {
    "executing": 1.5,
    "reviewing": 1.5,
    "brainstorming": 0.75,
    "debugging": 0.75,
}

def select_brain_class(config: dict, conn: sqlite3.Connection):
    """Choose and persist the sticky Brain implementation.

    A valid persisted operator choice is authoritative. ``BRAIN_CLIENT`` remains
    a backward-compatible first-start seed independent of provider-role config;
    after that seed is persisted, stale launch environment cannot override an
    explicit ``/provider brain`` cutover. With neither, configured preference
    seeds the same durable state.
    """
    from ironclaude.provider_state import ProviderState

    state = ProviderState(conn)
    selected = state.get_current_client("brain")
    if selected not in ("claude", "codex"):
        env_selected = os.environ.get("BRAIN_CLIENT", "").strip().lower()
        preferred = (
            (config.get("providers") or {})
            .get("roles", {})
            .get("brain", {})
            .get("preferred", "claude")
        )
        selected = (
            env_selected
            if env_selected in ("claude", "codex")
            else preferred
        )
        state.set_current_client("brain", selected)

    if selected == "codex":
        from ironclaude.codex_brain_client import CodexBrainClient

        return CodexBrainClient
    return BrainClient


def _log_brain_start_result(brain) -> bool:
    """Log startup truthfully without changing Claude's established contract."""
    if getattr(brain, "client_name", None) == "codex":
        if brain.is_alive():
            logger.info("Brain SDK client started client=codex")
            return True
        reason = (
            "capability-blocked"
            if getattr(brain, "capability_block", None) is not None
            else "not-alive"
        )
        logger.error("Brain SDK client not started client=codex reason=%s", reason)
        return False
    logger.info("Brain SDK client started")
    return True


_daemon = None
_pid_lock_fd: int | None = None
_clean_shutdown = False
_sigterm_trusted: bool = True  # set False by _sigaction_cb for untrusted SIGTERM senders
_sigaction_callback = None  # GC anchor for ctypes CFUNCTYPE; must outlive sigaction syscall

_PID_FILE = "/tmp/ic-daemon.pid"


def _is_trusted_signal_sender(
    sender_pid: int,
    sender_uid: int,
    *,
    our_pid: int | None = None,
    our_ppid: int | None = None,
    effective_uid: int | None = None,
) -> bool:
    """Return whether siginfo identifies an authorized local shutdown sender."""
    current_pid = os.getpid() if our_pid is None else our_pid
    parent_pid = os.getppid() if our_ppid is None else our_ppid
    current_uid = os.geteuid() if effective_uid is None else effective_uid
    return (
        sender_pid in (0, 1, current_pid, parent_pid)
        or sender_uid in (0, current_uid)
    )


def _substitute_prompt(text: str, config: dict) -> str:
    """Replace template placeholders in brain prompt text with config values."""
    text = text.replace("{OPERATOR_NAME}", config.get("operator_name", "Operator"))
    text = text.replace("{AUTONOMY_LEVEL}", str(config.get("autonomy_level", "3")))
    return text


def _load_dotenv(dotenv_path: str = ".env") -> None:
    """Load .env file into os.environ without overriding existing vars.

    Shell environment always takes precedence over .env file values.
    Silently ignores missing file.
    """
    try:
        with open(dotenv_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


def _acquire_singleton_lock() -> None:
    """Acquire an exclusive flock on the PID file to enforce daemon singleton."""
    global _pid_lock_fd
    fd = os.open(_PID_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)  # prevent fd inheritance across exec
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            existing = os.read(fd, 32).decode().strip()
            pid_info = f" (PID {existing})" if existing else ""
        except OSError:
            pid_info = ""
        os.close(fd)
        logger.error(f"Another ironclaude daemon is already running{pid_info}. Exiting.")
        logger.info("To stop the existing process: make stop  |  To follow its output: make follow-run")
        sys.exit(1)

    # Lock acquired — check for a live PID from an older (non-locking) daemon
    try:
        existing = os.read(fd, 32).decode().strip()
        if existing:
            try:
                old_pid = int(existing)
                os.kill(old_pid, 0)
                os.close(fd)
                logger.error(f"Another ironclaude daemon is already running (PID {old_pid}). Exiting.")
                logger.info("To stop the existing process: make stop  |  To follow its output: make follow-run")
                sys.exit(1)
            except PermissionError:
                os.close(fd)
                logger.error(f"Another ironclaude daemon is already running (PID {existing}). Exiting.")
                logger.info("To stop the existing process: make stop  |  To follow its output: make follow-run")
                sys.exit(1)
            except ProcessLookupError:
                logger.warning(f"Removing stale PID file from dead process {existing}.")
            except ValueError:
                pass  # Corrupted content — overwrite silently
    except OSError:
        pass

    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{os.getpid()}\n".encode())
    _pid_lock_fd = fd  # Keep open to hold the lock for the process lifetime


def _handle_shutdown(signum, frame):
    import traceback
    global _clean_shutdown, _sigterm_trusted
    logger.warning(
        f"Received signal {signum} — pid={os.getpid()} ppid={os.getppid()} pgid={os.getpgid(0)}"
    )
    logger.warning(f"Shutdown caller stack:\n{''.join(traceback.format_stack(frame))}")
    _clean_shutdown = _sigterm_trusted
    _sigterm_trusted = True  # reset for next signal
    if _daemon:
        if _daemon.plugin_registry:
            _daemon.plugin_registry.run_lifecycle("shutdown", _daemon)
        _daemon.shutdown()
        _daemon.brain._stop_event.set()


def _install_sigaction_handler() -> None:
    """Install SA_SIGINFO signal handler for SIGTERM and SIGINT on macOS arm64.

    Captures sender PID/UID/comm from siginfo_t. Falls back to signal.signal() on any error.
    SA_SIGINFO = 0x0040; struct layouts verified for macOS arm64.
    """
    SA_SIGINFO = 0x0040

    class SigInfo(ctypes.Structure):
        _fields_ = [
            ("si_signo", ctypes.c_int),
            ("si_errno", ctypes.c_int),
            ("si_code",  ctypes.c_int),
            ("si_pid",   ctypes.c_int),   # pid_t, offset 12
            ("si_uid",   ctypes.c_uint),  # uid_t, offset 16
        ]

    class SigAction(ctypes.Structure):
        _fields_ = [
            ("sa_sigaction", ctypes.c_void_p),  # function pointer union, 8 bytes on arm64
            ("sa_mask",      ctypes.c_uint32),   # sigset_t = uint32 on macOS
            ("sa_flags",     ctypes.c_int),
        ]

    SIGACTION_CB = ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.POINTER(SigInfo), ctypes.c_void_p
    )

    def _sigaction_cb(signum, siginfo_ptr, _ctx):
        try:
            if siginfo_ptr:
                sender_pid = siginfo_ptr.contents.si_pid
                sender_uid = siginfo_ptr.contents.si_uid
            else:
                sender_pid = 0
                sender_uid = 0
            try:
                result = subprocess.run(
                    ["ps", "-p", str(sender_pid), "-o", "comm="],
                    capture_output=True, text=True, timeout=2,
                )
                sender_comm = result.stdout.strip() or "<unknown>"
            except Exception:
                sender_comm = "<unknown>"
            logger.warning(
                f"Received signal {signum} FROM pid={sender_pid} uid={sender_uid} "
                f"(comm={sender_comm}) — our pid={os.getpid()} ppid={os.getppid()} "
                f"pgid={os.getpgid(0)}"
            )
            # Trust check: rogue senders leave _clean_shutdown=False so respawner fires
            our_pid = os.getpid()
            our_ppid = os.getppid()
            global _sigterm_trusted
            _sigterm_trusted = _is_trusted_signal_sender(
                sender_pid,
                sender_uid,
                our_pid=our_pid,
                our_ppid=our_ppid,
            )
            if not _sigterm_trusted:
                logger.warning(
                    f"Rogue SIGTERM: sender_pid={sender_pid} sender_uid={sender_uid} "
                    f"({sender_comm}) not in trusted pid/uid sets — respawner will fire"
                )
        except Exception as e:
            logger.warning(f"Received signal {signum} (siginfo parse error: {e})")
        _handle_shutdown(signum, None)

    global _sigaction_callback
    # Store callback before sigaction syscall — prevents GC between assignment and use
    _sigaction_callback = SIGACTION_CB(_sigaction_cb)

    try:
        libc = ctypes.CDLL(None)
        libc.sigaction.restype = ctypes.c_int
        libc.sigaction.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(SigAction),
            ctypes.POINTER(SigAction),
        ]
        for sig in (signal.SIGTERM, signal.SIGINT):
            sa = SigAction()
            sa.sa_sigaction = ctypes.cast(_sigaction_callback, ctypes.c_void_p)
            sa.sa_mask = 0
            sa.sa_flags = SA_SIGINFO
            ret = libc.sigaction(sig, ctypes.byref(sa), None)
            if ret != 0:
                raise OSError(f"sigaction returned {ret} for signal {sig}")
    except Exception as e:
        logger.warning(f"sigaction setup failed ({e}), falling back to signal.signal")
        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)


def _kill_duplicate_daemons() -> None:
    """Kill any ironclaude.main processes other than the current process before restart."""
    our_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ironclaude.main"],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().splitlines():
            try:
                pid = int(pid_str.strip())
            except ValueError:
                continue
            if pid == our_pid:
                continue
            try:
                _logged_kill(pid, signal.SIGTERM, f"kill_duplicate_daemon pid={pid}")
                logger.info(f"Killed duplicate daemon PID {pid} before restart")
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"Could not kill duplicate daemon PID {pid}: {e}")
    except Exception as e:
        logger.warning(f"Failed to scan for duplicate daemons: {e}")
    time.sleep(2)


def _spawn_respawner() -> None:
    """Fork a detached watchdog that restarts the daemon after 5 seconds."""
    logger.info("Abnormal exit detected — forking crash respawner (5s delay)")
    pid = os.fork()
    if pid == 0:
        # Child: detach from parent's process group
        os.setsid()
        time.sleep(5)
        subprocess.Popen(
            [sys.executable, '-m', 'ironclaude.main', '--no-respawn'],
            start_new_session=True,
        )
        os._exit(0)
    # Parent: continues to exit normally


_BRAIN_SESSION = "ic-brain"


def _kill_orphan_workers(tmux: TmuxManager, registry: WorkerRegistry) -> None:
    """Kill ic-* tmux sessions not in the worker registry (orphans from prior daemon lifecycle)."""
    try:
        sessions = tmux.list_sessions(prefix="ic-")
        registered = {w["tmux_session"] for w in registry.get_running_workers()}
        for name in sessions:
            if name == _BRAIN_SESSION:
                continue
            if name in registered:
                continue
            tmux.kill_session(name)
            logger.info(f"Killed orphan worker session: {name}")
    except Exception as e:
        logger.error(f"Failed to kill orphan worker sessions: {e}")


def _kill_orphan_brains(recorded_brain_pid: int | None = None) -> None:
    """Belt-and-suspenders: terminate a surviving Brain subprocess by its RECORDED
    PID (never by pattern). pgrep is used only to LOG residue for diagnostics."""
    if recorded_brain_pid:
        try:
            _logged_kill(recorded_brain_pid, signal.SIGTERM,
                         f"kill_orphan_brain recorded_pid={recorded_brain_pid}")
            logger.info(f"Signalled recorded brain PID {recorded_brain_pid}")
            time.sleep(2)
        except (ProcessLookupError, PermissionError) as e:
            logger.warning(f"Could not signal recorded brain PID {recorded_brain_pid}: {e}")
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
            capture_output=True, text=True, timeout=5,
        )
        residue = [p for p in result.stdout.strip().split() if p.strip()]
        if residue:
            logger.warning("Brain pattern residue after recorded-PID kill (NOT killed): %s", residue)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Brain residue pgrep check failed: {e}")


def _handle_restart(signum, frame):
    global _clean_shutdown
    logger.info("Daemon restart requested via SIGHUP")
    _clean_shutdown = True
    if _daemon:
        # Step 0: abort any in-progress /login relay so its `claude auth login` child isn't
        # orphaned across the execvp (review M6). Non-destructive: an incomplete login never
        # replaces the credential.
        try:
            if getattr(_daemon, "_auth_relay", None) and _daemon._auth_relay.in_progress():
                logger.info("Restart step 0: abort in-progress /login relay")
                _daemon._auth_relay.abort()
        except Exception:
            pass
        # Step 1: Poison brain retry loop BEFORE any subprocess kills
        try:
            logger.info("Restart step 1: poison brain retry loop")
            _daemon.brain._stop_event.set()
            _daemon.brain._running = False
        except Exception:
            pass
        logger.info("Restart step 2: daemon.shutdown()")
        _daemon.shutdown()
        # Explicitly stop Slack socket connection (main()'s finally block won't run on execvp)
        try:
            if _daemon.socket_handler:
                logger.info("Restart step 3: socket_handler.stop()")
                _daemon.socket_handler.stop()
        except Exception:
            pass
        # Capture the recorded Brain PID BEFORE brain.shutdown() clears it (step 4
        # sets _brain_pid=None and removes BRAIN_PID_FILE), so the belt-and-suspenders
        # kills of steps 5 and 7 target the real pid instead of None.
        _recorded_brain_pid = getattr(getattr(_daemon, "brain", None), "_brain_pid", None)
        # Shut down BrainClient — thread joins quickly since _stop_event already set
        try:
            logger.info("Restart step 4: brain.shutdown()")
            _daemon.brain.shutdown()
        except Exception:
            pass
        # Belt-and-suspenders: kill any brain subprocesses that survived shutdown
        try:
            logger.info("Restart step 5: _kill_orphan_brains()")
            _kill_orphan_brains(_recorded_brain_pid)
        except Exception:
            pass
        # Kill orphaned worker tmux sessions from previous daemon lifecycle
        try:
            logger.info("Restart step 5b: _kill_orphan_workers()")
            _kill_orphan_workers(_daemon.tmux, _daemon.registry)
        except Exception:
            pass
        # Verify no brain subprocesses remain
        try:
            result = subprocess.run(
                ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
                capture_output=True, text=True, timeout=5,
            )
            survivors = [p for p in result.stdout.strip().split() if p.strip()]
            if survivors:
                logger.warning(f"Brain subprocesses still alive after cleanup: {survivors}")
            else:
                logger.info("Restart step 6: verified no brain subprocesses remain")
        except Exception:
            pass
        # Belt-and-suspenders: targeted kill of brain subprocess if still alive
        try:
            brain_pid = _recorded_brain_pid
            if brain_pid is not None:
                logger.info(f"Restart step 7: targeted kill of brain PID {brain_pid}")
                _logged_kill(brain_pid, signal.SIGTERM, "handle_restart targeted brain kill")
                time.sleep(1)
            else:
                logger.info("Restart step 7: no brain PID to kill")
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            pass
        # Close the DB connection before exec
        try:
            if _daemon._db:
                _daemon._db.close()
        except Exception:
            pass
    # Kill any other ironclaude.main processes before restarting
    logger.info("Restart step 8: _kill_duplicate_daemons()")
    _kill_duplicate_daemons()
    global _pid_lock_fd
    if _pid_lock_fd is not None:
        try:
            logger.info("Restart step 9: releasing lock fd")
            os.ftruncate(_pid_lock_fd, 0)
        except OSError:
            pass
        try:
            os.close(_pid_lock_fd)
        except OSError:
            pass
        _pid_lock_fd = None
    logger.info("Restart step 10: os.execvp() — this is the last log line before restart")
    os.execvp(sys.executable, [sys.executable, '-m', 'ironclaude.main'])


def ensure_brain_trusted(brain_cwd: str) -> None:
    """Ensure the brain's working directory is trusted in ~/.claude.json.

    Reads ~/.claude.json, checks if brain_cwd has hasTrustDialogAccepted=true,
    adds a minimal project entry if missing, and writes back with file locking.
    Defensive: logs warnings and continues on any failure.
    """
    claude_json_path = os.path.expanduser("~/.claude.json")
    abs_cwd = os.path.abspath(brain_cwd)

    try:
        with open(claude_json_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse {claude_json_path}")
                return

            projects = data.get("projects", {})
            project = projects.get(abs_cwd, {})

            if project.get("hasTrustDialogAccepted") is True:
                logger.info(f"Brain directory already trusted: {abs_cwd}")
                return

            project["hasTrustDialogAccepted"] = True
            project.setdefault("allowedTools", [])
            projects[abs_cwd] = project
            data["projects"] = projects

            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
            logger.info(f"Added trust entry for brain directory: {abs_cwd}")
    except FileNotFoundError:
        logger.warning(f"{claude_json_path} not found — cannot pre-trust brain directory")
    except OSError as e:
        logger.warning(f"Could not update {claude_json_path}: {e}")


def _deploy_worker_hooks(
    repo_root: str,
    stable_dir: str | None = None,
    plugin_cache_base: str | None = None,
) -> None:
    """Deploy worker/hooks/*.sh to the runtime locations, mirroring the root
    Makefile's deploy-hooks target. Called at every daemon start (including
    SIGHUP execvp restarts) so a restart always implies current hooks —
    eliminating the forgotten-`make deploy-hooks` stale-hook failure mode.

    - Stable dir ~/.claude/ironclaude-hooks: mandatory. Missing source dir or
      copy failure exits the daemon (consistent with the brain-file syncs in
      main() — a broken layout should be fixed, not papered over).
    - Latest plugin-cache hooks dir: best-effort. Absent cache logs a WARNING
      and continues (mirrors the Makefile's WARN branch).
    """
    hooks_src = os.path.join(os.path.dirname(repo_root), "worker", "hooks")
    stable = stable_dir or os.path.expanduser("~/.claude/ironclaude-hooks")
    cache_base = plugin_cache_base or os.path.expanduser(
        "~/.claude/plugins/cache/ironclaude/ironclaude"
    )
    if not os.path.isdir(hooks_src):
        logger.error(f"Worker hooks source not found at {hooks_src}")
        sys.exit(1)
    scripts = sorted(f for f in os.listdir(hooks_src) if f.endswith(".sh"))
    try:
        os.makedirs(stable, exist_ok=True)
        for name in scripts:
            shutil.copy2(os.path.join(hooks_src, name), os.path.join(stable, name))
    except OSError as exc:
        logger.error(f"Failed deploying worker hooks to {stable}: {exc}")
        sys.exit(1)
    logger.info(f"Deployed {len(scripts)} worker hooks to {stable}")

    def _version_key(name: str) -> tuple:
        # Numeric sort so 1.0.16 beats 1.0.9 (lexicographic would invert;
        # the Makefile uses `sort -V` for the same reason). Non-version dirs
        # sort lowest and are never selected over a real version.
        try:
            return (1, tuple(int(p) for p in name.split(".")))
        except ValueError:
            return (0, ())

    latest = None
    if os.path.isdir(cache_base):
        versions = [
            d for d in os.listdir(cache_base)
            if os.path.isdir(os.path.join(cache_base, d)) and _version_key(d)[0] == 1
        ]
        if versions:
            latest = max(versions, key=_version_key)
    cache_hooks = os.path.join(cache_base, latest, "hooks") if latest else None
    if cache_hooks and os.path.isdir(cache_hooks):
        try:
            for name in scripts:
                shutil.copy2(
                    os.path.join(hooks_src, name), os.path.join(cache_hooks, name)
                )
            logger.info(
                f"Deployed {len(scripts)} worker hooks to plugin cache {cache_hooks}"
            )
        except OSError as exc:
            logger.warning(f"Plugin-cache hook deploy failed (continuing): {exc}")
    else:
        logger.warning(
            f"Plugin cache hooks dir absent under {cache_base} — stable dir updated only"
        )


def _worker_matches_directive(worker: dict, directive_id: int) -> bool:
    description = worker.get("description") or ""
    worker_id = worker.get("id") or ""
    return f"#{directive_id}" in description or worker_id.startswith(f"d{directive_id}-")


class IroncladeDaemon:
    def __init__(self, config: dict, slack: SlackBot, socket_handler: SlackSocketHandler | None,
                 registry: WorkerRegistry, tmux_manager: TmuxManager, brain: BrainClient,
                 db_conn=None, plugin_registry=None, ssh_manager=None):
        self.config = config
        self.slack = slack
        self.socket_handler = socket_handler
        self.registry = registry
        self.tmux = tmux_manager
        self.brain = brain
        self._running = True
        self._paused = False
        self._brain_paused = False
        self.plugin_registry = plugin_registry or PluginRegistry()
        self._last_heartbeat = 0.0
        self._last_heartbeat_ts: str | None = None   # Slack ts of the last heartbeat (threads tactical chatter)
        self._last_brain_context: tuple[str, str] | None = None   # fully delivered top-level (ts, text) eligible for operator-wait links
        self._decisions_dir = os.path.join(config.get("tmp_dir", "/tmp/ic"), "brain-decisions")
        self._ledger_path = os.path.join(config.get("tmp_dir", "/tmp/ic"), "task-ledger.json")
        self._db = db_conn
        self._last_checkin_sent: dict[str, float] = {}
        self._last_checkin_stage: dict[str, str | None] = {}
        self._last_checkin_hash: dict[str, int] = {}
        self._last_stage_seen: dict[str, str | None] = {}
        self._stage_history: dict[str, list[tuple[float, str]]] = {}
        self._directive_reminder_sent: dict[int, float] = {}
        self._claude_dir: Path | None = None
        self._last_maintenance = 0.0
        self._state_manager_db_path = os.path.expanduser("~/.claude/ironclaude.db")
        self._ssh_manager = ssh_manager
        # Lazily-built OrchestratorTools handle (owns the WorkspaceClient + review
        # gate readers + evidence derivation) for deterministic auto-integration.
        # main.py deliberately holds NO WorkspaceClient of its own.
        self._orchestrator = None
        # Idle enforcement state
        self._idle_enforcement_start = 0.0
        self._idle_escalation_tier = 0
        self._last_idle_check = 0.0
        self._operator_notified_idle = False
        # Post-kill sweep state
        self._last_kill_sweep_check: float = time.time()
        # Message aging state
        self._last_message_aging_check: float = 0.0
        self._message_aging_alerted: set[str] = set()
        self._push_pending_alerted: set[str] = set()
        # Row-less orphan reaper (step 7 of _run_maintenance) surfacing state.
        self._orphaned_unmerged_count = 0
        self._orphaned_surface_state: dict[str, tuple[str, str]] = {}  # {orphan id: (tip, category)}
        self._load_orphan_surface_state()
        # /login account-switch relay (operator-triggered; SIGHUP-restart on verified success)
        self._auth_relay = AuthRelay()
        # Usage-limit surfacing: {reset-string: last-alert-epoch} for a per-window cooldown
        self._limit_alerted: dict[str, float] = {}
        # Stuck worker detection state
        self._stuck_hash: dict[str, int] = {}
        self._stuck_since: dict[str, float] = {}
        self._stuck_alert_sent: dict[str, bool] = {}
        self._stuck_kill_deferred: dict[str, float] = {}
        # Idle-TTL reaper: {worker_id: epoch first seen idle}. Armed on a .done
        # sighting, disarmed on pane-log activity, cleared when the worker leaves
        # the running set. A worker armed past idle_worker_ttl_seconds with no
        # activity is reaped (see _reap_idle_worker).
        self._worker_idle_since: dict[str, float] = {}
        self._last_stuck_check: float = 0.0
        self._grader = LocalGrader(keep_alive="30m")
        # FIX 2: cap concurrent bounded grades at one. _grade_bounded sets this flag
        # before offloading; a stalled grade that is abandoned keeps the flag set
        # (cleared only by the worker's finally) so later calls fail fast to None
        # instead of piling up daemon threads against a stalled grader.
        self._grade_state_lock = threading.Lock()
        self._grade_in_flight = False
        self._prompt_waiting_cache: dict[int, tuple[float, PromptDetection]] = {}
        self._stuck_liveness_count: dict[str, int] = {}
        self._pm_gate_slack_sent: dict[str, bool] = {}
        self._stage_entered_at: dict[str, float] = {}
        self._heartbeat_state_history: dict[str, list[tuple[str, int]]] = {}
        self._heartbeat_stuck_notified: set[str] = set()
        # Finalization-drift recovery: per-worker count of consecutive cycles the
        # daemon has driven the plain-reconcile recovery for a worker stuck in
        # frozen drift, and the once-per-worker surfacing gate (used both to fire
        # the session-died posts exactly once and to hold — never re-alert — a
        # worker whose drift is unresolved after FINALIZE_DRIFT_RETRY_CAP cycles).
        self._finalize_drift_retry: dict[str, int] = {}
        self._finalize_failure_count: dict[str, int] = {}
        self._session_died_notified: set[str] = set()
        # Once-per-worker gate for the conflict/repair finalization-recovery
        # operator surface (a stuck reconcile the daemon can neither drift-retry
        # nor complete). Cleared alongside the drift state in the periodic
        # non-running cleanup so it neither leaks nor permanently suppresses a
        # re-alert on worker-id reuse.
        self._finalize_recovery_alerted: set[str] = set()
        # Marker-aware once-per-episode gate for the persistently-failing
        # commit_worker surface and the reopen/integrate re-arm, collapsed
        # into a single episode key: worker_id -> the highest integrate/
        # reopen marker id already observed for that worker (0 if none yet).
        # A later marker re-arms the drift/recovery state and clears the
        # alert set below, so a re-fail after the worker is fixed still
        # surfaces. Without this, the re-arm block would fire every cycle
        # while a marker remains the newest event, repeatedly clearing
        # drift/alert state and defeating the drift cap.
        self._finalize_marker_seen: dict[str, int] = {}
        self._commit_failure_alerted: set[str] = set()
        # Awaiting-operator surfacing (in-memory; refreshed by Brain re-emission)
        self._operator_waits: dict[str, dict] = {}
        # R6: worker_id -> (worker_id, directive_id, directive_status) of the last
        # alert fired, so one operator_wait alert lands per pending directive.
        self._operator_wait_alerted: dict[str, tuple] = {}
        self._brain_waits: dict[str, dict] = {}
        self._prompt_dispatch_recovery_checked = False
        self._load_staleness_state()

    def shutdown(self):
        self._running = False

    def _get_orchestrator(self):
        """Return a memoized OrchestratorTools sharing this daemon's registry,
        tmux, db and ssh manager. It owns the WorkspaceClient and the review-gate
        readers, so the daemon reaches deterministic auto-integration through it
        without constructing any workspace client itself. Returns None if the
        handle cannot be built (auto-integration is best-effort; the daemon loop
        must never die because of it)."""
        if self._orchestrator is None:
            try:
                from ironclaude.orchestrator_mcp import OrchestratorTools
                self._orchestrator = OrchestratorTools(
                    self.registry,
                    self.tmux,
                    slack_bot=self.slack,
                    db_conn=self._db,
                    config=self.config,
                    ssh_manager=self._ssh_manager,
                )
            except Exception as exc:  # noqa: BLE001 - best-effort integration
                logger.warning("Could not build orchestrator handle: %s", exc)
                return None
        return self._orchestrator

    def _finalize_and_release_worker(self, worker_id: str, reason: str, terminal: bool):
        """Best-effort delegate to OrchestratorTools' auto-integration seam.

        Returns the outcome dict, or None if the handle is unavailable or the
        call raised (the daemon loop must never die on an integration attempt).
        """
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return None
        try:
            return orchestrator._finalize_and_release_worker(
                worker_id, reason=reason, terminal=terminal,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort integration
            logger.warning(
                "Auto-integration failed for %s (%s): %s", worker_id, reason, exc,
            )
            return None

    @staticmethod
    def _finalization_recovery_mode(outcome):
        """Return the reconcile recovery mode for a finalization-failure outcome,
        or None.

        The classifier nests the mode under recovery.reconcile.mode (see
        OrchestratorTools._workspace_failure); it is NOT a top-level key. Any
        outcome that is not a finalization failure — a transient preserved
        failure (authority/probe/abandon), a completion, or None — returns None,
        which the driver treats as 'leave running, do nothing'.
        """
        if isinstance(outcome, dict) and outcome.get("failure_phase") == "finalization":
            return outcome.get("recovery", {}).get("reconcile", {}).get("mode")
        return None

    def _drive_finalization_recovery(self, worker_id: str, outcome, *, terminal: bool = False) -> str:
        """Unified finalization-recovery driver for terminal AND idle finalize
        outcomes. The orchestrator seam owns ALL worker completion now; this
        driver NEVER calls update_worker_status. It switches on the reconcile
        recovery mode and returns a disposition the caller may use:

          'integrated' — a 'drift' recovery landed the work. The seam already
                         completed a dead worker (a live idle worker was
                         integrated-not-completed); the driver only clears the
                         drift counter. No daemon completion.
          'retrying'   — 'drift' under the cap: the seam was driven this cycle;
                         the worker is left running to retry next cycle.
          'held'       — 'drift' over FINALIZE_DRIFT_RETRY_CAP: the daemon STOPS
                         driving the seam and holds the worker running. Never
                         abandoned, never completed — the repo-wide integration
                         lock the drift row may hold must not be stranded.
          'surfaced'   — 'conflict'/'repair': surfaced to the operator exactly
                         once (via _finalize_recovery_alerted). Never completed,
                         never abandoned.
          'transient'  — None / any other mode (authority/probe/abandon/None):
                         leave the worker running. A TERMINAL outcome with a
                         non-'finalization' failure_phase is counted; past
                         FINALIZE_FAILURE_SURFACE_CAP consecutive terminal
                         failures it is surfaced once (never completed).

        Mints no commit and never calls _abandon_rescue_worker.
        """
        mode = self._finalization_recovery_mode(outcome)
        if mode == "drift":
            attempts = self._finalize_drift_retry.get(worker_id, 0) + 1
            self._finalize_drift_retry[worker_id] = attempts
            if attempts > FINALIZE_DRIFT_RETRY_CAP:
                # Surface-and-hold: stop driving, leave running, never abandon.
                # Alert the operator exactly once (mirrors the conflict/repair
                # surface below) -- a drift row held here may still hold the
                # repo-wide integration lock and needs eyes on it.
                if worker_id not in self._finalize_recovery_alerted:
                    self._finalize_recovery_alerted.add(worker_id)
                    self.slack.post_message(
                        f"Worker {worker_id} finalization drift unresolved after "
                        f"{FINALIZE_DRIFT_RETRY_CAP} attempts; held — reviewed work "
                        f"preserved, not integrated, not completed; needs operator help."
                    )
                    self.brain.send_message(
                        f"Worker {worker_id} finalization drift unresolved after "
                        f"{FINALIZE_DRIFT_RETRY_CAP} attempts; held. Not completed, not "
                        f"abandoned. Needs operator intervention."
                    )
                return "held"
            orchestrator = self._get_orchestrator()
            state = None
            if orchestrator is not None:
                try:
                    result = orchestrator.drive_frozen_reconcile_recovery(worker_id)
                    state = result.get("state") if isinstance(result, dict) else None
                except Exception as exc:  # noqa: BLE001 - best-effort recovery
                    logger.warning(
                        "Drift reconcile recovery failed for %s: %s", worker_id, exc,
                    )
            if state in FINALIZATION_INTEGRATED_STATES:
                # The seam integrated the work: a dead worker was completed by
                # the seam, a live idle worker was integrated-not-completed. The
                # daemon completes NOTHING here — just clear the retry counter.
                self._finalize_drift_retry.pop(worker_id, None)
                return "integrated"
            return "retrying"
        if mode in ("conflict", "repair"):
            # A stuck reconcile the daemon can neither drift-retry nor complete:
            # surface to the operator ONCE, never abandon, never complete.
            if worker_id not in self._finalize_recovery_alerted:
                self._finalize_recovery_alerted.add(worker_id)
                self.slack.post_message(
                    f"Worker {worker_id} finalization needs operator help "
                    f"(reconcile mode={mode}); left running, not completed."
                )
                self.brain.send_message(
                    f"Worker {worker_id} finalization stuck (reconcile mode="
                    f"{mode}); needs operator intervention. Not completed, not "
                    f"abandoned."
                )
            return "surfaced"
        # Transient preserved failure (authority/probe) or None: leave running.
        # A finalization failure with no recognized recovery mode still needs
        # operator eyes eventually — surface it once, same style as the
        # conflict/repair branch above, never completing/abandoning.
        if (
            mode is None
            and isinstance(outcome, dict)
            and outcome.get("failure_phase") == "finalization"
            and worker_id not in self._finalize_recovery_alerted
        ):
            self._finalize_recovery_alerted.add(worker_id)
            self.slack.post_message(
                f"Worker {worker_id} finalization failed with no recognized "
                "recovery mode; left running, not completed, reviewed work "
                "preserved."
            )
            self.brain.send_message(
                f"Worker {worker_id} finalization failed with no recognized "
                "recovery mode; needs operator intervention. Not completed, "
                "not abandoned."
            )
        # A TERMINAL finalize failing outside the 'finalization' phase
        # (authority/probe/abandon) used to stay silently 'transient' forever.
        # Count consecutive terminal failures and surface ONCE past the cap.
        # Non-terminal (idle) outcomes are not counted: an idle live worker
        # legitimately returns a preserved failure every cycle.
        phase = outcome.get("failure_phase") if isinstance(outcome, dict) else None
        if terminal and phase and phase != "finalization":
            failures = self._finalize_failure_count.get(worker_id, 0) + 1
            self._finalize_failure_count[worker_id] = failures
            if (
                failures > FINALIZE_FAILURE_SURFACE_CAP
                and worker_id not in self._finalize_recovery_alerted
            ):
                self._finalize_recovery_alerted.add(worker_id)
                error = outcome.get("error") or "no error detail"
                self.slack.post_message(
                    f"Worker {worker_id} terminal finalize has failed {failures} "
                    f"consecutive cycles (phase={phase}): {error}; left running, "
                    "not completed, needs operator help."
                )
                self.brain.send_message(
                    f"Worker {worker_id} terminal finalize has failed {failures} "
                    f"consecutive cycles (phase={phase}): {error}. Not completed, "
                    "not abandoned. Needs operator intervention."
                )
        return "transient"

    def _get_operator_message_dispositions(self) -> dict[str, str]:
        """Return durable operator-message dispositions from the authoritative ledger."""
        if self._db is None:
            return {}
        rows = self._db.execute(
            "SELECT source_ts, 'directive' AS disposition FROM directives "
            "UNION "
            "SELECT source_ts, 'acknowledged' AS disposition "
            "FROM operator_message_acknowledgements"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def _get_unprocessed_messages(self, max_age_seconds: int = 1800) -> list[dict]:
        """Find operator messages older than max_age with no durable disposition."""
        operator_user_id = self.config.get("slack_operator_user_id", "")
        if not operator_user_id or self._db is None:
            return []
        try:
            oldest = str(time.time() - 7200)
            messages = self.slack.get_recent_messages(limit=50, oldest=oldest)
        except Exception:
            return []
        now = time.time()
        try:
            dispositions = self._get_operator_message_dispositions()
        except Exception:
            return []
        result = []
        for msg in messages:
            if msg.get("user") != operator_user_id:
                continue
            msg_age = now - float(msg["ts"])
            if msg_age < max_age_seconds:
                continue
            if msg["ts"] in dispositions:
                continue
            result.append(msg)
        return result

    def _validate_brain_message(self, text: str) -> tuple[bool, str]:
        """Validate Brain message has directive reference and reason clause via LLM."""
        if not text.strip():
            return False, "Empty message"
        if not _DIRECTIVE_REF_RE.search(text):
            return False, _BLOCKED_NO_DIRECTIVE
        result = self._grader.grade(
            _BRAIN_MSG_VALIDATION_SYSTEM,
            f"Validate this Brain message:\n{truncate_middle(text)}",
            _BRAIN_MSG_SCHEMA,
        )
        if result.get("infrastructure_error"):
            logger.warning(
                "Brain message validator unavailable: %s — allowing through",
                result.get("error_detail"),
            )
            return True, ""
        if result.get("valid", True):
            return True, ""
        return False, result.get("reason", "Message does not meet Brain message requirements")

    def check_post_kill_sweep(self):
        """Send mandatory sweep message to Brain after each kill_worker event."""
        if self._db is None:
            return
        try:
            rows = self._db.execute(
                "SELECT worker_id FROM events "
                "WHERE event_type = 'worker_finished' "
                "AND timestamp > datetime(?, 'unixepoch')",
                (self._last_kill_sweep_check,),
            ).fetchall()
        except Exception:
            return
        self._last_kill_sweep_check = time.time()
        if not rows:
            return
        try:
            directives = self._db.execute(
                "SELECT id, interpretation FROM directives "
                "WHERE status IN ('confirmed', 'in_progress')"
            ).fetchall()
        except Exception:
            return
        if not directives:
            return
        directive_list = ", ".join(f"#{r[0]}: {r[1][:60]}" for r in directives)
        for row in rows:
            worker_id = row[0] or "unknown"
            self.brain.send_message(
                f"[MANDATORY SWEEP] Worker {worker_id} completed. "
                f"{len(directives)} directive(s) remain unworked: {directive_list}. "
                f"Run full attention sweep — spawn workers for all unblocked "
                f"directives before doing anything else."
            )

    def check_message_aging(self):
        """Alert Brain about operator messages >30min old without directives."""
        now = time.time()
        if now - self._last_message_aging_check < 300:
            return
        self._last_message_aging_check = now

        unprocessed = self._get_unprocessed_messages()

        if self._message_aging_alerted and self._db is not None:
            try:
                dispositions = self._get_operator_message_dispositions()
                self._message_aging_alerted -= set(dispositions)
            except Exception:
                pass

        for msg in unprocessed:
            ts = msg["ts"]
            if ts in self._message_aging_alerted:
                continue
            minutes_ago = int((now - float(ts)) / 60)
            self.brain.send_message(
                f"[UNPROCESSED MESSAGE] Operator message from {minutes_ago} minutes ago "
                f"has not been processed into a directive. "
                f'Message: "{msg["text"][:100]}..." '
                f"(ts: {ts}). Read this message and submit_directive() or acknowledge it."
            )
            self._message_aging_alerted.add(ts)

    def _run_maintenance(self):
        """Run periodic maintenance: clean old logs and prune DB tables.

        Runs on first call and then hourly. Each sub-operation is independent —
        one failure does not block others. Episodic memory is NEVER touched.
        """
        now = time.time()
        if now - self._last_maintenance < 3600:
            return
        self._last_maintenance = now

        # 1. Clean old log files (7 days)
        try:
            self.tmux.cleanup_old_logs(7)
        except Exception as e:
            logger.warning(f"Maintenance: log cleanup failed: {e}")

        # 2. Prune daemon events table (30 days)
        try:
            if self._db:
                def _prune_events():
                    self._db.execute(
                        "DELETE FROM events WHERE timestamp < datetime('now', '-30 days')"
                    )
                    self._db.commit()
                self._db_write_with_retry(_prune_events)
        except Exception as e:
            logger.warning(f"Maintenance: events pruning failed: {e}")

        # 3. Prune state-manager audit_log (90 days) — separate DB, best-effort
        try:
            conn = sqlite3.connect(self._state_manager_db_path, timeout=10)
            try:
                conn.execute(
                    "DELETE FROM audit_log WHERE created_at < datetime('now', '-90 days')"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Maintenance: audit_log pruning failed: {e}")

        # 4. Sweep stale idle/undecided session artifacts (rows + dead-pid id files)
        try:
            claude_dir = Path(self._state_manager_db_path).parent
            live_uuids, dead_files = _scan_session_id_files(claude_dir)
            for f in dead_files:
                try:
                    f.unlink()
                except OSError:
                    pass
            deleted = _sweep_stale_sessions(
                self._state_manager_db_path, live_uuids, _SESSION_SWEEP_MAX_AGE_HOURS
            )
            if deleted or dead_files:
                logger.info(
                    "Maintenance: session sweep removed %d stale rows, %d dead id files",
                    deleted, len(dead_files),
                )
        except Exception as e:
            logger.warning(f"Maintenance: session sweep failed: {e}")

        # 5. Reap leaked managed worktrees (release-only: cleanup / abandon(rescue))
        try:
            orchestrator = self._get_orchestrator()
            if orchestrator is not None:
                counts = _reap_leaked_worktrees(
                    self._db, orchestrator._workspace_client, self.tmux,
                    resolve_transport=self._worktree_reap_transport,
                    push_pending_alerted=self._push_pending_alerted,
                )
                if counts["released"] or counts["surfaced"] or counts["errors"]:
                    logger.info("Maintenance: worktree reaper %s", counts)
        except Exception as e:
            logger.warning(f"Maintenance: worktree reaper failed: {e}")

        # 6. Sync idle managed worktrees toward their integration target — a
        # periodic-sweep approximation of a pre-assignment sync hook (see
        # `_sync_idle_worktrees` docstring for why there is no true hook here).
        try:
            orchestrator = self._get_orchestrator()
            if orchestrator is not None:
                counts = _sync_idle_worktrees(
                    self._db, orchestrator._workspace_client,
                    resolve_transport=self._worktree_reap_transport,
                )
                if counts["synced"] or counts["errors"]:
                    logger.info("Maintenance: worktree sync sweep %s", counts)
        except Exception as e:
            logger.warning(f"Maintenance: worktree sync sweep failed: {e}")

        # 7. Reap ROW-LESS orphaned worktrees/branches (git-state, not DB-driven).
        try:
            orchestrator = self._get_orchestrator()
            if orchestrator is not None:
                summary = _reap_row_less_orphans(
                    self._db, orchestrator._workspace_client, self.tmux,
                    resolve_transport=self._worktree_reap_transport,
                )
                summary.pop("preserved_unmerged_names")
                preserved_detail = summary.pop("preserved_detail")
                self._orphaned_unmerged_count = len(
                    [d for d in preserved_detail if d.get("category") != "squash-merged"]
                )
                self._surface_preserved_orphans(preserved_detail)
                if any(summary[k] for k in ("reaped", "preservedUnmerged", "errors", "repo_failures")):
                    logger.info("Maintenance: orphan reaper %s", summary)
        except Exception as e:
            logger.warning(f"Maintenance: orphan reaper failed: {e}")

    def _surface_preserved_orphans(self, details: list[dict]) -> None:
        """Post preserved orphans to Slack on first appearance and whenever
        the surfaced {id: (tip, category)} map changes; the heartbeat
        carries the standing count between changes. Re-arms when the set
        empties."""
        current = {d["id"]: (d.get("tip", ""), d.get("category", "")) for d in details}
        changed = current != self._orphaned_surface_state
        if current and changed:
            self.slack.post_message(format_orphaned_orphans(details))
            if self._orphaned_unmerged_count > 0:
                by_repo: dict[str, list[str]] = {}
                for d in details:
                    by_repo.setdefault(d.get("repository_path", ""), []).append(
                        f"{d.get('id')} [{d.get('category')}]"
                    )
                repo_lines = "\n".join(
                    f"{repo}: {', '.join(ids)}" for repo, ids in by_repo.items()
                )
                self.brain.send_message(
                    f"PRESERVED ORPHANS SURFACED — {self._orphaned_unmerged_count} need review.\n"
                    f"{repo_lines}\n"
                    "Per 'Resolving Orphaned Worktrees': call "
                    "list_surfaced_orphans(repository_path) for each repo above and offer "
                    "the operator a per-orphan walkthrough once."
                )
        self._orphaned_surface_state = current
        if changed and getattr(self, "_db", None) is not None:
            self._persist_orphan_surface_state()

    def _worktree_reap_transport(self, worker: dict) -> dict:
        """Best-effort transport kwargs (ssh_host/plugin_root) for a worktree
        reaper release/sync call on this worker's host. Never raises — an
        exception here degrades to a local-transport attempt, which the
        client call itself will then fail (and log) if that guess is wrong."""
        try:
            orchestrator = self._get_orchestrator()
            if orchestrator is None:
                return {}
            ssh_host = orchestrator._resolve_ssh_host(worker["id"])
            installed_root = orchestrator._workspace_client.discover_installed_plugin_root(
                worker.get("client") or "claude", ssh_host=ssh_host,
            )
            return orchestrator._workspace_transport(installed_root, ssh_host)
        except Exception as exc:
            logger.warning(
                "Worktree reaper: transport resolution failed for worker=%s: %s",
                worker.get("id"), exc,
            )
            return {}

    def _route_operator_prompt_guidance(
        self, text: str, source_ts: str
    ) -> dict | None:
        """Route trusted Slack guidance only when it names one active worker."""
        store = self._prompt_store()
        if store is None:
            return None
        try:
            active = store.active_incidents()
        except sqlite3.Error:
            logger.exception(
                "Prompt guidance inventory read failed; Brain dispatch held"
            )
            return {
                "worker_id": None,
                "replayed": False,
                "delivered": False,
                "held": True,
            }
        matches = []
        for incident in active:
            worker_id = incident["worker_id"]
            token = re.compile(
                rf"(?<![A-Za-z0-9_-]){re.escape(worker_id)}(?![A-Za-z0-9_-])"
            )
            if token.search(text):
                matches.append(incident)
        if len(matches) != 1:
            return None
        incident = matches[0]
        try:
            observation = store.rearm_from_operator_guidance(
                incident["worker_id"], source_ts, now=time.time()
            )
        except ValueError:
            logger.warning(
                "Prompt guidance rejected: invalid authenticated Slack ts=%r", source_ts
            )
            return {
                "worker_id": incident["worker_id"],
                "replayed": False,
                "delivered": False,
                "held": True,
            }
        except sqlite3.Error:
            logger.exception(
                "Prompt guidance persistence failed for %s; Brain dispatch held",
                incident["worker_id"],
            )
            return {
                "worker_id": incident["worker_id"],
                "replayed": False,
                "delivered": False,
                "held": True,
            }
        if observation is None:
            return {
                "worker_id": incident["worker_id"],
                "replayed": True,
                "delivered": False,
            }
        claimed_at = time.time()
        try:
            claimed = store.claim_dispatch(
                observation.dispatch_id, destination="brain", now=claimed_at
            )
        except sqlite3.Error:
            logger.exception(
                "Prompt guidance claim failed for %s; Brain dispatch held",
                incident["worker_id"],
            )
            return {
                "worker_id": incident["worker_id"],
                "replayed": False,
                "delivered": False,
                "held": True,
            }
        if not claimed:
            return {
                "worker_id": incident["worker_id"],
                "replayed": True,
                "delivered": False,
            }
        elapsed = max(0, int(claimed_at - float(incident["first_observed_at"])))
        message = (
            f"OPERATOR MESSAGE (ts={source_ts}): {text}\n"
            f"[ACTIVE PROMPT] worker={incident['worker_id']} "
            f"stage={incident['stage']} age={elapsed}s\n{incident['evidence']}"
        )
        try:
            delivered = bool(self.brain.send_message(message))
        except Exception:
            logger.exception(
                "Operator prompt-guidance Brain delivery raised for %s",
                incident["worker_id"],
            )
            delivered = False
        capability_block = getattr(self.brain, "capability_block", None)
        failure_category = (
            None
            if delivered
            else "capability_blocked"
            if isinstance(capability_block, dict)
            else "transport_failure"
        )
        delivery_unknown = False
        try:
            store.record_delivery(
                observation.dispatch_id,
                delivered=delivered,
                failure_category=failure_category,
            )
        except (sqlite3.Error, RuntimeError):
            logger.exception(
                "Prompt guidance delivery outcome could not be persisted for %s; "
                "claim remains delivery-unknown",
                incident["worker_id"],
            )
            delivery_unknown = True
        return {
            "worker_id": incident["worker_id"],
            "replayed": False,
            "delivered": delivered,
            "delivery_unknown": delivery_unknown,
        }

    def poll_slack_commands(self):
        """Drain and process Slack commands."""
        if not self.socket_handler:
            return
        items = self.socket_handler.drain()
        # Operator re-engaged: clear any "waiting on you" state. If the Brain is still
        # genuinely holding, it re-emits next cycle and re-sets it (self-healing).
        # R6: this clear stays — it is the one place the retained alert markers are
        # released. It is no longer an alert-spam source: R1 removed the ~30-min
        # restart loop that re-fired the alert 21x, and the marker now survives the
        # TTL prune, so a still-pending directive alerts exactly once until the
        # operator re-engages here.
        if items:
            # I2: clear on ANY operator input, even if a TTL prune already emptied
            # _operator_waits — otherwise the retained alert marker suppresses a
            # legitimate re-alert after re-engagement. Clearing empty dicts is a no-op.
            had_state = bool(self._operator_waits or self._operator_wait_alerted)
            self._operator_waits.clear()
            self._operator_wait_alerted.clear()
            if had_state:
                logger.info("operator_waits cleared — operator re-engaged via Slack")
        for item in items:
            # Check for directive confirmation before command parsing
            raw_text = item.get("original_text", "").strip()
            if self._handle_directive_confirmation(raw_text):
                continue
            # Check for reaction events
            if item.get("type") == "reaction":
                logger.info("routing reaction: emoji=%r ts=%r", item["emoji"], item["message_ts"])
                if not self._handle_directive_reaction(item["emoji"], item["message_ts"]):
                    self._handle_push_reaction(item["emoji"], item["message_ts"])
                continue
            # Check for plugin event types
            if "parsed" not in item and item.get("type"):
                if self.plugin_registry.handle_event(self, item):
                    continue
            parsed = item["parsed"]
            cmd_type = parsed["type"]
            logger.info(f"Slack command: {item.get('original_text', cmd_type)}")

            if cmd_type == "help":
                self.slack.post_message(format_help_text())
            elif cmd_type == "status":
                self._handle_status()
            elif cmd_type == "stop":
                self.slack.post_message("Stopping all work and shutting down.")
                self.shutdown()
            elif cmd_type == "pause":
                self._paused = True
                self.slack.post_message("Paused. No new work will be started.")
            elif cmd_type == "resume":
                self._paused = False
                self.slack.post_message("Resumed.")
            elif cmd_type == "login":
                try:
                    r = self._auth_relay.start()
                    if r["state"] == "busy":
                        self.slack.post_message("A sign-in is already in progress. If it showed a code, reply `login code <the-code>`.")
                    else:
                        self.slack.post_message("Starting sign-in — I'll post the link here in a moment.")
                except Exception as exc:   # spawn failure must still be reported (principle #3)
                    self.slack.post_message(f"Couldn't start sign-in: {exc}")
            elif cmd_type == "login_code":
                res = self._auth_relay.submit_code(parsed["code"])
                if res == "sent":
                    self.slack.post_message("Code submitted — finishing sign-in…")
                elif res == "idle":
                    self.slack.post_message("No sign-in is in progress. Send `login` first.")
                else:
                    self.slack.post_message("Couldn't submit the code — the sign-in may have ended. Send `login` to retry.")
            elif cmd_type == "message":
                text = parsed.get("text", "")
                msg_ts = item.get("ts", "")
                if msg_ts:
                    self.slack.add_reaction("eyes", msg_ts)
                guidance = self._route_operator_prompt_guidance(text, msg_ts)
                if guidance is None:
                    self.brain.send_message(
                        f"OPERATOR MESSAGE (ts={msg_ts}): {text}"
                    )
                    self.slack.post_message(
                        f"Forwarded to brain: {text}", thread_ts=msg_ts or None
                    )
                elif guidance.get("held"):
                    worker = guidance.get("worker_id")
                    target = f" for `{worker}`" if worker else ""
                    self.slack.post_message(
                        f"Guidance routing{target} held: durable prompt state is "
                        "unavailable; no Brain dispatch.",
                        thread_ts=msg_ts or None,
                    )
                elif guidance["replayed"]:
                    self.slack.post_message(
                        f"Guidance for `{guidance['worker_id']}` was already recorded; "
                        "no duplicate Brain dispatch.",
                        thread_ts=msg_ts or None,
                    )
                elif guidance.get("delivery_unknown"):
                    self.slack.post_message(
                        f"Guidance for `{guidance['worker_id']}` reached the Brain, "
                        "but durable delivery recording failed; automatic replay is held.",
                        thread_ts=msg_ts or None,
                    )
                elif guidance["delivered"]:
                    self.slack.post_message(
                        f"Forwarded guidance for `{guidance['worker_id']}` to brain: {text}",
                        thread_ts=msg_ts or None,
                    )
                else:
                    self.slack.post_message(
                        f"Guidance for `{guidance['worker_id']}` recorded; Brain delivery held.",
                        thread_ts=msg_ts or None,
                    )
            elif cmd_type == "detail":
                self._handle_detail(parsed)
            elif cmd_type == "log":
                self._handle_log(parsed)
            elif cmd_type == "objective":
                text = parsed.get("text", "")
                obj_id = self.registry.create_objective(text)
                self.slack.post_message(format_objective_received(text))
                self.brain.send_message(f"NEW OBJECTIVE: {text}")
                self.registry.log_event("objective_received", details={"text": text, "id": obj_id})
            elif cmd_type == "approve":
                worker_id = parsed.get("target", "")
                worker, reason = self._validate_worker_plan_target(worker_id)
                if not worker:
                    self.slack.post_message(f"Approval not queued for `{worker_id}`: {reason}")
                    continue
                write_decision(self._decisions_dir, {"action": "approve_plan", "worker_id": worker_id})
                self.slack.post_message(f"Approval queued for `{worker_id}`.")
            elif cmd_type == "reject":
                worker_id = parsed.get("target", "")
                worker, reason = self._validate_worker_plan_target(worker_id)
                if not worker:
                    self.slack.post_message(f"Rejection not queued for `{worker_id}`: {reason}")
                    continue
                write_decision(self._decisions_dir, {"action": "reject_plan", "worker_id": worker_id, "reason": "User rejected via Slack"})
                self.slack.post_message(f"Rejection queued for `{worker_id}`.")
            elif cmd_type == "summary":
                self._handle_summary()
            elif cmd_type == "audit":
                self._handle_audit()
            elif cmd_type == "provider":
                self._handle_provider_command(parsed.get("args"))
            elif self.plugin_registry.handle_command(self, cmd_type, parsed):
                pass  # handled by plugin
            else:
                self.slack.post_message(f"Command `{cmd_type}` acknowledged (not yet implemented).")

        # Advance the /login relay once per cycle (non-blocking). Restart ONLY on a verified success.
        ev = self._auth_relay.tick()
        if ev is not None:
            st = ev["state"]
            if st == "url":
                self.slack.post_message(f"Open this to sign in, then I'll switch over:\n{ev['url']}\n(If it shows a code, reply `login code <the-code>`.)")
            elif st == "already_logged_in":
                self.slack.post_message(f"Already signed in as {ev.get('account') or 'the current account'}. Nothing to switch.")
            elif st == "success":
                self.slack.post_message(f"Signed in as {ev['account']}. Restarting onto the new credential…")
                self.slack.flush_queue()   # deliver the notice before execvp discards the in-memory queue (review M5)
                os.kill(os.getpid(), signal.SIGHUP)   # verify-gated: reached ONLY when status confirmed the account
            elif st == "verify_failed":
                self.slack.post_message("Signed in, but I couldn't confirm the account after retries — the switch may or may not have completed. Not restarting; send `login` again to be sure.")
            elif st == "timeout":
                self.slack.post_message("Sign-in timed out — no change; the previous account is still active.")
            elif st == "error":
                self.slack.post_message(f"Sign-in didn't complete: {ev.get('detail','')[:300]} — previous account unchanged.")
            elif st == "waiting":
                self.slack.post_message("Still completing sign-in…")
            elif st == "needs_code":
                self.slack.post_message("Sign-in needs a new code — reply `login code <the-code>` with just the code (no extra text).")

    def _handle_provider_command(self, args) -> None:
        """Show or set provider routing.

        Routed-role sets are rejected unless the client is in the role's configured
        `clients` list and globally enabled — ProviderRouter falls back to `preferred`
        otherwise. Brain is a direct sticky construction path, so its explicit selection
        requires only a globally enabled client.
        """
        from ironclaude.provider_config import CLIENT_NAMES, ROLE_NAMES
        from ironclaude.provider_state import ProviderState

        usage = "Usage: `/ironclaude provider` (status) or `/ironclaude provider <role> <client>`."
        args = list(args or [])
        if len(args) not in (0, 2):
            self.slack.post_message(usage)
            return

        providers = (self.config or {}).get("providers", {})
        roles = providers.get("roles", {})
        clients = providers.get("clients", {})

        if not args:
            try:
                state = ProviderState(self._db)
                lines = ["*Provider routing:*"]
                for name in ROLE_NAMES:
                    cfg = roles.get(name, {})
                    preferred = cfg.get("preferred", "?")
                    allowed_list = cfg.get("clients", [])
                    allowed = ", ".join(allowed_list) or "-"
                    sticky = state.get_current_client(name)
                    # Brain construction treats its persisted choice as authoritative even
                    # after config drift; other roles retain ProviderRouter semantics.
                    sticky_is_effective = (
                        sticky in CLIENT_NAMES
                        and (name == "brain" or sticky in allowed_list)
                    )
                    effective = sticky if sticky_is_effective else preferred
                    note = ""
                    if sticky and not sticky_is_effective:
                        note = f" — stored `{sticky}` is ignored (not in clients)"
                    if name in ("worker", "grader") and effective in CLIENT_NAMES:
                        unavailable = state.unavailable_capabilities(effective, name)
                        if unavailable:
                            scopes = ", ".join(
                                f"{item['host']}/{item['tier']}"
                                for item in unavailable
                            )
                            note += (
                                f" — selected client quarantined at {scopes}; "
                                "routing may use fallback"
                            )
                    if name == "brain":
                        from ironclaude.codex_brain_client import CodexBrainClient
                        active = (
                            "codex"
                            if isinstance(self.brain, CodexBrainClient)
                            else "claude"
                        )
                        if active != effective:
                            note += f" — active: *{active}*; restart pending"
                    lines.append(
                        f"• `{name}` — current: *{effective}* (preferred: {preferred}; clients: {allowed}){note}"
                    )
                enabled = [c for c in CLIENT_NAMES if clients.get(c, {}).get("enabled")]
                lines.append(f"\nEnabled clients: {', '.join(enabled) or 'none'}")
                lines.append(usage)
                self.slack.post_message("\n".join(lines))
            except Exception as e:
                self.slack.post_message(f"Couldn't read provider routing: {e}")
            return

        role, client = args
        if role not in ROLE_NAMES:
            self.slack.post_message(f"Unknown role `{role}`. Valid roles: {', '.join(ROLE_NAMES)}.")
            return
        if client not in CLIENT_NAMES:
            self.slack.post_message(f"Unknown client `{client}`. Valid clients: {', '.join(CLIENT_NAMES)}.")
            return
        allowed = roles.get(role, {}).get("clients", [])
        if role != "brain" and client not in allowed:
            # NOTE: adding a client to a role's `clients` WITHOUT also enabling it makes
            # provider_config reject the config at startup (load_config validates unguarded),
            # so the guidance must name both edits.
            self.slack.post_message(
                f"`{client}` is not in `{role}`'s clients list ({', '.join(allowed) or 'none'}) — "
                f"the router would ignore it. Add it to providers.roles.{role}.clients AND set "
                f"providers.clients.{client}.enabled=true (both, or the daemon won't start), then restart."
            )
            return
        if not clients.get(client, {}).get("enabled"):
            self.slack.post_message(
                f"Client `{client}` is disabled — enable providers.clients.{client}.enabled first."
            )
            return

        try:
            state = ProviderState(self._db)
            if role in ("worker", "grader"):
                state.set_current_client(
                    role, client, reset_capabilities=True,
                )
            else:
                state.set_current_client(role, client)
        except Exception as e:
            self.slack.post_message(f"Failed to set provider for `{role}`: {e}")
            return
        if role == "brain":
            self.slack.post_message(
                f"`brain` selected *{client}*. Restarting to activate it."
            )
            self.slack.flush_queue()
            os.kill(os.getpid(), signal.SIGHUP)
            return
        if role in ("worker", "grader"):
            self.slack.post_message(
                f"`{role}` selected *{client}*. Capabilities will be rechecked "
                "on next use; existing fallback rules still apply."
            )
            return
        self.slack.post_message(f"`{role}` now routes to *{client}*.")

    def _handle_directive_confirmation(self, text: str) -> bool:
        """Check if text is a yes/no reply to a pending directive confirmation.

        Returns True if handled (caller should skip normal processing).
        """
        if self._db is None:
            return False
        normalized = text.strip().lower()
        if normalized not in ("yes", "no", "changes", "change", "revise"):
            return False
        row = self._db.execute(
            "SELECT id, interpretation, interpretation_ts FROM directives "
            "WHERE status='pending_confirmation' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        directive_id = row[0]
        interpretation = row[1]
        terminal = normalized in ("yes", "no")
        if normalized == "yes":
            self._db.execute(
                "UPDATE directives SET status='confirmed', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} confirmed: {interpretation}")
            operator = self.config.get("operator_name", "Operator")
            self.brain.send_message(f"Directive #{directive_id} confirmed by {operator}: {interpretation}")
        elif normalized in ("changes", "change", "revise"):
            self._db.execute(
                "UPDATE directives SET status='awaiting_changes', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self._notify_awaiting_changes(directive_id)
        else:
            self._db.execute(
                "UPDATE directives SET status='rejected', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} rejected.")
        if terminal and row[2]:
            # The changes/revise branch keeps the interpretation pinned — the
            # operator is about to reference it while composing feedback (mirrors
            # the reaction-based 🤔 path, which also leaves it pinned).
            self.slack.unpin_message(row[2])
        return True

    def _match_directive_by_content(self, message_text: str) -> tuple | None:
        """Match message text to a pending directive by content.

        Strategies in order:
        1. Regex for 'Directive #N' — look up by ID
        2. Check if text contains a pending directive's interpretation
        3. Check if text matches a pending directive's source_text

        Returns (id, interpretation, status) — the same 3-column shape as the
        fast-path timestamp match in _handle_directive_reaction, so callers
        don't need a second query to learn the status.
        """
        # Strategy 1: Directive ID reference
        match = re.search(r"Directive\s*#(\d+)", message_text, re.IGNORECASE)
        if match:
            directive_id = int(match.group(1))
            row = self._db.execute(
                "SELECT id, interpretation, status FROM directives "
                "WHERE id=? AND status IN "
                "('pending_confirmation','in_progress','awaiting_changes','superseded') LIMIT 1",
                (directive_id,),
            ).fetchone()
            if row:
                return row

        # Strategy 2: Interpretation text match
        pending = self._db.execute(
            "SELECT id, interpretation, status FROM directives "
            "WHERE status IN "
            "('pending_confirmation','in_progress','awaiting_changes','superseded')"
        ).fetchall()
        for row in pending:
            if row[1] in message_text:
                return row

        # Strategy 3: Source text match
        pending_sources = self._db.execute(
            "SELECT id, interpretation, status, source_text FROM directives "
            "WHERE status IN "
            "('pending_confirmation','in_progress','awaiting_changes','superseded')"
        ).fetchall()
        for row in pending_sources:
            if row[3] in message_text:
                return (row[0], row[1], row[2])

        return None

    def _notify_awaiting_changes(self, directive_id: int) -> None:
        """Single source of truth for the awaiting-changes notification pair.

        The brain rules key on the EXACT wire-protocol phrasing below — both
        entry paths (🤔 reaction and 'changes' text reply) MUST emit identical
        text, or the brain recognizes the revision prompt from one path but
        not the other.
        """
        self.slack.post_message(f"Directive #{directive_id} → waiting for your feedback...")
        operator = self.config.get("operator_name", "Operator")
        self.brain.send_message(
            f"Directive #{directive_id} needs revision by {operator}. "
            f"Their next Slack message is the requested change. "
            f"Once they send it, re-submit this directive by calling submit_directive "
            f"with supersedes={directive_id} and the updated interpretation + planned fields."
        )

    def _walk_to_chain_head(self, start_id: int, cap: int = 20) -> int:
        """Walk the superseded_by chain to the head (row where superseded_by IS NULL).
        Returns start_id if that row is already the head. Caps at `cap` hops as a
        safety valve — if the loop doesn't terminate (cyclic chain, corruption),
        log a warning and return the last valid id we followed.

        Load-bearing for the 🤔 revision workflow: after N rounds, the chain has
        N+1 rows, and code that assumes at most one hop points the operator (or
        the brain) at another already-stale row.
        """
        if self._db is None:
            return start_id
        current = start_id
        prev = start_id
        for _ in range(cap):
            row = self._db.execute(
                "SELECT superseded_by FROM directives WHERE id=?",
                (current,),
            ).fetchone()
            if row is None:
                if current == start_id:
                    # Caller passed an id that doesn't exist at all —
                    # nothing better to return than what we were given.
                    return start_id
                # Dangling superseded_by pointer: #prev points at a
                # nonexistent #current. Warn like the cyclic-cap case and
                # return the last EXISTING id so the operator is never
                # pointed at a row that isn't there.
                logger.warning(
                    "_walk_to_chain_head: dangling superseded_by pointer — "
                    "#%s points at nonexistent #%s; returning #%s",
                    prev, current, prev,
                )
                try:
                    self.slack.post_message(
                        f"⚠️ Supersession chain has a dangling pointer: "
                        f"#{prev} points at nonexistent #{current} — possible "
                        f"data corruption; check the directives table."
                    )
                except Exception:
                    logger.debug(
                        "Failed to post dangling-pointer warning to Slack",
                        exc_info=True,
                    )
                return prev
            next_id = row[0]
            if next_id is None:
                return current  # head of chain
            prev = current
            current = int(next_id)
        logger.warning(
            "_walk_to_chain_head cap (%s) reached starting at #%s; "
            "abandoning walk (possible cyclic supersession chain)",
            cap, start_id,
        )
        try:
            self.slack.post_message(
                f"⚠️ Supersession chain walk hit the safety cap starting at "
                f"#{start_id} — possible data corruption; check the directives table."
            )
        except Exception:
            logger.debug("Failed to post chain-walk cap warning to Slack", exc_info=True)
        # Return start_id, not the mid-cycle node the loop happened to stop
        # on — the caller's head_id == directive_id corruption fallback then
        # fires instead of pointing the operator at another broken row.
        return start_id

    def _handle_directive_reaction(self, emoji: str, message_ts: str) -> bool:
        """Handle a reaction on any directive-related message.

        Tries fast-path timestamp match first, then falls back to content-based
        matching by fetching the reacted-to message text and searching for
        directive references.

        Returns True if handled (matched a pending directive).
        """
        if self._db is None:
            return False
        logger.info("_handle_directive_reaction: emoji=%r ts=%r", emoji, message_ts)
        if emoji not in (
            "thumbsup", "+1", "thumbs_up", "thumbsdown", "-1", "thumbs_down",
            "thinking_face", "thinking",
        ):
            logger.debug("reaction emoji %r not in accepted set, ignoring", emoji)
            return False

        # Fast path: match on interpretation_ts (bot's message) or source_ts (operator's message)
        # ORDER BY prefers the chain head (superseded_by IS NULL) over stale
        # rows sharing the same source_ts after a supersession; ties broken
        # by newest id.
        row = self._db.execute(
            "SELECT id, interpretation, status FROM directives "
            "WHERE (interpretation_ts=? OR source_ts=?) "
            "AND status IN ('pending_confirmation','in_progress','awaiting_changes','superseded') "
            "ORDER BY (superseded_by IS NULL) DESC, id DESC LIMIT 1",
            (message_ts, message_ts),
        ).fetchone()

        if row is not None:
            logger.info("Fast-path matched directive #%d for reaction ts=%r", row[0], message_ts)

        # Fallback: content-based matching
        if row is None and self.slack is not None:
            message_text = self.slack.get_message(message_ts)
            if message_text:
                row = self._match_directive_by_content(message_text)
                if row:
                    logger.info(
                        "Content-based match: directive #%d matched via message text", row[0]
                    )

        if row is None:
            pending = self._db.execute(
                "SELECT id, interpretation_ts, source_ts FROM directives "
                "WHERE status IN ('pending_confirmation','in_progress')"
            ).fetchall()
            pending_info = [(r[0], r[1], r[2]) for r in pending] if pending else []
            logger.warning(
                "No directive matched for reaction ts=%r. Pending directives (id, interpretation_ts, source_ts): %r",
                message_ts, pending_info,
            )
            return False

        directive_id = row[0]
        interpretation = row[1]
        # Both the fast path and the content fallback return
        # (id, interpretation, status) — no separate status re-query needed.
        status = row[2]
        if status == "superseded":
            head_id = self._walk_to_chain_head(directive_id)
            if head_id != directive_id:
                self.slack.post_message(
                    f"Directive #{directive_id} is superseded by #{head_id}; "
                    f"please react on the newer message."
                )
            else:
                # Row claims 'superseded' but chain-walk finds no successor —
                # data corruption. Post a fallback message without a specific id.
                self.slack.post_message(
                    f"Directive #{directive_id} is superseded; please react "
                    f"on the current head directive."
                )
            return True  # We handled the reaction (by rejecting), so don't fall through

        ts_row = self._db.execute(
            "SELECT interpretation_ts FROM directives WHERE id=?", (directive_id,)
        ).fetchone()
        interpretation_ts = ts_row[0] if ts_row else None
        if emoji in ("thinking_face", "thinking"):
            self._db.execute(
                "UPDATE directives SET status='awaiting_changes', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self._notify_awaiting_changes(directive_id)
            source_ts = self._db.execute(
                "SELECT source_ts FROM directives WHERE id=?", (directive_id,)
            ).fetchone()[0]
            self.slack.remove_reaction("hourglass_flowing_sand", source_ts)
            self.slack.add_reaction("thinking_face", source_ts)
            logger.info("Directive #%d awaiting_changes via reaction %r", directive_id, emoji)
            return True
        if emoji in ("thumbsup", "+1", "thumbs_up"):
            self._db.execute(
                "UPDATE directives SET status='confirmed', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            operator = self.config.get("operator_name", "Operator")
            if status == "awaiting_changes":
                self.slack.post_message(
                    f"Directive #{directive_id} confirmed (cancelling the pending "
                    f"change request): {interpretation}"
                )
                delivered = self.brain.send_message(
                    f"Directive #{directive_id} confirmed by {operator} — the earlier "
                    f"change request is cancelled; do NOT wait for feedback. "
                    f"Interpretation: {interpretation}"
                )
            else:
                self.slack.post_message(f"Directive #{directive_id} confirmed: {interpretation}")
                delivered = self.brain.send_message(
                    f"Directive #{directive_id} confirmed by {operator}: {interpretation}"
                )
            if delivered:
                self._directive_reminder_sent[directive_id] = time.time()
            else:
                logger.warning(
                    "Brain unreachable; directive #%d confirmation will retry via check_confirmed_directives",
                    directive_id,
                )
            source_ts = self._db.execute(
                "SELECT source_ts FROM directives WHERE id=?", (directive_id,)
            ).fetchone()[0]
            self.slack.remove_reaction("hourglass_flowing_sand", source_ts)
            self.slack.add_reaction(DIRECTIVE_STATUS_EMOJI.get("confirmed", "thumbsup"), source_ts)
        else:
            self._db.execute(
                "UPDATE directives SET status='rejected', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            if status == "awaiting_changes":
                self.slack.post_message(
                    f"Directive #{directive_id} rejected (cancelling the pending "
                    f"change request)."
                )
                # The brain was told "their next Slack message is the requested
                # change" when the operator reacted 🤔 — it must be told to
                # stand down, or it will misinterpret the operator's next
                # message as feedback for this rejected directive. Normal
                # rejects stay brain-silent (pre-existing design; brain
                # discovers them via status polling).
                operator = self.config.get("operator_name", "Operator")
                self.brain.send_message(
                    f"Directive #{directive_id} rejected by {operator} — the "
                    f"earlier change request is cancelled; do NOT wait for "
                    f"feedback."
                )
            else:
                self.slack.post_message(f"Directive #{directive_id} rejected.")
            source_ts = self._db.execute(
                "SELECT source_ts FROM directives WHERE id=?", (directive_id,)
            ).fetchone()[0]
            self.slack.remove_reaction("hourglass_flowing_sand", source_ts)
            self.slack.add_reaction(DIRECTIVE_STATUS_EMOJI.get("rejected", "x"), source_ts)
        if interpretation_ts:
            self.slack.unpin_message(interpretation_ts)
        new_status = "confirmed" if emoji in ("thumbsup", "+1", "thumbs_up") else "rejected"
        logger.info("Directive #%d %s via reaction %r", directive_id, new_status, emoji)
        return True

    def _handle_push_reaction(self, emoji: str, message_ts: str) -> bool:
        """Handle ✅/❌ reaction on a push request confirmation message.

        Returns True if the reaction matched a push request (handled), False otherwise.
        """
        if self._db is None:
            return False
        if emoji not in ("white_check_mark", "x"):
            return False

        row = self._db.execute(
            "SELECT id, repo, remote, branch FROM push_requests"
            " WHERE message_ts=? AND status='pending' LIMIT 1",
            (message_ts,),
        ).fetchone()
        if row is None:
            return False

        push_id, repo, remote, branch = row[0], row[1], row[2], row[3]

        expired = self._db.execute(
            "SELECT COUNT(*) FROM push_requests WHERE id=? AND expires_at < datetime('now')",
            (push_id,),
        ).fetchone()[0]
        if expired:
            self._db.execute(
                "UPDATE push_requests SET status='expired' WHERE id=?", (push_id,)
            )
            self._db.commit()
            self.slack.post_message(f"Push request `{push_id[:8]}` expired before confirmation.")
            self.slack.unpin_message(message_ts)
            logger.info("Push %s expired — no push executed", push_id[:8])
            return True

        if emoji == "x":
            self._db.execute(
                "UPDATE push_requests SET status='rejected' WHERE id=?", (push_id,)
            )
            self._db.commit()
            self.slack.post_message(f"Push request `{push_id[:8]}` rejected.")
            self.slack.unpin_message(message_ts)
            self.brain.send_message(f"Push {push_id[:8]} was rejected by the operator.")
            logger.info("Push %s rejected", push_id[:8])
            return True

        result = subprocess.run(
            ["git", "push", remote, branch],
            cwd=repo, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            self._db.execute(
                "UPDATE push_requests SET status='completed' WHERE id=?", (push_id,)
            )
            self._db.commit()
            self.slack.post_message(
                f"Push request `{push_id[:8]}` completed: `{remote}/{branch}` pushed successfully."
            )
            self.brain.send_message(
                f"Push {push_id[:8]} completed: {remote}/{branch} pushed successfully."
            )
            logger.info("Push %s completed: %s/%s", push_id[:8], remote, branch)
        else:
            self._db.execute(
                "UPDATE push_requests SET status='failed' WHERE id=?", (push_id,)
            )
            self._db.commit()
            err = result.stderr.strip()
            self.slack.post_message(f"Push request `{push_id[:8]}` failed: {err}")
            self.brain.send_message(f"Push {push_id[:8]} failed: {err}")
            logger.warning("Push %s failed: %s", push_id[:8], err)
        self.slack.unpin_message(message_ts)
        return True

    @staticmethod
    def _is_lock_error(exc) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc))

    def _db_rollback_quietly(self) -> None:
        try:
            self._db.rollback()
        except sqlite3.Error as exc:
            logger.warning("db rollback failed: %s", exc)

    def _db_write_with_retry(self, op, *, attempts: int = 3, backoff: float = 0.1):
        """Run a self._db write op with rollback-based self-heal on lock errors.

        Never recreates/reassigns self._db (WorkerRegistry and OrchestratorTools
        cache the connection object) — self-heal is rollback-only.
        """
        for i in range(attempts):
            try:
                return op()
            except Exception as exc:
                self._db_rollback_quietly()
                if not self._is_lock_error(exc) or i == attempts - 1:
                    raise
                time.sleep(backoff * (i + 1))

    def _sweep_expired_push_requests(self):
        """Mark pending push requests past their TTL as expired."""
        if self._db is None:
            return

        def _write():
            rows = self._db.execute(
                "SELECT message_ts FROM push_requests"
                " WHERE status='pending' AND expires_at < datetime('now')"
            ).fetchall()
            self._db.execute(
                "UPDATE push_requests SET status='expired'"
                " WHERE status='pending' AND expires_at < datetime('now')"
            )
            self._db.commit()
            return rows

        try:
            rows = self._db_write_with_retry(_write)
            for row in rows:
                if row[0]:
                    self.slack.unpin_message(row[0])
        except sqlite3.OperationalError as e:
            logger.warning("push_requests sweep skipped (db locked): %s", e)

    def _prune_operator_waits(self, now: float) -> None:
        """Drop awaiting-operator entries the Brain has stopped re-affirming (TTL backstop)."""
        stale = [
            wid for wid, info in self._operator_waits.items()
            if now - info.get("updated_at", now) > _OPERATOR_WAIT_TTL_SECONDS
        ]
        for wid in stale:
            self._operator_waits.pop(wid, None)
            # R6: retain the alerted marker across the TTL prune. Dropping it here
            # let a still-pending directive re-alert once its wait entry aged out;
            # the marker is bounded by worker count and cleared on operator
            # re-engagement (poll_slack_commands) and on overflow.

    def _prune_brain_waits(self, now: float) -> None:
        """Drop brain-wait entries the Brain has stopped re-affirming (TTL backstop)."""
        stale = [
            wid for wid, info in self._brain_waits.items()
            if now - info.get("updated_at", now) > _OPERATOR_WAIT_TTL_SECONDS
        ]
        for wid in stale:
            self._brain_waits.pop(wid, None)

    def _grade_bounded(self, system: str, user: str, schema, timeout: float | None = None):
        """R3: run a classifier grade off the poll thread with a hard wall-clock
        bound so an empty-Ollama stall (up to 339s observed) cannot block the
        operator fast lane. LocalGrader's per-call read_timeout bounds the
        client's own read timeout to `timeout`, so an abandoned grade's daemon
        thread ends near that bound instead of the full inference read timeout
        (e.g. 600s); offload + join still caps how long THIS call blocks.
        Returns the grade dict, or None on timeout/error (the caller treats None
        as 'do not capture'). A grade that overruns the bound is abandoned to its
        daemon thread and discarded when it finishes."""
        if timeout is None:
            timeout = self.config.get("fast_lane_grade_timeout_seconds", 5)
        # FIX 2: only one bounded grade may be in flight. If a prior grade is still
        # running (including one abandoned past its timeout), fail fast to None
        # rather than spawn another daemon thread against the shared grader.
        with self._grade_state_lock:
            if self._grade_in_flight:
                logger.warning("bounded grade skipped: a prior grade is still in flight")
                return None
            self._grade_in_flight = True
        box: dict = {}

        def _worker():
            try:
                box["r"] = self._grader.grade(system, user, schema, read_timeout=timeout)
            except Exception as e:  # noqa: BLE001 — surfaced as None, matches prior catch
                box["e"] = e
            finally:
                with self._grade_state_lock:
                    self._grade_in_flight = False

        t = threading.Thread(target=_worker, daemon=True)
        try:
            t.start()
        except Exception as e:  # noqa: BLE001 — thread creation failed; do not leak the flag
            with self._grade_state_lock:
                self._grade_in_flight = False
            logger.warning("bounded grade thread failed to start: %s — not capturing", e)
            return None
        t.join(timeout)
        if t.is_alive():
            logger.warning(
                "awaiting-operator grade exceeded %ss bound; skipping capture (offloaded)",
                timeout,
            )
            return None
        if "e" in box:
            logger.warning("awaiting-operator classify failed: %s — not capturing", box["e"])
            return None
        return box.get("r")

    def _maybe_capture_operator_wait(self, text: str) -> bool:
        """If the Brain message reports it is waiting on the operator or on the Brain
        itself, record structured state + (for operator waits) post a one-time alert.
        Returns True when captured — the caller must then NOT post it as a normal Brain
        message and NOT send CONTEXT_REQUIRED (preserving the loop-break). Fail-safe: any
        classifier error/uncertainty -> not captured."""
        if not _AWAITING_PHRASE_RE.search(text):
            return False
        if _NOT_AWAITING_RE.search(text):
            return False
        # R3: bounded/offloaded so a stalled grader cannot block the operator fast lane.
        result = self._grade_bounded(
            _AWAITING_OP_SYSTEM, f"Classify this Brain message:\n{truncate_middle(text)}", _AWAITING_OP_SCHEMA
        )
        if (not isinstance(result, dict)) or result.get("infrastructure_error"):
            return False
        waiting_on = result.get("waiting_on")
        if waiting_on not in ("operator", "brain"):
            return False
        worker_id = (str(result.get("worker_id") or "").strip()) or "brain"
        question = str(result.get("question") or "").strip()
        now = time.time()
        if waiting_on == "brain":
            self._prune_brain_waits(now)
            self._brain_waits[worker_id] = {"question": question, "updated_at": now}
            if len(self._brain_waits) > _OPERATOR_WAIT_MAX:
                oldest = min(self._brain_waits, key=lambda k: self._brain_waits[k]["updated_at"])
                self._brain_waits.pop(oldest, None)
            logger.info("brain_wait recorded for %s: %s", worker_id, question[:80])
            return True
        self._prune_operator_waits(now)
        self._operator_waits[worker_id] = {"question": question, "updated_at": now}
        if len(self._operator_waits) > _OPERATOR_WAIT_MAX:
            oldest = min(self._operator_waits, key=lambda k: self._operator_waits[k]["updated_at"])
            self._operator_waits.pop(oldest, None)
            self._operator_wait_alerted.pop(oldest, None)
        # R6: alert once per PENDING DIRECTIVE, not once per sweep or paraphrase.
        directive_id = _extract_directive_id(text)
        # (1) If the directive already sits in pending_confirmation, the heartbeat's
        # deterministic pending-confirmation surface already shows it — skip the
        # duplicate one-time alert (the wait itself is still recorded above).
        if directive_id is not None and f"d{directive_id}" in self._get_pending_confirmation_waits():
            logger.info(
                "operator_wait alert suppressed: d%s already pending_confirmation", directive_id
            )
            logger.info("operator_wait recorded for %s: %s", worker_id, question[:80])
            return True
        # (2) Alert key is (worker, directive, directive-status): a paraphrased
        # question for the same pending directive does NOT re-alert; a status change
        # (e.g. -> blocked) does. The marker survives the TTL prune (see
        # _prune_operator_waits) so a still-pending wait is not re-alerted.
        status = None
        if directive_id is not None and self._db is not None:
            try:
                row = self._db.execute(
                    "SELECT status FROM directives WHERE id=?", (directive_id,)
                ).fetchone()
                status = row[0] if row else None
            except Exception as e:
                logger.warning("directive status lookup failed for d%s: %s", directive_id, e)
        # With a directive id, the (worker, directive, status) key intentionally
        # suppresses a paraphrased re-ask of the SAME directive. Without one, fall
        # back to the question text so two DISTINCT questions from the same worker
        # do not collapse into a single alert.
        alert_key = (
            (worker_id, directive_id, status)
            if directive_id is not None
            else (worker_id, None, question.strip())
        )
        if self._operator_wait_alerted.get(worker_id) != alert_key:
            self._operator_wait_alerted[worker_id] = alert_key
            operator_name = self.config.get("operator_name", "Operator")
            alert_body = f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
            ts = self.slack.post_message(alert_body)
            candidate = self._last_brain_context
            if ts and candidate and worker_id != "brain":
                context_ts, context_text = candidate
                worker_token = re.compile(
                    rf"(?<![A-Za-z0-9_-]){re.escape(worker_id)}(?![A-Za-z0-9_-])"
                )
                if worker_token.search(context_text):
                    permalink = self.slack.get_permalink(context_ts)
                    if permalink:
                        self.slack.update_message(ts, f"{self.slack.prefix}{alert_body}\nLink: {permalink}")
        logger.info("operator_wait recorded for %s: %s", worker_id, question[:80])
        return True

    def _post_brain_message(self, text: str, thread_ts: str | None = None) -> str | None:
        """Post a Brain message to Slack as `*Brain:* ...`, chunked to stay under Slack's
        ~40000-char per-message limit; every chunk shares thread_ts. Returns the ts of the
        first SUCCESSFULLY posted chunk ONLY when every chunk landed; returns None if any
        chunk failed (all-chunks-delivered semantics) so callers can gate a follow-up
        action (e.g. the answered ✅) on FULL delivery. Failed chunks are enqueued for
        retry by SlackBot.post_message (they will retry-deliver threaded via flush_queue).
        Returns None (no post) for empty/whitespace-only text."""
        if not text.strip():
            return None
        first_ts: str | None = None
        all_ok = True
        for i in range(0, len(text), _BRAIN_POST_CHUNK):
            chunk = text[i:i + _BRAIN_POST_CHUNK]
            ts = self.slack.post_message(f"*Brain:* {chunk}", thread_ts=thread_ts)
            if first_ts is None and ts is not None:
                first_ts = ts
            if ts is None:
                all_ok = False
        if all_ok and first_ts is not None and thread_ts is None:
            self._last_brain_context = (first_ts, text)
        return first_ts if all_ok else None

    def poll_brain_responses(self):
        """Drain brain responses, validate context, and post to Slack."""
        for text in self.brain.get_pending_responses():
            logger.info(f"Brain response: {text[:100]}...")
            # R1: the Brain's reply to an idle [PING] health probe is a liveness
            # ack only — never relay it to Slack. Tolerate Brain non-compliance:
            # strip the narration prefix, then an optional leading [reply-to:...]
            # marker the Brain may thread onto the ack (R2 encourages threading),
            # then drop when the residual leads with the ack token. The len bound
            # keeps a genuine narration that merely mentions the token from being
            # dropped.
            _probe_body = text[len(_NARRATION_PREFIX):] if text.startswith(_NARRATION_PREFIX) else text
            if "[PING-ACK]" in _probe_body:
                _probe_parsed = parse_reply_to_marker(_probe_body.strip())
                _probe_core = _probe_parsed[0] if _probe_parsed else _probe_body.strip()
                if _probe_core.startswith("[PING-ACK]") and len(_probe_core) <= 60:
                    logger.debug("Brain [PING-ACK] received; not relayed")
                    continue
            # Usage-limit surfacing runs BEFORE the operator-wait continue so a limit
            # message that also reads as "waiting" still prompts the operator to /login.
            limit = detect_account_limit(text)
            if limit:
                now = time.time()
                # prune expired keys so the map can't grow unbounded over the daemon's life (review M3)
                self._limit_alerted = {k: t for k, t in self._limit_alerted.items() if now - t <= _LIMIT_COOLDOWN_S}
                last = self._limit_alerted.get(limit)
                if last is None or now - last > _LIMIT_COOLDOWN_S:
                    self._limit_alerted[limit] = now
                    self.slack.post_message(f"⚠️ Usage limit hit ({limit}). Send `login` to switch accounts.")
            # R2: the Brain prepends _NARRATION_PREFIX to ALL its text
            # (brain_client.py:848), which defeats parse_reply_to_marker's leading
            # [reply-to: check. Strip the prefix ONLY when the text is marker-led,
            # so a solicited threaded reply reaches the ✅ branch; ordinary narration
            # keeps its prefix and flows to the narration branch below. An
            # UNCONDITIONAL strip would misroute plain narration (TestBrainNarrationThreading).
            if text.startswith(f"{_NARRATION_PREFIX}[reply-to:"):
                text = text[len(_NARRATION_PREFIX):]
            # Solicited reply: the Brain echoes the operator ts as [reply-to:<ts>].
            # Thread the answer under the operator's message and mark it answered (✅).
            # Malformed leading markers are dropped instead of being treated as chatter.
            parsed_reply = parse_reply_to_marker(text)
            if parsed_reply is None:
                logger.warning("Brain message dropped: malformed reply-to marker | text=%s", text[:200])
                continue
            cleaned, reply_ts = parsed_reply
            if reply_ts is not None:
                if not cleaned:
                    logger.info("Brain reply not delivered for reply_ts=%s (empty body)", reply_ts)
                    continue
                try:
                    persist_operator_message_acknowledgement(
                        self._db, reply_ts, DIRECT_REPLY_FALLBACK_REASON,
                    )
                except Exception:
                    logger.warning(
                        "Brain reply acknowledgement failed; skipping delivery | reply_ts=%s",
                        reply_ts,
                        exc_info=True,
                    )
                    continue
                if self._post_brain_message(cleaned, thread_ts=reply_ts):
                    self.slack.add_reaction("white_check_mark", reply_ts)
                else:
                    logger.info("Brain reply not delivered for reply_ts=%s (empty or chunk failure)", reply_ts)
                continue
            # Awaiting-operator capture applies only to unmarked messages. A marked reply
            # belongs in the operator's Slack thread even when its wording says "waiting".
            if self._maybe_capture_operator_wait(text):
                continue
            if text.startswith(_NARRATION_PREFIX):
                # Brain narration: thread-only under the last heartbeat, and NEVER through
                # the directive-ref/reason gate below (that path sends [CONTEXT REQUIRED]
                # back to the Brain — the d1435 flood/restart-loop). No heartbeat yet ⇒ drop
                # (thread_ts=None would post top-level, violating thread-only).
                body = text[len(_NARRATION_PREFIX):]
                if self._last_heartbeat_ts is not None:
                    self._post_brain_message(body, thread_ts=self._last_heartbeat_ts)
                else:
                    logger.debug("Brain narration dropped (no heartbeat thread yet): %s", body[:80])
                continue
            valid, reason = self._validate_brain_message(text)
            if not valid:
                if reason == _BLOCKED_NO_DIRECTIVE:
                    # Control-marker echoes stay silently dropped (preserves the d1133
                    # loop-break); genuine ref-less chatter is threaded under the last
                    # heartbeat — buried but never lost.
                    if "[CONTEXT REQUIRED]" in text or "[FYI]" in text:
                        logger.info("Brain control-echo dropped: %s", text[:100])
                        continue
                    logger.info("Brain chatter threaded under heartbeat: %s", text[:100])
                    self._post_brain_message(text, thread_ts=self._last_heartbeat_ts)
                    continue
                logger.warning(f"Brain message blocked: {reason} | text={text[:200]}")
                self.brain.send_message(
                    f"[CONTEXT REQUIRED] Your message was blocked from Slack. "
                    f"Reason: {reason}. Restate your message with: "
                    f"(1) a directive reference (#N or dN), and "
                    f"(2) why you are reporting this (status/update/result). "
                    f'Original message: "{text[:200]}..."'
                )
                continue
            self._post_brain_message(text)

    def _handle_status(self):
        running = self.registry.get_running_workers()
        obj = self.registry.get_active_objective()
        ledger = read_task_ledger(self._ledger_path)
        progress = f"{ledger['current_task']}/{ledger['total_tasks']}" if ledger else "N/A"
        status_lines = [
            "*IronClaude Status*",
            f"State: {'paused' if self._paused else 'running'}",
            f"Workers: {len(running)} active",
            f"Brain: {'alive' if self.brain.is_alive() else 'dead/stale'}",
            f"Objective: {obj['text'] if obj else 'none'}",
            f"Progress: {progress}",
        ]
        self.slack.post_message("\n".join(status_lines))

    def _handle_summary(self):
        if self._db is None:
            self.slack.post_message("Database not configured.")
            return
        in_progress = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='in_progress' ORDER BY created_at DESC"
        ).fetchall()
        needs_input = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='pending_confirmation' ORDER BY created_at DESC"
        ).fetchall()
        awaiting_changes = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='awaiting_changes' ORDER BY created_at DESC"
        ).fetchall()
        recently_completed = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='completed' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        workers = self.registry.get_running_workers()
        lines = ["*Directive Summary*", ""]
        lines.append(f"*In Progress ({len(in_progress)}):*")
        if in_progress:
            for row in in_progress:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        if workers:
            worker_ids = ", ".join(w["id"] for w in workers)
            lines.append(f"Active workers: {worker_ids}")
        lines.append("")
        lines.append(f"*Blocked / Needs Input ({len(needs_input)}):*")
        if needs_input:
            for row in needs_input:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        lines.append("")
        # awaiting_changes is where the daemon is waiting on the OPERATOR —
        # the summary is exactly where they need that reminder. superseded is
        # deliberately excluded: it's a historical chain-link state, not
        # actionable.
        lines.append(f"*Awaiting Your Feedback 🤔 ({len(awaiting_changes)}):*")
        if awaiting_changes:
            for row in awaiting_changes:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("*Recently Completed (last 5):*")
        if recently_completed:
            for row in recently_completed:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        self.slack.post_message("\n".join(lines))

    def _handle_audit(self):
        if self._db is None:
            self.slack.post_message("Database not configured.")
            return
        try:
            messages = self.slack.search_operator_messages(limit=100, hours_back=72)
        except RuntimeError as e:
            self.slack.post_message(f"Audit unavailable: {e}")
            return
        try:
            dispositions = self._get_operator_message_dispositions()
            rows = self._db.execute(
                "SELECT id, source_ts, interpretation, status FROM directives ORDER BY created_at DESC"
            ).fetchall()
        except Exception as e:
            self.slack.post_message(f"Audit unavailable: {e}")
            return
        directive_by_ts = {row[1]: row for row in rows}
        mapped = []
        acknowledged = []
        unresolved = []
        for msg in messages:
            ts = msg["ts"]
            if dispositions.get(ts) == "directive":
                row = directive_by_ts[ts]
                mapped.append((ts, row[0], row[2], row[3]))
            elif dispositions.get(ts) == "acknowledged":
                acknowledged.append(msg)
            else:
                unresolved.append(msg)
        lines = ["*Slack Audit Report (72h)*", ""]
        lines.append("📊 *Summary*")
        lines.append(f"• Messages scanned: {len(messages)}")
        lines.append(f"• Directives: {len(mapped)}")
        lines.append(f"• Acknowledged: {len(acknowledged)}")
        lines.append(f"• Unresolved: {len(unresolved)}")
        lines.append("")
        lines.append("✅ *Mapped Messages*")
        if mapped:
            for ts, d_id, interpretation, status in mapped:
                lines.append(f"• `ts:{ts}` → d{d_id} ({status}): {interpretation[:60]}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("💬 *Acknowledged Messages*")
        if acknowledged:
            for msg in acknowledged:
                snippet = msg["text"][:50]
                lines.append(f'• "{snippet}" (ts:{msg["ts"]})')
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("⚠️ *Unresolved Messages*")
        if unresolved:
            for msg in unresolved:
                snippet = msg["text"][:50]
                lines.append(f'• "{snippet}" (ts:{msg["ts"]})')
        else:
            lines.append("(none)")
        self.slack.post_message("\n".join(lines))

    def check_brain(self):
        """Check brain health, restart if needed."""
        if self._brain_paused:
            return
        if self._reconcile_codex_brain_capability():
            return
        # Check if compaction just completed — post notification
        if self.brain.check_compaction_complete():
            self.slack.post_message(format_brain_compacted())
            logger.info("Brain compacted and resumed successfully")
            return
        if not self.brain.needs_restart():
            logger.debug("Brain healthy (alive, no timeout)")
            return
        if self.brain.circuit_breaker_tripped():
            self._brain_paused = True
            self.slack.post_message(format_brain_circuit_breaker(
                self.brain.restart_count,
                self.brain.max_restarts,
                self.brain.restart_window_seconds,
            ))
            logger.error(
                f"Brain circuit breaker tripped: {self.brain.restart_count} restarts. "
                f"Brain paused until manual intervention. "
                f"restart_timestamps={self.brain._restart_timestamps}"
            )
            return
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        brain_cwd = os.path.expanduser(self.config.get("brain_cwd", "~/.ironclaude/brain"))
        os.makedirs(brain_cwd, exist_ok=True)
        prompt_path = self.config.get("brain_prompt_path") or os.path.join(repo_root, "src", "brain", "system_prompt.md")
        # IRONCLAUDE_LLM_PATH: commander_brain
        try:
            system_prompt = _render_brain_system_prompt(prompt_path, self.config)
        except FileNotFoundError:
            logger.error(f"Brain system prompt not found: {prompt_path}")
            return
        except CommunicationProfileError as exc:
            logger.error("Brain communication-profile infrastructure error: %s", exc)
            return
        success = self.brain.restart(system_prompt, cwd=brain_cwd)
        if success:
            self.slack.post_message(format_brain_restarted(self.brain.restart_count, self.brain.restart_reason))
            logger.info(f"Brain restarted fresh ({self.brain.restart_reason})")
            logger.info(f"Brain new pid={self.brain._brain_pid}")

    @staticmethod
    def _brain_capability_fingerprint(observation: dict) -> str:
        canonical = json.dumps(
            {
                key: observation.get(key)
                for key in (
                    "schema_version",
                    "status",
                    "reason",
                    "source_companion",
                    "destination_companion",
                )
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _brain_capability_category(observation: dict) -> str:
        reason = str(observation.get("reason") or "")
        if reason.startswith(("helper-", "malformed-", "unsupported-", "executable-")):
            return "installed_runtime_mismatch"
        return "command_bridge"

    def _notify_brain_capability_once(
        self, state, *, tier: str, fingerprint: str, observation: dict
    ) -> None:
        if state.claim_brain_capability_notification(
            tier=tier, fingerprint=fingerprint
        ):
            self.slack.post_message(format_brain_capability_blocked(observation))

    def _recover_orphaned_prompt_dispatch_claims(self, *, now: float) -> None:
        if self._db is None or self._prompt_dispatch_recovery_checked:
            return
        with self._db:
            self._db.execute(
                """
                UPDATE worker_prompt_dispatches
                SET state='failed', failure_category='delivery_unknown', completed_at=?
                WHERE state='claimed'
                """,
                (now,),
            )
        self._prompt_dispatch_recovery_checked = True

    def _drain_pending_capability_recovery_dispatches(self, *, now: float) -> None:
        if self._db is None:
            return
        from ironclaude.prompt_incidents import PromptIncidentStore

        store = PromptIncidentStore(self._db)
        rows = self._db.execute(
            """
            SELECT dispatch.id, incident.evidence
            FROM worker_prompt_dispatches AS dispatch
            JOIN worker_prompt_incidents AS incident ON incident.id=dispatch.incident_id
            WHERE dispatch.reason='capability_recovery'
              AND dispatch.state='pending'
              AND incident.status='active'
            ORDER BY dispatch.id
            """
        ).fetchall()
        for dispatch_id, evidence in rows:
            if not store.claim_dispatch(
                dispatch_id, destination="brain", now=now
            ):
                continue
            delivered = self.brain.send_message(
                "[CAPABILITY RECOVERY] Re-dispatch held worker prompt after verified "
                f"Codex Brain readiness:\n{evidence}"
            )
            store.record_delivery(
                dispatch_id,
                delivered=bool(delivered),
                failure_category=None if delivered else "transport_failure",
            )

    def _finish_brain_capability_recovery(
        self, state, *, tier: str, fingerprint: str, now: float
    ) -> None:
        from ironclaude.prompt_incidents import PromptIncidentStore

        # Durable outbox ordering: create idempotent recovery generations before
        # clearing the capability hold. A crash before the clear repeats this
        # safely; a crash after the clear leaves pending generations that every
        # healthy reconstruction drains below.
        PromptIncidentStore(self._db).rearm_capability_recovery(
            fingerprint, now=now
        )
        state.record_brain_capability_recovery(tier=tier)
        self._drain_pending_capability_recovery_dispatches(now=now)

    def _reconcile_codex_brain_capability(self) -> bool:
        """Hold/reprobe Codex runtime capability without model or restart churn.

        Returns True when capability handling owns this health cycle.
        """
        if (
            getattr(self, "_db", None) is None
            or getattr(self.brain, "client_name", None) != "codex"
        ):
            return False
        from ironclaude.provider_state import ProviderState

        state = ProviderState(self._db)
        tier = str(getattr(self.brain, "capability_tier", "brain"))
        now = time.time()
        self._recover_orphaned_prompt_dispatch_claims(now=now)
        block = getattr(self.brain, "capability_block", None)
        row = self._db.execute(
            """
            SELECT available, capability_fingerprint, next_probe_at
            FROM provider_capability_state
            WHERE host='local' AND client='codex' AND role='brain' AND tier=?
            """,
            (tier,),
        ).fetchone()

        if not isinstance(block, dict):
            if (
                row is not None
                and not bool(row[0])
                and row[1]
                and self.brain.is_alive()
            ):
                self._finish_brain_capability_recovery(
                    state, tier=tier, fingerprint=row[1], now=now
                )
            elif self.brain.is_alive():
                self._drain_pending_capability_recovery_dispatches(now=now)
            return False

        fingerprint = self._brain_capability_fingerprint(block)
        persisted_fingerprint = row[1] if row is not None else None
        initial_backoff = float(
            self.config.get("brain_capability_probe_seconds", 30.0)
        )
        if persisted_fingerprint != fingerprint:
            state.record_brain_capability_block(
                tier=tier,
                category=self._brain_capability_category(block),
                reason=str(block.get("reason") or "unknown")[:256],
                fingerprint=fingerprint,
                now=now,
                initial_backoff=initial_backoff,
            )
            self._notify_brain_capability_once(
                state,
                tier=tier,
                fingerprint=fingerprint,
                observation=block,
            )
            return True

        self._notify_brain_capability_once(
            state,
            tier=tier,
            fingerprint=fingerprint,
            observation=block,
        )
        if not state.brain_capability_recheck_due(tier=tier, now=now):
            return True

        fresh = self.brain.probe_runtime_capability()
        if fresh.get("status") not in ("healthy", "repaired"):
            fresh_fingerprint = self._brain_capability_fingerprint(fresh)
            state.record_brain_capability_block(
                tier=tier,
                category=self._brain_capability_category(fresh),
                reason=str(fresh.get("reason") or "unknown")[:256],
                fingerprint=fresh_fingerprint,
                now=now,
                initial_backoff=initial_backoff,
            )
            self._notify_brain_capability_once(
                state,
                tier=tier,
                fingerprint=fresh_fingerprint,
                observation=fresh,
            )
            return True

        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        brain_cwd = os.path.expanduser(
            self.config.get("brain_cwd", "~/.ironclaude/brain")
        )
        os.makedirs(brain_cwd, exist_ok=True)
        prompt_path = self.config.get("brain_prompt_path") or os.path.join(
            repo_root, "src", "brain", "system_prompt.md"
        )
        try:
            system_prompt = _render_brain_system_prompt(prompt_path, self.config)
        except (FileNotFoundError, CommunicationProfileError) as exc:
            logger.error("Brain capability recovered but prompt render failed: %s", exc)
            return True
        if not self.brain.restart(system_prompt, cwd=brain_cwd):
            return True
        self._finish_brain_capability_recovery(
            state, tier=tier, fingerprint=fingerprint, now=now
        )
        self.slack.post_message(
            format_brain_restarted(
                self.brain.restart_count, "Codex runtime capability recovered"
            )
        )
        return True

    def process_brain_decisions(self):
        """Read and act on brain decision files."""
        decisions = read_pending_decisions(self._decisions_dir)
        for decision in decisions:
            action = decision.get("action")
            logger.info(f"Brain decision: {action}")
            self.registry.log_event("brain_decision", details=decision)

            if action == "spawn_worker":
                self._handle_spawn_worker(decision)
            elif action == "approve_plan":
                worker_id = decision.get("worker_id", "")
                worker, reason = self._validate_worker_plan_target(worker_id)
                if not worker:
                    self.slack.post_message(f"Approval not delivered for `{worker_id}`: {reason}")
                    continue
                ssh_host, _ = self._resolve_worker_ssh(worker)
                if self.tmux.send_keys(worker["tmux_session"], "yes", ssh_host=ssh_host):
                    self.slack.post_message(f"Approved plan for `{worker_id}`.")
                else:
                    self.slack.post_message(f"Approval for `{worker_id}` could not be delivered.")
            elif action == "reject_plan":
                worker_id = decision.get("worker_id", "")
                reason = decision.get("reason", "No reason given")
                worker, validation_reason = self._validate_worker_plan_target(worker_id)
                if not worker:
                    self.slack.post_message(f"Rejection not delivered for `{worker_id}`: {validation_reason}")
                    continue
                ssh_host, _ = self._resolve_worker_ssh(worker)
                if self.tmux.send_keys(worker["tmux_session"], f"no: {reason}", ssh_host=ssh_host):
                    self.slack.post_message(f"Rejected plan for `{worker_id}`: {reason}")
                else:
                    self.slack.post_message(f"Rejection for `{worker_id}` could not be delivered.")
            elif action == "send_to_worker":
                worker_id = decision.get("worker_id", "")
                message = decision.get("message", "")
                self.tmux.send_keys(f"ic-{worker_id}", message)

    def _wait_for_ready(self, session_name: str, timeout: int = 30,
                        marker: str = "ironclaude v", log_offset: int | None = None) -> bool:
        """Poll tmux log until the worker is ready or timeout exceeded.

        Returns True if marker is found in output, False on timeout.
        Dismisses trust dialogs by sending Enter if detected.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            output = (
                self.tmux.read_log_tail(session_name, lines=20)
                if log_offset is None
                else self.tmux.read_log_since(session_name, log_offset)
            )
            if output:
                lower = output.lower()
                if "trust this folder" in lower:
                    self.tmux.send_keys(session_name, "")
                if marker in output:
                    return True
            time.sleep(1)
        return False

    def _dispatch_worker_communication_profile(self, session_name: str) -> str | None:
        """Activate the interactive AI profile and require a marker newer than dispatch."""
        # IRONCLAUDE_LLM_PATH: managed_worker
        try:
            invocation = skill_invocation("managed_worker", "claude")
        except CommunicationProfileError as exc:
            return f"Communication-profile infrastructure error: {exc}"
        log_offset = self.tmux.get_log_size(session_name)
        if not self.tmux.send_keys(session_name, invocation):
            return "Failed to deliver write-lossless-ai-messages activation"
        if not self._wait_for_ready(
            session_name, timeout=30, marker=PROFILE_READY_MARKER,
            log_offset=log_offset,
        ):
            return "write-lossless-ai-messages activation did not emit a fresh readiness marker"
        return None

    def _handle_spawn_worker(self, decision: dict):
        """Spawn a worker from a brain decision."""
        worker_id = decision.get("worker_id", "")
        worker_type = decision.get("type", "claude-sonnet")
        repo = decision.get("repo", "")
        objective = decision.get("objective", "")
        model_name = decision.get("model_name", "")
        session_name = f"ic-{worker_id}"

        # Redirect claude-fable -> claude-opus while Fable is flagged
        # unavailable (fable_availability.resolve_worker_type). This path
        # never inspects _wait_for_ready's return value below, so it has no
        # "died before ready" surface to detect a dead-on-arrival Fable
        # session — unlike orchestrator_mcp's MCP spawn path, which checks it
        # and calls mark_fable_unavailable + retries as claude-opus on death.
        # Only the redirect is mirrored here; there is no death-triggered
        # retry/Slack hook to wire in this path.
        worker_type = _resolve_fable_worker_type(worker_type)

        # Handle ollama dynamic command construction
        if worker_type == "ollama":
            if not model_name:
                self.slack.post_message(
                    f"Cannot spawn ollama worker `{worker_id}` — model_name is required."
                )
                return

            # Enforce ollama singleton
            existing = self.registry.get_running_workers_by_type("ollama")
            if existing:
                self.slack.post_message(
                    f"Local LLM worker slot occupied by `{existing[0]['id']}`. "
                    f"Wait for completion or use claude-opus/claude-sonnet."
                )
                return

            _hooks_cfg_path = Path.home() / ".claude" / "ironclaude-hooks-config.json"
            try:
                with open(_hooks_cfg_path) as _f:
                    _ollama_url = json.load(_f).get("ollama", {}).get("url", "http://localhost:11434")
            except (FileNotFoundError, json.JSONDecodeError):
                _ollama_url = "http://localhost:11434"
            cmd = f"export CLAUDE_CODE_ATTRIBUTION_HEADER=0; export ANTHROPIC_BASE_URL={shlex.quote(_ollama_url)}; export ANTHROPIC_AUTH_TOKEN=ollama; export ANTHROPIC_API_KEY=; exec claude --model {shlex.quote(model_name)} --dangerously-skip-permissions"
        elif worker_type == "claude-opus":
            _default_opus_model = self.config.get("default_opus_model", "opus")
            cmd = make_opus_command(
                _default_opus_model,
                effort_for_tier(
                    _semantic_tier(_default_opus_model, "default_opus_model", "opus"),
                    self.config.get("effort_level", "high"),
                    self.config.get("effort_levels", {}),
                ),
            )
        elif worker_type == "claude-fable":
            cmd = make_opus_command(
                "fable",
                effort_for_tier("fable", self.config.get("effort_level", "high"), self.config.get("effort_levels", {})),
            )
        elif worker_type in WORKER_COMMANDS:
            # Byte-identical to WORKER_COMMANDS["claude-sonnet"] except the effort,
            # which is now TIER-FIRST resolved (sonnet tier -> effort_levels override
            # else global effort_level). Mirrors orchestrator_mcp.py's sonnet path.
            _sonnet_effort = effort_for_tier(
                "sonnet", self.config.get("effort_level", "high"), self.config.get("effort_levels", {})
            )
            cmd = f"export CLAUDE_CODE_EFFORT_LEVEL={_sonnet_effort}; exec claude --model 'sonnet' --dangerously-skip-permissions"
        else:
            # MP-04: drop claude-fable from the advertised list while Fable is
            # flagged unavailable — the daemon would silently redirect it to
            # claude-opus if requested, so advertising it here contradicts the
            # "Fable unavailable" Slack alert.
            builtin_types = ["ollama", "claude-opus"]
            if not _is_fable_unavailable():
                builtin_types.append("claude-fable")
            supported = ", ".join(builtin_types + list(WORKER_COMMANDS.keys()))
            self.slack.post_message(
                f"Unknown worker type `{worker_type}`. Supported: {supported}"
            )
            return

        # Inject worker ID for stop hook completion detection
        cmd = f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; export ENABLE_STOP_REVIEW=0; {cmd}"

        # Stage 1: ensure trust
        ensure_worker_trusted(repo)

        # Stage 2: spawn tmux session
        success = self.tmux.spawn_session(session_name, cmd, cwd=repo)
        if not success:
            self.slack.post_message(f"Failed to spawn worker `{worker_id}`.")
            return

        # Stage 3: wait for ready
        self._wait_for_ready(session_name, timeout=30)

        # Stage 4: activate professional mode
        self.tmux.send_keys(session_name, "/activate-professional-mode")
        log_worker_event("WORKER_PM_ACTIVATED", worker_id=worker_id)

        # Stage 5: wait for professional mode
        self._wait_for_ready(session_name, timeout=15, marker="Professional Mode: ON")

        profile_error = self._dispatch_worker_communication_profile(session_name)
        if profile_error is not None:
            self.tmux.kill_session(session_name)
            self.slack.post_message(f"Failed to activate communication profile for `{worker_id}`: {profile_error}")
            return

        # Stage 5.5: enable advisor if configured (skip for claude-fable — top
        # tier, no higher advisor available)
        advisor_cfg = self.config.get("advisor", {})
        if advisor_cfg.get("enabled") and worker_type != "claude-fable":
            advisor_model = advisor_cfg.get("advisor_models", {}).get(worker_type) or advisor_cfg.get("advisor_model", "opus")
            advisor_model = _resolve_fable_advisor_model(advisor_model)
            self.tmux.send_keys(session_name, f"/advisor {advisor_model}")
            self._wait_for_ready(session_name, timeout=10, marker="advisor")

        # Stage 5.6: dispatch by goal instead of raw objective, if configured
        dispatch_cfg = self.config.get("dispatch", {})
        if dispatch_cfg.get("use_goal"):
            self.tmux.send_keys(session_name, "/goal the assigned objective is complete and code review has passed")
            self._wait_for_ready(session_name, timeout=10, marker="goal")

        # Stage 6: register and send objective
        self.registry.register_worker(worker_id, worker_type, session_name, repo=repo, description=objective)
        self.tmux.send_keys(session_name, objective)
        log_worker_event("WORKER_OBJECTIVE_DELIVERED", worker_id=worker_id, objective=objective[:200])
        self.slack.post_message(format_worker_spawned(worker_id, worker_type, repo, objective))
        self.registry.log_event("worker_spawned", worker_id=worker_id, details=decision)
        pane_pid = self.tmux.list_pane_pid(session_name)
        log_worker_event("WORKER_SPAWNED", worker_id=worker_id, worker_type=worker_type, repo=repo, pane_pid=pane_pid)

    def _resolve_worker_ssh(self, worker: dict) -> tuple[str | None, str | None]:
        """Resolve SSH host and remote log dir for a worker.

        Returns (ssh_host, remote_log_dir) — both None for local workers.
        """
        machine_name = worker.get("machine")
        if not machine_name or not self._ssh_manager:
            return None, None
        machine_cfg = self._ssh_manager.get_machine(machine_name)
        if not machine_cfg:
            return None, None
        return machine_cfg.host, machine_cfg.log_dir

    def _validate_worker_plan_target(self, worker_id: str) -> tuple[dict | None, str | None]:
        """Return a live registered worker suitable for plan approval delivery."""
        worker = self.registry.get_worker(worker_id)
        if not worker:
            return None, "worker is not registered"
        if worker.get("status") != "running":
            return None, f"worker is not running (status: {worker.get('status')})"
        session_name = worker.get("tmux_session")
        if not session_name:
            return None, "worker has no registered tmux session"
        ssh_host, _ = self._resolve_worker_ssh(worker)
        if worker.get("machine") and not ssh_host:
            return None, f"worker machine `{worker['machine']}` is unavailable"
        if not self.tmux.has_session(session_name, ssh_host=ssh_host):
            return None, "registered tmux session is not active"
        return worker, None

    def _get_worker_workflow_stage(
        self, session_name: str, _claude_dir: Path | None = None,
        ssh_host: str | None = None,
    ) -> str | None:
        """Read worker's workflow stage from ironclaude.db.

        Follows the pane_pid → session_id file → DB query pattern.
        Returns the workflow_stage string, or None if any step fails.
        For remote workers, uses tmux SSH helpers instead of local file access.
        """
        if ssh_host:
            return self._get_worker_workflow_stage_remote(session_name, ssh_host)

        claude_dir = _claude_dir if _claude_dir is not None else Path("~/.claude").expanduser()

        # Step 1: Get pane PID
        pane_pid = self.tmux.list_pane_pid(session_name)
        if not pane_pid:
            return None

        # Step 2: Read session ID file
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        if not session_id_file.exists():
            return None
        try:
            session_id = session_id_file.read_text().strip()
        except OSError:
            return None
        if len(session_id) != 36:
            return None

        # Step 3: Query DB for workflow_stage
        db_path = claude_dir / "ironclaude.db"
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            row = conn.execute(
                "SELECT workflow_stage FROM sessions WHERE terminal_session = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _get_worker_workflow_stage_remote(
        self, session_name: str, ssh_host: str,
    ) -> str | None:
        """Read remote worker's workflow stage via SSH."""
        pane_pid = self.tmux.list_pane_pid(session_name, ssh_host=ssh_host)
        if not pane_pid:
            return None
        session_id_file = f"~/.claude/ironclaude-session-{pane_pid}.id"
        content = self.tmux.read_file(session_id_file, ssh_host=ssh_host)
        if not content or len(content.strip()) != 36:
            return None
        session_id = content.strip()
        # Guard against SQL injection: session_id is interpolated into the
        # remote query string, so require a strict UUID before proceeding.
        if not _UUID_RE.fullmatch(session_id):
            return None
        db_path = "~/.claude/ironclaude.db"
        result = self.tmux.run_sqlite_query(
            db_path,
            f"SELECT workflow_stage FROM sessions WHERE terminal_session='{session_id}';",
            ssh_host=ssh_host,
        )
        return result if result else None

    def _detect_worker_prompt(self, log_tail: str) -> PromptDetection:
        """Extract one validated current prompt; distinguish absence from outage."""
        menu = detect_ask_user_menu(log_tail)
        if menu["detected"]:
            return PromptDetection(menu["signal"], True)
        cache_key = hash(log_tail)
        cached = self._prompt_waiting_cache.get(cache_key)
        if cached is not None:
            ts, result = cached
            if time.time() - ts < PROMPT_WAITING_CACHE_TTL:
                return result
        result_dict = self._grader.grade(
            _PROMPT_WAITING_SYSTEM,
            f"Worker terminal context:\n{log_tail[-PROMPT_CAPTURE_CHARS:]}",
            _PROMPT_WAITING_SCHEMA,
        )
        if result_dict.get("infrastructure_error"):
            logger.debug(
                "Prompt-waiting check unavailable: %s — defaulting to False",
                result_dict.get("error_detail"),
            )
            return PromptDetection(None, False)
        if result_dict.get("kind") == "none":
            detection = PromptDetection(None, True)
        else:
            capture_truncated = len(log_tail.splitlines()) >= PROMPT_CAPTURE_LINES
            signal = validate_prompt_candidate(
                log_tail,
                result_dict,
                capture_truncated=capture_truncated,
            )
            detection = PromptDetection(signal, signal is not None)
        now = time.time()
        self._prompt_waiting_cache[cache_key] = (now, detection)
        self._prune_prompt_waiting_cache(now)
        return detection

    def _detect_prompt_waiting(self, log_tail: str) -> bool:
        """Compatibility wrapper; incident routing uses `_detect_worker_prompt`."""
        return self._detect_worker_prompt(log_tail).waiting

    def _prune_prompt_waiting_cache(self, now: float) -> None:
        """Drop expired entries and cap the cache to a bounded size."""
        cache = self._prompt_waiting_cache
        expired = [
            k for k, (ts, _) in cache.items()
            if now - ts >= PROMPT_WAITING_CACHE_TTL
        ]
        for k in expired:
            del cache[k]
        # Evict oldest entries if still over the cap.
        while len(cache) > PROMPT_WAITING_CACHE_MAX:
            oldest = min(cache, key=lambda k: cache[k][0])
            del cache[oldest]

    def _load_staleness_state(self):
        """Load stuck worker state from DB for restart persistence."""
        if self._db is None:
            return
        try:
            rows = self._db.execute(
                "SELECT worker_id, hash_value, stale_since, alert_sent "
                "FROM worker_staleness"
            ).fetchall()
            for row in rows:
                wid = row[0]
                self._stuck_hash[wid] = row[1]
                self._stuck_since[wid] = row[2]
                self._stuck_alert_sent[wid] = bool(row[3])
        except sqlite3.OperationalError:
            pass

    def _load_orphan_surface_state(self):
        """Load orphan surface-state dedup map from DB for restart persistence."""
        if getattr(self, "_db", None) is None:
            return
        try:
            rows = self._db.execute(
                "SELECT orphan_id, tip, category FROM orphan_surface_state"
            ).fetchall()
            self._orphaned_surface_state = {row[0]: (row[1], row[2]) for row in rows}
        except sqlite3.OperationalError:
            pass

    def _persist_orphan_surface_state(self):
        """Persist the current orphan surface-state map to DB as a full
        snapshot replace (the map itself is always replaced wholesale by
        `_surface_preserved_orphans`, not merged)."""
        if self._db is None:
            return
        snapshot = list(self._orphaned_surface_state.items())
        try:
            def _replace():
                self._db.execute("DELETE FROM orphan_surface_state")
                self._db.executemany(
                    "INSERT INTO orphan_surface_state (orphan_id, tip, category, updated_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    [(oid, tip, category) for oid, (tip, category) in snapshot],
                )
                self._db.commit()
            self._db_write_with_retry(_replace)
        except sqlite3.Error as e:
            logger.warning(f"Failed to persist orphan surface state: {e}")

    def _persist_staleness_state(self, worker_id: str):
        """Persist single worker's staleness state to DB."""
        if self._db is None:
            return
        if worker_id not in self._stuck_since:
            try:
                def _delete():
                    self._db.execute(
                        "DELETE FROM worker_staleness WHERE worker_id = ?",
                        (worker_id,),
                    )
                    self._db.commit()
                self._db_write_with_retry(_delete)
            except sqlite3.Error as e:
                logger.warning(f"Failed to delete staleness state for {worker_id}: {e}")
            return
        try:
            def _upsert():
                self._db.execute(
                    "INSERT OR REPLACE INTO worker_staleness "
                    "(worker_id, hash_value, stale_since, alert_sent, updated_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))",
                    (
                        worker_id,
                        self._stuck_hash.get(worker_id, 0),
                        self._stuck_since[worker_id],
                        int(self._stuck_alert_sent.get(worker_id, False)),
                    ),
                )
                self._db.commit()
            self._db_write_with_retry(_upsert)
        except sqlite3.Error as e:
            logger.warning(f"Failed to persist staleness state for {worker_id}: {e}")

    def _is_oscillating(self, worker_id: str) -> bool:
        """Return True if worker is oscillating between executing/reviewing."""
        now = time.time()
        cutoff = now - OSCILLATION_WINDOW
        history = self._stage_history.get(worker_id, [])
        pruned = [(ts, s) for ts, s in history if ts >= cutoff]
        self._stage_history[worker_id] = pruned
        if len(pruned) < OSCILLATION_THRESHOLD:
            return False
        return all(s in OSCILLATING_STAGES for _, s in pruned)

    def check_stuck_workers(self):
        """Detect workers with unchanged output and escalate/kill."""
        now = time.time()
        if now - self._last_stuck_check < STALENESS_CHECK_INTERVAL:
            return
        self._last_stuck_check = now

        running_ids = set()
        for worker in self.registry.get_running_workers():
            worker_id = worker["id"]
            session_name = worker["tmux_session"]
            running_ids.add(worker_id)
            ssh_host, _ = self._resolve_worker_ssh(worker)

            if not self.tmux.has_session(session_name, ssh_host=ssh_host):
                continue

            try:
                raw = self.tmux.capture_pane(session_name, lines=20, ssh_host=ssh_host)
                log_tail = _strip_ansi(raw)
            except Exception:
                continue

            current_hash = hash(log_tail)

            if current_hash != self._stuck_hash.get(worker_id):
                self._stuck_hash[worker_id] = current_hash
                self._stuck_since[worker_id] = now
                self._stuck_alert_sent[worker_id] = False
                self._stuck_kill_deferred.pop(worker_id, None)
                self._persist_staleness_state(worker_id)
                continue

            if worker_id not in self._stuck_since:
                self._stuck_hash[worker_id] = current_hash
                self._stuck_since[worker_id] = now
                self._stuck_alert_sent[worker_id] = False
                self._persist_staleness_state(worker_id)
                continue

            duration = now - self._stuck_since[worker_id]
            stage = self._get_worker_workflow_stage(session_name, ssh_host=ssh_host)
            if self._routine_prompt_active(worker_id):
                continue
            detection = self._detect_worker_prompt(log_tail)
            prompt_waiting = detection.waiting
            if prompt_waiting:
                # Durable prompt routing owns this episode. Staleness timers
                # cannot create a second Brain dispatch, Slack alert, or kill.
                continue

            multiplier = STAGE_STALENESS_MULTIPLIER.get(stage, 1.0)
            alert_threshold = STALENESS_ALERT_SECONDS * multiplier
            kill_threshold = STALENESS_KILL_SECONDS * multiplier

            if duration >= kill_threshold:
                deferred_until = self._stuck_kill_deferred.get(worker_id, 0)
                if now < deferred_until:
                    continue
                self._confirm_and_kill_stuck_worker(
                    worker_id, session_name, duration,
                    stage, prompt_waiting, ssh_host,
                )
            elif duration >= alert_threshold and not self._stuck_alert_sent.get(worker_id, False):
                minutes = int(duration / 60)
                self.brain.send_message(
                    f"[STUCK] Worker {worker_id} output unchanged for {minutes}min. "
                    f"{'Prompt waiting — respond or kill.' if prompt_waiting else 'Check worker status.'}"
                )
                self._stuck_alert_sent[worker_id] = True
                self._persist_staleness_state(worker_id)

        for wid in list(self._stuck_since.keys()):
            if wid not in running_ids:
                del self._stuck_since[wid]
                self._stuck_hash.pop(wid, None)
                self._stuck_alert_sent.pop(wid, None)
                self._stuck_kill_deferred.pop(wid, None)
                self._stuck_liveness_count.pop(wid, None)
                self._persist_staleness_state(wid)

        # A worker no longer running has completed (integrated) or been reaped, so
        # drop its finalization-drift state. This is the single clear point for the
        # drift counter and the session-died surfacing gate: a still-running drift
        # worker (retrying or held) stays in running_ids and is preserved, so the
        # cap keeps accumulating and the surface never re-fires.
        for wid in list(self._finalize_drift_retry.keys()):
            if wid not in running_ids:
                self._finalize_drift_retry.pop(wid, None)
        for wid in list(self._finalize_failure_count.keys()):
            if wid not in running_ids:
                self._finalize_failure_count.pop(wid, None)
        for wid in list(self._session_died_notified):
            if wid not in running_ids:
                self._session_died_notified.discard(wid)
        for wid in list(self._finalize_recovery_alerted):
            if wid not in running_ids:
                self._finalize_recovery_alerted.discard(wid)
        for wid in list(self._commit_failure_alerted):
            if wid not in running_ids:
                self._commit_failure_alerted.discard(wid)
        for wid in list(self._finalize_marker_seen):
            if wid not in running_ids:
                self._finalize_marker_seen.pop(wid, None)

    def _confirm_and_kill_stuck_worker(
        self, worker_id: str, session_name: str, duration: float,
        stage: str | None, prompt_waiting: bool, ssh_host: str | None,
    ):
        """Liveness confirmation gate + kill action for stuck worker."""
        if ssh_host is None:
            pane_pid = self.tmux.list_pane_pid(session_name)
            if pane_pid:
                try:
                    parent = psutil.Process(int(pane_pid))
                    children = parent.children(recursive=True)
                    if children:
                        for child in children:
                            try:
                                child.cpu_percent()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        time.sleep(2)
                        for child in children:
                            try:
                                if child.cpu_percent() > 1.0:
                                    deferral_count = self._stuck_liveness_count.get(worker_id, 0) + 1
                                    self._stuck_liveness_count[worker_id] = deferral_count
                                    if prompt_waiting and deferral_count > MAX_LIVENESS_DEFERRALS:
                                        logger.info(
                                            f"Worker {worker_id} liveness deferred {deferral_count} times "
                                            f"but prompt_waiting=True — proceeding with kill"
                                        )
                                        break
                                    self._stuck_kill_deferred[worker_id] = (
                                        time.time() + STALENESS_LIVENESS_EXTENSION
                                    )
                                    logger.info(
                                        f"Worker {worker_id} liveness check passed "
                                        f"(CPU active, deferral {deferral_count}), deferring kill by "
                                        f"{STALENESS_LIVENESS_EXTENSION}s"
                                    )
                                    return
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError) as e:
                    logger.warning(f"Liveness check failed for {worker_id}: {e}")

        self.tmux.kill_session(session_name, ssh_host=ssh_host)
        # Deterministic auto-integration for the now-dead session before the
        # completed-flip. A recoverable finalization failure re-queues (leave the
        # worker running so a later cycle retries); every other outcome completes.
        outcome = self._finalize_and_release_worker(
            worker_id, reason="stuck-killed", terminal=True,
        )
        # The seam owns ALL completion; the daemon completes nothing. Route the
        # outcome through the unified recovery driver (drift-retry / surface /
        # transient) which never calls update_worker_status.
        self._drive_finalization_recovery(worker_id, outcome, terminal=True)
        # Log worker_finished ONLY when the worker is actually completed (re-read
        # the registry after the seam+driver ran). A still-running transient/held
        # worker logs no finish.
        _w = self.registry.get_worker(worker_id)
        if isinstance(_w, dict) and _w.get("status") == "completed":
            self.registry.log_event("worker_finished", worker_id=worker_id)

        minutes = int(duration / 60)
        log_worker_event(
            "WORKER_STUCK_KILLED", worker_id=worker_id,
            duration_minutes=minutes, stage=stage or "unknown",
            prompt_waiting=prompt_waiting,
        )

        from ironclaude.notifications import format_worker_stuck_killed
        self.slack.post_message(format_worker_stuck_killed(
            worker_id, minutes, stage or "unknown", prompt_waiting,
        ))

        directive_list = ""
        if self._db is not None:
            try:
                rows = self._db.execute(
                    "SELECT id, interpretation FROM directives "
                    "WHERE status IN ('confirmed', 'in_progress')"
                ).fetchall()
                if rows:
                    directive_list = " Remaining: " + ", ".join(
                        f"#{r[0]}: {r[1][:60]}" for r in rows
                    )
            except Exception:
                pass

        self.brain.send_message(
            f"[MANDATORY SWEEP] Worker {worker_id} killed (stuck {minutes}min, "
            f"stage={stage or 'unknown'}, "
            f"prompt={'yes' if prompt_waiting else 'no'})."
            f"{directive_list} Spawn replacement if needed."
        )

        self._stuck_since.pop(worker_id, None)
        self._stuck_hash.pop(worker_id, None)
        self._stuck_alert_sent.pop(worker_id, None)
        self._stuck_kill_deferred.pop(worker_id, None)
        self._stuck_liveness_count.pop(worker_id, None)
        self._persist_staleness_state(worker_id)

    def _prompt_store(self):
        if self._db is None:
            return None
        from ironclaude.prompt_incidents import PromptIncidentStore

        return PromptIncidentStore(self._db)

    def _routine_prompt_active(self, worker_id: str) -> bool:
        """Fail closed for direct alerting when prompt state is unreadable."""
        store = self._prompt_store()
        if store is None:
            return False
        try:
            return store.active_for_worker(worker_id) is not None
        except sqlite3.Error:
            logger.exception(
                "Prompt incident read failed for %s; suppressing direct stuck alert",
                worker_id,
            )
            return True

    @staticmethod
    def _worker_elapsed_minutes(worker: dict) -> int:
        spawned_at = worker.get("spawned_at", "")
        try:
            spawn_time = datetime.fromisoformat(spawned_at)
            return int((datetime.utcnow() - spawn_time).total_seconds() / 60)
        except (ValueError, TypeError):
            return 0

    def _resolve_worker_prompt(self, worker_id: str, *, now: float) -> None:
        store = self._prompt_store()
        if store is not None:
            try:
                self._db_write_with_retry(lambda: store.resolve_worker(worker_id, now=now))
            except sqlite3.Error:
                logger.exception(
                    "Prompt incident resolution failed for %s; retaining episode",
                    worker_id,
                )

    def _handle_worker_prompt(
        self,
        worker: dict,
        stage: str,
        signal: PromptSignal,
        *,
        now: float,
    ) -> bool:
        """Persist and, only for a new episode, dispatch one waiting prompt."""
        store = self._prompt_store()
        if store is None:
            return False
        worker_id = worker["id"]
        try:
            observation = store.observe(worker_id, stage, signal, now=now)
        except (sqlite3.Error, TypeError):
            logger.exception(
                "Prompt incident persistence failed for %s; dispatch held", worker_id
            )
            return True
        dispatch_id = observation.dispatch_id
        if observation.action == "hold":
            try:
                active = store.active_for_worker(worker_id)
            except sqlite3.Error:
                logger.exception(
                    "Prompt incident recovery read failed for %s; dispatch held",
                    worker_id,
                )
                return True
            if not (
                active is not None
                and active.get("dispatch_state") == "pending"
                and active.get("dispatch_reason") == "initial"
            ):
                return True
            dispatch_id = active["dispatch_id"]
        try:
            claimed = store.claim_dispatch(
                dispatch_id, destination="brain", now=now
            )
        except sqlite3.Error:
            logger.exception(
                "Prompt incident claim failed for worker=%s; dispatch held", worker_id
            )
            return True
        if not claimed:
            logger.warning(
                "Prompt incident dispatch already claimed for worker=%s", worker_id
            )
            return True
        message = format_worker_checkin(
            worker_id,
            self._worker_elapsed_minutes(worker),
            stage,
            signal.evidence,
            True,
        )
        try:
            delivered = bool(self.brain.send_message(message))
        except Exception:
            logger.exception("Prompt incident Brain delivery raised for %s", worker_id)
            delivered = False
        capability_block = getattr(self.brain, "capability_block", None)
        failure_category = (
            None
            if delivered
            else "capability_blocked"
            if isinstance(capability_block, dict)
            else "transport_failure"
        )
        try:
            store.record_delivery(
                dispatch_id,
                delivered=delivered,
                failure_category=failure_category,
            )
        except (sqlite3.Error, RuntimeError):
            logger.exception(
                "Prompt incident delivery outcome could not be persisted for %s; "
                "claim remains delivery-unknown",
                worker_id,
            )
        return True

    def _reap_idle_worker(self, worker_id, session_name, ssh_host, remote_log_dir, armed):
        """Reap a worker idle past its TTL. Drive finalization recovery, then
        end the live session (the seam alone never kills a live worker). Never
        kill a worker mid finalization-recovery (its drift row may hold the
        integration lock). Returns True when the worker was actually killed and
        finalized (caller may `continue`); False when the reap was deferred
        (recovery in flight) or aborted (fresh pane activity), leaving the worker
        running so it falls through to normal marker/session-died handling."""
        idle_seconds = time.time() - armed
        pre = self._finalize_and_release_worker(worker_id, reason="idle-ttl", terminal=False)
        disp = self._drive_finalization_recovery(worker_id, pre)
        if disp in ("retrying", "held", "surfaced"):
            logger.info("Idle-TTL reap deferred for %s (disposition=%s); left running", worker_id, disp)
            return False
        # Re-read the pane-log mtime immediately before the kill: if activity
        # landed after arm+grace between the gate's read and now, the worker is
        # no longer idle — abort, leave it running, keep the idle clock.
        fresh = self.tmux.get_log_mtime(session_name, ssh_host=ssh_host, remote_log_dir=remote_log_dir)
        if fresh is not None and fresh > armed + IDLE_ACTIVITY_GRACE_SECONDS:
            logger.info("Idle-TTL reap aborted for %s: fresh pane activity before kill", worker_id)
            return False
        self.tmux.kill_session(session_name, ssh_host=ssh_host)
        outcome = self._finalize_and_release_worker(worker_id, reason="idle-ttl", terminal=True)
        self._drive_finalization_recovery(worker_id, outcome, terminal=True)
        _w = self.registry.get_worker(worker_id)
        if isinstance(_w, dict) and _w.get("status") == "completed":
            self.registry.log_event("worker_finished", worker_id=worker_id)
        self.slack.post_message(format_worker_idle_ttl_reaped(worker_id, int(idle_seconds // 60)))
        self.brain.send_message(
            f"[SWEEP] Worker {worker_id} reaped after {int(idle_seconds // 60)} min idle; "
            "finalization handled by the integration seam. Spawn a replacement if the objective is unfinished."
        )
        self._worker_idle_since.pop(worker_id, None)
        return True

    def check_workers(self):
        """Check running workers for completion signals."""
        running_workers = self.registry.get_running_workers()
        running_ids = {worker["id"] for worker in running_workers}
        for worker in running_workers:
            worker_id = worker["id"]
            session_name = worker["tmux_session"]
            ssh_host, remote_log_dir = self._resolve_worker_ssh(worker)

            # Marker-aware persistently-failing commit_worker surface. Runs
            # every cycle for every running worker (BEFORE the .done-marker /
            # idle-TTL / dead-session branches below) because a live worker
            # whose finalize deterministically fails while it keeps retrying
            # never reaches those seams — commit_worker logs finalize_failed
            # events directly (see orchestrator_mcp.py), so this counts
            # failures SINCE the last integrate/reopen marker and fires the
            # existing one-shot operator surface for a still-running worker.
            _events = self.registry.get_events_for_worker(worker_id)
            if not isinstance(_events, list):
                _events = []
            _since = count_failed_since_marker(_events)
            _latest_marker = max_marker_id(_events)
            # A new integrate/reopen marker re-arms drift/recovery/alert state
            # so a post-marker re-fail is handled fresh rather than inheriting
            # stale drift/alert state. Gated to fire at most once per new
            # marker (via _finalize_marker_seen) so it does not re-arm every
            # cycle while that marker remains the newest event, which would
            # defeat the drift cap and re-post the conflict/repair/transient
            # surfaces.
            if _latest_marker > self._finalize_marker_seen.get(worker_id, 0):
                self._finalize_marker_seen[worker_id] = _latest_marker
                self._finalize_drift_retry.pop(worker_id, None)
                self._finalize_failure_count.pop(worker_id, None)
                self._finalize_recovery_alerted.discard(worker_id)
                self._commit_failure_alerted.discard(worker_id)
            if (
                _since >= FINALIZE_DRIFT_RETRY_CAP
                and worker_id not in self._commit_failure_alerted
            ):
                self._commit_failure_alerted.add(worker_id)
                self.slack.post_message(
                    f"Worker {worker_id} finalization (commit_worker) has failed "
                    f"{_since} times since its last integrate/reopen; reviewed work "
                    f"preserved, not integrated, not completed. Try "
                    f'recover_worker_integration("{worker_id}", "reopen_for_edit") '
                    "to hand it back for edits."
                )
                self.brain.send_message(
                    f"Worker {worker_id} finalization via commit_worker is failing "
                    f"repeatedly ({_since} failures since the last marker). Stop "
                    "calling commit_worker for this worker; pin a blocker and "
                    'offer recover_worker_integration("' + worker_id + '", '
                    '"reopen_for_edit") to hand the reviewed commit back for '
                    "edits. Not completed, not abandoned."
                )

            # Primary: check for .done marker from stop hook
            if ssh_host:
                log_dir = remote_log_dir or self.tmux.log_dir
                marker_path = os.path.join(log_dir, f"{session_name}.done")
                marker_exists = self.tmux.file_exists(marker_path, ssh_host=ssh_host)
            else:
                marker_path = os.path.join(self.tmux.log_dir, f"{session_name}.done")
                marker_exists = os.path.exists(marker_path)

            # Idle-TTL gate: a worker armed idle (a prior .done sighting) that has
            # written nothing to its pane log past the TTL is reaped. Fresh pane
            # activity (mtime after arm + grace) disarms; a missing log fails safe.
            ttl = self.config.get("idle_worker_ttl_seconds", 1800)
            armed = self._worker_idle_since.get(worker_id)
            if armed is not None and ttl and ttl > 0:
                mtime = self.tmux.get_log_mtime(session_name, ssh_host=ssh_host, remote_log_dir=remote_log_dir)
                if mtime is not None and mtime > armed + IDLE_ACTIVITY_GRACE_SECONDS:
                    self._worker_idle_since.pop(worker_id, None)
                elif (mtime is not None and time.time() - armed >= ttl
                      and not self._routine_prompt_active(worker_id)):
                    if self._reap_idle_worker(worker_id, session_name, ssh_host, remote_log_dir, armed):
                        continue

            if marker_exists:
                self._worker_idle_since.setdefault(worker_id, time.time())
                self._resolve_worker_prompt(worker_id, now=time.time())
                log_worker_event("WORKER_IDLE", worker_id=worker_id)
                self.slack.post_message(format_worker_idle(worker_id))
                # Idle-but-alive worker: recycle its work in place when gates pass
                # AND there is genuinely new work. The nothing-new guard stops an
                # empty commit on repeated Brain-down cycles; runs regardless of
                # the Brain delivery below (work integrates even when Brain is down).
                outcome = self._finalize_and_release_worker(
                    worker_id, reason="idle", terminal=False,
                )
                # Route the (previously discarded) idle outcome through the
                # unified recovery driver so a drift/conflict/repair finalization
                # is driven/surfaced, not dropped. A live idle worker is never
                # completed by the daemon.
                self._drive_finalization_recovery(worker_id, outcome)
                delivered = self.brain.send_message(
                    f"Worker {worker_id} idle."
                )
                if delivered:
                    if ssh_host:
                        self.tmux.remove_file(marker_path, ssh_host=ssh_host)
                    else:
                        try:
                            os.remove(marker_path)
                        except FileNotFoundError:
                            pass
                    logger.info(f"Idle notification delivered for {worker_id}, marker removed")
                else:
                    logger.warning(f"Brain unreachable, keeping marker for {worker_id} (will retry)")
                continue

            # Fallback: session died (crash, OOM, etc.)
            if not self.tmux.has_session(session_name, ssh_host=ssh_host):
                self._resolve_worker_prompt(worker_id, now=time.time())
                # Integrate (or rescue) the dead worker's reviewed work before the
                # completed-flip. A recoverable finalization failure re-queues.
                outcome = self._finalize_and_release_worker(
                    worker_id, reason="session ended", terminal=True,
                )
                # The seam owns ALL completion; the daemon completes nothing.
                # Route the outcome through the unified recovery driver
                # (drift-retry / surface / transient) which never completes.
                disposition = self._drive_finalization_recovery(worker_id, outcome, terminal=True)
                # Log worker_finished ONLY when the worker is actually completed
                # (re-read the registry after the seam+driver ran). A worker left
                # running (transient/held/surfaced) logs no finish.
                _w = self.registry.get_worker(worker_id)
                _completed = isinstance(_w, dict) and _w.get("status") == "completed"
                if _completed:
                    self.registry.log_event("worker_finished", worker_id=worker_id)
                # Surface the dead session exactly once per worker. A drift worker
                # re-enters this branch every cycle while it stays running; gating
                # on the notified set stops the Slack/Brain posts re-firing. The
                # surface must reflect actual completion: a worker the seam left
                # NOT completed (transient/drift/held; work preserved) must never
                # be reported as "Worker Completed".
                if worker_id not in self._session_died_notified:
                    log_worker_event("WORKER_DEAD", worker_id=worker_id)
                    if _completed:
                        self.slack.post_message(format_worker_completed(worker_id, "Session ended"))
                        self.brain.send_message(
                            f"Worker {worker_id} session died (tmux gone)."
                        )
                    else:
                        self.slack.post_message(
                            format_worker_session_ended_preserved(worker_id, disposition)
                        )
                        self.brain.send_message(
                            f"Worker {worker_id} session died (tmux gone); reviewed work "
                            f"preserved, NOT completed; finalization disposition={disposition}. "
                            f'Call recover_worker_integration("{worker_id}", "status") and '
                            "handle the assignment with Commander-owned recovery/finalization "
                            "tools. Do not spawn a cleanup worker. Do not request "
                            "primary-checkout or terminal commands. Ask one natural-language "
                            "disposition question only if no durable directive or operator "
                            "instruction already decides integrate versus abandon."
                        )
                    self._session_died_notified.add(worker_id)
                continue

            # Proactive check-in: notify brain when cadence elapses
            claude_dir = self._claude_dir if self._claude_dir is not None else Path("~/.claude").expanduser()
            stage = self._get_worker_workflow_stage(session_name, _claude_dir=claude_dir, ssh_host=ssh_host)

            cadence = CHECKIN_CADENCE.get(stage, DEFAULT_CADENCE) if stage else DEFAULT_CADENCE

            # Active prompt episodes are observed every cycle so changed prompt
            # content dispatches immediately and prompt disappearance resolves
            # without waiting for ordinary check-in cadence.
            store = self._prompt_store()
            try:
                active_prompt = (
                    store.active_for_worker(worker_id) if store is not None else None
                )
            except sqlite3.Error:
                logger.exception(
                    "Prompt incident read failed for %s; holding proactive dispatch",
                    worker_id,
                )
                continue
            if active_prompt is not None:
                try:
                    active_tail = self.tmux.capture_pane(
                        session_name, lines=PROMPT_CAPTURE_LINES, ssh_host=ssh_host
                    )
                except Exception:
                    logger.exception(
                        "Active prompt capture failed for %s; retaining episode",
                        worker_id,
                    )
                    continue
                active_detection = self._detect_worker_prompt(active_tail)
                if active_detection.signal is not None:
                    self._handle_worker_prompt(
                        worker,
                        stage or "unknown",
                        active_detection.signal,
                        now=time.time(),
                    )
                    self._last_checkin_stage[worker_id] = stage
                    self._last_checkin_hash[worker_id] = hash(active_tail)
                    continue
                if active_detection.conclusive:
                    self._resolve_worker_prompt(worker_id, now=time.time())
                else:
                    # Infrastructure/semantic ambiguity cannot prove resolution.
                    continue

            # Check brain contact file (written by MCP server)
            contact_path = os.path.join(self.tmux.log_dir, f"{session_name}.brain_contact")
            last_contact = 0.0
            if os.path.exists(contact_path):
                try:
                    with open(contact_path) as f:
                        last_contact = float(f.read().strip())
                except (ValueError, OSError):
                    pass

            # Dedup gate: suppress if brain hasn't acked and heartbeat window hasn't elapsed
            heartbeat_interval = self.config.get("heartbeat_interval_seconds", 900)
            last_sent = self._last_checkin_sent.get(worker_id, 0.0)
            last_stage_sent = self._last_checkin_stage.get(worker_id)
            stage_changed = (last_stage_sent is not None and stage != last_stage_sent)

            # Track transitions independently of notification sends
            last_seen = self._last_stage_seen.get(worker_id)
            if stage != last_seen:
                self._stage_history.setdefault(worker_id, []).append((time.time(), stage))
                self._last_stage_seen[worker_id] = stage
                self._pm_gate_slack_sent.pop(worker_id, None)
                self._stage_entered_at[worker_id] = time.time()
            if self._is_oscillating(worker_id):
                stage_changed = False
                cadence = OSCILLATION_CADENCE

            if not stage_changed:
                if last_sent > 0:
                    brain_acknowledged = last_contact > last_sent
                    heartbeat_elapsed = (time.time() - last_sent) >= heartbeat_interval
                    if not brain_acknowledged and not heartbeat_elapsed:
                        continue
                # Cadence check (applies on first send and after ack)
                most_recent = max(last_contact, last_sent)
                if time.time() - most_recent < cadence:
                    continue
            # stage_changed=True → bypass both gates, fire immediately

            # Cadence expired — send proactive notification
            try:
                log_tail = self.tmux.capture_pane(
                    session_name, lines=PROMPT_CAPTURE_LINES, ssh_host=ssh_host
                )
            except Exception:
                log_tail = "(could not capture output)"

            detection = self._detect_worker_prompt(log_tail)
            prompt_waiting = detection.waiting

            current_hash = hash(log_tail)
            if not stage_changed and current_hash == self._last_checkin_hash.get(worker_id):
                if not prompt_waiting:
                    continue

            elapsed = self._worker_elapsed_minutes(worker)

            prompt_evidence = (
                detection.signal.evidence if detection.signal is not None else log_tail
            )
            brain_message = format_worker_checkin(
                worker_id,
                elapsed,
                stage or "unknown",
                prompt_evidence,
                prompt_waiting,
            )
            handled_prompt = False
            if detection.signal is not None:
                handled_prompt = self._handle_worker_prompt(
                    worker,
                    stage or "unknown",
                    detection.signal,
                    now=time.time(),
                )
            if not handled_prompt:
                self.brain.send_message(brain_message)
            self._last_checkin_sent[worker_id] = time.time()
            self._last_checkin_stage[worker_id] = stage
            self._last_checkin_hash[worker_id] = current_hash

            # Routine prompt incidents never directly surface through the PM-gate
            # Slack path. Brain-declared operator wait is the sole escalation.

        # Prune idle-TTL arm times for workers no longer running (completed or
        # reaped) so a recycled worker id never inherits a stale idle clock.
        for _wid in list(self._worker_idle_since):
            if _wid not in running_ids:
                self._worker_idle_since.pop(_wid, None)

        store = self._prompt_store()
        if store is not None:
            try:
                active_incidents = store.active_incidents()
            except sqlite3.Error:
                logger.exception(
                    "Prompt incident inventory read failed; missing-worker cleanup held"
                )
                active_incidents = []
            for incident in active_incidents:
                if incident["worker_id"] not in running_ids:
                    self._resolve_worker_prompt(incident["worker_id"], now=time.time())

    def check_confirmed_directives(self):
        """Send reminder to Brain for confirmed directives with no worker spawned within 5 minutes.

        Runs every poll cycle. Queries all confirmed directives. For each with no matching
        running worker, fires immediately if never notified (_directive_reminder_sent absent),
        or fires an ACTION REQUIRED reminder if 10+ minutes have elapsed since last notification.
        """
        if self._db is None:
            return
        REMINDER_INTERVAL = 600  # 10 minutes between reminders per directive
        now = time.time()
        try:
            rows = self._db.execute(
                "SELECT id, interpretation FROM directives d WHERE status='confirmed' "
                "AND NOT EXISTS (SELECT 1 FROM directive_capability_blocks b "
                "WHERE b.directive_id=d.id AND b.state='recovered')"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        running_workers = self.registry.get_running_workers()
        for directive_id, interpretation in rows:
            worker_found = any(_worker_matches_directive(w, directive_id) for w in running_workers)
            if worker_found:
                continue
            last_sent = self._directive_reminder_sent.get(directive_id, 0.0)
            if last_sent == 0.0:
                msg = (
                    f"Directive #{directive_id} confirmed — no worker spawned yet. Act on it now."
                )
            elif now - last_sent < REMINDER_INTERVAL:
                continue
            else:
                msg = (
                    f"[ACTION REQUIRED] Directive #{directive_id} confirmed but no worker spawned. "
                    f"Act on it now."
                )
            delivered = self.brain.send_message(msg)
            if delivered:
                self._directive_reminder_sent[directive_id] = now

    def check_idle_enforcement(self):
        """Escalate when Brain is idle with pending work.

        Throttled to 60s. Three tiers: INFO (0-60s), WARNING (60-360s),
        CRITICAL (360s+) with operator notification.
        """
        now = time.time()
        if now - self._last_idle_check < 60:
            return
        self._last_idle_check = now

        alive_workers = []
        for w in self.registry.get_recent_workers():
            ssh_host, _ = self._resolve_worker_ssh(w)
            if self.tmux.has_session(w["tmux_session"], ssh_host=ssh_host):
                alive_workers.append(w)

        if alive_workers:
            self._idle_enforcement_start = 0.0
            self._idle_escalation_tier = 0
            self._operator_notified_idle = False
            return

        unworked_directives = []
        if self._db is not None:
            try:
                rows = self._db.execute(
                    "SELECT id, status, interpretation FROM directives "
                    "WHERE status IN ('confirmed', 'in_progress') "
                    "AND NOT EXISTS (SELECT 1 FROM directive_capability_blocks b "
                    "WHERE b.directive_id=directives.id AND b.state='recovered')"
                ).fetchall()
                unworked_directives = rows
            except Exception:
                pass

        pending_tasks = []
        try:
            ledger = read_task_ledger(self._ledger_path)
            if ledger and isinstance(ledger, dict):
                pending_tasks = [
                    t for t in ledger.get("tasks", [])
                    if isinstance(t, dict) and t.get("status") in ("pending", "in_progress")
                ]
        except Exception:
            pass

        unprocessed_msgs = self._get_unprocessed_messages()

        if not unworked_directives and not pending_tasks and not unprocessed_msgs:
            self._idle_enforcement_start = 0.0
            self._idle_escalation_tier = 0
            self._operator_notified_idle = False
            return

        if self._idle_enforcement_start == 0.0:
            self._idle_enforcement_start = now

        idle_duration = now - self._idle_enforcement_start

        # R4: operator priority. While the Brain is mid-turn on operator work,
        # suppress the idle-escalation SENDS this cycle so a background nudge never
        # competes with the operator's own request. _idle_enforcement_start kept
        # accumulating above, so escalation resumes at the correct tier once the
        # turn completes. Gated on `is True` (a real bool in production;
        # brain_client.py:838 clears it on the first assistant text) — this also
        # keeps the gate inert for the MagicMock brains used in other tests.
        if self.brain._executing_tool is True:
            return

        if idle_duration < 60:
            if self._idle_escalation_tier < 1:
                self._idle_escalation_tier = 1
                self.brain.send_message(
                    f"You have {len(unworked_directives)} unworked directive(s), "
                    f"{len(pending_tasks)} pending ledger task(s), and "
                    f"{len(unprocessed_msgs)} unprocessed operator message(s) "
                    f"with no active workers. Spawn workers now."
                )
        elif idle_duration < 360:
            if self._idle_escalation_tier < 2:
                self._idle_escalation_tier = 2
                directive_list = ", ".join(
                    f"#{r[0]} ({r[1]}): {r[2][:60]}" for r in unworked_directives
                )
                task_list = ", ".join(
                    t.get("description", "unknown")[:40] for t in pending_tasks[:5]
                )
                parts = [f"[WARNING] Idle for {int(idle_duration)}s with pending work."]
                if directive_list:
                    parts.append(f"Directives: {directive_list}.")
                if task_list:
                    parts.append(f"Ledger tasks: {task_list}.")
                if unprocessed_msgs:
                    parts.append(f"{len(unprocessed_msgs)} unprocessed message(s).")
                parts.append("Act immediately.")
                self.brain.send_message(" ".join(parts))
        else:
            if self._idle_escalation_tier < 3:
                self._idle_escalation_tier = 3
                self.brain.send_message(
                    f"[CRITICAL] Idle for {int(idle_duration)}s with pending work. "
                    f"This is a hard enforcement escalation. Spawn workers NOW."
                )
            if not self._operator_notified_idle:
                self._operator_notified_idle = True
                total = len(unworked_directives) + len(pending_tasks) + len(unprocessed_msgs)
                self.slack.post_message(
                    f"[ALERT] Brain idle for {int(idle_duration // 60)} minutes "
                    f"with {total} pending item(s). Manual intervention may be needed."
                )

    def _handle_detail(self, parsed: dict):
        """Handle /detail command — show worker status and log."""
        worker_id = parsed.get("target", "")
        w = self.registry.get_worker(worker_id)
        if not w:
            self.slack.post_message(f"Worker `{worker_id}` not found.")
            return
        try:
            log_tail = self.tmux.capture_pane(w["tmux_session"], lines=20)
        except subprocess.CalledProcessError:
            log_tail = self.tmux.read_log_tail(w["tmux_session"], lines=20)
        self.slack.post_message(f"*Worker {worker_id}:* status={w['status']}\n```{log_tail}```")

    def _handle_log(self, parsed: dict):
        """Handle /log command — show worker log."""
        worker_id = parsed.get("target", "")
        lines = parsed.get("lines", 20)
        w = self.registry.get_worker(worker_id)
        if not w:
            self.slack.post_message(f"Worker `{worker_id}` not found.")
            return
        try:
            log_tail = self.tmux.capture_pane(w["tmux_session"], lines=lines)
        except subprocess.CalledProcessError:
            log_tail = self.tmux.read_log_tail(w["tmux_session"], lines=lines)
        self.slack.post_message(f"```{log_tail}```")

    def _get_pending_confirmation_waits(self) -> dict[str, dict]:
        """Deterministic operator-wait signal: directives sitting in pending_confirmation
        are, by construction, waiting on the operator's confirm/reject Slack reaction —
        no LLM classification needed, cannot false-positive."""
        if self._db is None:
            return {}
        try:
            rows = self._db.execute(
                "SELECT id, interpretation FROM directives WHERE status='pending_confirmation'"
            ).fetchall()
        except Exception as e:
            # Broad catch matches the existing pattern at main.py:2456-2470 — a query
            # failure here must not crash the heartbeat.
            logger.warning("pending_confirmation query skipped: %s", e)
            return {}
        return {f"d{row[0]}": {"question": (row[1] or "")[:150]} for row in rows}

    def _load_directive_capability_blocks(self) -> list[dict]:
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT b.*, d.status AS directive_status, d.interpretation "
                "FROM directive_capability_blocks b "
                "JOIN directives d ON d.id=b.directive_id "
                "WHERE (b.state='blocked' AND d.status='blocked') "
                "OR (b.state='recovered' AND b.recovery_dispatch_state='pending' "
                "AND d.status='blocked')"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        result = []
        for row in rows:
            item = dict(row)
            item["capabilities"] = json.loads(item["capabilities_json"])
            result.append(item)
        return result

    def _process_directive_capability_block(self, block: dict, now: float) -> None:
        if self._db is None or block["directive_status"] != "blocked":
            return
        directive_id = block["directive_id"]
        generation = block["generation"]
        if block["state"] == "recovered":
            delivered = self.brain.send_message(
                f"[CAPABILITY RECOVERED] Directive #{directive_id} capabilities recovered. "
                "Run one fresh attention sweep and make one dispatch decision."
            )
            if delivered:
                with self._db:
                    changed = self._db.execute(
                        "UPDATE directive_capability_blocks "
                        "SET recovery_dispatch_state='accepted' "
                        "WHERE directive_id=? AND generation=? AND state='recovered' "
                        "AND recovery_dispatch_state='pending' "
                        "AND EXISTS (SELECT 1 FROM directives d WHERE d.id=? "
                        "AND d.status='blocked')",
                        (directive_id, generation, directive_id),
                    ).rowcount
                    if changed:
                        self._db.execute(
                            "UPDATE directives SET status='confirmed', "
                            "updated_at=datetime('now') WHERE id=? AND status='blocked'",
                            (directive_id,),
                        )
            return

        if block["notification_state"] == "pending":
            with self._db:
                claimed = self._db.execute(
                    "UPDATE directive_capability_blocks SET notification_state='submitted' "
                    "WHERE directive_id=? AND generation=? AND state='blocked' "
                    "AND notification_state='pending' "
                    "AND EXISTS (SELECT 1 FROM directives d WHERE d.id=? "
                    "AND d.status='blocked')",
                    (directive_id, generation, directive_id),
                ).rowcount
            if claimed:
                capabilities = ", ".join(block["capabilities"])
                self.slack.post_message(
                    f"*Directive #{directive_id} blocked* — {capabilities} "
                    f"({block['denial_scope']}): {block['reason']}"
                )

        heartbeat = max(1, int(self.config.get("heartbeat_interval_seconds", 900)))
        initial_deadline = block["last_observed_at"] + min(60, heartbeat)
        if heartbeat < 60 and (
            block["backoff_seconds"] > heartbeat
            or block["next_recheck_at"] > initial_deadline
        ):
            with self._db:
                clamped = self._db.execute(
                    "UPDATE directive_capability_blocks "
                    "SET next_recheck_at=?, backoff_seconds=? "
                    "WHERE directive_id=? AND generation=? AND state='blocked' "
                    "AND next_recheck_at=? AND backoff_seconds=? "
                    "AND EXISTS (SELECT 1 FROM directives d WHERE d.id=? "
                    "AND d.status='blocked')",
                    (
                        initial_deadline, min(60, heartbeat), directive_id,
                        generation, block["next_recheck_at"],
                        block["backoff_seconds"], directive_id,
                    ),
                ).rowcount
            if not clamped:
                return
            block["next_recheck_at"] = initial_deadline
            block["backoff_seconds"] = min(60, heartbeat)

        if now < block["next_recheck_at"]:
            return
        current_backoff = min(float(block["backoff_seconds"]), heartbeat)
        next_backoff = min(current_backoff * 2, heartbeat)
        next_deadline = now + next_backoff
        with self._db:
            claimed = self._db.execute(
                "UPDATE directive_capability_blocks "
                "SET next_recheck_at=?, backoff_seconds=? "
                "WHERE directive_id=? AND generation=? AND state='blocked' "
                "AND next_recheck_at=? AND backoff_seconds=? "
                "AND EXISTS (SELECT 1 FROM directives d WHERE d.id=? "
                "AND d.status='blocked')",
                (
                    next_deadline, next_backoff, directive_id, generation,
                    block["next_recheck_at"], block["backoff_seconds"], directive_id,
                ),
            ).rowcount
        if not claimed:
            return
        delivered = self.brain.send_message(
            f"[CAPABILITY RECHECK] Directive #{directive_id}: perform exactly one "
            f"non-mutating probe for each capability ({', '.join(block['capabilities'])}) "
            "in this turn and report one aggregate result through the structured "
            "capability block/recovery tools. Do not start a monitor."
        )
        if not delivered:
            with self._db:
                self._db.execute(
                    "UPDATE directive_capability_blocks "
                    "SET next_recheck_at=?, backoff_seconds=? "
                    "WHERE directive_id=? AND generation=? AND state='blocked' "
                    "AND next_recheck_at=? AND backoff_seconds=? "
                    "AND EXISTS (SELECT 1 FROM directives d WHERE d.id=? "
                    "AND d.status='blocked')",
                    (
                        block["next_recheck_at"], block["backoff_seconds"],
                        directive_id, generation, next_deadline, next_backoff,
                        directive_id,
                    ),
                )

    def check_directive_capability_blocks(self, now: float | None = None) -> None:
        observed_at = time.time() if now is None else now
        for block in self._load_directive_capability_blocks():
            self._process_directive_capability_block(block, observed_at)

    def post_heartbeat(self, now: float | None = None):
        """Post heartbeat to Slack at configured interval."""
        heartbeat_interval = self.config.get("heartbeat_interval_seconds", 900)
        now = time.time() if now is None else now
        if now - self._last_heartbeat < heartbeat_interval:
            return
        self._last_heartbeat = now

        candidates = self.registry.get_recent_workers()
        prompt_store = self._prompt_store()
        try:
            prompt_incidents = (
                prompt_store.active_incidents() if prompt_store is not None else []
            )
        except sqlite3.Error:
            logger.exception(
                "Prompt incident inventory read failed; heartbeat omits prompt status"
            )
            prompt_incidents = []
        active_prompts = {
            incident["worker_id"]: incident for incident in prompt_incidents
        }
        worker_details = []
        stage_map: dict[str, str] = {}
        for w in candidates:
            ssh_host, _ = self._resolve_worker_ssh(w)
            if not self.tmux.has_session(w["tmux_session"], ssh_host=ssh_host):
                continue
            stage = self._get_worker_workflow_stage(w["tmux_session"], ssh_host=ssh_host)
            stage_map[w["id"]] = stage or "unknown"
            detail = {
                "id": w["id"],
                "description": w.get("description"),
                "workflow_stage": stage,
            }
            active_prompt = active_prompts.get(w["id"])
            if active_prompt is not None:
                detail["prompt_incident"] = {
                    "age_seconds": max(
                        0.0, now - float(active_prompt["first_observed_at"])
                    ),
                    "dispatch_state": active_prompt.get("dispatch_state"),
                    "failure_category": active_prompt.get("failure_category"),
                }
            worker_details.append(detail)

        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        blocked_directives = [
            block for block in self._load_directive_capability_blocks()
            if block["state"] == "blocked"
        ]
        self._prune_operator_waits(now)
        self._prune_brain_waits(now)
        operator_name = self.config.get("operator_name", "Operator")
        # Deterministic signal is merged LAST so it wins over a same-id `_operator_waits`
        # entry — a stale/false-positive classifier entry must never mask a real
        # pending_confirmation directive sharing the same d{id} key.
        merged_waits = {**dict(self._operator_waits), **self._get_pending_confirmation_waits()}
        from ironclaude.ollama_client import ollama_busy_urls, ollama_degraded_urls
        from ironclaude.notifications import resolve_degraded_backend_label
        _hooks_cfg_path = os.environ.get("IC_OLLAMA_CONFIG_PATH") or os.path.expanduser(
            "~/.claude/ironclaude-hooks-config.json"
        )
        self._last_heartbeat_ts = self.slack.post_message(
            format_heartbeat(
                worker_details,
                brain_usage=brain_usage,
                waits=merged_waits,
                brain_waits=dict(self._brain_waits),
                operator_name=operator_name,
                ollama_degraded=bool(ollama_degraded_urls()),
                ollama_busy=bool(ollama_busy_urls()),
                blocked_directives=blocked_directives,
                degraded_backend_label=resolve_degraded_backend_label(_hooks_cfg_path),
                orphaned_unmerged=self._orphaned_unmerged_count,
                mem_line=_format_mem_line(),
            )
        )

        # Grader enforcement: if no alive workers but directives exist, nudge the Brain.
        # R4: skip while the Brain is mid-turn on operator work (`is True`; inert for
        # MagicMock brains) — this defers the grader-check nudge one heartbeat interval
        # (~900s) when busy, which is acceptable.
        if not worker_details and self._db is not None and self.brain._executing_tool is not True:
            try:
                unworked = self._db.execute(
                    "SELECT count(*) FROM directives d "
                    "WHERE status IN ('confirmed', 'in_progress') "
                    "AND NOT EXISTS (SELECT 1 FROM directive_capability_blocks b "
                    "WHERE b.directive_id=d.id AND b.state='recovered')"
                ).fetchone()[0]
                if unworked > 0:
                    self.brain.send_message(
                        f"GRADER CHECK: You have {unworked} confirmed/in_progress directive(s) "
                        f"with no active workers. Follow the Attention Sweep Protocol — "
                        f"spawn workers for unblocked directives immediately."
                    )
                    self.slack.post_message(
                        f"*Idle with {unworked} unworked directive(s)* — nudge sent to Brain."
                    )
            except Exception as e:
                logger.warning(f"Heartbeat directive check failed: {e}")

        # Heartbeat-level stuck detection: compare (stage, log_bytes) across consecutive heartbeats
        running_heartbeat_ids: set[str] = set()
        for w in candidates:
            worker_id = w["id"]
            if worker_id not in stage_map:
                continue  # session was gone in first loop
            running_heartbeat_ids.add(worker_id)
            ssh_host, _ = self._resolve_worker_ssh(w)
            if ssh_host:
                log_bytes = 0
            else:
                log_path = os.path.join(self.tmux.log_dir, f"{w['tmux_session']}.log")
                log_bytes = os.path.getsize(log_path) if os.path.exists(log_path) else 0
            snapshot = (stage_map[worker_id], log_bytes)
            history = self._heartbeat_state_history.setdefault(worker_id, [])
            history.append(snapshot)
            if len(history) > 2:
                history.pop(0)
            if len(history) >= 2 and history[-1] == history[-2]:
                if worker_id in active_prompts:
                    # Heartbeat already renders the durable held prompt. It must
                    # never create a second Brain action for the same episode.
                    self._heartbeat_stuck_notified.discard(worker_id)
                    continue
                # R4: suppress the stuck-notify while the Brain is mid-turn on
                # operator work; skip the .add too so it re-evaluates (and notifies)
                # on a later heartbeat once the turn completes. `is True` keeps the
                # gate inert for MagicMock brains in other tests.
                if worker_id not in self._heartbeat_stuck_notified and self.brain._executing_tool is not True:
                    minutes = int(heartbeat_interval / 60) * 2
                    self.brain.send_message(
                        f"[ACTION REQUIRED] Worker {worker_id} unchanged for 2 consecutive heartbeats "
                        f"(~{minutes} min) at stage {stage_map[worker_id]}. Not a PM gate — "
                        f"verify the worker is alive and making progress."
                    )
                    self.slack.post_message(
                        format_worker_heartbeat_stuck_slack(worker_id, stage_map[worker_id])
                    )
                    self._heartbeat_stuck_notified.add(worker_id)
            elif len(history) >= 2 and history[-1] != history[-2]:
                self._heartbeat_stuck_notified.discard(worker_id)

        for wid in list(self._heartbeat_state_history.keys()):
            if wid not in running_heartbeat_ids:
                del self._heartbeat_state_history[wid]
                self._heartbeat_stuck_notified.discard(wid)
        for wid in list(self._heartbeat_stuck_notified):
            if wid not in running_heartbeat_ids:
                self._heartbeat_stuck_notified.discard(wid)

    def run(self):
        """Main daemon loop.

        R3: operator fast lane. Slack commands, Brain responses, and the Slack
        send-queue flush run every ~fast_interval (2-3s) so operator input is
        picked up promptly; the heavier worker/heartbeat/maintenance sweeps stay on
        the slower poll_interval (15s). The slow-lane members are the single writer
        of their state — they run ONLY on the slow tick, never on a fast tick, so
        the fast lane introduces no concurrent writer. post_heartbeat already
        self-throttles internally, so it needs no separate timer beyond the slow
        tick. last_slow starts a full interval in the past so the first iteration
        runs the slow lane immediately (parity with the pre-split loop)."""
        poll_interval = self.config.get("poll_interval_seconds", 15)
        fast_interval = self.config.get("fast_poll_interval_seconds", 3)
        last_slow = time.monotonic() - poll_interval
        while self._running:
            # Fast lane: operator-facing I/O only.
            self.poll_slack_commands()
            self.poll_brain_responses()
            self.slack.flush_queue()
            # Slow lane: worker/heartbeat/maintenance sweeps on poll_interval.
            now = time.monotonic()
            if now - last_slow >= poll_interval:
                last_slow = now
                self._sweep_expired_push_requests()
                if not self._paused:
                    self.check_brain()
                    self.process_brain_decisions()
                    self.check_workers()
                    self.check_directive_capability_blocks()
                    self.check_confirmed_directives()
                    self.check_idle_enforcement()
                    self.check_post_kill_sweep()
                    self.check_message_aging()
                self.post_heartbeat()
                self._run_maintenance()
            time.sleep(fast_interval)


def _sync_brain_settings_hooks(template_path: str, settings_path: str,
                               hooks_src_dir: str, hooks_dst_dir: str) -> None:
    """SF2/R5a: register the repo-declared PreToolUse hooks into the Brain's live
    settings.json and deploy their scripts. The Brain loads these via
    setting_sources=[project,local]; the in-process can_use_tool callback is dead
    under bypassPermissions, so shell-hook registration is the live enforcement
    layer. The merge APPENDS each entry only if its command is absent (idempotent —
    running twice yields exactly one), and NEVER rewrites the hooks object, so every
    existing PreToolUse entry and the entire PostToolUse block (block-pin-enforcer
    on update_ledger included) are preserved; settings.local.json is untouched.
    Template-driven: a new hook (e.g. Task 9's Agent gate) is added by editing the
    template only. hook-logger.sh, which the scripts source, is NOT template-listed
    — it is deployed to hooks_dst_dir by `make deploy-hooks`."""
    try:
        with open(template_path) as f:
            template = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("brain_settings_hooks template unavailable (%s); skipping sync", e)
        return
    entries = template.get("PreToolUse", [])
    if not entries:
        return

    # Deploy every hook script the template references (bash $HOME/.../<script>.sh).
    os.makedirs(hooks_dst_dir, exist_ok=True)
    for entry in entries:
        for hook in entry.get("hooks", []):
            m = re.search(r"([A-Za-z0-9._-]+\.sh)\s*$", hook.get("command", ""))
            if not m:
                continue
            script = m.group(1)
            src = os.path.join(hooks_src_dir, script)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(hooks_dst_dir, script))
            else:
                logger.warning("brain settings hook script missing at source: %s", src)

    # Merge (append-if-absent) into settings.json PreToolUse — preserve everything else.
    # A missing file is fine (fresh install → create it). A CORRUPT file must NOT be
    # clobbered: overwriting it with only the template entries would silently drop the
    # existing guardrail hooks (memory-search, block-push, the PostToolUse block-pin) —
    # so log and skip, leaving the file for a human to repair.
    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    except json.JSONDecodeError as e:
        logger.error(
            "brain settings.json is corrupt (%s); skipping hook sync to avoid clobbering "
            "existing guardrail hooks at %s", e, settings_path,
        )
        return
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    existing_cmds = {h.get("command") for e in pre for h in e.get("hooks", [])}
    added = False
    for entry in entries:
        entry_cmds = [h.get("command") for h in entry.get("hooks", [])]
        if entry_cmds and all(c in existing_cmds for c in entry_cmds):
            continue  # already registered — idempotent
        pre.append(entry)
        existing_cmds.update(entry_cmds)
        added = True
    if added:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
        logger.info("Synced brain settings hooks into %s", settings_path)


def main():
    _log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    os.makedirs("/tmp/ic", exist_ok=True)
    _stderr_handler = logging.StreamHandler()
    _stderr_handler.setFormatter(logging.Formatter(_log_format))
    _root_logger = logging.getLogger()
    _root_logger.setLevel(logging.INFO)
    _root_logger.addHandler(_stderr_handler)
    # SF1: never attach the live /tmp/ic/daemon.log handler under pytest — test
    # runs that call main() (test_daemon.py) would else write into the real daemon
    # log and corrupt its latency stats. Production behaviour is unchanged.
    if "PYTEST_CURRENT_TEST" not in os.environ:
        _file_handler = RotatingFileHandler(
            "/tmp/ic/daemon.log", maxBytes=5 * 1024 * 1024, backupCount=3
        )
        _file_handler.setFormatter(logging.Formatter(_log_format))
        _root_logger.addHandler(_file_handler)

    no_respawn = '--no-respawn' in sys.argv

    # Isolate daemon from pipeline process group (e.g. make run | tee)
    # so SIGTERM to the pipeline group doesn't kill the brain subprocess
    try:
        os.setpgid(0, 0)
    except PermissionError:
        logger.warning("Could not create new process group (setpgid failed)")

    _acquire_singleton_lock()
    logger.info(
        "Daemon started — pid=%d executable=%s cwd=%s utc=%s",
        os.getpid(),
        sys.executable,
        os.getcwd(),
        datetime.now(timezone.utc).isoformat(),
    )
    _load_dotenv()

    config = load_config()

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    brain_cwd = os.path.expanduser(config.get("brain_cwd", "~/.ironclaude/brain"))
    os.makedirs(brain_cwd, exist_ok=True)

    wiki_dir = os.path.join(brain_cwd, "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    for wiki_file, wiki_content in [
        ("index.md", "# Wiki Index\n\n*No wiki pages yet.*\n"),
        ("log.md", "# Wiki Log\n\n"),
    ]:
        wiki_path = os.path.join(wiki_dir, wiki_file)
        if not os.path.exists(wiki_path):
            with open(wiki_path, "w") as f:
                f.write(wiki_content)
            logger.info(f"Initialized wiki file: {wiki_path}")

    # Sync orchestrator CLAUDE.md from source control to brain home (with template substitution)
    orchestrator_claude_md_src = os.path.join(repo_root, "src", "brain", "orchestrator_claude.md")
    orchestrator_claude_md_dst = os.path.join(brain_cwd, "CLAUDE.md")
    try:
        with open(orchestrator_claude_md_src) as f:
            content = _substitute_prompt(f.read(), config)
        with open(orchestrator_claude_md_dst, "w") as f:
            f.write(content)
        logger.info(f"Synced orchestrator CLAUDE.md to {orchestrator_claude_md_dst}")
    except FileNotFoundError:
        logger.error(f"Orchestrator CLAUDE.md not found at {orchestrator_claude_md_src}")
        sys.exit(1)

    # Sync orchestrator rules files to brain_cwd/.claude/rules/
    rules_src_dir = os.path.join(repo_root, "src", "brain", "rules")
    rules_dst_dir = os.path.join(brain_cwd, ".claude", "rules")
    if os.path.isdir(rules_src_dir):
        os.makedirs(rules_dst_dir, exist_ok=True)
        for rules_file in sorted(os.listdir(rules_src_dir)):
            if not rules_file.endswith(".md"):
                continue
            src_path = os.path.join(rules_src_dir, rules_file)
            dst_path = os.path.join(rules_dst_dir, rules_file)
            try:
                with open(src_path) as f:
                    content = _substitute_prompt(f.read(), config)
                with open(dst_path, "w") as f:
                    f.write(content)
                logger.info(f"Synced rules file {rules_file} to {dst_path}")
            except FileNotFoundError:
                logger.error(f"Rules file not found at {src_path}")
                sys.exit(1)

    # SF2/R5a: register the repo-declared Brain PreToolUse hooks (48h-lookback
    # enforcer, and the Task-9 Agent fan-out gate) into the Brain's live
    # settings.json and deploy their scripts. can_use_tool is dead under
    # bypassPermissions, so this shell-hook layer is the real enforcement.
    _sync_brain_settings_hooks(
        template_path=os.path.join(repo_root, "src", "brain", "brain_settings_hooks.json"),
        settings_path=os.path.join(brain_cwd, ".claude", "settings.json"),
        hooks_src_dir=os.path.join(repo_root, "hooks"),
        hooks_dst_dir=os.path.expanduser("~/.claude/ironclaude-hooks"),
    )

    # Sync grader CLAUDE.md from source control to grader home (with template substitution)
    grader_home = os.path.expanduser("~/.ironclaude/grader")
    os.makedirs(grader_home, exist_ok=True)
    grader_claude_md_src = os.path.join(repo_root, "src", "brain", "grader_claude.md")
    grader_claude_md_dst = os.path.join(grader_home, "CLAUDE.md")
    try:
        with open(grader_claude_md_src) as f:
            content = _substitute_prompt(f.read(), config)
        with open(grader_claude_md_dst, "w") as f:
            f.write(content)
        logger.info(f"Synced grader CLAUDE.md to {grader_claude_md_dst}")
    except FileNotFoundError:
        logger.error(f"Grader CLAUDE.md not found at {grader_claude_md_src}")
        sys.exit(1)

    # Deploy worker hooks so a daemon restart always implies current hooks
    # (mirrors `make deploy-hooks`; see _deploy_worker_hooks docstring).
    _deploy_worker_hooks(repo_root)

    ensure_brain_trusted(grader_home)

    slack_token = config.get("slack_bot_token", "")
    channel_id = config.get("slack_channel_id", "")
    if not slack_token or not channel_id:
        logger.error("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are required.")
        sys.exit(1)

    user_token = config.get("slack_user_token", "")
    operator_user_id = config.get("slack_operator_user_id", "")
    slack = SlackBot(token=slack_token, channel_id=channel_id, user_token=user_token, operator_user_id=operator_user_id)

    # Plugin system
    plugin_registry = PluginRegistry()
    loaded_plugins = discover_plugins(plugin_registry)
    if loaded_plugins:
        logger.info("Loaded plugins: %s", ", ".join(loaded_plugins))
    from ironclaude.slack_interface import SLASH_COMMANDS
    SLASH_COMMANDS.update(plugin_registry.get_slash_commands())

    socket_handler = None
    app_token = config.get("slack_app_token", "")
    if app_token:
        socket_handler = SlackSocketHandler(app_token=app_token, bot_token=slack_token, operator_user_id=operator_user_id, registry=plugin_registry)
        socket_handler.start()
        logger.info("Slack Socket Mode enabled")
    else:
        logger.warning("SLACK_APP_TOKEN not set — slash commands disabled")

    conn = init_db(config.get("db_path", "data/db/ic.db"))

    # SSH remote machines
    from ironclaude.ssh_manager import SSHConnectionManager
    ssh_manager = None
    machines = load_machines_config()
    if machines:
        ssh_manager = SSHConnectionManager()
        ssh_manager.register_machines(machines)
        for name in ssh_manager.list_machine_names():
            health = ssh_manager.health_check(name)
            logger.info(f"Remote machine '{name}': {'healthy' if health.ok else health.details}")

    tmux = TmuxManager(log_dir=config.get("log_dir", "/tmp/ic-logs"), ssh_manager=ssh_manager)
    registry = WorkerRegistry(conn)
    _kill_orphan_workers(tmux, registry)
    brain = select_brain_class(config, conn)(
        timeout_seconds=config.get("brain_timeout_seconds", 600),
        operator_name=config.get("operator_name", "Operator"),
        model=config.get("brain_model", "opus"),
        effort_level=config.get("effort_level", "high"),
        effort_levels=config.get("effort_levels", {}),
    )

    # Start brain
    prompt_path = config.get("brain_prompt_path") or os.path.join(repo_root, "src", "brain", "system_prompt.md")
    # IRONCLAUDE_LLM_PATH: commander_brain
    try:
        system_prompt = _render_brain_system_prompt(prompt_path, config)
        ensure_brain_trusted(brain_cwd)
        brain.start(system_prompt, cwd=brain_cwd)
        _log_brain_start_result(brain)
    except FileNotFoundError:
        logger.warning(f"Brain system prompt not found: {prompt_path} — brain will start on first check_brain()")
    except CommunicationProfileError as exc:
        logger.error("Brain communication-profile infrastructure error: %s", exc)
        sys.exit(1)

    daemon = IroncladeDaemon(
        config=config, slack=slack, socket_handler=socket_handler,
        registry=registry, tmux_manager=tmux, brain=brain,
        db_conn=conn, plugin_registry=plugin_registry,
        ssh_manager=ssh_manager,
    )
    plugin_registry.run_lifecycle("init", daemon)

    global _daemon
    _daemon = daemon

    _install_sigaction_handler()
    signal.signal(signal.SIGHUP, _handle_restart)

    logger.info("IronClaude Commander daemon starting.")
    slack.post_message("IronClaude Commander daemon started.")

    try:
        daemon.run()
    finally:
        try:
            Path(_PID_FILE).unlink(missing_ok=True)
        except OSError:
            pass
    brain.shutdown()
    conn.close()
    if ssh_manager:
        ssh_manager.teardown_all()
    if socket_handler:
        socket_handler.stop()
    slack.post_message("IronClaude Commander daemon stopped.")
    logger.info("IronClaude Commander daemon stopped.")

    if not _clean_shutdown and not no_respawn:
        _spawn_respawner()


if __name__ == "__main__":
    main()
