#!/bin/bash
# Provider-neutral managed-worktree input rewriting for professional-mode-guard.
# Sourced after INPUT and SESSION_TAG are initialized.

WORKSPACE_UPDATED_INPUT=""
WORKSPACE_TARGET_FILES=""
WORKSPACE_EFFECTIVE_ROOT=""
WORKSPACE_GUARD_TOOL_NAME=""
WORKSPACE_GUARD_FILE_PATH=""
WORKSPACE_ADAPTER_ERROR=""
WORKSPACE_REWRITTEN_PATCH=""
WORKSPACE_READONLY_INPUT=""
WORKSPACE_PRIMARY_ROOT=""

workspace_adapter_fail() {
  WORKSPACE_ADAPTER_ERROR="$1"
  return 1
}

# Managed worktrees isolate MUTATIONS. A read observes bytes it could equally
# reach through Grep, Glob, or `cat`, so refusing one buys no isolation while
# costing the session every diagnostic it needs to repair a broken assignment.
# Reads therefore never require an assignment: when the binding is missing or
# unusable they pass through unrewritten instead of blocking.
workspace_input_is_readonly() {
  local tool_class="$1" original_tool="$2" value="$3"
  case "$original_tool" in Read) return 0 ;; esac
  if [ "$tool_class" = 'command' ] \
      && { is_readonly_git "$value" || is_readonly_research_bash "$value"; } \
      && ! workspace_command_writes_despite_readonly "$value"; then
    return 0
  fi
  return 1
}

# The shared read-only predicates were written to decide "may this run at a
# non-executing stage", and they admit a few forms that still write a file or
# execute a program: `git diff --output=<path>` creates that path, and rg's
# `--pre`/`--pre-glob`/`--hostname-bin` run an arbitrary binary. Promoting those
# predicates into the mutation-vs-read discriminator for worktree isolation
# makes those forms an escape, so they are excluded here rather than loosening
# the shared predicates other call sites depend on.
workspace_command_writes_despite_readonly() {
  printf '%s' "$1" | grep -qE '(^|[[:space:]])(--output(=|[[:space:]])|--pre(=|[[:space:]])|--pre-glob(=|[[:space:]])|--hostname-bin(=|[[:space:]]))'
}

