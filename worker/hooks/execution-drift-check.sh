#!/bin/bash
# execution-drift-check.sh - execution side-quest detector (v1.1.16 L2b-1 R5, L2b-1b;
# session attribution 2026-10-15).
#
# Triggers (worker/hooks/hooks.json):
#   PreToolUse   Bash|exec_command          -> close this agent lineage's leftover
#                                              windows, then open one (dirty snapshot)
#                                              unless the command is read-only
#   PostToolUse  Bash|exec_command          -> close that command's window
#   SubagentStop                            -> close that subagent's leftover windows
#   PreToolUse   state-manager submit_task  -> settle (discard) all open windows, block (exit 2) on drift
#   Library      IC_DRIFT_LIB=1 source      -> _drift_effective_root and
#                                              ic_drift_stop_violations, used by
#                                              get-back-to-work-impl.sh at Stop;
#                                              ic_drift_record_writes, used by
#                                              professional-mode-guard.sh (writer log)
#
# Root: _drift_effective_root resolves the session's effective root exactly as the
#   write guard does (workspace-path-adapter.sh workspace_bind_effective_root): a
#   managed worktree (or the primary it owns) when the session has an active
#   assignment, else the cwd's git toplevel.
# Attribution: a window is one shell command of THIS session (subagent commands carry
#   the parent's session_id), keyed by agent lineage (agent_id, else "main") and
#   tool_use_id. Closing it appends (dirty after - dirty before) to a ledger per
#   in-progress wave_tasks row. Other sessions' changes made between this session's
#   commands never enter the ledger. submit_task and Stop discard all open windows (R3g).
# Drift = (ledger paths ∩ paths dirty now in the root; gitignored files never appear)
#         minus the plan's tasks[].allowed_files: relative entries as-is, absolute
#         entries under the root made root-relative, other absolute entries dropped.
#         Exact string match only. A ledger path later committed or reverted drops out.
# Cross-session exclusion (2026-10-17 hotfix R2): before reporting, drift drops a path
#   (a) whose newest writer-log record (ic_drift_record_writes, fed by
#   professional-mode-guard.sh for every allowed file-tool write) is another session's,
#   with an epoch at or after the earliest created_at of the checked rows (no created_at:
#   (a) drops nothing), or (b) that a live session's same-root ledger lists: the ledger's
#   <ledger>.root names the same directory as this root, by file identity (a ledger
#   without one is ignored), and its wave_tasks row is in_progress and belongs to a session in executing or reviewing
#   (professional_mode not checked; v120-completeness R4d).
# Accepted residuals (R3): another live session's shell write during an overlapping window
#   lands in both ledgers and is excluded for both (fail open); a session left in
#   executing/reviewing keeps its in-progress rows' ledgers live until its next
#   create_plan or reset; a
#   finished session's shell write during an overlapping window blocks (pre-fix behavior);
#   this session's shell rewrite of a file whose newest record is another session's recent
#   write (fail open); a record lost to a concurrent trim (the path may block).
# Known gaps (fail open, accepted 2026-10-15): Codex write_stdin input, background
#   commands that write after their PostToolUse, files written by MCP tools, and (R3b)
#   two parallel shell commands of one agent lineage: the second's PreToolUse closes
#   the first's window early. A window with no PostToolUse (a command denied before it
#   ran, or a failed Claude Bash that fires PostToolUseFailure) stays open until the
#   same lineage's next shell PreToolUse. Sweeps (next same-lineage PreToolUse, SubagentStop, settle at submit/Stop) discard the leftover's delta (R3f), so a failed or denied command's own writes are not counted.
#
# Fail-open (no block; the caller logs via log_hook): professional mode not 'on';
#   stage not executing/reviewing; no cwd or cwd not in a git repo; assignment
#   evidence unreadable, ambiguous or unbindable (reason in _DRIFT_ROOT_ERROR);
#   session row, plan_json or its tasks array unreadable.
# Fail-closed (block): a resolved root and readable plan with non-empty drift, or an
#   unsupported newline path in current dirt.

