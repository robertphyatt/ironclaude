# Codex Worker Spawn Reproduction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Measure why Codex worker PM activation is slow or never finishes, and whether `$skill` dispatch submits. Write the results to a findings note that feeds the spawn-fix design.

**Requirements:** `docs/plans/2026-10-06-codex-spawn-repro-requirements.md`

**Design:** `docs/plans/2026-10-06-codex-spawn-repro-design.md`

**Loop:** light. One task. Writes go only to a scratchpad probe script and its logs, plus one findings note.

**Architecture:** A bash probe script reproduces the Commander's spawn sequence (`orchestrator_mcp.py` 4882 → 5053 → 5114) in a scratch tmux session:

1. Launch with the daemon's exact command, with `codex` bare, as the daemon runs it.
2. Wait for readiness, dismissing the trust and hooks prompts exactly as `_wait_for_ready` does.
3. Poll for an id file in the pane's process tree for 300 s.
4. Run the dispatch test, then one plain prompt, rescanning for id files after each step.
5. Clean up by exact session name.

It runs once per variant: A without `--no-daemon`, B with it.

**Tech Stack:** bash, tmux, `pgrep`, `ps`, `sqlite3 -readonly`, Codex CLI.

**Deviations from the design, all deliberate.** Each mirrors the daemon:
- the poll window is 300 s, the daemon's own window, not 360 s;
- prompts are dismissed only during the 30 s readiness phase, because the daemon dismisses nothing after ready, so a later modal is snapshotted as a finding;
- the dispatch marker is searched in both the snapshot and `pane.log`.

## Execution invariants

- **Shell use:**
  - Shell state does not persist between steps, so every path is absolute.
  - Use `git -C /Users/roberthyatt/Code/ironclaude` for git commands.
  - zsh `nomatch` is on, so quote globs.
- **Waiting:** foreground `sleep` is blocked. The probe runs as one background Bash invocation (`run_in_background`, timeout 900000), and the orchestrator waits for its completion notification.
- **Staging:** `docs/` is gitignored, so stage with `git add -f`.
- **Evidence:**
  - Each variant writes to its own directory: `<SP>/codex-repro/run-A` or `run-B`.
  - No evidence command uses `2>/dev/null`.
  - Every id file is logged before `kill-session`. `session-init.sh` deletes the id files of dead PIDs at every later session start, so an unlogged one is lost.
- **Expected results:** this is an investigation, so each expected result is to record what was actually measured.
- **Hard limits:**
  - Do not access any Commander process or the Commander DB. Do not access `roleplaying-agents`, except read-only `sqlite3 -readonly` on `~/.claude/ironclaude.db`.
  - No pattern-kill. Codex processes may be *listed* by pattern, but are killed only with `tmux kill-session -t <exact name>`, or with `kill <exact PID>` for a recorded survivor that the probe launched.
  - No commits or pushes in the project repo.
- **Disclosed side effects:**
  - Accepting the trust and hooks prompts persists `[projects.*]` and `[hooks.state.*]` rows in `~/.codex/config.toml`. The probe logs them.
  - Every session start runs `session-init.sh`'s global maintenance: it purges stale id files and cleans the `/tmp/ic-*` caches.
  - The probe writes session rows to `~/.claude/ironclaude.db`.
- **Scratch path:** `<SP>` = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad`.
- **Model:** `gpt-5.6-terra`, the Codex worker tier. Startup and hook timing do not depend on the model.

---

## Task 1: Reproduce, measure and record

**Files:**
- Create: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe.sh`
- Create: `docs/reviews/2026-10-06-codex-spawn-repro-findings.md`
- Stage, not edited: the four loop docs listed in Step 5.

**No tests required.** This is an investigation with no product code.

**Step 1: write the probe script** to `<SP>/codex-repro/probe.sh`:

