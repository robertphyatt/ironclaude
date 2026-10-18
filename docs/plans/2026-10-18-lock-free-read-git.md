# Lock-Free Read-Only Git Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Every read-only git command IronClaude launches stops writing the index (and so stops taking `.git/index.lock`), in the hooks, workspace-manager and the Commander, after a scratch-repository investigation confirms the cause and decides whether `git diff` also needs `diff.autoRefreshIndex=false`.

**Requirements:** `docs/plans/2026-10-18-lock-free-read-git-design.md` (R1-R5). It is both the requirements file and the design file.

**Architecture:**
- Hooks: `git --no-optional-locks status …` at `worker/hooks/execution-drift-check.sh:152` and `worker/hooks/subagent-drift-detector.sh:41`.
- workspace-manager: `runGit` and `runGitEnv` (`worker/mcp-servers/workspace-manager/src/git.ts:66-86`) and the separate `spawnSync` in `cumulativeBinaryEffect` (`integration.ts:321`) pass `GIT_OPTIONAL_LOCKS=0` in the child environment. Required locks for writes are unaffected.
- Commander: `orchestrator_mcp.py:1934-1937` (`git diff --stat HEAD` in `push_repo`) and `shadow_grader.py:213-216` (`git … diff` in the `git_diff` tool) pass `env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}`; both modules already import `os` and `subprocess`.
- Conditional diff flag: Task 1 writes a verdict line `diff_flag=APPLY` or `diff_flag=SKIP …` into the findings. Steps marked **[APPLY only]** run only when the verdict is exactly `diff_flag=APPLY`; with `SKIP` they are skipped and recorded as skipped.

**Tech Stack:** bash (hooks), TypeScript + vitest (workspace-manager), Python + pytest (Commander).

**Execution invariants:**
- Shell state does not persist between steps; every step uses absolute paths. Repo root `/Users/roberthyatt/Code/ironclaude`; git runs as `git -C /Users/roberthyatt/Code/ironclaude`. zsh `nomatch`: quote globs. Foreground `sleep` is blocked in the Bash tool (it is allowed inside the probe script file).
- Evidence logs go under `/private/tmp/claude-502/taf4/` (each run its own log). No `2>/dev/null` on evidence commands.
- Never write, lock, stash or delete anything in `/Users/roberthyatt/Code/roleplaying-agents`; the only command run there is the lock-free timing read in Task 1.
- `docs/` is gitignored: stage `docs/plans` files with `git add -f`. Never commit, tag or push; the operator does.
- RED expectations are derived by tracing current code; a differing RED run is a STOP. Any GREEN failure is a STOP.
- Commander tests: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`; full suite with Bash `timeout: 600000`.

---

## Task 1: Scratch-repository investigation (R1)

No tests required: evidence gathering only; the deliverable is the findings file.

**Files:** Create `docs/plans/2026-10-18-lock-free-read-git-findings.md`.

**Step 1: Write the probe script**

```bash
mkdir -p /private/tmp/claude-502/taf4 && cat > /private/tmp/claude-502/taf4/probe.sh <<'SH'
#!/bin/bash
# Lock-free read-only git probe (design 2026-10-18 lock-free-read-git R1). Scratch repo only.
set -u
R=/private/tmp/claude-502/taf4/scratch
rm -rf "$R"; mkdir -p "$R"
git -C "$R" init -q
python3 - "$R" <<'PY'
import os, sys
r = sys.argv[1]
for d in range(200):
    p = os.path.join(r, f"d{d}"); os.makedirs(p)
    for f in range(300):
        with open(os.path.join(p, f"f{f}.txt"), "w") as fh:
            fh.write(f"{d}-{f}\n")
PY
git -C "$R" add -A
git -C "$R" -c user.email=t@example.invalid -c user.name=t commit -q -m init
echo "files=$(git -C "$R" ls-files | wc -l | tr -d ' ')"
touch_all() { find "$R" -path "$R/.git" -prune -o -type f -exec touch {} +; }
idx() { python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "$R/.git/index"; }
same() { if [ "$1" = "$2" ]; then echo no; else echo yes; fi; }

