import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "worker/scripts/codex-runtime-preflight.mjs"
NODE = shutil.which("node") or "node"
EXPECTED_KEYS = {
    "schema_version",
    "mode",
    "status",
    "invoked_launcher",
    "resolved_launcher",
    "source_companion",
    "destination_companion",
    "action",
    "reason",
}


def executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def runtime_layout(tmp_path: Path, *, spaced: bool = False):
    app = tmp_path / ("App With Spaces" if spaced else "app") / "bin"
    local = tmp_path / ("Local Bin" if spaced else "local") / "bin"
    launcher = executable(app / "codex")
    companion = executable(app / "codex-code-mode-host")
    local.mkdir(parents=True)
    invoked = local / "codex"
    invoked.symlink_to(launcher)
    return invoked, launcher, companion, local / "codex-code-mode-host"


def run_preflight(codex_path: Path | None, mode: str = "check", env=None):
    argv = [NODE, str(SCRIPT), "--mode", mode]
    if codex_path is not None:
        argv.extend(["--codex-path", str(codex_path)])
    completed = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    payload = json.loads(completed.stdout)
    assert set(payload) == EXPECTED_KEYS
    assert payload["schema_version"] == 1
    assert payload["mode"] == mode
    return completed, payload


def test_check_reports_repairable_without_mutation(tmp_path: Path):
    invoked, launcher, source, destination = runtime_layout(tmp_path)

    completed, payload = run_preflight(invoked)

    assert completed.returncode == 2
    assert payload["status"] == "repairable"
    assert payload["action"] == "none"
    assert payload["reason"] == "destination-missing"
    assert payload["invoked_launcher"] == str(invoked.absolute())
    assert payload["resolved_launcher"] == str(launcher.resolve())
    assert payload["source_companion"] == str(source.resolve())
    assert payload["destination_companion"] == str(destination.absolute())
    assert not destination.exists()


def test_repair_creates_one_equivalent_symlink_and_is_idempotent(tmp_path: Path):
    invoked, _, source, destination = runtime_layout(tmp_path)

    first, repaired = run_preflight(invoked, "repair")
    original_link = os.readlink(destination)
    second, healthy = run_preflight(invoked, "repair")

    assert first.returncode == 0
    assert repaired["status"] == "repaired"
    assert repaired["action"] == "create-symlink"
    assert repaired["reason"] == "created-equivalent-symlink"
    assert destination.is_symlink()
    assert destination.resolve() == source.resolve()
    assert second.returncode == 0
    assert healthy["status"] == "healthy"
    assert healthy["action"] == "none"
    assert os.readlink(destination) == original_link


def test_direct_launcher_with_same_companion_is_healthy(tmp_path: Path):
    launcher = executable(tmp_path / "bin/codex")
    companion = executable(tmp_path / "bin/codex-code-mode-host")

    completed, payload = run_preflight(launcher)

    assert completed.returncode == 0
    assert payload["status"] == "healthy"
    assert payload["reason"] == "source-is-destination"
    assert payload["source_companion"] == str(companion.resolve())


def test_symlinked_plugin_root_executes_cli_and_emits_one_json_object(tmp_path: Path):
    invoked, _, _, destination = runtime_layout(tmp_path / "runtime")
    plugin_alias = tmp_path / "Installed Plugin Alias"
    plugin_alias.symlink_to(REPO_ROOT / "worker", target_is_directory=True)
    aliased_script = plugin_alias / "scripts/codex-runtime-preflight.mjs"

    completed = subprocess.run(
        [NODE, str(aliased_script), "--mode", "check", "--codex-path", str(invoked)],
        text=True,
        capture_output=True,
        check=False,
    )

    lines = completed.stdout.splitlines()
    assert completed.returncode == 2
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert set(payload) == EXPECTED_KEYS
    assert payload["schema_version"] == 1
    assert payload["status"] == "repairable"
    assert payload["reason"] == "destination-missing"
    assert completed.stderr == ""
    assert not destination.exists()


def test_relative_multihop_links_and_spaces_are_fully_resolved(tmp_path: Path):
    invoked, launcher, source, destination = runtime_layout(tmp_path, spaced=True)
    middle = tmp_path / "Middle Dir/codex-middle"
    middle.parent.mkdir(parents=True)
    middle.symlink_to(os.path.relpath(launcher, middle.parent))
    invoked.unlink()
    invoked.symlink_to(os.path.relpath(middle, invoked.parent))

    completed, payload = run_preflight(invoked, "repair")

    assert completed.returncode == 0
    assert payload["resolved_launcher"] == str(launcher.resolve())
    assert destination.resolve() == source.resolve()


def test_equivalent_existing_relative_link_is_healthy(tmp_path: Path):
    invoked, _, source, destination = runtime_layout(tmp_path)
    destination.symlink_to(os.path.relpath(source, destination.parent))
    before = os.readlink(destination)

    completed, payload = run_preflight(invoked, "repair")

    assert completed.returncode == 0
    assert payload["status"] == "healthy"
    assert payload["reason"] == "destination-equivalent"
    assert os.readlink(destination) == before


def _assert_blocked_unchanged(invoked: Path, destination: Path, snapshot):
    completed, payload = run_preflight(invoked, "repair")
    assert completed.returncode == 3
    assert payload["status"] == "blocked"
    assert payload["action"] == "none"
    assert snapshot() == snapshot.before
    return payload


