# Codex Brain Parity Design

> **Created:** 2026-10-10
> **Status:** Design Complete

Loop: full. This changes enforcement hooks, the guard/hook-logger path, and the Commander's Brain start-up. It touches roughly 15 files.

## Requirements

### Operator decisions (2026-10-10)

- "Fix the commander first so that it can actually do work while we do stuff here."
- "We should have parity between the Claude version of the brain/Commander and the codex version of it. The Claude version is supposed to follow the brain/commander restrictions/permissions, *not* the normal worker version."
- Scope: "Both now (Rec.)". The worker hooks stand down in the Codex Brain, and the Codex Brain runs the Brain restriction set.
- Approach: "A: in plugin (Rec.)". The Brain hooks ship in the trusted IronClaude plugin `hooks.json`, gated to the Codex Brain identity.
- Hook trust: "A: Commander warm-up (Rec.)".
- Local-only scripts: "A: Adopt into repo (Rec.)".
- Parity level: "A: Intended rules, both (Rec.)". Both Brains enforce the intended rule set, and each rule has exactly one owner.
- Design approval: "Yes, write it (Rec.)".

### Verified facts

**F1. Why the Brain gave its answer.** The 15:27 Codex Brain was blocked by the worker `professional-mode-guard`.
- Rollout `~/.codex/sessions/2026/10/10/rollout-2026-10-10T15-27-24-01a127b6-…jsonl:19` shows the message "PROFESSIONAL MODE NOT SET".
- The Brain then answered the 16:09 question with no tool call at all (lines 28-40).
- The orchestrator MCP passed its start-up check (`codex_brain_client.py:1153-1158`, `daemon.log:4440`).
- So "worker-status access is unavailable" was the Brain generalizing from that block, not an MCP fault.

**F2. Why worker hooks run in the Codex Brain.** The Codex Brain loads the global IronClaude Codex plugin, so worker hooks run there.
- The Claude Brain uses `setting_sources=["project","local"]` (`brain_client.py:795`), which loads no plugin hooks.
- Hook env reaches the Codex Brain's hooks: `/tmp/ic/codex-brain-gate/0262b858…/lookback-ledger` was written on 10-06.

**F3. The Codex Brain's own hooks have never run.**
- `~/.ironclaude/brain/.codex/hooks.json` has no `[hooks.state]` trust entry in `~/.codex/config.toml`.
- Untrusted hooks are skipped silently.

**F4. How Codex hook trust works.**
- Trust is stored per entry, keyed `ironclaude@ironclaude:hooks/hooks.json:<event>:<group>:<handler>` with a `trusted_hash`.
- Appending entries keeps the existing keys. Inserting entries shifts them.
- What the hash covers is unknown: the design-stage guard blocked hashing.
- The Codex plugin uses `worker/hooks/hooks.json`.

**F5. The Claude Brain's live enforcement.**
- The Claude Brain runs `permission_mode="bypassPermissions"` (`brain_client.py:787`). Its in-process `_tool_guard_logic` (`:358`) never runs, as `main.py:5704` notes.
- Its live layer is shell hooks only:
  - from `settings.json`: block-push, memory-search-enforcer, wiki-synthesis-enforcer, attention-sweep-enforcer, startup-lookback-enforcer, brain-task-gate, attention-sweep-arm, block-pin-enforcer;
  - from `settings.local.json`: brain-orchestrator-guard.
- `commander/hooks/brain-hooks.json` (Stop hooks) is loaded by nothing, so it is out of scope.

**F6. Four scripts are not in the repo.** They exist only on this machine, and the repo has only ever referenced them by name (`git log -S`):
- `~/.ironclaude/brain/hooks/block-push.sh`
- `~/.ironclaude/brain/hooks/block-pin-enforcer.sh`
- `~/.claude/ironclaude-hooks/attention-sweep-enforcer.sh`
- `~/.claude/ironclaude-hooks/attention-sweep-arm.sh`

**F7. Codex hook tool names.**

| Tool | Name | Status |
|---|---|---|
| Shell | `Bash` (`.command`) | captured |
| Shell | `exec_command` (`.cmd`) | guard code |
| Edit | `apply_patch` | guard code |
| MCP | `mcp__<server>__<tool>`, with hyphens in the server name becoming `_` | captured |
| Subagent | `spawn_agent` | listed in the Brain's tools, payload not captured |