```bash
#!/bin/bash
# Usage: probe.sh A|B   (A = daemon's current cmd, B = adds --no-daemon)
set -u
V="$1"
SP=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro
REPO="$SP/repo"; OUT="$SP/run-$V"; LOG="$OUT/probe.log"
S="icrepro-$V"; MODEL=gpt-5.6-terra
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

TMUX_PATH=$(tmux show-environment -g PATH 2>&1); log "tmux_global_PATH=[$TMUX_PATH] default_shell=[$(tmux show-options -gv default-shell 2>&1)]"
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

# Phase 1: readiness, mirroring _wait_for_ready (orchestrator_mcp.py:2322-2383)
TRUST=no; HOOKS=no; READY_AT=""
while [ $(( $(date +%s) - START )) -lt 30 ]; do
  T=$(tmux capture-pane -p -t "$S" -S -50 | tr -d ' \n\t' | tr 'A-Z' 'a-z')
  if [ $TRUST = no ] && echo "$T" | grep -q 'doyoutrustthecontentsofthisdirectory'; then
    tmux send-keys -t "$S" Enter; TRUST=yes; log "dismissed trust"
  fi
  if [ $HOOKS = no ] && echo "$T" | grep -q 'hooksneedreview'; then
    tmux send-keys -t "$S" -- 2; HOOKS=yes; log "dismissed hooks review with 2"
  fi
  if echo "$T" | grep -q '>_openaicodex'; then READY_AT=$(( $(date +%s) - START )); break; fi
  sleep 1
done
log "ready_at=${READY_AT:-NOT_READY_30s}"; snap ready

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
for t in $(seq 0 5 300); do
  while [ $(( $(date +%s) - START )) -lt $(( ${READY_AT:-30} + t )) ]; do sleep 1; done
  rescan "t=$t"
  case " 10 30 60 120 240 300 " in *" $t "*) snap "id-$t";; esac
  [ -n "$ID_AT" ] && break
done
log "id_at=${ID_AT:-NONE_300s} id_pid=${ID_PID:-none} uuid=${ID_UUID:-none} any_at=${ANY_AT:-NONE_300s} any_pid=${ANY_PID:-none}"

# Phase 3: dispatch test, mirroring tmux_manager.send_keys (text, 0.2 s, Enter)
tmux send-keys -t "$S" -- '$ironclaude:write-lossless-ai-messages'; sleep 0.2; tmux send-keys -t "$S" Enter
sleep 10; snap dispatch-enter1; rescan after-enter1
tmux send-keys -t "$S" Enter
sleep 30; snap dispatch-enter2; rescan after-enter2
log "picker_after_enter1=$(grep -c 'enter insert' "$OUT/snap-dispatch-enter1.txt")"
log "marker_after_enter2_snap=$(grep -c IC_LOSSLESS_AI_MESSAGES_ACTIVE "$OUT/snap-dispatch-enter2.txt") marker_in_pane_log=$(grep -c IC_LOSSLESS_AI_MESSAGES_ACTIVE "$OUT/pane.log")"
# Phase 3b: one plain prompt — direct test of "SessionStart fires only on first submitted turn"
tmux send-keys -t "$S" -- 'Reply with the single word ok. Use no tools.'; sleep 0.2; tmux send-keys -t "$S" Enter
sleep 45; snap plain-turn; rescan after-plain-turn
log "post_dispatch id_at=${ID_AT:-NONE} any_at=${ANY_AT:-NONE}"

# Phase 4: state + cleanup by exact name
rescan before-kill
for u in $ID_UUID $ANY_UUID; do log "db_row[$u]=[$(sqlite3 -readonly "$HOME/.claude/ironclaude.db" "SELECT terminal_session, professional_mode, workflow_stage FROM sessions WHERE terminal_session='$u'" 2>&1)]"; done
log "codex_config_entries=[$(grep -n "$REPO" "$HOME/.codex/config.toml")]"
ALLPIDS=$(echo $ALLPIDS | tr ' ' '\n' | sort -u | tr '\n' ' '); log "allpids=[$ALLPIDS]"
tmux kill-session -t "$S"; log "killed session $S rc=$?"
sleep 3
for p in $ALLPIDS; do
  if ps -p "$p" > /dev/null; then log "SURVIVOR pid=$p [$(ps -p "$p" -o pid=,ppid=,lstart=,command=)]"; fi
done
log "codex_procs_after=[$(pgrep -fl -- codex)]"   # diff against codex_procs_before in the note; report only, never kill by pattern
log "done"
```

**Step 2: run variant A** in the background, with a Bash timeout of 900000 ms:

```bash
bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe.sh A
```

Expected: record what actually happened. When the run completes:
- read `run-A/probe.log` and the `snap-*.txt` files;
- record any `SURVIVOR` lines;
- kill a survivor only with `kill <that exact PID>`, and only if its command is a Codex process the probe launched.

Any Codex process that appears in `codex_procs_after` but not in `codex_procs_before` is reported, not killed.

**Step 3: run variant B** the same way, after Step 2 finishes:

```bash
bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe.sh B
```

Expected: record what actually happened, as in Step 2.

**Step 4: write the findings note** to `docs/reviews/2026-10-06-codex-spawn-repro-findings.md`.

For each variant, record:
- **Launch:**
  - the Codex path under Claude's PATH and under the tmux PATH;
  - the Codex version;
  - the command.
- **Readiness:**
  - `ready_at`;
  - which trust and hooks prompts were dismissed.
- **Id file:**
  - `id_at`, `id_pid` and `uuid`, which are the in-tree measurements the daemon would accept;
  - `any_at` and `any_pid`, the first id file seen from any process, with that process's `ps` line;
  - the results of the rescans after Enter 1, after Enter 2 and after the plain prompt.
- **Dispatch:**
  - any banner or modal text in the snapshots;
  - whether the picker appeared after Enter 1;
  - the marker counts in the snapshot and in `pane.log`.
- **Leftovers:**
  - the database rows;
  - the `config.toml` rows;
  - survivors;
  - the before/after diff of Codex processes.

Then add three sections:
- **Conclusions**, each marked verified or unresolved:
  - the root cause of slow or missing activation, including whether the id file appears only after a turn is submitted;
  - how dispatch submission behaves.
- **Inputs for the spawn-fix design.**
- **Leftover state**, covering id files, session rows, `config.toml` rows and any new processes.

**Step 5: stage the note and the loop docs:**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-06-codex-spawn-repro-findings.md docs/plans/2026-10-06-codex-spawn-repro-requirements.md docs/plans/2026-10-06-codex-spawn-repro-design.md docs/plans/2026-10-06-codex-spawn-repro.md docs/plans/2026-10-06-codex-spawn-repro.plan.json
```

Expected: no output, exit 0.
