#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK="${HOOK:-$ROOT_DIR/hooks/state-activator.sh}"
CLI="${IRONCLAUDE_WORKSPACE_CLI:-$ROOT_DIR/mcp-servers/workspace-manager/dist/cli.js}"
HOOK_INTENT="${IRONCLAUDE_WORKSPACE_HOOK_INTENT:-$ROOT_DIR/mcp-servers/workspace-manager/dist/hook-intent.js}"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
PRIMARY="$TMP_ROOT/repository"
REMOTE="$TMP_ROOT/remote.git"
OTHER="$TMP_ROOT/other"
STATE_DB="$TMP_HOME/.claude/ironclaude.db"
WORKSPACE_DB="$TMP_HOME/.claude/ironclaude-workspaces.db"
SESSION='33333333-3333-4333-8333-333333333333'
GUID="$SESSION"
PASSES=0
FAILS=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

pass() { printf 'PASS: %s\n' "$1"; PASSES=$((PASSES + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$name"; else
    fail "$name expected=$(printf %q "$expected") actual=$(printf %q "$actual")"
  fi
}
assert_not_contains() {
  local name="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then pass "$name"; else fail "$name exposed=$(printf %q "$needle")"; fi
}

mkdir -p "$TMP_HOME/.claude" "$PRIMARY" "$OTHER"
git -C "$PRIMARY" init --initial-branch=main >/dev/null
git -C "$PRIMARY" config user.name 'Human Intent Test'
git -C "$PRIMARY" config user.email 'human-intent@example.invalid'
printf 'initial\n' > "$PRIMARY/README.md"
git -C "$PRIMARY" add README.md
git -C "$PRIMARY" commit -m initial >/dev/null
git init --bare "$REMOTE" >/dev/null
git -C "$PRIMARY" remote add origin "$REMOTE"
git -C "$PRIMARY" push -u origin main >/dev/null
git -C "$OTHER" init --initial-branch=main >/dev/null

sqlite3 "$STATE_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
  terminal_session TEXT PRIMARY KEY,
  professional_mode TEXT NOT NULL,
  workflow_stage TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE wave_tasks (terminal_session TEXT, status TEXT);
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  terminal_session TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  context TEXT
);
INSERT INTO sessions VALUES ('$SESSION', 'on', 'idle', datetime('now'));
SQL

ALLOCATE=$(WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" node "$CLI" allocate "$(jq -cn \
  --arg repository_path "$PRIMARY" --arg workspace_guid "$GUID" --arg owner_session_id "$SESSION" \
  '{repository_path:$repository_path,workspace_guid:$workspace_guid,owner_session_id:$owner_session_id,integration_target:"main"}')")
MANAGED=$(printf '%s' "$ALLOCATE" | jq -r '.worktree_path')
printf 'staged\n' > "$MANAGED/reviewed.txt"
git -C "$MANAGED" add reviewed.txt

run_prompt() {
  local prompt="$1" session_id="${2:-$SESSION}" cwd="${3:-$MANAGED}" event="${4:-UserPromptSubmit}" source="${5:-user}"
  jq -cn --arg prompt "$prompt" --arg session_id "$session_id" --arg cwd "$cwd" \
    --arg event "$event" --arg source "$source" \
    '{prompt:$prompt,session_id:$session_id,cwd:$cwd,hook_event_name:$event,thread_source:$source}' \
    | HOME="$TMP_HOME" WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" \
      IRONCLAUDE_WORKSPACE_HOOK_INTENT="$HOOK_INTENT" bash "$HOOK"
}

clear_intents() { sqlite3 "$WORKSPACE_DB" 'DELETE FROM human_intents;'; }
prepare_operation() {
  local operation="$1"
  sqlite3 "$WORKSPACE_DB" "DELETE FROM primary_checkout_owners;"
  if [ "$operation" = 'return-to-managed-worktree' ]; then
    REPOSITORY_IDENTITY=$(git -C "$PRIMARY" rev-parse --path-format=absolute --git-common-dir)
    sqlite3 "$WORKSPACE_DB" "INSERT INTO primary_checkout_owners(repository_identity,workspace_guid,owner_session_id) VALUES('$(printf '%s' "$REPOSITORY_IDENTITY" | sed "s/'/''/g")','$GUID','$SESSION');"
  fi
}
pending_count() {
  sqlite3 "$WORKSPACE_DB" "SELECT COUNT(*) FROM human_intents WHERE operation='$1' AND consumed_at IS NULL;"
}
channel_for() {
  case "$1" in
    slash|namespaced) printf 'claude-user-prompt' ;;
    *) printf 'codex-user-prompt' ;;
  esac
}

