#!/bin/bash
# codex-brain-hooks.sh <script.sh> — runs one Brain restriction hook for the
# Commander's Codex Brain only (IC_ROLE=brain + IRONCLAUDE_CLIENT=codex).
# The Codex Brain loads this plugin's trusted hooks.json, so its Brain hooks are
# registered there behind this dispatcher; every other session exits 0 here
# without reading the payload. The Claude Brain runs the same scripts from its
# project settings.json (commander/src/brain/brain_settings_hooks.json).
if [ "${IC_ROLE:-}" != "brain" ] || [ "${IRONCLAUDE_CLIENT:-}" != "codex" ]; then
  exit 0
fi

TARGET="${1:-}"
HOOK_DIR="${IC_BRAIN_HOOK_DIR:-$HOME/.claude/ironclaude-hooks}"
if [[ ! "$TARGET" =~ ^[A-Za-z0-9][A-Za-z0-9-]*\.sh$ ]] || [ ! -f "$HOOK_DIR/$TARGET" ]; then
  echo "✗ [codex-brain-hooks]: Blocked - Brain hook '${TARGET}' is missing from ${HOOK_DIR}; enforcement cannot run. Restart the Commander to redeploy Brain hooks." >&2
  exit 2
fi

# Brain hooks call run_hook; this marks them as Brain hooks so the Codex-Brain
# worker stand-down in hook-logger.sh does not skip them.
export IC_BRAIN_HOOK=1
exec bash "$HOOK_DIR/$TARGET"
