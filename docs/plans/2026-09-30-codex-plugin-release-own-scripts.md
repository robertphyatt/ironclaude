# Repo-Owned Codex Plugin Release Scripts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make `make codex-plugin-release` work again by replacing the two removed Codex plugin-creator scripts with repo-owned Node scripts: a cachebuster and a validator.

**Requirements:** `docs/plans/2026-09-30-codex-plugin-release-own-scripts-requirements.md` (R1–R5)

**Design:** `docs/plans/2026-09-30-codex-plugin-release-own-scripts-design.md`

**Architecture:**
- **Cachebuster:** `worker/scripts/codex-plugin-cachebuster.mjs` rewrites only the version text in `worker/.codex-plugin/plugin.json` to `<X.Y.Z>+codex.<UTC YYYYMMDDHHMMSS>`.
- **Validator:** `worker/scripts/validate-codex-plugin.mjs` checks the manifest and every file it references.
- **Release target:** the Makefile calls both scripts in place of the removed Python scripts. The step order is unchanged.

**Tech Stack:** Node ESM (matching `codex-runtime-preflight.mjs`), pytest via subprocess.

## Execution invariants (every step)

- **Commands:**
  - Shell state does not persist between steps, so use absolute paths.
  - Run pytest as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`.
  - Stage with `git -C /Users/roberthyatt/Code/ironclaude add …`.
- **Plan docs:** `docs/` is gitignored, so plan artifacts need `git add -f`.
- **Evidence commands:** no `2>/dev/null`.
- **Real manifest is off limits:** never run the cachebuster against the real `worker/` directory in this plan, because it would rewrite the committed `worker/.codex-plugin/plugin.json`. Tests use `tmp_path` copies only.
- **Not in this plan:** version bump, commit, tag, push or deploy.

---

## Task 1: Cachebuster script (R1)

**Files:**
- Create: `worker/scripts/codex-plugin-cachebuster.mjs`
- Test: `commander/tests/test_codex_plugin_cachebuster.py`

**Step 1: Write the tests (RED).** Create `commander/tests/test_codex_plugin_cachebuster.py`:

```python
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
```

Broken states these catch:
- **Unchanged-bytes checks:** fail if the script re-serializes the JSON, because `—` would become `—`.
- **Default-stamp window:** fails if the stamp uses local time instead of UTC (this machine is UTC−6), or has the wrong width.
- **Reject cases:** fail if validation is loosened or if the script writes before it validates.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_codex_plugin_cachebuster.py
```

Expected: FAIL. The script doesn't exist, so node exits 1 and no JSON is printed.
- The success tests fail on the return code.
- The reject tests may fail on `json.loads(stderr)`.

**Step 3: Implement.** Create `worker/scripts/codex-plugin-cachebuster.mjs`:

```js
#!/usr/bin/env node
// Stamps <pluginDir>/.codex-plugin/plugin.json with a fresh +codex.<UTC YYYYMMDDHHMMSS>
// cachebuster so Codex installs every release build as a new plugin version. Only the
// version text changes; the rest of the file is left byte-for-byte as it was.

import { readFile, realpath, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const VERSION_RE = /^(\d+\.\d+\.\d+)(\+codex\.\d{14})?$/;
const STAMP_RE = /^\d{14}$/;
const USAGE = 'usage: codex-plugin-cachebuster.mjs <pluginDir> [--stamp YYYYMMDDHHMMSS]';


export function utcStamp(date = new Date()) {
  return date.toISOString().replace(/[-:T]/g, '').slice(0, 14);
}


export async function stampPlugin(pluginDir, stamp = utcStamp()) {
  if (!STAMP_RE.test(stamp)) throw new Error(`invalid stamp: ${stamp}`);
  const manifestPath = path.join(pluginDir, '.codex-plugin', 'plugin.json');
  const text = await readFile(manifestPath, 'utf8');
  const previous = JSON.parse(text).version;
  const match = typeof previous === 'string' ? VERSION_RE.exec(previous) : null;
  if (!match) throw new Error(`invalid version: ${JSON.stringify(previous)}`);
  const needle = `"version": ${JSON.stringify(previous)}`;
  const first = text.indexOf(needle);
  if (first === -1 || text.indexOf(needle, first + 1) !== -1) {
    throw new Error(`expected exactly one ${needle} in ${manifestPath}`);
  }
  const version = `${match[1]}+codex.${stamp}`;
  await writeFile(manifestPath, text.replace(needle, `"version": ${JSON.stringify(version)}`));
  return { previous, version };
}


function parseArgs(argv) {
  const [pluginDir, ...rest] = argv;
  if (!pluginDir || pluginDir.startsWith('--')) return null;
  if (rest.length === 0) return { pluginDir };
  if (rest.length === 2 && rest[0] === '--stamp') return { pluginDir, stamp: rest[1] };
  return null;
}


export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  if (!args) {
    process.stderr.write(`${JSON.stringify({ error: USAGE })}\n`);
    return 1;
  }
  try {
    const result = await stampPlugin(args.pluginDir, args.stamp);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return 0;
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ error: error.message })}\n`);
    return 1;
  }
}


