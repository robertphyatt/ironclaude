import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "worker/scripts/codex-plugin-cachebuster.mjs"
NODE = shutil.which("node") or "node"
MANIFEST_TEMPLATE = (
    '{\n  "name": "ironclaude",\n  "version": "VERSION",\n'
    '  "description": "Ironclad \\u2014 discipline",\n  "skills": "./skills/"\n}\n'
)


def plugin(tmp_path: Path, version: str) -> Path:
    manifest = tmp_path / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST_TEMPLATE.replace("VERSION", version))
    return manifest


def run(*args: str):
    return subprocess.run(
        [NODE, str(SCRIPT), *args], text=True, capture_output=True, check=False,
    )


def test_adds_stamp_to_plain_release(tmp_path: Path):
    manifest = plugin(tmp_path, "1.2.3")

    completed = run(str(tmp_path), "--stamp", "20261001020304")

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "previous": "1.2.3",
        "version": "1.2.3+codex.20261001020304",
    }
    assert manifest.read_text() == MANIFEST_TEMPLATE.replace("VERSION", "1.2.3+codex.20261001020304")


def test_replaces_existing_stamp_and_keeps_every_other_byte(tmp_path: Path):
    manifest = plugin(tmp_path, "1.1.15+codex.20260930172837")

    completed = run(str(tmp_path), "--stamp", "20261001020304")

    assert completed.returncode == 0, completed.stderr
    text = manifest.read_text()
    assert text == MANIFEST_TEMPLATE.replace("VERSION", "1.1.15+codex.20261001020304")
    assert "\\u2014" in text


def test_default_stamp_is_current_utc_time(tmp_path: Path):
    manifest = plugin(tmp_path, "1.2.3")
    before = datetime.now(timezone.utc).replace(microsecond=0)

    completed = run(str(tmp_path))

    after = datetime.now(timezone.utc)
    assert completed.returncode == 0, completed.stderr
    match = re.fullmatch(r"1\.2\.3\+codex\.(\d{14})", json.loads(manifest.read_text())["version"])
    assert match
    stamped = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    assert before <= stamped <= after


@pytest.mark.parametrize(
    ("version", "args"),
    [
        ("1.2", ["--stamp", "20261001020304"]),
        ("1.2.3+codex.abc", ["--stamp", "20261001020304"]),
        ("1.2.3-beta", ["--stamp", "20261001020304"]),
        ("1.2.3", ["--stamp", "2026100102030"]),
        ("1.2.3", ["--stamp", "2026-10-01"]),
        ("1.2.3", ["--bogus"]),
    ],
)
def test_rejects_bad_input_without_writing(tmp_path: Path, version, args):
    manifest = plugin(tmp_path, version)
    before = manifest.read_bytes()

    completed = run(str(tmp_path), *args)

    assert completed.returncode == 1
    assert "error" in json.loads(completed.stderr)
    assert manifest.read_bytes() == before


def test_rejects_missing_manifest(tmp_path: Path):
    completed = run(str(tmp_path), "--stamp", "20261001020304")

    assert completed.returncode == 1
    assert "error" in json.loads(completed.stderr)


def test_rejects_malformed_json_without_writing(tmp_path: Path):
    manifest = tmp_path / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"version": "1.2.3",')

    completed = run(str(tmp_path), "--stamp", "20261001020304")

    assert completed.returncode == 1
    assert manifest.read_text() == '{"version": "1.2.3",'


def test_rejects_version_field_not_written_as_expected(tmp_path: Path):
    manifest = tmp_path / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"version":"1.2.3"}\n')

    completed = run(str(tmp_path), "--stamp", "20261001020304")

    assert completed.returncode == 1
    assert manifest.read_text() == '{"version":"1.2.3"}\n'