**F8. Script gaps under Codex payloads.**
- `brain-orchestrator-guard` misses `apply_patch` and `exec_command`.
- `block-push` misses `exec_command`.
- `memory-search-enforcer` arms only on `mcp__episodic-memory__*`. Codex names it `mcp__episodic_memory__*`.
- `brain-task-gate` misses `spawn_agent`.

### Requirements

**R1. One rule set with single owners.**
- `worker/hooks/codex-brain-gated-actions.sh` becomes `brain-gated-actions.sh`. It enforces for any Brain where `IC_ROLE=brain`, on both clients. Its rules:
  - Lookback: Slack messages at least 48h back, plus a ledger update.
  - An episodic memory search and a wiki query before each gated action.
  - Ledger staleness.
  - Game-tool ban.
- `AskUserQuestion` joins its gated set.
- Its memory arm matches `episodic[-_]memory`, with or without the plugin prefix.
- `memory-search-enforcer` and `startup-lookback-enforcer` are retired from both Brains, because `brain-gated-actions` now owns their rules.
- The Claude Brain gets `IRONCLAUDE_BRAIN_GATE_SESSION` in its environment.

**R2. Adopt the local-only scripts.**
- The four F6 scripts move into `commander/hooks/` unchanged.
- Then `block-push` accepts `exec_command` and `.tool_input.cmd`.

**R3. Fix the Codex payload gaps.**
- `brain-orchestrator-guard` blocks `apply_patch`.
- It treats `exec_command` (`.cmd`) as Bash, under the same git allowlist.
- `brain-task-gate` blocks `spawn_agent`, because the Codex Brain has no memory-search agent type.
- Behavior on Claude tool names is unchanged.

**R4. Claude Brain registration.** `_sync_brain_settings_hooks` becomes the source of truth for every IronClaude-owned hook in `~/.ironclaude/brain/.claude/settings.json`, for both PreToolUse and PostToolUse.
- It repoints legacy `$HOME/.ironclaude/brain/hooks/<x>.sh` commands to `$HOME/.claude/ironclaude-hooks/<x>.sh`.
- It removes retired commands.
- It preserves entries IronClaude doesn't own.
- It deploys every `commander/hooks/*.sh` (not tests) to `~/.claude/ironclaude-hooks/`.
- `settings.local.json` is untouched.

**R5. Codex Brain registration.**
- `worker/hooks/hooks.json` gains Brain entries appended at the end of the PreToolUse, PostToolUse and UserPromptSubmit arrays.
- Each entry runs the dispatcher `codex-brain-hooks.sh <script>`. The dispatcher exits 0 unless `IC_ROLE=brain` and `IRONCLAUDE_CLIENT=codex`, and only then sources the logger.
- Matchers are explicit regexes covering both Claude and Codex names, never `""`.
- Every existing entry stays byte-identical.

**R6. Worker hooks stand down in the Codex Brain.**
- `run_hook` in `hook-logger.sh` exits 0 for worker hooks when `IC_ROLE=brain` and `IRONCLAUDE_CLIENT=codex`.
- Brain hooks are exempt through `IC_BRAIN_HOOK=1`, which the dispatcher sets for each named target script. `brain-gated-actions.sh` declares it itself.
- The existing fail-closed fallbacks are preserved.

**R7. Trust warm-up.** Before the Codex Brain starts, the Commander:
- launches a throwaway Codex TUI in the dedicated, persistent, otherwise empty directory `~/.ironclaude/codex-hook-trust`, never `~/.ironclaude/brain`. Codex adds a `[projects."<cwd>"]` trust entry to `config.toml` for every new working directory, so a fresh temp directory on each restart would make those entries pile up;
- waits for ready through the existing auto-answer of "Trust all and continue";
- **R7a (added 2026-10-11, after the live start-up failure below):** then reads the *rendered* pane with `tmux capture-pane`, not the raw log, for up to a settle window. If the screen shows "Hooks need review" together with "Trust all and continue", it sends `2` once. It then waits, bounded, until that prompt is no longer on screen;
- kills it by exact session name.

