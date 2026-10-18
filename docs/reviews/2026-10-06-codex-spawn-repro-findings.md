# Codex worker spawn reproduction: findings (2026-10-06)

Probe script: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe.sh`. Logs and snapshots are in `run-A/` and `run-B/` beside it. Variant A is the daemon's current command. Variant B adds `--no-daemon`.

Headline: in both variants the probe never got an id file, because both runs stalled on a Codex trust prompt that the daemon-mirroring dismissal code did not recognise. Neither run reached a normal Codex prompt. The variants therefore do not test what they were designed to test (id-file timing, `--no-daemon`, `$skill` dispatch). They do show a concrete startup failure, listed below.

## Variant A (no `--no-daemon`)

**Launch**
- Codex under Claude's PATH and under the tmux PATH: `/Users/roberthyatt/.local/bin/codex` for both.
- Version: `codex-cli 0.160.0`.
- Command: `export IC_ROLE=worker; export IC_WORKER_ID=icrepro-A; export ENABLE_STOP_REVIEW=0; exec codex --model gpt-5.6-terra  --dangerously-bypass-approvals-and-sandbox`.

**Readiness**
- `ready_at=1`. The 1 s snapshot (`snap-ready.txt`) shows the `>_ OpenAI Codex (v0.160.0)` banner and a `› Ask Codex to do anything` input line.
- No prompt was dismissed: `grep -c dismissed run-A/probe.log` gives 0.
- Every later snapshot (`snap-id-10` through `snap-id-300`) shows a modal instead: `Folder access ... Trust this folder? ... › 1. Trust and continue / 2. Back to Agent Command Center`.
- At 10 s and 120 s the modal names the parent directory `.../codex-repro`. At 300 s it names `.../codex-repro/repo`.

**Id file**
- Log line: `id_at=NONE_300s id_pid=none uuid=none any_at=NONE_300s any_pid=none`.
- `grep -c idfile probe.log` gives 0: no `ironclaude-session-*.id` file newer than the start reference appeared in 300 s.
- Rescans after Enter 1, after Enter 2 and after the plain prompt also found none. Final line: `post_dispatch id_at=NONE any_at=NONE`.
- Process tree: every `desc=[...]` line is `[45579]`, the pane shell only. No child process was ever seen under the pane PID. (UNRESOLVED why: `exec codex` replaces the shell, so the pane PID may itself be Codex, but this was not checked.)

**Dispatch** (the typed text and Enters landed on whatever modal was up, not on a prompt)
- After Enter 1: a `Working directory · resume` picker (`› 1. Use session directory (/Users/roberthyatt/.ironclaude/brain)`, `2. Use current directory (...)`). The `picker_after_enter1=0` line counts the string `enter insert`, which is not what this picker shows, so that count is not meaningful.
- After Enter 2: a `Folder access` trust prompt for `/Users/roberthyatt/.ironclaude/brain`.
- After the plain prompt: a `Hooks need review` modal (`7 hooks are new or changed`, `1. Review hooks / 2. Trust all and continue / 3. Continue without trusting`).
- Marker: `marker_after_enter2_snap=0 marker_in_pane_log=0`.

**Leftovers**
- `codex_config_entries=[]`: no `[projects.*]` row for the probe repo was written to `config.toml`.
- No database rows (there was no uuid to query).
- `killed session icrepro-A rc=0`.
- Survivors: none (no `SURVIVOR` lines).
- Codex process diff, before versus after: no new and no missing PIDs.

## Variant B (`--no-daemon`)

**Launch**
- Same Codex path and version as A.
- Command: `... exec codex --model gpt-5.6-terra --no-daemon --dangerously-bypass-approvals-and-sandbox`.

**Readiness**
- Log line: `ready_at=NOT_READY_30s`. The banner never matched; the screen at 30 s is the trust modal `Folder access ... .../codex-repro/repo ... › 1. Trust and continue / 2. Quit`.
- `grep -c dismissed run-B/probe.log` gives 0: the trust prompt was not dismissed.

**Id file**
- Log line: `id_at=NONE_300s id_pid=none uuid=none any_at=NONE_300s any_pid=none`. No id file appeared.
- Process tree: `desc=[60115]` throughout.

**Dispatch**
- `snap-dispatch-enter1`, `snap-dispatch-enter2` and `snap-plain-turn` contain only `can't find pane: icrepro-B`. The tmux session was gone before the 300 s poll ended.
- UNRESOLVED: I did not determine why it exited. The probe typed text and Enter into the trust modal, which may have chosen "Quit". The `killed session icrepro-B rc=1` line confirms the session no longer existed when the probe tried to kill it.
- `picker_after_enter1=0`, `marker_after_enter2_snap=0 marker_in_pane_log=0` (meaningless, since the pane was gone).

