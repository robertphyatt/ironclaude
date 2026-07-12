# CHANGELOG entry for the kill_worker directive fast-path — Design

> **Created:** 2026-07-11
> **Status:** Design Complete
> **Scope mode:** hold (single documentation edit)

## Summary

The v1.0.20 staged changeset already contains a concurrent worker's `kill_worker`
directive fast-path (code in `commander/src/ironclaude/orchestrator_mcp.py`, tests in
`commander/tests/test_orchestrator_mcp.py`, and its own design/plan docs) but it is
**not documented in the CHANGELOG**. A Fable-subagent review recommended KEEP-and-
document: the change is opt-in, backward-compatible, degrades gracefully, and is
tested, so it is safe to ship in v1.0.20 — provided it is not shipped silently. This
change adds the missing CHANGELOG entry (including the deliberate enforcement-weakening
caveat) so the release note matches what ships.

## Architecture

Documentation-only. Add one `### Added` bullet under the existing `## 1.0.20` heading in
`CHANGELOG.md`. No code, no tests.

## Components

- `CHANGELOG.md` — add a `### Added` entry (or extend the existing `### Added` block
  under `## 1.0.20`) describing the `kill_worker` `directive_id` fast-path and its
  opt-in enforcement-relaxation caveat.

## Data Flow

N/A (documentation).

## Error Handling

N/A. Verification is that the existing `test_version_consistency.py` still passes (the
version is unchanged at 1.0.20) and the CHANGELOG remains well-formed.

## Testing Strategy

No tests required: documentation-only CHANGELOG addition. Sanity-verify by reading the
edited section.

## Implementation Notes

- The kill_worker CODE and its tests are already staged (concurrent worker); this task
  ONLY adds the CHANGELOG note — it does not touch `orchestrator_mcp.py` or any test.
- Do NOT push; the operator commits manually.
