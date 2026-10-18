# Codex Worker Spawn Fix Design

> **Created:** 2026-10-10
> **Status:** Design Complete

Loop: full — worker lifecycle and workspace-reservation release (git/worktree operations), a hook shared by both clients, and several Commander files.

## Requirements

**Operator directives**
- 2026-10-09: "we need it working as codex. that was the point of the shift. Instead of starting it in claude, let's actually fix it so that it can run in codex ... do a pm loop vs this now".
- Ordering decision: "Profile skill first (Rec.)", which sends the `$ironclaude:write-lossless-ai-messages` turn before waiting for the session ID. Claude workers keep today's order.
- Section 1 approved: "Yes (Rec.)". Section 2 approved: "Yes (Rec.)".

**Evidence**
- `docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md` (run 2026-10-09, codex-cli 0.162.0-alpha.17.2). It is corrected by the source facts below.
- `/tmp/ic-logs/ic-d1501-r4-blind-review*.log` (production, 2026-10-06).
- `~/.claude/ironclaude-mcp-state-manager.log:43628-43629`.

**Corrected source facts**
- Current Codex runs SessionStart, and so `session-init.sh` and the worker's own id file, only on the first submitted turn. In the probe, the first id-file write (at 125 s) came from the probe operator's own Claude session.
  - The worker's state-manager wrapper wrote `ironclaude-ppid-pending-32293` at about 7 s. The log reads `[ppid] No session file found — pending marker written`.
  - That session's `state-activator.sh` (UserPromptSubmit) handshake at `:48-64` then claimed every global pending marker and wrote its own session id.
  - The worker's own SessionStart rewrote the file with its thread id only after the first submitted turn (Enter 2).
  - Findings conclusions 4, 5 and 7 are therefore wrong, and the `node_repl` delay hypothesis is void.
- The July subtree search (`_set_pm_via_sqlite` codex branch, `orchestrator_mcp.py:2473-2486`) stays.
- `--no-daemon` stays.

**Requirements**
- **R1. Codex spawn order.** For Codex workers, in both `spawn_worker` and the batch path `_start_batch_item`, the order is: launch → readiness and trust → profile skill turn → wait for the worker's own session id → professional mode on → identity verification → workspace bind → objective.
  - The profile dispatch's existing failure handling (kill the session, `dispatch` failure) moves with it.
  - Remote Codex (`_activate_pm_remote`) follows the same order.
  - Claude order and behaviour are byte-for-byte unchanged.
- **R2. Codex skill submit.** `_dispatch_worker_communication_profile` for Codex sends the invocation and Enter. It then sends a second Enter only while the picker (`enter insert`) or the unsubmitted `$ironclaude:write-lossless-ai-messages` mention is still on screen. Claude keeps its single Enter.
- **R3. Marker match.** When `_wait_for_ready` has a `marker`, it also matches the whitespace-compacted output, as it already does for the Codex banner, so that `IC_LOSSLESS_AI_MESSAGES_ACTIVE` split by cursor movement is detected. Absent text still does not match. In marker mode, the Codex banner fallback no longer counts as ready: only the marker does.
- **R4. Trust prompt.** The Codex readiness path also recognises `Folder access` / `Trust this folder?` and answers it with Enter. The existing match string stays. Because Codex draws its banner before the modal, readiness requires the banner to appear after the last modal text. A modal check runs again just before the profile turn is typed.
- **R5. Session binding.** For Codex, `_activate_pm_via_sqlite` and `_read_pm_state_via_sqlite` use `not_before` = the time the profile turn was dispatched. A file written or stamped before the worker's own turn is never accepted.
- **R6. No cross-session stamping.** `state-activator.sh` claims only `ironclaude-ppid-pending-<its own real PPID>`, derived as `session-init.sh` derives `REAL_PPID`. Other PIDs' markers are left untouched. This applies to both clients.
- **R7. Name release on pre-work failure.** When a spawn fails before the worker is registered and sent its objective, the Commander releases the workspace reservation so that a retry under the same worker id succeeds.
  - **Unbound reservation** (no owner): release it with the existing `reap`. `abandon` cannot be used here, because the CLI rejects an empty owner (`cli.ts:66-69`) and the service compares owners strictly (`workspace-service.ts:514`).
  - **Bound reservation** (owner known): release it with `abandon` in `rescue` mode.
  - **Stays preserved, as today:**
    - a bind failure, where the server-side owner is unknown and may be another session;
    - a failure at or after the Stage 6 registry write, where a registry row may already point at the assignment.
  - A failed release is logged and the original failure is still returned.
