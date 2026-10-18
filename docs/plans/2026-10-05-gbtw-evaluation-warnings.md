# GBTW evaluation warnings implementation plan

Requirements: docs/plans/2026-10-05-gbtw-evaluation-warnings-requirements.md
Design: docs/plans/2026-10-05-gbtw-evaluation-warnings-design.md

Loop: full — hook result handling.
Predicted answer: subagent-sequential; run plan review (delegated).

## Task 1: Warn on unavailable judge evaluations

Preserve fail-open control flow, report unavailable grading through existing session warning queue, and verify native client contracts.

Allowed files:
- worker/hooks/get-back-to-work-impl.sh
- worker/hooks/tests/test-gbtw-continuation-judge.sh

Dependencies: none.

### Step 1

RED: extend existing isolated wrapper fixture in worker/hooks/tests/test-gbtw-continuation-judge.sh. Stub empty transport failure and nonempty malformed/invalid grade. For Claude and Codex assert approval, one queued warning naming unavailable checks, queue drain additionalContext, and no all-checks-passed claim in Claude output. Add successful/suppressed no-warning controls, mixed valid D plus failed other grader preserving block, and queue-path-as-directory IO failure preserving approval. Assert native Codex {} approval. Run before implementation and report actual failures.

```sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
```

### Step 2

GREEN: modify worker/hooks/get-back-to-work-impl.sh run_check to represent empty response or invalid grade as result unavailable with fixed bounded reasoning and no raw response disclosure. read_check_result must distinguish fired absent/invalid result from intentional suppression. After joins, aggregate unavailable labels and queue one best-effort warning before any grade decision can exit. Keep unavailable outside A/B/C/D/F so failed grader does not block. Keep all valid grades and deterministic gates unchanged. Build approval reason/systemMessage that explicitly says evaluation unavailable when applicable; successful-only output remains unchanged. No wrapper protocol changes.

### Step 3

Verify extended routing suite and existing warning and wrapper regressions; execute commands separately and inspect each exit status. Record actual outcomes, do not claim a predicted pass count.

```sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
```

### Step 4

Run existing warning regressions.

```sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-client-aware.sh
```

### Step 5

Run existing Stop wrapper regressions.

```sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
```

### Step 6

Check shell syntax.

```sh
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/get-back-to-work-impl.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh
```

### Step 7

Check scoped whitespace before submission.

```sh
git -C /Users/roberthyatt/Code/ironclaude diff --check -- worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-gbtw-continuation-judge.sh
```

### Step 8

Parent stages exact two task files, reviews implementation and meaningful failure assertions, then submits and records task-boundary verdict.

```sh
git -C /Users/roberthyatt/Code/ironclaude add -- worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-gbtw-continuation-judge.sh
```

## Execution invariants

Each command runs independently; shell state does not persist. Use absolute paths and git -C. Quote globs for zsh. No foreground sleep. Docs are ignored and require git add -f. Distinguish empty output from errors; do not suppress evidence errors. Preserve evidence until results are reported. Enumerate rather than assume counts. Test results and counts must be measured; RED must demonstrate warning or summary assertions fail against the current implementation, GREEN must demonstrate their correction. Do not move assertion targets with unrelated edits or use inferred marker spelling. No new verification framework, live LLM calls, deployment, config edits, commits, pushes, or unrelated whitespace cleanup. Preserve prior staged content.
