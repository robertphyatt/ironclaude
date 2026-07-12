# Advisor Fallback Directive Design

> **Created:** 2026-07-11
> **Status:** Design Complete
> **Scope mode:** hold (fixed scope; maximum rigor within it)

## Summary

Bake into IronClaude — as **defined behavior shipped to all users** — the rule that
when the harness-injected `advisor` tool returns unavailable, Claude must **spawn a
Fable subagent to perform the same adversarial-review role**, not silently skip the
advisor step. This session surfaced the gap live: the `advisor` tool returned
"unavailable" and the assistant initially said "I'll reason it through" instead of
substituting a subagent — the operator corrected this and directed that it become
permanent, propagated behavior, not a one-off or a personal memory.

The `advisor` tool itself and its "# Advisor Tool" usage instructions are **injected
by the harness/session infrastructure and are not present in the IronClaude repo**
(confirmed by grep across the repo, hooks dir, and plugin cache). Therefore IronClaude
cannot edit the tool or its error message. The only surfaces IronClaude owns are its
**behavioral-directive files** and the **activate-professional-mode skill** that
deploys/upgrades those files for every user. This design adds the directive there.

## Architecture

A new numbered behavioral directive, **"Advisor Fallback,"** added to IronClaude's
owned instruction surfaces. **No runtime code, no hooks.** An enforcement hook
(considered as Option C) was rejected: the advisor tool is harness-injected and its
"unavailable" result is not reliably observable by a PreToolUse/PostToolUse hook, so a
hook would be fragile and likely never fire — and enforcing a judgment call is
disproportionate for a guidance directive. This is a documentation/instruction change
propagated through the established directive-addition pattern (the same path by which
directive #9 "Right-Size Every Subagent" was added).

Propagation mechanism: the `activate-professional-mode` skill (a) writes its CLAUDE.md
compact template + full `.claude/rules/behavioral.md` template into **new** projects,
and (b) runs a **concept-detection table** on activation that appends any **missing**
concept to an existing `behavioral.md`. Adding "Advisor Fallback" as a new concept in
that table + templates is what makes every IronClaude user pick it up (new projects at
creation, existing projects on next activation).

## Components

Edits (exact files):

1. **`.claude/rules/behavioral.md`** (repo canonical, currently 9 principles) — append
   **#10 Advisor Fallback**.
2. **`commander/src/brain/rules/behavioral.md`** (brain's own copy, currently 22
   directives) — append **#23 Advisor Fallback**, brain-framed (the brain is a Claude
   Code orchestrator session and has the advisor tool too).
3. **`worker/skills/activate-professional-mode/SKILL.md`** (the skill SOURCE in the
   repo; the plugin is built from `worker/`) — four coordinated edits:
   - Compact **CLAUDE.md template** (currently lists 8 core directives) → add
     **9. Advisor Fallback** one-liner.
   - Full **behavioral.md template** (currently 8 principles) → add the full
     **9. Advisor Fallback** block.
   - **Concept-detection table** (currently 8 rows) → add **row 9 | Advisor Fallback |
     Covered if the file contains instructions to substitute a subagent/Fable review
     when the advisor tool is unavailable, rather than skipping review**.
   - Add a **Concept 9 canonical text** block (for the "append missing concept" path)
     and bump the "Created ... (8 principles)" display strings to "(9 principles)".
4. **v1.0.20 release prep** (final task): bump the version in
   `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, and
   `.claude-plugin/marketplace.json` from 1.0.19 → **1.0.20** (kept in lockstep by
   `commander/tests/test_version_consistency.py`); rename the CHANGELOG `## [Unreleased]`
   heading to `## 1.0.20` and add an entry for the Advisor Fallback directive (the
   grader-transport entry already sits under [Unreleased] from the prior plan and rolls
   into 1.0.20).

### Directive text (canonical / worker form)

