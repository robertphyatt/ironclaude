#!/bin/bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="$ROOT_DIR/hooks/professional-mode-guard.sh"
HOOKS_JSON="$ROOT_DIR/hooks/hooks.json"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$label"; else fail "$label" "expected=$expected actual=$actual"; fi
}
assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  if [[ "$haystack" == *"$needle"* ]]; then pass "$label"; else fail "$label" "missing=$needle"; fi
}

TEST_ROOT=$(mktemp -d)
TEST_ROOT=$(cd "$TEST_ROOT" && pwd -P)
trap 'rm -rf "$TEST_ROOT"' EXIT
TEST_HOME="$TEST_ROOT/home"
PRIMARY="$TEST_ROOT/repository"
NON_GIT="$TEST_ROOT/non-git"
SESSION="019fc5e5-fc72-7493-b785-bee8cda62b1b"
OTHER_SESSION="029fc5e5-fc72-7493-b785-bee8cda62b1b"
GUID="$SESSION"
OTHER_GUID="$OTHER_SESSION"
WORKSPACE_DB="$TEST_HOME/.claude/ironclaude-workspaces.db"
STATE_DB="$TEST_HOME/.claude/ironclaude.db"

mkdir -p "$TEST_HOME/.claude" "$PRIMARY/src" "$NON_GIT"
printf '{"verbose_hook_logs":false}\n' > "$TEST_HOME/.claude/ironclaude-hooks-config.json"
git -C "$PRIMARY" init -q
git -C "$PRIMARY" config user.email test@example.com
git -C "$PRIMARY" config user.name Test
printf 'primary\n' > "$PRIMARY/src/existing.txt"
git -C "$PRIMARY" add src/existing.txt
git -C "$PRIMARY" commit -qm initial
BASE=$(git -C "$PRIMARY" rev-parse HEAD)
REPOSITORY_IDENTITY=$(cd "$PRIMARY/.git" && pwd -P)
MANAGED="$PRIMARY/.ironclaude/worktrees/$GUID"
OTHER_MANAGED="$PRIMARY/.ironclaude/worktrees/$OTHER_GUID"
mkdir -p "$PRIMARY/.ironclaude/worktrees"
git -C "$PRIMARY" worktree add -qb "ironclaude/$GUID" "$MANAGED" "$BASE"
git -C "$PRIMARY" worktree add -qb "ironclaude/$OTHER_GUID" "$OTHER_MANAGED" "$BASE"

sqlite3 "$STATE_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
  terminal_session TEXT PRIMARY KEY,
  professional_mode TEXT NOT NULL,
  workflow_stage TEXT NOT NULL,
  current_wave INTEGER DEFAULT 1,
  review_pending INTEGER DEFAULT 0,
  review_block_count INTEGER DEFAULT 0
);
CREATE TABLE wave_tasks (
  terminal_session TEXT NOT NULL,
  wave_number INTEGER NOT NULL,
  allowed_files TEXT,
  status TEXT
);
CREATE TABLE registered_designs (
  file_path TEXT,
  terminal_session TEXT,
  consumed INTEGER DEFAULT 0
);
INSERT INTO sessions VALUES ('$SESSION', 'on', 'executing', 1, 0, 0);
INSERT INTO wave_tasks VALUES (
  '$SESSION', 1,
  '["docs/notes.txt","src/claude.txt","src/codex-a.txt","src/codex-b.txt","src/command.txt","notes.txt"]',
  'in_progress'
);
SQL

sqlite3 "$WORKSPACE_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE assignments (
  workspace_guid TEXT PRIMARY KEY,
  repository_identity TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  branch TEXT NOT NULL,
  base_commit TEXT NOT NULL,
  current_head TEXT NOT NULL,
  owner_session_id TEXT,
  worker_id TEXT,
  lifecycle_status TEXT NOT NULL,
  integration_target TEXT NOT NULL,
  integrated_commit TEXT,
  recovery_ref TEXT,
  disposition TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE primary_checkout_owners (
  repository_identity TEXT PRIMARY KEY,
  workspace_guid TEXT NOT NULL,
  owner_session_id TEXT NOT NULL,
  acquired_at TEXT DEFAULT (datetime('now'))
);
INSERT INTO assignments (
  workspace_guid, repository_identity, worktree_path, branch, base_commit,
  current_head, owner_session_id, lifecycle_status, integration_target
) VALUES (
  '$GUID', '$REPOSITORY_IDENTITY', '$MANAGED', 'ironclaude/$GUID', '$BASE',
  '$BASE', '$SESSION', 'active', 'refs/heads/main'
);
INSERT INTO assignments (
  workspace_guid, repository_identity, worktree_path, branch, base_commit,
  current_head, owner_session_id, lifecycle_status, integration_target
) VALUES (
  '$OTHER_GUID', '$REPOSITORY_IDENTITY', '$OTHER_MANAGED', 'ironclaude/$OTHER_GUID', '$BASE',
  '$BASE', '$OTHER_SESSION', 'active', 'refs/heads/main'
);
SQL