**Live start-up finding (2026-10-11).**
- `_wait_for_ready` returned `ready=True` on the `>_ OpenAI Codex … loading` banner after 1.3 s.
- Codex drew "Hooks need review — 9 hooks are new or changed / 2. Trust all and continue" only after that. The warm-up had already killed the window, so nothing was trusted.
- The canary then stopped the Brain, as designed, and the daemon retried. The operator chose to stop the Commander and fix the warm-up.
- The raw log draws the prompt with cursor moves (`Hooks [2;10Heed review`), so prompt detection must use the rendered pane.
- The new canary slot `user_prompt_submit:2:0` already held a stale trust hash from an older `hooks.json`. Codex counts it as "changed", and "Trust all" re-trusts it.

**R7a details. Operator approved "Yes (Rec.)" on 2026-10-11. Scope is held; the follow-up plan is `docs/plans/2026-10-11-codex-brain-parity-followup.*`.**
- New helper `_answer_codex_hooks_review(tools, tmux, session, settle_seconds=20)` in `main.py`. It polls `tools._codex_screen(session, None)` about once a second.
  - When the whitespace-stripped, lower-cased screen contains both `hooksneedreview` and `trustallandcontinue`, it sends `2` once with `tmux.send_raw_keys`. It then waits up to 10 s for the prompt to leave the screen and returns `True`.
  - If no prompt appears within the settle window, it returns `False`.
- `_warm_up_codex_hook_trust` calls the helper after `_wait_for_ready` and logs its result before the exact-name kill.
- The canary remains the proof.
- **Backlog, not in this loop:** after a canary failure the daemon keeps retrying the Brain. Each retry runs its first turn for up to 30 s before the canary stops it.

**Second live start-up finding (2026-10-11 13:22).**
- R7a worked as far as detection: the warm-up saw the prompt on the rendered screen and sent `2`.
- In Codex v0.162.0-alpha.17.2, `2` only moves the highlight. The prompt footer reads `enter confirm · esc skip`, and the pane log ends with `› 2. Trust all and continue` highlighted but unconfirmed.
- The prompt stayed on screen for the full 10 s, and the helper logged a warning. It still returned `True`, so the warm-up logged `trust_prompt_answered=True`.
- `~/.codex/config.toml` was last modified at 02:03, so nothing was trusted. The slots `pre_tool_use:7–11` and `post_tool_use:7–8` are absent. The canary stopped the Brain, and the daemon retried twice before the Commander was stopped (`make stop`, PID 87091).
- Worker spawn has the same defect: `_wait_for_ready` (`orchestrator_mcp.py:2378`) answers the same prompt with `["2"]` only.

**R7b. Confirm the selection. Operator chose "A: Retreat + fix" and "Include it" (the worker path) on 2026-10-11.**
- Both callers send `["2", "Enter"]` in a single `tmux.send_raw_keys` call: `_answer_codex_hooks_review` in `main.py` and `_wait_for_ready` in `orchestrator_mcp.py`. `2` selects "Trust all and continue", and `Enter` confirms it.
  - If some Codex build acts on `2` alone, the extra Enter lands in an empty composer, which does nothing.
- `_answer_codex_hooks_review` returns `True` only once the prompt has left the screen. A prompt that is still on screen after `clear_seconds` returns `False` after logging the warning, so `trust_prompt_answered=` reports what actually happened. It still sends keys only once.
- The tests that pin `["2"]` (`test_codex_hook_trust_warmup.py:54`, `test_worker_adapter.py:158` and `:284`) pin `["2", "Enter"]` instead. The never-clears test asserts `False`.
- The canary remains the proof, and the live acceptance re-runs.
- Out of scope: the canary retry loop (backlog `project_brain_canary_retry_loop`).

**Third live start-up (2026-10-11 13:51), finding F1.**
- R7b worked. Every check passed except one: the negative probe's subagent spawn went through (`docs/plans/2026-10-10-codex-brain-parity-live-verification.md`).
- **Root cause (Fable investigation)**, from Codex source at tag `rust-v0.162.0-alpha.17`. Each fact is read from source; none was captured at runtime.
  - The model catalog (`~/.codex/models_cache.json`) gives `gpt-5.6-terra` `multi_agent_version: v2`, so the Brain gets the v2 collaboration tools in the `collaboration` namespace. The live Brain rollout carries the v2 role prompt ("4 available concurrency slots").
  - `registry.rs` `function_hook_tool_name` names the hook `spawn_agent` (alias `Agent`) only in the default or `multi_agent_v1` namespace. Otherwise it uses `flat_tool_name`, which concatenates namespace and name, giving `collaborationspawn_agent`.
  - `Agent|Task|spawn_agent` is a literal-only matcher, so Codex compares each alternative for exact equality. No handler matched, and `brain-task-gate.sh` never ran.