# Targets that stay writable outside the effective root. Only the auto-memory
# tree qualifies: professional-mode-guard.sh has always allowed those writes at
# any workflow stage, and redirecting them into the worktree would silently
# strand memory the operator expects in $HOME.
workspace_file_target_is_exempt() {
  local candidate="$1" projects_root rest project remainder
  case "$candidate" in /*) ;; *) return 1 ;; esac
  projects_root="$HOME/.claude/projects"
  case "$candidate" in
    "$projects_root"/*) rest="${candidate#"$projects_root"/}" ;;
    *) return 1 ;;
  esac
  # `*` in a case pattern spans `/`, so `projects/*/memory/*` also matched
  # arbitrarily deep paths. Split the segments explicitly instead.
  project="${rest%%/*}"
  case "$project" in ''|.|..) return 1 ;; esac
  [ "$project" != "$rest" ] || return 1
  remainder="${rest#"$project"/}"
  case "$remainder" in memory/?*) ;; *) return 1 ;; esac
  # Do NOT canonicalize with `realpath -m` — BSD realpath has no -m, so the
  # fallback returned the raw candidate and no symlink was ever resolved. A
  # symlink planted at .../memory/out then redirected writes anywhere on disk.
  # workspace_resolve_target rejects traversal and any symlinked component.
  [ -d "$projects_root" ] || return 1
  [ "$(workspace_canonical_dir "$projects_root")" = "$projects_root" ] || return 1
  workspace_resolve_target "$rest" "$projects_root" >/dev/null || return 1
  return 0
}

# A managed-worktree session's OWN plan/review artifacts live under the PRIMARY
# checkout's docs/plans and docs/reviews. Those trees are gitignored, so
# redirecting the writes into the worktree overlay strands artifacts the operator
# and the workflow expect in the primary tree — the escape refusal used to force
# the human-only /use-primary-checkout. Exempt ONLY those two directories, ONLY
# when git confirms the exact target is gitignored, and ONLY after the memory
# carve-out's physical traversal/symlink guards resolve the path. This is NOT a
# general primary-write escape: a TRACKED file (git check-ignore exit 1), any
# path outside docs/plans and docs/reviews, and any git error all FAIL CLOSED.
workspace_file_target_is_own_artifact() {
  local candidate="$1" primary="$2" rest first remainder second base
  case "$candidate" in /*) ;; *) return 1 ;; esac
  [ -n "$primary" ] || return 1
  case "$candidate" in
    "$primary"/*) rest="${candidate#"$primary"/}" ;;
    *) return 1 ;;
  esac
  # `*` in a case pattern spans `/`, so split the segments explicitly instead of
  # matching `docs/plans/*` — that would also admit `docsX/plans/...` shapes.
  first="${rest%%/*}"
  case "$first" in docs) ;; *) return 1 ;; esac
  [ "$first" != "$rest" ] || return 1
  remainder="${rest#"$first"/}"
  second="${remainder%%/*}"
  case "$second" in plans|reviews) ;; *) return 1 ;; esac
  [ "$second" != "$remainder" ] || return 1
  # A real file component must follow docs/{plans,reviews}/ — not a bare directory.
  base="${remainder#"$second"/}"
  case "$base" in ''|*/) return 1 ;; esac
  # Resolve physically against the primary root: reject `..` traversal and any
  # symlinked component, mirroring workspace_file_target_is_exempt. Runs BEFORE
  # check-ignore so git never sees a traversal or a symlink-redirected path.
  [ -d "$primary" ] || return 1
  [ "$(workspace_canonical_dir "$primary")" = "$primary" ] || return 1
  workspace_resolve_target "$rest" "$primary" >/dev/null || return 1
  # FAIL CLOSED: only a path git confirms is gitignored is exempt. A tracked file
  # (exit 1) or any git error (exit >1) stays refused. `-q` suppresses stdout and
  # 2>/dev/null suppresses stderr so nothing leaks into the hook's JSON output.
  git -C "$primary" check-ignore -q -- "$rest" 2>/dev/null || return 1
  return 0
}

workspace_sql_quote() {
  printf "%s" "$1" | sed "s/'/''/g"
}

workspace_canonical_dir() {
  [ -d "$1" ] && [ ! -L "$1" ] || return 1
  (cd -- "$1" 2>/dev/null && pwd -P)
}