# BASH_BIN pins the interpreter under test. Left as PATH `bash`, a contributor
# (or a runner image) with bash 5 silently loses the bash-3.2 coverage that the
# escape guard depends on — the fail-open regression reproduced only under 3.2.
BASH_BIN="${BASH_BIN:-bash}"

run_guard() {
  local payload="$1"
  local output status
  set +e
  output=$(printf '%s' "$payload" | HOME="$TEST_HOME" WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" "$BASH_BIN" "$GUARD" 2>&1)
  status=$?
  set -e
  printf '%s\n%s' "$status" "$output"
}

run_guard_db() {
  local db="$1" payload="$2"
  local output status
  set +e
  output=$(printf '%s' "$payload" | HOME="$TEST_HOME" WORKSPACE_MANAGER_DB_PATH="$db" "$BASH_BIN" "$GUARD" 2>&1)
  status=$?
  set -e
  printf '%s\n%s' "$status" "$output"
}

status_of() { printf '%s' "$1" | sed -n '1p'; }
output_of() { printf '%s' "$1" | sed '1d'; }
payload() {
  local tool="$1" tool_input="$2" cwd="${3:-$PRIMARY}"
  jq -cn --arg tool "$tool" --argjson input "$tool_input" --arg cwd "$cwd" --arg session "$SESSION" \
    '{tool_name:$tool,tool_input:$input,cwd:$cwd,session_id:$session}'
}
updated_input() {
  printf '%s' "$1" | jq -c '.hookSpecificOutput.updatedInput' 2>/dev/null
}

echo '=== matcher coverage ==='
MATCHER=$(jq -r '.hooks.PreToolUse[] | select(.hooks[].command | contains("professional-mode-guard.sh")) | .matcher' "$HOOKS_JSON")
assert_contains 'matcher includes native Codex apply_patch' "$MATCHER" 'apply_patch'
assert_contains 'matcher includes native Codex exec_command' "$MATCHER" 'exec_command'

echo '=== Claude path rewriting ==='
RESULT=$(run_guard "$(payload Read '{"file_path":"src/existing.txt"}')")
assert_eq 'relative Claude read allowed' '0' "$(status_of "$RESULT")"
assert_eq 'relative Claude read path rewritten to managed root' \
  "$MANAGED/src/existing.txt" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"managed\n"}')")
assert_eq 'relative Claude write allowed' '0' "$(status_of "$RESULT")"
assert_eq 'relative Claude path rewritten to managed root' \
  "$MANAGED/src/claude.txt" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"

RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/src/claude.txt" '{file_path:$p,content:"bad"}')")")
assert_eq 'absolute primary write denied without primary authority' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Write '{"file_path":"../escape.txt","content":"bad"}')")
assert_eq 'parent escape denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p $'src/bad\npath.txt' '{file_path:$p,content:"bad"}')")")
assert_eq 'control-character path denied' '2' "$(status_of "$RESULT")"
ln -s "$TEST_ROOT" "$MANAGED/src/outside-link"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/outside-link/escape.txt","content":"bad"}')")
assert_eq 'symlink escape denied' '2' "$(status_of "$RESULT")"
# Assert the REASON, not just the code. With the symlink defense removed this
# path is still refused — by the wave allowlist, for "FILE NOT IN PLAN" — so an
# exit-code-only assertion passes against a guard that no longer exists.
assert_contains 'symlink escape denied BY THE WORKTREE GUARD' \
  "$(output_of "$RESULT")" 'MANAGED WORKTREE ENFORCEMENT FAILED'
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$OTHER_MANAGED/src/claude.txt" '{file_path:$p,content:"bad"}')")")
assert_eq 'other assignment path denied' '2' "$(status_of "$RESULT")"
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET owner_session_id='unbound-for-test' WHERE workspace_guid='$GUID'"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"bad"}')")
assert_eq 'unassigned session writes to the primary checkout' '0' "$(status_of "$RESULT")"

