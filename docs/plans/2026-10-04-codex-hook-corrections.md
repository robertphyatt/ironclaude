# Codex Hook Corrections Implementation Plan

Required skill: ironclaude:executing-plans, subagent-sequential.

**Goal:** Repair approved hook and workflow defects and detect premature stops with existing context-aware LLM judge.

**Requirements:** docs/plans/2026-10-04-codex-hook-corrections-requirements.md

**Design:** docs/plans/2026-10-04-codex-hook-corrections-design.md

**Execution invariants:** Use absolute command paths and git -C /Users/roberthyatt/Code/ironclaude; shell state does not persist. Quote globs. No foreground sleep. docs are ignored and require git add -f. Do not suppress errors on evidence commands or truncate completeness searches. Enumerate actual outputs; expected pass/fail outcomes below are acceptance criteria to measure during execution, not previously observed results. RED must demonstrate defect before GREEN. Preserve pre-existing staged experiment documents. No commits, pushes, deployment, version changes, WIP branch deletion, L3 implementation, new framework, or broad cleanup. Each step is one focused operation; split editing/test cycles into 2–5 minute actions. Parent owns state, review and staging; agents implement only claimed task. Review every task before advancing. Existing configured validator is used for live cases; transport failure is an explicit limitation, never a semantic pass.

## Task 1: Synchronize retreat skill context

Depends on: []

Allowed files:
- `worker/hooks/mcp-state-logger.sh`
- `worker/hooks/tests/test-mcp-state-logger-active-skill.sh`
- `worker/hooks/tests/test-memory-search-gate.sh`

Normalize verified transition object from tool_output or tool_response, including JSON strings and MCP content text. Reject isError envelopes and anything except changed:true. For retreat, map destination brainstorming to active_skill brainstorming and debugging to systematic-debugging. No other destination maps; no memory-search rearm. Existing tool mappings remain intact. Share decoded transition object for changed/from/to instead of parsing independently. Use existing UPDATE only when different; preserve updated_at.

### Step 1

RED: extend focused regression tests before implementation. Add direct/object/string/MCP-text retreat responses for both destinations; changed:false, malformed, isError and unsupported destination preserve active_skill and memory_search_required. Include same-target resume after retreat and require unchanged state.

### Step 2

Run regression commands; record observed failure and verify it demonstrates specified defect, not setup failure.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-mcp-state-logger-active-skill.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-memory-search-gate.sh
```

### Step 3

GREEN: implement specified correction. Normalize verified transition object from tool_output or tool_response, including JSON strings and MCP content text. Reject isError envelopes and anything except changed:true. For retreat, map destination brainstorming to active_skill brainstorming and debugging to systematic-debugging. No other destination maps; no memory-search rearm. Existing tool mappings remain intact. Share decoded transition object for changed/from/to instead of parsing independently. Use existing UPDATE only when different; preserve updated_at.

### Step 4

Run focused regressions again; inspect actual results and modified source. Resolve failures within this task.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-mcp-state-logger-active-skill.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-memory-search-gate.sh
```

### Step 5

