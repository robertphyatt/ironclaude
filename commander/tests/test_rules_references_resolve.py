"""Every rules-file reference in a skill must resolve to a file that exists.

This is the guard that was missing. `code-review` Step 4.5 named
`.claude/rules/review-checklist.md` from the initial commit onward while the file shipped
at `<plugin_root>/rules/`, so the reference never resolved and Step 4.5 silently took its
fallback branch for months. Nothing failed, because nothing checked.

The reference is anchored to the skill's own directory, which is the one location a skill
is always given. Both layouts share the shape `<root>/skills/<name>/` and `<root>/rules/`,
so `../../rules/<file>` resolves in the repo and in the deployed plugin alike.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "worker" / "skills"

# Matches a rules-file path in skill prose, with or without a leading `.claude/` or `../`.
REFERENCE = re.compile(r"[\w./-]*rules/[\w.-]+\.md")

# behavioral.md is PROJECT-scoped, not plugin-scoped: activate-professional-mode WRITES it
# into the target project, so it is intentionally unresolvable from the skill directory.
# Exempted by filename — a precise carve-out, not a blanket skip.
PROJECT_SCOPED = {"behavioral.md"}


def _references():
    """Yield (skill_md_path, reference_string) for every plugin-scoped rules reference."""
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        for ref in REFERENCE.findall(skill_md.read_text()):
            if Path(ref).name in PROJECT_SCOPED:
                continue
            yield skill_md, ref


def test_reference_scan_is_not_vacuous():
    """A broken REFERENCE regex would make the resolution test pass while checking nothing.

    Five plugin-scoped references exist today (4x ask-user-question-format.md, 1x
    review-checklist.md), enumerated by rg over worker/skills.
    """
    found = list(_references())
    assert len(found) >= 5, f"scanner found only {len(found)} references — regex likely broken"


def test_every_skill_rules_reference_resolves():
    unresolved = []
    for skill_md, ref in _references():
        if not (skill_md.parent / ref).resolve().is_file():
            unresolved.append(f"{skill_md.relative_to(REPO_ROOT)} -> {ref}")
    assert not unresolved, "rules references that resolve to nothing:\n" + "\n".join(unresolved)