# Managed worktrees are OPT-IN: a session works in the primary checkout until a
# human runs /use-managed-worktree. "No assignment" is therefore the NORMAL
# state and must pass through untouched — the property that still has to hold is
# that an assignment which EXISTS but is damaged still fails closed (below).
echo '=== unassigned session: primary checkout, nothing rewritten ==='
RESULT=$(run_guard "$(payload Read '{"file_path":"src/existing.txt"}')")
assert_eq 'unassigned session still allows Read' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Read "$(jq -cn --arg p "$PRIMARY/src/existing.txt" '{file_path:$p}')")")
assert_eq 'unassigned session still allows absolute Read' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"git log --oneline -1"}')")
assert_eq 'unassigned session still allows read-only git' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"ls src"}')")
assert_eq 'unassigned session still allows read-only research bash' '0' "$(status_of "$RESULT")"

# Mutations are allowed and, critically, NOT REWRITTEN — they land where the
# user is looking. An emitted updatedInput here would mean silent redirection.
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"ok"}')")
assert_eq 'unassigned Write allowed' '0' "$(status_of "$RESULT")"
assert_eq 'unassigned Write is not redirected' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
RESULT=$(run_guard "$(payload Bash '{"command":"printf ok > src/command.txt"}')")
assert_eq 'unassigned mutating Bash allowed' '0' "$(status_of "$RESULT")"
assert_eq 'unassigned Bash gets no cd prefix' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.command // empty' 2>/dev/null)"
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET owner_session_id='$SESSION' WHERE workspace_guid='$GUID'"

# `git rev-parse --git-common-dir` reports relative to the CWD git ran in. When
# that result was resolved against the worktree top instead, a cwd one level
# down turned `../.git` into a sibling path that does not exist and every tool
# was blocked. Any session not sitting exactly at the repository root hit this.
echo '=== subdirectory cwd ==='
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"managed"}' "$PRIMARY/src")")
assert_eq 'write from subdirectory cwd allowed' '0' "$(status_of "$RESULT")"
assert_eq 'write from subdirectory cwd rewritten to managed root' \
  "$MANAGED/src/claude.txt" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
RESULT=$(run_guard "$(payload Bash '{"command":"git status"}' "$PRIMARY/src")")
assert_eq 'read-only git from subdirectory cwd allowed' '0' "$(status_of "$RESULT")"

# Home-relative paths contain no literal root byte, so the escape check never
# saw them: the cd-prefix was prepended and the shell expanded ~/$HOME after,
# landing the write in the primary checkout.
echo '=== home-relative escapes ==='
RESULT=$(run_guard "$(payload Bash '{"command":"printf bad > ~/repository/src/x.txt"}')")
assert_eq 'tilde-relative write denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"printf bad > $HOME/repository/src/x.txt"}')")
assert_eq 'HOME-relative write denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"cd ~/repository && printf bad > src/x.txt"}')")
assert_eq 'tilde cd escape denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"git -C ~/repository add -A"}')")
assert_eq 'tilde git -C staging escape denied' '2' "$(status_of "$RESULT")"
# Negative: a managed-relative command with no home reference still works.
RESULT=$(run_guard "$(payload Bash '{"command":"printf ok > src/command.txt"}')")
assert_eq 'managed relative write still allowed' '0' "$(status_of "$RESULT")"

# A literal-path check cannot see a path the shell COMPUTES. Each of these was
# observed writing into the primary checkout while the guard allowed it.
echo '=== computed-path escapes ==='
for computed in \
  'cd "$(git worktree list --porcelain | head -1 | cut -d" " -f2)" && printf bad > PWNED.txt' \
  'cd "$(dirname "$(dirname "$(dirname "$PWD")")")" && printf bad > PWNED.txt' \
  'cd "${PWD%/.ironclaude/*}" && printf bad > PWNED.txt' \
  'printf bad > "$(git worktree list --porcelain | head -1 | cut -d" " -f2)/PWNED.txt"' \
  'git -C "$(git worktree list --porcelain | head -1 | cut -d" " -f2)" add -A'
