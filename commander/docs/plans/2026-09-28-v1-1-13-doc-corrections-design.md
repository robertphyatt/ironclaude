# v1.1.13 Doc Corrections and Constant Cleanup — Design

> **Created:** 2026-09-28
> **Status:** Design Complete
> **Scope mode:** reduction
> **Requirements:** docs/plans/2026-09-28-v1-1-13-doc-corrections-requirements.md

## Summary

This is the final, minimal loop before v1.1.13 ships. It removes one unused constant and corrects the v1.1.13 CHANGELOG and README so they describe what ships accurately. There are no behaviour changes.

## Components

### 1. Remove `GRAMMAR_MAXLENGTH_HIGHEST_PASS` (`grader.py`, `test_grade_schema_bounds.py`)

- **`grader.py`:** delete the line `GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999`. The comment above the constants keeps the measured fact "1999 passes, 2000 fails", so no information is lost.
- **`test_grade_schema_bounds.py`:** drop the constant from the import.
  - The pin test asserts `LLAMA_CPP_MAX_REPETITION_THRESHOLD == 2000` and `GRAMMAR_MAX_STRING_LENGTH == LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1 == 1999`.
  - It also asserts `not hasattr(grader_module, "GRAMMAR_MAXLENGTH_HIGHEST_PASS")`, which is the RED case.

### 2. CHANGELOG `## 1.1.13`

- **`kill_worker` bullet:** replace "a repeated call against an already-`completed` worker records nothing new" with "a repeated call against an already-`completed` worker writes no second completion record (the `WORKER_KILLED` audit line and the session kill still happen)".
- **Grammar bullet:** replace the "Consequence: …" sentence with: "Consequence: a final interaction block (or question) longer than `GRAMMAR_MAX_STRING_LENGTH` can't be copied whole, so it is detected only when the grader returns the block's short tail (the question and its options); detection of very long prompts is therefore unreliable. The validator's own ceiling remains 4096."
- **Vitest bullet:** replace the multi-sentence `onTaskUpdate` bullet with a one-line known issue: "Known issue: the vitest suite can hit a `Timeout calling \"onTaskUpdate\"` RPC error, isolated to `integration-core.test.ts`; see `2026-09-26-v1-1-13-vitest-ontaskupdate-findings.md` — the fix is a follow-up."

### 3. README "What's New in v1.1.13"

- Replace "caps each text field at 1999 characters" with "caps each long text field at 1999 characters".
- Replace "A worker prompt whose final block is longer than that length is not detected as a prompt." with "A worker prompt whose final block is longer than that is detected only when the grader returns its short tail, so very long prompts are detected unreliably."

## Testing strategy

- **Constant removal:** RED then GREEN. The pin test fails while the constant exists, and passes once it is removed.
- **Docs:** verified with `rg`. Each removed phrase must return no output, and each added phrase exactly one match.
- **Gate:** the full commander pytest suite.

## Implementation notes

- The deferred lineage-137 artifacts (the locator, footer, `kill_worker` skip and summarizer design, requirements and plan), and the chunking and locator probe documents, stay on disk as v1.1.14 inputs. They were left uncommitted. No source file from them changes in this loop.
- **Execution record:** this loop was carried out directly, with professional mode off at the operator's instruction ("Make the amendment, ensure the readme, commit message and change log are to standard"), so it has no plan file. Its changes were verified by `test_grade_schema_bounds.py` and the full commander suite (3372 passed).
- After the loop, with professional mode off and the operator's go:
  1. amend the v1.1.13 release commit with the staged v1.1.13 set, with no trailers, and move the local `v1.1.13` tag;
  2. copy `plan-validator.sh` into the Codex cache;
  3. restart Commander;
  4. validate live.
  Nothing is pushed without an explicit go.
