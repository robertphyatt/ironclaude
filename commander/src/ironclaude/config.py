# src/ic/config.py
"""Configuration loader for IronClaude daemon."""

from __future__ import annotations

import json
import logging
import os
import shlex

logger = logging.getLogger("ironclaude.config")

DEFAULTS = {
    "poll_interval_seconds": 15,
    "heartbeat_interval_seconds": 900,
    "worker_stale_threshold_seconds": 300,
    "brain_heartbeat_timeout_seconds": 60,
    "brain_timeout_seconds": 600,
    "max_worker_retries": 3,
    "machines": [],
    "tmp_dir": "/tmp/ic",
    "log_dir": "/tmp/ic-logs",
    "db_path": "data/db/ironclaude.db",
    "brain_cwd": "~/.ironclaude/brain",
    "brain_prompt_path": "",  # test11
    "operator_name": "Operator",
    "autonomy_level": "3",
    "brain_model": "claude-opus-4-6",
    "grader_model": "claude-opus-4-6",
    "effort_level": "high",
    "advisor": {
        "enabled": True,
        "executor_model": "sonnet",
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
