import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "worker/scripts/validate-codex-plugin.mjs"
NODE = shutil.which("node") or "node"
WRAPPER = "./mcp-servers/state-manager/cli/wrapper.js"
BUNDLE = "mcp-servers/state-manager/dist/index.js"


def write_manifest(root: Path, data) -> None:
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data))


def read_manifest(root: Path) -> dict:
    return json.loads((root / ".codex-plugin" / "plugin.json").read_text())


def good_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "skills" / "demo" / "SKILL.md").write_text("# demo\n")
    for relative in (WRAPPER, BUNDLE):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// stub\n")
    write_manifest(root, {
        "name": "ironclaude",
        "version": "1.2.3+codex.20261001020304",
        "skills": "./skills/",
        "mcpServers": {"state-manager": {"command": "node", "args": [WRAPPER]}},
    })
    return root


def run(*args: str):
    completed = subprocess.run(
        [NODE, str(SCRIPT), *args], text=True, capture_output=True, check=False,
    )
    return completed, json.loads(completed.stdout)


def set_field(key, value):
    def apply(root: Path) -> None:
        data = read_manifest(root)
        data[key] = value
        write_manifest(root, data)
    return apply


def set_server_script(script):
    def apply(root: Path) -> None:
        data = read_manifest(root)
        data["mcpServers"]["state-manager"]["args"] = [script]
        write_manifest(root, data)
    return apply


def remove(relative):
    def apply(root: Path) -> None:
        (root / relative).unlink()
    return apply


def test_good_plugin_is_valid(tmp_path: Path):
    completed, result = run(str(good_plugin(tmp_path)))

    assert completed.returncode == 0
    assert result == {"valid": True, "errors": []}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (set_field("name", "other"), "name must be"),
        (set_field("version", "1.2.3"), "version must be"),
        (set_field("version", "1.2.3+codex.abc"), "version must be"),
        (set_field("skills", "../outside"), "skills must be a relative path inside the plugin"),
        (set_field("skills", "/tmp"), "skills must be a relative path inside the plugin"),
        (set_field("skills", "./nope/"), "skills directory missing"),
        (remove("skills/demo/SKILL.md"), "has no */SKILL.md"),
        (set_field("mcpServers", []), "mcpServers must be an object"),
        (set_server_script("./mcp-servers/missing.js"), "args[0] missing"),
        (set_server_script("/usr/bin/env"), "must be a relative path inside the plugin"),
        (set_server_script("../escape.js"), "must be a relative path inside the plugin"),
        (remove(BUNDLE), "built bundle missing"),
    ],
)
def test_each_check_reports_its_own_error(tmp_path: Path, mutate, message):
    root = good_plugin(tmp_path)
    mutate(root)

    completed, result = run(str(root))

    assert completed.returncode == 1
    assert result["valid"] is False
    assert len(result["errors"]) == 1, result["errors"]
    assert message in result["errors"][0]


def test_reports_every_error_not_just_the_first(tmp_path: Path):
    root = good_plugin(tmp_path)
    set_field("name", "other")(root)
    remove(BUNDLE)(root)

    completed, result = run(str(root))

    assert completed.returncode == 1
    assert len(result["errors"]) == 2
    assert any("name must be" in error for error in result["errors"])
    assert any("built bundle missing" in error for error in result["errors"])


def test_malformed_manifest_is_invalid(tmp_path: Path):
    root = good_plugin(tmp_path)
    (root / ".codex-plugin" / "plugin.json").write_text("{")

    completed, result = run(str(root))

    assert completed.returncode == 1
    assert result["errors"][0].startswith("manifest unreadable")


def test_requires_exactly_one_argument():
    completed, result = run()

    assert completed.returncode == 1
    assert result["valid"] is False
