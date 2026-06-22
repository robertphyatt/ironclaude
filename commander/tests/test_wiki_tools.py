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