async function isDirectEntry(argvEntry = process.argv[1]) {
  if (!argvEntry) return false;
  try {
    const physicalEntry = await realpath(path.resolve(argvEntry));
    return pathToFileURL(physicalEntry).href === import.meta.url;
  } catch {
    return false;
  }
}


if (await isDirectEntry()) {
  process.exitCode = await main();
}
```

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_codex_plugin_cachebuster.py
```

Expected: 12 passed, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/scripts/codex-plugin-cachebuster.mjs commander/tests/test_codex_plugin_cachebuster.py
```

---

## Task 2: Validator script (R2)

**Files:**
- Create: `worker/scripts/validate-codex-plugin.mjs`
- Test: `commander/tests/test_validate_codex_plugin.py`

**Step 1: Write the tests (RED).** Create `commander/tests/test_validate_codex_plugin.py`:

```python
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
```

Broken states these catch:
- **Escape and absolute cases:** each points at something that exists (`/tmp`, `/usr/bin/env`) or would produce a different message. Deleting the containment check therefore either passes silently or reports the wrong error, and the exact-message, single-error assertions fail.
- **Multi-error test:** fails if the validator stops at the first error.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_validate_codex_plugin.py
```

Expected: FAIL. The script doesn't exist, so stdout is empty and `json.loads` raises.

**Step 3: Implement.** Create `worker/scripts/validate-codex-plugin.mjs`:

```js
#!/usr/bin/env node
// Validates <pluginDir>/.codex-plugin/plugin.json and every file it references before
// `codex plugin add` installs the plugin. Reports every failed check, not just the first.

import { readFile, readdir, realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STAMPED_VERSION_RE = /^\d+\.\d+\.\d+\+codex\.\d{14}$/;
const STATE_MANAGER_BUNDLE = 'mcp-servers/state-manager/dist/index.js';


function resolveInside(root, relative) {
  if (typeof relative !== 'string' || relative.length === 0 || path.isAbsolute(relative)) return null;
  const resolved = path.resolve(root, relative);
  const fromRoot = path.relative(root, resolved);
  if (fromRoot === '..' || fromRoot.startsWith(`..${path.sep}`) || path.isAbsolute(fromRoot)) return null;
  return resolved;
}


async function kind(target) {
  try {
    const value = await stat(target);
    if (value.isFile()) return 'file';
    return value.isDirectory() ? 'directory' : 'other';
  } catch {
    return 'missing';
  }
}


async function countSkills(skillsDir) {
  let found = 0;
  for (const entry of await readdir(skillsDir, { withFileTypes: true })) {
    if (entry.isDirectory() && (await kind(path.join(skillsDir, entry.name, 'SKILL.md'))) === 'file') {
      found += 1;
    }
  }
  return found;
}


export async function validatePlugin(pluginDir) {
  const root = path.resolve(pluginDir);
  let manifest;
  try {
    manifest = JSON.parse(await readFile(path.join(root, '.codex-plugin', 'plugin.json'), 'utf8'));
  } catch (error) {
    return { valid: false, errors: [`manifest unreadable: ${error.message}`] };
  }
  const errors = [];

  if (manifest?.name !== 'ironclaude') {
    errors.push(`name must be "ironclaude", got ${JSON.stringify(manifest?.name)}`);
  }
  if (typeof manifest?.version !== 'string' || !STAMPED_VERSION_RE.test(manifest.version)) {
    errors.push(`version must be X.Y.Z+codex.<14 digits>, got ${JSON.stringify(manifest?.version)}`);
  }

  const skillsDir = resolveInside(root, manifest?.skills);
  if (!skillsDir) {
    errors.push(`skills must be a relative path inside the plugin, got ${JSON.stringify(manifest?.skills)}`);
  } else if ((await kind(skillsDir)) !== 'directory') {
    errors.push(`skills directory missing: ${manifest.skills}`);
  } else if ((await countSkills(skillsDir)) === 0) {
    errors.push(`skills directory has no */SKILL.md: ${manifest.skills}`);
  }

  const servers = manifest?.mcpServers;
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
    errors.push('mcpServers must be an object');
  } else {
    for (const [name, server] of Object.entries(servers)) {
      const script = Array.isArray(server?.args) ? server.args[0] : undefined;
      const resolved = resolveInside(root, script);
      if (!resolved) {
        errors.push(`mcpServers.${name}.args[0] must be a relative path inside the plugin, got ${JSON.stringify(script)}`);
      } else if ((await kind(resolved)) !== 'file') {
        errors.push(`mcpServers.${name}.args[0] missing: ${script}`);
      }
    }
  }

  if ((await kind(path.join(root, STATE_MANAGER_BUNDLE))) !== 'file') {
    errors.push(`built bundle missing: ${STATE_MANAGER_BUNDLE}`);
  }
  return { valid: errors.length === 0, errors };
}


export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 1) {
    process.stdout.write(`${JSON.stringify({
      valid: false,
      errors: ['usage: validate-codex-plugin.mjs <pluginDir>'],
    })}\n`);
    return 1;
  }
  const result = await validatePlugin(argv[0]);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  return result.valid ? 0 : 1;
}


async function isDirectEntry(argvEntry = process.argv[1]) {
  if (!argvEntry) return false;
  try {
    const physicalEntry = await realpath(path.resolve(argvEntry));
    return pathToFileURL(physicalEntry).href === import.meta.url;
  } catch {
    return false;
  }
}


if (await isDirectEntry()) {
  process.exitCode = await main();
}
```

