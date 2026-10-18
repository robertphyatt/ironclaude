# Codex Worker Spawn Reproduction v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Get past every Codex 0.160.0 startup modal to a real prompt. Then measure:
- when the id file appears, and which process writes it;
- what `--no-daemon` changes;
- whether `$skill` dispatch submits.

**Requirements:** `docs/plans/2026-10-07-codex-spawn-repro-v2-design.md` (its `## Requirements` section, Q1–Q6 with Q1a, Q2a, Q3a)

**Design:** `docs/plans/2026-10-07-codex-spawn-repro-v2-design.md`

**Loop:** light. One task. It writes only a scratchpad probe and its logs, plus one findings note.

**Variants:** B = `--no-daemon`, the Commander's current local worker command (`commander/src/ironclaude/orchestrator_mcp.py:564-571`, selected at `:641-642`). A = daemon mode, as on the remote-machine path (`:769`). B runs first because it is production.

**Architecture:** `probe-v2.sh` is the v1 probe with these changes:
- **Modal handler on every check.** `handle_modal` answers each modal by its exact v1-captured text: `Folder access` gets `1`, `Hooks need review` gets `2`, and `Working directory` gets `2`. If the same modal is still up on the next check, it sends Enter once. It runs in readiness, before every id-file poll, and before each dispatch step.
- **Readiness** waits up to 90 s. Ready means the banner and the `Ask Codex to do anything` input line are both on screen with no modal marker, and that holds on two checks 3 s apart.
- **Id-file poll** runs every 1 s for the first 60 s after readiness, then every 5 s to 300 s. In-tree writers (pane process tree) and any writer are logged separately.
- **Dispatch** mirrors `tmux_manager.send_keys` (text, 0.2 s, Enter) and snapshots the screen after typing and before Enter 1, so the picker is visible. After Enter 1 it counts the unsubmitted mention in the composer.
- **Scratch repo and output** are per variant: `repo-v2-<V>`, `run-v2-<V>`.

**Tech Stack:** bash, tmux, `pgrep`, `ps`, `sqlite3 -readonly`, Codex CLI 0.160.0.

## Execution invariants

- **Shell use:**
  - Shell state does not persist between steps, so every path is absolute.
  - Use `git -C /Users/roberthyatt/Code/ironclaude` for project git commands.
  - zsh `nomatch` is on, so quote globs.
- **Writing the script:** write `probe-v2.sh` with the Write tool, not a bash heredoc. The guard blocks any Bash command whose text contains `git commit`, and the script has one in its scratch-repo setup.
- **Waiting:** foreground `sleep` is blocked. Run each variant as a background Bash invocation (`run_in_background`, timeout 900000) and wait for its completion notification. Worst case per variant is about 90 + 300 + 85 + 5 s ≈ 8 min.
- **Staging:** `docs/` is gitignored, so stage with `git add -f`.
- **Evidence:**
  - Each variant writes to its own `run-v2-<V>` directory.
  - No evidence command uses `2>/dev/null`.
  - Every id file is logged before `kill-session`.
- **Expected results:** this is an investigation, so expected results mean recording what was actually seen.
- **Hard limits:**
  - No access to Commander processes, the Commander DB or `roleplaying-agents`, except read-only `sqlite3 -readonly` on `~/.claude/ironclaude.db`.
  - No pattern-kill. Kill only with `tmux kill-session -t <exact name>`, or kill a recorded survivor by exact PID if the probe launched it. Codex processes may be listed but are never killed by pattern.
  - No commits or pushes in the project repo.
- **Disclosed side effects:**
  - Trust and hooks rows in `~/.codex/config.toml` for the two scratch repos and possibly their parent directory. Both are logged.
  - `session-init` maintenance.
  - Session rows in `ironclaude.db`.
- **Paths:** `<SP>` = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad`.
- **Model:** `gpt-5.6-terra`.

---

## Task 1: Re-run the probe past the modals, measure and record

**Files:**
- Create: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe-v2.sh`
- Create: `docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`
- Stage without editing: the three loop docs listed in Step 5.

