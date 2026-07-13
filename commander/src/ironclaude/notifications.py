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


def format_worker_idle(worker_id: str) -> str:
    return f"*Worker Idle:* `{worker_id}` went idle (stop hook fired). Brain notified."


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
) -> str:
    waits = waits or {}
    if not workers and not waits:
        return "*Heartbeat* | No active workers"
    lines = ["*Heartbeat*"]
    if waits:
        # Contract: if anything is holding on the operator, EVERY heartbeat says so.
        lines.append("⏳ *WAITING ON YOU*")
        for wid, info in waits.items():
            question = _escape_mrkdwn(str((info or {}).get("question") or "").strip()) or "(awaiting your reply)"
            lines.append(f"  • `{wid}` — {question}")
    for w in workers:
        snippet = _extract_task_snippet(w.get("description"))
        desc = _escape_mrkdwn(snippet)
        if len(desc) > 60:
            desc = desc[:60] + "..."
        stage = w.get("workflow_stage") or "unknown"
        tag = " — ⏳ waiting on you" if w["id"] in waits else ""
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
    return "\n".join(lines)


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


def format_fable_unavailable(reason: str, redirected_to: str = "opus", worker_id: str | None = None) -> str:
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