do
  RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg c "$computed" '{command:$c}')")")
  assert_eq 'computed-path escape denied' '2' "$(status_of "$RESULT")"
done
# Negative: an ordinary managed-relative mutation is unaffected.
RESULT=$(run_guard "$(payload Bash '{"command":"printf ok > src/command.txt"}')")
assert_eq 'plain managed write unaffected by substitution rule' '0' "$(status_of "$RESULT")"

# hook-intent.js is the second executable entry bundle and is what MINTS
# direct-human Git authority. A matcher covering only cli.js left it reachable.
# The pre-adapter mappings ARE the Codex parity fix. Without these two cases
# nothing in the suite fails when either mapping is deleted, because the adapter
# renames the tools later and a different guard produces the same exit code.
echo '=== Codex native tools reach the pre-adapter gates ==='
CFG_PATCH='*** Begin Patch
*** Update File: '"$TEST_HOME"'/.claude/ironclaude-hooks-config.json
+{"tier_up_review_policy":"none"}
*** End Patch'
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg c "$CFG_PATCH" '{command:$c}')")")
assert_contains 'apply_patch cannot rewrite the guardrail config' \
  "$(output_of "$RESULT")" 'HUMAN-ONLY CONFIG'
RESULT=$(run_guard "$(payload exec_command "$(jq -cn --arg c "printf x > $TEST_HOME/.claude/ironclaude-hooks-config.json" '{cmd:$c}')")")
assert_contains 'exec_command cannot rewrite the guardrail config' \
  "$(output_of "$RESULT")" 'HUMAN-ONLY CONFIG'
RESULT=$(run_guard "$(payload exec_command '{"cmd":"node /x/mcp-servers/workspace-manager/dist/cli.js finalize {}"}')")
assert_contains 'exec_command cannot reach the private transport' \
  "$(output_of "$RESULT")" 'COMMANDER-ONLY WORKSPACE TRANSPORT'

echo '=== private entry bundles ==='
RESULT=$(run_guard "$(payload Bash '{"command":"node /installed/ironclaude/mcp-servers/workspace-manager/dist/hook-intent.js {}"}')")
assert_eq 'hook-intent.js transport denied' '2' "$(status_of "$RESULT")"
assert_contains 'hook-intent.js denial names commander-only transport' \
  "$(output_of "$RESULT")" 'COMMANDER-ONLY WORKSPACE TRANSPORT'

# `cd -` and $OLDPWD reach the primary WITHOUT naming it: the adapter's own
# injected `cd -- '<managed>' &&` is what sets OLDPWD to the primary checkout.
echo '=== OLDPWD escapes ==='
for oldpwd_form in \
  'cd - && printf bad > src/command.txt' \
  'cd "$OLDPWD" && printf bad > src/command.txt' \
  'cd ${OLDPWD} && printf bad > src/command.txt' \
  'printf bad > "$OLDPWD/src/command.txt"' \
  'git -C "$OLDPWD" add -A'
do
  RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg c "$oldpwd_form" '{command:$c}')")")
  assert_eq 'OLDPWD escape denied' '2' "$(status_of "$RESULT")"
done
# Negative: `cd -- <relative>` and a directory literally named "-x" are fine.
RESULT=$(run_guard "$(payload Bash '{"command":"cd src && printf ok > command.txt"}')")
assert_eq 'ordinary cd into a managed subdirectory still allowed' '0' "$(status_of "$RESULT")"

# A corrupt (non-SQLite) workspace DB exercises the sqlite READ-FAILURE branch,
# which the absent-DB case below never reaches.
echo '=== corrupt workspace database ==='
CORRUPT_DB="$TEST_HOME/.claude/corrupt-workspaces.db"
head -c 4096 /dev/urandom > "$CORRUPT_DB"
RESULT=$(run_guard_db "$CORRUPT_DB" "$(payload Read '{"file_path":"src/existing.txt"}')")
assert_eq 'corrupt workspace DB still allows Read' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard_db "$CORRUPT_DB" "$(payload Bash '{"command":"git log --oneline -1"}')")
assert_eq 'corrupt workspace DB still allows read-only git' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard_db "$CORRUPT_DB" "$(payload Write '{"file_path":"src/claude.txt","content":"bad"}')")
assert_eq 'corrupt workspace DB still denies Write' '2' "$(status_of "$RESULT")"

