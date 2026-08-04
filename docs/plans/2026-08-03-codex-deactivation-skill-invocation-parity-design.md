# Codex Deactivation Skill-Invocation Parity Design

> **Created:** 2026-08-03
> **Status:** Design Complete
> **Scope mode:** hold

## Summary

Normalize the exact standalone Markdown envelope emitted by the Codex Desktop skill chip into the
already-supported bare dollar invocation before the existing deactivation branch runs. Keep the
human-only database mutation, session selection, active-task guard, logging, and verification
unchanged.

Requirements:
`docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity-requirements.md`.

## Root cause and constraints

The prior parity repair added an exact comparison for the documented bare dollar token. Current
rollout evidence shows Codex Desktop serializes the UI chip as:

```text
[$ironclaude:deactivate-professional-mode](/Users/.../skills/deactivate-professional-mode/SKILL.md)
```

The focused current-hook reproduction leaves state at `on|brainstorming` for that standalone
payload while the slash form changes it to `off|idle`. Runtime and source hook hashes match. The
defect is therefore a missing input shape at the hook boundary, not downstream state handling.

An earlier broad matcher deactivated professional mode when prompts merely mentioned the command.
This repair must remain standalone, anchored, case-sensitive, and path-bounded.

## Approaches considered

### 1. Exact envelope normalization — selected

After existing outer-whitespace trimming, recognize one anchored shape:

- label exactly `$ironclaude:deactivate-professional-mode`;
- target begins with `/`, proving an absolute local path;
- target contains no closing parenthesis;
- target ends exactly with `/skills/deactivate-professional-mode/SKILL.md`;
- no characters exist before or after the link.

Normalize that shape to the existing canonical bare token, then reuse the existing exact-match
branch. This changes only input recognition and works across user homes and plugin versions.

### 2. Generic Markdown-link parser — rejected

A parser adds machinery for a single stable prompt shape and creates unrequested behavior for
other labels and targets.

### 3. Broad substring or label-only match — rejected

This recreates the historical bug where prose mentioning deactivation changed state.

### 4. State-manager or skill-side fallback — rejected

The off transition is intentionally human-only and hook-owned. Letting the agent or skill mutate
state would weaken that invariant and would not fix the missing hook input contract.

## Implementation

### Hook recognition

File: `worker/hooks/state-activator.sh`

Keep slash matching unchanged. In the non-slash branch, retain outer-whitespace trimming. If the
trimmed prompt matches the exact Codex link envelope, replace the local trimmed value with the
canonical bare dollar token. The existing case-sensitive equality test then sets
`DEACTIVATE_REQUEST=true`.

No downstream SQL, audit, session, or workflow-stage code changes.

### Regression tests

File: `worker/hooks/tests/test-professional-mode-deactivation.sh`

Add protocol-shaped positive fixtures for the standalone Codex link and outer whitespace. Add
negative fixtures for embedded prose, wrong terminal skill path, relative target, URL target,
label case change, code-span wrapping, and suffix text. Retain every existing slash, bare-token,
active-task, other-session, and zero-row assertion.

The positive protocol fixture must be introduced and run before implementation to preserve RED
before GREEN. Tests exercise the real hook against SQLite state, so a matcher-only test cannot
produce a false GREEN while the state transition remains disconnected.

## Deployment and verification

1. Run the focused hook test and existing Commander deactivation parity test.
2. Run relevant hook regression coverage and `git diff --check`.
3. Deploy hook source through the existing bounded hook deployment path so
   `~/.claude/ironclaude-hooks/state-activator.sh`—the path executed by `hooks.json`—matches repo
   source byte-for-byte. Do not alter deployment architecture or plugin versioning.
4. Run the exact Codex payload against the deployed stable hook in the isolated SQLite harness,
   then query provider-native professional-mode state and require that agent-run verification left
   the live session `on`. The next standalone human skill-chip invocation is the human-only live
   acceptance event; the agent does not synthesize it.

## Risks and controls

- **Accidental deactivation from prose:** exact whole-prompt anchoring plus negative fixtures.
- **Accepting another skill:** exact label and terminal skill path.
- **Machine-specific path:** match an absolute path with the exact suffix, not a username or
  plugin version.
- **Testing only source while runtime remains stale:** byte comparison against the stable executed
  hook plus live same-session state verification.
- **Scope expansion:** only the hook and its focused test are implementation files; all existing
  state behavior is reused.
