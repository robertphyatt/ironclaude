# Codex worker spawn reproduction v2: findings (run 2026-10-09)

Probe: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-repro/probe-v2.sh`. Logs and snapshots are in `run-v2-B/` and `run-v2-A/` beside it.

The variants are:
- **B:** `--no-daemon`. This is the Commander's current local worker command (`commander/src/ironclaude/orchestrator_mcp.py:564-571`, selected at `:641-642`). It ran first.
- **A:** daemon mode, as on the remote-machine path (`:769`).

Headline:
- **A second Enter submits the skill.** In production mode, a second Enter submits `$ironclaude:write-lossless-ai-messages`, and the profile marker appears.
- **The Commander's marker check would still miss it.** After the Commander's ANSI stripping, the marker is not a contiguous substring of the pane log.
- **The trust modal has changed.** It is now "Folder access", and only Enter dismisses it.
- **The id file at about 125 s was not the worker's.** Another session's prompt hook stamped it from the worker's pending marker (corrected conclusion 4).
- **Daemon mode is unusable for workers.** It stops on a modal Codex added in this version, and its id file belongs to ChatGPT.app's app-server, not to the worker.

## Codex under test

`codex` on both PATHs is `/Users/roberthyatt/.local/bin/codex`, and reports `codex-cli 0.162.0-alpha.17.2`. The pane's process is ChatGPT.app's bundled `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`. The 2026-10-06 production spawns ran `v0.160.0` (their pane logs show it), so the installed Codex has changed since.

## Variant B (`--no-daemon`, production)

Command: `export IC_ROLE=worker; export IC_WORKER_ID=icrepro2-B; export ENABLE_STOP_REVIEW=0; exec codex --model gpt-5.6-terra --no-daemon --dangerously-bypass-approvals-and-sandbox`. The pane PID was 32293. Because of `exec`, the pane PID is the Codex process itself.

**Modals and readiness.** VERIFIED.
- At t=1: `ready_flapped`. The banner and input line were drawn before the modal.
- At t=4: `modal=trust answered=1`.
- At t=6: `modal=trust still_up answered=Enter`. The digit `1` did not dismiss the "Folder access" trust modal; Enter did.
- `ready_at=11 modals_answered=2`.
- No hooks modal and no resume picker appeared.

**Id file.** VERIFIED.
- `ironclaude-session-32293.id` was written with mtime `1791591828`, which is **125 s after launch** (start `1791591703`) and about 114 s after readiness.
- It was found at `id_at=127 in_pane_tree=yes`.
- Its PID is the pane PID (Codex), so the Commander's descendant search would find it.
- No turn had been submitted at that point; Phase 3 had not started.
- The 1 s polls from t=0 to t=60 found nothing, and no other writer appeared (`any_at=127 any_pid=32293`).

**Id file content.** VERIFIED, cause UNRESOLVED.
- The first content was `7c7f63a0-e272-4e34-a05b-3d6ebc128c94`. That is the probe operator's own Claude Code session id; the probe shell exports `CLAUDE_CODE_SESSION_ID` with this value, and tmux passed it to the pane.
- After the skill turn was submitted (Enter 2), the file was rewritten (mtime `1791591844`) with the Codex thread id `01a1232f-fcd5-7502-a34b-619416dd7ad3`.
- No IronClaude hook reads `CLAUDE_CODE_SESSION_ID`: `rg` over `worker/hooks` and `state-manager/src` found nothing. So the startup payload's `session_id` came from Codex itself.
- Production launches come from the Commander daemon, whose environment has the Claude variables removed, so this exact leak is probe-specific. What Codex sends as `session_id` before a thread exists is UNRESOLVED.

**Dispatch.** VERIFIED.
- `picker_when_typed=1`: the skill picker (`enter insert`) is open after typing the text and 0.2 s.
- After Enter 1: `mention_after_enter1=1 marker_after_enter1=0`. The composer holds the unsubmitted `$ironclaude:write-lossless-ai-messages` mention.
- After Enter 2: `marker_after_enter2_snap=1`. The snapshot shows `• IC_LOSSLESS_AI_MESSAGES_ACTIVE`, `Worked for 6s`, and the footer `Write lossless AI messages`.
- `marker_in_pane_log=0`, using a raw grep.
- Through `ironclaude.tmux_manager._strip_ansi` (the Commander's `read_log_since` path), the marker count in `pane.log` is 0. With whitespace collapsed it is 1. The Commander's check, `marker in output` at `orchestrator_mcp.py:2347`, would therefore miss it.

**Plain prompt.** The rescan shows the same id file and the same Codex thread id.

**Leftover state.**
- DB row for the startup id: `7c7f63a0…|on|executing`. This is the operator's own session row, read only, not written by the probe.
- `config.toml` gained a `[projects."…/repo-v2-B"]` trust row.
- `killed session icrepro2-B rc=0`; no survivors.
- An episodic `sync-cli.js` (PID 39875) appeared in the after-list; it was started by the SessionStart sync hook.

## Variant A (daemon mode)

Command: as for B, but without `--no-daemon`. The pane PID was 51249.

**Modals and readiness.** VERIFIED.
- `ready_at=NOT_READY_90s modals_answered=0`.
- The screen showed a modal the probe does not know: `Background server has incompatible feature settings / This session requires api_key_model_discovery to be enabled / … 1. Run without daemon this time / 2. Restart with these settings / › 3. Cancel`. The installed shared daemon is `app-server-daemon/releases/0.160.0`; the worker CLI is 0.162.0-alpha.
- The probe deliberately did not answer it. Choosing "Restart" would have restarted the shared daemon used by other clients.

**Id file.** VERIFIED.
- No in-tree id file appeared (`id_at=NONE_300s`).
- The only one found, `ironclaude-session-84032.id`, was written at t≈3 with content `019f7d26-9faa-7e71-b7cf-ee2a2334b9f4` and `in_pane_tree=no`. PID 84032 is ChatGPT.app's `codex … app-server` (started 16:39:36). Its DB row reads `|on|design_ready`.
- A Commander search of the pane tree would never find it.

**Dispatch.** VERIFIED.
- The typed text landed on the modal (`picker_when_typed=0`).
- Enter 1 selected the default "3. Cancel" and the session exited: `can't find pane` in every later snapshot, `killed session … rc=1`.

