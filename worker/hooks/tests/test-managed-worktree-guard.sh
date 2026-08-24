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
assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  if [[ "$haystack" == *"$needle"* ]]; then fail "$label" "unexpectedly present=$needle"; else pass "$label"; fi
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
HEARTBEAT_LOG="$TEST_HOME/.claude/ironclaude-worktree-heartbeat.log"

mkdir -p "$TEST_HOME/.claude" "$PRIMARY/src" "$NON_GIT"
printf '{"verbose_hook_logs":false}\n' > "$TEST_HOME/.claude/ironclaude-hooks-config.json"
git -C "$PRIMARY" init -q
git -C "$PRIMARY" config user.email test@example.com
git -C "$PRIMARY" config user.name Test
printf 'primary\n' > "$PRIMARY/src/existing.txt"
git -C "$PRIMARY" add src/existing.txt
git -C "$PRIMARY" commit -qm initial
# docs/ is gitignored so a managed session's OWN plan/review artifacts — which
# live under the PRIMARY checkout's docs/plans and docs/reviews — are exempt from
# the escape guard when git confirms they are ignored. A force-added TRACKED file
# under docs/plans proves the check-ignore gate still refuses tracked paths
# (git check-ignore reports a tracked path as NOT ignored → exit 1).
printf 'docs/\n' > "$PRIMARY/.gitignore"
mkdir -p "$PRIMARY/docs/plans" "$PRIMARY/docs/reviews" "$PRIMARY/docs/other"
printf 'tracked\n' > "$PRIMARY/docs/plans/tracked.md"
git -C "$PRIMARY" add .gitignore
git -C "$PRIMARY" add -f docs/plans/tracked.md
git -C "$PRIMARY" commit -qm 'gitignore docs; track one plan artifact'
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
  '["docs/notes.txt","src/claude.txt","src/codex-a.txt","src/codex-b.txt","src/command.txt","notes.txt","docs/plans/x.md","docs/reviews/y.md","docs/plans/tracked.md","docs/other/z.md","src/x.txt","docs/plans/plink/evil.md"]',
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

# A managed session's OWN gitignored plan/review artifacts live under the PRIMARY
# checkout's docs/plans and docs/reviews. The adapter used to refuse them as
# escaping the worktree overlay, forcing the human-only /use-primary-checkout.
# The carve-out lets them pass through UNREWRITTEN, but ONLY when git confirms the
# exact target is gitignored — a TRACKED file, a path outside those two dirs, a
# plain source path, `..` traversal, and a symlinked component all stay refused.
#
# Every REFUSE case below is in this wave's allowed_files, so the wave allowlist
# cannot be what refuses it; the ADAPTER carve-out is the only thing that can, and
# each denial asserts the adapter's 'MANAGED WORKTREE ENFORCEMENT FAILED' reason
# (not the wave's 'FILE NOT IN PLAN'). That makes each negative falsifiable
# against the carve-out itself: widen the dir gate or drop the check-ignore gate
# and the guard would return 0 instead of 2.
echo '=== own gitignored plan/review artifacts under the primary checkout ==='
# ALLOW — gitignored docs/plans and docs/reviews artifacts pass through unrewritten.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/plans/x.md" '{file_path:$p,content:"plan"}')")")
assert_eq 'gitignored primary docs/plans artifact allowed' '0' "$(status_of "$RESULT")"
assert_eq 'gitignored docs/plans artifact is not rewritten' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/reviews/y.md" '{file_path:$p,content:"review"}')")")
assert_eq 'gitignored primary docs/reviews artifact allowed' '0' "$(status_of "$RESULT")"
assert_eq 'gitignored docs/reviews artifact is not rewritten' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"

# REFUSE (C) — a TRACKED file under docs/plans (git check-ignore exit 1) stays refused.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/plans/tracked.md" '{file_path:$p,content:"bad"}')")")
assert_eq 'tracked primary docs/plans file denied' '2' "$(status_of "$RESULT")"
assert_contains 'tracked docs/plans denial is the worktree adapter, not the wave' \
  "$(output_of "$RESULT")" 'MANAGED WORKTREE ENFORCEMENT FAILED'