# (b) status
touch_all; sleep 1.1; b0=$(idx)
git -C "$R" --no-optional-locks status --porcelain >/dev/null; b1=$(idx)
echo "flag_status_rewrote_index=$(same "$b0" "$b1")"
GIT_OPTIONAL_LOCKS=0 git -C "$R" status --porcelain >/dev/null; b2=$(idx)
echo "env_status_rewrote_index=$(same "$b1" "$b2")"
git -C "$R" status --porcelain >/dev/null; b3=$(idx)
echo "plain_status_rewrote_index=$(same "$b2" "$b3")"

# (c) diff --name-only on stat-only-touched files (content unchanged)
touch_all; sleep 1.1; c0=$(idx)
n1=$(GIT_OPTIONAL_LOCKS=0 git -C "$R" diff --name-only | wc -l | tr -d ' '); c1=$(idx)
echo "env_diff_rewrote_index=$(same "$c0" "$c1") env_diff_lines=$n1"
touch_all; sleep 1.1; c2=$(idx)
n2=$(GIT_OPTIONAL_LOCKS=0 git -C "$R" -c diff.autoRefreshIndex=false diff --name-only | wc -l | tr -d ' '); c3=$(idx)
echo "flag_diff_rewrote_index=$(same "$c2" "$c3") flag_diff_lines=$n2"

# (a) a git status killed with SIGKILL mid-refresh
for delay in 0.02 0.05 0.1 0.2 0.4; do
  rm -f "$R/.git/index.lock"; touch_all; sleep 1.1
  git -C "$R" status --porcelain >/dev/null 2>&1 &
  pid=$!; sleep "$delay"; kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  if [ -e "$R/.git/index.lock" ]; then
    echo "kill_delay=$delay lock=present size=$(wc -c < "$R/.git/index.lock" | tr -d ' ')"
  else
    echo "kill_delay=$delay lock=absent"
  fi
done
rm -f "$R/.git/index.lock"
SH
chmod +x /private/tmp/claude-502/taf4/probe.sh; echo written
```

Expected: `written`.

**Step 2: Run the probe**

```bash
bash /private/tmp/claude-502/taf4/probe.sh > /private/tmp/claude-502/taf4/t1-probe.log 2>&1; echo "probe_rc=$?"; cat /private/tmp/claude-502/taf4/t1-probe.log
```

Expected (extra lines such as bash `Killed` notices are tolerated): `probe_rc=0`; `files=60000`; one line each for `flag_status_rewrote_index`, `env_status_rewrote_index`, `plain_status_rewrote_index`, `env_diff_…`, `flag_diff_…`, and five `kill_delay=` lines. Record the values as observed. The fix rests on `plain_status_rewrote_index=yes` with `flag_status_rewrote_index=no` and `env_status_rewrote_index=no`; if either lock-free status line says `yes`, STOP and report. At least one `kill_delay` line should show `lock=present size=0`; if none does, record it (the kill window was missed) — that alone is not a STOP.

**Step 3: Time the lock-free status in roleplaying-agents (read-only)**

```bash
/usr/bin/time -p git -C /Users/roberthyatt/Code/roleplaying-agents --no-optional-locks status --porcelain=v1 -z --untracked-files=all > /private/tmp/claude-502/taf4/t1-rpa-status.out 2> /private/tmp/claude-502/taf4/t1-rpa-time.log; echo "rpa_rc=$?"; cat /private/tmp/claude-502/taf4/t1-rpa-time.log; wc -c /private/tmp/claude-502/taf4/t1-rpa-status.out
```

Expected: `rpa_rc=0` and `real`/`user`/`sys` lines; record them. This command takes no index lock.

**Step 4: Write the findings and the diff verdict**

Create `docs/plans/2026-10-18-lock-free-read-git-findings.md` with `# Lock-Free Read-Only Git Findings`, a `## Probe` section quoting `t1-probe.log` verbatim, a `## roleplaying-agents timing` section quoting Step 3's output, and a `## Diff verdict` section whose first line is exactly one of:
- `diff_flag=APPLY` — when `env_diff_rewrote_index=yes`, `flag_diff_rewrote_index=no` and `flag_diff_lines` equals `env_diff_lines` (the flag stops the write without changing which files diff lists).
- `diff_flag=SKIP (not needed)` — when `env_diff_rewrote_index=no`.
- `diff_flag=SKIP (unsafe)` — otherwise.