**No tests required.** This is an investigation with no product code.

**Step 1: write the probe** with the Write tool, to `<SP>/codex-repro/probe-v2.sh`:

```bash
#!/bin/bash
# Usage: probe-v2.sh B|A   (B = --no-daemon, the Commander's current local worker cmd; A = daemon mode, remote-path style)
set -u
V="$1"
SP=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro
REPO="$SP/repo-v2-$V"; OUT="$SP/run-v2-$V"; LOG="$OUT/probe.log"
S="icrepro2-$V"; MODEL=gpt-5.6-terra
mkdir -p "$OUT"
log() { echo "$(date +%s) $*" >> "$LOG"; }
snap() { tmux capture-pane -p -t "$S" > "$OUT/snap-$1.txt" 2>&1; }

if [ ! -d "$REPO/.git" ]; then
  mkdir -p "$REPO"
  git -C "$REPO" init -q
  cp /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/templates/worker_agents.md "$REPO/AGENTS.md"
  git -C "$REPO" add AGENTS.md
  git -C "$REPO" -c user.name=repro -c user.email=repro@local commit -qm init
fi

TMUX_PATH=$(tmux show-environment -g PATH 2>&1); log "tmux_global_PATH=[$TMUX_PATH]"
log "codex_via_claude_PATH=$(command -v codex) codex_via_tmux_PATH=$(PATH="${TMUX_PATH#PATH=}" command -v codex 2>&1)"
log "version=$(codex --version 2>&1)"
PRE_CODEX=$(pgrep -fl -- codex); log "codex_procs_before=[$PRE_CODEX]"   # list only; never killed by pattern
EXTRA=""; [ "$V" = B ] && EXTRA="--no-daemon"
CMD="export IC_ROLE=worker; export IC_WORKER_ID=$S; export ENABLE_STOP_REVIEW=0; exec codex --model $MODEL $EXTRA --dangerously-bypass-approvals-and-sandbox"
log "cmd=$CMD"

touch "$OUT/start.ref"; START=$(date +%s)
tmux new-session -d -s "$S" -x 200 -y 50 -c "$REPO" "$CMD"
tmux pipe-pane -t "$S" -o "cat >> $OUT/pane.log"
PANE=$(tmux list-panes -t "$S" -F '#{pane_pid}')
log "start=$START pane_pid=$PANE"

desc() {  # pane pid + all descendants
  local all="$1" front="$1" next p c
  while [ -n "$front" ]; do
    next=""
    for p in $front; do
      for c in $(pgrep -P "$p"); do next="$next $c"; all="$all $c"; done
    done
    front=$(echo $next)
  done
  echo $all
}

# Modal handler, run on every check in every phase. Exact Codex 0.160.0 modal texts from v1 snapshots.
N=0; LAST_MODAL=""
handle_modal() {  # $1 = tag; returns 0 if a modal was on screen and answered
  local scr kind key el
  scr=$(tmux capture-pane -p -t "$S" 2>&1); el=$(( $(date +%s) - START ))
  if echo "$scr" | grep -q 'Folder access'; then kind=trust; key=1
  elif echo "$scr" | grep -q 'Hooks need review'; then kind=hooks; key=2
  elif echo "$scr" | grep -q 'Working directory'; then kind=resume_picker; key=2
  else LAST_MODAL=""; return 1; fi
  N=$((N+1)); snap "modal-$N-$kind"
  if [ "$LAST_MODAL" = "$kind" ]; then
    tmux send-keys -t "$S" Enter; log "$1 t=$el modal=$kind still_up answered=Enter"; LAST_MODAL=""
  else
    tmux send-keys -t "$S" -- "$key"; log "$1 t=$el modal=$kind answered=$key"; LAST_MODAL=$kind
  fi
  sleep 2; return 0
}
is_modal() { echo "$1" | grep -q -e 'Folder access' -e 'Hooks need review' -e 'Working directory'; }
is_ready() { echo "$1" | grep -q 'OpenAI Codex' && echo "$1" | grep -q 'Ask Codex to do anything' && ! is_modal "$1"; }

# Phase 1: modal-aware readiness, up to 90 s.
READY_AT=""
while [ $(( $(date +%s) - START )) -lt 90 ]; do
  handle_modal p1 && continue
  SCR=$(tmux capture-pane -p -t "$S" 2>&1); EL=$(( $(date +%s) - START ))
  if is_ready "$SCR"; then
    sleep 3; SCR2=$(tmux capture-pane -p -t "$S" 2>&1)
    if is_ready "$SCR2"; then READY_AT=$(( $(date +%s) - START )); break; fi
    log "t=$EL ready_flapped"; continue
  fi
  sleep 1
done
log "ready_at=${READY_AT:-NOT_READY_90s} modals_answered=$N"; snap ready

# Phase 2: id-file poll, mirroring _set_pm_via_sqlite (300 s). Daemon accepts only
# pane-tree writers (orchestrator_mcp.py:2473-2486): ID_* = in-tree, ANY_* = first of any provenance.
ALLPIDS=""; ID_AT=""; ID_UUID=""; ID_PID=""; ANY_AT=""; ANY_PID=""; ANY_UUID=""
rescan() {
  local tag="$1" D f pid d intree; D=$(desc "$PANE"); ALLPIDS="$ALLPIDS $D"
  log "$tag desc=[$D]"
  for f in $(find "$HOME/.claude" -maxdepth 1 -name 'ironclaude-session-*.id' -newer "$OUT/start.ref"); do
    pid=${f##*-}; pid=${pid%.id}; intree=no
    for d in $D; do [ "$d" = "$pid" ] && intree=yes; done
    log "$tag idfile=$f pid=$pid in_pane_tree=$intree mtime=$(stat -f %m "$f") content=$(cat "$f") ps=[$(ps -p "$pid" -o pid=,ppid=,lstart=,command= 2>&1)]"
    if [ "$intree" = yes ] && [ -z "$ID_AT" ]; then ID_AT=$(( $(date +%s) - START )); ID_UUID=$(cat "$f"); ID_PID=$pid; fi
    if [ -z "$ANY_AT" ]; then ANY_AT=$(( $(date +%s) - START )); ANY_UUID=$(cat "$f"); ANY_PID=$pid; fi
  done
}
for t in $(seq 0 1 60) $(seq 65 5 300); do
  while [ $(( $(date +%s) - START )) -lt $(( ${READY_AT:-90} + t )) ]; do sleep 1; done
  handle_modal "p2 t=$t"
  rescan "t=$t"
  case " 10 30 60 120 240 300 " in *" $t "*) snap "id-$t";; esac
  [ -n "$ID_AT" ] && break
done
log "id_at=${ID_AT:-NONE_300s} id_pid=${ID_PID:-none} uuid=${ID_UUID:-none} any_at=${ANY_AT:-NONE_300s} any_pid=${ANY_PID:-none}"

# Phase 3: dispatch test, mirroring tmux_manager.send_keys (text, 0.2 s, Enter).
# The picker closes on Enter, so snapshot it after typing and before Enter 1.
handle_modal p3-pre
tmux send-keys -t "$S" -- '$ironclaude:write-lossless-ai-messages'; sleep 0.2; snap dispatch-typed; tmux send-keys -t "$S" Enter
sleep 10; snap dispatch-enter1; rescan after-enter1
log "picker_when_typed=$(grep -c 'enter insert' "$OUT/snap-dispatch-typed.txt") mention_after_enter1=$(grep -c 'ironclaude:write-lossless-ai-messages' "$OUT/snap-dispatch-enter1.txt") marker_after_enter1=$(grep -c IC_LOSSLESS_AI_MESSAGES_ACTIVE "$OUT/snap-dispatch-enter1.txt")"
handle_modal p3-enter2
tmux send-keys -t "$S" Enter
sleep 30; snap dispatch-enter2; rescan after-enter2
log "marker_after_enter2_snap=$(grep -c IC_LOSSLESS_AI_MESSAGES_ACTIVE "$OUT/snap-dispatch-enter2.txt") marker_in_pane_log=$(grep -c IC_LOSSLESS_AI_MESSAGES_ACTIVE "$OUT/pane.log")"
# Phase 3b: one plain prompt — direct test of "SessionStart fires only on first submitted turn"
handle_modal p3-plain
tmux send-keys -t "$S" -- 'Reply with the single word ok. Use no tools.'; sleep 0.2; tmux send-keys -t "$S" Enter
sleep 45; snap plain-turn; rescan after-plain-turn
log "post_dispatch id_at=${ID_AT:-NONE} any_at=${ANY_AT:-NONE}"

# Phase 4: state + cleanup by exact name
rescan before-kill
for u in $ID_UUID $ANY_UUID; do log "db_row[$u]=[$(sqlite3 -readonly "$HOME/.claude/ironclaude.db" "SELECT terminal_session, professional_mode, workflow_stage FROM sessions WHERE terminal_session='$u'" 2>&1)]"; done
log "codex_config_entries=[$(grep -n -e "$REPO" -e "$SP" "$HOME/.codex/config.toml")]"
ALLPIDS=$(echo $ALLPIDS | tr ' ' '\n' | sort -u | tr '\n' ' '); log "allpids=[$ALLPIDS]"
tmux kill-session -t "$S"; log "killed session $S rc=$?"
sleep 3
for p in $ALLPIDS; do
  if ps -p "$p" > /dev/null; then log "SURVIVOR pid=$p [$(ps -p "$p" -o pid=,ppid=,lstart=,command=)]"; fi
done
log "codex_procs_after=[$(pgrep -fl -- codex)]"   # report only, never kill by pattern
log "done"
```