# REFUSE (D) — a gitignored path OUTSIDE docs/plans and docs/reviews stays refused,
# proving the widening is bounded to those two directories.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/other/z.md" '{file_path:$p,content:"bad"}')")")
assert_eq 'gitignored non-artifact primary docs path denied' '2' "$(status_of "$RESULT")"
assert_contains 'non-artifact docs denial is the worktree adapter' \
  "$(output_of "$RESULT")" 'MANAGED WORKTREE ENFORCEMENT FAILED'
# REFUSE (E) — a plain primary source path stays refused (no general primary-write escape).
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/src/x.txt" '{file_path:$p,content:"bad"}')")")
assert_eq 'primary src write still denied' '2' "$(status_of "$RESULT")"
assert_contains 'primary src denial is the worktree adapter' \
  "$(output_of "$RESULT")" 'MANAGED WORKTREE ENFORCEMENT FAILED'
# REFUSE (F) — a `..` traversal out of docs/plans stays refused.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/plans/../../src/x" '{file_path:$p,content:"bad"}')")")
assert_eq 'traversal out of docs/plans denied' '2' "$(status_of "$RESULT")"
assert_contains 'traversal denial is the worktree adapter' \
  "$(output_of "$RESULT")" 'MANAGED WORKTREE ENFORCEMENT FAILED'
# REFUSE (G) — a symlinked component under docs/plans stays refused. `plink` is
# gitignored (docs/) so check-ignore alone would pass it; only the physical
# symlink guard refuses. In allowed_files, so a dropped guard would return 0.
ln -s "$TEST_ROOT" "$PRIMARY/docs/plans/plink"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/plans/plink/evil.md" '{file_path:$p,content:"bad"}')")")
assert_eq 'symlinked docs/plans component denied' '2' "$(status_of "$RESULT")"
assert_contains 'symlink denial is the worktree adapter' \
  "$(output_of "$RESULT")" 'MANAGED WORKTREE ENFORCEMENT FAILED'
rm -f "$PRIMARY/docs/plans/plink"

# The bug bites in the NON-executing stages where artifacts are actually written
# (writing-plans emits docs/plans, reviewing emits docs/reviews). There the docs
# gate governs and the wave allowlist never runs, so no allowed_files entry is
# needed — this proves the reported bug is fixed, not merely hand-seeded above.
sqlite3 "$STATE_DB" "UPDATE sessions SET workflow_stage='reviewing' WHERE terminal_session='$SESSION'"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$PRIMARY/docs/reviews/y.md" '{file_path:$p,content:"review"}')")")
assert_eq 'reviewing-stage gitignored docs/reviews artifact allowed' '0' "$(status_of "$RESULT")"
assert_eq 'reviewing-stage docs/reviews artifact is not rewritten' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
sqlite3 "$STATE_DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='$SESSION'"

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

