# src/ic/notifications.py
"""Slack notification formatters for IronClaude events."""

from __future__ import annotations

WORKER_TYPE_LABELS = {
    "claude-max": "Claude Max",
    "ollama-api": "Ollama API",
}


def _escape_mrkdwn(text: str) -> str:
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _escape_backticks(s: str) -> str:
    """Escape backticks so they don't terminate a Slack code fence
    (```…```) or single-backtick span (`…`). Slack treats a
    backslash-backtick as a literal backtick and does NOT close the span.
    Load-bearing for format_directive_review — planned_prompt is LLM-
    authored and may contain code fences."""
    return s.replace("`", "\\`")


def format_worker_spawned(worker_id: str, worker_type: str, repo: str, objective: str) -> str:
    label = WORKER_TYPE_LABELS.get(worker_type, worker_type)
    return (
        f"*Worker Started:* `{worker_id}` ({label})\n"
        f"Repo: `{repo}`\n"
        f"Objective: {_escape_mrkdwn(objective)}"
    )


def format_worker_completed(worker_id: str, summary: str) -> str:
    return f"*Worker Completed:* `{worker_id}`\nResult: {_escape_mrkdwn(summary)}"


def format_worker_session_ended_preserved(worker_id: str, disposition: str) -> str:
    """The dead-session branch's non-completion surface: the worker's tmux
    session ended but the seam left it NOT completed (a transient, drift, or
    held recovery disposition) — the reviewed work is preserved on its
    branch, never lost, but must not be reported as 'Worker Completed'."""
    return (
        f"*Worker session ended — work preserved (NOT completed):* `{worker_id}`\n"
        f"Reviewed work is preserved on its branch (recovery: {_escape_mrkdwn(disposition)}); "
        f"NOT completed. May need operator attention."
    )


def format_worker_idle(worker_id: str) -> str:
    return f"*Worker Idle:* `{worker_id}` went idle (stop hook fired). Brain notified."


def format_worker_idle_ttl_reaped(worker_id: str, minutes: int) -> str:
    return (
        f"*Worker Idle-TTL Reaped:* `{worker_id}` was idle for {minutes}min\n"
        f"Finalization handled by the integration seam; Brain notified to respawn if unfinished."
    )


def format_worker_failed(worker_id: str, error: str, attempts: int) -> str:
    return (
        f"*Worker Failed:* `{worker_id}` after {attempts} attempt(s)\n"
        f"Error: {_escape_mrkdwn(error)}\n"
        f"Use `/detail {worker_id}` for logs."
    )


PREAMBLE_START = "Professional mode is active."