**Step 2: run variant B (production) in the background** (Bash timeout 900000):

```bash
bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe-v2.sh B
```

Expected: record what actually happened. Then read `run-v2-B/probe.log` and every `snap-*.txt`. If a survivor remains, kill it only by its exact PID, and only if the probe launched it. New Codex processes are reported, never killed.

**Step 3: run variant A** the same way, after Step 2 finishes:

```bash
bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe-v2.sh A
```

Expected: record what actually happened, as in Step 2.

**Step 4: write the findings note** at `docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`.

For each variant (B = production `--no-daemon`, A = daemon mode), record:
- the Codex path and version, and the command;
- every modal that was answered, with its phase, time and key, and the readiness result;
- the in-tree id-file result (`id_at`, `id_pid`, `uuid`) and the any-provenance result (`any_at`, `any_pid`, with the writer's `ps` line);
- the rescan results after Enter 1, after Enter 2 and after the plain prompt;
- what the snapshots show: whether the picker was open after typing (`picker_when_typed`), whether the unsubmitted mention sat in the composer after Enter 1, and the marker counts after Enter 1, after Enter 2 and in `pane.log`;
- the DB rows, `config.toml` rows, any survivors, and the before/after diff of Codex processes.

Then add these sections:
- **Conclusions**, each marked VERIFIED or UNRESOLVED:
  - Q2: when the id file appears and who writes it;
  - whether the id file appears only after a turn is submitted;
  - the effect of `--no-daemon`;
  - Q3: dispatch behaviour at a real prompt, including whether a second Enter submits the skill and emits `IC_LOSSLESS_AI_MESSAGES_ACTIVE`.
- **Relation to the production failure:** production spawn 1 (2026-10-06) got through activation and stalled on the picker; production spawn 2 sat at an idle prompt after an update banner and no id file was found in 300 s.
- **Inputs for the spawn-fix design.**
- **Leftover state.**

**Step 5: stage the findings and the loop docs:**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md docs/plans/2026-10-07-codex-spawn-repro-v2-design.md docs/plans/2026-10-07-codex-spawn-repro-v2.md docs/plans/2026-10-07-codex-spawn-repro-v2.plan.json
```

Expected: no output, exit 0.
