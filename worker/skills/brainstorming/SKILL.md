---
name: brainstorming
description: Turn ideas into fully formed designs through collaborative dialogue
---

# Brainstorming

## Purpose

Turn vague ideas into fully-formed designs through systematic collaborative dialogue. This skill guides you through understanding the problem, exploring approaches, and creating a comprehensive design document before any implementation.

## When to Use

- User wants to add a new feature
- User has an idea but it's not well-defined
- Before writing an implementation plan
- When exploring architectural decisions

<HARD-GATE>
Do NOT write code, create files (other than design docs), scaffold projects,
or take any implementation action during brainstorming. The professional-mode-guard
hook will block you, but you should not even attempt it. Your job here is to
DESIGN, not IMPLEMENT.
</HARD-GATE>

## MANDATORY: Structured User Input

Whenever soliciting user input — choices, confirmations, or selections — ALWAYS use the `AskUserQuestion` tool. NEVER ask via prose. Follow the format in `.claude/rules/ask-user-question-format.md`: Re-ground context, Predict, Options. This produces structured UI and enforces one-question-at-a-time discipline.

## Common Rationalizations (all wrong)

| Rationalization | Why it's wrong |
|----------------|---------------|
| "This is too simple to brainstorm" | Simple changes break. Every v1.0.x bug came from "simple" changes. |
| "I already know the answer" | Verify with evidence. Confidence is not correctness. |
| "Let me just make this quick fix" | Quick fixes become tech debt. The workflow exists for a reason. |
| "The user said to skip brainstorming" | The user set up professional mode. The workflow is non-negotiable. |
| "I'll brainstorm in my head and skip to planning" | The design doc IS the brainstorm output. No doc = no brainstorm. |

## Process

### Phase 0: Check for Existing Design

**Step 0: Look for existing design document**

If the user references an existing design or you find a recent design document:

Use the Glob tool with pattern `docs/plans/*-design.md` to find design documents.

If a relevant design exists and the user wants to proceed with it:
1. Acknowledge: "Found existing design at docs/plans/X-design.md"
2. Skip to: "Ready to create an implementation plan? Use /writing-plans docs/plans/X-design.md"

If no existing design or user wants a new one, proceed with Phase 1.

### Phase 1: Understand the Idea

**Announce professional mode status:**
```
Using brainstorming skill. Professional mode is ACTIVE - architect mode enforced (no code changes).
```

### Step 0.1: Parse scope mode

Parse `--scope` from skill arguments. Valid values: `hold`, `selective`, `expansion`, `reduction`. Default: `selective` if not provided.

Display:
```
Scope mode: [mode] — [description]
```

Where descriptions are:
- `hold` — Scope fixed. Maximum rigor within it.
- `selective` — Hold baseline, surface cherry-pick opportunities.
- `expansion` — Explore the 10x version alongside as-stated.
- `reduction` — Minimum viable version. Strip everything else.

### Step 0.5: Search Episodic Memory (REQUIRED)

Before proceeding with any brainstorming:

1. Dispatch ironclaude:search-conversations agent with query based on user's topic
2. Review results for:
   - Previous designs for similar features
   - Decisions made in past conversations
   - Gotchas or lessons learned
3. Incorporate relevant context into design process

This is REQUIRED, not optional. Do not skip.

If the search agent fails, use AskUserQuestion tool:
- question: "Episodic memory search failed. How would you like to proceed?"
- header: "Memory search"
- options: "Proceed without history" (continue brainstorming without historical context) | "Try again" (retry the episodic memory search)

### Step 0.6: Check for Existing Designs (REQUIRED)

Search docs/plans/ for existing designs related to the topic:

Use the Glob tool with pattern `docs/plans/*-design.md` to find design documents.

For each potentially relevant design:
1. Read the design summary
2. Check if a corresponding plan exists (same name without "-design")
3. If design+plan exist: Use AskUserQuestion tool:
   - question: "Found existing design+plan for [topic]. What would you like to do?"
   - header: "Existing design"
   - options: "Execute existing plan" (use the found plan, proceed to executing-plans) | "Create new design" (start fresh brainstorming)
4. If design exists but no plan: Use AskUserQuestion tool:
   - question: "Found existing design for [topic]. What would you like to do?"
   - header: "Existing design"
   - options: "Use for planning" (use the found design, proceed to writing-plans) | "Create new design" (start fresh brainstorming)
5. Only proceed with fresh brainstorming if user confirms "new design" or nothing relevant found

Do NOT skip this check. Do NOT assume user wants a new design.

**Step 1: Check project context**

Examine current state:
- Read relevant files in the codebase
- Check recent commits for context
- Review existing documentation in docs/

