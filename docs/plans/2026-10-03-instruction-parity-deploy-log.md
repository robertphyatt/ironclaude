# Instruction Parity — Verification and Deploy Log (v1.1.16 L2b-2)

> **Date:** 2026-10-03
> **Plan:** `docs/plans/2026-10-03-instruction-parity.md`, Task 5
> **Requirements:** R8 (verify and redeploy), R9 (the final Fable gate)

## Task results before deploy

| Task | Result | Grade |
|---|---|---|
| 1. Measure | The three classification lines are listed below. `~/.codex/config.toml` is restored to its snapshot. | A |
| 2. Repo instruction files and parity test | `AGENTS.md`, `CLAUDE.md` and `behavioral.md` now share the same 15 directives. `AGENTS.md` alone keeps the Fable directive, as 16. The parity test failed 2 of 3 before the change and passes 3 of 3 after. | B |
| 3. Brainstorming entry, memory gate, writing-plans gate removed | `test-memory-search-gate.sh` ran 9/19 before the change and 19/19 after. | B |
| 4. Global files and Codex rules | Backups taken. The two global files now differ only in the title and the search line. `default.rules` is down to 5 lines. | A |

Task 1 measured these values:

```
CODEX_EPISODIC_SEARCH_TOOL=mcp__episodic_memory__search
CLAUDE_EPISODIC_SEARCH_TOOL=mcp__plugin_ironclaude_episodic-memory__search
CLAUDE_TASK_MATCHER_FIRES_FOR_AGENT=yes
```

Two expected values in the plan were wrong. The code behaved correctly in both cases:
- **The `jq` precedence check in Task 3.** The plan's `[A | length, B]` fails. With the parentheses added, `[(A | length), B]` prints the expected result: `7`, then the matcher.
- **`test-skill-state-bridge-inherit-review.sh`.** The plan expected a "Results:" line. The script prints a single `PASS` line by design and has not changed since v1.1.6. It exited 0.

## Step 1: hook suites

`make -C /Users/roberthyatt/Code/ironclaude test-hooks` exited 0. A search for any non-zero fail count found only a section heading.

```
--- worker/hooks/tests/test-memory-search-gate.sh ---
Results: 19 pass, 0 fail
```

## Step 2: Commander suite

```
3460 passed in 407.22s (0:06:47)
exit=0
```

## Step 3: deploy hooks

```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```

## Steps 4–7: the Claude plugin's hooks.json

`installed_plugins.json:27` gives the install path:

```
"installPath": "/Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15"
```

Before the copy, the installed `hooks.json` differed from the repo in one place:

```
124c124
<         "matcher": "Task|Agent|mcp__plugin_ironclaude_episodic-memory__search|mcp__episodic_memory__search"
---
>         "matcher": "Task"
```

After `cp`, the diff printed nothing and exited 0.

## Steps 8–9: the Codex plugin cache

```
"version": "1.1.15+codex.20260930172837",
"installedPath": "/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837",
```

`diff -rq` of `worker/` against the Codex cache printed nothing and exited 0.

## Step 10: drift in the Claude skill cache (nothing copied)

```
Files .../worker/skills/brainstorming/SKILL.md and .../1.1.15/skills/brainstorming/SKILL.md differ
Files .../worker/skills/code-review/SKILL.md and .../1.1.15/skills/code-review/SKILL.md differ
Files .../worker/skills/executing-plans/SKILL.md and .../1.1.15/skills/executing-plans/SKILL.md differ
Files .../worker/skills/writing-plans/SKILL.md and .../1.1.15/skills/writing-plans/SKILL.md differ
```

This drift predates this loop. It has no effect, because Claude loads its skills from the repository: every skill invoked in this session resolved to `/Users/roberthyatt/Code/ironclaude/worker/skills/...`.

## Final gate and order (R9)

1. **Final Fable end review.** A Fable end review of the full staged diff for L2b-2 must come back CLEAN. If it reports any material finding, that finding is fixed and the review runs again.
2. **Commit.** Only after that does the operator commit, with no trailers and no push. The agent does not commit.
3. **Re-trust the Codex hook.** After the commit, the operator re-trusts the changed Codex hook `post_tool_use:2`, the memory-search tracker, whose matcher changed. Then quit and relaunch Codex.
4. **New Claude sessions.** New Claude sessions pick up the new `hooks.json` matcher and skills at start.
