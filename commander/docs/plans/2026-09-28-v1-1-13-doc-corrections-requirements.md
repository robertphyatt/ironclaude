# v1.1.13 Doc Corrections and Constant Cleanup — Requirements

> **Created:** 2026-09-28
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-28-v1-1-13-doc-corrections-design.md

## Origin

The operator asked for a PM loop to fix the five observations from the Fable end review of the staged v1.1.13 work (lineage 134):
1. a repeat `kill_worker` re-grades a completed worker;
2. the README over-states the 1999 cap ("each text field");
3. the CHANGELOG sentence "records nothing new" is too broad;
4. the summarizer uses an unbounded schema and stores raw broken output;
5. `GRAMMAR_MAXLENGTH_HIGHEST_PASS` has no production consumer.

That loop grew into probes and a locator redesign. The operator then asked: "Have a fable sub agent look at the entire v1.1.13 corpus and assess if we have kept it focused enough or if we got lost chasing stuff that doesn't matter."

**Fable verdict: SOMEWHAT DRIFTED.**
- v1.1.13 was correct and shippable once the 1999 cap passed its live gate.
- The locator, the mode-agnostic footer, the `kill_worker` grade skip and the summarizer fix improve behaviour that predates v1.1.13. They belong in v1.1.14 or later as separate loops.
- P9 showed that long-prompt detection above 1999 characters is unreliable, not absent. The CHANGELOG and README over-state the loss.
- The vitest `onTaskUpdate` bullet is test-infrastructure noise, not a release feature.

**Operator decision:** "Take Fable's cut".

## Operator directives in force

- v1.1.13 ships the committed `60509a1` work, the staged lineage-134 set, and this loop only.
- The deferred work (the locator and footer, the `kill_worker` skip, the summarizer) is recorded in memory for v1.1.14 or later. It is not part of this loop.
- No commits, stash or push in professional mode, and no commit trailers.

## Acceptance criteria

1. **`GRAMMAR_MAXLENGTH_HIGHEST_PASS` is removed** from `grader.py` and its tests (finding 5).
   - The pin test asserts `GRAMMAR_MAX_STRING_LENGTH == LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1 == 1999`.
   - It also asserts that the grader module no longer defines `GRAMMAR_MAXLENGTH_HIGHEST_PASS`.
2. **CHANGELOG `## 1.1.13`:**
   - the `kill_worker` bullet says a repeated call "writes no second completion record" instead of "records nothing new", and notes that the `WORKER_KILLED` audit line and the session kill still happen (finding 3);
   - the grammar bullet's "Consequence" sentence is reworded: a block longer than 1999 characters is detected only when the grader returns its short tail, so very long prompts are detected unreliably. The validator's own ceiling remains 4096;
   - the vitest `onTaskUpdate` bullet becomes a one-line "Known issue".
3. **README "What's New in v1.1.13":**
   - "caps each text field at 1999" becomes "caps each long text field at 1999" (finding 2);
   - the "is not detected as a prompt" sentence is reworded to "detected unreliably", matching the CHANGELOG.
4. **Gates:** the full commander pytest suite passes, with 0 failed.