Parent reviews task diff and tests; stage only explicit changed allowed paths.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- 'worker/hooks/mcp-state-logger.sh' 'worker/hooks/tests/test-mcp-state-logger-active-skill.sh' 'worker/hooks/tests/test-memory-search-gate.sh'
```

## Task 2: Align drift rename and absolute path coverage

Depends on: [1]

Allowed files:
- `worker/hooks/execution-drift-check.sh`
- `worker/hooks/tests/test-execution-drift-check.sh`
- `worker/hooks/tests/test-execution-drift-identity.sh`
- `worker/mcp-servers/workspace-manager/src/scoped-tree.ts`
- `worker/mcp-servers/workspace-manager/src/__tests__/scoped-tree.test.ts`

In _drift_dirty_paths remember R versus C when reading NUL porcelain records. Emit rename source record as path, skip copy source; destination remains emitted. In _drift_rel_by_identity preserve final basename and missing suffix while finding longest existing ancestor directory; resolve that directory physically with cd -P/pwd -P, compare physical ancestry to root by -ef, then append missing suffix. Map every absolute entry, including lexical root prefix, through this function. Distinguish external path status from unreadable ancestor resolution; propagate unreadable as existing explicit fail-open result, not silently dropping. Relative exact matching and managed root resolver unchanged. Preserve baseline semantics. In scoped-tree.ts repoRelativeFromAbsolute, throw a descriptive Cannot scope commit error for non-ENOENT/non-ENOTDIR resolution failures rather than returning external/null. Preserve null for proven external/root entries; preserve missing-parent fallback. Add regression using an ELOOP ancestor symlink to prove unreadable resolution fails before producing a tree, alongside outside entry skip and alias/deletion contract cases.

### Step 1

RED: extend focused regression tests before implementation. Add staged rename source-disallowed/destination-allowed rejection and both allowed pass. Feed copy record fixture to record parser without inventing source deletion. Add absolute internal-directory alias, root alias, deleted leaf/missing parent, final symlink entry preservation, sibling and internal alias escaping repository rejection. Existing APFS case identity case remains conditional on filesystem. Add ELOOP resolution failure regression in scoped-tree.test.ts; require throw, while outside entry remains skipped.

### Step 2

Run regression commands; record observed failure and verify it demonstrates specified defect, not setup failure.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- src/__tests__/scoped-tree.test.ts
```

### Step 3

GREEN: implement specified correction. In _drift_dirty_paths remember R versus C when reading NUL porcelain records. Emit rename source record as path, skip copy source; destination remains emitted. In _drift_rel_by_identity preserve final basename and missing suffix while finding longest existing ancestor directory; resolve that directory physically with cd -P/pwd -P, compare physical ancestry to root by -ef, then append missing suffix. Map every absolute entry, including lexical root prefix, through this function. Distinguish external path status from unreadable ancestor resolution; propagate unreadable as existing explicit fail-open result, not silently dropping. Relative exact matching and managed root resolver unchanged. Preserve baseline semantics. In scoped-tree.ts repoRelativeFromAbsolute, throw a descriptive Cannot scope commit error for non-ENOENT/non-ENOTDIR resolution failures rather than returning external/null. Preserve null for proven external/root entries; preserve missing-parent fallback. Add regression using an ELOOP ancestor symlink to prove unreadable resolution fails before producing a tree, alongside outside entry skip and alias/deletion contract cases.

### Step 4

Run focused regressions again; inspect actual results and modified source. Resolve failures within this task.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test -- src/__tests__/scoped-tree.test.ts
```

### Step 5

Parent reviews task diff and tests; stage only explicit changed allowed paths.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- 'worker/hooks/execution-drift-check.sh' 'worker/hooks/tests/test-execution-drift-check.sh' 'worker/hooks/tests/test-execution-drift-identity.sh' 'worker/mcp-servers/workspace-manager/src/scoped-tree.ts' 'worker/mcp-servers/workspace-manager/src/__tests__/scoped-tree.test.ts'
```

## Task 3: Make workflow handoffs and completion staging executable on both clients

Depends on: [2]

Allowed files:
- `worker/skills/brainstorming/SKILL.md`
- `worker/skills/writing-plans/SKILL.md`
- `worker/skills/executing-plans/SKILL.md`
- `worker/hooks/tests/test-skill-handoffs-restaging.sh`

Add authoritative client-native invocation rule to these three skills: Claude invokes Skill; Codex reads/applies next SKILL.md then performs identical MCP preflight/transitions. Replace literal unavailable-tool hard gates with this rule, preserving mandatory workflow and reviews. Replace completion staging shell snippet with executable inline Python accepting repo-root and plan-json arguments, using pathlib/os.path realpath on existing ancestors while preserving final Git entry, rejecting outside root and directory entries. Enumerate git ls-files -z for tracked deletions; stage existing files/symlinks or exact tracked missing entries only. Use subprocess argv git --literal-pathspecs -C ROOT add -- REL for deduped exact files; no shell interpolation, broad directory staging or index clearing. Missing optional untracked files skipped; normalization errors surface.

