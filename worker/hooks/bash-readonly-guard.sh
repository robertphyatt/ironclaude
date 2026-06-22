#!/bin/bash
# bash-readonly-guard.sh — shared predicates for the read-only Bash allowlist.
# Sourced by professional-mode-guard.sh. Pure functions, no side effects.
#
# NOTE: like hook-logger.sh, this file defines functions only and does NOT call
# run_hook / set -euo pipefail at the top level — it must be sourceable without
# side effects. All branches are if-guarded so the functions are safe when the
# caller has `set -euo pipefail` and an ERR trap active.

# _has_blocked_metachars CMD -> 0 (true) if CMD contains a metacharacter that
# could chain, substitute, redirect, or inject a second command.
# Blocks: ; & | ` $(  > <  and newline.
_has_blocked_metachars() {
  local cmd="$1"
  case "$cmd" in *$'\n'*) return 0 ;; esac
  if printf '%s' "$cmd" | grep -qE '[;&|`<>]|\$\('; then return 0; fi
  return 1
}

# _find_has_write_action CMD -> 0 (true) if CMD is a `find` (first token) with a
# write/exec action. Complete GNU/BSD set: -exec -execdir -delete -fls -fprint
# -fprintf -fprint0 -ok -okdir. (-fls and -fprint* write to a named file;
# -ls/-print* are stdout-only and stay allowed.)
_find_has_write_action() {
  local cmd="$1"
  if ! printf '%s' "$cmd" | grep -qE '^[[:space:]]*find([[:space:]]|$)'; then return 1; fi
  if printf '%s' "$cmd" | grep -qE '(^|[[:space:]])-(exec|execdir|delete|fls|fprint|fprintf|fprint0|ok|okdir)([[:space:]]|$)'; then return 0; fi
  return 1
}

# is_readonly_research_bash CMD -> 0 (true) if CMD is a safe read-only research
# command: first token in the allowlist, no blocked metacharacters, and (if find)
# no write/exec action.
is_readonly_research_bash() {
  local cmd="$1"
  if _has_blocked_metachars "$cmd"; then return 1; fi
  if ! printf '%s' "$cmd" | grep -qE '^[[:space:]]*(cat|head|tail|wc|grep|rg|find|ls)([[:space:]]|$)'; then return 1; fi
  if _find_has_write_action "$cmd"; then return 1; fi
  return 0
}