# Bug B: a COMPLETE quote-wrapped owned-worktree root word resolves INSIDE this
# assignment, but the boundary loop rejected it because the surrounding quote
# byte is not one of its token-boundary bytes. `git -C '<root>' …` is what a
# model naturally writes. The fix pre-strips such a word before the loop while
# preserving the concatenation-visibility contract: any quote/byte concatenation
# that reaches OUTSIDE the assignment must still be refused.
echo '=== quoted owned-worktree root word (bug B) ==='
# ALLOW — exact quoted root, both quote styles, and a quoted descendant.
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C '$MANAGED' status" '{command:$command}')")")
assert_eq 'quoted exact owned-worktree root allowed (single quotes)' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C \"$MANAGED\" status" '{command:$command}')")")
assert_eq 'quoted exact owned-worktree root allowed (double quotes)' '0' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C \"$MANAGED/src\" log" '{command:$command}')")")
assert_eq 'quoted owned-worktree descendant allowed' '0' "$(status_of "$RESULT")"
# Regression guard: the pre-existing UNQUOTED bare-root form stays allowed.
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C $MANAGED add src/existing.txt" '{command:$command}')")")
assert_eq 'unquoted bare owned-worktree root stays allowed' '0' "$(status_of "$RESULT")"
# REFUSE — every quoted concatenation that escapes the assignment stays denied.
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C '$MANAGED'x status" '{command:$command}')")")
assert_eq 'quoted-root trailing-byte concatenation still denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C x'$MANAGED' status" '{command:$command}')")")
assert_eq 'quoted-root leading-byte concatenation still denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C '$PRIMARY' status" '{command:$command}')")")
assert_eq 'quoted primary checkout still denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C '$MANAGED/..' status" '{command:$command}')")")
assert_eq 'quoted parent traversal still denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C '$OTHER_MANAGED' status" '{command:$command}')")")
assert_eq 'quoted other-assignment path still denied' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"git -C ~/repository status"}')")
assert_eq 'tilde path still denied (unaffected by quoted-word strip)' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash "$(jq -cn --arg command "git -C \"\$(pwd)\" status" '{command:$command}')")")
assert_eq 'command-substitution still denied (unaffected by quoted-word strip)' '2' "$(status_of "$RESULT")"
RESULT=$(run_guard "$(payload Bash '{"command":"cd - && git status"}')")
assert_eq 'cd - still denied (unaffected by quoted-word strip)' '2' "$(status_of "$RESULT")"

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
# C(ii): a FROZEN assignment (lifecycle_status != 'active') must still let a
# READ-ONLY input through — workspace_bind_effective_root fails the frozen
# check at line ~351, and the caller's WORKSPACE_READONLY_INPUT fallback
# (~372) is what turns that bind failure into passthrough instead of a block.
# The Read case exercises the 'file' tool branch of that fallback; the
# read-only Bash case exercises the 'command' branch. Both must return 0 with
# NOTHING rewritten, since a failed bind never reaches the rewrite step.
RESULT=$(run_guard "$(payload Read '{"file_path":"src/existing.txt"}')")
assert_eq 'frozen assignment allows read-only Read (passthrough)' '0' "$(status_of "$RESULT")"
assert_eq 'frozen assignment Read is not rewritten' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
RESULT=$(run_guard "$(payload Bash '{"command":"git log --oneline -1"}')")
assert_eq 'frozen assignment allows read-only Bash (passthrough)' '0' "$(status_of "$RESULT")"
assert_eq 'frozen assignment read-only Bash is not rewritten' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.command // empty' 2>/dev/null)"
# C: the frozen-guard memory-write carve-out. A memory-path Write is not
# READ-ONLY (workspace_input_is_readonly only classifies Read and read-only
# Bash), so it does not benefit from the WORKSPACE_READONLY_INPUT fallback
# above. It must still pass through UNREWRITTEN while frozen, because
# professional-mode-guard.sh has always allowed auto-memory writes at any
# workflow stage — the frozen bind failure must not strand them.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_HOME/.claude/projects/demo/memory/note.md" '{file_path:$p,content:"frozen-remembered"}')")")
assert_eq 'frozen assignment allows auto-memory write (passthrough)' '0' "$(status_of "$RESULT")"
assert_eq 'frozen assignment auto-memory write is not redirected' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
# Negative: a symlinked pseudo-memory escape must still be denied while frozen —
# the carve-out reuses workspace_file_target_is_exempt's symlink guard (:51-74)
# unchanged, so this proves the carve-out did not widen it.
ln -sfn / "$TEST_HOME/.claude/projects/demo/memory/out"
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_HOME/.claude/projects/demo/memory/out$PRIMARY/src/pwned.txt" '{file_path:$p,content:"bad"}')")")
assert_eq 'frozen assignment denies symlinked auto-memory escape' '2' "$(status_of "$RESULT")"
rm -f "$TEST_HOME/.claude/projects/demo/memory/out"
# Negative: `*` in a case pattern spans `/`, so a deeper/memory path must not
# inherit the exemption while frozen either.
RESULT=$(run_guard "$(payload Write "$(jq -cn --arg p "$TEST_HOME/.claude/projects/demo/deeper/memory/note.md" '{file_path:$p,content:"bad"}')")")
assert_eq 'frozen assignment denies nested pseudo-memory path' '2' "$(status_of "$RESULT")"
# Falsifier: the SAME frozen assignment must still block a tracked-file
# mutation — proving the read-only fallback did not also open the door for
# writes. Asserting the frozen-lifecycle message text (not just exit 2) means
# deleting the :351 frozen check, or widening the :372 fallback to cover
# mutations, both make this fail rather than pass vacuously.
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"frozen"}')")
assert_eq 'frozen assignment denies mutation' '2' "$(status_of "$RESULT")"
assert_contains 'frozen mutation denial names the frozen lifecycle state' \
  "$(output_of "$RESULT")" 'frozen in lifecycle state ready_for_integration'
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='active' WHERE workspace_guid='$GUID'"
sqlite3 "$WORKSPACE_DB" "INSERT INTO primary_checkout_owners VALUES ('$REPOSITORY_IDENTITY','$GUID','$SESSION',datetime('now'))"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"primary-authorized"}')")
assert_eq 'primary-authorized relative write allowed' '0' "$(status_of "$RESULT")"
assert_eq 'primary-authorized path rewritten to primary root' "$PRIMARY/src/claude.txt" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
sqlite3 "$WORKSPACE_DB" "DELETE FROM primary_checkout_owners WHERE repository_identity='$REPOSITORY_IDENTITY'"