```
N. **Advisor Fallback (advisor unavailable ≠ skip the advisor)**
   - When the `advisor` tool returns unavailable, do NOT skip the advisor step and do
     NOT just "reason it through" yourself.
   - Spawn a top-tier subagent to perform the same role: dispatch it via the `Agent`
     tool — `model=fable` if Fable is available, otherwise `model=opus` (Fable can be
     unavailable for the same class of reason the advisor is — never let that skip the
     review). Give it the same context and a focused, report-only adversarial-review
     prompt — the task, the change or decision, the evidence, and the specific
     questions to pressure-test.
   - Weight its findings as you would the advisor's; reconcile conflicts with evidence.
   - "No advisor" means "use a subagent for the same effect," never "proceed unreviewed."
```

**Fallback is tier-relative, not a hard-coded model.** The remedy must not name a single
model that can itself be unavailable: Fable is redirected to Opus by
`~/.ironclaude/state/fable_unavailable.json` and has been removed for subscription users,
so a directive that said only `model=fable` would prescribe an unavailable remedy for a
tool-unavailability failure — self-defeating. The directive therefore reads "Fable if
available, else Opus" (top available tier). The brain copy (#23) uses the same substance,
framed for the brain (it likewise has the advisor tool).

## Data Flow

Not a runtime data-flow change. The flow is authoring-time:
`activate-professional-mode` → writes/updates CLAUDE.md + `.claude/rules/behavioral.md`
in the user's project → the directive is in the model's loaded context each session →
at an advisor call site, if the tool is unavailable, the model dispatches a Fable
subagent instead of skipping.

## Error Handling

- Version lockstep: all three version files must match or `test_version_consistency.py`
  fails — the release-prep task edits all three together and runs that test.
- Idempotence: the concept-detection table entry makes re-activation a no-op once the
  concept is present (semantic match), so users are not spammed with duplicate text.
- No new failure modes are introduced at runtime (documentation-only behavioral change).

## Testing Strategy

- **Presence guard** (mirrors the existing `test_writing_plans_skill.py` /
  `test_executing_plans_skill.py` pattern): assert the "Advisor Fallback" directive text
  is present in `.claude/rules/behavioral.md` and in the
  `activate-professional-mode/SKILL.md` templates + concept table, so a future edit
  can't silently drop it. (Directive files are Python-testable via file reads.)
- **`test_version_consistency.py`** stays green after the 1.0.20 bump (run it).
- No runtime unit tests — there is no new executable code; the change is instruction
  text plus a version bump.

## Implementation Notes

- **Out of scope (`--scope=hold`):** the pre-existing drift where the activation skill's
  templates/concept-table (8 concepts) lag the canonical `.claude/rules/behavioral.md`
  (which already has #9 "Right-Size Every Subagent" that never propagated to the
  templates). This design does NOT reconcile that; it only adds Advisor Fallback as the
  next concept. Per-file directive numbers therefore differ (canonical #10, template #9),
  which is harmless because concept detection matches **semantically**, not by number.
  Flagged for a possible future template-sync pass.
- **Not touched:** the many divergent top-level `CLAUDE.md` files
  (`worker/CLAUDE.md`, `commander/CLAUDE.md`, repo `CLAUDE.md`, `~/.claude/CLAUDE.md`,
  etc.) — the operator-approved surface is the canonical `behavioral.md` + brain copy +
  activation templates. The compact CLAUDE.md **template** in the activation skill is the
  propagation vector for new projects; existing per-project CLAUDE.md files are indexes
  that point at `behavioral.md` and are intentionally left to each project.
- **Deploy:** the directive text ships with the repo/plugin; `make deploy-hooks` is not
  required (no hook change). To propagate to a user's project, run
  `/activate-professional-mode` there (concept-detection appends the missing directive).
  The brain copy takes effect on daemon restart.
- **Do NOT push:** the operator commits and pushes manually; this cycle only stages the
  changes and prepares the v1.0.20 release.
