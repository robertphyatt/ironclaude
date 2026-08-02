# Code Review Checklist

Applied by `code-review` Step 4.5 against the staged diff. Each entry has detection
steps and explicit suppressions. These exist because each defect below actually
shipped into a plan or a diff in this repo and was caught by a reviewer, not the author.

## Falsifiability at the end state

For every guard, assertion, or `expected:` value in the diff, name the deletion that
would make it fail — evaluated as though **every change in this branch has landed**,
not the state at the line where it appears.

**Detection steps**
1. For each added assertion or guard, identify the behaviour it exists to catch.
2. Delete that behaviour mentally and re-evaluate the expression **against the
   post-merge state**, including changes made by other files in the same diff.
3. If the expression still holds, the check cannot fail — flag it.
4. Pay particular attention to a guard whose subject is relocated by another change
   in the same diff (a path moved under a redirected root, a value the diff itself
   rewrites).

**DO NOT flag**
- A check that is weaker than ideal but still fails on a real regression.
- A deliberate assertion of a current limitation, where the diff or plan says the
  limitation is removed later.
- Documentation, comments, or logging.

## Provenance for factual claims

Flag counts, line numbers, symbol names and "N sites" claims that nothing in the diff
or its recorded evidence establishes.

**Detection steps**
1. List every numeric or positional claim in the diff and its commit/plan text.
2. For each, find the command output or file read that establishes it.
3. If the only support is a summary — an agent report, a prior document, a
   recollection — flag it as unverified provenance.
4. A count derived from a line range rather than traced through the code is
   unverified.

**DO NOT flag**
- Claims the diff itself demonstrates (a test asserting the value).
- Approximations explicitly marked as such.
- Values whose supporting command appears in the same change.

## Widened-guard scope

When a diff loosens an allowlist, matcher, permission, or validation, flag the absence
of negative cases proving it did not widen further than the requirement states.

**Detection steps**
1. Identify any pattern, allowlist, or predicate the diff makes more permissive.
2. Compare the scope the requirement or plan states against the scope the code
   actually admits — read the regex or condition, do not trust its description.
3. Confirm negative cases exist proving the newly-admitted set is bounded.
4. A widening tested only with positive cases proves nothing about what it refuses.

**DO NOT flag**
- Widenings whose negative cases exist elsewhere in the same suite (say where).
- Pure refactors that preserve the admitted set.