**Step 4: Run the tests and confirm they pass, then validate the real plugin (read-only).**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_validate_codex_plugin.py
```

Expected: 16 passed, 0 failed.

```bash
node /Users/roberthyatt/Code/ironclaude/worker/scripts/validate-codex-plugin.mjs /Users/roberthyatt/Code/ironclaude/worker
```

Expected: `{"valid":true,"errors":[]}`, exit 0. The real manifest is `1.1.15+codex.20260930172837`, its skills directory has `*/SKILL.md`, all three MCP wrapper scripts exist, and `dist/index.js` was built during the v1.1.15 deploy.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/scripts/validate-codex-plugin.mjs commander/tests/test_validate_codex_plugin.py
```

---

## Task 3: Makefile, skill wording and ordering test (R3, R4, R5)

**Depends on:** Tasks 1 and 2.

**Files:**
- Modify: `Makefile:25` and `Makefile:27`
- Modify: `worker/skills/executing-plans/SKILL.md:668`
- Test: `commander/tests/test_executing_plans_skill.py:228-230` and `:257-290`

**Step 1: Update the tests (RED).** In `commander/tests/test_executing_plans_skill.py`:

(a) In `test_self_update_boundary_requires_same_task_runtime_and_behavioral_proof`, replace the concept `"cachebuster before the final build",` with `"ironclaude cachebuster before the final build",`. The section text is lower-cased by `_normalized`.

(b) In `test_codex_install_and_release_use_runtime_preflight_before_plugin_install`:
- replace `"update_plugin_cachebuster.py worker",` with `"worker/scripts/codex-plugin-cachebuster.mjs worker",`;
- replace `"validate_plugin.py worker",` with `"worker/scripts/validate-codex-plugin.mjs worker",`;
- directly after the line `assert "worker/scripts/codex-runtime-preflight.mjs --mode repair" in makefile`, add `assert "plugin-creator" not in makefile`.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_executing_plans_skill.py
```

Expected: FAIL. The ordering test fails because the new script names are absent from `make -n`, and the self-update test fails because the skill still says "plugin-creator cachebuster".

**Step 2: Update the Makefile and the skill (GREEN).**
- In `Makefile`, replace `	python3 $(HOME)/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py worker` with `	node worker/scripts/codex-plugin-cachebuster.mjs worker`.
- In `Makefile`, replace `	python3 $(HOME)/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py worker` with `	node worker/scripts/validate-codex-plugin.mjs worker`.
- Keep the leading tab in both lines.
- In `worker/skills/executing-plans/SKILL.md`, replace `The target then applies the plugin-creator cachebuster before the` with `The target then applies the IronClaude cachebuster before the`.

**Step 3: Verify.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_executing_plans_skill.py tests/test_codex_plugin_cachebuster.py tests/test_validate_codex_plugin.py tests/test_version_consistency.py
```

Expected: all pass, 0 failed.

```bash
make -n -C /Users/roberthyatt/Code/ironclaude codex-plugin-release
```

Expected, in order:
1. `node worker/scripts/codex-runtime-preflight.mjs --mode repair`
2. `node worker/scripts/codex-plugin-cachebuster.mjs worker`
3. the `tsc` and `npm run bundle` line
4. `node worker/scripts/validate-codex-plugin.mjs worker`
5. `codex plugin add ironclaude@ironclaude --json`

No `plugin-creator` appears.

```bash
rg -n -F "plugin-creator" /Users/roberthyatt/Code/ironclaude/Makefile /Users/roberthyatt/Code/ironclaude/worker/skills /Users/roberthyatt/Code/ironclaude/README.md /Users/roberthyatt/Code/ironclaude/CODEX_SETUP.md
```

Expected: no output, exit 1.

**Step 4: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add Makefile worker/skills/executing-plans/SKILL.md commander/tests/test_executing_plans_skill.py
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-09-30-codex-plugin-release-own-scripts-requirements.md docs/plans/2026-09-30-codex-plugin-release-own-scripts-design.md docs/plans/2026-09-30-codex-plugin-release-own-scripts.md docs/plans/2026-09-30-codex-plugin-release-own-scripts.plan.json
```

Expected: exit 0.