- **Refuted risk:** orchestrator MCP calls made inside Codex's `exec` wrapper (`tools.mcp__orchestrator__*`) do fire the Brain hooks. The live Brain rollout shows `brain-gated-actions` and `attention-sweep-enforcer` blocks on `mcp__orchestrator__spawn_worker`.

**R12. No subagents for the Codex Brain, enforced twice. Operator chose "A+B" on 2026-10-11.**
- **A (capability):** `CodexBrainClient._app_server_argv` adds `-c agents.enabled=false`. Per source, `multi_agent_version_override` returns `Disabled`, which takes precedence over the model catalog, so no collaboration tools are registered. `features.multi_agent=false` is not enough, because the catalog wins. The argv unit test pins the pair.
- **B (hook, defense in depth):**
  - The `brain-task-gate` matcher in `worker/hooks/hooks.json` becomes `Agent|Task|spawn_agent|collaborationspawn_agent`. It is still literal-only, so it keeps exact matching; an unanchored regex would also catch names that merely contain `Agent` or `Task`.
  - `brain-task-gate.sh` blocks every name ending in `spawn_agent`, in both the main path and the no-logger fallback. The near-miss `spawn_agents` stays ungated. New tests cover `collaborationspawn_agent`.
  - The matcher text changes the trusted hash of `pre_tool_use:11:0`. The R7b warm-up re-trusts it at the next Commander start.
  - The operator's own interactive Codex sessions will show "Hooks need review" once, and so will Codex worker spawns, which answer it with `2`+`Enter`.
- **Parity:** the Claude Brain allows only `ironclaude:search-conversations` as a subagent. Codex has no such agent type, so "no subagents" is the equivalent rule, as `brain-task-gate.sh` already states.

**R13. Inventory of the Brain's tools. Operator chose "Inventory now" on 2026-10-11.**
- After the restart, a probe started with the Brain's own config flags lists every tool the Brain gets, including the `tools.*` exposed inside `exec`. The probe's own record is the evidence.
- The listing is compared with the Claude Brain's tool set: orchestrator, episodic-memory and ollama MCP, plus Claude built-ins. Fable flagged `node_repl` (browser and computer use), the Slack, Google Calendar and Chrome plugins, and web search.
- Findings are reported to the operator. Any fix is decided then; R13 changes no code.

**R13 result (2026-10-11 16:36).** The Codex Brain inherits the operator's whole Codex tool surface. It includes:
- `mcp__codex_apps__*` (90+ tools: site deploys, sharing, plugin uninstall, parental controls);
- `cua_repl` (computer use) and `node_repl`;
- IronClaude `state_manager` and `workspace_manager` (with `push` and `commit_and_push`, which `block-push.sh` does not cover);
- web, image generation and goals.

The Claude Brain gets only orchestrator, episodic-memory, research and ollama, plus Read, Grep, Glob and Bash (`brain_client.py:174`, `:763-803`, `setting_sources=["project","local"]`). The full table is in `2026-10-10-codex-brain-parity-live-verification.md`.

**R14. The Codex Brain tool set matches the Claude Brain's. Operator chose "A: Match Claude set" and "A: Derive at start" on 2026-10-11.**
- **Mechanism:** extra `-c` pairs in `CodexBrainClient._app_server_argv`. `~/.codex/config.toml` is never written; it is read only.
- **Research (Opus, source at tag `rust-v0.162.0-alpha.17` plus local files; not yet run):**
  - `-c` keys are split on `.`, so plugin ids must be unquoted (`plugins.slack@openai-curated.enabled=false`). A quoted id silently does nothing.
  - A plugin's MCP server can be disabled through `plugins.<id>.mcp_servers.<manifest-name>.enabled=false` while the plugin and its hooks stay active.
  - Disabling a whole plugin also drops its hooks, so `ironclaude@ironclaude` is never disabled as a whole.
