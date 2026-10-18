# Haiku-Class Fast-Model Fallback: Task 1 Findings

Measured 2026-10-16. Scratch dir: /private/tmp/claude-502/haiku-measure/. Claude CLI 2.1.302 (Claude Code); Codex codex-cli 0.162.0-alpha.17.2.
`env -u CLAUDECODE` was NOT needed; claude ran directly.

## Claude single (5 sequential, single.json)
rc all 0; seconds 4.38, 4.8, 4.51, 4.61, 5.02. min 4.38, max 5.02, p95_nearest_rank 5.02.

## Claude concurrent (6 parallel, conc.json)
rc all 0; seconds 5.85, 6.14, 5.1, 5.65, 5.82, 6.39. min 5.1, max 6.39, p95_nearest_rank 6.39.

## Shape, model, persistence
Every run returned structured_output with evidence_quality in A-F (B once, D once, C nine times). Resolved model in modelUsage: `claude-haiku-5-5`.
persist.log: 11 lines, all `absent` (no session file written with --no-session-persistence).

## Hooks-off proof (Step 5, persisted run without --no-session-persistence)
hooksoff rc 0; sid 12170aeb-5c99-441d-ba1c-8a9ff24f3d85; transcript exists (77293 bytes).
Child `grep -c hookName` = 0 (exit 1, no match). Control (b97b76c6-...jsonl) `grep -c hookName` = 3.

## Codex Luna
Newest installed slug from ~/.codex/models_cache.json: `gpt-6-luna`.
Run A (schema.json without additionalProperties): all 3 runs rc=1 (2.79, 1.73, 2.23 s). Error: "Invalid schema for response_format 'codex_output_schema': 'schema.additionalProperties' is required to be supplied and to be false when strict is true." (invalid_json_schema, HTTP 400). Therefore CODEX_SCHEMA_STRICT=yes. Outputs kept in codex-nonstrict/.
Run B (schema-strict.json = schema + "additionalProperties": false): 3 runs, rc all 0; seconds 3.51, 2.72, 2.91; min 2.72, max 3.51, p95_nearest_rank 3.51. Each last agent_message parsed as an object with evidence_quality (D, F, F). thread_ids: 01a147b2-8bcc-71e0-b12a-28699915b1dd, 01a147b2-996f-7180-9305-d4c3dfa38f69, 01a147b2-a3cf-7ee3-923b-e7036af533c5.
Rollouts: `find ~/.codex/sessions -newer codex-marker2 -type f` returned 0 files for run B (run A, also 0). No rollout matches a thread_id.

## STOP
P95_SINGLE_CLAUDE = 5.02 s exceeds the 5 s threshold, so PROMPT_DETECTION_SYNC=STOP. Per the plan, execution stops; operator must choose: accept synchronous prompt detection at ~5.02 s p95 (n=5, nearest-rank equals max; the run-to-run spread 4.38-5.02 straddles the threshold), or start a follow-up design moving detection off the poll thread (design R6). Note: the 5 s threshold is per call; with K workers emitting new output in one poll the poll thread spends up to K x P95, because the cache is keyed per log-tail hash.

## Operator decision (2026-10-16)

The `PROMPT_DETECTION_SYNC` gate tripped: `P95_SINGLE_CLAUDE` = 5.02, above the 5 s threshold. The operator chose "Accept synchronous (Recommended)". Prompt detection stays on the poll thread, and each newly-idle worker adds about 5 s to that poll. The gate is therefore treated as `yes` for every later task.

## Task 8 regression (2026-10-16)

- **Commander suite:** 3968 passed, 1 failed. The failure is `test_notifications.py::test_resolve_degraded_backend_label_missing_config_defaults_ollama`, a stale expectation after the default flip: the banner now says "Haiku". On the operator's decision ("Carry into next loop") it is fixed first thing in the usage-ledger loop, and both loops are committed together.
- **Episodic-memory suite:** 150/150 passed.
- **Hook suites (`make test-hooks`, sentinel claude/codex first on PATH):**
  - The first aggregate run failed 2 checks in `test-gbtw-continuation-judge.sh` ("throttle fourth Stop passes natively / skips judge").
  - The second aggregate run passed fully (exit 0).
  - The sentinel logged no real claude/codex calls.
- **The throttle flake is unexplained.**
  - Observed 2 times in about 22 runs on the new code, both inside aggregate runs (the Task 2 loop and Task 8); 0 failures in 9 HEAD runs.
  - Ruled out: cross-suite state (unique throttle file names) and the 300 s counter-reset window (the suite takes about 12 s).
  - No causal link to this change is identified: in that test the change only adds a stub-binary call on a failed local answer.
  - Backlogged for investigation.

## Gate values
P95_SINGLE_CLAUDE=5.02
P95_CONCURRENT_CLAUDE=6.39
P95_SINGLE_CODEX=3.51
FAST_MODEL_TIMEOUT_DEFAULT=max(30, ceil(2 × 6.39)) = 30
FAST_LANE_FAST_MODEL_SECONDS=ceil(2 × 5.02) = 11
PROMPT_DETECTION_SYNC=yes (operator-accepted; measured value tripped STOP, see Operator decision)
HOOKS_OFF_PROVEN=yes
NO_PERSISTENCE_PROVEN=yes
CODEX_NO_ROLLOUT=yes
CODEX_WITHIN_CAP=yes
CODEX_SCHEMA_STRICT=yes
NEWEST_LUNA=gpt-6-luna
