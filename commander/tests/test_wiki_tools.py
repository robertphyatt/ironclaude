import subprocess
from pathlib import Path

import pytest

from ironclaude.wiki_tools import WikiTools


def _init_brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "wiki").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=brain, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=brain, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=brain, check=True)
    return brain


def test_write_creates_page_index_log_and_commit(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    result = w.wiki_write("worker-lifecycle", "Worker Lifecycle",
                          "Workers are spawned by the orchestrator and report status back. " * 2)
    assert "worker-lifecycle.md" in result
    assert (brain / "wiki" / "worker-lifecycle.md").exists()
    index = (brain / "wiki" / "index.md").read_text()
    assert "worker-lifecycle.md" in index
    assert (brain / "wiki" / "log.md").exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=brain, capture_output=True, text=True).stdout
    assert "wiki: created worker-lifecycle" in log


def test_delete_removes_page_rebuilds_index_idempotent(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("doomed", "Doomed Page",
                 "This page exists only to be deleted in the test suite. " * 2)
    r1 = w.wiki_delete("doomed")
    assert "Deleted doomed.md" in r1
    assert not (brain / "wiki" / "doomed.md").exists()
    assert "doomed.md" not in (brain / "wiki" / "index.md").read_text()
    r2 = w.wiki_delete("doomed")
    assert "not found" in r2


def test_query_matches_by_keyword(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("deployment-patterns", "Deployment Patterns",
                 "Deployment uses blue-green rollout with health checks before cutover. " * 2)
    out = w.wiki_query("deployment rollout")
    assert "deployment-patterns" in out


def test_log_appends_entry(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_log("manual cleanup performed")
    assert "manual cleanup performed" in (brain / "wiki" / "log.md").read_text()


def test_write_rejects_short_content(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    assert "at least 50 characters" in w.wiki_write("x", "Real Title", "too short")


def test_write_rejects_date_stamped_name(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    out = w.wiki_write("2026-06-thing", "Real Title",
                       "Valid content that is definitely long enough to pass the length guard. " * 2)
    assert "date-stamped names are not allowed" in out


def test_delete_rejects_path_traversal(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    assert "Path traversal rejected" in w.wiki_delete("../../etc/passwd")


def test_write_with_description_stores_in_frontmatter(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("worker-state", "Worker State",
                 "Workers cycle through states: idle, claimed, executing, review. " * 2,
                 description="Worker state machine and transition rules.")
    content = (brain / "wiki" / "worker-state.md").read_text()
    assert "description: Worker state machine and transition rules." in content


def test_write_without_description_omits_field(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("worker-state", "Worker State",
                 "Workers cycle through states: idle, claimed, executing, review. " * 2)
    content = (brain / "wiki" / "worker-state.md").read_text()
    assert "description:" not in content


def test_index_uses_description_when_present(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("worker-state", "Worker State",
                 "First line that should not appear in index. Workers cycle through states. " * 2,
                 description="Routing summary for index.")
    index = (brain / "wiki" / "index.md").read_text()
    assert "Routing summary for index." in index
    assert "First line that should not appear in index." not in index


def test_index_falls_back_to_extracted_summary_without_description(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("worker-state", "Worker State",
                 "Distinctive first sentence used as fallback. Workers cycle through states. " * 2)
    index = (brain / "wiki" / "index.md").read_text()
    assert "Distinctive first sentence used as fallback." in index


def _committed_files(brain: Path) -> str:
    return subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=brain, capture_output=True, text=True,
    ).stdout


def test_write_commit_does_not_sweep_prestaged_files(tmp_path):
    brain = _init_brain(tmp_path)
    (brain / "junk.txt").write_text("unrelated pre-staged change\n")
    subprocess.run(["git", "add", "junk.txt"], cwd=brain, check=True)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("worker-lifecycle", "Worker Lifecycle",
                 "Workers are spawned by the orchestrator and report status back. " * 2)
    committed = _committed_files(brain)
    assert "worker-lifecycle.md" in committed
    assert "junk.txt" not in committed


def test_delete_commit_does_not_sweep_prestaged_files(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("doomed", "Doomed Page",
                 "This page exists only to be deleted in the test suite. " * 2)
    (brain / "junk.txt").write_text("unrelated pre-staged change\n")
    subprocess.run(["git", "add", "junk.txt"], cwd=brain, check=True)
    w.wiki_delete("doomed")
    committed = _committed_files(brain)
    assert "junk.txt" not in committed


def test_write_commit_uses_wiki_pathspec(tmp_path):
    brain = _init_brain(tmp_path)
    captured = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        if args and args[0][:2] == ["git", "commit"]:
            captured.append(args[0])
        return real_run(*args, **kwargs)

    import ironclaude.wiki_tools as wt
    orig = wt.subprocess.run
    wt.subprocess.run = spy
    try:
        w = WikiTools(str(brain / "wiki"))
        w.wiki_write("worker-lifecycle", "Worker Lifecycle",
                     "Workers are spawned by the orchestrator and report status back. " * 2)
    finally:
        wt.subprocess.run = orig
    assert captured, "no git commit invocation captured"
    argv = captured[0]
    assert "--" in argv
    assert "wiki/" in argv


def test_write_sanitizes_title_newline(tmp_path):
    brain = _init_brain(tmp_path)
    w = WikiTools(str(brain / "wiki"))
    w.wiki_write("worker-state", "Evil\ndescription: injected",
                 "Workers cycle through states: idle, claimed, executing, review. " * 2)
    content = (brain / "wiki" / "worker-state.md").read_text()
    # Injected fragment must not appear as its own frontmatter line.
    assert "\ndescription: injected" not in content
    title_lines = [ln for ln in content.splitlines() if ln.startswith("title:")]
    assert len(title_lines) == 1
    assert "Evil description: injected" in title_lines[0]