_DRIFT_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The write guard's effective-root resolver. Sourcing only assigns empty WORKSPACE_*
# globals. If it is missing, sessions with workspace evidence fail open.
source "$_DRIFT_HOOKS_DIR/workspace-path-adapter.sh" 2>/dev/null || true
# is_readonly_git / is_readonly_research_bash for workspace_input_is_readonly. If missing,
# every shell command counts as mutating (a window opens), which never under-attributes.
source "$_DRIFT_HOOKS_DIR/bash-readonly-guard.sh" 2>/dev/null || true

_DRIFT_ROOT=""
_DRIFT_ROOT_ERROR=""

_drift_sql() {
  local db="$HOME/.claude/ironclaude.db"
  [ -f "$db" ] || return 0
  sqlite3 -cmd ".timeout 10000" "$db" "$1" 2>/dev/null || true
}

_drift_q() { printf '%s' "${1:-}" | sed "s/'/''/g"; }

# _drift_effective_root SESSION CWD
# Status 0: _DRIFT_ROOT holds the root. Status 1: fail-open, reason in _DRIFT_ROOT_ERROR.
# Call it directly (never in $(...)) so the caller can log the reason. Writes
# nothing to stdout. May renew the primary-checkout owner heartbeat (as every
# guarded file operation does).
_drift_effective_root() {
  local session="$1" cwd="${2:-}" top event_cwd common repository_identity workspace_db safe_session row_count
  _DRIFT_ROOT=""
  _DRIFT_ROOT_ERROR=""
  if [ -z "$cwd" ] || [ ! -d "$cwd" ]; then
    _DRIFT_ROOT_ERROR="hook cwd is missing"
    return 1
  fi
  if ! top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || [ -z "$top" ]; then
    _DRIFT_ROOT_ERROR="hook cwd is not in a git repository"
    return 1
  fi
  workspace_db="${WORKSPACE_MANAGER_DB_PATH:-$HOME/.claude/ironclaude-workspaces.db}"
  if [ ! -f "$workspace_db" ]; then
    _DRIFT_ROOT="$top"
    return 0
  fi
  if ! type workspace_bind_effective_root >/dev/null 2>&1; then
    _DRIFT_ROOT_ERROR="workspace-path-adapter.sh is not loaded"
    return 1
  fi
  if ! event_cwd=$(workspace_canonical_dir "$cwd"); then
    _DRIFT_ROOT_ERROR="hook cwd is not a canonical directory"
    return 1
  fi
  if ! common=$(git -C "$event_cwd" rev-parse --git-common-dir 2>/dev/null); then
    _DRIFT_ROOT_ERROR="cannot resolve the git common directory"
    return 1
  fi
  case "$common" in /*) ;; *) common="$event_cwd/$common" ;; esac
  if ! repository_identity=$(workspace_canonical_dir "$common"); then
    _DRIFT_ROOT_ERROR="git common directory is not canonical"
    return 1
  fi
  safe_session=$(workspace_sql_quote "$session")
  if ! row_count=$(sqlite3 "$workspace_db" ".timeout 10000" \
      "SELECT COUNT(*) FROM assignments WHERE repository_identity='$(workspace_sql_quote "$repository_identity")' AND owner_session_id='$safe_session' AND lifecycle_status NOT IN ('integrated','abandoned','cleaned');" 2>/dev/null); then
    _DRIFT_ROOT_ERROR="cannot read managed-worktree assignment"
    return 1
  fi
  if [ "$row_count" = "0" ]; then
    _DRIFT_ROOT="$top"
    return 0
  fi
  if [ "$row_count" != "1" ]; then
    _DRIFT_ROOT_ERROR="ambiguous managed-worktree assignment"
    return 1
  fi
  # workspace_bind_effective_root compares the primary owner to $SESSION_TAG.
  local SESSION_TAG="$session"
  if ! workspace_bind_effective_root "$workspace_db" "$repository_identity" "$event_cwd" "$safe_session"; then
    _DRIFT_ROOT_ERROR="managed-worktree binding failed: ${WORKSPACE_ADAPTER_ERROR:-unknown}"
    return 1
  fi
  _DRIFT_ROOT="$WORKSPACE_EFFECTIVE_ROOT"
  return 0
}

# Repo-relative dirty paths, one per line, sorted. Status 2 means a current
# dirty path contains LF and cannot safely enter the line-based comparison.
# --untracked-files=all lists
# files inside new directories individually; -z avoids path quoting. A rename
# emits both destination and source; a copy emits its destination only.
_drift_dirty_paths() {
  local root="$1" status_file paths_file rec next_record="" unsupported=0 status
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || return 1
  status_file=$(mktemp) || return 1
  paths_file=$(mktemp) || { rm -f "$status_file"; return 1; }
  # --no-optional-locks: a read never rewrites the index, so a hook killed mid-run cannot leave a
  # stale .git/index.lock (design 2026-10-18 lock-free-read-git).
  if ! git -C "$root" --no-optional-locks status --porcelain=v1 -z --untracked-files=all > "$status_file" 2>/dev/null; then
    rm -f "$status_file" "$paths_file"
    return 1
  fi
  while IFS= read -r -d '' rec; do
    if [ -n "$next_record" ]; then
      if [ "$next_record" = "rename" ]; then
        if [[ "$rec" == *$'\n'* ]]; then unsupported=1; break; fi
        printf '%s\n' "$rec"
      fi
      next_record=""
      continue
    fi
    if [[ "${rec:3}" == *$'\n'* ]]; then unsupported=1; break; fi
    case "${rec:0:2}" in
      R?|?R) next_record="rename" ;;
      C?|?C) next_record="copy" ;;
    esac
    printf '%s\n' "${rec:3}"
  done < "$status_file" > "$paths_file"
  rm -f "$status_file"
  if [ "$unsupported" = "1" ]; then
    rm -f "$paths_file"
    return 2
  fi
  LC_ALL=C sort -u "$paths_file"
  status=$?
  rm -f "$paths_file"
  [ "$status" = "0" ] && return 0
  return 1
}

# _drift_rel_by_identity ENTRY ROOT
# Resolve the longest existing ancestor directory physically, then preserve the
# missing suffix and final leaf. Status 1 means external/root entry; 2 means an
# ancestor exists but cannot be resolved.
_drift_rel_by_identity() {
  local entry="$1" root="$2" dir physical suffix part
  local -a components=() normalized=()
  IFS='/' read -r -a components <<< "$entry"
  for part in "${components[@]}"; do
    case "$part" in
      ''|.) ;;
      ..)
        if [ "${#normalized[@]}" -gt 0 ]; then
          unset "normalized[$((${#normalized[@]} - 1))]"
        fi
        ;;
      *) normalized+=("$part") ;;
    esac
  done
  entry="/$(IFS=/; printf '%s' "${normalized[*]}")"
  suffix=$(basename -- "$entry")
  dir=$(dirname -- "$entry")
  while ! physical=$(cd -P -- "$dir" 2>/dev/null && pwd -P); do
    if [ -d "$dir" ] || { [ -L "$dir" ] && [ ! -e "$dir" ]; } || [ "$dir" = "/" ]; then
      return 2
    fi
    part=$(basename -- "$dir")
    suffix="$part/$suffix"
    dir=$(dirname -- "$dir")
  done
  while :; do
    if [ "$physical" -ef "$root" ]; then
      printf '%s\n' "$suffix"
      return 0
    fi
    [ "$physical" = "/" ] && return 1
    suffix="$(basename -- "$physical")/$suffix"
    physical=$(dirname -- "$physical")
  done
}

# _drift_allowed_files SESSION ROOT
# The plan's tasks[].allowed_files in root-relative form, sorted. Absolute entries
# map by physical ancestry (_drift_rel_by_identity); external entries are dropped,
# unreadable ancestry fails open. Relative entries pass through. Status 1 when
# plan or ancestry cannot be resolved.
_drift_allowed_files() {
  local plan out mapped root="$2" entry status
  plan=$(_drift_sql "SELECT plan_json FROM sessions WHERE terminal_session='$(_drift_q "$1")';")
  [ -n "$plan" ] || return 1
  out=$(printf '%s' "$plan" | jq -r 'if (.tasks | type) == "array" then [.tasks[] | (.allowed_files // [])[] | strings] | .[] else error("plan has no tasks array") end' 2>/dev/null) || return 1
  mapped=$(while IFS= read -r entry; do
    case "$entry" in
      '') ;;
      /*)
        _drift_rel_by_identity "$entry" "$root" || {
          status=$?
          [ "$status" = "1" ] || exit 1
        }
        ;;
      *) printf '%s\n' "$entry" ;;
    esac
  done <<< "$out") || return 1
  printf '%s\n' "$mapped" | sed '/^$/d' | LC_ALL=C sort -u
}

# Newest wave_tasks row id for SESSION's TASK_ID in the current wave.
_drift_row_id() {  # SESSION TASK_ID
  local id
  [[ "${2:-}" =~ ^[0-9]+$ ]] || return 1
  id=$(_drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.task_id=$2 AND w.wave_number = s.current_wave ORDER BY w.id DESC LIMIT 1;")
  [ -n "$id" ] || return 1
  printf '%s' "$id"
}

_drift_safe() { printf '%s' "$1" | tr -cd 'a-zA-Z0-9_-'; }

# Window and ledger files. '.' separates fields because _drift_safe never emits it,
# so a session or lineage glob can never match another session's or lineage's files.
_drift_window_file() {  # SESSION LINEAGE KEY
  printf '%s/.claude/ironclaude-drift-window-%s.%s.%s.txt' "$HOME" "$(_drift_safe "$1")" "$(_drift_safe "$2")" "$(_drift_safe "$3")"
}
_drift_ledger_file() {  # SESSION ROW_ID
  printf '%s/.claude/ironclaude-drift-touched-%s.%s.txt' "$HOME" "$(_drift_safe "$1")" "$2"
}

# In-progress wave_tasks row ids of SESSION's current wave, one per line.
_drift_in_progress_rows() {
  _drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.status='in_progress' AND w.wave_number = s.current_wave ORDER BY w.id;"
}

# Writer log (2026-10-17 cross-session hotfix R1). professional-mode-guard.sh appends
# "epoch<TAB>session<TAB>path" for every file-tool write it allows (Claude Edit, Write,
# MultiEdit, NotebookEdit; Codex apply_patch, every target), so ic_drift_violations can
# tell another session's file from this session's drift.
_DRIFT_WRITERS_MAX_DEFAULT=10000
_drift_writers_log() { printf '%s' "${IC_DRIFT_WRITERS_LOG:-$HOME/.claude/ironclaude-drift-writers.tsv}"; }

# _drift_physical_path PATH BASE -> PATH made absolute against BASE, with its longest
# existing ancestor resolved physically (PreToolUse runs before Write creates parents).
_drift_physical_path() {
  local p="$1" dir suffix phys
  case "$p" in /*) ;; *) p="$2/$p" ;; esac
  suffix=$(basename -- "$p")
  dir=$(dirname -- "$p")
  while ! phys=$(cd -P -- "$dir" 2>/dev/null && pwd -P); do
    [ "$dir" = "/" ] && return 1
    suffix="$(basename -- "$dir")/$suffix"
    dir=$(dirname -- "$dir")
  done
  if [ "$phys" = "/" ]; then printf '/%s\n' "$suffix"; else printf '%s/%s\n' "$phys" "$suffix"; fi
}

# ic_drift_record_writes SESSION BASE: append one record per target path on stdin (one
# per line; relative paths resolve against BASE). Fail-open: it never prints, never
# fails, and skips a path holding a tab or CR. Past IC_DRIFT_WRITERS_MAX_LINES lines
# (default _DRIFT_WRITERS_MAX_DEFAULT) the log keeps its newest half. A concurrent trim
# can lose a record; that fails closed (the path may block again).
ic_drift_record_writes() {
  (
    set +eu
    session=$(_drift_safe "$1")
    [ -n "$session" ] || exit 0
    log=$(_drift_writers_log)
    mkdir -p "$(dirname -- "$log")" || exit 0
    now=$(date +%s)
    while IFS= read -r target; do
      [ -n "$target" ] || continue
      case "$target" in *$'\t'*|*$'\r'*) continue ;; esac
      abs=$(_drift_physical_path "$target" "$2") || continue
      printf '%s\t%s\t%s\n' "$now" "$session" "$abs" >> "$log" || exit 0
    done
    max="${IC_DRIFT_WRITERS_MAX_LINES:-$_DRIFT_WRITERS_MAX_DEFAULT}"
    [[ "$max" =~ ^[1-9][0-9]*$ ]] || max="$_DRIFT_WRITERS_MAX_DEFAULT"
    lines=$(wc -l < "$log" | tr -d ' ')
    if [ "${lines:-0}" -gt "$max" ]; then
      tmp="$log.tmp.$$"
      if tail -n "$((max / 2))" "$log" > "$tmp"; then mv -f "$tmp" "$log"; else rm -f "$tmp"; fi
    fi
  ) >/dev/null 2>&1
  return 0
}

# ic_drift_open_window SESSION LINEAGE KEY ROOT  (ROOT from _drift_effective_root)
# Status 0: window written or already open. Status 1: skipped (git or write failed).
# Status 2: current dirt has an unsupported newline path; no window is written.
# Line 1 of the window file is the root; the rest is the dirty snapshot.
ic_drift_open_window() {
  local file paths tmp
  file=$(_drift_window_file "$1" "$2" "$3")
  if [ -e "$file" ]; then return 0; fi
  if paths=$(_drift_dirty_paths "$4"); then :; else return $?; fi
  mkdir -p "$(dirname "$file")" || return 1
  tmp="${file}.tmp.$$"
  { printf '%s\n' "$4"; printf '%s\n' "$paths"; } > "$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
  return 0
}

# _drift_close_window_file FILE SESSION
# A command's own PostToolUse close: append the window's delta (paths dirty now that
# were not dirty at its snapshot) to each in-progress row's ledger, then remove it.
_drift_close_window_file() {
  local file="$1" session="$2" root before now delta row status content ledger
  # Read once, without pipes. A window removed mid-close, or with an empty root line,
  # would make `git -C ""` list the hook cwd's whole checkout: skip it (R3d).
  if content=$(cat "$file" 2>/dev/null); then :; else return 1; fi
  root="${content%%$'\n'*}"
  if [ -z "$root" ]; then return 1; fi
  if [ "$content" = "$root" ]; then before=""; else before="${content#*$'\n'}"; fi
  if now=$(_drift_dirty_paths "$root"); then :; else
    status=$?
    rm -f "$file"
    return "$status"
  fi
  delta=$(LC_ALL=C comm -13 \
    <(printf '%s\n' "$before" | sed '/^$/d' | LC_ALL=C sort -u) \
    <(printf '%s\n' "$now" | sed '/^$/d' | LC_ALL=C sort -u))
  if [ -n "$delta" ]; then
    for row in $(_drift_in_progress_rows "$session"); do
      ledger=$(_drift_ledger_file "$session" "$row")
      printf '%s\n' "$delta" >> "$ledger"
      # The root beside the ledger lets another session match only same-root paths (R2b).
      printf '%s\n' "$root" > "${ledger%.txt}.root"
    done
  fi
  rm -f "$file"
  return 0
}

# ic_drift_close_window SESSION LINEAGE KEY: the command's own PostToolUse.
ic_drift_close_window() {
  local f
  f=$(_drift_window_file "$1" "$2" "$3")
  if [ -f "$f" ]; then _drift_close_window_file "$f" "$1" || true; fi
  return 0
}

# ic_drift_close_lineage SESSION LINEAGE: sweep one agent lineage's leftover windows
# (R3b next PreToolUse, SubagentStop). A leftover belongs to a command
# that failed or was denied; any change seen now may be another session's write made
# after it ended, so the window is discarded without appending (R3f). Other
# lineages' windows are never touched.
ic_drift_close_lineage() {
  local f
  for f in "$HOME/.claude/ironclaude-drift-window-$(_drift_safe "$1").$(_drift_safe "$2")".*.txt; do
    if [ -f "$f" ]; then rm -f "$f"; fi
  done
  return 0
}

# ic_drift_settle_windows SESSION: submit_task and Stop (R3g). A window still open then
# cannot be told apart from a failed or denied command's leftover, and any change seen
# now may be another session's write, so every open window of SESSION (main and
# subagent) is discarded without appending.
ic_drift_settle_windows() {
  local f
  for f in "$HOME/.claude/ironclaude-drift-window-$(_drift_safe "$1")".*.txt; do
    if [ -f "$f" ]; then rm -f "$f"; fi
  done
  return 0
}

# _drift_foreign_paths SESSION ROOT CANDIDATES ROW_ID... -> the CANDIDATES (root-relative,
# one per line) that another live session wrote (R2):
#   (a) the newest writer-log record for the file is another session's, with an epoch at
#       or after "since", the earliest created_at of SESSION's ROW_IDs (no since: none);
#   (b) another session's ledger <other>.<row>.txt lists it, its <ledger>.root names the same directory as
#       ROOT, and wave_tasks row <row> belongs to <other>, is in_progress, and <other> is
#       executing or reviewing (professional_mode is not checked).
# Writer records match by identity (_drift_rel_by_identity), so case and symlink
# spellings agree and a record outside ROOT never matches. Prints nothing on any error
# (no exclusion); always status 0.
_drift_foreign_paths() {
  local session="$1" root="$2" candidates="$3" safe log rows since entry rel f name other row live ledger_root
  shift 3
  safe=$(_drift_safe "$session")
  [ -n "$candidates" ] || return 0
  rows=$(printf '%s,' "$@")
  rows="${rows%,}"
  since=""
  if [[ "$rows" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    since=$(_drift_sql "SELECT MIN(CAST(strftime('%s',created_at) AS INTEGER)) FROM wave_tasks WHERE terminal_session='$(_drift_q "$session")' AND id IN ($rows);")
  fi
  log=$(_drift_writers_log)
  if [[ "$since" =~ ^[0-9]+$ ]] && [ -f "$log" ]; then
    awk -F '\t' 'NF == 3 { s[$3] = $2; t[$3] = $1 } END { for (p in s) printf "%s\t%s\t%s\n", s[p], t[p], p }' "$log" 2>/dev/null \
      | DRIFT_NAMES="$(printf '%s\n' "$candidates" | sed 's#.*/##')" awk -F '\t' -v me="$safe" -v since="$since" '
          BEGIN { n = split(ENVIRON["DRIFT_NAMES"], a, "\n"); for (i = 1; i <= n; i++) if (a[i] != "") want[a[i]] = 1 }
          $1 != me && ($2 + 0) >= (since + 0) { b = $3; sub(/.*\//, "", b); if (b in want) print $3 }' 2>/dev/null \
      | while IFS= read -r entry; do
          rel=$(_drift_rel_by_identity "$entry" "$root") || continue
          if grep -qxF -- "$rel" <<< "$candidates"; then printf '%s\n' "$rel"; fi
        done || true
  fi
  for f in "$HOME/.claude/ironclaude-drift-touched-"*.txt; do
    [ -f "$f" ] || continue
    name=${f##*-drift-touched-}; name=${name%.txt}; other=${name%.*}; row=${name##*.}
    [[ $row =~ ^[0-9]+$ ]] || continue
    [ "$other" != "$safe" ] || continue
    # Roots compare by file identity, not spelling (code vs Code, symlinks), like rule (a)
    # (design 2026-10-18 drift-foreign-session R1).
    ledger_root=$(cat "${f%.txt}.root" 2>/dev/null) || continue
    [ -n "$ledger_root" ] && [ "$ledger_root" -ef "$root" ] || continue
    live=$(_drift_sql "SELECT 1 FROM wave_tasks w JOIN sessions s ON s.terminal_session=w.terminal_session WHERE w.id=$row AND w.terminal_session='$(_drift_q "$other")' AND w.status='in_progress' AND s.workflow_stage IN ('executing','reviewing') LIMIT 1;")
    [ "$live" = "1" ] || continue
    printf '%s\n' "$candidates" | awk 'NR == FNR { if ($0 != "") want[$0] = 1; next } ($0 in want)' "$f" - 2>/dev/null || true
  done
  return 0
}

# ic_drift_violations SESSION ROOT ROW_ID...  (ROOT from _drift_effective_root)
# Prints drift paths (possibly none) with status 0; status 1 means fail-open;
# status 2 means an unsupported current Git pathname. A row with no ledger adds
# nothing: no command of this session changed a path while it was in progress.
# Paths another live session wrote are dropped (_drift_foreign_paths, R2).
ic_drift_violations() {
  local session="$1" root="$2" allowed touched="" row file current drift foreign
  shift 2
  [ -n "$root" ] || return 1
  if current=$(_drift_dirty_paths "$root"); then :; else return $?; fi
  allowed=$(_drift_allowed_files "$session" "$root") || return 1
  for row in "$@"; do
    file=$(_drift_ledger_file "$session" "$row")
    if [ -f "$file" ]; then touched="${touched}$(cat "$file")"$'\n'; fi
  done
  drift=$(LC_ALL=C comm -23 \
    <(LC_ALL=C comm -12 \
        <(printf '%s\n' "$current" | sed '/^$/d' | LC_ALL=C sort -u) \
        <(printf '%s\n' "$touched" | sed '/^$/d' | LC_ALL=C sort -u)) \
    <(printf '%s\n' "$allowed" | sed '/^$/d' | LC_ALL=C sort -u)) || return 1
  [ -n "$drift" ] || return 0
  foreign=$(_drift_foreign_paths "$session" "$root" "$drift" "$@")
  LC_ALL=C comm -23 \
    <(printf '%s\n' "$drift") \
    <(printf '%s\n' "$foreign" | sed '/^$/d' | LC_ALL=C sort -u) || return 1
  return 0
}

# ic_drift_stop_violations SESSION ROOT  (ROOT from _drift_effective_root)
# Stop-time check across every in_progress task of the current wave, after
# settling the session's open windows. Status 0 with no output when nothing is
# in progress; status 1 means fail-open.
ic_drift_stop_violations() {
  local rows
  ic_drift_settle_windows "$1"
  rows=$(_drift_in_progress_rows "$1")
  [ -n "$rows" ] || return 0
  # shellcheck disable=SC2086
  ic_drift_violations "$1" "$2" $rows
}

# ic_drift_block_message FILES -> the block text listing each drift path.
ic_drift_block_message() {
  local list
  list=$(printf '%s\n' "$1" | sed '/^$/d; s/^/  - /')
  printf '%s\n\n%s\n%s\n\n%s' \
    "BLOCKED — EXECUTION DRIFT" \
    "These files changed while this session's own shell commands were running, and none of them is in the plan's allowed_files:" \
    "$list" \
    "If you made the change as a side quest, undo your own change. If the plan needs the file, retreat and amend the plan so it is listed in allowed_files. Files another live session recently wrote with a file tool, or lists in its own drift ledger, are already left out of this list. If a listed file is still not yours, never delete, revert or edit a file you did not change: report the block to the operator and stop."
}

ic_drift_unsupported_path_message() {
  printf '%s' 'BLOCKED — UNSUPPORTED GIT PATH

Current Git dirt contains an unsupported newline Git path. The drift check cannot compare it safely with line-based baseline and allowed_files entries. Resolve that pathname before submitting or stopping the task.'
}

# Library mode: define the functions only.
if [ "${IC_DRIFT_LIB:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

SCRIPT_DIR="$_DRIFT_HOOKS_DIR"
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "EXECUTION-DRIFT-CHECK"

INPUT=$(cat)
init_session_id

# Professional-mode gate (idiom from plan-execution-reminder.sh). A plain read,
# not db_read_or_fail: its hard_fail exits 2 and would block claim/submit.
SAFE_SESSION=$(_drift_q "$SESSION_TAG")
PROF_MODE=$(_drift_sql "SELECT professional_mode FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
if [ "$PROF_MODE" != "on" ]; then
  log_hook "EXECUTION-DRIFT-CHECK" "Disabled" "professional mode ${PROF_MODE:-unreadable}"
  exit 0
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
EVENT_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
TASK_ID=$(printf '%s' "$INPUT" | jq -r '.tool_input.task_id // empty' 2>/dev/null || true)
HOOK_EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty' 2>/dev/null || true)

# A finished subagent never runs another command, so its leftover windows (a last
# command that was denied, or failed and fired PostToolUseFailure) would otherwise
# linger until the next submit/Stop.
# A SubagentStop without agent_id closes nothing: the main lineage is never swept here.
if [ "$HOOK_EVENT" = "SubagentStop" ]; then
  STOP_AGENT=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || true)
  if [ -n "$STOP_AGENT" ]; then
    ic_drift_close_lineage "$SESSION_TAG" "$STOP_AGENT"
    log_hook "EXECUTION-DRIFT-CHECK" "Window" "closed lineage ${STOP_AGENT} at SubagentStop"
  else
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "SubagentStop without agent_id"
  fi
  exit 0
fi

case "$TOOL_NAME" in
  Bash|exec_command)
    SHORT_TOOL="shell"
    WINDOW_KEY=$(printf '%s' "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null || true)
    LINEAGE=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || true)
    [ -n "$LINEAGE" ] || LINEAGE="main"
    if [ -z "$WINDOW_KEY" ]; then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "shell event without tool_use_id"
      exit 0
    fi
    # Closing needs neither the workflow stage nor the effective root (line 1 of the
    # window file), so it runs before both gates (R3e): a window never outlives its
    # command across a retreat, a stage change or a failed root lookup.
    if [ "$HOOK_EVENT" = "PostToolUse" ]; then
      ic_drift_close_window "$SESSION_TAG" "$LINEAGE" "$WINDOW_KEY"
      log_hook "EXECUTION-DRIFT-CHECK" "Window" "closed ${LINEAGE}/${WINDOW_KEY}"
      exit 0
    fi
    # This lineage's windows still open here belong to its earlier commands that had
    # no PostToolUse (denied, or a failed Claude Bash). Other lineages are untouched.
    ic_drift_close_lineage "$SESSION_TAG" "$LINEAGE"
    ;;
  *state_manager__*|*state-manager__*) SHORT_TOOL="${TOOL_NAME##*__}" ;;
  *)
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "not a drift tool: ${TOOL_NAME:-none}"
    exit 0
    ;;
esac

WORKFLOW=$(_drift_sql "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
if [ "$WORKFLOW" != "executing" ] && [ "$WORKFLOW" != "reviewing" ]; then
  log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "workflow_stage=${WORKFLOW:-unreadable}"
  exit 0
fi

case "$SHORT_TOOL" in
  shell|submit_task) ;;
  *)
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "unhandled state-manager tool: ${SHORT_TOOL}"
    exit 0
    ;;