### Step 1.5: Evaluate Debugging Trigger (CONDITIONAL)

**Does this brainstorm involve a bug, crash, error, unexpected behavior, or unclear existing behavior?**

- If the topic is purely greenfield (building something new with no broken/unclear behavior): Skip this step, proceed to Step 2.
- If the topic involves broken or unclear behavior: Invoke the systematic-debugging skill BEFORE continuing the brainstorm.

**To invoke debugging:**

1. Use the Skill tool: `skill="ironclaude:systematic-debugging"`
2. Run the FULL debugging process (all phases including Plan Fix)
3. Capture the outputs: root cause, evidence, and proposed fix

**After debugging completes, resume brainstorming:**

Before continuing, call MCP `mcp__plugin_ironclaude_state-manager__mark_brainstorming` to transition workflow_stage back to `brainstorming`. If it fails (wrong stage), report the error to the user before proceeding.

- The debugging results become inputs to Phase 2 (Explore Approaches)
- Use confirmed evidence instead of guesswork when exploring approaches
- If debugging fully resolved the issue and no broader design is needed, use AskUserQuestion tool:
  - question: "Debugging found root cause and proposed a fix. What would you like to do?"
  - header: "Debug resolved"
  - options: "Plan the fix directly" (proceed to writing-plans without broader brainstorming) | "Continue brainstorming" (brainstorm broader design context)

**If debugging skill fails to invoke:** Use AskUserQuestion tool (do NOT silently skip):
- question: "Debugging skill failed to invoke. How would you like to proceed?"
- header: "Debug skill fail"
- options: "Try again" (retry invoking the debugging skill) | "Proceed without investigation" (continue brainstorming without debugging)

**Step 2: Ask clarifying questions**

Ask ONE question at a time:
- What is the goal?
- Who will use this?
- What problem does it solve?
- What are the constraints?
- What does success look like?

**Format:** ALWAYS use the AskUserQuestion tool for questions with defined options. For genuinely open-ended questions (no predefined choices), plain text is acceptable.

Example (prediction + AskUserQuestion):
```
**My prediction:** You'll say B because the user mentioned wanting to "add" something new.

[Use AskUserQuestion tool with:]
- question: "What is the primary goal of this feature?"
- header: "Goal"
- options: "Improve performance" | "Add new functionality" | "Fix existing bugs" | "Refactor architecture"
```

**Important:** Before EVERY question, state your prediction with reasoning: "My prediction: You'll say X because..." NEVER ask multiple questions in one message. Wait for answer, then ask next question.

Continue until you have clear understanding of:
- Purpose
- Scope
- Constraints
- Success criteria

### Phase 2: Explore Approaches

**Step 3: Propose 2-3 approaches**

Before presenting approaches to the user, internally validate each against COA criteria:

1. **Suitability** — Does this approach solve the stated problem? Does it comply with the user's guidance and constraints?
2. **Feasibility** — Can the codebase support this? Are required dependencies available? Is the blast radius manageable?
3. **Distinguishability** — Is this approach meaningfully different from the others? Different architecture, different trade-off profile, or different risk posture — not just a minor variation.

Drop or rework any approach that fails these criteria. The user should only see validated options. If you can only find one viable approach after validation, present it as a recommendation with rationale for why alternatives were not viable.

Present different ways to solve the problem:
- Describe each approach in 2-3 sentences
- List trade-offs for each
- Lead with your recommended option
- Explain why you recommend it

Example (prediction + approaches):
```
I recommend Approach B (Event-driven with queue) because it's more scalable and handles failures better.

Approach A: Direct synchronous calls
  + Simple implementation
  + Easy to debug
  - Tight coupling
  - No failure handling

Approach B: Event-driven with queue (Recommended)
  + Loose coupling
  + Handles failures gracefully
  + Scales horizontally
  - More complex setup
  - Eventual consistency

Approach C: Hybrid with caching
  + Fast for common cases
  + Simpler than full event-driven
  - Cache invalidation complexity
  - Still has tight coupling

**My prediction:** You'll say B because you mentioned scalability as a priority.

Which approach fits your needs?
```

**REQUIRED for every approach presentation:**
For each approach, you MUST include:
1. Pros and cons
2. How well it aligns with the user's previously stated guidance, intent, and principles

Do not present approaches without this analysis. The user should never have to ask "how do these align with my guidance?" — that analysis must be proactive.

**Scope mode adjustments to Step 3:**

