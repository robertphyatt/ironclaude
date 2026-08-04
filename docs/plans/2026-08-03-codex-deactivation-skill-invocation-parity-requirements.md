# Codex Deactivation Skill-Invocation Parity Requirements

> **Created:** 2026-08-03
> **Status:** Requirements Confirmed
> **Scope mode:** hold

## Goal

Restore the existing professional-mode deactivation feature so a human's standalone Codex
Desktop skill-chip invocation has the same effect as `/deactivate-professional-mode`.

## Verified problem

Codex Desktop submits the skill chip to `UserPromptSubmit` as a Markdown link:

```text
[$ironclaude:deactivate-professional-mode](/absolute/plugin/path/skills/deactivate-professional-mode/SKILL.md)
```

`worker/hooks/state-activator.sh` currently recognizes the two slash forms and the exact bare
`$ironclaude:deactivate-professional-mode` token. It does not recognize the Markdown wrapper, so
the hook leaves professional mode on and the skill's subsequent state check truthfully reports
failure. Source, stable runtime, and plugin-cache copies have identical behavior; this is not a
deployment, state-manager identity, or authorization failure.

## Required behavior

1. A standalone Codex Markdown skill link whose label is exactly
   `$ironclaude:deactivate-professional-mode` and whose absolute-path target ends exactly in
   `/skills/deactivate-professional-mode/SKILL.md` must enter the existing human-only deactivation
   path.
2. Outer whitespace around that standalone link must remain harmless.
3. Existing accepted forms must continue to work:
   `/deactivate-professional-mode`, `/ironclaude:deactivate-professional-mode`, and the exact bare
   `$ironclaude:deactivate-professional-mode` token.
4. Mentions in prose, code spans, escaped labels, relative or non-file targets, wrong skill paths,
   label case changes, and prefix/suffix variants must remain ignored.
5. Existing session scoping, active-task workflow-stage preservation, audit logging, and
   human-only ownership of the off transition must remain unchanged.

## Scope boundaries

- No new command surface, parser, state transition, fallback, retry, or user-visible behavior.
- No changes to state-manager identity or authorization, database schema, activation, Commander,
  provider routing, token accounting, or skill outcome text.
- No broad substring or generic Markdown-link matching.
- No commit or push in this loop unless separately authorized.

## Acceptance evidence

- Focused hook test fails before implementation for the actual Codex Markdown payload.
- Focused hook test passes afterward for all positive and negative fixtures.
- Existing Commander deactivation parity tests and relevant hook tests pass.
- Repository source and active stable hook are byte-identical after bounded deployment.
- The deployed stable hook passes the exact Codex UI payload in the isolated SQLite harness, while
  provider-native verification confirms agent-run tests did not deactivate the live session.
- The next standalone human skill-chip invocation is reported as the live human-only acceptance
  event; the agent does not synthesize that prompt or mutate live state.
