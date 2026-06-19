# src/ic/config.py
"""Configuration loader for IronClaude daemon."""

from __future__ import annotations

import json
import logging
import os
import re as _re
import shlex

logger = logging.getLogger("ironclaude.config")

DEFAULTS = {
    "poll_interval_seconds": 15,
    "heartbeat_interval_seconds": 900,
    "worker_stale_threshold_seconds": 300,
    "brain_heartbeat_timeout_seconds": 60,
    "brain_timeout_seconds": 600,
    "max_worker_retries": 3,
    "min_available_memory_pct": 0.10,
    "ollama_vram_block_threshold_gb": 8.0,
    "ollama_worker_num_ctx": 32768,
    "machines": [],
    "push_enabled": False,
    "push_max_per_hour": 5,
    "tmp_dir": "/tmp/ic",
    "log_dir": "/tmp/ic-logs",
    "db_path": "data/db/ironclaude.db",
    "brain_cwd": "~/.ironclaude/brain",
    "brain_prompt_path": "",  # test11
    "operator_name": "Operator",
    "autonomy_level": "3",
    "brain_model": "opus",
    "grader_model": "opus",
    "effort_level": "high",
    "advisor": {
        "enabled": True,
        "executor_model": "sonnet",  # CLI routing only — sets --model flag for sonnet workers; not a Brain model selection input
        "advisor_model": "opus",
    },
}

# Env vars that override JSON config (env name -> config key, type)
ENV_OVERRIDES = {
    "POLL_INTERVAL_SECONDS": ("poll_interval_seconds", int),
    "HEARTBEAT_INTERVAL_SECONDS": ("heartbeat_interval_seconds", int),
    "DB_PATH": ("db_path", str),
    "BRAIN_TIMEOUT_SECONDS": ("brain_timeout_seconds", int),
    "BRAIN_MODEL": ("brain_model", str),
    "GRADER_MODEL": ("grader_model", str),
    "EFFORT_LEVEL": ("effort_level", str),
}

# Env vars loaded directly into config (not in JSON)
ENV_DIRECT = {
    "SLACK_BOT_TOKEN": "slack_bot_token",
    "SLACK_APP_TOKEN": "slack_app_token",
    "SLACK_CHANNEL_ID": "slack_channel_id",
    "SLACK_USER_TOKEN": "slack_user_token",
    "SLACK_OPERATOR_USER_ID": "slack_operator_user_id",
    "OPERATOR_NAME": "operator_name",
    "AUTONOMY_LEVEL": "autonomy_level",
}

_ENV_VAR_RE = _re.compile(r'\$\{(\w+)\}')

REQUIRED_MACHINE_FIELDS = ("name", "host", "claude_path", "repos")


def load_config(config_path: str = "config/ironclaude.json") -> dict:
    """Load config from JSON file, apply env overrides, add env-only vars."""
    cfg = dict(DEFAULTS)

    # Load JSON if it exists
    try:
        with open(config_path) as f:
            file_cfg = json.load(f)
        cfg.update(file_cfg)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Config file not loaded: {e}. Using defaults.")

    # Apply env overrides
    for env_name, (cfg_key, cast) in ENV_OVERRIDES.items():
        val = os.environ.get(env_name)
        if val is not None:
            cfg[cfg_key] = cast(val)

    # Load env-only vars
    for env_name, cfg_key in ENV_DIRECT.items():
        val = os.environ.get(env_name)
        if val is not None:
            cfg[cfg_key] = val

    # Apply ANTHROPIC_DEFAULT_OPUS_MODEL as a lower-priority override.
    # Specific BRAIN_MODEL / GRADER_MODEL env vars take precedence.
    opus_env = os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
    if opus_env:
        cfg["default_opus_model"] = opus_env
        if "BRAIN_MODEL" not in os.environ:
            cfg["brain_model"] = opus_env
        if "GRADER_MODEL" not in os.environ:
            cfg["grader_model"] = opus_env
    else:
        cfg["default_opus_model"] = cfg["brain_model"]

    return cfg


def make_opus_command(model: str, effort: str) -> str:
    """Build the claude-opus worker command with the given model and effort level."""
    return f"export CLAUDE_CODE_EFFORT_LEVEL={effort}; exec claude --model {shlex.quote(model)} --dangerously-skip-permissions"


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} with os.environ[VAR]. Missing vars left as-is."""
    def _replace(m):
        return os.environ.get(m.group(1), m.group(0))
    return _ENV_VAR_RE.sub(_replace, value)


def load_machines_config(config_path: str = "config/machines.yaml") -> list[dict]:
    """Load remote machine definitions from YAML. Returns empty list if file missing."""
    try:
        import yaml
    except ImportError:
        logger.warning("pyyaml not installed — remote machines unavailable")
        return []

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.info(f"No machines config at {config_path}")
        return []

    if not data or "machines" not in data:
        return []

    machines = data["machines"]
    names_seen: set[str] = set()

    for m in machines:
        for field in REQUIRED_MACHINE_FIELDS:
            if field not in m:
                raise ValueError(
                    f"Machine '{m.get('name', '?')}' missing required field '{field}'"
                )
        if m["name"] in names_seen:
            raise ValueError(f"Duplicate machine name '{m['name']}'")
        names_seen.add(m["name"])

        # Interpolate env vars
        if "env" in m:
            m["env"] = {k: _interpolate_env(v) for k, v in m["env"].items()}

    return machines