# The 60-minute reap TTL never distinguishes an idle owner from a LIVE one
# because acquired_at is never renewed. A confirmed owner's file operation
# must renew its own row so the (unchanged) reap TTL becomes an idle signal.
# Falsifier: without the renewal, acquired_at stays at -90 minutes and the
# `> datetime('now','-5 minutes')` comparison returns 0.
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='active' WHERE workspace_guid='$GUID'"
sqlite3 "$WORKSPACE_DB" "INSERT INTO primary_checkout_owners VALUES ('$REPOSITORY_IDENTITY','$GUID','$SESSION',datetime('now','-90 minutes'))"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"heartbeat"}')")
assert_eq 'owner file op still allowed after stale acquired_at' '0' "$(status_of "$RESULT")"
assert_eq 'owner file op renews the primary-checkout lock' '1' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT (acquired_at > datetime('now','-5 minutes')) FROM primary_checkout_owners WHERE repository_identity='$REPOSITORY_IDENTITY'")"
sqlite3 "$WORKSPACE_DB" "DELETE FROM primary_checkout_owners WHERE repository_identity='$REPOSITORY_IDENTITY'"

# Cross-session negative (characterization: current source already behaves
# correctly — no RED expected). A row whose workspace_guid matches THIS
# session's guid but whose owner_session_id belongs to a DIFFERENT session
# must not renew — the full :374 guard is `owner_guid = guid AND owner_session
# = SESSION_TAG`. This seed isolates the owner_session conjunct: owner_guid
# already equals guid, so dropping the owner_session half of the guard is what
# this seed would expose (the heartbeat log would gain an entry stamped with
# OTHER_SESSION); a fully-mismatched seed (both fields differ) would stay
# silent even with that half removed, since owner_guid alone would still fail.
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='active' WHERE workspace_guid='$GUID'"
sqlite3 "$WORKSPACE_DB" "INSERT INTO primary_checkout_owners VALUES ('$REPOSITORY_IDENTITY','$GUID','$OTHER_SESSION',datetime('now','-90 minutes'))"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"cross-session"}')")
assert_eq 'cross-session file op still allowed' '0' "$(status_of "$RESULT")"
assert_eq 'cross-session guid-match owner is NOT renewed' '1' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT (acquired_at < datetime('now','-5 minutes')) FROM primary_checkout_owners WHERE repository_identity='$REPOSITORY_IDENTITY'")"
assert_not_contains 'cross-session file op logs no heartbeat renewal for the mismatched owner' \
  "$(cat "$HEARTBEAT_LOG" 2>/dev/null)" "owner_session=$OTHER_SESSION"
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

