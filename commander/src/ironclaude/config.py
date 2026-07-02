# src/ic/config.py
"""Configuration loader for IronClaude daemon."""

from __future__ import annotations

import copy
import json
import logging
import os
import re as _re
import shlex

logger = logging.getLogger("ironclaude.config")

# Allowed effort levels. Interpolated unquoted into make_opus_command's shell
# string, so an out-of-allowlist value is rejected (defense-in-depth).
EFFORT_LEVELS = {"low", "medium", "high"}
DEFAULT_EFFORT_LEVEL = "high"

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
    "brain_model": "sonnet",
    "default_opus_model": "opus",
    "grader_model": "opus",
    "effort_level": "high",
    "advisor": {
        "enabled": True,
        "executor_model": "sonnet",  # CLI routing only — sets --model flag for sonnet workers; not a Brain model selection input
        "advisor_model": "opus",  # scalar fallback for unknown worker types
        "advisor_models": {"claude-sonnet": "opus", "claude-opus": "fable"},  # one-tier-up map per worker type
    },
    "dispatch": {"use_goal": False},
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


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` in place.

    For keys present in both where both values are dicts, merge recursively so
    a partial nested override retains sibling defaults instead of replacing the
    whole nested dict. Otherwise the override value wins.
    """
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(config_path: str = "config/ironclaude.json") -> dict:
    """Load config from JSON file, apply env overrides, add env-only vars."""
    cfg = copy.deepcopy(DEFAULTS)

    # Load JSON if it exists
    try:
        with open(config_path) as f:
            file_cfg = json.load(f)
        _deep_merge(cfg, file_cfg)
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
    # default_opus_model is DECOUPLED from brain_model: it comes from the
    # ANTHROPIC_DEFAULT_OPUS_MODEL env var if set, else the "opus" default from
    # DEFAULTS. It must NEVER inherit brain_model — otherwise claude-opus
    # workers would silently run whatever the Brain model is (e.g. Fable).
    opus_env = os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
    if opus_env:
        cfg["default_opus_model"] = opus_env
        if "BRAIN_MODEL" not in os.environ:
            cfg["brain_model"] = opus_env
        if "GRADER_MODEL" not in os.environ:
            cfg["grader_model"] = opus_env

    # Validate effort_level against the allowlist (defense-in-depth: it is
    # interpolated unquoted into make_opus_command's shell string).
    if cfg.get("effort_level") not in EFFORT_LEVELS:
        logger.warning(
            "effort_level %r not in %s; falling back to %r",
            cfg.get("effort_level"),
            sorted(EFFORT_LEVELS),
            DEFAULT_EFFORT_LEVEL,
        )
        cfg["effort_level"] = DEFAULT_EFFORT_LEVEL

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