### Step 1

RED: extend focused regression tests before implementation. Create focused shell regression that extracts and executes actual marked Python restaging snippet from executing-plans skill in temporary Git repo. Check relative and absolute files, root/directory aliases, tracked deletions, final symlink, missing optional entries, external paths, glob-like literal names, directory entry rejection, preservation of unrelated staged and unstaged files. Assert Codex handoff rule overrides Skill-only demands. Documentation prose itself needs no behavioral test; executable snippet does.

### Step 2

Run regression commands; record observed failure and verify it demonstrates specified defect, not setup failure.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-handoffs-restaging.sh
```

### Step 3

GREEN: implement specified correction. Add authoritative client-native invocation rule to these three skills: Claude invokes Skill; Codex reads/applies next SKILL.md then performs identical MCP preflight/transitions. Replace literal unavailable-tool hard gates with this rule, preserving mandatory workflow and reviews. Replace completion staging shell snippet with executable inline Python accepting repo-root and plan-json arguments, using pathlib/os.path realpath on existing ancestors while preserving final Git entry, rejecting outside root and directory entries. Enumerate git ls-files -z for tracked deletions; stage existing files/symlinks or exact tracked missing entries only. Use subprocess argv git --literal-pathspecs -C ROOT add -- REL for deduped exact files; no shell interpolation, broad directory staging or index clearing. Missing optional untracked files skipped; normalization errors surface.

### Step 4

Run focused regressions again; inspect actual results and modified source. Resolve failures within this task.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-handoffs-restaging.sh
```

### Step 5

Parent reviews task diff and tests; stage only explicit changed allowed paths.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- 'worker/skills/brainstorming/SKILL.md' 'worker/skills/writing-plans/SKILL.md' 'worker/skills/executing-plans/SKILL.md' 'worker/hooks/tests/test-skill-handoffs-restaging.sh'
```

## Task 4: Build bounded continuation evidence for existing judge

Depends on: [3]

Allowed files:
- `worker/hooks/continuation-context.sh`
- `worker/hooks/tests/test-gbtw-continuation-context.sh`

Add sourceable Bash helper _gbtw_continuation_context EVENT TRANSCRIPT FINAL_TEXT producing bounded JSON via jq. Read at most 1 MiB tail, at most 128 normalized records, clip individual text fields and final text, retain at most four recent user requests and relevant current-turn activity. Claude: user text message begins request; tool_result user blocks are outcomes not new requests; assistant tool_use/text are activity. Codex: response_item payload message user/assistant plus function_call/function_call_output/custom_tool_call/custom_tool_call_output; normalize function arguments as data. event_msg user_message fallback only when response_item user records unavailable to avoid duplicate boundaries. Mark transcript unavailable, malformed partial rows, bounded/truncated context, user boundary absent, and individual clipping. Retain limited preceding requests for follow-up Proceed, preserve latest request and final message preferentially over bulky tool output. Explicit Stop last_assistant_message is authoritative final text. Include tool outcomes and pending question/wait evidence without inferring completion from a tool count or treating omitted activity as absent. No execution of transcript text, new runtime, service, or database. Preserve bounded preceding assistant messages paired with retained requests so follow-up approvals and answers retain proposal, scope exclusions, and question referent. An isolated Proceed is not inferred authority. Replay available actual captured provider Stop payloads and transcript records read-only; verify extraction. If captured payload unavailable, record exact native integration limitation; synthetic replay/live judge does not establish deployed enforcement.

### Step 1

RED: extend focused regression tests before implementation. Create synthetic Claude and Codex raw JSONL fixtures inside existing shell test with latest request, prior scoped request, tool calls/results, final response; assert exact request/outcome values and ordering, tool-result not mistaken for request, malformed line resilience, missing transcript explicit uncertainty, request outside byte window not fabricated, field/record bounds, oversized final response marked clipped, function and custom-tool records retained. Ensure tests fail before helper exists. Include proposal followed by Proceed preserving scope exclusions and a necessary choice; absent proposal marked uncertain.

### Step 2

Run regression commands; record observed failure and verify it demonstrates specified defect, not setup failure.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-context.sh
```