echo '=== absent workspace database ==='
ABSENT_DB="$TEST_HOME/.claude/absent-workspaces.db"
RESULT=$(run_guard_db "$ABSENT_DB" "$(payload Read '{"file_path":"src/existing.txt"}')")
assert_eq 'absent workspace DB still allows Read' '0' "$(status_of "$RESULT")"
# No database means nobody ever opted in — the primary checkout is correct.
RESULT=$(run_guard_db "$ABSENT_DB" "$(payload Write '{"file_path":"src/claude.txt","content":"ok"}')")
assert_eq 'absent workspace DB writes to the primary checkout' '0' "$(status_of "$RESULT")"

echo '=== out-of-root targets under a healthy assignment ==='
mkdir -p "$TEST_HOME/.claude/projects/demo/memory"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_HOME/.claude/projects/demo/memory/note.md" '{file_path:$p,content:"remembered"}')")")
assert_eq 'auto-memory write allowed outside the worktree' '0' "$(status_of "$RESULT")"
# Empty means the adapter emitted no rewrite at all. A regression that redirected
# memory into the worktree would surface the managed path here instead.
assert_eq 'auto-memory write is not redirected into the worktree' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
RESULT=$(run_guard "$(payload Read "$(jq -cn --arg p "$TEST_HOME/.claude/ironclaude-hooks-config.json" '{file_path:$p}')")")
assert_eq 'out-of-root absolute Read allowed' '0' "$(status_of "$RESULT")"

# Negative case: only the auto-memory tree is exempt, not any out-of-root path.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_ROOT/outside.txt" '{file_path:$p,content:"bad"}')")")
assert_eq 'non-memory out-of-root write still denied' '2' "$(status_of "$RESULT")"

# A symlink planted inside the memory tree redirects writes anywhere on disk.
# The exemption must resolve components physically, not by string shape.
ln -sfn / "$TEST_HOME/.claude/projects/demo/memory/out"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_HOME/.claude/projects/demo/memory/out$PRIMARY/src/pwned.txt" '{file_path:$p,content:"bad"}')")")
assert_eq 'symlinked auto-memory escape denied' '2' "$(status_of "$RESULT")"
rm -f "$TEST_HOME/.claude/projects/demo/memory/out"
# `*` in a case pattern spans `/`, so a deeper path must not inherit the exemption.
mkdir -p "$TEST_HOME/.claude/projects/demo/deeper/memory"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_HOME/.claude/projects/demo/deeper/memory/note.md" '{file_path:$p,content:"bad"}')")")
assert_eq 'nested pseudo-memory path denied' '2' "$(status_of "$RESULT")"

# The shared read-only predicates admit forms that still write or execute
# (`git diff --output=<path>`, `rg --pre <prog>`). That misclassification only
# matters where passthrough is DECIDED — an assignment that exists but cannot be
# bound. An unassigned session has no isolation to protect, so it is not the
# case to test here.
echo '=== read-only predicates that still write (damaged assignment) ==='
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='ready_for_integration' WHERE workspace_guid='$GUID'"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg c "git diff --output=$TEST_ROOT/pwned.txt" '{command:$c}')")")
assert_eq 'git diff --output denied on damaged assignment' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"rg --pre sh pattern ."}')")
assert_eq 'rg --pre denied on damaged assignment' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"git log --oneline -1"}')")
assert_eq 'plain read-only git allowed on damaged assignment' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"bad"}')")
assert_eq 'damaged assignment still denies Write' '2' "$(status_of "$RESULT")"
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='active' WHERE workspace_guid='$GUID'"

echo '=== command rewriting ==='
RESULT=$(run_guard "$(payload Bash '{"command":"printf managed > src/command.txt"}')")
assert_eq 'Claude Bash allowed' '0' "$(status_of "$RESULT")"
CLAUDE_COMMAND=$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.command // empty' 2>/dev/null)
assert_contains 'Claude Bash prepends managed cwd' "$CLAUDE_COMMAND" "cd -- '$MANAGED' &&"
assert_eq 'Claude Bash preserves original command bytes after prefix' \
  "cd -- '$MANAGED' && printf managed > src/command.txt" "$CLAUDE_COMMAND"