def snapshot_path(path: Path):
    def take():
        if path.is_symlink():
            return ("symlink", os.readlink(path))
        if path.is_dir():
            return ("directory", sorted(p.name for p in path.iterdir()))
        if path.is_socket():
            value = path.lstat()
            return ("socket", value.st_ino, value.st_mode)
        if stat.S_ISFIFO(path.lstat().st_mode):
            value = path.lstat()
            return ("fifo", value.st_ino, value.st_mode)
        if path.exists():
            return ("file", path.read_bytes(), path.stat().st_mode)
        return ("absent",)

    take.before = take()
    return take


def test_regular_file_collision_is_blocked_and_preserved(tmp_path: Path):
    invoked, _, _, destination = runtime_layout(tmp_path)
    destination.write_text("do not replace")
    payload = _assert_blocked_unchanged(invoked, destination, snapshot_path(destination))
    assert payload["reason"] == "destination-conflict"


def test_directory_collision_is_blocked_and_preserved(tmp_path: Path):
    invoked, _, _, destination = runtime_layout(tmp_path)
    destination.mkdir()
    (destination / "sentinel").write_text("keep")
    payload = _assert_blocked_unchanged(invoked, destination, snapshot_path(destination))
    assert payload["reason"] == "destination-conflict"


def test_nonregular_fifo_collision_is_blocked_and_preserved(tmp_path: Path):
    invoked, _, _, destination = runtime_layout(tmp_path)
    os.mkfifo(destination)
    payload = _assert_blocked_unchanged(invoked, destination, snapshot_path(destination))
    assert payload["reason"] == "destination-conflict"


def test_different_and_dangling_links_are_blocked_and_preserved(tmp_path: Path):
    for dangling in (False, True):
        root = tmp_path / str(dangling)
        invoked, _, _, destination = runtime_layout(root)
        target = root / "missing" if dangling else executable(root / "other-host")
        destination.symlink_to(target)
        payload = _assert_blocked_unchanged(invoked, destination, snapshot_path(destination))
        assert payload["reason"] == "destination-conflict"


def test_invalid_sources_are_blocked_without_destination_creation(tmp_path: Path):
    for kind in ("missing", "directory", "non-executable"):
        root = tmp_path / kind
        app = root / "app/bin"
        local = root / "local/bin"
        launcher = executable(app / "codex")
        source = app / "codex-code-mode-host"
        if kind == "directory":
            source.mkdir()
        elif kind == "non-executable":
            source.write_text("host")
        local.mkdir(parents=True)
        invoked = local / "codex"
        invoked.symlink_to(launcher)
        destination = local / "codex-code-mode-host"

        completed, payload = run_preflight(invoked, "repair")

        assert completed.returncode == 3
        assert payload["status"] == "blocked"
        assert payload["reason"] == f"source-{kind}"
        assert not os.path.lexists(destination)


def test_non_executable_wrapper_is_blocked_without_mutation(tmp_path: Path):
    wrapper = tmp_path / "bin/codex"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\nexec /real/codex\n")

    completed, payload = run_preflight(wrapper, "repair")

    assert completed.returncode == 3
    assert payload["status"] == "blocked"
    assert payload["reason"] == "launcher-not-executable"
    assert not (wrapper.parent / "codex-code-mode-host").exists()


def test_concurrent_repair_eexist_race_never_overwrites(tmp_path: Path):
    invoked, _, source, destination = runtime_layout(tmp_path)
    argv = [NODE, str(SCRIPT), "--mode", "repair", "--codex-path", str(invoked)]
    children = [subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
    results = [child.communicate() + (child.returncode,) for child in children]

    payloads = [json.loads(stdout) for stdout, _stderr, _code in results]
    assert all(code == 0 for _stdout, _stderr, code in results)
    assert [payload["status"] for payload in payloads].count("repaired") == 1
    assert [payload["status"] for payload in payloads].count("healthy") == 7
    assert [payload["action"] for payload in payloads].count("create-symlink") == 1
    assert destination.is_symlink()
    assert destination.resolve() == source.resolve()


def test_path_discovery_uses_first_executable_codex(tmp_path: Path):
    invoked, launcher, _, _ = runtime_layout(tmp_path)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(invoked.parent), env.get("PATH", "")])

    completed, payload = run_preflight(None, env=env)

    assert completed.returncode == 2
    assert payload["invoked_launcher"] == str(invoked.absolute())
    assert payload["resolved_launcher"] == str(launcher.resolve())


def test_rejects_relative_explicit_launcher_with_stable_json(tmp_path: Path):
    completed = subprocess.run(
        [NODE, str(SCRIPT), "--mode", "check", "--codex-path", "relative/codex"],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert set(payload) == EXPECTED_KEYS
    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid-codex-path"


def test_rejects_invalid_mode_without_emitting_an_out_of_schema_mode():
    completed = subprocess.run(
        [NODE, str(SCRIPT), "--mode", "destroy"],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert payload["mode"] == "check"
    assert payload["reason"] == "invalid-arguments"


def test_unexpected_filesystem_error_still_emits_one_sanitized_json_result():
    program = f"""
      import {{ main }} from {json.dumps(SCRIPT.as_uri())};
      process.exitCode = await main(
        ['--mode', 'check', '--codex-path', '/absolute/codex'],
        {{ inspectRuntime: async () => {{
          const error = new Error('sensitive filesystem detail');
          error.code = 'EACCES';
          throw error;
        }} }},
      );
    """
    completed = subprocess.run(
        [NODE, "--input-type=module", "--eval", program],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert set(payload) == EXPECTED_KEYS
    assert payload["status"] == "blocked"
    assert payload["reason"] == "unexpected-filesystem-error"
    assert "sensitive filesystem detail" not in completed.stdout
    assert "sensitive filesystem detail" not in completed.stderr