### Step 3

GREEN: implement specified correction. Add sourceable Bash helper _gbtw_continuation_context EVENT TRANSCRIPT FINAL_TEXT producing bounded JSON via jq. Read at most 1 MiB tail, at most 128 normalized records, clip individual text fields and final text, retain at most four recent user requests and relevant current-turn activity. Claude: user text message begins request; tool_result user blocks are outcomes not new requests; assistant tool_use/text are activity. Codex: response_item payload message user/assistant plus function_call/function_call_output/custom_tool_call/custom_tool_call_output; normalize function arguments as data. event_msg user_message fallback only when response_item user records unavailable to avoid duplicate boundaries. Mark transcript unavailable, malformed partial rows, bounded/truncated context, user boundary absent, and individual clipping. Retain limited preceding requests for follow-up Proceed, preserve latest request and final message preferentially over bulky tool output. Explicit Stop last_assistant_message is authoritative final text. Include tool outcomes and pending question/wait evidence without inferring completion from a tool count or treating omitted activity as absent. No execution of transcript text, new runtime, service, or database. Preserve bounded preceding assistant messages paired with retained requests so follow-up approvals and answers retain proposal, scope exclusions, and question referent. An isolated Proceed is not inferred authority. Replay available actual captured provider Stop payloads and transcript records read-only; verify extraction. If captured payload unavailable, record exact native integration limitation; synthetic replay/live judge does not establish deployed enforcement.

### Step 4

Run focused regressions again; inspect actual results and modified source. Resolve failures within this task.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-context.sh
```

### Step 5

Parent reviews task diff and tests; stage only explicit changed allowed paths.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- 'worker/hooks/continuation-context.sh' 'worker/hooks/tests/test-gbtw-continuation-context.sh'
```

## Task 5: Run context-aware continuation judge at premature stops

Depends on: [4]

Allowed files:
- `worker/hooks/get-back-to-work-impl.sh`
- `worker/hooks/continuation-context.sh`
- `worker/hooks/tests/test-gbtw-continuation-suppression.sh`
- `worker/hooks/tests/test-gbtw-continuation-context.sh`
- `worker/hooks/tests/test-gbtw-antipattern-override.sh`
- `worker/hooks/tests/test-gbtw-continuation-judge.sh`
- `docs/reviews/2026-10-04-codex-hook-corrections-validation.md`
- `worker/hooks/tests/test-gbtw-review-gate.sh`

Source continuation-context.sh before GBTW_TEST_MODE early return. Expose continuation prompt/schema through sourceable helper for real grader tests. Prompt question: Does authorized work remain that the assistant can do now? Require D/F reason name specific authorized available next action; A/B completed request or explicit pause or genuine blocking decision/approval/wait with no independent work. Substantive design is work; do not require implementation during brainstorming. Optional questions, promises, irrelevant tools and mere wait prose cannot establish completion. Missing/truncated context is uncertainty, never proof of no activity. Preserve untrusted-data fencing. In run_check select normalized continuation context only for check_name continuation; other graders retain RECENT_CONTEXT. Remove brainstorming, execution_complete, in-flight/wait-tool and holding-prose blanket suppression of continuation. Preserve deterministic task/review gates, review-wait helper behavior, forced-turn bypass handling, activation-only behavior, validator-error fail-open, existing three-block/five-minute escape, wrappers and grades. Fix regression tests that encode superseded continuation suppression while retaining tests of helpers still used by deterministic gates. In earlier submitted/pending/in-progress task branches, preserve deferral of deterministic review/task block but remove approve-and-exit on live job/wait evidence: retain evidence and fall through to continuation judge. Do not fall into unconditional task block after deferral. Keep deterministic blocks when no legitimate deferral signal exists. Add actual task-row routing tests for submitted and pending/in-progress: unrelated live job with independent work reaches judge and blocks; genuine dependency wait reaches judge and passes. Existing review-gate test expectations may require transport stub with legitimate wait grade A, never network dependency.