RESULT=$(run_guard "$(payload Bash '{"command":"cd .. && touch escape.txt"}')")
assert_eq 'explicit literal parent escape denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"cd src && pwd"}')")
assert_eq 'managed child-directory command remains allowed' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"git -C src status --short"}')")
assert_eq 'managed git -C child path remains allowed' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf managed-absolute > '$MANAGED/src/command.txt'" '{command:$command}')")")
assert_eq 'absolute effective-root command remains allowed' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "test -d $MANAGED" '{command:$command}')")")
assert_eq 'exact unquoted effective-root token remains allowed' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf bad > '$MANAGED-escape.txt'" '{command:$command}')")")
assert_eq 'effective-root byte-prefix outside assignment denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf bad > \"$MANAGED\"-escape.txt" '{command:$command}')")")
assert_eq 'quoted effective-root concatenation outside assignment denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf bad > '$MANAGED:escape.txt'" '{command:$command}')")")
assert_eq 'colon-suffixed effective-root outside assignment denied' '2' "$(status_of "$RESULT")"
MANAGED_PARENT=$(dirname "$MANAGED")
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf bad > '$MANAGED_PARENT/unassigned-sibling/escape.txt'" '{command:$command}')")")
assert_eq 'unassigned sibling worktree path denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf bad > '$PRIMARY/escaped-primary.txt'" '{command:$command}')")")
assert_eq 'explicit primary-checkout command denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "printf bad > '$OTHER_MANAGED/escaped-other.txt'" '{command:$command}')")")
assert_eq 'explicit other-assignment command denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload exec_command '{"cmd":"printf codex > src/command.txt"}')")
assert_eq 'Codex exec allowed' '0' "$(status_of "$RESULT")"
CODEX_COMMAND=$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.cmd // empty' 2>/dev/null)
assert_contains 'Codex exec prepends managed cwd' "$CODEX_COMMAND" "cd -- '$MANAGED' &&"
sqlite3 "$STATE_DB" "UPDATE sessions SET workflow_stage='reviewing' WHERE terminal_session='$SESSION'"
RESULT=$(run_guard "$(payload Bash '{"command":"git status"}')")
assert_eq 'review-stage policy inspects original read-only command' '0' "$(status_of "$RESULT")"
assert_eq 'review-stage read-only command still returns managed cwd' \
  "cd -- '$MANAGED' && git status" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.command // empty' 2>/dev/null)"
sqlite3 "$STATE_DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='$SESSION'"

echo '=== Codex patch rewriting ==='
PATCH=$'*** Begin Patch\n*** Add File: src/codex-a.txt\n+one\n*** Add File: src/codex-b.txt\n+two\n*** End Patch'
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$PATCH" '{command:$command}')")")
assert_eq 'Codex multi-target patch allowed' '0' "$(status_of "$RESULT")"
REWRITTEN_PATCH=$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.command // empty' 2>/dev/null)
assert_contains 'Codex first target rewritten' "$REWRITTEN_PATCH" "*** Add File: $MANAGED/src/codex-a.txt"
assert_contains 'Codex second target rewritten' "$REWRITTEN_PATCH" "*** Add File: $MANAGED/src/codex-b.txt"
MIXED_PATCH=$'*** Begin Patch\n*** Add File: docs/notes.txt\n+allowed-first\n*** Add File: src/not-allowed.txt\n+blocked-second\n*** End Patch'
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$MIXED_PATCH" '{command:$command}')")")
assert_eq 'docs-first patch cannot bypass second-target allowlist' '2' "$(status_of "$RESULT")"
sqlite3 "$STATE_DB" "UPDATE sessions SET workflow_stage='brainstorming' WHERE terminal_session='$SESSION'"
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$MIXED_PATCH" '{command:$command}')")")
assert_eq 'non-executing docs-first patch cannot mutate later source target' '2' "$(status_of "$RESULT")"
DOCS_PATCH=$'*** Begin Patch\n*** Add File: docs/notes.txt\n+docs-only\n*** End Patch'
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$DOCS_PATCH" '{command:$command}')")")
assert_eq 'all-docs patch remains allowed during brainstorming' '0' "$(status_of "$RESULT")"
PLAN_JSON_PATCH=$'*** Begin Patch\n*** Add File: docs/plans/no-design.plan.json\n+{}\n*** End Patch'
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$PLAN_JSON_PATCH" '{command:$command}')")")
assert_eq 'plan JSON requires a consumed design' '2' "$(status_of "$RESULT")"
sqlite3 "$STATE_DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='$SESSION'"
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$PATCH" '{command:$command,workdir:"invented"}')")")
assert_eq 'Codex malformed hybrid input denied' '2' "$(status_of "$RESULT")"
PRIMARY_PATCH="*** Begin Patch
*** Add File: $PRIMARY/src/codex-a.txt
+bad
*** End Patch"
RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$PRIMARY_PATCH" '{command:$command}')")")
assert_eq 'Codex primary patch denied without authority' '2' "$(status_of "$RESULT")"