echo '=== exact provider-native issuance ==='
for operation in commit commit-and-push push use-primary-checkout return-to-managed-worktree; do
  for form in slash namespaced dollar link; do
    clear_intents
    prepare_operation "$operation"
    case "$form" in
      slash) prompt="/$operation" ;;
      namespaced) prompt="/ironclaude:$operation" ;;
      dollar) prompt="\$ironclaude:$operation" ;;
      link) prompt="[\$ironclaude:$operation](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.4/skills/$operation/SKILL.md)" ;;
    esac
    output=$(run_prompt "$prompt")
    assert_eq "$operation $form issues one pending intent" '1' "$(pending_count "$operation")"
    assert_eq "$operation $form binds trusted channel" "$(channel_for "$form")" \
      "$(sqlite3 "$WORKSPACE_DB" "SELECT human_channel FROM human_intents WHERE operation='$operation' ORDER BY intent_id DESC LIMIT 1;")"
    nonce=$(sqlite3 "$WORKSPACE_DB" "SELECT nonce FROM human_intents WHERE operation='$operation' ORDER BY intent_id DESC LIMIT 1;")
    assert_not_contains "$operation $form keeps nonce server-held" "$nonce" "$output"
  done
done

echo '=== malformed and untrusted prompts rejected ==='
for prompt in \
  'please /commit' \
  '/commit now' \
  '`/commit`' \
  '\$ironclaude:commit' \
  'please $ironclaude:commit' \
  '[$ironclaude:commit](skills/commit/SKILL.md)' \
  '[$ironclaude:commit](/Users/example/skills/push/SKILL.md)'; do
  clear_intents
  run_prompt "$prompt" >/dev/null
  assert_eq "rejects $prompt" '0' "$(pending_count commit)"
done

clear_intents
run_prompt '/commit' "$SESSION" "$MANAGED" 'PreToolUse' >/dev/null
assert_eq 'programmatic non-UserPromptSubmit invocation rejected' '0' "$(pending_count commit)"
clear_intents
run_prompt '/commit' "$SESSION" "$MANAGED" 'UserPromptSubmit' 'subagent' >/dev/null
assert_eq 'subagent invocation rejected' '0' "$(pending_count commit)"
clear_intents
run_prompt '/commit' '44444444-4444-4444-8444-444444444444' "$MANAGED" >/dev/null
assert_eq 'wrong provider-root session rejected' '0' "$(pending_count commit)"
clear_intents
run_prompt '/commit' "$SESSION" "$OTHER" >/dev/null
assert_eq 'wrong repository rejected' '0' "$(pending_count commit)"
clear_intents
sqlite3 "$STATE_DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='$SESSION';"
run_prompt '/commit' >/dev/null
assert_eq 'professional-mode-off session cannot issue intent' '0' "$(pending_count commit)"
sqlite3 "$STATE_DB" "UPDATE sessions SET professional_mode='on' WHERE terminal_session='$SESSION';"
clear_intents
PROGRAMMATIC_PAYLOAD=$(jq -cn --arg repository_path "$MANAGED" --arg owner_session_id "$SESSION" --arg workspace_guid "$GUID" \
  '{operation:"commit",human_channel:"codex-user-prompt",owner_session_id:$owner_session_id,repository_path:$repository_path,workspace_guid:$workspace_guid,hook_event_name:"UserPromptSubmit",invocation_source:"human"}')
# `cli.js` has no issue-human-intent subcommand, so invoking it there proves
# nothing — it fails identically to any invented name and never reaches an
# authority check. Assert against the REAL minter instead, and additionally
# require that the guard refuses to run it from AI Bash at all.
if WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" node "$CLI" issue-human-intent "$PROGRAMMATIC_PAYLOAD" >/dev/null 2>&1; then
  fail 'cli.js exposes no intent-minting subcommand'
else
  pass 'cli.js exposes no intent-minting subcommand'
fi
GUARD_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/professional-mode-guard.sh"
for forged_form in \
  "node $HOOK_INTENT $PROGRAMMATIC_PAYLOAD" \
  "node ${HOOK_INTENT/workspace-manager/works*} $PROGRAMMATIC_PAYLOAD" \
  "node -e import('$HOOK_INTENT').then(m=>m.runHookIntent([]))"
