"""Private, held-assignment worker provider handoff controller.

The registry and workspace manager are separate transaction domains. Every
external effect has a durable intent; uncertain outcomes remain held for
readback, never retried by a second controller invocation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shlex
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager

from ironclaude.worker_pm_handoff import snapshot_pm_state, prepare_pm_handoff, readback_pm_handoff
from ironclaude.worker_handoff_process import NativeProcessAdapter
from ironclaude.worker_quota import read_native_quota
from ironclaude.workspace_handoff_authority import WorkspaceHandoffAuthority
from ironclaude.provider_state import DEFAULT_USAGE_QUARANTINE_SECONDS
from datetime import datetime

# The longest native limit window is 7 days, so no usage quarantine outlasts the limit event by
# more than that, whatever reset the quota evidence reports (design 2026-10-18 v120-security-fixes 2).
_QUARANTINE_MAX_SECONDS = 7 * 24 * 3600


class HandoffHold(RuntimeError):
    """A bounded reason for preserving an unfinished held assignment."""


def _evidence(row: dict) -> dict:
    return json.loads(row["evidence_json"])


def _git_head(path: str) -> str:
    result = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
        text=True, timeout=5, check=False,
    )
    if result.returncode or len(result.stdout.strip()) != 40:
        raise HandoffHold("workspace_git_evidence_unavailable")
    return result.stdout.strip()


def _project_hash(db_path: Path, native_id: str) -> str:
    try:
        db = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT project_hash FROM sessions WHERE terminal_session=?", (native_id,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise HandoffHold("source_pm_root_unavailable") from exc
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
        raise HandoffHold("source_pm_project_unproved")
    return rows[0][0]


def _continuation_objective(worker: dict, pm: dict) -> str:
    description = worker.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    plan_json = pm.get("session", {}).get("plan_json")
    try:
        plan = json.loads(plan_json) if plan_json else None
    except (TypeError, ValueError):
        plan = None
    goal = plan.get("goal") if isinstance(plan, dict) else None
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    raise HandoffHold("continuation_objective_missing")


class WorkerHandoffController:
    """Use only from Commander; never expose as a worker MCP tool."""

    def __init__(self, orchestrator, *, process=None, runtime=None,
                 authority=None, pm_db_path=None, native_log_root=None,
                 session_id_dir=None):
        self.tools = orchestrator
        self.registry = orchestrator.registry
        self.workspace = orchestrator._workspace_client
        self.process = process or NativeProcessAdapter(orchestrator.tmux, orchestrator._ssh_manager)
        self.runtime = runtime or InstalledNativeRuntime(orchestrator)
        self.authority = authority or WorkspaceHandoffAuthority()
        self.pm_db_path = Path(pm_db_path or os.environ.get(
            "IRONCLAUDE_PM_DB_PATH", Path.home() / ".claude/ironclaude.db"))
        self.native_log_root = Path(native_log_root or Path.home())
        self.session_id_dir = Path(session_id_dir or Path.home() / ".claude")

    def _validate_quota(self, worker: dict, generation: int, proposed: dict,
                        *, require_same_event: bool = True) -> dict:
        client, native_id = worker["client"], worker["native_session_id"]
        if client == "claude":
            candidates = list((self.native_log_root / ".claude/projects").glob(f"*/{native_id}.jsonl"))
        elif client == "codex":
            candidates = list((self.native_log_root / ".codex/sessions").glob(f"**/*{native_id}*.jsonl"))
        else:
            raise HandoffHold("native_quota_client_unsupported")
        if len(candidates) != 1 or not candidates[0].is_file():
            raise HandoffHold("native_quota_log_ambiguous_or_missing")
        handoff = self.registry.get_worker_handoff(worker["id"])
        active = (handoff is not None and handoff["generation"] == generation
                  and handoff["completed_at"] is None)
        if active:
            evidence = _evidence(handoff)
            cached = evidence.get("quota_verdict")
            progress = evidence.get("quota_progress")
            if (isinstance(progress, dict) and isinstance(cached, dict)
                    and progress.get("file_identity") == cached.get("file_identity")
                    and type(progress.get("cursor")) is int
                    and type(cached.get("cursor")) is int
                    and progress["cursor"] > cached["cursor"]):
                cached = progress
            expected_pause_reason = handoff["pause_reason"]
        else:
            saved = self.registry.get_native_quota_evidence(worker["id"])
            cached = saved["verdict"] if saved and saved["generation"] == generation else None
        valid_cache = (
            isinstance(cached, dict) and isinstance(cached.get("reader_state"), dict)
            and cached.get("client") == client and cached.get("native_session_id") == native_id
            and cached["reader_state"].get("workspace_path") == worker["workspace_path"]
            and str(cached.get("generation")) == str(generation)
            and cached.get("file_identity")
            and type(cached.get("cursor")) is int and cached["cursor"] >= 0
            and cached["reader_state"].get("cursor") == cached["cursor"]
            and cached["reader_state"].get("file_identity") == cached["file_identity"]
        )
        started = time.monotonic()
        cursor = cached["cursor"] if valid_cache else 0
        reader_state = cached["reader_state"] if valid_cache else None
        identity = cached["file_identity"] if valid_cache else None
        while True:
            actual = read_native_quota(candidates[0], client=client, native_session_id=native_id,
                                       workspace_path=worker["workspace_path"], generation=str(generation),
                                       cursor=cursor, reader_state=reader_state,
                                       expected_file_identity=identity)
            if actual.get("reason") != "native_log_requires_cursor_continuation":
                break
            if time.monotonic() - started > 15:
                progress = {**actual, "state": "unknown",
                            "reason": "native_quota_log_scan_budget_exceeded"}
                try:
                    if active:
                        local = getattr(self, "_reconcile_local", None)
                        if local is None or (worker["id"], generation) not in getattr(local, "active", set()):
                            raise HandoffHold("handoff_reconcile_owner_unproved")
                        owner_nonce = getattr(local, "nonces", {}).get((worker["id"], generation))
                        if not owner_nonce:
                            raise HandoffHold("handoff_reconcile_owner_unproved")
                        self.registry.record_worker_handoff_quota_progress(
                            worker["id"], generation, progress,
                            snapshot=self.registry.get_worker(worker["id"]),
                            owner_nonce=owner_nonce,
                            expected_pause_reason=expected_pause_reason,
                        )
                    else:
                        self.registry.record_native_quota_verdict(
                            worker["id"], generation, progress, snapshot=worker,
                        )
                except HandoffHold:
                    raise
                except RuntimeError as exc:
                    raise HandoffHold("native_quota_progress_unproved") from exc
                raise HandoffHold("native_quota_log_scan_budget_exceeded")
            cursor = actual["cursor"]
            reader_state = actual["reader_state"]
            identity = actual["file_identity"]
        if (actual.get("state") != "exhausted"
                or actual.get("file_identity") != proposed.get("file_identity")
                or (require_same_event and (
                    actual.get("event_key") != proposed.get("event_key")
                    or actual.get("cursor") != proposed.get("cursor")))):
            raise HandoffHold("native_quota_evidence_changed")
        return actual

    def _verify_hook_binding(self, snapshot: dict, native_id: str, client: str) -> None:
        processes = {item["pid"]: item for item in snapshot["processes"]}
        producer_pids = set(snapshot.get("producer_pids", ()))
        pids = [snapshot.get("pane_pid")] if client == "claude" else list(processes)
        matches = 0
        for pid in pids:
            if type(pid) is not int:
                continue
            identity = processes.get(pid)
            if identity is None:
                continue
            if client == "codex":
                cursor = pid
                seen = set()
                while cursor not in producer_pids and cursor in processes and cursor not in seen:
                    seen.add(cursor)
                    cursor = processes[cursor]["ppid"]
                if cursor not in producer_pids:
                    continue
            path = self.session_id_dir / f"ironclaude-session-{pid}.id"
            try:
                if (path.stat().st_mtime >= identity["creation_time"]
                        and path.read_text(encoding="utf-8").strip() == native_id):
                    matches += 1
            except OSError:
                continue
        if matches < 1:
            raise HandoffHold("source_process_native_binding_unproved")

    @staticmethod
    def _host(worker: dict) -> str:
        return worker.get("machine") or "local"

    def _quarantine_validated_source(self, worker: dict, generation: int, quota: dict) -> None:
        """Record each current native exhaustion before another source-side effect."""
        if quota.get("state") != "exhausted" or not quota.get("event_key"):
            raise HandoffHold("native_quota_evidence_changed")
        current = self.registry.get_worker(worker["id"])
        if current is None:
            raise HandoffHold("source_runtime_changed")
        handoff = self.registry.get_worker_handoff(worker["id"])
        if handoff and handoff["completed_at"] is None:
            if handoff["generation"] != generation or handoff["phase"] != "claimed":
                raise HandoffHold("source_runtime_changed")
            source = json.loads(handoff["snapshot_json"])
            expected = {**source, "status": "handoff_pending"}
        else:
            expected = self.registry._snapshot(worker)
        if self.registry._snapshot(current) != expected:
            raise HandoffHold("source_runtime_changed")
        tier = self.registry.effective_worker_tier(current)
        if tier is None:
            raise HandoffHold("worker_effective_tier_unproved")
        _router, _config, state, _capabilities = self.tools._provider_router()
        state.mark_unavailable(self._host(current), current["client"], "worker",
                               state.worker_usage_scope_tier("worker", tier),
                               "usage", "native_usage_exhausted",
                               usage_generation=f"{current['native_session_id']}:{quota['file_identity']}:{generation}",
                               usage_cursor=quota["cursor"], usage_event_key=quota["event_key"],
                               usage_event_timestamp=quota.get("event_timestamp"),
                               usage_expires_at=self._quarantine_expires_at(quota))

    def _quarantine_expires_at(self, quota: dict) -> float:
        """Native reset time when it follows the event, capped at the event time plus
        _QUARANTINE_MAX_SECONDS (now plus the cap when the event time cannot be parsed);
        else the configured default (H9)."""
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
            limit = (event if event is not None else time.time()) + _QUARANTINE_MAX_SECONDS
            return float(min(reset_at, limit))
        return time.time() + float(default)

    @staticmethod
    def _transport(worker: dict, root: str) -> dict:
        if worker.get("machine"):
            return {"ssh_host": worker["machine"], "remote_plugin_root": root}
        return {"plugin_root": root}

    def _target(self, worker: dict, *, allow_source_client: bool = False):
        from ironclaude.orchestrator_mcp import _WORKER_TYPE_TIER
        from ironclaude.provider_capabilities import CapabilityProbe
        tier = _WORKER_TYPE_TIER.get(worker["type"])
        if tier is None:
            raise HandoffHold("unsupported_worker_tier")
        host = self._host(worker)
        if host != "local":
            raise HandoffHold("remote_native_process_proof_unavailable")
        router, config, _state, capabilities = self.tools._provider_router()
        probe = CapabilityProbe()
        for client in config.roles["worker"].clients:
            if client == worker["client"] and not allow_source_client:
                continue
            for probe_tier in ((tier, "opus") if tier == "fable" else (tier,)):
                capabilities.record(probe.probe_local(config, client, "worker", probe_tier))
        try:
            handle = router.resolve("worker", tier, [host],
                                    excluded_clients=() if allow_source_client else (worker["client"],))
        except Exception as exc:
            raise HandoffHold("opposite_capability_unavailable") from exc
        if (handle.client == worker["client"] and not allow_source_client) or handle.host != host:
            raise HandoffHold("opposite_capability_unavailable")
        return handle, tier

    def _source_preflight(self, worker: dict, generation: int) -> tuple[dict, dict, str]:
        if worker.get("machine"):
            raise HandoffHold("remote_native_process_proof_unavailable")
        path = Path(worker["workspace_path"])
        if not path.is_dir() or not all((path / name).is_file() for name in ("AGENTS.md", "CLAUDE.md")):
            raise HandoffHold("workspace_instructions_missing")
        root = self.workspace.discover_installed_plugin_root(worker["client"])
        transport = self._transport(worker, root)
        status = self.workspace.handoff_status({"workspace_guid": worker["workspace_guid"]}, **transport)
        prior_finished = (status.get("present") is True and status.get("phase") == "finished"
                          and status.get("generation") == generation - 1
                          and (status.get("target_owner_session_id") == worker["native_session_id"]
                               # Released by this runtime (H5/H6a): the source still owned the
                               # assignment, whether or not a target had been authorized.
                               or status.get("source_owner_session_id") == worker["native_session_id"]))
        if status.get("protocol_version") != 1:
            raise HandoffHold("workspace_handoff_protocol_unavailable")
        head = _git_head(worker["workspace_path"])
        scope = {
            "repository_path": worker["repo"],
            "repository_identity": worker["workspace_repository_identity"],
            "workspace_guid": worker["workspace_guid"],
            "worker_id": worker["id"],
            "source_owner_session_id": worker["native_session_id"],
            "generation": generation,
            "expected_worktree_path": worker["workspace_path"],
            "expected_branch": worker["workspace_branch"],
            "expected_base_commit": worker["workspace_base_commit"],
            "expected_current_head": head,
            "expected_integration_target": worker["workspace_integration_target"],
        }
        if not (status.get("present") is False or prior_finished):
            row = self.registry.get_worker_handoff(worker["id"])
            evidence = _evidence(row) if row and row["generation"] == generation else {}
            if (not evidence.get("begin_proved") or evidence.get("scope") != scope
                    or status.get("phase") != "begun"):
                raise HandoffHold("workspace_handoff_protocol_unavailable")
            self._validate_scope_readback({"scope": scope}, status)
        return scope, transport, head

    def _preflight(self, worker: dict, handle, generation: int) -> tuple[dict, dict, str]:
        scope, transport, head = self._source_preflight(worker, generation)
        self.runtime.preflight(handle.client)
        return scope, transport, head

    def _status(self, worker_id: str) -> dict:
        row = self.registry.get_worker_handoff(worker_id)
        if row is None:
            return {"status": "unclaimed", "worker_id": worker_id}
        if row["completed_at"]:
            state = "released" if row["phase"] == "released" else "completed"
        else:
            state = "held" if row["pause_reason"] else "pending"
        status = {"status": state,
                  "worker_id": worker_id, "generation": row["generation"],
                  "phase": row["phase"], "reason": row["pause_reason"]}
        observed = _evidence(row).get("observed_model")
        if isinstance(observed, str) and observed:
            status["observed_model"] = observed
        return status

    def _pause(self, worker_id: str, generation: int, reason: str) -> dict:
        self.registry.pause_worker_handoff(worker_id, generation, reason)
        return self._status(worker_id)

    def handoff(self, worker_id: str, quota_verdict: dict) -> dict:
        worker = self.registry.get_worker(worker_id)
        if not worker or worker.get("status") != "running":
            return self._status(worker_id)
        latest = self.registry.get_worker_handoff(worker_id)
        generation = 1 if latest is None else latest["generation"] + 1
        if (not isinstance(quota_verdict, dict) or quota_verdict.get("state") != "exhausted"
                or quota_verdict.get("client") != worker.get("client")
                or quota_verdict.get("native_session_id") != worker.get("native_session_id")
                or str(quota_verdict.get("generation")) != str(generation)
                or not quota_verdict.get("event_key")):
            return {"status": "rejected", "worker_id": worker_id, "reason": "native_quota_evidence_invalid"}
        try:
            quota_verdict = self._validate_quota(worker, generation, quota_verdict)
            self.registry.record_native_quota_verdict(worker_id, generation, quota_verdict, snapshot=worker)
            self._quarantine_validated_source(worker, generation, quota_verdict)
        except HandoffHold as exc:
            return {"status": "rejected", "worker_id": worker_id, "reason": str(exc)}
        except Exception:
            return {"status": "rejected", "worker_id": worker_id, "reason": "native_quota_validation_failed"}
        try:
            pm = snapshot_pm_state(self.pm_db_path, worker["native_session_id"],
                                   _project_hash(self.pm_db_path, worker["native_session_id"]))
            if pm["session"]["workflow_stage"] == "plan_interrupted":
                raise HandoffHold("operator_paused_pm")
            objective = _continuation_objective(worker, pm)
            scope, transport, head = self._source_preflight(worker, generation)
            source = self.process.snapshot(worker["tmux_session"], worker["workspace_path"], worker["client"])
            if not source.get("processes"):
                raise HandoffHold("source_process_unproved")
            self._verify_hook_binding(source, worker["native_session_id"], worker["client"])
        except Exception as exc:
            reason = str(exc) if isinstance(exc, HandoffHold) else "source_preflight_failed"
            return {"status": "rejected", "worker_id": worker_id, "reason": reason}
        nonce = secrets.token_hex(16)
        try:
            self.registry.claim_worker_handoff(
                worker, generation=generation, reconcile_nonce=nonce,
                initial_evidence={
                    "quota_verdict": quota_verdict, "scope": scope, "transport": transport,
                    "pm_snapshot": pm, "source_process": source,
                    "source_head": head, "continuation_objective": objective,
                },
            )
        except Exception:
            return {"status": "rejected", "worker_id": worker_id, "reason": "handoff_claim_failed"}
        with self._reconcile_scope(worker_id, generation, preclaimed_nonce=nonce) as denial:
            if denial:
                return {**self._status(worker_id), "reason": denial}
            try:
                self._begin_source_hold(worker_id, generation)
                handle, _tier = self._target(worker)
                scope, transport, head = self._preflight(worker, handle, generation)
                self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {
                    "scope": scope, "transport": transport, "target_client": handle.client,
                    "target_model": handle.model, "target_tier": handle.effective_tier,
                    "source_head": head,
                })
            except Exception as exc:
                reason = str(exc) if isinstance(exc, HandoffHold) else "preflight_failed"
                return self._pause(worker_id, generation, reason)
            return self.reconcile(worker_id)

    PREEFFECT_RETRY_REASONS = frozenset({
        "opposite_capability_unavailable", "workspace_instructions_missing",
        "workspace_handoff_protocol_unavailable", "native_readonly_or_resume_flags_unsupported",
        "preflight_failed", "source_process_unproved", "source_process_native_binding_unproved",
        "remote_native_process_proof_unavailable", "target_preflight_pending",
        "native_quota_log_scan_budget_exceeded",
    })
    POST_STOP_RETRY_REASONS = frozenset({
        "opposite_capability_unavailable", "target_preflight_pending",
        "native_readonly_or_resume_flags_unsupported", "workspace_quiescence_unproved",
        "source_stop_unproved", "handoff_reconcile_owner_unproved",
        # H6a: after the transfer, a resume, dispatch or finish proof is retried forward.
        "target_resume_session_already_exists", "target_resume_launch_failed",
        "target_resume_readiness_unproved", "target_resume_identity_unproved",
        "bootstrap_stop_unproved", "continuation_dispatch_unproved", "assignment_finish_unproved",
    })
    POST_TRANSFER_PHASES = ("owner_transferred", "runtime_registered", "continuation_dispatched")

    @staticmethod
    def _source_stop_started(row: dict, evidence: dict) -> bool:
        """True once a source stop effect was attempted or proved (the H5/H6 boundary)."""
        return (row["phase"] != "claimed" or bool(evidence.get("stop_intent"))
                or bool(evidence.get("source_stop_proof")))

    def retry_paused_preflight(self, worker_id: str) -> dict:
        """Recover a paused handoff by how far it got: retry or release before the source stop."""
        row = self.registry.get_worker_handoff(worker_id)
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
            if row["phase"] == "runtime_registered" and row["pause_reason"] == "continuation_dispatch_unproved":
                # R3: a target session tmux positively reports absent can never receive the
                # continuation, so preserve as the target instead of replaying. A tmux error
                # keeps the hold; a present session keeps the replay below.
                try:
                    target_alive = self.tools.tmux.has_session(evidence["target_session"])
                except Exception:
                    return self._status(worker_id)
                if not target_alive:
                    return self._preserve_after_stop(worker_id)
            if row["pause_reason"] in self.POST_STOP_RETRY_REASONS:
                return self._retry_forward(worker_id)
            return self._preserve_after_stop(worker_id)
        if (row["pause_reason"] not in self.PREEFFECT_RETRY_REASONS
                or any(key.endswith("_intent") and value for key, value in evidence.items())):
            # H5: a pre-effect pause that can never succeed on retry releases.
            return self.release_paused(worker_id)
        generation = row["generation"]
        source = json.loads(row["snapshot_json"])
        current = self.registry.get_worker(worker_id)
        expected = {**source, "status": "handoff_pending"}
        if current is None or self.registry._snapshot(current) != expected:
            return self._status(worker_id)
        with self._reconcile_scope(worker_id, generation, allow_paused=True) as denial:
            if denial:
                return {**self._status(worker_id), "reason": denial}
            resumed = False
            try:
                quota = self._validate_quota(source, generation, evidence["quota_verdict"],
                                             require_same_event=False)
                self._quarantine_validated_source(source, generation, quota)
                pm = snapshot_pm_state(self.pm_db_path, source["native_session_id"],
                                       _project_hash(self.pm_db_path, source["native_session_id"]))
                if pm["session"]["workflow_stage"] == "plan_interrupted":
                    raise HandoffHold("operator_paused_pm")
                objective = _continuation_objective(source, pm)
                process = self.process.snapshot(source["tmux_session"], source["workspace_path"], source["client"])
                self._verify_hook_binding(process, source["native_session_id"], source["client"])
                if quota.get("state") != "exhausted":
                    raise HandoffHold("native_quota_evidence_changed")
                self.registry.resume_worker_handoff(worker_id, generation, "claimed")
                resumed = True
                self._begin_source_hold(worker_id, generation)
                handle, _tier = self._target(source)
                scope, transport, head = self._preflight(source, handle, generation)
                self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {
                    "quota_verdict": quota,
                    "scope": scope, "transport": transport, "pm_snapshot": pm,
                    "source_process": process, "target_client": handle.client,
                    "target_model": handle.model, "target_tier": handle.effective_tier,
                    "source_head": head, "continuation_objective": objective,
                })
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

    def _bootstrap_proven_gone(self, evidence: dict, phase: str | None = None) -> bool:
        """A recorded target bootstrap counts as gone only on PID plus start-time proof (H7).

        H7b: when the worktree directory no longer exists, every recorded process must be
        proved absent by PID plus start time. H7a: with only a launch intent and no
        recorded process, the bootstrap tmux session must be absent and no process may
        remain associated with the worktree.

        R1: once a target session may occupy the worktree (a post-transfer phase, or
        evidence of bootstrap_stopped, resume_intent or target_session), worktree
        quiescence cannot be required because the target legitimately runs there. A
        recorded bootstrap is then proved gone by PID plus start time alone, and a
        launch-intent-only bootstrap by its own tmux session being absent. Unreadable
        identities and tmux errors stay unproved.
        """
        target_may_occupy = (phase in self.POST_TRANSFER_PHASES
                             or any(evidence.get(key) for key in ("bootstrap_stopped", "resume_intent",
                                                                  "target_session")))
        snapshot = evidence.get("bootstrap_process")
        if isinstance(snapshot, dict):
            try:
                if target_may_occupy or not Path(evidence["scope"]["expected_worktree_path"]).is_dir():
                    return bool(self.process.proved_absent(snapshot))
                return bool(self.process.verify_stopped(snapshot).get("stopped"))
            except Exception:
                return False
        name = evidence.get("bootstrap_intent")
        if name:
            try:
                if self.tools.tmux.has_session(name):
                    return False
                if target_may_occupy:
                    return True
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
            return self._bootstrap_proven_gone(evidence, phase)
        if self._bootstrap_proven_gone({**evidence, "bootstrap_process": snapshot}, phase):
            # R2: the bootstrap is already gone; record the proof instead of signalling an
            # exited tree (stop_and_prove refuses once the recorded pane root has exited).
            if evidence.get("bootstrap_stopped"):
                return True
            try:
                self.registry.resume_worker_handoff(worker_id, generation, phase)
                try:
                    self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                        "bootstrap_process": snapshot, "bootstrap_stop_intent": False,
                        "bootstrap_stopped": True,
                        "bootstrap_stop_proof": {
                            "stopped": True, "proved_absent": True,
                            "source_pids": [item["pid"] for item in snapshot["processes"]]}})
                finally:
                    self.registry.pause_worker_handoff(worker_id, generation, reason)
            except Exception:
                return False
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
                if abort:
                    if not self._bootstrap_proven_gone(evidence, phase):
                        return {**self._status(worker_id), refusal_key: "target_bootstrap_alive_or_unproved"}
                elif not self._stop_bootstrap(worker_id, generation, phase, reason, evidence):
                    return {**self._status(worker_id), refusal_key: "target_bootstrap_stop_unproved"}
                return self._release_to_target(worker_id, generation, phase, reason, evidence, refusal_key)
            if abort:
                if not self._bootstrap_proven_gone(evidence, phase):
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

    @contextmanager
    def _reconcile_scope(self, worker_id: str, generation: int, *, allow_paused: bool = False,
                         preclaimed_nonce: str | None = None):
        """Keep one durable effect owner through setup and nested phase calls.

        Yields None when owned, otherwise a bounded denial reason. A rival
        controller never pauses the assignment merely because owner is busy.
        """
        local = getattr(self, "_reconcile_local", None)
        if local is None:
            local = self._reconcile_local = threading.local()
        active = getattr(local, "active", None)
        if active is None:
            active = local.active = set()
        key = (worker_id, generation)
        if key in active:
            yield None
            return
        nonce = preclaimed_nonce or secrets.token_hex(16)
        if preclaimed_nonce:
            try:
                import psutil
                row = self.registry.get_worker_handoff(worker_id)
                owner = _evidence(row)["reconcile_owner"]
                claimed = (row["generation"] == generation and owner["nonce"] == nonce
                           and owner["pid"] == os.getpid()
                           and owner["started"] == psutil.Process(os.getpid()).create_time())
            except Exception:
                claimed = False
            if not claimed:
                yield "handoff_reconcile_owner_unproved"
                return
        else:
            try:
                claimed = self.registry.claim_worker_handoff_reconcile(
                    worker_id, generation, nonce, allow_paused=allow_paused)
            except Exception:
                yield "handoff_reconcile_owner_unproved"
                return
            if not claimed:
                yield "handoff_reconcile_owned"
                return
        active.add(key)
        nonces = getattr(local, "nonces", None)
        if nonces is None:
            nonces = local.nonces = {}
        nonces[key] = nonce
        try:
            yield None
        finally:
            active.remove(key)
            nonces.pop(key, None)
            self.registry.release_worker_handoff_reconcile(worker_id, generation, nonce)

    def reconcile(self, worker_id: str) -> dict:
        row = self.registry.get_worker_handoff(worker_id)
        if not row or row["completed_at"] or row["pause_reason"]:
            return self._status(worker_id)
        with self._reconcile_scope(worker_id, row["generation"]) as denial:
            if denial:
                return {**self._status(worker_id), "reason": denial}
            return self._reconcile_owned(worker_id)

    def _reconcile_owned(self, worker_id: str) -> dict:
        row = self.registry.get_worker_handoff(worker_id)
        if not row or row["completed_at"] or row["pause_reason"]:
            return self._status(worker_id)
        generation = row["generation"]
        phase = row["phase"]
        evidence = _evidence(row)
        # A started external effect cannot be replayed from an uncertain response.
        if any(key.endswith("_intent") and value for key, value in evidence.items()):
            try:
                return self._reconcile_intent(worker_id, generation, phase, evidence)
            except Exception:
                return self._pause(worker_id, generation, "uncertain_external_effect")
        try:
            if phase == "claimed":
                return self._stop_source(worker_id, generation, evidence)
            if phase == "source_stopped":
                return self._start_target(worker_id, generation, evidence)
            if phase == "target_ready":
                return self._prepare_pm(worker_id, generation, evidence)
            if phase == "pm_prepared":
                return self._transfer_owner(worker_id, generation, evidence)
            if phase == "owner_transferred":
                return self._resume_target(worker_id, generation, evidence)
            if phase == "runtime_registered":
                return self._dispatch(worker_id, generation, evidence)
            if phase == "continuation_dispatched":
                return self._finish(worker_id, generation, evidence)
            raise HandoffHold("unknown_phase")
        except HandoffHold as exc:
            return self._pause_after_pretransfer_failure(worker_id, generation, phase, evidence, str(exc))
        except Exception:
            return self._pause_after_pretransfer_failure(worker_id, generation, phase, evidence,
                                                         "handoff_effect_or_proof_failed")

    def _pause_after_pretransfer_failure(self, worker_id, generation, phase, evidence, reason):
        """Stop a proved read-only bootstrap before preserving a pre-transfer hold."""
        if (phase not in {"target_ready", "pm_prepared"} or not evidence.get("target_id")
                or not isinstance(evidence.get("bootstrap_process"), dict)
                or any(key.endswith("_intent") and value for key, value in evidence.items())):
            return self._pause(worker_id, generation, reason)
        try:
            state = self._workspace_readback(evidence)
            if (state.get("phase") != "begun"
                    or state.get("target_owner_session_id") is not None):
                return self._pause(worker_id, generation, reason)
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                "cleanup_intent": True, "pretransfer_failure_reason": reason})
            proof = self.process.stop_and_prove(evidence["bootstrap_process"])
            if not proof.get("stopped"):
                raise HandoffHold("bootstrap_cleanup_unproved")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                "cleanup_intent": False, "bootstrap_cleanup_proof": proof})
        except Exception:
            return self._pause(worker_id, generation, "bootstrap_cleanup_unproved")
        return self._pause(worker_id, generation, reason)

    def _workspace_readback(self, evidence: dict) -> dict:
        scope = evidence["scope"]
        state = self.workspace.handoff_status({"workspace_guid": scope["workspace_guid"]},
                                               **evidence["transport"])
        self._validate_scope_readback(evidence, state)
        return state

    @staticmethod
    def _validate_scope_readback(evidence: dict, state: dict) -> None:
        scope = evidence["scope"]
        fields = {
            "workspace_guid": "workspace_guid", "generation": "generation",
            "repository_identity": "repository_identity", "worker_id": "worker_id",
            "source_owner_session_id": "source_owner_session_id",
            "worktree_path": "expected_worktree_path", "branch": "expected_branch",
            "base_commit": "expected_base_commit", "current_head": "expected_current_head",
            "integration_target": "expected_integration_target",
        }
        target = evidence.get("target_id")
        if (state.get("protocol_version") != 1
                or any(state.get(actual) != scope[expected] for actual, expected in fields.items())
                or (target and state.get("target_owner_session_id") not in (None, target))
                or (state.get("phase") in {"transferred", "finished"}
                    and target and state.get("target_owner_session_id") != target)):
            raise HandoffHold("workspace_handoff_readback_mismatch")

    def _reconcile_intent(self, worker_id, generation, phase, evidence):
        if phase in {"target_ready", "pm_prepared"} and evidence.get("cleanup_intent"):
            proof = self.process.verify_stopped(evidence["bootstrap_process"])
            if not proof.get("stopped"):
                raise HandoffHold("bootstrap_cleanup_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                "cleanup_intent": False, "bootstrap_cleanup_proof": proof})
            return self._pause(worker_id, generation,
                               evidence.get("pretransfer_failure_reason", "pretransfer_failure"))
        if phase == "claimed" and evidence.get("grant_intent"):
            scope = evidence["scope"]
            # Same persisted token makes a committed grant readback/replay exact.
            self.authority.grant(
                workspace_guid=scope["workspace_guid"], generation=generation,
                repository_identity=scope["repository_identity"], worker_id=worker_id,
                worktree_path=scope["expected_worktree_path"], branch=scope["expected_branch"],
                base_commit=scope["expected_base_commit"], current_head=scope["expected_current_head"],
                integration_target=scope["expected_integration_target"],
                source_owner_session_id=scope["source_owner_session_id"],
                capability_token=evidence["capability_token"],
            )
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase,
                {"grant_intent": False, "grant_proved": True})
            return self.reconcile(worker_id)
        if phase == "claimed" and evidence.get("begin_intent"):
            state = self._workspace_readback(evidence)
            if state.get("phase") != "begun":
                raise HandoffHold("begin_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase,
                {"begin_intent": False, "begin_proved": True})
            return self.reconcile(worker_id)
        if phase == "claimed" and evidence.get("stop_intent"):
            proof = self.process.verify_stopped(evidence["source_process"])
            if not proof.get("stopped"):
                raise HandoffHold("stop_outcome_unknown")
            frozen_pm = snapshot_pm_state(self.pm_db_path, evidence["scope"]["source_owner_session_id"],
                                          evidence["pm_snapshot"]["project_hash"])
            if frozen_pm["session"]["workflow_stage"] == "plan_interrupted":
                raise HandoffHold("operator_paused_pm")
            return self._advance(worker_id, generation, phase, "source_stopped",
                                 {"stop_intent": False, "source_stop_proof": proof,
                                  "pm_snapshot": frozen_pm})
        if phase == "pm_prepared" and evidence.get("transfer_intent"):
            state = self._workspace_readback(evidence)
            if state.get("phase") != "transferred" or state.get("target_owner_session_id") != evidence["target_id"]:
                raise HandoffHold("transfer_outcome_unknown")
            return self._advance(worker_id, generation, phase, "owner_transferred", {"transfer_intent": False})
        if phase == "pm_prepared" and evidence.get("authorize_intent"):
            state = self.authority.readback(workspace_guid=evidence["scope"]["workspace_guid"])
            self._validate_scope_readback(evidence, state)
            if state.get("phase") != "begun" or state.get("generation") != generation or state.get("target_owner_session_id") != evidence["target_id"]:
                raise HandoffHold("target_authorization_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase,
                {"authorize_intent": False, "authorize_proved": True})
            return self.reconcile(worker_id)
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
            proof = self.process.verify_stopped(evidence["bootstrap_process"])
            if not proof.get("stopped"):
                raise HandoffHold("bootstrap_stop_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase,
                {"bootstrap_stop_intent": False, "bootstrap_stopped": True})
            return self.reconcile(worker_id)
        if phase == "owner_transferred" and evidence.get("resume_intent"):
            name = evidence["resume_intent"]
            if not self.runtime.inspect_resumed(name, evidence["target_client"], evidence["target_id"],
                                                not_before=evidence["resume_started_at"]):
                raise HandoffHold("target_resume_outcome_unknown")
            self._register_target(worker_id, generation, evidence, name)
            return self._advance(worker_id, generation, phase, "runtime_registered",
                                 {"resume_intent": False, "target_session": name})
        if phase == "continuation_dispatched" and evidence.get("finish_intent"):
            state = self._workspace_readback(evidence)
            if state.get("phase") != "finished" or state.get("target_owner_session_id") != evidence["target_id"]:
                raise HandoffHold("finish_outcome_unknown")
            self.registry.record_worker_handoff_evidence(worker_id, generation, phase,
                {"finish_intent": False, "assignment_finished": True})
            self.registry.complete_worker_handoff(worker_id, generation, phase)
            return self._status(worker_id)
        raise HandoffHold("nonreplayable_external_intent")

    def _advance(self, worker_id, generation, old, new, evidence):
        self.registry.advance_worker_handoff(worker_id, generation, old, new, evidence=evidence)
        return self.reconcile(worker_id)

    def _begin_source_hold(self, worker_id: str, generation: int) -> dict:
        row = self.registry.get_worker_handoff(worker_id)
        if (not row or row["generation"] != generation or row["phase"] != "claimed"
                or row["pause_reason"] or row["completed_at"]):
            raise HandoffHold("source_hold_claim_missing")
        evidence = _evidence(row)
        scope = evidence["scope"]
        if not evidence.get("begin_proved"):
            if not evidence.get("grant_proved"):
                token = secrets.token_urlsafe(32)
                self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {
                    "capability_token": token, "grant_intent": True})
                self.authority.grant(
                    workspace_guid=scope["workspace_guid"], generation=generation,
                    repository_identity=scope["repository_identity"], worker_id=worker_id,
                    worktree_path=scope["expected_worktree_path"], branch=scope["expected_branch"],
                    base_commit=scope["expected_base_commit"], current_head=scope["expected_current_head"],
                    integration_target=scope["expected_integration_target"],
                    source_owner_session_id=scope["source_owner_session_id"],
                    capability_token=token,
                )
                self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {
                    "grant_intent": False, "grant_proved": True})
            else:
                token = evidence["capability_token"]
            payload = {**scope, "capability_token": token}
            # Token remains in controller-only DB evidence, never worker prompt or log.
            self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {"begin_intent": True})
            begun = self.workspace.begin_handoff(payload, **evidence["transport"])
            if begun.get("protocol_version") != 1 or begun.get("phase") != "begun":
                raise HandoffHold("assignment_hold_unproved")
            self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {"begin_intent": False, "begin_proved": True})
        held = _evidence(self.registry.get_worker_handoff(worker_id))
        state = self._workspace_readback(held)
        if state.get("phase") != "begun" or state.get("target_owner_session_id") is not None:
            raise HandoffHold("assignment_hold_unproved")
        return held

    def _stop_source(self, worker_id, generation, evidence):
        evidence = self._begin_source_hold(worker_id, generation)
        if not all(evidence.get(key) for key in ("target_client", "target_model", "source_process")):
            raise HandoffHold("target_preflight_pending")
        scope = evidence["scope"]
        source = evidence["source_process"]
        if _git_head(scope["expected_worktree_path"]) != scope["expected_current_head"]:
            raise HandoffHold("source_workspace_head_changed")
        fresh = self.process.snapshot(source["session_name"], scope["expected_worktree_path"],
                                      self.registry.get_worker(worker_id)["client"])
        if fresh != source:
            raise HandoffHold("source_process_changed_before_stop")
        current_pm = snapshot_pm_state(self.pm_db_path, scope["source_owner_session_id"],
                                       evidence["pm_snapshot"]["project_hash"])
        if current_pm["session"]["workflow_stage"] == "plan_interrupted":
            raise HandoffHold("operator_paused_pm")
        current_worker = self.registry.get_worker(worker_id)
        quota = self._validate_quota(current_worker, generation, evidence["quota_verdict"],
                                     require_same_event=False)
        self._quarantine_validated_source(current_worker, generation, quota)
        self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {
            "quota_verdict": quota,
        })
        self.registry.record_worker_handoff_evidence(worker_id, generation, "claimed", {"stop_intent": True})
        proof = self.process.stop_and_prove(source)
        if not proof.get("stopped"):
            raise HandoffHold("source_stop_unproved")
        quiet = self.process.assert_quiescent(scope["expected_worktree_path"])
        if not quiet.get("quiescent"):
            raise HandoffHold("workspace_quiescence_unproved")
        frozen_pm = snapshot_pm_state(self.pm_db_path, scope["source_owner_session_id"],
                                      evidence["pm_snapshot"]["project_hash"])
        if frozen_pm["session"]["workflow_stage"] == "plan_interrupted":
            raise HandoffHold("operator_paused_pm")
        return self._advance(worker_id, generation, "claimed", "source_stopped", {
            "stop_intent": False, "source_stop_proof": proof,
            "quiescence_proof": quiet, "pm_snapshot": frozen_pm})

    def _start_target(self, worker_id, generation, evidence):
        name = f"ic-{worker_id}-h{generation}-bootstrap"
        self.registry.record_worker_handoff_evidence(worker_id, generation, "source_stopped", {"bootstrap_intent": name})
        target = self.runtime.bootstrap(name, evidence["target_client"], evidence["target_model"],
                                        evidence["scope"]["expected_worktree_path"], worker_id)
        if target.get("client") != evidence["target_client"] or target.get("model") != evidence["target_model"]:
            raise HandoffHold("target_identity_unproved")
        try:
            target_id = str(uuid.UUID(target["native_session_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise HandoffHold("target_identity_unproved") from exc
        if target_id == evidence["scope"]["source_owner_session_id"] or not target.get("persisted") or not target.get("ack_complete"):
            raise HandoffHold("target_persistence_unproved")
        if not isinstance(target.get("observed_model"), str) or not target["observed_model"]:
            raise HandoffHold("target_model_unproved")
        bootstrap_process = self.process.snapshot(name, evidence["scope"]["expected_worktree_path"],
                                                  evidence["target_client"])
        return self._advance(worker_id, generation, "source_stopped", "target_ready", {
            "bootstrap_intent": False, "target_id": target_id,
            "bootstrap_session": name, "bootstrap_process": bootstrap_process,
            "observed_model": target["observed_model"]})

    def _prepare_pm(self, worker_id, generation, evidence):
        result = prepare_pm_handoff(self.pm_db_path, evidence["pm_snapshot"], evidence["target_id"], generation)
        return self._advance(worker_id, generation, "target_ready", "pm_prepared", {"pm_digest": result["target_digest"]})

    def _transfer_owner(self, worker_id, generation, evidence):
        scope = evidence["scope"]
        token = evidence["capability_token"]
        # H8: a new controller has no in-memory lease; rebuild it from the persisted identity.
        self.process.adopt_readonly_bootstrap(evidence["bootstrap_process"])
        allowed = [item["pid"] for item in evidence["bootstrap_process"]["processes"]]
        if not self.process.assert_quiescent(scope["expected_worktree_path"], allowed_pids=allowed).get("quiescent"):
            raise HandoffHold("workspace_quiescence_unproved")
        if not evidence.get("authorize_proved"):
            self.registry.record_worker_handoff_evidence(worker_id, generation, "pm_prepared", {"authorize_intent": True})
            self.authority.authorize_target(workspace_guid=scope["workspace_guid"], generation=generation,
                                            capability_token=token, source_owner_session_id=scope["source_owner_session_id"],
                                            target_owner_session_id=evidence["target_id"])
            self.registry.record_worker_handoff_evidence(worker_id, generation, "pm_prepared", {"authorize_intent": False, "authorize_proved": True})
        self.registry.record_worker_handoff_evidence(worker_id, generation, "pm_prepared", {"transfer_intent": True})
        result = self.workspace.transfer_handoff({**scope, "capability_token": token,
            "target_owner_session_id": evidence["target_id"]}, **evidence["transport"])
        if result.get("protocol_version") != 1 or result.get("phase") != "transferred" or result.get("target_owner_session_id") != evidence["target_id"]:
            raise HandoffHold("owner_transfer_unproved")
        return self._advance(worker_id, generation, "pm_prepared", "owner_transferred", {"transfer_intent": False})

    def _resume_target(self, worker_id, generation, evidence):
        bootstrap = evidence["bootstrap_session"]
        if not evidence.get("bootstrap_stopped"):
            snap = evidence["bootstrap_process"]
            self.registry.record_worker_handoff_evidence(worker_id, generation, "owner_transferred",
                {"bootstrap_process": snap, "bootstrap_stop_intent": True})
            proof = self.process.stop_and_prove(snap)
            if not proof.get("stopped"):
                raise HandoffHold("bootstrap_stop_unproved")
            self.registry.record_worker_handoff_evidence(worker_id, generation, "owner_transferred",
                {"bootstrap_stop_intent": False, "bootstrap_stopped": True})
        name = f"ic-{worker_id}-h{generation}"
        self.registry.record_worker_handoff_evidence(worker_id, generation, "owner_transferred", {
            "bootstrap_stop_intent": False, "resume_intent": name,
            "resume_started_at": time.time()})
        resumed = self.runtime.resume(name, evidence["target_client"], evidence["target_model"],
                                      evidence["target_id"], evidence["scope"]["expected_worktree_path"], worker_id)
        if resumed.get("native_session_id") != evidence["target_id"] or not resumed.get("ready"):
            raise HandoffHold("target_resume_identity_unproved")
        self._register_target(worker_id, generation, evidence, name)
        return self._advance(worker_id, generation, "owner_transferred", "runtime_registered", {"resume_intent": False, "target_session": name})

    def _register_target(self, worker_id, generation, evidence, name):
        current = self.registry.get_worker(worker_id)
        if (current and current.get("client") == evidence["target_client"]
                and current.get("model") == evidence["target_model"]
                and current.get("native_session_id") == evidence["target_id"]
                and current.get("tmux_session") == name):
            return
        self.registry.replace_worker_runtime(worker_id, generation, "owner_transferred", client=evidence["target_client"],
            model=evidence["target_model"], native_session_id=evidence["target_id"], tmux_session=name,
            machine=self.registry.get_worker(worker_id)["machine"])

    def _dispatch(self, worker_id, generation, evidence):
        worker = self.registry.get_worker(worker_id)
        if worker["native_session_id"] != evidence["target_id"] or worker["tmux_session"] != evidence["target_session"]:
            raise HandoffHold("target_runtime_changed")
        pm = readback_pm_handoff(self.pm_db_path, evidence["scope"]["source_owner_session_id"], evidence["target_id"], generation)
        if not pm or pm["target_digest"] != evidence["pm_digest"]:
            raise HandoffHold("pm_readback_failed")
        source = json.loads(self.registry.get_worker_handoff(worker_id)["snapshot_json"])
        objective = evidence.get("continuation_objective")
        if not isinstance(objective, str) or not objective.strip():
            raise HandoffHold("continuation_objective_missing")
        plan = evidence["pm_snapshot"]["session"].get("plan_json")
        design_file = json.loads(plan).get("design_file") if plan else None
        plan_name = evidence["pm_snapshot"]["session"].get("plan_name")
        message = (f"Continue original worker assignment: {objective}\n"
                   f"PM stage: {pm['stage']}. Inspect the existing design, plan, task artifacts and current worktree. "
                   f"Preserve existing constraints and complete normal review gates.\n"
                   f"Design: {design_file or 'none'}; plan: {plan_name or 'none'}")
        if source.get("task") and source["task"].get("description"):
            message += f"\nCommander task: {source['task']['description']}"
        self.registry.record_worker_handoff_evidence(worker_id, generation, "runtime_registered", {
            "dispatch_intent": True})
        if not self.tools.tmux.send_keys(evidence["target_session"], message):
            raise HandoffHold("continuation_dispatch_unproved")
        self.registry.advance_worker_handoff(worker_id, generation, "runtime_registered", "continuation_dispatched", evidence={"dispatch_intent": False, "dispatch_accepted": True})
        return self.reconcile(worker_id)

    def _finish(self, worker_id, generation, evidence):
        scope = evidence["scope"]
        payload = {**scope, "capability_token": evidence["capability_token"]}
        self.registry.record_worker_handoff_evidence(worker_id, generation, "continuation_dispatched", {"finish_intent": True})
        finished = self.workspace.finish_handoff(payload, **evidence["transport"])
        if finished.get("protocol_version") != 1 or finished.get("phase") != "finished":
            raise HandoffHold("assignment_finish_unproved")
        self.registry.record_worker_handoff_evidence(worker_id, generation, "continuation_dispatched", {"finish_intent": False, "assignment_finished": True})
        self.registry.complete_worker_handoff(worker_id, generation, "continuation_dispatched")
        return self._status(worker_id)


class InstalledNativeRuntime:
    """Production native-driver seam; only verified installed flags may be used."""

    def __init__(self, orchestrator):
        self.tools = orchestrator

    def _executable(self, client: str) -> str:
        if client not in {"claude", "codex"}:
            raise HandoffHold("target_client_unsupported")
        candidate = self.tools._config.get("providers", {}).get("clients", {}).get(client, {}).get("path", client)
        if not isinstance(candidate, str) or not candidate.strip():
            raise HandoffHold("native_executable_unconfigured")
        return candidate

    def preflight(self, client: str) -> None:
        executable = self._executable(client)
        checks = (
            ((executable, "--help"), ("--no-daemon", "--sandbox", "--ask-for-approval", "--model")),
            ((executable, "resume", "--help"), ("--no-daemon", "--dangerously-bypass-approvals-and-sandbox", "--model")),
        ) if client == "codex" else (
            ((executable, "--help"), ("--permission-mode", "--tools", "--strict-mcp-config", "--resume",
                                       "--system-prompt-snapshot", "--dangerously-skip-permissions", "--model")),
        )
        for argv, required in checks:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
            if result.returncode or any(flag not in result.stdout for flag in required):
                raise HandoffHold("native_readonly_or_resume_flags_unsupported")

    def bootstrap(self, name: str, client: str, model: str, path: str, worker_id: str) -> dict:
        tmux = self.tools.tmux
        if tmux.has_session(name):
            raise HandoffHold("target_bootstrap_session_already_exists")
        if client == "codex":
            executable = self._executable(client)
            cmd = (f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; export ENABLE_STOP_REVIEW=0; "
                   f"exec {shlex.quote(executable)} --model {shlex.quote(model)} "
                   "--no-daemon --sandbox read-only --ask-for-approval never")
        elif client == "claude":
            executable = self._executable(client)
            cmd = (f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; export ENABLE_STOP_REVIEW=0; "
                   f"exec {shlex.quote(executable)} --model {shlex.quote(model)} --permission-mode plan "
                   "--tools '' --strict-mcp-config")
        else:
            raise HandoffHold("target_client_unsupported")
        launched_at = time.time()
        if not tmux.spawn_session(name, cmd, cwd=path):
            raise HandoffHold("target_bootstrap_launch_failed")
        if not self.tools._wait_for_ready(name, timeout=30, client=client):
            raise HandoffHold("target_bootstrap_readiness_unproved")
        identity_args = {"not_before": launched_at} if client == "codex" else {}
        if self.tools._activate_pm_via_sqlite(name, timeout=30, max_retries=1,
                                               client=client, **identity_args) is not None:
            raise HandoffHold("target_pm_activation_unproved")
        state = self.tools._read_pm_state_via_sqlite(name, client=client, **identity_args)
        native_id = state.get("session_uuid")
        try:
            if str(uuid.UUID(native_id)) != native_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise HandoffHold("target_native_identity_unproved") from exc
        if state.get("professional_mode") != "on" or state.get("workflow_stage") != "idle":
            raise HandoffHold("target_pm_idle_unproved")
        if not tmux.send_keys(name, "Reply HANDOFF_BOOTSTRAP_READY; do not use tools or change workspace"):
            raise HandoffHold("target_ack_send_unproved")
        for _ in range(15):
            observed_model = self._saved_ack(client, native_id, path, model, launched_at)
            if observed_model:
                return {"client": client, "model": model, "native_session_id": native_id,
                        "persisted": True, "ack_complete": True,
                        "observed_model": observed_model}
            time.sleep(1)
        raise HandoffHold("target_native_conversation_unproved")

    @staticmethod
    def _saved_ack(client: str, native_id: str, cwd: str, model: str, launched_at: float) -> str | None:
        if client == "claude":
            roots = (Path.home() / ".claude/projects").glob(f"*/{native_id}.jsonl")
        elif client == "codex":
            roots = (Path.home() / ".codex/sessions").glob(f"**/*{native_id}*.jsonl")
        else:
            return None
        for path in roots:
            try:
                if path.stat().st_mtime < launched_at:
                    continue
                with path.open(encoding="utf-8") as stream:
                    records = []
                    for _ in range(2048):
                        line = stream.readline(262145)
                        if not line:
                            break
                        if len(line) > 262144:
                            return None
                        records.append(json.loads(line))
                if client == "claude":
                    observed_model = None
                    for record in records:
                        if (record.get("sessionId") != native_id or record.get("cwd") != cwd
                                or record.get("type") != "assistant"):
                            continue
                        # A newer bound assistant event supersedes an earlier ACK.
                        observed_model = None
                        message = record.get("message")
                        if (record.get("isApiErrorMessage") is True
                                or record.get("error") not in (None, False, "")
                                or record.get("apiErrorStatus") is not None
                                or not isinstance(message, dict)):
                            continue
                        usage = message.get("usage")
                        output = usage.get("output_tokens") if isinstance(usage, dict) else None
                        content = message.get("content")
                        native_model = message.get("model")
                        if (message.get("stop_reason") not in {"end_turn", "stop_sequence"}
                                or type(output) is not int or output <= 0
                                or not isinstance(native_model, str) or not native_model
                                or not (native_model == model or
                                        (model in {"sonnet", "opus", "haiku"}
                                         and model in native_model.split("-")))
                                or not isinstance(content, list)):
                            continue
                        if any(isinstance(block, dict) and block.get("type") == "text"
                               and isinstance(block.get("text"), str)
                               and block["text"].strip() == "HANDOFF_BOOTSTRAP_READY"
                               for block in content):
                            observed_model = native_model
                    if observed_model:
                        return observed_model
                else:
                    meta = [r.get("payload") for r in records if r.get("type") == "session_meta"]
                    if len(meta) != 1 or not isinstance(meta[0], dict) or meta[0].get("id") != native_id or meta[0].get("cwd") != cwd:
                        continue
                    active_turn = None
                    observed_model = None
                    ack = False
                    turn_failed = False
                    ready_model = None
                    completed_turn = None
                    for record in records:
                        payload = record.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        if record.get("type") == "event_msg" and payload.get("type") == "task_started":
                            active_turn = payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None
                            observed_model, ack, turn_failed = None, False, False
                            ready_model, completed_turn = None, None
                        elif active_turn and record.get("type") == "turn_context" and payload.get("turn_id") == active_turn:
                            observed_model = payload.get("model")
                        elif (active_turn and record.get("type") == "response_item"
                              and payload.get("turn_id") in (None, active_turn)
                              and payload.get("type") == "message" and payload.get("role") == "assistant"):
                            content = payload.get("content")
                            ack = ack or (isinstance(content, list) and any(
                                isinstance(block, dict) and block.get("type") == "output_text"
                                and isinstance(block.get("text"), str)
                                and block["text"].strip() == "HANDOFF_BOOTSTRAP_READY"
                                for block in content))
                        elif record.get("type") == "event_msg":
                            event_type = payload.get("type")
                            event_turn = payload.get("turn_id")
                            relevant = event_turn is None or event_turn == (active_turn or completed_turn)
                            failed = (event_type in {"error", "turn_error", "task_failed", "turn_aborted"}
                                      or payload.get("error") not in (None, False, ""))
                            if relevant and failed:
                                turn_failed = True
                                ready_model = None
                            if active_turn and event_turn == active_turn and event_type == "task_complete":
                                ready_model = (observed_model if not turn_failed and ack
                                               and observed_model == model else None)
                                completed_turn = active_turn
                                active_turn = None
                    if ready_model:
                        return ready_model
            except (OSError, UnicodeError, ValueError, TypeError):
                continue
        return None

    def resume(self, name: str, client: str, model: str, native_session_id: str, path: str, worker_id: str) -> dict:
        tmux = self.tools.tmux
        if tmux.has_session(name):
            raise HandoffHold("target_resume_session_already_exists")
        suffix = name.rsplit("-h", 1)[-1]
        env = (f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; "
               f"export TRON_WORKER_ID={shlex.quote(worker_id + '-h' + suffix)}; "
               "export ENABLE_STOP_REVIEW=0; ")
        if client == "codex":
            executable = self._executable(client)
            cmd = env + (f"exec {shlex.quote(executable)} resume {shlex.quote(native_session_id)} "
                         f"--model {shlex.quote(model)} --no-daemon --dangerously-bypass-approvals-and-sandbox")
        elif client == "claude":
            executable = self._executable(client)
            cmd = env + (f"exec {shlex.quote(executable)} --resume {shlex.quote(native_session_id)} "
                         f"--model {shlex.quote(model)} --system-prompt-snapshot off "
                         "--dangerously-skip-permissions")
        else:
            raise HandoffHold("target_client_unsupported")
        launched_at = time.time()
        if not tmux.spawn_session(name, cmd, cwd=path):
            raise HandoffHold("target_resume_launch_failed")
        if not self.tools._wait_for_ready(name, timeout=30, client=client):
            raise HandoffHold("target_resume_readiness_unproved")
        identity_args = {"not_before": launched_at} if client == "codex" else {}
        state = self.tools._read_pm_state_via_sqlite(name, client=client, **identity_args)
        if state.get("session_uuid") != native_session_id or state.get("professional_mode") != "on":
            raise HandoffHold("target_resume_identity_unproved")
        if client == "claude" and not self._fresh_claude_binding(name, native_session_id, launched_at):
            raise HandoffHold("target_resume_identity_unproved")
        return {"native_session_id": native_session_id, "ready": True}

    def _fresh_claude_binding(self, name: str, native_session_id: str, not_before: float) -> bool:
        pane_pid = self.tools.tmux.list_pane_pid(name)
        if not pane_pid:
            return False
        binding = Path.home() / ".claude" / f"ironclaude-session-{pane_pid}.id"
        try:
            return binding.stat().st_mtime >= not_before and binding.read_text(encoding="utf-8").strip() == native_session_id
        except OSError:
            return False

    def inspect_resumed(self, name: str, client: str, native_session_id: str, *, not_before: float) -> bool:
        if not self.tools.tmux.has_session(name):
            return False
        args = {"not_before": not_before} if client == "codex" else {}
        state = self.tools._read_pm_state_via_sqlite(name, client=client, **args)
        return (state.get("session_uuid") == native_session_id
                and state.get("professional_mode") == "on"
                and (client != "claude" or self._fresh_claude_binding(name, native_session_id, not_before))
                and self.tools._wait_for_ready(name, timeout=30, client=client))