- **R8. Findings correction.** Correct conclusions 4, 5 and 7, the "Inputs" list, the headline and the production-failure lines in `docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`, so the note does not contradict itself.
- **R9. Verification.**
  - TDD unit tests per requirement, then the full Commander pytest (`PYTHONUNBUFFERED=1`) and `make test-hooks`.
  - One live Codex worker spawned through the product `spawn_worker` code against a scratch repo. It must show:
    - professional mode `on` for the worker's own thread id (not any other session's);
    - the profile marker detected;
    - the objective delivered.
  - Cleanup by exact session name.
  - A final Fable review before commit.

- **R10. Remote Codex binding.** `_activate_pm_remote` accepts `not_before` for Codex. It only takes a remote `ironclaude-session-<pid>.id` whose modification time on the remote host is at or after the profile dispatch time. The remote stat must work on both macOS (`stat -f %m`) and Linux (`stat -c %Y`). If no stat is available, the file is rejected (fail closed). The spawn passes the dispatch time, as R5 does locally.
- **R11. Codex resume.** `resume_session` with `client == "codex"` follows the R1 order: readiness and trust → profile turn (R2) → wait for the id file with `not_before` = dispatch time → professional mode on → identity check. The identity check must also confirm that the bound uuid equals the `session_id` being resumed, or the resume fails closed. Claude resume is unchanged.

Operator decision (2026-10-10): the amended R3, R4 and R7, plus "Fix L1/L2 too", which turned the former limitations into R10 and R11.

**Known limitation (recorded)**
- **L3.** R6 cannot be exercised by the live spawn until the plugin is redeployed, because the worker runs the installed plugin's hooks. Its unit and hook tests cover it.

## Architecture

The changes stay local to the spawn sequence in `commander/src/ironclaude/orchestrator_mcp.py` and to one hook.

- Codex dispatch moves ahead of activation in both spawn paths, behind `worker_client == "codex"`.
- `_dispatch_worker_communication_profile` gains a Codex submit-confirmation branch.
- `_wait_for_ready` gains the compacted marker match and the new trust text.
- `worker/hooks/state-activator.sh` scopes its handshake to its own PPID.
- The pre-objective failure exits call one small release helper that wraps `self._workspace_client.abandon(..., mode="rescue")`.

No schema, MCP-tool or workspace-manager changes are made.

## Error Handling

- **Second Enter.** It is sent only if the picker or mention is still visible. If the marker does not arrive within 30 s, today's `dispatch` failure applies.
- **Session wait.** If no id file newer than the dispatch appears within `pm_timeout`, today's activation failure applies and the session is killed.
- **Release.** It applies only before the objective is sent. Release errors are logged and never mask the original failure.
- **Markers.** Foreign markers are left in place, so each is consumed only by its own session.

## Testing Strategy

- **Unit tests (fakes, no live tmux):**
  - Codex profile-before-activation ordering, and `not_before` = dispatch time.
  - Claude ordering unchanged.
  - Second Enter sent only while the picker or mention is visible.
  - Compacted marker positive and negative cases.
  - `Folder access` trust answered with Enter.
  - Pre-registration failure releases (`reap` when unbound, `abandon(rescue)` when bound), and a same-id retry is accepted. Bind and registry-write failures preserve.
  - Release error does not mask the failure.
  - Remote Codex activation rejects an id file older than `not_before`, accepts a newer one, and rejects when no stat is available (R10).
  - Codex resume dispatches the profile before activation, and fails closed when the bound uuid is not the resumed `session_id` (R11). Claude resume is unchanged.
- **Hook test:** a foreign marker is untouched and the own marker is consumed. Update `worker/hooks/tests/test-state-activator-single-object.sh` where it plants `ironclaude-ppid-pending-424242`.
- **Then:** the full suites and the R9 live spawn.
