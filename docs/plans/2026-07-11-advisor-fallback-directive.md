# Advisor Fallback Directive Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Bake into IronClaude, as defined behavior shipped to all users, the rule that when the harness-injected `advisor` tool is unavailable, Claude spawns a Fable subagent to perform the same adversarial review instead of skipping it — then release it as v1.0.20.

**Architecture:** A new "Advisor Fallback" behavioral directive added to the repo canonical `.claude/rules/behavioral.md`, the brain's own `commander/src/brain/rules/behavioral.md`, and the `activate-professional-mode` skill (templates + concept-detection table — the propagation vector to all users). A presence-guard test locks the directive into each surface. Final task bumps the version to 1.0.20 across the three lockstep files and renames the CHANGELOG heading. No runtime code; documentation/instruction change only.

**Tech Stack:** Markdown/JSON directive files, a pytest presence-guard, `test_version_consistency.py`.

---

## Task 1: Add the directive to both behavioral.md files (+ presence guard)

**Files:**
- Create: `commander/tests/test_advisor_fallback_directive.py`
- Modify: `.claude/rules/behavioral.md`
- Modify: `commander/src/brain/rules/behavioral.md`

**Step 1 (RED): Write the presence-guard test** for the two behavioral.md files.

Create `commander/tests/test_advisor_fallback_directive.py`:
```python
"""Presence guard: the Advisor Fallback directive must exist in every IronClaude
behavioral surface, spelling out the TIER-RELATIVE fallback (Fable if available, else
Opus) — so a future edit cannot silently drop it or reintroduce a hard-coded model."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = "Advisor Fallback"
PRIMARY = "model=fable"   # preferred subagent tier
FALLBACK = "model=opus"   # required fallback when Fable is unavailable


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_directive_in_canonical_behavioral():
    text = _read(".claude/rules/behavioral.md")
    assert MARKER in text
    assert "advisor" in text.lower()
    # both the preferred tier AND its fallback must be named (no hard-coded model)
    assert PRIMARY in text and FALLBACK in text


def test_directive_in_brain_behavioral():
    text = _read("commander/src/brain/rules/behavioral.md")
    assert MARKER in text
    assert PRIMARY in text and FALLBACK in text
```

Run:
```bash
cd commander && .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py -q -p no:cacheprovider
```
Expected: FAIL (directive not yet present in either file).

**Step 2 (GREEN): Append directive #10 to the canonical file.**

Append to `.claude/rules/behavioral.md` (after the `#9` "Right-Size Every Subagent" block, keeping the trailing newline):
```markdown

10. **Advisor Fallback (advisor unavailable ≠ skip the advisor)**
   - When the `advisor` tool returns unavailable, do NOT skip the advisor step and do NOT just "reason it through" yourself.
   - Spawn a top-tier subagent to perform the same role: dispatch it via the `Agent` tool — `model=fable` if Fable is available, otherwise `model=opus` (Fable can be unavailable for the same class of reason the advisor is — never let that skip the review). Give it the same context and a focused, report-only adversarial-review prompt — the task, the change or decision, the evidence, and the specific questions to pressure-test.
   - Weight its findings as you would the advisor's; reconcile conflicts with evidence.
   - "No advisor" means "use a subagent for the same effect," never "proceed unreviewed."
```

**Step 3 (GREEN): Append directive #23 to the brain copy.**

Append to `commander/src/brain/rules/behavioral.md` (after `#22` "Verify Worker Reasoning Integrity"):
```markdown

23. **Advisor Fallback** — When the `advisor` tool returns unavailable, do NOT skip the advisor step or just reason it through yourself. Spawn a top-tier subagent via the `Agent` tool (`model=fable` if Fable is available, else `model=opus`) with the same context and a focused, report-only adversarial-review prompt (the task, the change/decision, the evidence, the specific questions) and weight its findings as you would the advisor's. "No advisor" means "use a subagent for the same effect," never "proceed unreviewed."
```

**Step 4: Run the test — verify GREEN.**
```bash
cd commander && .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py -q -p no:cacheprovider
```
Expected: 2 passed.