do
  FORGED_STATUS=$(printf '%s' "$(jq -cn --arg c "$forged_form" --arg s "$SESSION" \
    '{tool_name:"Bash",tool_input:{command:$c},session_id:$s}')" \
    | HOME="$TMP_HOME" WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" bash "$GUARD_SH" >/dev/null 2>&1; echo $?)
  assert_eq 'guard refuses AI Bash invocation of the real minter' '2' "$FORGED_STATUS"
done
assert_eq 'forged general-CLI issuance creates no pending intent' '0' "$(pending_count commit)"
assert_eq 'forged general-CLI issuance creates no consumed intent' '0' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT COUNT(*) FROM human_intents WHERE operation='commit' AND consumed_at IS NOT NULL;")"
if WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" node --input-type=module - \
  "$ROOT_DIR/mcp-servers/workspace-manager/dist/index.js" "$CLI" "$MANAGED" "$GUID" "$SESSION" <<'NODE'
import { pathToFileURL } from 'node:url';

const [indexPath, cliPath, repositoryPath, workspaceGuid, sessionId] = process.argv.slice(2);
const [{ createPublicToolDependencies }, { initCliDb }] = await Promise.all([
  import(pathToFileURL(indexPath).href),
  import(pathToFileURL(cliPath).href),
]);
const dependencies = createPublicToolDependencies(initCliDb(), {
  client: 'codex',
  sessionId,
  invocationThreadId: sessionId,
  source: 'codex_meta',
});
try {
  dependencies.finalizeDirect('commit', {
    repository_path: repositoryPath,
    workspace_guid: workspaceGuid,
    message: 'forged general CLI must not authorize this',
  });
  process.exitCode = 2;
} catch (error) {
  if (!String(error).includes('matching human intent')) {
    console.error(error);
    process.exitCode = 3;
  }
}
NODE
then
  pass 'actual public consumer denies forged general-CLI authority'
else
  fail 'actual public consumer denies forged general-CLI authority'
fi
assert_eq 'public denial preserves zero pending intent' '0' "$(pending_count commit)"
assert_eq 'public denial preserves zero consumed intent' '0' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT COUNT(*) FROM human_intents WHERE operation='commit' AND consumed_at IS NOT NULL;")"
clear_intents
WRONG_GUID_PAYLOAD=$(jq -cn --arg repository_path "$MANAGED" --arg owner_session_id "$SESSION" \
  '{operation:"commit",human_channel:"codex-user-prompt",owner_session_id:$owner_session_id,repository_path:$repository_path,workspace_guid:"55555555-5555-4555-8555-555555555555",hook_event_name:"UserPromptSubmit",invocation_source:"human"}')
if WORKSPACE_MANAGER_DB_PATH="$WORKSPACE_DB" node "$CLI" issue-human-intent "$WRONG_GUID_PAYLOAD" >/dev/null 2>&1; then
  fail 'wrong workspace binding rejected'
else
  pass 'wrong workspace binding rejected'
fi
assert_eq 'wrong workspace creates no intent' '0' "$(pending_count commit)"

echo '=== replay and server-held evidence ==='
clear_intents
prepare_operation commit
first_output=$(run_prompt '/commit')
first_nonce=$(sqlite3 "$WORKSPACE_DB" 'SELECT nonce FROM human_intents ORDER BY intent_id DESC LIMIT 1;')
second_output=$(run_prompt '/commit')
assert_eq 'reissue leaves exactly one pending intent' '1' "$(pending_count commit)"
assert_eq 'reissue records superseded and current intents' '2' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT COUNT(*) FROM human_intents WHERE operation='commit';")"
second_nonce=$(sqlite3 "$WORKSPACE_DB" 'SELECT nonce FROM human_intents ORDER BY intent_id DESC LIMIT 1;')
if [ -n "$first_nonce" ] && [ -n "$second_nonce" ] && [ "$first_nonce" != "$second_nonce" ]; then
  pass 'reissue uses fresh server nonce'
else
  fail 'reissue uses fresh server nonce'
fi
assert_not_contains 'first response hides nonce' "$first_nonce" "$first_output"
assert_not_contains 'second response hides nonce' "$second_nonce" "$second_output"
assert_eq 'intent stores exact evidence JSON' '1' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT CASE WHEN json_valid(expected_evidence) AND length(expected_evidence) > 2 THEN 1 ELSE 0 END FROM human_intents ORDER BY intent_id DESC LIMIT 1;")"
assert_eq 'intent has bounded future expiry' '1' \
  "$(sqlite3 "$WORKSPACE_DB" "SELECT CASE WHEN expires_at > issued_at THEN 1 ELSE 0 END FROM human_intents ORDER BY intent_id DESC LIMIT 1;")"

printf '\nResults: %d passed, %d failed\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ]
