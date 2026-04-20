# AskUserQuestion Format

Every AskUserQuestion call across all skills MUST follow this 3-part structure.

---

## 1. Re-ground (1-2 sentences)

State the project, current branch, and what task/phase you're in. Assume the user hasn't looked at this window in 20 minutes and may be context-switching between multiple ironclaude worker sessions.

Example: *"We're in the ironclaude plugin on branch `feat/fix-first-review`, executing Task 3 of the gstack improvements plan (updating code-review SKILL.md)."*

## 2. Predict (1 sentence)

Before every question, state your prediction with reasoning: "My prediction: You'll say X because [reasoning]." This forces thinking from the user's perspective and surfaces assumptions that might be wrong.

Example: *"My prediction: You'll choose option A because you've consistently preferred surgical fixes over broad refactors."*

## 3. Options

Clear, distinct choices via the AskUserQuestion tool. Each option should have:
- A concise label (1-5 words)
- A description of what happens if chosen

Do not batch multiple questions. One question at a time.

---

## When to skip re-grounding

If the previous message in the conversation was also an AskUserQuestion from the same skill in the same phase, the user already has context. You may abbreviate the re-ground to just the current step (e.g., "Still in Task 3, Step 2.").

## Enforcement

The GBTW prediction check validates that predictions appear before questions. Skills that omit predictions will be blocked by the Stop hook.
