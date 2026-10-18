# Codex Hook Probe Findings (v1.1.16 L2a)

Date: 2026-10-02. Plan: `docs/plans/2026-10-02-codex-hook-probe.md`, Task 1.
SCRATCH = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe`; PROBE = its parent directory.

## Summary

| Q | Answer | Evidence class |
|---|---|---|
| Q1 | IronClaude plugin hooks run on Codex under the current trust state (no bypass). The professional-mode guard blocked a shell command in an undecided session. | observed |
| Q2 | Shell command: `tool_name` = `Bash`. Edit: not captured. | Bash observed; edit unanswered (the guard blocked the Bash call, the model replied DONE and never attempted an edit) |
| Q3 (Codex) | Stop input keys: `cwd, hook_event_name, last_assistant_message, model, permission_mode, session_id, stop_hook_active, transcript_path, turn_id`. No `agent_transcript_path`, no `agent_id`. | observed |
| Q3 (Claude) | Claude Code's Stop input carries `last_assistant_message`. | documented |
| Q4 | A Stop `systemMessage` was not visible in `codex exec --json` output or in the rollout. | observed-negative for exec/rollout only; whether the Codex UI shows it is unanswered (not tested; exec is headless) |

## 1. Evidence already in hand (from the plan header)

- Rollout `~/.codex/sessions/2026/10/02/rollout-2026-10-02T22-58-43-01a10020-fac1-7b31-b868-98f5fd1d3ed4.jsonl` contains `PROFESSIONAL MODE NOT SET` 2 times; the guard ran as a Codex PreToolUse hook today.
- Operator screenshots at 23:07 show IronClaude hooks switched on in Codex Settings > Hooks: PreToolUse 1-6, PostToolUse 1-6, Stop 1, SessionStart 1-3, UserPromptSubmit 1-2, no untrusted badge.
- In those screenshots Stop runs `bash $HOME/.claude/ironclaude-hooks/get-back-to-work-claude.sh`, and SessionStart Hook 1 runs the versioned cache path `.../1.1.15+codex.20260930172837/hooks/session-init.sh`.

Step 1 history commands (run by this probe):

- `grep -r -l -F "hook_prompt" .../sessions/2026/09 .../sessions/2026/10` listed many rollouts from 2026-09-02 through 2026-09-26 (first: `2026/09/03/rollout-2026-09-03T13-45-36-...`; newest listed: `2026/09/26/rollout-2026-09-26T21-58-50-01a0e103-fc90-73e1-a0ed-57271fac3fad.jsonl`). None under `2026/10`.
- `grep -r -o -E "ironclaude/[0-9.]+(\+codex\.[0-9]+)?/hooks/hooks.json"` (filenames kept) found plugin hooks.json paths for many versions. Newest file per version (selected lines):
  - `1.1.15+codex.20260930172837` -> `2026/09/30/rollout-2026-09-30T18-44-23-01a0f4eb-6649-7e03-a516-a9834e15a38a.jsonl`
  - `1.1.13+codex.20260926152651`, `1.1.12+codex.20260923220118` -> `2026/09/26/rollout-2026-09-26T21-58-50-01a0e103-fc90-73e1-a0ed-57271fac3fad.jsonl`
  - `1.1.11+codex.20260922004058` -> same 09-26 rollout; `1.1.11+codex.20260912072838` -> `2026/09/25/rollout-2026-09-25T23-53-32-...`
  - `1.1.10+codex.20260912072838` -> `2026/09/16/rollout-2026-09-16T15-55-28-...`
  - `1.1.9+codex.20260912072838` -> `2026/09/12/rollout-2026-09-12T01-22-27-...`
  - `1.1.8+codex.*`, `1.1.7+codex.*`, `1.1.6+codex.*`, `1.1.3`, `1.1.2`, `1.0.23`: earlier rollouts, 2026-09-01 .. 2026-09-26. Some versions (1.1.7/1.1.8/1.1.9) also appear in `2026/10/02/rollout-2026-10-02T23-15-10-01a10030-...`, a rollout that quotes older paths (this probe's own listing output), not evidence of those versions running.
  - Caveat: a path string in a rollout shows the path was mentioned (e.g. in a listing or prompt), not that the hook ran.
- `grep -r -l -F "PROFESSIONAL MODE NOT SET" .../sessions/2026/10` returned two rollouts: `rollout-2026-10-02T22-59-52-01a10022-07f7-7e12-92d4-4bc73250e23a.jsonl` and `rollout-2026-10-02T22-58-43-01a10020-fac1-7b31-b868-98f5fd1d3ed4.jsonl`.

## 2. Q1-Q4

### Q1: do IronClaude hooks run on Codex, and in what trust state?

Answer: yes, under the current persisted trust state with no bypass. Evidence class: observed (Turn A).

Turn A command (no `--dangerously-bypass-hook-trust`, `IRONCLAUDE_PM` unset):

```
codex exec -C $SCRATCH --skip-git-repo-check -s workspace-write --json --output-last-message $PROBE/turnA-last.txt "Run exactly one shell command: echo ironclaude-hook-probe. If it is blocked, do not retry; reply DONE." > $PROBE/turnA.jsonl 2>&1
```

Exit 0. Quoted `turnA.jsonl`:

```
2026-10-03T05:17:33.470975Z ERROR codex_core::tools::router: error=Command blocked by PreToolUse hook: ✗ [professional-mode-guard|01a10032-0f03-7aa3-8920-6512779b181d]: Blocked - BLOCKED — PROFESSIONAL MODE NOT SET
... Command: echo ironclaude-hook-probe
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"DONE"}}
```

Rollout: `/Users/roberthyatt/.codex/sessions/2026/10/02/rollout-2026-10-02T23-17-23-01a10032-0f03-7aa3-8920-6512779b181d.jsonl` (found by the `cwd` grep). The echo did not run. Session row for that thread in `ironclaude.db`: `professional_mode=undecided, workflow_stage=idle`.

Caveat: during Turn A (started ~23:17:23) the scratch `.codex/hooks.json` was written (23:17, prepared in parallel). No `capture/` directory existed after Turn A, so the scratch hook did not run in Turn A. The block message names `professional-mode-guard`, so the plugin hook is the source.

Turn B used the bypass flag, so Q1 rests on Turn A alone. Turn B also showed the same block (see Q2).

Implication for L2b: Codex runs the installed plugin PreToolUse guard now; the Codex hook port is not a from-scratch problem. The L2b work is correctness of hook input/output fields (Q2-Q4), not enabling hooks.

### Trust-state table (config.toml lines 140-230, read before the probe)

Hash scheme: unknown (not derivable from the config; recorded as `trusted_hash = sha256:...`). `[features]` (config.toml:102-104) holds only `js_repl = false` and `memories = true`: no `hooks=false`. `~/.codex/hooks.json`: `ls: No such file or directory`.

Keys are `ironclaude@ironclaude:hooks/hooks.json:<event>:<group>:<hook>`:

| Key | enabled | trusted_hash (sha256, prefix) | Note |
|---|---|---|---|
| pre_tool_use:0:0 | true | c523653c... | |
| pre_tool_use:1:0 | true | 9d8b4278... | |
| pre_tool_use:2:0 | true | 630923ab... | |
| pre_tool_use:3:0 | true | 5c6afdbc... | |
| pre_tool_use:4:0 | true | fa8ac5d5... | |
| pre_tool_use:5:0 | absent | b3098603... | no `enabled`; on in UI per screenshots, so absent = on |
| post_tool_use:0:0 | true | 26bd3baf... | |
| post_tool_use:1:0 | true | 7b02c33f... | |
| post_tool_use:2:0 | true | 1b6e7b5c... | |
| post_tool_use:3:0 | true | 8fa099bc... | |
| post_tool_use:4:0 | true | 5d655ad4... | |
| post_tool_use:5:0 | true | 4bb9a559... | |
| session_start:0:0 | true | afa7372c... | |
| session_start:1:0 | absent | 1350dc00... | no `enabled`; this is SessionStart Hook 1 (versioned cache path) |
| session_start:2:0 | true | 44305334... | |
| user_prompt_submit:0:0 | true | 110bfc84... | |
| user_prompt_submit:1:0 | true | a22e2c70... | |
| user_prompt_submit:2:0 | absent | 1c5533c3... | no `enabled`; stale: only 2 UserPromptSubmit groups exist in hooks.json |
| subagent_stop:0:0 | absent | 54eeef18... | no `enabled` |
| stop:0:0 | absent | 1ba9b4d5... | no `enabled` |

Also present: `/Users/roberthyatt/Code/roleplaying-agents/.codex/hooks.json:pre_tool_use:0:0` (hash 77558863..., no `enabled`).

The 5 keys without `enabled`: `pre_tool_use:5`, `session_start:1`, `user_prompt_submit:2`, `subagent_stop:0`, `stop:0`. Turn A shows PreToolUse running with these persisted hashes, so trusted_hash entries are sufficient for a hook to run.

### Q2: PreToolUse `tool_name` for a shell command and for an edit

Answer: shell command = `Bash` (observed). Edit = unanswered: no edit tool call was made, so no capture exists.

Turn B command (scratch repo `.codex/hooks.json` with PreToolUse `matcher: "*"` and Stop entries running `capture.sh`; `--dangerously-bypass-hook-trust`):

```
codex exec -C $SCRATCH --skip-git-repo-check -s workspace-write --json --dangerously-bypass-hook-trust "Run exactly one shell command: echo ironclaude-hook-probe. Then create probe.txt containing hi. If anything is blocked, do not retry; reply DONE." > $PROBE/turnB.jsonl 2>&1
```

Exit 0. The plugin guard blocked the echo (same `PROFESSIONAL MODE NOT SET` message, thread `01a10032-8662-7e52-9dbd-a3cc0619109b`), the model replied `DONE`, and `probe.txt` was never created (`ls $SCRATCH` shows only `capture/`).

```
$ jq -r .tool_name $SCRATCH/capture/PreToolUse-1791004682-79177.json
Bash
$ cat $SCRATCH/capture/PreToolUse-*.json
{"session_id":"01a10032-8662-7e52-9dbd-a3cc0619109b","turn_id":"01a10032-871f-7f90-81d2-93f9336ca336","transcript_path":"/Users/roberthyatt/.codex/sessions/2026/10/02/rollout-2026-10-02T23-17-53-01a10032-8662-7e52-9dbd-a3cc0619109b.jsonl","cwd":"<SCRATCH>","hook_event_name":"PreToolUse","model":"gpt-6-astra","permission_mode":"bypassPermissions","tool_name":"Bash","tool_input":{"command":"echo ironclaude-hook-probe"},"tool_use_id":"exec-e977bc54-0c96-4d4c-b513-7b31254d6660"}
```

Only one PreToolUse capture exists. Note the capture hook ran even though the plugin guard blocked the call, so the capture does not depend on the guard allowing the tool. Answer taken from `tool_name` in the captured hook input, not from the rollout's model-facing `"name"`.

Implication for L2b: Codex sends `Bash` for shell, matching Claude's name, so Bash matchers need no rename. The edit name (the plan expected `apply_patch`) is still unknown: a follow-up probe needs a run where the guard does not block, e.g. with professional mode decided, or a capture-only hook set. Do not assume `Edit|Write` matchers cover Codex edits.

### Q3: Stop hook input fields

Codex answer: observed. `jq keys` on `$SCRATCH/capture/Stop-1791004685-79299.json`:

```
["cwd","hook_event_name","last_assistant_message","model","permission_mode","session_id","stop_hook_active","transcript_path","turn_id"]
```

Full input: `{"session_id":"01a10032-8662-...","turn_id":"01a10032-871f-...","transcript_path":"<rollout path>","cwd":"<SCRATCH>","hook_event_name":"Stop","model":"gpt-6-astra","permission_mode":"bypassPermissions","stop_hook_active":false,"last_assistant_message":"DONE"}`.

Versus the documented field list (`permission_mode`, `agent_transcript_path`, `stop_hook_active`, `agent_id`, `last_assistant_message`): present = `permission_mode`, `stop_hook_active`, `last_assistant_message`; absent = `agent_transcript_path`, `agent_id` (these may be subagent-only; this was a top-level turn, so absence is not proof they are never sent).

Claude half: documented. WebFetch of https://code.claude.com/docs/en/hooks: "Hooks that need the final assistant text of the current turn should use `last_assistant_message` on [Stop] and [SubagentStop] instead of reading the transcript". The Stop section with the full field table was truncated in the fetch, so the full Claude field list is not quoted here.

Implication for L2b: `last_assistant_message` is available on both clients, so the stop-hook logic can read it instead of parsing the transcript on both. Keep `transcript_path` as the fallback; do not require `agent_id` / `agent_transcript_path` on Codex top-level turns.

### Q4: is a Stop hook's `systemMessage` shown?

Answer: not visible in headless exec output or the rollout. Evidence class: observed-negative for those surfaces; Codex UI display is unanswered (exec is headless, not tested).

The capture hook ran for Stop (a `Stop-*.json` exists) and its stdout was `{"systemMessage":"IC-PROBE-STOP-SYSMSG"}`.

```
$ grep -r -l -F "IC-PROBE-STOP-SYSMSG" $PROBE/turnB.jsonl /Users/roberthyatt/.codex/sessions/2026/10
(no output)
$ grep -c -i systemMessage <Turn B rollout> $PROBE/turnB.jsonl
0
0
```

Implication for L2b: do not rely on `systemMessage` to surface Stop-hook text to the operator on Codex. Use a mechanism observed to reach the model/operator (the PreToolUse block text did appear in exec output), or probe the interactive UI separately.

## 3. Side effects

- `~/.codex/config.toml` diff (`diff $PROBE/config.before.toml ~/.codex/config.toml`, exit 1). Changed by Codex during the probe; recorded, not fixed:
  ```
  1845a1846,1848
  > [projects."/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe"]
  > trust_level = "trusted"
  >
  ```
  No residual `hooks.state` change. The scratch project was auto-trusted by `codex exec`. The scratch path entry is now persisted in the global config and is the operator's to clean up.
- Session rows in `~/.claude/ironclaude.db` `sessions` (column `terminal_session`): two rows inserted, both `professional_mode=undecided, workflow_stage=idle`: `01a10032-0f03-7aa3-8920-6512779b181d` (Turn A, updated_at 2026-10-03 05:17:24) and `01a10032-8662-7e52-9dbd-a3cc0619109b` (Turn B, 05:17:55).
- `~/.claude/ironclaude-session-*.id`: one new file, `ironclaude-session-77451.id` (23:17:56), content `01a10032-8662-7e52-9dbd-a3cc0619109b` (Turn B thread). Turn A created no new file.
- `sync-cli.js`: exact process listing (`ps -axo pid,lstart,command | grep -F sync-cli.js`) before and after shows the same single process, PID 39718, started 23:12:56 (before the probe), cache path `1.1.15+codex.20260930172837`. No new sync-cli.js process was launched by the probe. Nothing was killed.
- Scratch files only under PROBE (scratch repo, `.codex/capture.sh`, `.codex/hooks.json`, `turnA.jsonl`, `turnB.jsonl`, `turnA-last.txt`, `config.before.toml`, `baseline-*.txt`). Two rollouts were created in `~/.codex/sessions/2026/10/02/` (`23-17-23-01a10032-0f03...`, `23-17-53-01a10032-8662...`).

## 4. L2b candidates

1. SessionStart Hook 1 runs the versioned cache path (`.../1.1.15+codex.20260930172837/hooks/session-init.sh`). Every plugin version bump changes the path, and trust is keyed by hook hash in `config.toml`; a stale path or hash risks the hook silently going untrusted or missing. Candidate: resolve a stable path (or `$HOME/.claude/ironclaude-hooks/...` like Stop) for SessionStart Hook 1, and verify trust survives upgrades.
2. Edit tool name on Codex is unobserved (Q2). Run a follow-up probe where the guard does not block, then align matchers (`hooks.json:68`).
3. Stop `systemMessage` is not visible in exec/rollout (Q4): choose a delivery path that is.
4. Use `last_assistant_message` from Stop input on both clients (Q3); keep `transcript_path` fallback.
5. Stale trust keys with no `hooks.json` counterpart (`user_prompt_submit:2`) and five keys lacking `enabled`: candidate cleanup, deferred to the operator since global config is out of scope here.
6. `codex exec` persists a `[projects."<cwd>"] trust_level = "trusted"` entry in the global config for every new working directory: any automated probe leaves residue in `config.toml`.