def _extract_task_snippet(raw: str | None) -> str:
    if raw is None:
        return "no task"
    text = raw.lstrip()
    if text.startswith(PREAMBLE_START):
        sep = text.find("\n\n")
        if sep != -1:
            text = text[sep:].lstrip()
    for marker in ("Task:", "Your task:"):
        idx = text.find(marker)
        if idx != -1:
            after = text[idx + len(marker):].lstrip()
            end = after.find("\n")
            if end != -1:
                after = after[:end]
            return after.strip() or "[malformed objective]"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "[malformed objective]"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def format_heartbeat(
    workers: list[dict],
    brain_usage: dict | None = None,
    waits: dict | None = None,
    brain_waits: dict | None = None,
    operator_name: str = "Operator",
    ollama_degraded: bool = False,
    ollama_busy: bool = False,
    blocked_directives: list[dict] | None = None,
    degraded_backend_label: str = "Ollama",
    orphaned_unmerged: int = 0,
    mem_line: str | None = None,
) -> str:
    waits = waits or {}
    brain_waits = brain_waits or {}
    blocked_directives = blocked_directives or []
    if not workers and not waits and not brain_waits and not blocked_directives:
        base = "*Heartbeat* | No active workers"
        if ollama_degraded:
            base += f"\n⚠️ validator degraded ({degraded_backend_label} endpoint(s) unreachable/degraded — see logs)"
        if ollama_busy and not ollama_degraded:
            base += f"\n⏳ validator busy ({degraded_backend_label} endpoint(s) reachable but slow — normal under load)"
        if orphaned_unmerged:
            base += f"\n⚠️ {orphaned_unmerged} preserved orphan(s) need review"
        if mem_line:
            base += f"\n{mem_line}"
        return base
    lines = ["*Heartbeat*"]
    if blocked_directives:
        lines.append("⛔ *Blocked directives:*")
        for block in blocked_directives:
            capabilities = ", ".join(block.get("capabilities") or [])
            lines.append(
                f"  • #{block['directive_id']} — {capabilities} "
                f"({block['denial_scope']}): {_escape_mrkdwn(block.get('reason') or '')}"
            )
    if waits or brain_waits:
        # brain_waits is populated by the brain-wait classifier — only render its
        # section when it actually has entries, instead of an always-empty
        # "there is nothing" line.
        if brain_waits:
            lines.append("⏳ *WAITING ON Brain:*")
            for wid, info in brain_waits.items():
                question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting reply)"
                lines.append(f"  • `{wid}` — {question}")
        lines.append(f"⏳ *WAITING ON {operator_name}:*")
        if waits:
            for wid, info in waits.items():
                question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting your reply)"
                lines.append(f"  • `{wid}` — {question}")
        else:
            lines.append("  there is nothing")
    if waits and workers:
        lines.append("*Active Workers:*")
    for w in workers:
        snippet = _extract_task_snippet(w.get("description"))
        desc = _escape_mrkdwn(snippet)
        if len(desc) > 60:
            desc = desc[:60] + "..."
        stage = w.get("workflow_stage") or "unknown"
        if w["id"] in brain_waits:
            tag = " — ⏳ waiting on brain"
        elif w["id"] in waits:
            tag = f" — ⏳ waiting on {operator_name}"
        else:
            tag = ""
        prompt = w.get("prompt_incident")
        if isinstance(prompt, dict):
            age = _fmt_duration(float(prompt.get("age_seconds") or 0))
            dispatch = str(prompt.get("dispatch_state") or "unknown")
            failure = prompt.get("failure_category")
            prompt_tag = f" — ⚠ prompt active {age}, dispatch {dispatch}"
            if failure:
                prompt_tag += f" ({_escape_mrkdwn(str(failure))})"
            tag += prompt_tag
        lines.append(f'• {w["id"]} — "{desc}" ({stage}{tag})')
    if brain_usage is not None:
        inp = brain_usage.get("input_tokens", 0)
        out = brain_usage.get("output_tokens", 0)
        total = brain_usage.get("total_tokens", 0)
        line = f"🧠 Brain: {_fmt_tokens(total)} tokens ({_fmt_tokens(inp)} in + {_fmt_tokens(out)} out)"
        if total == 0:
            age = brain_usage.get("seconds_since_last_activity")
            if age is not None:
                line += f" — turn in progress (last activity {_fmt_duration(age)} ago)"
        lines.append(line)
    if ollama_degraded:
        lines.append(f"⚠️ validator degraded ({degraded_backend_label} endpoint(s) unreachable/degraded — see logs)")
    if ollama_busy and not ollama_degraded:
        lines.append(f"⏳ validator busy ({degraded_backend_label} endpoint(s) reachable but slow — normal under load)")
    if orphaned_unmerged:
        lines.append(f"⚠️ {orphaned_unmerged} preserved orphan(s) need review")
    if mem_line:
        lines.append(mem_line)
    return "\n".join(lines)


def format_orphaned_orphans(details: list[dict]) -> str:
    """Slack surface for row-less orphan worktrees/branches the auto-reaper
    preserved (muted entries already excluded by the caller). Each `details`
    entry is a dict with keys id/guid/branch/category/tip/worktreePresent/
    evidence/repository_path, as returned by workspace-manager's
    reap_orphans (preservedDetail)."""
    need_review = len([d for d in details if d.get("category") != "squash-merged"])
    already_merged = len(details) - need_review
    header = f"⚠️ *{need_review} orphaned worktree branch(es) preserved — need review*"
    if already_merged:
        header += f" (+{already_merged} already squash-merged, listed below)"
    lines = [header]
    for d in details:
        tip = str(d.get("tip") or "")[:7]
        evidence = _escape_mrkdwn(str(d.get("evidence") or ""))
        lines.append(
            f"• `{d.get('id')}` [{d.get('category')}] `{d.get('branch')}` "
            f"tip `{tip}` — {evidence} (repo `{d.get('repository_path')}`)"
        )
    lines.append(
        "Not deleted. Reply `reap <ids>`, `keep <ids>`, or `merge <ids>` to act on these."
    )
    return "\n".join(lines)