esac

if ! _drift_effective_root "$SESSION_TAG" "$EVENT_CWD"; then
  log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: fail-open — ${_DRIFT_ROOT_ERROR}"
  exit 0
fi

case "$SHORT_TOOL" in
  shell)
    if [ -z "$(_drift_in_progress_rows "$SESSION_TAG")" ]; then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "no in-progress task"
      exit 0
    fi
    SHELL_CMD=$(printf '%s' "$INPUT" | jq -r '(.tool_input.command // .tool_input.cmd // "") | if type == "array" then join(" ") else tostring end' 2>/dev/null || true)
    if type workspace_input_is_readonly >/dev/null 2>&1 \
       && workspace_input_is_readonly command "$TOOL_NAME" "$SHELL_CMD"; then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "read-only command"
      exit 0
    fi
    if ic_drift_open_window "$SESSION_TAG" "$LINEAGE" "$WINDOW_KEY" "$_DRIFT_ROOT"; then
      log_hook "EXECUTION-DRIFT-CHECK" "Window" "opened ${LINEAGE}/${WINDOW_KEY}, root ${_DRIFT_ROOT}"
    else
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "window ${LINEAGE}/${WINDOW_KEY} not opened"
    fi
    exit 0
    ;;
  submit_task)
    ic_drift_settle_windows "$SESSION_TAG"
    if ! ROW_ID=$(_drift_row_id "$SESSION_TAG" "$TASK_ID"); then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: no wave_tasks row in the current wave"
      exit 0
    fi
    if DRIFT=$(ic_drift_violations "$SESSION_TAG" "$_DRIFT_ROOT" "$ROW_ID"); then :
    else
      DRIFT_STATUS=$?
      if [ "$DRIFT_STATUS" = "2" ]; then
        block_pretooluse "EXECUTION-DRIFT-CHECK" "$(ic_drift_unsupported_path_message)"
      fi
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID}: fail-open (no plan)"
      exit 0
    fi
    if [ -n "$DRIFT" ]; then
      block_pretooluse "EXECUTION-DRIFT-CHECK" "$(ic_drift_block_message "$DRIFT")"
    fi
    log_hook "EXECUTION-DRIFT-CHECK" "Allowed" "task ${TASK_ID}: no drift"
    exit 0
    ;;
esac