workspace_resolve_target() {
  local candidate="$1" root="$2" relative current component old_ifs
  [ -n "$candidate" ] || return 1
  case "$candidate" in *$'\n'*|*$'\r'*|*$'\t'*) return 1 ;; esac
  case "$candidate" in
    "$root") return 1 ;;
    "$root"/*) relative="${candidate#"$root"/}" ;;
    /*) return 1 ;;
    ./*) relative="${candidate#./}" ;;
    *) relative="$candidate" ;;
  esac
  [ -n "$relative" ] || return 1
  case "/$relative/" in *'/../'*|*'/./'*|*'//'*) return 1 ;; esac

  current="$root"
  old_ifs="$IFS"
  IFS='/'
  for component in $relative; do
    IFS="$old_ifs"
    [ -n "$component" ] && [ "$component" != "." ] && [ "$component" != ".." ] || return 1
    current="$current/$component"
    [ ! -L "$current" ] || return 1
    if [ -e "$current" ] && [ "$current" != "$root/$relative" ]; then
      [ -d "$current" ] || return 1
      [ "$(workspace_canonical_dir "$current")" = "$current" ] || return 1
    fi
    IFS='/'
  done
  IFS="$old_ifs"
  printf '%s' "$root/$relative"
}

workspace_rewrite_patch() {
  local patch="$1" root="$2" counts line prefix target rewritten="" resolved
  printf '%s' "$patch" | jq -Rs -e '
    explode | all(. == 9 or . == 10 or . == 13 or (. >= 32 and . != 127))
  ' >/dev/null 2>&1 || return 1
  counts=$(printf '%s\n' "$patch" | awk '
    $0 == "*** Begin Patch" { begin++ }
    $0 == "*** End Patch" { end++ }
    $0 ~ /^\*\*\* (Add|Update|Delete) File: / { operation++ }
    $0 ~ /^\*\*\* Move to: / { move++ }
    END { printf "%d %d %d %d", begin, end, operation, move }
  ') || return 1
  set -- $counts
  [ "$1" -eq 1 ] && [ "$2" -eq 1 ] && [ "$3" -ge 1 ] || return 1
  [ "$4" -le "$3" ] || return 1
  [[ "$patch" == '*** Begin Patch'$'\n'* ]] || return 1
  [[ "$patch" == *$'\n''*** End Patch' || "$patch" == *$'\n''*** End Patch'$'\n' ]] || return 1

  WORKSPACE_TARGET_FILES=""
  while IFS= read -r line || [ -n "$line" ]; do
    prefix=""
    case "$line" in
      '*** Add File: '*) prefix='*** Add File: '; target="${line#'*** Add File: '}" ;;
      '*** Update File: '*) prefix='*** Update File: '; target="${line#'*** Update File: '}" ;;
      '*** Delete File: '*) prefix='*** Delete File: '; target="${line#'*** Delete File: '}" ;;
      '*** Move to: '*) prefix='*** Move to: '; target="${line#'*** Move to: '}" ;;
    esac
    if [ -n "$prefix" ]; then
      resolved=$(workspace_resolve_target "$target" "$root") || return 1
      line="$prefix$resolved"
      if [ -z "$WORKSPACE_TARGET_FILES" ]; then
        WORKSPACE_TARGET_FILES="$resolved"
      else
        WORKSPACE_TARGET_FILES="$WORKSPACE_TARGET_FILES
$resolved"
      fi
    fi
    if [ -z "$rewritten" ]; then rewritten="$line"; else rewritten="$rewritten
$line"; fi
  done <<< "$patch"
  [ -n "$WORKSPACE_TARGET_FILES" ] || return 1
  WORKSPACE_REWRITTEN_PATCH="$rewritten"
}

workspace_command_has_explicit_checkout_escape() {
  local command="$1" root="$2" primary="$3" workspace_db="$4" other others without_root before after boundary
  local scrubbed q acc seg tail rest bchar achar achar_src word_ok before_ok after_ok
  # This is an explicit-path guard, not a process sandbox. Commands still start
  # in the managed root, while literal parent components and other checkout
  # paths are rejected. Arbitrary child processes remain ordinary shell code.
  if printf '%s' "$command" | grep -qE "(^|[[:space:]/=:;|&\"'])(\.\.)([/[:space:];|&\"']|$)"; then
    return 0
  fi
  # Home-relative paths reach the primary checkout without ever containing a
  # literal root byte: the `cd -- '<managed>' &&` prefix is prepended, then the
  # shell expands `~` / $HOME afterwards and the write lands outside the
  # worktree. This is not an adversarial form — it is what a model writes
  # naturally — so it is refused rather than rewritten.
  if printf '%s' "$command" | grep -qE '(^|[[:space:]=:;|&"'"'"'(])~($|[/[:space:]])'; then
    return 0
  fi
  case "$command" in
    *'$HOME'*|*'${HOME}'*) return 0 ;;
  esac
  # A literal-path check cannot see a path the shell COMPUTES. `cd "$(git
  # worktree list … | head -1 | cut -d' ' -f2)"`, `$(dirname "$(dirname "$PWD")")`
  # and `${PWD%/.ironclaude/*}` each reach the primary checkout without naming
  # it, and all three were observed writing there. A mutating command has no
  # need to derive its own checkout root — it already starts in the managed one
  # via the injected `cd` — so substitution and PWD-surgery are refused here.
  #
  # NOT read-only-exempt: with a healthy assignment this runs for every command,
  # so `cat '<primary>/file'` is refused too. That is intended — a read that
  # explicitly names the primary checkout is what the guard message says it
  # rejects — but do not read the surrounding code as though reads bypass this,
  # because a future carve-out here would open the escape it closes.
  case "$command" in
    *'$('*|*'`'*|*'${PWD'*|*'$PWD'*|*'$OLDPWD'*|*'${OLDPWD'*) return 0 ;;
  esac
  # `cd -` needs no marker at all. The adapter emits `cd -- '<managed>' && <cmd>`,
  # so the injected cd sets OLDPWD to the session's previous directory — the
  # PRIMARY checkout — and `cd -` walks straight back to it. Observed executing
  # `rm -rf src` against the primary with the guard returning 0.
  if printf '%s' "$command" | grep -qE '(^|[[:space:];|&(])cd[[:space:]]+-([[:space:]]|;|$)'; then
    return 0
  fi
  # Canonical descendants are unambiguous because the root is followed by a
  # slash. Remove those first. Any remaining occurrence can only be an exact
  # root token when it has unquoted shell-token boundaries; quote/colon/suffix
  # concatenation remains visible so the primary authority check rejects it.
  # The pattern MUST come from an unquoted variable. bash 3.2 — the stock shell
  # on macOS, which hooks.json invokes as plain `bash` — treats quotes embedded
  # in a ${var//pat/repl} pattern as literal characters, so `${command//"$root/"/}`
  # searched for a path wrapped in quote marks, never matched, and the escape
  # check silently failed OPEN on every machine without a newer bash.
  # A COMPLETE quote-wrapped owned-worktree root word — `'<root>'`, `"<root>"`,
  # `'<root>/<desc>'`, `"<root>/<desc>"` — resolves INSIDE this assignment, but
  # the boundary loop below rejects it: the surrounding quote byte is not one of
  # the token-boundary bytes it recognises, so `return 0` fires on the quote.
  # `git -C '<root>' …` is what a model naturally writes. Pre-strip such a word
  # before the loop, but ONLY when the opening quote starts the string or follows
  # one of the loop's before-boundary bytes (:218-219) AND the closing quote ends
  # the string or is followed by one of its after-boundary bytes (:225-226). That
  # keeps the :202-206 concatenation-visibility contract intact: `'<root>'x`,
  # `x'<root>'`, `'<root>x'`, `"<root>"-y` are NOT complete owned words, are left
  # visible, and the loop/primary check below still rejects them. The stripped
  # word always resolves under the root, so nothing new escapes. `..` / `~` /
  # `$HOME` / `$(` / `cd -` were already refused above, so they never reach here.
  # bash 3.2: quoted `"$q$root"` in `%%`/`#` matches those bytes literally, which
  # is the intent (verified on 3.2.57), matching the :214-215 convention.
  scrubbed="$command"
  for q in "'" '"'; do
    acc=''
    rest="$scrubbed"
    while [ -n "$rest" ]; do
      seg="${rest%%"$q$root"*}"
      if [ "$seg" = "$rest" ]; then acc="$acc$rest"; rest=''; break; fi
      tail="${rest#*"$q$root"}"
      word_ok=0
      if [ "${tail:0:1}" = "$q" ]; then
        achar_src="${tail#"$q"}"; word_ok=1
      elif [ "${tail:0:1}" = '/' ] && [[ "$tail" == *"$q"* ]]; then
        achar_src="${tail#*"$q"}"; word_ok=1
      fi
      if [ "$word_ok" = 1 ]; then
        before_ok=0; after_ok=0
        if [ -z "$seg" ]; then
          before_ok=1
        else
          bchar="${seg: -1}"
          case "$bchar" in ' '|$'\t'|$'\n'|'='|';'|'|'|'&'|'('|'>'|'<') before_ok=1 ;; esac
        fi
        if [ -z "$achar_src" ]; then
          after_ok=1
        else
          achar="${achar_src:0:1}"
          case "$achar" in ' '|$'\t'|$'\n'|';'|'|'|'&'|')'|'>'|'<') after_ok=1 ;; esac
        fi
        if [ "$before_ok" = 1 ] && [ "$after_ok" = 1 ]; then
          acc="$acc$seg"; rest="$achar_src"; continue
        fi
      fi
      acc="$acc$seg$q$root"; rest="$tail"
    done
    scrubbed="$acc"
  done
  local _root_prefix="$root/"
  without_root="${scrubbed//$_root_prefix/}"
  while [[ "$without_root" == *"$root"* ]]; do
    before="${without_root%%"$root"*}"
    after="${without_root#*"$root"}"
    if [ -n "$before" ]; then
      boundary="${before: -1}"
      case "$boundary" in
        ' '|$'\t'|$'\n'|'='|';'|'|'|'&'|'('|">"|"<") ;;
        *) return 0 ;;
      esac
    fi
    if [ -n "$after" ]; then
      boundary="${after:0:1}"
      case "$boundary" in
        ' '|$'\t'|$'\n'|';'|'|'|'&'|')'|'>'|'<') ;;
        *) return 0 ;;
      esac
    fi
    without_root="$before$after"
  done
  if [ "$root" != "$primary" ] && [[ "$without_root" == *"$primary"* ]]; then return 0; fi
  others=$(sqlite3 "$workspace_db" ".timeout 10000" \
    "SELECT worktree_path FROM assignments WHERE lifecycle_status NOT IN ('integrated','abandoned','cleaned');" 2>/dev/null) || return 0
  while IFS= read -r other; do
    [ -z "$other" ] && continue
    [ "$other" = "$root" ] && continue
    [[ "$command" != *"$other"* ]] || return 0
  done <<< "$others"
  return 1
}

# Resolves the durable assignment into WORKSPACE_EFFECTIVE_ROOT, proving the
# recorded worktree still exists, is canonical, belongs to this repository, and
# sits on the recorded branch. Extracted so every one of these evidence failures
# shares a single mutation-only block decision in the caller.
workspace_bind_effective_root() {
  local workspace_db="$1" repository_identity="$2" event_cwd="$3" safe_session="$4"
  local row guid recorded_root branch status primary actual_root actual_common actual_branch
  local owner_row owner_guid owner_session effective_root

  row=$(sqlite3 -tabs "$workspace_db" ".timeout 10000" \
    "SELECT workspace_guid,worktree_path,branch,lifecycle_status FROM assignments WHERE repository_identity='$(workspace_sql_quote "$repository_identity")' AND owner_session_id='$safe_session' AND lifecycle_status NOT IN ('integrated','abandoned','cleaned');" 2>/dev/null) || \
    { workspace_adapter_fail 'Cannot read managed-worktree assignment evidence'; return 1; }
  IFS=$'\t' read -r guid recorded_root branch status <<< "$row"
  [ -n "$guid" ] && [ -n "$recorded_root" ] && [ -n "$branch" ] || { workspace_adapter_fail 'Managed-worktree assignment evidence is incomplete'; return 1; }
  [ "$status" = 'active' ] || { workspace_adapter_fail "Managed-worktree assignment is frozen in lifecycle state $status"; return 1; }

  primary=$(git -C "$event_cwd" worktree list --porcelain 2>/dev/null | awk '/^worktree / { sub(/^worktree /, ""); print; exit }') || \
    { workspace_adapter_fail 'Cannot resolve primary checkout'; return 1; }
  primary=$(workspace_canonical_dir "$primary") || { workspace_adapter_fail 'Primary checkout is not canonical'; return 1; }
  # The command branch compares against the primary checkout, so it must outlive
  # this function's locals.
  WORKSPACE_PRIMARY_ROOT="$primary"
  actual_root=$(workspace_canonical_dir "$recorded_root") || { workspace_adapter_fail 'Recorded managed worktree is unavailable or symlinked'; return 1; }
  [ "$actual_root" = "$recorded_root" ] || { workspace_adapter_fail 'Recorded managed-worktree path is not canonical'; return 1; }
  actual_common=$(git -C "$actual_root" rev-parse --git-common-dir 2>/dev/null) || { workspace_adapter_fail 'Recorded managed worktree is not a Git checkout'; return 1; }
  case "$actual_common" in /*) ;; *) actual_common="$actual_root/$actual_common" ;; esac
  actual_common=$(workspace_canonical_dir "$actual_common") || { workspace_adapter_fail 'Recorded managed worktree common directory is unavailable'; return 1; }
  [ "$actual_common" = "$repository_identity" ] || { workspace_adapter_fail 'Recorded worktree belongs to another repository'; return 1; }
  actual_branch=$(git -C "$actual_root" symbolic-ref -q HEAD 2>/dev/null) || { workspace_adapter_fail 'Recorded managed worktree is detached'; return 1; }
  [ "$actual_branch" = "refs/heads/$branch" ] || { workspace_adapter_fail 'Recorded managed-worktree branch does not match durable assignment'; return 1; }

  owner_row=$(sqlite3 -tabs "$workspace_db" ".timeout 10000" \
    "SELECT workspace_guid,owner_session_id FROM primary_checkout_owners WHERE repository_identity='$(workspace_sql_quote "$repository_identity")';" 2>/dev/null) || \
    { workspace_adapter_fail 'Cannot read primary-checkout authority'; return 1; }
  effective_root="$actual_root"
  if [ -n "$owner_row" ]; then
    IFS=$'\t' read -r owner_guid owner_session <<< "$owner_row"
    if [ "$owner_guid" = "$guid" ] && [ "$owner_session" = "$SESSION_TAG" ]; then
      effective_root="$primary"
      # HEARTBEAT: the reap TTL below is fixed, so a LIVE owner must renew its
      # own row on every confirmed file operation or an idle-vs-live owner look
      # identical to the reaper. Scoped to this exact owner row; best-effort so
      # a renewal failure never alters or fails the file-operation decision.
      # Observability only, protocol-clean: this hook's stdout is the PreToolUse
      # JSON decision channel, so the log goes to an append-only sideband file
      # (never stdout, and never stderr either — some callers capture stderr
      # alongside stdout when validating the decision, so even stderr is not
      # safely inert here). Best-effort: a log write never alters or fails the
      # file-operation decision.
      if sqlite3 "$workspace_db" ".timeout 10000" "UPDATE primary_checkout_owners SET acquired_at = datetime('now') WHERE repository_identity='$(workspace_sql_quote "$repository_identity")' AND workspace_guid='$(workspace_sql_quote "$owner_guid")' AND owner_session_id='$(workspace_sql_quote "$owner_session")';" 2>/dev/null; then
        printf 'workspace-path-adapter: heartbeat renewed owner_session=%s\n' "$owner_session" \
          >> "$HOME/.claude/ironclaude-worktree-heartbeat.log" 2>/dev/null || :
      fi
    fi
  fi
  WORKSPACE_EFFECTIVE_ROOT="$effective_root"
}

workspace_prepare_input() {
  local original_tool tool key event_cwd top common repository_identity workspace_db
  local safe_session row_count effective_root value rewritten quoted_root updated

  original_tool=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null) || {
    workspace_adapter_fail 'Malformed hook JSON payload'; return 1;
  }
  case "$original_tool" in
    Read|Edit|Write|MultiEdit) key='file_path'; tool='file' ;;
    NotebookEdit) key='notebook_path'; tool='file' ;;
    Bash) key='command'; tool='command' ;;
    exec_command) key='cmd'; tool='command' ;;
    apply_patch) key='command'; tool='patch' ;;
    *) return 0 ;;
  esac
  printf '%s' "$INPUT" | jq -e --arg key "$key" \
    '(.tool_input | type) == "object" and (.tool_input[$key] | type) == "string"' \
    >/dev/null 2>&1 || { workspace_adapter_fail "Malformed $original_tool tool_input"; return 1; }
  if [ "$original_tool" = 'apply_patch' ]; then
    printf '%s' "$INPUT" | jq -e '(.tool_input | keys) == ["command"]' >/dev/null 2>&1 || \
      { workspace_adapter_fail 'Native Codex apply_patch requires command-only tool_input'; return 1; }
  fi
  value=$(printf '%s' "$INPUT" | jq -r --arg key "$key" '.tool_input[$key]' 2>/dev/null) || {
    workspace_adapter_fail 'Cannot decode tool input'; return 1;
  }

  # Classified before any repository resolution so that every failure below can
  # fall through to passthrough for a read rather than stranding the session.
  WORKSPACE_READONLY_INPUT=''
  if workspace_input_is_readonly "$tool" "$original_tool" "$value"; then
    WORKSPACE_READONLY_INPUT='1'
  fi

  event_cwd=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
  [ -n "$event_cwd" ] || event_cwd=$(pwd -P)
  event_cwd=$(workspace_canonical_dir "$event_cwd") || {
    [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
    workspace_adapter_fail 'Hook cwd is not a canonical directory'; return 1;
  }
  top=$(git -C "$event_cwd" rev-parse --show-toplevel 2>/dev/null) || return 0
  top=$(workspace_canonical_dir "$top") || {
    [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
    workspace_adapter_fail 'Git worktree root is not canonical'; return 1;
  }
  common=$(git -C "$event_cwd" rev-parse --git-common-dir 2>/dev/null) || {
    [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
    workspace_adapter_fail 'Cannot resolve Git common directory'; return 1;
  }
  # `--git-common-dir` is reported relative to the CWD git ran in, not to the
  # worktree top. Resolving it against $top turned `../../.git` from a
  # subdirectory into a sibling path that does not exist, which blocked every
  # tool for any session not sitting exactly at the repository root.
  case "$common" in /*) ;; *) common="$event_cwd/$common" ;; esac
  repository_identity=$(workspace_canonical_dir "$common") || {
    [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
    workspace_adapter_fail 'Git common directory is not canonical'; return 1;
  }

  # MANAGED WORKTREES ARE OPT-IN. A session works in the primary checkout until
  # a human runs /use-managed-worktree, so "no assignment" is the NORMAL state
  # and must pass through untouched — not fail closed.
  #
  # The safety property that fail-closed provided still holds, because it keys on
  # row EXISTENCE rather than treating absence as failure:
  #   no row      -> never opted in            -> allow, no rewrite
  #   one row     -> opted in                  -> bind (damaged binding still blocks)
  #   many rows   -> ambiguous                 -> block
  # An absent database is the same case as no row: nobody ever opted in.
  workspace_db="${WORKSPACE_MANAGER_DB_PATH:-$HOME/.claude/ironclaude-workspaces.db}"
  [ -f "$workspace_db" ] || return 0
  safe_session=$(workspace_sql_quote "$SESSION_TAG")
  row_count=$(sqlite3 "$workspace_db" ".timeout 10000" \
    "SELECT COUNT(*) FROM assignments WHERE repository_identity='$(workspace_sql_quote "$repository_identity")' AND owner_session_id='$safe_session' AND lifecycle_status NOT IN ('integrated','abandoned','cleaned');" 2>/dev/null) || \
    { [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
      workspace_adapter_fail 'Cannot read managed-worktree assignment'; return 1; }
  # Zero is the opt-out default. Anything above one is ambiguous and still fails
  # closed, even though the unique partial index makes it unreachable in practice.
  [ "$row_count" = '0' ] && return 0
  if [ "$row_count" != '1' ]; then
    [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
    workspace_adapter_fail 'Ambiguous managed-worktree assignment for this session'; return 1
  fi
  # Any failure to resolve usable binding evidence is a mutation-only block: a
  # read falls through to passthrough rather than stranding the session.
  if ! workspace_bind_effective_root "$workspace_db" "$repository_identity" "$event_cwd" "$safe_session"; then
    [ "$WORKSPACE_READONLY_INPUT" = '1' ] && return 0
    # A frozen or otherwise unbindable assignment still must not strand an
    # auto-memory write: professional-mode-guard.sh's memory allowance
    # (:587-593) has always applied at any workflow stage. Pass the input
    # through UNREWRITTEN, exactly like the read-only fallback above, reusing
    # workspace_file_target_is_exempt's own symlink/traversal guard unchanged
    # so a source-file Write (which fails that predicate) still falls through
    # to `return 1` below.
    if [ "$tool" = 'file' ] && workspace_file_target_is_exempt "$value"; then
      return 0
    fi
    return 1
  fi
  effective_root="$WORKSPACE_EFFECTIVE_ROOT"

  case "$tool" in
    file)
      if ! rewritten=$(workspace_resolve_target "$value" "$effective_root"); then
        # A read of an out-of-root absolute path reaches bytes that Grep, Glob
        # and `cat` already reach, so refusing it isolates nothing. Auto-memory
        # writes are the one carve-out professional-mode-guard has always made
        # at any workflow stage. Both pass through unrewritten.
        if [ "$WORKSPACE_READONLY_INPUT" = '1' ] || workspace_file_target_is_exempt "$value" \
            || workspace_file_target_is_own_artifact "$value" "$WORKSPACE_PRIMARY_ROOT"; then
          return 0
        fi
        workspace_adapter_fail 'File target escapes or traverses the effective checkout root'; return 1
      fi
      WORKSPACE_TARGET_FILES="$rewritten"
      WORKSPACE_GUARD_FILE_PATH="$rewritten"
      updated=$(printf '%s' "$INPUT" | jq -c --arg key "$key" --arg value "$rewritten" '.tool_input[$key] = $value') || \
        { workspace_adapter_fail 'Cannot rewrite file tool input'; return 1; }
      ;;
    command)
      if workspace_command_has_explicit_checkout_escape "$value" "$effective_root" "$WORKSPACE_PRIMARY_ROOT" "$workspace_db"; then
        workspace_adapter_fail 'Command explicitly references a parent or another checkout'; return 1
      fi
      quoted_root=${effective_root//\'/\'\\\'\'}
      rewritten="cd -- '$quoted_root' && $value"
      WORKSPACE_GUARD_TOOL_NAME='Bash'
      # Existing workflow policy must inspect operator/model input, not the
      # adapter-injected `cd ... &&` prefix returned to the provider.
      WORKSPACE_GUARD_FILE_PATH="$value"
      updated=$(printf '%s' "$INPUT" | jq -c --arg key "$key" --arg value "$rewritten" '.tool_input[$key] = $value') || \
        { workspace_adapter_fail 'Cannot rewrite command tool input'; return 1; }
      ;;
    patch)
      workspace_rewrite_patch "$value" "$effective_root" || { workspace_adapter_fail 'Codex patch is malformed or contains an escaping target'; return 1; }
      rewritten="$WORKSPACE_REWRITTEN_PATCH"
      WORKSPACE_GUARD_TOOL_NAME='MultiEdit'
      WORKSPACE_GUARD_FILE_PATH=$(printf '%s\n' "$WORKSPACE_TARGET_FILES" | sed -n '1p')
      updated=$(printf '%s' "$INPUT" | jq -c --arg value "$rewritten" '.tool_input.command = $value') || \
        { workspace_adapter_fail 'Cannot rewrite Codex patch input'; return 1; }
      ;;
  esac
  INPUT="$updated"
  WORKSPACE_UPDATED_INPUT=$(printf '%s' "$INPUT" | jq -c '.tool_input') || { workspace_adapter_fail 'Cannot encode rewritten tool input'; return 1; }
}

workspace_allow() {
  if [ -n "$WORKSPACE_UPDATED_INPUT" ]; then
    jq -cn --argjson updated "$WORKSPACE_UPDATED_INPUT" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:$updated}}'
  fi
  exit 0
}
