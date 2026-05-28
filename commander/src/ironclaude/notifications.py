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


def _extract_task_snippet(raw: str | None) -> str:
    if raw is None:
        return "no task"
    idx = raw.find("Your task:")
    if idx != -1:
        after = raw[idx + len("Your task:"):].lstrip()
        end = after.find("\n")
        if end != -1:
            after = after[:end]
        return after.strip() or "[malformed objective]"
    for line in raw.splitlines():
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


def format_heartbeat(workers: list[dict], brain_usage: dict | None = None) -> str:
    if not workers:
        return "*Heartbeat* | No active workers"
    lines = ["*Heartbeat*"]
    for w in workers:
        snippet = _extract_task_snippet(w.get("description"))
        desc = _escape_mrkdwn(snippet)
        if len(desc) > 60:
            desc = desc[:60] + "..."
        stage = w.get("workflow_stage") or "unknown"
        lines.append(f'• {w["id"]} — "{desc}" ({stage})')
    if brain_usage is not None:
        inp = brain_usage.get("input_tokens", 0)
        out = brain_usage.get("output_tokens", 0)
        total = brain_usage.get("total_tokens", 0)
        lines.append(f"🧠 Brain: {_fmt_tokens(total)} tokens ({_fmt_tokens(inp)} in + {_fmt_tokens(out)} out)")
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
    return f"*Task {current}/{total}:* {description}"


def format_plan_ready(worker_id: str, plan_summary: str) -> str:
    return (
        f"*Plan Ready:* `{worker_id}` produced a plan\n"
        f"Summary: {plan_summary}\n"
        f"`/approve {worker_id}` or `/reject {worker_id}`"
    )


def format_blocked(task_num: int, total: int, reason: str) -> str:
    return f"*Blocked on task {task_num}/{total}:* {reason}\nNeed your input."


def format_worker_checkin(
    worker_id: str, elapsed_minutes: int, stage: str,
    log_tail: str, prompt_waiting: bool,
) -> str:
    prefix = "[ACTION REQUIRED]" if prompt_waiting else "[CHECK-IN]"
    msg = f"{prefix} {worker_id} {elapsed_minutes}min {stage}\n{log_tail}"
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
