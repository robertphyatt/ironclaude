---
name: use-fable-subagent
description: Use when a Codex user explicitly requests an actual Claude Fable subagent for one bounded report-only analysis or review; prevents model substitution, repository writes, and orchestration transfer
---

# Use an Actual Claude Fable Subagent from Codex

## Boundary

Native Codex subagents cannot satisfy an actual-Claude-Fable request. Do not substitute
Codex, Opus, Sonnet, Haiku, a semantic worker label, or a requested model
string. This skill invokes the installed Claude CLI and accepts output
only after the bundled launcher verifies effective model identity from
assistant `message.model` events.

This is a bounded report-only consultation. It never receives repository
writes, orchestration, workflow-state changes, staging, commits, pushes, task sequencing,
or operator-authority duties. Parent Codex session retains all of
those responsibilities.

Do not use this path for ordinary Codex advisor work. Keep
`run_codex_advisor_review` as Codex-native advisor path. Do not use it to spawn
or manage Commander `claude-fable` workers.

## Procedure

1. Load and apply `ironclaude:write-lossless-ai-messages` before constructing
   AI-directed prompt.
2. Build one complete, self-contained prompt containing:
   - exact task;
   - relevant evidence, including needed code or quoted artifacts;
   - operator and workflow constraints;
   - requested deliverable and decision questions;
   - explicit report-only instruction: no tools, repository reads, repository
     writes, orchestration, or external actions.
3. Exclude secrets, credentials, and irrelevant conversation history. Do not
   assume Fable can read current repository or conversation.
4. Create a fresh owner-only temporary directory with mode 0700 and a new UTF-8
   prompt file with mode 0600. Before invocation, verify exact prompt path is a
   regular file owned by current user and not a symlink. Use native file-writing
   operation; do not interpolate prompt content into shell syntax.
5. Resolve this installed skill's absolute directory from active skill catalog,
   then run:

   ```bash
   python3 <ABSOLUTE_SKILL_DIRECTORY>/scripts/run_fable_subagent.py --prompt-file <ABSOLUTE_PRIVATE_PROMPT_PATH>
   ```

6. Treat any nonzero exit or `FABLE_SUBAGENT_ERROR` as terminal. Report exact
   bounded error and stop. Do not retry through another model and do not
   silently downgrade.
7. On success, accept report only because launcher verified one effective
   Fable identity from assistant `message.model`. Requested alias, argv,
   `advisorModel`, worker type, or prose claim is not identity evidence.
8. Independently verify every material finding against repository evidence
   before acting. Fable report is advice, not authority.
9. In cleanup guaranteed for success, failure, or interruption, remove exact private prompt file
   and then its exact temporary directory. Never remove
   broad, unresolved, symlink-resolved, or repository paths.

## Prompt Template

```text
REPORT-ONLY ACTUAL CLAUDE FABLE CONSULTATION

Task:
<one bounded question>

Evidence:
<complete relevant facts and quoted artifacts>

Constraints:
<operator, safety, workflow, and scope boundaries>

Requested deliverable:
<specific analysis, findings, or recommendation>

Do not use tools, read or write repository files, change workflow state,
or take external action. Return report only. State evidence gaps explicitly.
```

## Failure Rules

- Fable unavailable: stop with exact launcher error; no fallback.
- Missing, mixed, or non-Fable `message.model`: reject entire report.
- Timeout or output overflow: report bounded failure after launcher terminates
  and reaps consultation process group.
- Incomplete prompt packet: repair packet before invocation; never invite
  Fable to discover missing repository context itself.
