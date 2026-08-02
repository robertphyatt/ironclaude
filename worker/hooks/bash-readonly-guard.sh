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

# _strip_dash_c CMD -> CMD with a LEADING `<tool> -C <path>` normalized to `<tool>`.
# Generalizes the make -C normalization professional-mode-guard.sh used at :468.
#
# INVARIANT — load-bearing, do not break: the sed captures and re-emits the FIRST
# TOKEN, so the normalized string always begins with the same token as the raw one.
# professional-mode-guard.sh's sqlite-write check (:478) and _find_has_write_action
# both anchor on the RAW command; they stay correct only because of this invariance.
#
# MUST be called only AFTER _has_blocked_metachars has rejected chaining: the
# [^[:space:]]+ path class would otherwise swallow a payload like $(evil).
_strip_dash_c() {
  printf '%s' "$1" | sed -E 's/^([[:space:]]*[A-Za-z0-9_.-]+)[[:space:]]+-C[[:space:]]+[^[:space:]]+[[:space:]]+/\1 /'
}

# is_readonly_git CMD -> 0 (true) if CMD is a read-only git command.
#
# TWO greps, deliberately separate. Grep A is byte-exact with the long-standing
# alternation minus the five write-capable subcommands; its trailing \b is required
# (it matches before '-', so `git diff-index` stays allowed). Grep B admits only
# pinned READ-ONLY FORMS of stash/branch/remote/tag/reflog.
#
# Whitelist-only, deliberately: nothing here enumerates destructive flags, so an
# unrecognised form falls through to blocked. A blocklist of -d|-D|-m would leave
# --delete as a loophole.
#
# Known deliberate omission: `git stash show` is read-only but not admitted — it is
# outside the approved set, and fail-closed is the correct default for this guard.
is_readonly_git() {
  local cmd="$1"
  if _has_blocked_metachars "$cmd"; then return 1; fi
  local normalized
  normalized=$(_strip_dash_c "$cmd")
  # Grep A — always read-only whatever the arguments.
  if printf '%s' "$normalized" | grep -qE '^\s*git\s+(diff|status|log|show|blame|rev-list|ls-files|ls-tree|check-ignore)\b'; then return 0; fi
  # Grep B — read-only FORMS only. Anchored so no write argument can follow.
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+stash[[:space:]]+list([[:space:]]|$)'; then return 0; fi
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+branch([[:space:]]+(-a|-r|-vv|-v|--list|--all|--remotes|--show-current))*[[:space:]]*$'; then return 0; fi
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+remote([[:space:]]+-v)?[[:space:]]*$'; then return 0; fi
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+tag[[:space:]]*$'; then return 0; fi
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+tag[[:space:]]+(-l|--list)([[:space:]]|$)'; then return 0; fi
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+reflog[[:space:]]*$'; then return 0; fi
  if printf '%s' "$normalized" | grep -qE '^[[:space:]]*git[[:space:]]+reflog[[:space:]]+show([[:space:]]|$)'; then return 0; fi
  return 1
}

# is_review_allowed CMD -> 0 (true) if CMD is permitted during the reviewing stage.
# Alternation and trailing \b are byte-exact with professional-mode-guard.sh:477.
is_review_allowed() {
  local cmd="$1"
  if _has_blocked_metachars "$cmd"; then return 1; fi
  local normalized
  normalized=$(_strip_dash_c "$cmd")
  # Unchanged members, byte-exact with :477 plus diff. No env prefix.
  if printf '%s' "$normalized" | grep -qE '^\s*(sqlite3|git\s+(diff|status|log|show|blame|ls-files|check-ignore)|pytest|make\s+test|cat|head|tail|wc|grep|rg|find|ls|diff)\b'; then return 0; fi
  # Env prefixes (any VAR=value, including PATH=) and any <path>/python -m pytest
  # are admitted to the pytest forms only — not to the alternation above. Stated
  # exactly because the set is wider than "the pytest forms" suggests: this repo
  # needs .venv/bin/python, and bare `pytest` has always been allowed. This
  # allowlist is workflow discipline, not an execution boundary — pytest collection
  # executes arbitrary repo Python either way.
  if printf '%s' "$normalized" | grep -qE '^\s*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*(pytest|([^[:space:]]*/)?python[0-9.]*\s+-m\s+pytest)\b'; then return 0; fi
  return 1
}

# is_readonly_research_bash CMD -> 0 (true) if CMD is a safe read-only research
# command: first token in the allowlist, no blocked metacharacters, and (if find)
# no write/exec action.
is_readonly_research_bash() {
  local cmd="$1"
  if _has_blocked_metachars "$cmd"; then return 1; fi
  if ! printf '%s' "$cmd" | grep -qE '^[[:space:]]*(cat|head|tail|wc|grep|rg|find|ls|diff)([[:space:]]|$)'; then return 1; fi
  if _find_has_write_action "$cmd"; then return 1; fi
  return 0
}