def format_orphaned_unmerged(entries: list[str]) -> str:
    """Slack surface for row-less orphan branches the auto-reaper preserved
    because they hold commits not on the integration target. `entries` are
    'repo_path:ironclaude/<guid>' strings."""
    lines = [f"⚠️ *{len(entries)} orphaned worktree branch(es) preserved (unmerged — need review)*"]
    lines.extend(f"• `{entry}`" for entry in sorted(entries))
    lines.append("Not deleted: each holds commits not on the integration target. Review, merge, or delete manually.")
    return "\n".join(lines)


def resolve_degraded_backend_label(config_path: str) -> str:
    """Display name of the resolved validator backend (for the degraded heartbeat).

    Fail-safe: a missing/unparseable config resolves to the ollama default -> 'Ollama'.
    """
    import json

    from ironclaude.backend_resolver import resolve_backend

    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        cfg = {}
    backend = resolve_backend(cfg, "grader").backend
    return {"openai": "OpenAI", "ollama": "Ollama"}.get(
        backend, backend.capitalize() if backend else "Ollama"
    )


def format_brain_restarted(restart_count: int, reason: str = "unknown") -> str:
    return (
        f"*Brain Restarted* ({reason})\n"
        f"Fresh session started — previous context lost. Restart count: {restart_count}"
    )


def format_brain_compacted() -> str:
    return (
        "*Brain Compacted*\n"
        "Context limit approached — session compacted and resumed. No context lost."
    )


def format_brain_circuit_breaker(restart_count: int, max_restarts: int, window_seconds: int) -> str:
    return (
        f"*Brain Circuit Breaker Tripped*\n"
        f"{restart_count} restarts detected (limit: {max_restarts} per {window_seconds // 60} min).\n"
        f"Brain paused. Manual restart required."
    )


def format_brain_capability_blocked(observation: dict) -> str:
    reason = _escape_mrkdwn(str(observation.get("reason") or "unknown")[:256])
    source = _escape_backticks(
        _escape_mrkdwn(str(observation.get("source_companion") or "(missing)")[:2048])
    )
    destination = _escape_backticks(_escape_mrkdwn(
        str(observation.get("destination_companion") or "(missing)")[:2048]
    ))
    return (
        "*Codex Brain capability blocked*\n"
        f"Reason: `{reason}`\n"
        f"Source: `{source}`\n"
        f"Destination: `{destination}`\n"
        "Existing destination preserved. Commander is holding without provider "
        "fallback, model calls, or repeated alerts; local preflight will recheck "
        "on bounded backoff."
    )


def format_objective_received(text: str) -> str:
    return f"*New Objective:* {_escape_mrkdwn(text)}\nDecomposing into tasks..."


def format_task_progress(current: int, total: int, description: str) -> str:
    return f"*Task {current}/{total}:* {_escape_mrkdwn(description)}"


def format_plan_ready(worker_id: str, plan_summary: str) -> str:
    return (
        f"*Plan Ready:* `{worker_id}` produced a plan\n"
        f"Summary: {_escape_mrkdwn(plan_summary)}\n"
        f"`/approve {worker_id}` or `/reject {worker_id}`"
    )


def format_blocked(task_num: int, total: int, reason: str) -> str:
    return f"*Blocked on task {task_num}/{total}:* {reason}\nNeed your input."


def format_worker_checkin(
    worker_id: str, elapsed_minutes: int, stage: str,
    log_tail: str, prompt_waiting: bool,
) -> str:
    prefix = "[ACTION REQUIRED]" if prompt_waiting else "[CHECK-IN]"
    msg = f"{prefix} {worker_id} {elapsed_minutes}min {stage}\n{_escape_mrkdwn(log_tail)}"
    if prompt_waiting:
        msg += "\n⚠️ Waiting for input."
    return msg


def format_worker_checkin_slack(
    worker_id: str, elapsed_minutes: int, stage: str, prompt_waiting: bool,
) -> str:
    prefix = "[ACTION REQUIRED]" if prompt_waiting else "[CHECK-IN]"
    msg = f"{prefix} {worker_id} ({elapsed_minutes}min) — {stage}"
    if prompt_waiting:
        msg += ": waiting for input"
    return msg


