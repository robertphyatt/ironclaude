---
name: writing-skills
description: Create and validate new skills using TDD methodology
---

# Writing Skills

## Purpose

Create and validate new IronClaude skills using test-driven development methodology. This skill guides you through the entire skill creation lifecycle: drafting, testing, and deploying.

## When to Use

- User invokes `/writing-skills` to create a new skill
- During skill maintenance or updates
- When testing existing skills for quality

## Process

### Phase 1: Initial Draft

**Step 1: Gather requirements**

Ask one question at a time:
1. What is the skill name? (lowercase-with-hyphens)
2. What is the skill's purpose? (one sentence)
3. When should this skill be used?
4. What are the key steps in the workflow?

**Step 2: Generate SKILL.md template**

Create the skill file at `plugins/ironclaude/skills/<skill-name>/SKILL.md` with:

```markdown
---
name: <skill-name>
description: <purpose from user>
---

# <Skill Name Title>

## Purpose

[Detailed purpose]

## When to Use

[Invocation patterns]

## Process

[Step-by-step workflow]

## Key Principles

[Important guidelines]
```

### Phase 2: RED - Test Without Skill

**Step 3: Create pressure scenario**

Document a realistic scenario where Claude would need this skill:
- Typical user request
- Expected behavior without skill
- Problems that occur without skill guidance

**Step 4: Test with subagent (no skill)**

Spawn a subagent WITHOUT access to the new skill:
- Give it the pressure scenario
- Observe failures, gaps, or poor quality
- Document what goes wrong

Create documentation file: `plugins/<skill-name>/test-scenarios/01-without-skill.md`

Expected failures might be:
- Forgot critical steps
- Inconsistent approach
- Missing validation
- Poor user experience

### Phase 3: GREEN - Minimal Working Skill

**Step 5: Write minimal skill content**

Based on observed failures, write the minimal SKILL.md that addresses them:
- Add workflow steps that were missed
- Add validation checks that were skipped
- Add user communication that was missing
- Keep it simple - don't over-engineer

**Step 6: Test with subagent (with skill)**

Spawn a subagent WITH access to the new skill:
- Give it the same pressure scenario
- Use Skill tool to invoke the skill
- Observe that it follows the skill correctly
- Document improvements

Create documentation file: `plugins/<skill-name>/test-scenarios/02-with-skill.md`

Expected: Subagent follows skill workflow, addresses previous failures

### Phase 4: REFACTOR - Close Loopholes

**Step 7: Identify loopholes**

Review the skill content and subagent behavior:
- Are there edge cases not covered?
- Can steps be clearer?
- Are there ways to skip important steps?
- Does it integrate well with professional mode?

**Step 8: Add clarity and constraints**

Refine the SKILL.md:
- Add "MUST" and "MUST NOT" language for critical steps
- Add examples for complex concepts
- Add error handling guidance
- Add integration notes (which other skills to invoke)

**Step 9: Final validation test**

Spawn one more subagent:
- Test with edge cases
- Try to break the skill intentionally
- Verify it handles unexpected input gracefully

Create documentation file: `plugins/<skill-name>/test-scenarios/03-edge-cases.md`

Expected: Skill handles edge cases appropriately

### Phase 5: Deploy

**Step 10: Format validation**

Run validation checks:

```bash
# Check YAML frontmatter is valid
head -5 plugins/<skill-name>/skills/<skill-name>/SKILL.md | grep -E "^(---|name:|description:)"

# Check file is in correct location
test -f plugins/<skill-name>/skills/<skill-name>/SKILL.md && echo "✓ Location correct"

# Verify it's actually SKILL.md (uppercase)
ls plugins/<skill-name>/skills/<skill-name>/ | grep -E "^SKILL.md$" && echo "✓ Filename correct"
```

Expected: All checks pass

**Step 11: Stage changes**

Run:
```bash
git add plugins/<skill-name>/skills/<skill-name>/SKILL.md
git add plugins/<skill-name>/test-scenarios/
```

Expected: Changes staged (professional mode blocks commit)

## Key Principles

- **TDD Always**: RED (test without) → GREEN (minimal working) → REFACTOR (close loopholes)
- **Test with Subagents**: Don't assume - spawn subagents and observe real behavior
- **Document Tests**: Keep test scenarios in test-scenarios/ directory
- **One Question at a Time**: During requirements gathering, never batch questions
- **YAGNI**: Minimal content that solves the problem - no more
- **Professional Mode Aware**: All skills should work with professional mode active