- **hold:** Only propose approaches that solve the stated problem. Actively push back if an approach introduces scope beyond the original ask.
- **selective:** Complete the core design first. Then, after the core is solid, surface 2-3 specific scope additions — each presented individually via AskUserQuestion for opt-in/opt-out. Never bundle additions.
- **expansion:** Before proposing approaches, ask "what would solving the broader problem look like?" Present the expanded version alongside the as-stated version. Explore whether the stated task is a symptom of something larger.
- **reduction:** Propose the smallest possible change that satisfies the requirement. Challenge every component: "can we ship without this?"

**Step 4: Apply YAGNI ruthlessly**

For chosen approach, identify and remove:
- Features not needed for MVP
- "Nice to have" additions
- Premature optimizations
- Speculative abstractions

Ask: "Do we need X for the first version?" for each questionable feature.

**Scope mode adjustments to Step 4:**

- **hold/reduction:** Challenge aggressively. Default answer is "no, cut it."
- **selective:** Standard YAGNI. Challenge questionable features.
- **expansion:** YAGNI is relaxed during scope exploration. Re-apply after scope is chosen.

### Phase 3: Present Design

**Step 5: Present design in sections**

Break design into ~200-300 word sections:
- Architecture overview
- Component breakdown
- Data flow
- Error handling
- Testing strategy

After EACH section, predict and ask:
```
**My prediction:** You'll say yes because [reasoning based on what was discussed].

Does this section look right so far?
```

Wait for confirmation before continuing to next section.

**Step 6: Handle feedback**

If user says something doesn't look right:
- Ask what specifically is unclear
- Clarify or revise that section
- Re-present the updated section
- Get confirmation before moving on

Be willing to go back and revise ANY section.

### Phase 4: Document Design

**Step 7: Write design document**

Save complete design to `docs/plans/YYYY-MM-DD-<topic>-design.md`:

```markdown
# <Feature Name> Design

> **Created:** YYYY-MM-DD
> **Status:** Design Complete

## Summary

[1-2 paragraphs describing what this is and why]

## Architecture

[Architecture overview with approach chosen]

## Components

[Detailed component breakdown]

## Data Flow

[How data moves through the system]

## Error Handling

[How errors are handled]

## Testing Strategy

[How this will be tested]

## Implementation Notes

[Any important notes for implementation]
```

**Step 8: Stage design document**

Run:
```bash
git add docs/plans/YYYY-MM-DD-<topic>-design.md
```

**Step 8.5: Signal design complete**

Call MCP `mcp__plugin_ironclaude_state-manager__mark_design_ready` with the `file` parameter set to the design document path (e.g., `docs/plans/YYYY-MM-DD-topic-design.md`) to transition the session to `design_ready` and auto-register the design.

If `mcp__plugin_ironclaude_state-manager__mark_design_ready` returns an error (wrong stage), display the error to the user. Do NOT proceed to Step 9 until it succeeds.

**Step 9: Announce completion and prompt for next steps**

<HARD-GATE>
DO NOT write plan content yourself. DO NOT output task lists, waves, or steps.
DO NOT say "I'll write the plan now" and then produce plan prose.
The Skill tool IS the plan-writing process. Invoking it is not optional.
Anything other than invoking the Skill tool is NOT writing a plan.
</HARD-GATE>

Display design completion status:
```
Design complete and saved to docs/plans/YYYY-MM-DD-<topic>-design.md.

Professional mode is ACTIVE - changes staged for your review.
```

Use AskUserQuestion tool:
  question: "Ready to create the implementation plan?"
  header: "Next step"
  options: "Yes, invoke writing-plans" | "No, stop here"

If user confirms: invoke Skill tool IMMEDIATELY with:
  skill: "ironclaude:writing-plans"
  args: "docs/plans/YYYY-MM-DD-<topic>-design.md"

Do NOT produce any text output between the confirmation and the Skill invocation.
If user declines: stop here. The design doc is saved; they can invoke /writing-plans later.

## Key Principles

- **One question at a time**: Never batch questions together
- **Multiple choice preferred**: Easier to answer than open-ended
- **YAGNI ruthlessly**: Remove unnecessary features from all designs
- **Explore alternatives**: Always propose 2-3 approaches before settling
- **Incremental validation**: Present design in sections, validate each
- **Be flexible**: Go back and clarify when something doesn't make sense
- **Always document**: Write to docs/plans/ before proceeding
- **Explicit skill invocation**: When user confirms at Step 9, invoke `Skill tool { skill: "ironclaude:writing-plans", args: "<design-path>" }` immediately. Never substitute prose, task lists, or step descriptions for the Skill invocation. Writing the plan means CALLING THE SKILL TOOL, not describing what you would write.
- **Predict before asking**: Before every question, state your prediction with reasoning ("My prediction: You'll say X because..."). This forces thinking from the user's perspective and is enforced by the get-back-to-work hook.
