import subprocess
from pathlib import Path

from ironclaude.wiki_cli import main


def _init_brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "wiki").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=brain, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=brain, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=brain, check=True)
    return brain


def test_cli_delete_routes_to_wikitools(tmp_path, capsys):
    brain = _init_brain(tmp_path)
    rc = main(["--brain", str(brain), "write", "scratch", "Scratch Page",
               "Seed content long enough to satisfy the minimum-length guard. " * 2])
    assert rc == 0
    assert (brain / "wiki" / "scratch.md").exists()
    capsys.readouterr()
    rc = main(["--brain", str(brain), "delete", "scratch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Deleted scratch.md" in out
    assert not (brain / "wiki" / "scratch.md").exists()