**Leftovers**
- `codex_config_entries=[]`.
- No database rows.
- Survivors: none.
- Codex process diff: no new and no missing PIDs.

## Conclusions

1. **VERIFIED (measured):** with Codex 0.160.0, in a fresh git repo the probe created, Codex shows a trust modal titled `Folder access` / `Trust this folder?`. `_wait_for_ready`-style detection did not dismiss it in either variant: `dismissed` appears in neither log. The probe's match strings are `doyoutrustthecontentsofthisdirectory` (trust) and `hooksneedreview` (hooks, after the same whitespace/case normalisation). The trust text on screen does not contain the first string. The hooks modal text `Hooks need review` normalises to `hooksneedreview`, but it only appeared after the readiness phase had ended, so it was not tested.
2. **VERIFIED (measured):** the readiness check `>_openaicodex` can match before the trust modal covers the screen. In A it matched at 1 s, which ended the readiness phase before the modal was dismissed. This is a false-ready.
3. **VERIFIED (measured):** while the trust modal is up, no `ironclaude-session-*.id` file is created, so a pane that sits on this modal produces exactly the symptom "PM activation never finishes". Both variants gave `id_at=NONE_300s`.
4. **VERIFIED (measured):** a typed `$ironclaude:write-lossless-ai-messages` followed by Enter does not reach a prompt while a modal is up. Text and Enters are consumed by modals (trust, then `Working directory · resume`, then trust for the brain directory, then `Hooks need review`). That tells us nothing yet about `$skill` submission at a real prompt.
5. **UNRESOLVED:** whether the id file appears only after a turn is submitted. No run reached a prompt, so this was not tested.
6. **UNRESOLVED:** whether `--no-daemon` changes anything. B never got past the trust modal either.
7. **UNRESOLVED:** how dispatch submission behaves at a real prompt (Enter 1 picker, Enter 2 marker). Not reached.
8. **UNRESOLVED:** why `--no-daemon` run B's session exited before the plain prompt step, and why A's trust modal named `codex-repro` at 10 s and 120 s but `codex-repro/repo` at 300 s.

## Inputs for the spawn-fix design

- The trust and hooks modal detection strings need re-checking against Codex 0.160.0 real text: `Folder access` / `Trust this folder?` / `Trust and continue`, and `Hooks need review` / `Trust all and continue`.
- Readiness must not treat the banner alone as ready while a modal is covering the input area. In A the banner and `› Ask Codex to do anything` showed at 1 s and the trust modal came after.
- The daemon dismisses modals only during the 30 s readiness phase (per the plan). A modal that appears later stays up until something else answers it.
- The re-run that answers the open questions needs the trust modal dismissed first, for example by pre-trusting the probe repo or by fixing the probe's match string. That re-run was not done; the task said to run Task 1 exactly.

## Leftover state

- Id files: none created by either run (`grep -c idfile` gives 0 in both logs).
- Session rows in `~/.claude/ironclaude.db`: none queried, no uuid obtained.
- `~/.codex/config.toml`: no row mentioning the probe repo (`codex_config_entries=[]` in both logs).
- tmux: `icrepro-A` killed (`rc=0`); `icrepro-B` already gone (`rc=1`). No `icrepro` session listed afterwards.
- New Codex processes: none in either run (before/after PID sets identical).
- The scratch repo is at `.../codex-repro/repo`.