# Worktree isolation is a property of the ASSIGNMENT, not of professional mode:
# a healthy owned assignment still redirects a relative write into the managed
# worktree even while professional mode is off (sub-test (a)). But an adapter
# FAILURE (damaged/unbindable assignment, or an escaping target) is ADVISORY
# while off — the operator's write is never blocked (Invariant B); the guard
# emits a WARNING and allows it.
echo '=== mode-off worktree redirection (#2) ==='
sqlite3 "$STATE_DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='$SESSION'"

# (a) healthy owned assignment -> relative write still redirected into the worktree.
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"mode-off-managed\n"}')")
assert_eq 'mode-off healthy assignment write allowed' '0' "$(status_of "$RESULT")"
assert_eq 'mode-off healthy assignment write redirected to managed root' \
  "$MANAGED/src/claude.txt" \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"

# (d) mode-off means no WORKFLOW-STAGE gating — a git commit must still be
# allowed (rewritten/passed), proving the redirect fix does not leak the
# ON-path's commit/push block into the OFF path.
RESULT=$(run_guard "$(payload Bash '{"command":"git commit -m x"}')")
assert_eq 'mode-off git commit allowed (no ON-path gating leak)' '0' "$(status_of "$RESULT")"

# (c) damaged/unhealthy assignment while OFF -> ADVISORY allow, NOT a block
#     (Invariant B: professional mode off never blocks an operator write; the
#     redirect still applies for a HEALTHY assignment — sub-test (a) above).
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='ready_for_integration' WHERE workspace_guid='$GUID'"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"bad"}')")
assert_eq 'mode-off damaged assignment write advisory-allowed' '0' "$(status_of "$RESULT")"
assert_contains 'mode-off damaged assignment advisory emits WARNING' "$(output_of "$RESULT")" 'WARNING'
assert_contains 'mode-off damaged assignment advisory names the adapter failure' "$(output_of "$RESULT")" 'managed-worktree adapter failed'
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET lifecycle_status='active' WHERE workspace_guid='$GUID'"

# (e) healthy assignment + escaping target while OFF -> advisory allow too.
OUT_OF_ROOT="$TEST_ROOT/outside/scratch.txt"
RESULT=$(run_guard "$(payload Write "$(jq -nc --arg p "$OUT_OF_ROOT" '{file_path:$p, content:"x"}')")")
assert_eq 'mode-off escaping-target write advisory-allowed' '0' "$(status_of "$RESULT")"
assert_contains 'mode-off escaping-target advisory emits WARNING' "$(output_of "$RESULT")" 'WARNING'

# (b) no assignment -> plain passthrough, unchanged (nothing to isolate).
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET owner_session_id='unbound-for-test' WHERE workspace_guid='$GUID'"
RESULT=$(run_guard "$(payload Write '{"file_path":"src/claude.txt","content":"passthrough"}')")
assert_eq 'mode-off no assignment write allowed' '0' "$(status_of "$RESULT")"
assert_eq 'mode-off no assignment write not redirected' '' \
  "$(output_of "$RESULT" | jq -r '.hookSpecificOutput.updatedInput.file_path // empty' 2>/dev/null)"
sqlite3 "$WORKSPACE_DB" "UPDATE assignments SET owner_session_id='$SESSION' WHERE workspace_guid='$GUID'"

sqlite3 "$STATE_DB" "UPDATE sessions SET professional_mode='on' WHERE terminal_session='$SESSION'"

echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