def format_worker_gate_stuck_slack(
    worker_id: str, minutes: int, stage: str,
) -> str:
    return (
        f"[ALERT] Worker {worker_id} stuck at {stage} for {minutes}min — "
        f"waiting for input. Brain may be unresponsive."
    )


def format_worker_heartbeat_stuck_slack(worker_id: str, stage: str) -> str:
    return (
        f"[STUCK] Worker {worker_id} unchanged for 2 consecutive heartbeats at stage {stage}. "
        f"Brain intervention required."
    )


def format_directive_review(
    directive_id: int,
    interpretation: str,
    source_text: str,
    planned_worker_type: str,
    planned_use_goal: bool,
    planned_prompt: str,
    planned_worker_type_reason: str,
    planned_use_goal_reason: str,
    planned_prompt_reason: str,
    supersedes: int | None = None,
) -> str:
    """Slack-mrkdwn presentation of a directive for operator review.

    Every user/LLM-supplied string is escaped via _escape_mrkdwn (so `&`,
    `<`, `>` — including Slack's <!channel>/<@U…>/<#C…> mention syntax,
    which is parsed at the payload level even inside a code fence — render
    as literal text). Strings that are additionally embedded inside a
    backtick span (`…`) or triple-backtick fence (```…```) also get
    _escape_backticks, applied AFTER _escape_mrkdwn, so any ``` or `
    sequences inside them cannot break out of their span/fence. This
    applies to source_text, planned_worker_type, and planned_prompt. When
    supersedes is given, the header notes the chain link so the operator
    can trust that this is a revised presentation.
    """
    if supersedes is None:
        header = f"*Directive #{directive_id}* detected:"
    else:
        header = f"*Directive #{directive_id}* (revised from #{supersedes}) detected:"
    goal_answer = "yes" if planned_use_goal else "no"
    lines = [
        header,
        f"> {_escape_mrkdwn(interpretation)}",
        f"_From your message:_ `{_escape_backticks(_escape_mrkdwn(source_text))}`",
        "",
        f"*Model:* `{_escape_backticks(_escape_mrkdwn(planned_worker_type))}` — {_escape_mrkdwn(planned_worker_type_reason)}",
        f"*`/goal`:* {goal_answer} — {_escape_mrkdwn(planned_use_goal_reason)}",
        "*Worker prompt:*",
        "```",
        _escape_backticks(_escape_mrkdwn(planned_prompt)),
        "```",
        f"_Why:_ {_escape_mrkdwn(planned_prompt_reason)}",
        "",
        "React 👍 to confirm, 👎 to reject, 🤔 to request changes.",
    ]
    return "\n".join(lines)


def format_worker_stuck_killed(
    worker_id: str, minutes: int, stage: str, prompt_waiting: bool,
) -> str:
    return (
        f"*Worker Stuck-Killed:* `{worker_id}` was idle for {minutes}min\n"
        f"Stage: {stage} | Prompt waiting: {'yes' if prompt_waiting else 'no'}\n"
        f"Liveness: confirmed stuck (0% CPU across process tree)\n"
        f"Brain notified to respawn."
    )


def format_fable_unavailable(reason: str, redirected_to: str = "claude-opus-4-8", worker_id: str | None = None) -> str:
    """Slack alert when Fable becomes unavailable and the daemon starts redirecting.

    Posted exactly once per detection episode (the caller decides based on
    mark_fable_unavailable's transition return). See fable_availability.py.
    """
    lines = [
        "⚠️ *Fable unavailable*",
        f"Reason: {_escape_mrkdwn(reason)}",
    ]
    if worker_id is not None:
        lines.append(f"Worker: `{_escape_mrkdwn(worker_id)}`")
    lines.append(f"Redirecting claude-fable requests to `{_escape_mrkdwn(redirected_to)}` for the next 24h.")
    lines.append("To re-probe manually: `rm ~/.ironclaude/state/fable_unavailable.json`")
    return "\n".join(lines)


def format_fable_recovered() -> str:
    """Slack alert when Fable comes back — flag cleared, subsequent claude-fable
    and /advisor fable requests will be honored again."""
    return (
        "✅ *Fable is back*\n"
        "Flag cleared — subsequent claude-fable and /advisor fable requests will be honored again."
    )
