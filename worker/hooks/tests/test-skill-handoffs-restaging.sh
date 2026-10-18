#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
python3 - "$REPO_ROOT" <<'PY'
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap

source = Path(sys.argv[1])
brainstorm = (source / "worker/skills/brainstorming/SKILL.md").read_text()
writing = (source / "worker/skills/writing-plans/SKILL.md").read_text()
executing = (source / "worker/skills/executing-plans/SKILL.md").read_text()

for name, content, next_skill in (
    ("brainstorming", brainstorm, "writing-plans"),
    ("writing-plans", writing, "executing-plans"),
    ("executing-plans", executing, "code-review"),
):
    assert re.search(r"Codex.*read.*SKILL\.md", content, re.I | re.S), f"{name}: missing Codex native handoff"
    assert re.search(r"Claude.*Skill tool", content, re.I | re.S), f"{name}: missing Claude native handoff"
    assert next_skill in content, f"{name}: missing next skill"
assert "The Skill tool IS the plan-writing process" not in brainstorm
assert "Anything other than invoking the Skill tool" not in brainstorm
print("PASS client-native handoff guidance")

match = re.search(
    r"(^   # BEGIN EXACT ALLOWED FILES RESTAGING\n.*?^   # END EXACT ALLOWED FILES RESTAGING)$",
    executing,
    re.S | re.M,
)
assert match, "missing marked executable Python restaging snippet"
snippet = textwrap.dedent(match.group(1))

def git(root, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=check
    )

def fixture(base):
    root = base / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "sub").mkdir()
    (root / "inside").mkdir()
    (root / "sub" / "relative.txt").write_text("old")
    (root / "absolute.txt").write_text("old")
    (root / "deleted.txt").write_text("old")
    (root / "alias.txt").write_text("old")
    (root / "inside" / "target.txt").write_text("old")
    (root / "literal[1].txt").write_text("old")
    (root / "literal1.txt").write_text("old")
    (root / "unrelated-staged.txt").write_text("old")
    (root / "unrelated-unstaged.txt").write_text("old")
    (root / "link").symlink_to("inside/target.txt")
    (root / "dir-link").symlink_to("inside", target_is_directory=True)
    (root / "directory-link").symlink_to("inside", target_is_directory=True)
    (root / "outside-link").symlink_to(base, target_is_directory=True)
    git(root, "add", "--", ".")
    git(root, "commit", "-qm", "base")
    return root

def run_snippet(root, entries):
    plan = root.parent / "plan.json"
    plan.write_text(json.dumps({"tasks": [{"allowed_files": entries}]}))
    return subprocess.run(
        [sys.executable, "-c", snippet, str(root), str(plan)],
        capture_output=True, text=True,
    )

with tempfile.TemporaryDirectory(prefix="skill-handoff-restaging-") as temp:
    base = Path(temp)
    root = fixture(base)
    root_alias = base / "repo-alias"
    root_alias.symlink_to(root, target_is_directory=True)
    (root / "sub" / "relative.txt").write_text("new")
    (root / "absolute.txt").write_text("new")
    (root / "deleted.txt").unlink()
    (root / "alias.txt").write_text("new")
    (root / "inside" / "target.txt").write_text("new")
    (root / "literal[1].txt").write_text("new")
    (root / "literal1.txt").write_text("unrelated")
    (root / "link").unlink()
    (root / "link").symlink_to("sub/relative.txt")
    (root / "directory-link").unlink()
    (root / "directory-link").symlink_to("sub", target_is_directory=True)
    (root / "unrelated-staged.txt").write_text("staged")
    git(root, "add", "--", "unrelated-staged.txt")
    (root / "unrelated-unstaged.txt").write_text("unstaged")
    entries = [
        "sub/relative.txt", str(root_alias / "absolute.txt"), "deleted.txt",
        str(root / "sub" / ".." / "alias.txt"),
        "outside-link/../alias.txt",
        str(root / ".." / "repo" / "inside" / "target.txt"),
        "link", "directory-link", "literal[1].txt", "missing-optional.txt",
        "dir-link/target.txt", "sub/relative.txt",
    ]
    result = run_snippet(root_alias, entries)
    assert result.returncode == 0, result.stderr
    staged = git(root, "diff", "--cached", "--name-only", "-z").stdout.split(b"\0")
    expected = {
        b"sub/relative.txt", b"absolute.txt", b"deleted.txt", b"alias.txt",
        b"inside/target.txt", b"link", b"directory-link", b"literal[1].txt",
        b"unrelated-staged.txt",
    }
    assert set(staged) - {b""} == expected, staged
    assert git(root, "ls-files", "-s", "--", "link").stdout.startswith(b"120000 ")
    assert git(root, "ls-files", "-s", "--", "directory-link").stdout.startswith(b"120000 ")
    assert git(root, "diff", "--name-only", "--", "literal1.txt", "unrelated-unstaged.txt").stdout.splitlines() == [
        b"literal1.txt", b"unrelated-unstaged.txt"
    ]
    print("PASS exact relative, absolute, root and internal aliases, deletion, final file and directory symlinks, optional missing, literal glob, index preservation")

    (root / "loop-a").symlink_to("loop-b")
    (root / "loop-b").symlink_to("loop-a")

    for label, entry in (
        ("outside absolute", str(base / "outside.txt")),
        ("outside relative", "../outside.txt"),
        ("outside directory alias", str(root / ".." / "outside.txt")),
        ("directory entry", "sub"),
        ("symlink directory escape", "outside-link/escape.txt"),
        ("symlink loop", "loop-a/file.txt"),
    ):
        (base / "outside.txt").write_text("outside")
        (base / "escape.txt").write_text("outside")
        before = git(root, "diff", "--cached", "--raw", "-z").stdout
        result = run_snippet(root, [entry])
        assert result.returncode != 0, (label, result.stdout, result.stderr)
        assert git(root, "diff", "--cached", "--raw", "-z").stdout == before, label
        print(f"PASS reject {label}")

with tempfile.TemporaryDirectory(prefix="skill-handoff-restaging-ignored-") as temp:
    base = Path(temp)
    root = base / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text("ignored/\n")
    (root / "ignored").mkdir()
    (root / "ignored" / "tracked.md").write_text("old")
    (root / "ignored" / "unlisted.md").write_text("old")
    git(root, "add", "--", ".gitignore")
    git(root, "add", "-f", "--", "ignored/tracked.md", "ignored/unlisted.md")
    git(root, "commit", "-qm", "base")
    (root / "ignored" / "tracked.md").write_text("new")
    (root / "ignored" / "unlisted.md").write_text("new")
    (root / "ignored" / "never-tracked.md").write_text("secret")
    result = run_snippet(root, ["ignored/tracked.md", "ignored/never-tracked.md"])
    assert result.returncode == 0, result.stderr
    staged = git(root, "diff", "--cached", "--name-only", "-z").stdout.split(b"\0")
    assert set(staged) - {b""} == {b"ignored/tracked.md"}, staged
    assert b"ignored/never-tracked.md" not in git(root, "ls-files", "-z").stdout
    assert "ignored/never-tracked.md" in result.stderr, result.stderr
    print("PASS gitignored allowed files: tracked staged with -f, never-tracked skipped with notice, unlisted untouched")
PY