echo '=== lifecycle and primary authority ==='
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='ready_for_integration' WHERE workspace_guid='$GUID'"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"frozen"}')")
assert_eq 'frozen assignment denies mutation' '2' "$(status_of "$RESULT")"
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='active' WHERE workspace_guid='$GUID'"
sqlite3 "$WORKSPACE_DB" "INSERT INTO primary_checkout_owners VALUES ('$REPOSITORY_IDENTITY','$GUID','$SESSION',datetime('now'))"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"primary-authorized"}')")
assert_eq 'primary-authorized relative write allowed' '0' "$(status_of "$RESULT")"
assert_eq 'primary-authorized path rewritten to primary root' "$PRIMARY/src/claude.txt" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
sqlite3 "$WORKSPACE_DB" "DELETE FROM primary_checkout_owners WHERE repository_identity='$REPOSITORY_IDENTITY'"

echo '=== non-Git preservation ==='
RESULT=$(run_guard "$(payload Write '{"file_path":"notes.txt","content":"plain"}' "$NON_GIT")")
assert_eq 'non-Git write preserves existing allow behavior' '0' "$(status_of "$RESULT")"
assert_eq 'non-Git input is not rewritten' '' "$(output_of "$RESULT")"

echo '=== production acceptance ==='
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"managed\n"}')")
CLAUDE_TARGET=$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path')
printf 'managed\n' > "$CLAUDE_TARGET"
assert_eq 'Claude write changes managed worktree' 'managed' "$(sed -n '1p' "$MANAGED/src/claude.txt")"
assert_eq 'Claude write leaves primary absent' 'absent' "$([ -e "$PRIMARY/src/claude.txt" ] && echo present || echo absent)"

RESULT=$(run_guard "$(payload apply_patch "$(jq -cn --arg command "$PATCH" '{command:$command}')")")
REWRITTEN_PATCH=$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.command')
# `apply_patch` is the Codex CLI's own binary and is absent on any machine
# without Codex installed. The REWRITE is what this suite owns, so assert that
# unconditionally and only apply the patch for real where the tool exists —
# otherwise a contributor without Codex sees a spurious failure.
assert_contains 'Codex patch first target rewritten into managed worktree' \
  "$REWRITTEN_PATCH" "$MANAGED/src/codex-a.txt"
if command -v apply_patch >/dev/null 2>&1; then
  printf '%s\n' "$REWRITTEN_PATCH" | apply_patch >/dev/null
  assert_eq 'Codex patch changes managed first target' 'one' "$(sed -n '1p' "$MANAGED/src/codex-a.txt")"
  assert_eq 'Codex patch leaves primary first target absent' 'absent' "$([ -e "$PRIMARY/src/codex-a.txt" ] && echo present || echo absent)"
else
  printf 'SKIP: apply_patch binary not installed — rewrite asserted, application not exercised\n'
fi

RESULT=$(run_guard "$(payload exec_command '{"cmd":"printf command > src/command.txt"}')")
CODEX_COMMAND=$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.cmd')
(cd "$PRIMARY" && bash -c "$CODEX_COMMAND")
assert_eq 'rewritten command changes managed worktree' 'command' "$(sed -n '1p' "$MANAGED/src/command.txt")"
assert_eq 'rewritten command leaves primary absent' 'absent' "$([ -e "$PRIMARY/src/command.txt" ] && echo present || echo absent)"

echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