**Leftover state.**
- Survivor PID 51284, `codex app-server daemon pid-update-loop` (0.160.0 daemon release), started 18:29:52, the probe's own start time. It was killed by exact PID (`kill -TERM 51284`, rc=0, then gone).
- No new trust row for `repo-v2-A`.

## Conclusions

1. **Q3, dispatch.** VERIFIED (B). The single Enter from `tmux_manager.send_keys` only inserts the skill from Codex's picker; a second Enter submits it and the skill runs. This matches production spawn 1 (2026-10-06), which ended with the mention unsubmitted.
2. **Marker detection.** VERIFIED (B). Even after it is submitted, the profile marker is not a contiguous substring of the ANSI-stripped pane log. Readiness has to match against whitespace-compacted text, or read `capture-pane`.
3. **Trust modal.** VERIFIED (B). Codex 0.162 shows "Folder access … Trust this folder?". `_wait_for_ready` matches `doyoutrustthecontentsofthisdirectory`, which this text does not contain. The digit `1` did not dismiss it; Enter did.
4. **Q2, id-file timing.** CORRECTED (2026-10-10).
   - The worker's state-manager wrapper started at about 7 s with `PPID=32293`. It found no session file and wrote `~/.claude/ironclaude-ppid-pending-32293` (`~/.claude/ironclaude-mcp-state-manager.log:43628-43629`).
   - The probe operator's own Claude session then ran `state-activator.sh` (UserPromptSubmit). Its handshake (`worker/hooks/state-activator.sh:48-64`) claims every global pending marker, so it wrote its own session id into `ironclaude-session-32293.id`. That write is the file seen at 125 s.
   - Current Codex runs SessionStart, and so `session-init.sh` and the worker's own id file, only on the first submitted turn. The worker's SessionStart rewrote the file with its thread id only after Enter 2.
   - The 125 s is therefore the timing of another session's prompt, not a Codex startup delay. The "only after a submitted turn" hypothesis holds.
5. **Cause of the ~2-minute delay.** VOID. Corrected conclusion 4 shows there is no Codex startup delay to explain. The `node_repl` hypothesis is withdrawn, and no MCP server needs disabling.
6. **`--no-daemon`.** VERIFIED. It is required. Daemon mode stops on the incompatible-settings modal (0.162 CLI against a 0.160 shared daemon). Even past that, its SessionStart runs in the shared app-server, so the id file is out of the worker's process tree and carries another session's identity.
7. **Startup session id.** RESOLVED (2026-10-10). The first content, `7c7f63a0…`, was written by the operator's Claude session through the pending-marker handshake, not by Codex. The worker's own id file holds its Codex thread id only after its first submitted turn. Binding must therefore wait for that turn and accept only an id file written after it, and the handshake must claim only its own client's marker.

## Relation to the production failures (2026-10-06)

- **Spawn 1** (`ic-d1501-r4-blind-review`): activation finished in about 2 minutes. Corrected conclusion 4 no longer explains that timing; this probe did not determine its cause.
- **Spawn 2** (`…-2`): it sat at an idle prompt after an update banner, and no id file was found in 300 s. This fits corrected conclusion 4: the Commander activated before any turn was submitted, so the worker never ran SessionStart and never wrote its own id file. That run is gone, so its exact cause is not verified.
- **Spawn 3**: the retry under the same worker id was refused by the preserved assignment (`_workspace_failure` → `assignment_preserved: True`, `orchestrator_mcp.py:3028`). This needs no probe.

## Inputs for the spawn-fix design

1. **Codex skill dispatch.** After the `$…` text and Enter, wait for the picker to close, then send a second Enter. Only Codex needs this; Claude's `/…` stays single-Enter.
2. **Marker readiness.** Match `PROFILE_READY_MARKER` against whitespace-compacted stripped output (`_wait_for_ready` already compacts for the Codex banner), or against `capture-pane`.
3. **Trust modal.** Recognise `Folder access` / `Trust this folder?` and answer with Enter. Keep handling modals during PM activation, not only in the 30 s readiness window.
4. **Spawn order (corrected).** No startup delay exists (corrected conclusion 4), so no MCP server needs disabling. For Codex, send the profile turn before waiting for the session id, because the worker writes its own id file only on its first submitted turn.
5. **Session binding (corrected).** Accept only an id file written after the profile turn was dispatched, and make `state-activator.sh` claim only its own client's pending marker. Then no other session can stamp the worker's PID.
6. **Worker-id reservation.** Release the reservation when a spawn fails before any work starts (no objective dispatched), so a retry can reuse the id.
7. **Keep `--no-daemon`.**

## Leftover state

- tmux: `icrepro2-B` killed (rc=0); `icrepro2-A` had already exited (rc=1).
- Survivor 51284 killed by exact PID. No other survivors.
- `~/.codex/config.toml` has a new trust row for `repo-v2-B`.
- The probe wrote no `ironclaude.db` rows. It only read the operator's session row and ChatGPT.app's app-server row.
- Scratch repos: `repo-v2-B`, `repo-v2-A` under `codex-repro/`.