**Step 5: Stage.**
```bash
git add .claude/rules/behavioral.md commander/src/brain/rules/behavioral.md commander/tests/test_advisor_fallback_directive.py
```

---

## Task 2: Add the directive to the activate-professional-mode skill (+ extend guard)

**Files:**
- Modify: `worker/skills/activate-professional-mode/SKILL.md`
- Modify: `commander/tests/test_advisor_fallback_directive.py`

**Depends on:** Task 1 (both edit the test file; keep sequential).

**Step 1 (RED): Extend the presence guard to the skill.**

Append to `commander/tests/test_advisor_fallback_directive.py`:
```python


def test_directive_in_activation_skill():
    text = _read("worker/skills/activate-professional-mode/SKILL.md")
    # assert each of the FIVE distinct edits individually. '9. **Advisor Fallback**'
    # alone is shared by the compact one-liner AND the full block, so guard them
    # apart: the compact one-liner via its em-dash form, and the two full-form
    # blocks (full template + Concept 9) via a bulleted phrase they alone carry.
    assert "**Advisor Fallback** — If the" in text            # compact CLAUDE.md one-liner
    assert text.count("just reason it through") >= 2          # full template block + Concept 9 block
    assert "| 9 | Advisor Fallback |" in text                 # concept-detection table row (propagation vector)
    assert "Concept 9 (Advisor Fallback):" in text            # append-path canonical block header
    assert "9 concepts" in text                                # count string bumped 8->9
    assert "(9 principles)" in text                            # count string bumped 8->9
    assert "9-principle template" in text                      # both 8-principle mentions bumped
    assert PRIMARY in text and FALLBACK in text                # tier-relative fallback named
```

Run:
```bash
cd commander && .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py::test_directive_in_activation_skill -q -p no:cacheprovider
```
Expected: FAIL (skill not yet updated).

**Step 2 (GREEN): Add a line to the compact CLAUDE.md template.**

In `worker/skills/activate-professional-mode/SKILL.md`, in the compact CLAUDE.md template, after the `8. **No Sycophantic Responses**` one-liner and before the `Full behavioral rules:` line, add:
```markdown
9. **Advisor Fallback** — If the `advisor` tool is unavailable, spawn a top-tier subagent (`Agent`, `model=fable` if Fable is available else `model=opus`) to do the same adversarial review; never skip the advisor step.
```

**Step 3 (GREEN): Add a block to the full behavioral.md template.**

In the same file, in the full `.claude/rules/behavioral.md` template, after the `8. **No Sycophantic Responses**` block, add:
```markdown

9. **Advisor Fallback**
   - When the `advisor` tool returns unavailable, do NOT skip the advisor step or just reason it through yourself
   - Spawn a top-tier subagent via the `Agent` tool (`model=fable` if Fable is available, else `model=opus`) with the same context and a focused, report-only adversarial-review prompt (task, change/decision, evidence, specific questions)
   - Weight its findings as you would the advisor's; "no advisor" means "use a subagent for the same effect," never "proceed unreviewed"
```

**Step 4 (GREEN): Add row 9 to the concept-detection table** (after the `| 8 | No Sycophantic Responses | ... |` row):
```markdown
| 9 | Advisor Fallback | Instructions to substitute a subagent (Fable if available, else Opus) for the advisor's review when the `advisor` tool is unavailable, rather than skipping the review |
```

**Step 5 (GREEN): Add the Concept 9 canonical text** (after the `Concept 8 (No Sycophantic Responses):` fenced block). NOTE: write PLAIN triple-backtick fences into SKILL.md — the block below shows them prefixed with a zero-width space only so the nested fence renders inside this plan; strip that when writing the file:
```markdown
Concept 9 (Advisor Fallback):
​```
N. **Advisor Fallback**
   - When the `advisor` tool returns unavailable, do NOT skip the advisor step or just reason it through yourself
   - Spawn a top-tier subagent via the `Agent` tool (`model=fable` if Fable is available, else `model=opus`) with the same context and a focused, report-only adversarial-review prompt (task, change/decision, evidence, specific questions)
   - Weight its findings as you would the advisor's; "no advisor" means "use a subagent for the same effect," never "proceed unreviewed"