```bash
rg -n -e '^## Probe$' -e '^## roleplaying-agents timing$' -e '^## Diff verdict$' -e '^diff_flag=(APPLY|SKIP \(not needed\)|SKIP \(unsafe\))$' /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-18-lock-free-read-git-findings.md
```

Expected: exactly four lines: the three headings and one verdict line.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-lock-free-read-git-findings.md
```

Expected: no output.

---

## Task 2: Hooks read lock-free (R2) (depends on 1)

**Files:** Modify `worker/hooks/execution-drift-check.sh`, `worker/hooks/subagent-drift-detector.sh`. Create `worker/hooks/tests/test-lock-free-read-git.sh`.

**Step 1: RED test** — create `worker/hooks/tests/test-lock-free-read-git.sh`:

```bash
#!/bin/bash
# Read-only git in IronClaude hooks never writes the index, so a hook killed mid-run cannot
# leave a stale .git/index.lock (design 2026-10-18 lock-free-read-git R2).
set -u
HOOKS="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
ok()  { echo "PASS: $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { if grep -qF -- "$2" "$HOOKS/$1"; then ok "$1 uses: $2"; else bad "$1 missing: $2"; fi; }

has execution-drift-check.sh 'git -C "$root" --no-optional-locks status --porcelain=v1 -z --untracked-files=all'
has subagent-drift-detector.sh 'git --no-optional-locks status --porcelain'

# Behavior: --no-optional-locks leaves a stat-dirty index unwritten; plain status (control) rewrites it.
R=$(mktemp -d); trap 'rm -rf "$R"' EXIT
git -C "$R" init -q
printf 'a\n' > "$R/a"
git -C "$R" add a
git -C "$R" -c user.email=t@example.invalid -c user.name=t commit -q -m init
mtime() { python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "$R/.git/index"; }
touch -t 202001010000 "$R/a"
m0=$(mtime)
git -C "$R" --no-optional-locks status --porcelain >/dev/null
m1=$(mtime)
if [ "$m0" = "$m1" ]; then ok "--no-optional-locks status leaves the index unwritten"; else bad "--no-optional-locks status rewrote the index"; fi
git -C "$R" status --porcelain >/dev/null
m2=$(mtime)
if [ "$m1" != "$m2" ]; then ok "control: plain status rewrites the index"; else bad "control: plain status did not rewrite the index"; fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

**Step 2: Run RED**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-lock-free-read-git.sh > /private/tmp/claude-502/taf4/t2-red.log 2>&1; echo "rc=$?"; cat /private/tmp/claude-502/taf4/t2-red.log
```

Expected (derived): `rc=1`; the two `missing:` lines FAIL; both behavior lines PASS; last line `2 passed, 2 failed`.

**Step 3: GREEN code**

In `worker/hooks/execution-drift-check.sh` replace

```bash
  if ! git -C "$root" status --porcelain=v1 -z --untracked-files=all > "$status_file" 2>/dev/null; then
```

with

```bash
  # --no-optional-locks: a read never rewrites the index, so a hook killed mid-run cannot leave a
  # stale .git/index.lock (design 2026-10-18 lock-free-read-git).
  if ! git -C "$root" --no-optional-locks status --porcelain=v1 -z --untracked-files=all > "$status_file" 2>/dev/null; then
```

In `worker/hooks/subagent-drift-detector.sh` replace

```bash
    CHANGES=$(git status --porcelain 2>/dev/null || true)
```

with

```bash
    # --no-optional-locks: read without rewriting the index (design 2026-10-18 lock-free-read-git).
    CHANGES=$(git --no-optional-locks status --porcelain 2>/dev/null || true)
```

**Step 4: Run GREEN and the drift suites**

```bash
cd /Users/roberthyatt/Code/ironclaude && for t in worker/hooks/tests/test-lock-free-read-git.sh worker/hooks/tests/test-execution-drift-check.sh worker/hooks/tests/test-execution-drift-identity.sh worker/hooks/tests/test-execution-drift-roots.sh worker/hooks/tests/test-execution-drift-writers.sh; do bash "$t" > "/private/tmp/claude-502/taf4/t2-green-$(basename "$t" .sh).log" 2>&1; echo "$t rc=$?"; tail -n 1 "/private/tmp/claude-502/taf4/t2-green-$(basename "$t" .sh).log"; done
```

Expected: every `rc=0`; the new test ends `4 passed, 0 failed`.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/subagent-drift-detector.sh worker/hooks/tests/test-lock-free-read-git.sh
```

Expected: no output.

---

## Task 3: workspace-manager reads lock-free (R3) (depends on 1)

**Files:** Modify `worker/mcp-servers/workspace-manager/src/git.ts`, `worker/mcp-servers/workspace-manager/src/integration.ts`, `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`.

**Step 1: RED test** — append to `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`:

```typescript
describe('read-only git takes no optional index lock (design 2026-10-18 lock-free-read-git)', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function statDirtyRepository(): string {
    const root = mkdtempSync(join(tmpdir(), 'ironclaude-lockfree-'));
    directories.push(root);
    git(root, 'init', '-q', '--initial-branch=main');
    git(root, 'config', 'user.name', 'Lock Free Test');
    git(root, 'config', 'user.email', 'lock-free@example.invalid');
    writeFileSync(join(root, 'a.txt'), 'a\n');
    git(root, 'add', 'a.txt');
    git(root, 'commit', '-q', '-m', 'init');
    utimesSync(join(root, 'a.txt'), new Date('2020-01-01T00:00:00Z'), new Date('2020-01-01T00:00:00Z'));
    return root;
  }

  const indexMtime = (root: string): bigint => statSync(join(root, '.git', 'index'), { bigint: true }).mtimeNs;

  it('runGit status leaves a stat-dirty index unwritten', () => {
    const root = statDirtyRepository();
    const before = indexMtime(root);
    runGit(root, ['status', '--porcelain']);
    expect(indexMtime(root)).toBe(before);
  });

  it('runGitEnv status leaves a stat-dirty index unwritten', () => {
    const root = statDirtyRepository();
    const before = indexMtime(root);
    runGitEnv(root, ['status', '--porcelain'], { ...process.env });
    expect(indexMtime(root)).toBe(before);
  });

  it('control: a plain git status rewrites the same stat-dirty index', () => {
    const root = statDirtyRepository();
    const before = indexMtime(root);
    git(root, 'status', '--porcelain');
    expect(indexMtime(root)).not.toBe(before);
  });
});
```

and extend the `node:fs` import on line 3 with `statSync, utimesSync` (keep alphabetical order: insert `statSync` after `rmSync` and `utimesSync` after `symlinkSync`).

**[APPLY only] Step 1b:** also append inside the same `describe`:

```typescript
  it('runGit diff --name-only leaves a stat-dirty index unwritten and lists nothing', () => {
    const root = statDirtyRepository();
    const before = indexMtime(root);
    expect(runGit(root, ['diff', '--name-only']).trim()).toBe('');
    expect(indexMtime(root)).toBe(before);
  });
```

**Step 2: Run RED**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/git.test.ts -t 'optional index lock' > /private/tmp/claude-502/taf4/t3-red.log 2>&1; echo "rc=$?"; rg -n -e ' ✓ ' -e ' × ' -e 'Tests ' /private/tmp/claude-502/taf4/t3-red.log
```

Expected (derived): `rc=1`; the `runGit status` and `runGitEnv status` tests fail (today both rewrite the index); the control passes. With **[APPLY only]** Step 1b, the diff test fails too.

**Step 3: GREEN code**

In `git.ts`, replace

```typescript
  const result = spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8', maxBuffer: GIT_MAX_BUFFER });
```

with

```typescript
  const result = spawnSync('git', ['-C', cwd, ...args], {
    encoding: 'utf8',
    env: { ...process.env, ...LOCK_FREE_READ_ENV },
    maxBuffer: GIT_MAX_BUFFER,
  });
```

replace

```typescript
  const result = spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8', env, maxBuffer: GIT_MAX_BUFFER });
```

with

```typescript
  const result = spawnSync('git', ['-C', cwd, ...args], {
    encoding: 'utf8',
    env: { ...env, ...LOCK_FREE_READ_ENV },
    maxBuffer: GIT_MAX_BUFFER,
  });
```

and insert immediately before `/** Executes Git through argv only. User-provided paths and refs never enter a shell. */`:

```typescript
/**
 * Reads never rewrite the index, so a git process killed mid-read cannot leave a stale
 * .git/index.lock; required locks for writes (add, commit, reset, …) are unaffected
 * (design 2026-10-18 lock-free-read-git).
 */
export const LOCK_FREE_READ_ENV = { GIT_OPTIONAL_LOCKS: '0' } as const;

```

In `integration.ts`, replace

```typescript
    const result = spawnSync('git', ['-C', cwd, ...args], { stdio: ['ignore', wfd, 'pipe'], maxBuffer: GIT_MAX_BUFFER });
```

with

```typescript
    const result = spawnSync('git', ['-C', cwd, ...args], {
      stdio: ['ignore', wfd, 'pipe'],
      env: { ...process.env, ...LOCK_FREE_READ_ENV },
      maxBuffer: GIT_MAX_BUFFER,
    });
```

and add `LOCK_FREE_READ_ENV` to the existing named import from `./git.js` in `integration.ts`.

**[APPLY only] Step 3b:** in the three `spawnSync` argument lists edited above, replace `['-C', cwd, ...args]` with `['-c', 'diff.autoRefreshIndex=false', '-C', cwd, ...args]`.

**Step 4: Run GREEN and the package suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit > /private/tmp/claude-502/taf4/t3-tsc.log 2>&1; echo "tsc_rc=$?"; npm test > /private/tmp/claude-502/taf4/t3-green.log 2>&1; echo "npm_rc=$?"; rg -n -e 'Test Files' -e 'Tests ' -e ' FAIL ' /private/tmp/claude-502/taf4/t3-green.log
```

Expected: `tsc_rc=0`, `npm_rc=0`, no ` FAIL ` lines; record the Tests count as `<W>`.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/integration.ts worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts
```

Expected: no output.

---

## Task 4: Commander reads lock-free (R4) (depends on 1)

**Files:** Modify `commander/src/ironclaude/orchestrator_mcp.py`, `commander/src/ironclaude/shadow_grader.py`, `commander/tests/test_shadow_grader.py`, `commander/tests/test_push.py`.

**Step 1: RED tests**

In `commander/tests/test_shadow_grader.py`, insert immediately before `    def test_validate_path_resolves_tmp_symlink(self):`:

```python
    def test_git_diff_reads_without_optional_locks(self):
        """git_diff must not rewrite the index: a 10 s kill mid-refresh would leave a stale
        .git/index.lock (design 2026-10-18 lock-free-read-git)."""
        grader = ShadowGrader(config_path="/nonexistent/config.json")
        mock_result = MagicMock()
        mock_result.stdout = "diff output\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            grader._execute_tool("git_diff", {"repo_path": "/tmp"}, "/tmp")
        assert mock_run.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"

```

In `commander/tests/test_push.py`, insert immediately before `def test_push_rate_limit(tools, db_conn, git_repo):`:

```python
def test_push_diff_stat_reads_without_optional_locks(tools, mock_slack, git_repo):
    """push_repo's `git diff --stat HEAD` must not rewrite the index (design 2026-10-18
    lock-free-read-git)."""
    with patch("ironclaude.orchestrator_mcp.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123 test commit\n", stderr="")
        tools.push_repo(str(git_repo), "origin", "main")
    diff_calls = [c for c in mock_run.call_args_list if "diff" in c.args[0] and "--stat" in c.args[0]]
    assert len(diff_calls) == 1
    assert diff_calls[0].kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"


```

**Step 2: Run RED**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_shadow_grader.py tests/test_push.py -k 'diff_stat_reads_without_optional_locks or git_diff_reads_without_optional_locks' -q > /private/tmp/claude-502/taf4/t4-red.log 2>&1; echo "rc=$?"; rg -n '^FAILED' /private/tmp/claude-502/taf4/t4-red.log; tail -n 1 /private/tmp/claude-502/taf4/t4-red.log
```

Expected (derived): `rc=1`, `2 failed` — both calls pass no `env` today, so `kwargs["env"]` raises `KeyError`.

**Step 3: GREEN code**

In `orchestrator_mcp.py`, replace

```python
        diff_result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=repo, capture_output=True, text=True,
        )
```

with

```python
        # GIT_OPTIONAL_LOCKS=0: read without rewriting the index (design 2026-10-18
        # lock-free-read-git).
        diff_result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=repo, capture_output=True, text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
```

In `shadow_grader.py`, replace

```python
                result = subprocess.run(
                    ["git", "--no-ext-diff", "-c", "core.fsmonitor=", "-c", "diff.textconv=", "diff"],
                    capture_output=True, text=True, timeout=10, cwd=rp,
                )
```

with

```python
                # GIT_OPTIONAL_LOCKS=0: the 10 s kill must never interrupt an index rewrite and
                # leave a stale .git/index.lock (design 2026-10-18 lock-free-read-git).
                result = subprocess.run(
                    ["git", "--no-ext-diff", "-c", "core.fsmonitor=", "-c", "diff.textconv=", "diff"],
                    capture_output=True, text=True, timeout=10, cwd=rp,
                    env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                )
```

**[APPLY only] Step 3b:** in the two command lists above, insert `"-c", "diff.autoRefreshIndex=false",` right after `"git",`.

**Step 4: Run GREEN**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_shadow_grader.py tests/test_push.py -q > /private/tmp/claude-502/taf4/t4-green.log 2>&1; echo "rc=$?"; rg -n -e '^(FAILED|ERROR)' /private/tmp/claude-502/taf4/t4-green.log; tail -n 1 /private/tmp/claude-502/taf4/t4-green.log
```

Expected: `rc=0`, no FAILED or ERROR lines.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/shadow_grader.py commander/tests/test_shadow_grader.py commander/tests/test_push.py
```

Expected: no output.

---

## Task 5: Full suites, docs and staging (depends on 2, 3, 4)

No tests required: regression run and release notes only.

**Files:** Modify `CHANGELOG.md`, `docs/plans/2026-10-18-lock-free-read-git-findings.md`. Stage the design, this plan and its `.plan.json`.

**Step 1: Commander suite** (Bash `timeout: 600000`)

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/taf4/t5-suite.log 2>&1; echo "suite=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf4/t5-suite.log; tail -n 1 /private/tmp/claude-502/taf4/t5-suite.log
```

Expected: `suite=0`, no FAILED or ERROR lines; derived passed count `4154 + 2 = 4156`; record as `<N>` (a different count with 0 failed is recorded, not a STOP).

**Step 2: Hook suites**

```bash
cd /Users/roberthyatt/Code/ironclaude && make test-hooks > /private/tmp/claude-502/taf4/t5-hooks.log 2>&1; echo "make_rc=$?"; rg -c '^--- ' /private/tmp/claude-502/taf4/t5-hooks.log
```

Expected: `make_rc=0` and `61` (60 suites plus `test-lock-free-read-git.sh`).

**Step 3: state-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf4/t5-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf4/t5-npm.log
```

Expected: `npm=0` and `Tests  273 passed (273)`.

**Step 4: Findings release paragraph**

Append to the findings file a `## Release commit message addition` section. Its paragraph depends on the Task 1 verdict:

- `diff_flag=APPLY`:

  > IronClaude's read-only git never takes the index lock. The drift hooks run `git --no-optional-locks status`, workspace-manager and the Commander run their reads with `GIT_OPTIONAL_LOCKS=0`, and read-only diffs also skip the index refresh (`diff.autoRefreshIndex=false`), so a git process killed mid-read (a hook, a timeout, an interrupted turn) can no longer leave a stale `.git/index.lock` that blocks every session's `git add` in a shared checkout. Writes still take the lock for one short command.

- `diff_flag=SKIP (not needed)`:

  > IronClaude's read-only git never takes the index lock. The drift hooks run `git --no-optional-locks status`, and workspace-manager and the Commander run their reads with `GIT_OPTIONAL_LOCKS=0`, so a git process killed mid-read (a hook, a timeout, an interrupted turn) can no longer leave a stale `.git/index.lock` that blocks every session's `git add` in a shared checkout. Writes still take the lock for one short command.

- `diff_flag=SKIP (unsafe)`:

  > IronClaude's read-only `git status` never takes the index lock. The drift hooks, which run on nearly every shell command, use `git --no-optional-locks status`, and workspace-manager and the Commander run their git with `GIT_OPTIONAL_LOCKS=0`, so a status read killed mid-run (a hook, a timeout, an interrupted turn) can no longer leave a stale `.git/index.lock` that blocks every session's `git add` in a shared checkout. Read-only `git diff` still refreshes the index under the lock, because turning that refresh off would change which files it reports. Writes still take the lock for one short command.

```bash
rg -n -e '^## Release commit message addition$' -e 'never takes the index lock' /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-18-lock-free-read-git-findings.md
```

Expected: two lines (every variant contains `never takes the index lock`).

**Step 5: CHANGELOG**

In `CHANGELOG.md`, insert immediately after the bullet that starts `- **Both Brains refuse git file writes and branch changes` (line 57):

```markdown
- **Read-only git never takes the index lock.** `execution-drift-check.sh` and `subagent-drift-detector.sh` run `git --no-optional-locks status`, and workspace-manager (`runGit`, `runGitEnv`) and the Commander (`push_repo`'s diff stat, the shadow grader's `git_diff`) run with `GIT_OPTIONAL_LOCKS=0`. A git process killed mid-read can no longer leave a stale `.git/index.lock` that blocks `git add` for every session sharing a checkout; writes still take the lock for one short command.
```

Verdict variants of this bullet: with `diff_flag=APPLY`, append ` Read-only diffs also skip the index refresh (`diff.autoRefreshIndex=false`).` at its end; with `diff_flag=SKIP (unsafe)`, replace the bold title with `**Read-only git status never takes the index lock.**` and append ` Read-only `git diff` still refreshes the index under the lock, because turning that refresh off would change which files it reports.` at its end; with `diff_flag=SKIP (not needed)`, use it as written. Then on the `Deploy:` line replace `Suites: commander 4154,` with `Suites: commander <N>,` and `hooks 60` with `hooks 61`.

```bash
rg -n -F -e 'never takes the index lock' -e 'Suites: commander' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; rg -n -F -e 'Suites: commander 4154,' -e 'hooks 60 (' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; echo "stale_rc=$?"
```

Expected: the new bullet at line 58; the 1.2.0 `Suites: commander <N>, state-manager 273, hooks 61` line plus older releases' lines; then the second `rg` prints only older releases' `hooks 60` lines, if any, and no `Suites: commander 4154,` line.

**Step 6: Stage and check**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-18-lock-free-read-git-design.md docs/plans/2026-10-18-lock-free-read-git.md docs/plans/2026-10-18-lock-free-read-git.plan.json docs/plans/2026-10-18-lock-free-read-git-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status; git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: staged against HEAD `664d3b8`: M `CHANGELOG.md`, `commander/src/ironclaude/orchestrator_mcp.py`, `commander/src/ironclaude/shadow_grader.py`, `commander/tests/test_push.py`, `commander/tests/test_shadow_grader.py`, `worker/hooks/execution-drift-check.sh`, `worker/hooks/subagent-drift-detector.sh`, `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`, `worker/mcp-servers/workspace-manager/src/git.ts`, `worker/mcp-servers/workspace-manager/src/integration.ts` (10 M); A `worker/hooks/tests/test-lock-free-read-git.sh` and the 4 `docs/plans/2026-10-18-lock-free-read-git*` files (5 A); nothing unstaged. Any other entry: STOP and report.

After this task an Opus end review covers the staged diff; the operator then folds it into the v1.2.0 commit and redeploys.
