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