- **Fixed overrides:**
  - `features.apps=false` removes `codex_apps`;
  - `features.tool_suggest=false`;
  - `features.image_generation=false`;
  - `features.goals=false`;
  - `web_search="disabled"`.
- **Derived at each start, so a newly installed plugin or server is covered automatically:**
  - Read `~/.codex/config.toml` with `tomllib`. For every `[plugins."<id>"]` other than `ironclaude@ironclaude`, emit `plugins.<id>.enabled=false`.
  - For every `[mcp_servers.<name>]` that is not one the Brain argv defines itself (orchestrator and the optional Brain servers), emit `mcp_servers.<name>.enabled=false`. Today that covers `node_repl` and `computer-use`.
  - Read the ironclaude plugin manifest (`worker/.codex-plugin/plugin.json`, `mcpServers`). For every server except `episodic-memory`, emit `plugins.ironclaude@ironclaude.mcp_servers.<name>.enabled=false`. Today that covers `state-manager` and `workspace-manager`.
  - An id or name containing `.`, which cannot be expressed as an unquoted `-c` key segment, fails the Brain start instead of being skipped.
  - A missing `config.toml` means there is nothing to disable. An unreadable or invalid one fails the Brain start with a clear reason (fail closed).
- **Kept:** core shell and read, `view_image`, and `apply_patch`, which is gated by the Brain hooks.
- **Cannot be removed:** `list_mcp_resources`, `list_mcp_resource_templates` and `read_mcp_resource` are present whenever any MCP server exists. Claude offers MCP resource tools too.
- **Verification:**
  - Unit tests run against fixture `config.toml` and manifest files.
  - A live differential probe runs `codex exec` with the exact Brain `-c` set, without and then with the trim. It checks the tool list and that `block-push` still fires.
  - Then the live Brain starts with the canary passing, and the operator asks it in Slack to list its tools.

**R8. Canary.**
- A Brain UserPromptSubmit entry writes `/tmp/ic/codex-brain-gate/<gate-session>/hooks-live`.
- After the Brain's first turn is sent, the Commander requires the marker.
- If the marker is missing, the Commander interrupts the turn, fails the Brain start, and alerts Slack.

**R9. Provenance.**
- Every Brain hook command in the Claude settings and in the plugin Brain entries must resolve to a script shipped from the repo.
- If one does not, the Brain refuses to start and Slack is alerted.

**R10. Retire the untrusted file.** The Commander renames `~/.ironclaude/brain/.codex/hooks.json` to `hooks.json.retired` at start-up.

**R11. Tests and live acceptance.** Per the Testing Strategy below.

## Architecture

There is one Brain rule set, and its scripts live in `commander/hooks/`. Two registration paths deliver it:
- the Claude Brain's project `settings.json`, through the Commander's sync;
- the Codex Brain's trusted plugin `hooks.json`, through an identity-gated dispatcher.

Worker hooks recognise the Codex Brain identity in `run_hook` and stand down there. The Commander also guarantees three things before the Codex Brain acts:
- Trust: through the warm-up.
- Liveness: through the canary.
- Provenance: every hook script ships from the repo.

## Components

| Component | Change |
|---|---|
| `worker/hooks/brain-gated-actions.sh` | Renamed from `codex-brain-gated-actions.sh`. Brain identity covers both clients, `AskUserQuestion` is gated, the memory arm is widened. |
| `worker/hooks/codex-brain-hooks.sh` (new) | The Codex-Brain-only dispatcher. |
| `worker/hooks/hook-logger.sh` | `run_hook` stands worker hooks down for the Codex Brain. |
| `worker/hooks/hooks.json` | Brain entries appended at the end. Existing entries are byte-identical. The gated-actions group 0 command path changes with the rename, which is a deliberate re-trust of one key, covered by the warm-up. |
| `commander/hooks/{block-push,block-pin-enforcer,attention-sweep-enforcer,attention-sweep-arm}.sh` | Adopted. `block-push` gets the Codex fix. |
| `commander/hooks/brain-orchestrator-guard.sh`, `brain-task-gate.sh` | Codex payload fixes. |
| `commander/src/brain/brain_settings_hooks.json` | PreToolUse and PostToolUse template: the Brain set minus the retired enforcers, plus `brain-gated-actions`. |
| `commander/src/ironclaude/main.py` | The settings sync (R4), the R10 file retirement, and the provenance check (R9). |
| `commander/src/ironclaude/brain_client.py` | Sets `IRONCLAUDE_BRAIN_GATE_SESSION`. |
| `commander/src/ironclaude/codex_brain_client.py` | Warm-up hook-in point and canary check (R7, R8). |