​```
```

**Step 6 (GREEN): Update the count strings.** In the same file there are THREE count-string occurrences (not two): change "For each of the 8 concepts below" → "For each of the 9 concepts below" (~line 126); change "(8 principles)" → "(9 principles)" (~line 119); and change BOTH "8-principle template" mentions → "9-principle template" (~lines 147 AND 232). Use replace-all on "8-principle template" so the second one is not left stale.

**Step 7: Run the full guard — verify GREEN.**
```bash
cd commander && .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py -q -p no:cacheprovider
```
Expected: 3 passed.

**Step 8: Stage.**
```bash
git add worker/skills/activate-professional-mode/SKILL.md commander/tests/test_advisor_fallback_directive.py
```

---

## Task 3: v1.0.20 release prep

**Files:**
- Modify: `commander/pyproject.toml`
- Modify: `worker/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `CHANGELOG.md`

**Depends on:** Task 2.

**No tests required:** version bump + documentation. Verification is `test_version_consistency.py` (already exists).

**Step 1:** In `commander/pyproject.toml` change `version = "1.0.19"` → `version = "1.0.20"`.

**Step 2:** In `worker/.claude-plugin/plugin.json` change `"version": "1.0.19"` → `"version": "1.0.20"`.

**Step 3:** In `.claude-plugin/marketplace.json` change the `plugins[0].version` `"1.0.19"` → `"1.0.20"`.

**Step 4:** In `CHANGELOG.md`, edit the `[Unreleased]` section into the `1.0.20` release. Four sub-edits:

(a) Rename the heading `## [Unreleased]` → `## 1.0.20`.

(b) Immediately under the heading, add a one-paragraph summary (house style, matching 1.0.17–1.0.19):
```markdown
A grader-transport overhaul (the inline grader now runs a tool-free `claude -p` subprocess with a hard timeout instead of scraping a persistent tmux pane) plus a new **Advisor Fallback** behavioral directive that makes "advisor unavailable" mean "spawn a top-tier subagent for the same review," never "skip it."
```

(c) Add — at the top of that section's body — an `### Added` entry for the directive:
```markdown
### Added
- **Advisor Fallback directive** (`.claude/rules/behavioral.md` #10, `commander/src/brain/rules/behavioral.md` #23, and the `activate-professional-mode` templates + concept-detection table). When the harness-injected `advisor` tool is unavailable, Claude must spawn a top-tier subagent (`Agent`, `model=fable` if Fable is available, else `model=opus`) to perform the same adversarial review rather than skipping it. Baked into the activation skill so it propagates to every IronClaude project (new projects at creation, existing projects on next `/activate-professional-mode`). Presence-guarded by `commander/tests/test_advisor_fallback_directive.py`.
```

(d) **Fix the now-false Deploy line.** The existing Deploy line (carried from the grader-transport entry) reads "daemon code only — restart the daemon after this ships (no hook/skill/plugin change)". Under 1.0.20 that is wrong — the Advisor Fallback change edits the `activate-professional-mode` skill (part of the worker plugin) and bumps the plugin version. Replace it with:
```markdown
**Deploy:** (1) restart the daemon (the grader-transport change is daemon code); (2) `claude plugin update` + `/reload-plugins` to pick up the `activate-professional-mode` skill change; the `behavioral.md` directives ship with the repo and reach a project on its next `/activate-professional-mode`.
```

**Step 5: Verify version lockstep.**
```bash
cd commander && .venv/bin/python -m pytest tests/test_version_consistency.py -q -p no:cacheprovider
```
Expected: 1 passed (all three sources = 1.0.20).

**Step 6: Stage.**
```bash
git add commander/pyproject.toml worker/.claude-plugin/plugin.json .claude-plugin/marketplace.json CHANGELOG.md
```
