#!/bin/bash
# brain-hooks-canary.sh — UserPromptSubmit hook for the Commander's Codex Brain.
# Proves the Brain's restriction hooks are trusted and running: the Commander
# requires this marker after the Brain's first turn starts and refuses to run the
# Brain without it (codex_brain_client.py, _await_hooks_canary).
GATE_SESSION="${IRONCLAUDE_BRAIN_GATE_SESSION:-}"
case "$GATE_SESSION" in
  ""|*[!A-Za-z0-9._-]*|.*) exit 0 ;;
esac
# Gate root (design 2026-10-17 v120-completeness R3). Write nothing unless it is a real directory
# owned by this user with no group or other permission bits; the Commander then stops the Brain.
# Keep gate_root_problem identical to brain-gated-actions.sh (a Commander test compares them).
gate_root_problem() {
  local root="$1" perms
  if [ -L "$root" ]; then echo "is a symlink"; return 0; fi
  if [ ! -d "$root" ]; then echo "is not a directory"; return 0; fi
  if [ ! -O "$root" ]; then echo "is not owned by the current user"; return 0; fi
  perms=$(ls -ld "$root") || { echo "has unreadable permissions"; return 0; }
  if [ "${perms:4:6}" != "------" ]; then echo "has group or other permission bits"; fi
  return 0
}
GATE_ROOT="${IRONCLAUDE_BRAIN_GATE_ROOT:-$HOME/.ironclaude/state/brain-gate}"
GATE_ROOT="${GATE_ROOT%/}"
[ -z "$(gate_root_problem "$GATE_ROOT")" ] || exit 0
mkdir -p "$GATE_ROOT/$GATE_SESSION" && touch "$GATE_ROOT/$GATE_SESSION/hooks-live"
exit 0