## Data Flow

Codex Brain tool call:
1. Codex runs the trusted plugin entries.
2. Worker hooks: `run_hook` sees the Codex Brain identity and exits 0.
3. Brain entries: the dispatcher sees the identity and runs the Brain script.
4. A block is exit 2 with a stderr reason.

Claude Brain tool call:
1. The project `settings.json` entries, synced by the Commander, run the same scripts from `~/.claude/ironclaude-hooks/`.

## Error Handling

- Warm-up fails or the canary is absent: the Brain does not start and Slack is alerted. The Brain is never allowed to run unenforced.
- Provenance violation: the Brain refuses to start and Slack is alerted.
- A corrupt `settings.json` is left untouched and logged, as today.

## Testing Strategy

**Hook tests (bash suites in `worker/hooks/tests` and `commander/hooks/tests`)**
- Dispatcher: runs the target only for Codex-Brain identity. Negatives: a Claude Brain, a Codex worker, a Claude worker.
- Stand-down: worker hooks no-op for the Codex Brain. Negative: they still enforce for a Codex worker.
- Each patched script on Claude and on Codex payloads, with block and allow cases.
- `brain-gated-actions` on both identities.
- `hooks.json`: every pre-existing entry is identical to HEAD, apart from the one rename.

**pytest**
- Sync: legacy repoint, retirement, preservation of foreign entries, idempotency.
- R10 rename.
- Provenance check: block and pass cases.
- Warm-up and canary with a fake tmux: present, absent, and timeout.

**Live acceptance (must be able to fail)**

After redeploy and Commander restart, the Codex Brain:
1. answers "what's idle?" with a real `get_worker_status` call;
2. is blocked by named hooks in the rollout for:
   - `git push` via `exec_command`;
   - an `apply_patch` edit;
   - `spawn_worker` before the lookback;
   - `spawn_agent`.

The Claude Brain shows the same block for `spawn_worker` before the lookback.

## Implementation Notes

- **No live payload probe** (operator, 2026-10-11, on Opus's recommendation). `codex app-server` (0.162.0-alpha.17.2) does not list `--dangerously-bypass-hook-trust`, and the guard blocks scratch probe files that aren't in the plan. The Brain hooks instead match every known tool shape:
  - shell: `Bash` or `exec_command`;
  - command field: `.command` or `.cmd`;
  - command value: a string or an argv array;
  - subagent: `spawn_agent`.

  Unit tests cover each shape, plus negative cases: an unrelated or near-miss tool name, and a payload with no command field. Task 14's live negative probe is the real-payload proof. The one uncaptured shape is the `spawn_agent` payload; the gate blocks every `spawn_agent` call regardless of its fields.
- **UserPromptSubmit fires inside the Codex Brain's app-server.** This is proven from existing evidence:
  - `~/.claude/ironclaude-state-cache-01a127b6-a9dc-78f1-9daf-b204d6bd771c.json` is written only by `state-activator.sh:217`, a UserPromptSubmit hook that runs after its empty-prompt exit. `session-init.sh:284` only deletes these files.
  - The file is stamped `2026-10-10T22:09:07Z`. That is the same second the Brain's turn 2 started (`22:09:07.629Z` in rollout `…15-27-24-01a127b6…`).
  - It is not proven for turn 1, or for a newly added entry before it is trusted. The fail-closed canary covers both: the Brain is stopped and told so, never left running unrestricted.
- What `trusted_hash` covers stays unknown. The canary does not depend on it.
- The warm-up session fires worker SessionStart hooks, which create session rows. It never receives a prompt.
- The parked `_strip_ansi` OSC fix makes warm-up readiness wait 30 s until it lands.