### Step 1

RED: extend focused regression tests before implementation. Update routing test to exercise real implementation with isolated HOME/db and stub transport responses; assert continuation actually called for brainstorming/optional question/live job/holding text/old execution_complete and sees request, tool outcome and final text. Assert D/F native Codex block and Claude protocol, completed A allows, validator failure allows with explicit failure reason, throttle still bounds retries. Preserve existing in-flight/wait/review-gate tests. Add opt-in --live mode to focused test using actual call_validation_llm and actual continuation prompt/schema; cases: promise-only brainstorming, optional question with independent work, irrelevant tool then promise => D/F; necessary design choice, completed no-tool answer, operator pause, required approval, genuine dependency wait => A/B. Record raw grades/reasons, backend config identity and transport failures in validation artifact; no mocked results presented as semantic validation. Exercise actual submitted and pending/in-progress task rows with deferred waits; ensure judge receives wait evidence and distinguishes remaining independent work. Capture available real Stop payload/transcript replay evidence and any unavailable native integration limitation.

### Step 2

Run regression commands; record observed failure and verify it demonstrates specified defect, not setup failure.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-context.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-suppression.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-inflight.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-waiting.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-review-gate.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-antipattern-override.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-client-aware.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh --live
```

### Step 3

GREEN: implement specified correction. Source continuation-context.sh before GBTW_TEST_MODE early return. Expose continuation prompt/schema through sourceable helper for real grader tests. Prompt question: Does authorized work remain that the assistant can do now? Require D/F reason name specific authorized available next action; A/B completed request or explicit pause or genuine blocking decision/approval/wait with no independent work. Substantive design is work; do not require implementation during brainstorming. Optional questions, promises, irrelevant tools and mere wait prose cannot establish completion. Missing/truncated context is uncertainty, never proof of no activity. Preserve untrusted-data fencing. In run_check select normalized continuation context only for check_name continuation; other graders retain RECENT_CONTEXT. Remove brainstorming, execution_complete, in-flight/wait-tool and holding-prose blanket suppression of continuation. Preserve deterministic task/review gates, review-wait helper behavior, forced-turn bypass handling, activation-only behavior, validator-error fail-open, existing three-block/five-minute escape, wrappers and grades. Fix regression tests that encode superseded continuation suppression while retaining tests of helpers still used by deterministic gates. In earlier submitted/pending/in-progress task branches, preserve deferral of deterministic review/task block but remove approve-and-exit on live job/wait evidence: retain evidence and fall through to continuation judge. Do not fall into unconditional task block after deferral. Keep deterministic blocks when no legitimate deferral signal exists. Add actual task-row routing tests for submitted and pending/in-progress: unrelated live job with independent work reaches judge and blocks; genuine dependency wait reaches judge and passes. Existing review-gate test expectations may require transport stub with legitimate wait grade A, never network dependency.

### Step 4

Run focused regressions again; inspect actual results and modified source. Resolve failures within this task.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-context.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-suppression.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-inflight.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-waiting.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-review-gate.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-antipattern-override.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-client-aware.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh --live
```

### Step 5

Parent reviews task diff and tests; stage only explicit changed allowed paths.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- 'worker/hooks/get-back-to-work-impl.sh' 'worker/hooks/continuation-context.sh' 'worker/hooks/tests/test-gbtw-continuation-suppression.sh' 'worker/hooks/tests/test-gbtw-continuation-context.sh' 'worker/hooks/tests/test-gbtw-antipattern-override.sh' 'worker/hooks/tests/test-gbtw-continuation-judge.sh' 'worker/hooks/tests/test-gbtw-review-gate.sh'
git -C /Users/roberthyatt/Code/ironclaude add -f -- 'docs/reviews/2026-10-04-codex-hook-corrections-validation.md'
```

## Completion verification

Run `make -C /Users/roberthyatt/Code/ironclaude test-hooks` once after focused tests. Repair verified task failures through workflow, review scoped diff, preserve evidence, distinguish native/runtime gaps from source validation. Stage exact allowed union and preserve unrelated index contents.
