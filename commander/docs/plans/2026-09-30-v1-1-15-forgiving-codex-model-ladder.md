# v1.1.15 Forgiving Codex Model Ladder and Package Layout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make IronClaude accept any Codex `gpt-<version>-<luna|terra|sol|astra>` model, pick broker reviewers from Codex's installed model list, and launch Codex through the `codex-cli` package entrypoint. Ship it as v1.1.15.

**Requirements:** `docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder-requirements.md` (R1–R9)

**Design:** `docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder-design.md`

**Architecture:**
- The advisor broker ranks Codex models by tier word and resolves the one-up reviewer to the newest installed model from `$CODEX_HOME/models_cache.json`, with a built-in fallback.
- Commander's provider-config check accepts any version with the matching tier word. Its defaults are unchanged.
- The preflight script and the broker both normalize a launcher that sits inside a `codex-cli` package (`codex-package.json`, layoutVersion 1) to the package's declared `entrypoint`, whose companion sits beside it.

**Tech Stack:** TypeScript and vitest (state-manager), Node ESM (preflight), Python 3.11 and pytest (commander).

## Execution invariants (every step)

- **Shell state does not persist between steps.** Use literal absolute paths.
- **Commands:**
  - pytest: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`
  - vitest: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run …`
  - typecheck: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit`
  - staging: `git -C /Users/roberthyatt/Code/ironclaude add …`
- **`docs/` is gitignored:** plan artifacts need `git add -f`. `worker/mcp-servers/*/dist/` is gitignored and is rebuilt at deploy, not committed.
- **No `2>/dev/null` on evidence commands.** `rg` exits 1 on no match.
- **No commit, tag, push or deploy.**

---

## Task 1: Broker tier-word ladder, installed-model reviewer, and schema pattern (R1–R4, R3a)

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/tools/advisor-review.ts`
- Test: `worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts`

**Step 1: Update the test harness and add tests (RED).**

(a) In `advisor-review.test.ts`, add `parseCodexModel` to the import list from `./advisor-review.js`.

(b) Above `function harness(`, add:

```ts
const LEGACY_CACHE = JSON.stringify({
  models: ['gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol', 'gpt-6-astra'].map((slug) => ({ slug })),
});
const GPT6_CACHE = JSON.stringify({
  models: [
    'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-reserve', 'gpt-5.6-sol', 'gpt-5.6-terra',
    'gpt-5.6-luna', 'gpt-5.5', 'codex-auto-review',
  ].map((slug) => ({ slug })),
});
const MODELS_CACHE_PATH = '/Users/test/.codex/models_cache.json';

function enoent(): Error {
  return Object.assign(new Error('ENOENT'), { code: 'ENOENT' });
}
```

(c) In `harness()`'s `deps` object, add this property directly after `access: vi.fn(async () => undefined),`:

```ts
    readFile: vi.fn(async (target: string) => {
      if (target === MODELS_CACHE_PATH) return LEGACY_CACHE;
      throw enoent();
    }),
```

With the legacy cache, every existing mapping test keeps its current expectations.

(d) The second `AdvisorReviewDeps` object in the real-process test (around `advisor-review.test.ts:531`, `spawn: nodeSpawn as AdvisorReviewDeps['spawn'],`) needs the property too: `readFile: (target: string, encoding: 'utf8') => readFile(target, encoding),`. `readFile` is already imported from `node:fs/promises`.

(e) In `describe('run_codex_advisor_review definition', …)`, add:

```ts
  it('publishes requester_model as a tier-word pattern, not a fixed enum', () => {
    const requester = advisorReviewToolDefinition.inputSchema.properties.requester_model as Record<string, unknown>;
    expect(requester).not.toHaveProperty('enum');
    const pattern = new RegExp(requester.pattern as string);
    for (const ok of ['gpt-6-sol', 'gpt-5.6-terra', 'gpt-7.1-luna', 'gpt-6-astra']) expect(pattern.test(ok)).toBe(true);
    for (const bad of ['gpt-6-unknown', 'gpt-6-sol-mini', 'claude-opus', 'gpt--sol']) expect(pattern.test(bad)).toBe(false);
  });
```

(f) In `describe('runCodexAdvisorReview', …)`, add:

```ts
  it.each([
    ['gpt-6-sol', 'gpt-6-astra'],
    ['gpt-6-luna', 'gpt-5.6-terra'],
    ['gpt-5.6-terra', 'gpt-6-sol'],
    ['gpt-5.6-sol', 'gpt-6-astra'],
    ['gpt-6-astra', 'gpt-6-astra'],
  ])('resolves %s one-up to the newest installed model: %s', async (requester, reviewer) => {
    const h = harness();
    h.deps.readFile = vi.fn(async (target: string) => {
      if (target === MODELS_CACHE_PATH) return GPT6_CACHE;
      throw enoent();
    });
    h.deps.trustedRequesterModel = requester;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: requester }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({ success: true, reviewer_model: reviewer }));
    expect(h.spawn.mock.calls[1][1]).toContain(reviewer);
    expect(h.deps.readFile).toHaveBeenCalledWith(MODELS_CACHE_PATH, 'utf8');
  });

  it('prefers the newest version at a tier', async () => {
    const h = harness();
    h.deps.readFile = vi.fn(async () => JSON.stringify({
      models: ['gpt-6-sol', 'gpt-7-sol', 'gpt-6.5-sol', 'gpt-6-astra'].map((slug) => ({ slug })),
    }));
    h.deps.trustedRequesterModel = 'gpt-5.6-terra';
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-terra' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reviewer_model).toBe('gpt-7-sol');
  });

  it.each([
    ['gpt-6-luna', 'gpt-5.6-terra'],
    ['gpt-6-sol', 'gpt-6-astra'],
  ])('falls back to the built-in ladder when the models cache is unreadable: %s -> %s', async (requester, reviewer) => {
    const h = harness();
    h.deps.readFile = vi.fn(async () => { throw enoent(); });
    h.deps.trustedRequesterModel = requester;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: requester }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reviewer_model).toBe(reviewer);
  });

  it('keeps the requester for a same-tier gpt-6 review', async () => {
    const h = harness();
    h.deps.trustedRequesterModel = 'gpt-6-sol';
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-6-sol', review_tier: 'same' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reviewer_model).toBe('gpt-6-sol');
  });

  it.each(['gpt-6-unknown', 'claude-opus', 'gpt-6-sol-mini', 42])(
    'rejects a non-Codex-shaped requester %s', async (requester) => {
      const h = harness();
      const result = await runCodexAdvisorReview(
        { packet: 'ok', requester_model: requester }, identity, session, runtime, h.deps,
      );
      expect(payload(result).reason).toBe('invalid-requester-model');
      expect(h.spawn).not.toHaveBeenCalled();
    },
  );

  it('parses tier and numeric version', () => {
    expect(parseCodexModel('gpt-6.5-sol')).toEqual({ version: [6, 5], tier: 'sol' });
    expect(parseCodexModel('gpt-6-terra-x')).toBeNull();
  });
```

Broken states caught:
- **Schema test:** fails while the enum remains.
- **gpt-6 mapping tests:** fail on today's hard-coded allowlist (`gpt-6-sol` is rejected) and on any non-installed-list mapping.
- **Newest-version test:** fails if the version comparison is wrong.
- **Fallback test:** fails if an unreadable cache crashes or maps incorrectly.
- **Reject test:** fails if the pattern is too permissive.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: FAIL. The import of `parseCodexModel` fails, so the whole file fails to run, or else the new tests fail.

**Step 3: Implement in `advisor-review.ts`.**

(a) Change the first import line to:

```ts
import { access as fsAccess, mkdtemp as fsMkdtemp, readFile as fsReadFile, rm as fsRm } from 'node:fs/promises';
```

(b) Replace the block from `const REQUESTER_MODELS = [` through the closing `};` of `REVIEWER_BY_REQUESTER`, keeping `REVIEW_TIERS` and `ReviewTier` intact, with:

```ts
const TIER_ORDER = ['luna', 'terra', 'sol', 'astra'] as const;
type CodexTier = typeof TIER_ORDER[number];
export const CODEX_MODEL_RE = /^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$/;
// Used when Codex's models_cache.json is missing, unreadable, or has no tier models.
export const FALLBACK_CODEX_MODELS = ['gpt-6-luna', 'gpt-5.6-terra', 'gpt-6-sol', 'gpt-6-astra'] as const;
type RequesterModel = string;
```

Keep `const REVIEW_TIERS = …` and `type ReviewTier = …` exactly as they are.

(c) Add `readFile: (target: string, encoding: 'utf8') => Promise<string>;` to `AdvisorReviewDeps`, directly after the `access:` line, and `readFile: fsReadFile,` to `DEFAULT_DEPS`, directly after `access: fsAccess,`.

(d) Replace `requester_model`'s schema entry, which currently holds `enum: [...REQUESTER_MODELS],`, with:

```ts
      requester_model: {
        type: 'string' as const,
        pattern: CODEX_MODEL_RE.source,
        description:
          'Current Codex requester model (any gpt-<version>-<luna|terra|sol|astra>); the broker maps the reviewer exactly once.',
      },
```

(e) Add these functions directly after `resolveTrustedCodexRequesterModel`:

```ts
export function parseCodexModel(slug: unknown): { version: number[]; tier: CodexTier } | null {
  if (typeof slug !== 'string') return null;
  const match = CODEX_MODEL_RE.exec(slug);
  if (!match) return null;
  return { version: match[1].split('.').map(Number), tier: match[2] as CodexTier };
}


function compareVersions(left: number[], right: number[]): number {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] ?? 0) - (right[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}


function newestAtTier(installed: readonly string[], tier: CodexTier): string | null {
  let best: { slug: string; version: number[] } | null = null;
  for (const slug of installed) {
    const parsed = parseCodexModel(slug);
    if (!parsed || parsed.tier !== tier) continue;
    if (!best || compareVersions(parsed.version, best.version) > 0) best = { slug, version: parsed.version };
  }
  return best?.slug ?? null;
}


export async function readInstalledCodexModels(
  deps: Pick<AdvisorReviewDeps, 'env' | 'readFile'>,
): Promise<string[]> {
  const codexHome = deps.env.CODEX_HOME || path.join(deps.env.HOME || os.homedir(), '.codex');
  try {
    const parsed = JSON.parse(await deps.readFile(path.join(codexHome, 'models_cache.json'), 'utf8')) as {
      models?: Array<{ slug?: unknown } | null>;
    };
    const slugs = (Array.isArray(parsed?.models) ? parsed.models : [])
      .map((entry) => entry?.slug)
      .filter((slug): slug is string => parseCodexModel(slug) !== null);
    if (slugs.length > 0) return slugs;
  } catch {
    // Missing, unreadable, or malformed cache: use the built-in fallback below.
  }
  return [...FALLBACK_CODEX_MODELS];
}


export function resolveCodexReviewer(requester: string, installed: readonly string[]): string {
  const parsed = parseCodexModel(requester);
  const ceiling = TIER_ORDER.length - 1;
  const start = parsed ? Math.min(TIER_ORDER.indexOf(parsed.tier) + 1, ceiling) : ceiling;
  for (let index = start; index <= ceiling; index += 1) {
    const newest = newestAtTier(installed, TIER_ORDER[index]);
    if (newest) return newest;
  }
  return FALLBACK_CODEX_MODELS[start];
}
```

(f) In `validateArgs`, replace:

```ts
  if (!REQUESTER_MODELS.includes(args.requester_model as RequesterModel)) {
    return { ok: false, reason: 'invalid-requester-model' };
  }
```

with:

```ts
  if (parseCodexModel(args.requester_model) === null) {
    return { ok: false, reason: 'invalid-requester-model' };
  }
```

(g) Replace:

```ts
  const reviewerModel = validated.reviewTier === 'same'
    ? validated.requesterModel
    : REVIEWER_BY_REQUESTER[validated.requesterModel];
```

with:

```ts
  const reviewerModel = validated.reviewTier === 'same'
    ? validated.requesterModel
    : resolveCodexReviewer(validated.requesterModel, await readInstalledCodexModels(deps));
```

**Step 4: Run the tests and typecheck, and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: all pass, 0 failed.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
```

Expected: exit 0, no output.

```bash
rg -n -e "REQUESTER_MODELS" -e "REVIEWER_BY_REQUESTER" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src
```

Expected: no output, exit 1.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/tools/advisor-review.ts worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts
```

---

## Task 2: Commander Codex model validation by tier word (R5, R6)

**Files:**
- Modify: `commander/src/ironclaude/provider_config.py`
- Test: `commander/tests/test_provider_config.py`

**Step 1: Update and add tests (RED).**

(a) Replace `test_codex_model_values_are_exact` (`test_provider_config.py:120-124`) with:

```python
def test_codex_model_values_must_match_tier_word(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["opus"] = "wrong-model"
    with pytest.raises(ProviderConfigError, match="gpt-<version>-<tier-word>"):
        parse_provider_config(raw)


def test_codex_accepts_gpt6_models(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"] = {
        "haiku": "gpt-6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-6-sol", "fable": "gpt-6-astra",
    }
    cfg = parse_provider_config(raw)
    assert cfg.model_for("codex", "opus") == "gpt-6-sol"
    assert cfg.model_for("codex", "haiku") == "gpt-6-luna"


def test_codex_accepts_future_versions(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["opus"] = "gpt-7.1-sol"
    assert parse_provider_config(raw).model_for("codex", "opus") == "gpt-7.1-sol"


@pytest.mark.parametrize(
    ("tier", "value"),
    [
        ("sonnet", "gpt-6-sol"),
        ("opus", "gpt-6-sol-mini"),
        ("opus", "GPT-6-sol"),
        ("haiku", "gpt--luna"),
        ("fable", None),
    ],
)
def test_codex_rejects_wrong_tier_word_or_shape(tier, value, base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"][tier] = value
    with pytest.raises(ProviderConfigError, match="gpt-<version>-<tier-word>"):
        parse_provider_config(raw)


def test_codex_rejects_extra_tier(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"]["mega"] = "gpt-6-astra"
    with pytest.raises(ProviderConfigError, match="gpt-<version>-<tier-word>"):
        parse_provider_config(raw)


def test_claude_models_remain_exact(base_config):
    raw = base_config()
    raw["clients"]["claude"]["models"]["opus"] = "claude-opus-4-8"
    with pytest.raises(ProviderConfigError, match="approved models"):
        parse_provider_config(raw)
```

Broken states caught:
- **Acceptance tests:** fail under the exact match (R5).
- **Reject tests:** fail if the check is too loose: wrong tier word, bad shape, extra key.
- **Claude test:** fails if the Claude side is loosened.
- **Default test:** the existing `test_codex_models_match_approved_tiers` still pins the unchanged defaults (R6).

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_provider_config.py
```

Expected: FAIL. The acceptance tests fail on the exact match, and the tests expecting the new message fail on message mismatch.

**Step 3: Implement in `provider_config.py`.**

(a) Directly after the `EXPECTED_MODELS = {…}` block (it ends at line 29), add:

```python
# Codex model names change with each release; accept any version whose tier word matches.
CODEX_TIER_WORDS = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}
CODEX_MODEL_RE = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$")


def _codex_models_valid(models: Mapping[str, object]) -> bool:
    if set(models) != set(TIER_NAMES):
        return False
    for tier, value in models.items():
        match = CODEX_MODEL_RE.fullmatch(value) if isinstance(value, str) else None
        if match is None or match.group(2) != CODEX_TIER_WORDS[tier]:
            return False
    return True
```

(b) Replace:

```python
        model_values = dict(models)
        if model_values != EXPECTED_MODELS[name]:
            raise ProviderConfigError(
                f"providers.clients.{name}.models must exactly match approved models"
            )
```

with:

```python
        model_values = dict(models)
        if name == "codex":
            if not _codex_models_valid(model_values):
                raise ProviderConfigError(
                    "providers.clients.codex.models must map each tier to a "
                    "gpt-<version>-<tier-word> model (haiku→luna, sonnet→terra, "
                    "opus→sol, fable→astra)"
                )
        elif model_values != EXPECTED_MODELS[name]:
            raise ProviderConfigError(
                f"providers.clients.{name}.models must exactly match approved models"
            )
```

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_provider_config.py tests/test_provider_router.py tests/test_grader_routing.py tests/test_worker_adapter.py
```

Expected: all pass, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/provider_config.py commander/tests/test_provider_config.py
```

---

## Task 3: Preflight resolves the codex-cli package entrypoint (R9.1, R9.2, R9.4, R9.5)

**Files:**
- Modify: `worker/scripts/codex-runtime-preflight.mjs`
- Test: `commander/tests/test_codex_runtime_preflight.py`

**Step 1: Add tests (RED).** In `commander/tests/test_codex_runtime_preflight.py`, add `import pytest` directly after `import os` (line 2); the file has no pytest import today. Then append the following, which reuses the existing helpers `executable` and `run_preflight`:

```python
def package_layout(tmp_path: Path, manifest=None, binary_dirs=("CodexCLI.app", "Contents", "MacOS")):
    package = tmp_path / "Resources" / "codex-cli"
    wrapper = executable(package / "bin" / "codex")
    companion = executable(package / "bin" / "codex-code-mode-host")
    binary = executable(package.joinpath(*binary_dirs) / "codex")
    body = {"layoutVersion": 1, "entrypoint": "bin/codex"} if manifest is None else manifest
    (package / "codex-package.json").write_text(body if isinstance(body, str) else json.dumps(body))
    return package, wrapper, companion, binary


def test_package_binary_uses_declared_entrypoint(tmp_path: Path):
    _, wrapper, companion, binary = package_layout(tmp_path)

    completed, payload = run_preflight(binary)

    assert completed.returncode == 0
    assert payload["status"] == "healthy"
    assert payload["reason"] == "source-is-destination"
    assert payload["invoked_launcher"] == str(wrapper.resolve())
    assert payload["source_companion"] == str(companion.resolve())
    assert not (binary.parent / "codex-code-mode-host").exists()


def test_package_binary_found_on_path_uses_entrypoint(tmp_path: Path):
    _, wrapper, _, binary = package_layout(tmp_path)

    completed, payload = run_preflight(None, env={"PATH": str(binary.parent)})

    assert completed.returncode == 0
    assert payload["invoked_launcher"] == str(wrapper.resolve())
    assert payload["status"] == "healthy"


def test_symlink_to_package_wrapper_uses_entrypoint(tmp_path: Path):
    _, wrapper, _, _ = package_layout(tmp_path)
    local = tmp_path / "local" / "bin"
    local.mkdir(parents=True)
    (local / "codex").symlink_to(wrapper)

    completed, payload = run_preflight(local / "codex")

    assert completed.returncode == 0
    assert payload["invoked_launcher"] == str(wrapper.resolve())
    assert payload["reason"] == "source-is-destination"
    assert not (local / "codex-code-mode-host").exists()


@pytest.mark.parametrize(
    "manifest",
    [
        {"layoutVersion": 2, "entrypoint": "bin/codex"},
        {"layoutVersion": 1, "entrypoint": "bin/missing"},
        {"layoutVersion": 1},
        "{not json",
    ],
)
def test_unusable_package_manifest_keeps_old_behavior(tmp_path: Path, manifest):
    _, _, _, binary = package_layout(tmp_path, manifest=manifest)

    completed, payload = run_preflight(binary)

    assert completed.returncode == 3
    assert payload["status"] == "blocked"
    assert payload["reason"] == "source-missing"
    assert payload["invoked_launcher"] == str(binary)


def test_escaping_entrypoint_is_ignored_even_when_its_target_exists(tmp_path: Path):
    cases = [
        ("../bin/codex", ("Resources", "bin", "codex")),
        ("bin/../../bin/codex", ("Resources", "bin", "codex")),
        ("/abs/bin/codex", ("Resources", "codex-cli", "abs", "bin", "codex")),  # path.join form
        ("<absolute>", ("escape", "codex")),  # path.resolve form
    ]
    for index, (entrypoint, escape) in enumerate(cases):
        root = tmp_path / str(index)
        target = root.joinpath(*escape)
        if entrypoint == "<absolute>":
            entrypoint = str(target)
        _, _, _, binary = package_layout(root, manifest={"layoutVersion": 1, "entrypoint": entrypoint})
        executable(target)
        executable(target.parent / "codex-code-mode-host")

        completed, payload = run_preflight(binary)

        assert completed.returncode == 3
        assert payload["status"] == "blocked"
        assert payload["reason"] == "source-missing"
        assert payload["invoked_launcher"] == str(binary)


def test_non_executable_entrypoint_keeps_old_behavior(tmp_path: Path):
    package, _, _, binary = package_layout(tmp_path, manifest={"layoutVersion": 1, "entrypoint": "bin/plain"})
    (package / "bin" / "plain").write_text("#!/bin/sh\nexit 0\n")

    completed, payload = run_preflight(binary)

    assert completed.returncode == 3
    assert payload["reason"] == "source-missing"
    assert payload["invoked_launcher"] == str(binary)


def test_package_manifest_in_sixth_directory_is_used(tmp_path: Path):
    _, wrapper, _, binary = package_layout(tmp_path, binary_dirs=("a", "b", "c", "d", "e"))

    completed, payload = run_preflight(binary)

    assert payload["status"] == "healthy"
    assert payload["invoked_launcher"] == str(wrapper.resolve())


def test_package_manifest_beyond_search_depth_is_ignored(tmp_path: Path):
    _, _, _, binary = package_layout(tmp_path, binary_dirs=("a", "b", "c", "d", "e", "f"))

    completed, payload = run_preflight(binary)

    assert payload["reason"] == "source-missing"
    assert payload["invoked_launcher"] == str(binary)
```

Each escape case creates an executable, plus its companion, exactly where an unchecked `path.join` or `path.resolve` of the entrypoint would land. If the absolute or `..` check were deleted, the run would report `healthy` with that target as the invoked launcher, so every assertion fails.
- The mid-path `bin/../../bin/codex` case refutes a check that only tests `startsWith('..')`.
- The non-executable case refutes replacing the executability check with an existence check.
- The 5-deep (manifest in the 6th directory) and 6-deep (7th directory) cases pin both edges of `PACKAGE_SEARCH_DEPTH`.

The search checks the launcher's own directory plus up to 5 parents, 6 directories in total. With `binary_dirs` a–f, the manifest sits in the 7th directory up and must be ignored. With `CodexCLI.app/Contents/MacOS`, it sits in the 4th.

Broken states caught:
- **Three positive tests:** fail today with `source-missing` or `destination-missing`.
- **Negative and depth tests:** guards. They pass today and must still pass, proving the entrypoint rule only applies to a valid, nearby, layoutVersion-1 manifest.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_codex_runtime_preflight.py
```

Expected: FAIL. The 4 positive tests fail (the three above plus `test_package_manifest_in_sixth_directory_is_used`). The negative, escape, non-executable and beyond-depth tests pass.

**Step 3: Implement in `worker/scripts/codex-runtime-preflight.mjs`.**

(a) Change the fs import to `import { access, lstat, readFile, realpath, stat, symlink } from 'node:fs/promises';`.

(b) After `const COMPANION = 'codex-code-mode-host';`, add:

```js
const PACKAGE_MANIFEST = 'codex-package.json';
// The launcher's own directory plus up to five parents (codex-cli/CodexCLI.app/Contents/MacOS/codex is 4).
const PACKAGE_SEARCH_DEPTH = 6;
```

(c) Add this exported function directly before `export async function resolveCodexPath(`:

```js
export async function resolvePackageEntrypoint(launcher) {
  let physical;
  try {
    physical = await realpath(launcher);
  } catch {
    return launcher;
  }
  let directory = path.dirname(physical);
  for (let depth = 0; depth < PACKAGE_SEARCH_DEPTH; depth += 1) {
    let raw = null;
    try {
      raw = await readFile(path.join(directory, PACKAGE_MANIFEST), 'utf8');
    } catch {
      raw = null;
    }
    if (raw !== null) {
      let manifest;
      try {
        manifest = JSON.parse(raw);
      } catch {
        return launcher;
      }
      const entrypoint = manifest?.entrypoint;
      if (
        manifest?.layoutVersion !== 1 ||
        typeof entrypoint !== 'string' ||
        entrypoint.length === 0 ||
        path.isAbsolute(entrypoint) ||
        entrypoint.split(/[\\/]/).includes('..')
      ) {
        return launcher;
      }
      const candidate = path.join(directory, entrypoint);
      return (await isExecutableRegularFile(candidate)) ? candidate : launcher;
    }
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  return launcher;
}
```

(d) In `resolveCodexPath`, replace `return { invokedLauncher: path.normalize(explicit) };` with `return { invokedLauncher: await resolvePackageEntrypoint(path.normalize(explicit)) };`, and `return { invokedLauncher: candidate };` with `return { invokedLauncher: await resolvePackageEntrypoint(candidate) };`.

**Step 4: Run the tests and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_codex_runtime_preflight.py tests/test_codex_brain_client.py
```

Expected: all pass, 0 failed.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/scripts/codex-runtime-preflight.mjs commander/tests/test_codex_runtime_preflight.py
```

---

## Task 4: Broker launches the codex-cli package entrypoint; CODEX_CLI_PATH fallback (R9.1, R9.3–R9.5)

**Depends on:** Task 1 (same files; `readFile` dep).

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/tools/advisor-review.ts`
- Test: `worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts`

**Step 1: Add tests (RED).**

(a) In `harness()`'s `deps` object, directly after the `readFile` property, add:

```ts
    realpath: vi.fn(async (target: string) => target),
    stat: vi.fn(async () => ({ isFile: () => true })),
```

In the real-process `AdvisorReviewDeps` object (around `:531`), add `realpath: (target: string) => realpath(target),` and `stat: (target: string) => stat(target),`, and add `realpath, stat` to the `node:fs/promises` import at the top of the test file.

(b) Add to `describe('runCodexAdvisorReview', …)`:

```ts
  function packageHarness(manifest: unknown, extraEnv: Record<string, string> = {}, pathEnv = '/pkg/CodexCLI.app/Contents/MacOS') {
    const h = harness([
      { stdout: JSON.stringify({
        schema_version: 1, mode: 'check', status: 'healthy',
        invoked_launcher: '/pkg/bin/codex', resolved_launcher: '/pkg/bin/codex',
        source_companion: '/pkg/bin/codex-code-mode-host', destination_companion: '/pkg/bin/codex-code-mode-host',
        action: 'none', reason: 'source-is-destination',
      }) + '\n' },
      { stdout: agentMessage('review report') },
    ]);
    h.deps.env = { ...h.deps.env, PATH: pathEnv, ...extraEnv };
    h.deps.readFile = vi.fn(async (target: string) => {
      if (target === MODELS_CACHE_PATH) return LEGACY_CACHE;
      if (target === '/pkg/codex-package.json') {
        return typeof manifest === 'string' ? manifest : JSON.stringify(manifest);
      }
      throw enoent();
    });
    return h;
  }

  it('launches the package entrypoint when PATH finds the bare CodexCLI binary', async () => {
    const h = packageHarness({ layoutVersion: 1, entrypoint: 'bin/codex' });
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).success).toBe(true);
    expect(h.spawn.mock.calls[0][1]).toEqual([
      '/installed/ironclaude/scripts/codex-runtime-preflight.mjs', '--mode', 'check', '--codex-path', '/pkg/bin/codex',
    ]);
    expect(h.spawn.mock.calls[1][0]).toBe('/pkg/bin/codex');
  });

  it('uses CODEX_CLI_PATH when PATH has no codex', async () => {
    const h = packageHarness(
      { layoutVersion: 1, entrypoint: 'bin/codex' },
      { CODEX_CLI_PATH: '/pkg/CodexCLI.app/Contents/MacOS/codex' },
      '/empty',
    );
    h.deps.access = vi.fn(async (target: string) => {
      if (target.startsWith('/empty')) throw enoent();
    });
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).success).toBe(true);
    expect(h.spawn.mock.calls[1][0]).toBe('/pkg/bin/codex');
  });

  it('ignores a relative CODEX_CLI_PATH', async () => {
    const h = packageHarness({ layoutVersion: 1, entrypoint: 'bin/codex' }, { CODEX_CLI_PATH: 'codex' }, '/empty');
    // Only the empty PATH entry is missing; a bare 'codex' would pass access() if it were ever consulted.
    h.deps.access = vi.fn(async (target: string) => {
      if (target.startsWith('/empty')) throw enoent();
    });
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reason).toBe('codex-executable-not-found');
    expect(h.spawn).not.toHaveBeenCalled();
  });

  it.each([
    [{ layoutVersion: 2, entrypoint: 'bin/codex' }],
    [{ layoutVersion: 1, entrypoint: '/abs/codex' }],
    [{ layoutVersion: 1, entrypoint: '../codex' }],
    ['{not json'],
  ])('keeps the bare launcher for an unusable package manifest %#', async (manifest) => {
    const h = packageHarness(manifest);
    await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(h.spawn.mock.calls[0][1]).toContain('/pkg/CodexCLI.app/Contents/MacOS/codex');
  });

  it.each(['stat', 'access'] as const)('keeps the bare launcher when the entrypoint fails %s', async (dep) => {
    const h = packageHarness({ layoutVersion: 1, entrypoint: 'bin/codex' });
    if (dep === 'stat') {
      h.deps.stat = vi.fn(async (target: string) => {
        if (target === '/pkg/bin/codex') throw enoent();
        return { isFile: () => true };
      });
    } else {
      h.deps.access = vi.fn(async (target: string) => {
        if (target === '/pkg/bin/codex') throw enoent();
      });
    }
    await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(h.spawn.mock.calls[0][1]).toContain('/pkg/CodexCLI.app/Contents/MacOS/codex');
  });

  it.each([
    ['/pkg/a/b/c/d/e', '/pkg/bin/codex'],            // manifest in the 6th directory: found
    ['/pkg/a/b/c/d/e/f', '/pkg/a/b/c/d/e/f/codex'],  // 7th: ignored
  ])('bounds the manifest search to six directories from %s', async (pathEnv, expected) => {
    const h = packageHarness({ layoutVersion: 1, entrypoint: 'bin/codex' }, {}, pathEnv);
    await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(h.spawn.mock.calls[0][1]).toContain(expected);
  });
```

With the relative-path test's `access` failing only under `/empty`, a deleted absolute-path check would let the bare `codex` through: the preflight would spawn and both assertions would fail. The `stat`/`access` cases refute dropping the executability check; the harness otherwise always succeeds. The depth cases pin both edges of `CODEX_PACKAGE_SEARCH_DEPTH`.

With the harness `realpath` as the identity function, the walk from `/pkg/CodexCLI.app/Contents/MacOS/codex` checks `MacOS`, `Contents`, `CodexCLI.app`, then `/pkg`, where the manifest is found. In the negative cases, the preflight fixture still reports `/pkg/bin/codex`, so the broker's equality check fails closed after spawning the preflight. The test asserts only that the preflight was invoked with the bare launcher.

Broken states caught:
- **Positive tests:** fail today because the broker launches the bare binary or finds no codex.
- **Relative-path test:** fails if `CODEX_CLI_PATH` is trusted without the absolute-path check.
- **Negative tests:** fail if the rule applies to an unusable manifest.

**Step 2: Run the tests and confirm they fail.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: FAIL. The new positive tests fail.

**Step 3: Implement in `advisor-review.ts`.**

(a) Change the fs import to:

```ts
import { access as fsAccess, mkdtemp as fsMkdtemp, readFile as fsReadFile, realpath as fsRealpath, rm as fsRm, stat as fsStat } from 'node:fs/promises';
```

(b) Add to `AdvisorReviewDeps`, directly after `readFile`:

```ts
  realpath: (target: string) => Promise<string>;
  stat: (target: string) => Promise<{ isFile(): boolean }>;
```

Add to `DEFAULT_DEPS`, after `readFile: fsReadFile,`: `realpath: fsRealpath,` and `stat: fsStat,`.

(c) Directly before `async function resolveCodexExecutable(`, add:

```ts
const CODEX_PACKAGE_MANIFEST = 'codex-package.json';
// The launcher's own directory plus up to five parents, mirroring codex-runtime-preflight.mjs.
const CODEX_PACKAGE_SEARCH_DEPTH = 6;


async function isExecutableFile(target: string, deps: AdvisorReviewDeps): Promise<boolean> {
  try {
    if (!(await deps.stat(target)).isFile()) return false;
    await deps.access(target, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}


export async function resolveCodexPackageEntrypoint(launcher: string, deps: AdvisorReviewDeps): Promise<string> {
  let physical: string;
  try {
    physical = await deps.realpath(launcher);
  } catch {
    return launcher;
  }
  let directory = path.dirname(physical);
  for (let depth = 0; depth < CODEX_PACKAGE_SEARCH_DEPTH; depth += 1) {
    let raw: string | null;
    try {
      raw = await deps.readFile(path.join(directory, CODEX_PACKAGE_MANIFEST), 'utf8');
    } catch {
      raw = null;
    }
    if (raw !== null) {
      let manifest: { layoutVersion?: unknown; entrypoint?: unknown } | null;
      try {
        manifest = JSON.parse(raw) as { layoutVersion?: unknown; entrypoint?: unknown } | null;
      } catch {
        return launcher;
      }
      const entrypoint = manifest?.entrypoint;
      if (
        manifest?.layoutVersion !== 1 ||
        typeof entrypoint !== 'string' ||
        entrypoint.length === 0 ||
        path.isAbsolute(entrypoint) ||
        entrypoint.split(/[\\/]/).includes('..')
      ) {
        return launcher;
      }
      const candidate = path.join(directory, entrypoint);
      return (await isExecutableFile(candidate, deps)) ? candidate : launcher;
    }
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  return launcher;
}
```

(d) Replace the body of `resolveCodexExecutable` with:

```ts
async function resolveCodexExecutable(deps: AdvisorReviewDeps): Promise<string | null> {
  for (const directory of (deps.env.PATH ?? '').split(path.delimiter)) {
    if (!directory) continue;
    const candidate = path.resolve(directory, process.platform === 'win32' ? 'codex.exe' : 'codex');
    try {
      await deps.access(candidate, constants.X_OK);
    } catch {
      continue;
    }
    return resolveCodexPackageEntrypoint(candidate, deps);
  }
  const cliPath = deps.env.CODEX_CLI_PATH;
  if (typeof cliPath === 'string' && path.isAbsolute(cliPath)) {
    try {
      await deps.access(cliPath, constants.X_OK);
      return resolveCodexPackageEntrypoint(cliPath, deps);
    } catch {
      // Not a usable launcher.
    }
  }
  return null;
}
```

**Step 4: Run the tests and typecheck, and confirm they pass.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts
```

Expected: all pass, 0 failed.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
```

Expected: exit 0, no output.

**Step 5: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/tools/advisor-review.ts worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts
```

---

## Task 5: Docs, skills, templates and their contract tests (R7, R9.6)

**Files:**
- Modify: `AGENTS.md`, `CODEX_SETUP.md`, `README.md`
- Modify: `commander/src/ironclaude/templates/worker_agents.md`, `commander/src/ironclaude/templates/worker_claude_md.md`
- Modify: `worker/skills/activate-professional-mode/SKILL.md`, `worker/skills/advisor-fallback/SKILL.md`, `worker/skills/executing-plans/SKILL.md`
- Test: `commander/tests/test_worker_claude_md_template.py`, `commander/tests/test_activation_client_parity.py`, `commander/tests/test_advisor_fallback_directive.py`, `commander/tests/test_executing_plans_skill.py`

**Canonical replacements.** Apply each one to every listed file, tests included, exactly:
- **OLD1** `gpt-5.6-luna → gpt-5.6-terra → gpt-5.6-sol → gpt-6-astra` becomes **NEW1** `gpt-<version>-luna → gpt-<version>-terra → gpt-<version>-sol → gpt-<version>-astra`.
- **OLD2** `gpt-5.6-luna→gpt-5.6-terra→gpt-5.6-sol→gpt-6-astra` becomes **NEW2** `gpt-<version>-luna→gpt-<version>-terra→gpt-<version>-sol→gpt-<version>-astra`.
- **OLD3** (`executing-plans` SKILL, the line starting `   - Codex: Luna→`): `Luna→`gpt-5.6-terra`, Terra→`gpt-5.6-sol`, Sol→`gpt-6-astra`, and Astra→`gpt-6-astra`; Astra is the ceiling and uses a fresh same-tier `gpt-6-astra` reviewer.` becomes **NEW3** `Luna→Terra→Sol→Astra, each resolved by the broker to the newest installed `gpt-<version>-<tier>` model (today Luna→`gpt-5.6-terra`, Terra→`gpt-6-sol`, Sol→`gpt-6-astra`, and Astra→`gpt-6-astra`); Astra is the ceiling and uses a fresh same-tier `gpt-6-astra` reviewer.`
- **OLD4** (`executing-plans` SKILL, fix-advisor line): `Luna→`gpt-5.6-terra`, Terra→`gpt-5.6-sol`, Sol→`gpt-6-astra`, Astra ceiling→same-tier Astra)` becomes **NEW4** `Luna→Terra→Sol→Astra, each the newest installed `gpt-<version>-<tier>` model, Astra ceiling→same-tier Astra)`.

**Step 1: Update the contract tests (RED).**
- **`test_advisor_fallback_directive.py`:**
  - set `CODEX_MODEL_LADDER` (line 18) to NEW1;
  - replace OLD2 with NEW2 on line 84;
  - replace line 93's assert with `assert "`gpt-6-sol` → `gpt-6-astra`" in text`;
  - add `assert "gpt-<version>-<luna|terra|sol|astra>" in text` to `test_codex_advisor_ladder_names_astra_requester_and_ceiling_without_claiming_fable`.
- **`test_worker_claude_md_template.py:100`:** OLD1 becomes NEW1 (inside the backticks).
- **`test_activation_client_parity.py`:** replace every OLD1 with NEW1 and every OLD2 with NEW2 (lines 122, 135, 423).
- **`test_executing_plans_skill.py`:**
  - line 140's asserted substring becomes `Luna→Terra→Sol→Astra, each resolved by the broker to the newest installed `gpt-<version>-<tier>` model`;
  - lines 148–151's asserted substring becomes `Luna→Terra→Sol→Astra, each the newest installed `gpt-<version>-<tier>` model, Astra ceiling→same-tier Astra`.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_advisor_fallback_directive.py tests/test_worker_claude_md_template.py tests/test_activation_client_parity.py tests/test_executing_plans_skill.py
```

Expected: FAIL, because the docs still carry the old strings.

**Step 2: Update the docs (GREEN).**
- **OLD1 → NEW1** in `AGENTS.md:66`, `commander/src/ironclaude/templates/worker_agents.md:52`, `worker/skills/activate-professional-mode/SKILL.md:317` and `worker/skills/advisor-fallback/SKILL.md:77`.
- **OLD2 → NEW2** in `commander/src/ironclaude/templates/worker_claude_md.md:53` and `worker/skills/activate-professional-mode/SKILL.md:455` and `:519`.
- **OLD3 → NEW3** at `worker/skills/executing-plans/SKILL.md:197`, and **OLD4 → NEW4** at `:376`.
- **`worker/skills/advisor-fallback/SKILL.md`:** apply OLD1 → NEW1 on line 77 first, then:
  - Replace lines 56–57, currently:

    ```
    - `requester_model`: your current full Codex model name: `gpt-5.6-luna`, `gpt-5.6-terra`,
      `gpt-5.6-sol`, or `gpt-6-astra`. Do not pre-map it; the broker rejects any mismatch with
    ```

    with:

    ```
    - `requester_model`: your current full Codex model name, any `gpt-<version>-<luna|terra|sol|astra>`
      model (for example `gpt-6-sol`). Do not pre-map it; the broker rejects any mismatch with
    ```

  - Replace lines 77–78, which after OLD1 → NEW1 read:

    ```
    (`gpt-<version>-luna → gpt-<version>-terra → gpt-<version>-sol → gpt-<version>-astra`). The broker maps
    **`gpt-5.6-sol` → `gpt-6-astra`**. At the Astra ceiling it runs
    ```

    with:

    ```
    (`gpt-<version>-luna → gpt-<version>-terra → gpt-<version>-sol → gpt-<version>-astra`). The broker
    resolves each tier to the newest installed model in `$CODEX_HOME/models_cache.json` (built-in
    fallback `gpt-6-luna`, `gpt-5.6-terra`, `gpt-6-sol`, `gpt-6-astra`), so today it maps
    **`gpt-6-sol` → `gpt-6-astra`**. At the Astra ceiling it runs
    ```

- **`CODEX_SETUP.md`:**
  - Directly after the `| fable  | gpt-6-astra     |` table row (line 43), add a blank line and: `These are the defaults. Any `gpt-<version>-<tier>` model whose tier word matches is accepted (haiku→luna, sonnet→terra, opus→sol, fable→astra), for example `gpt-6-sol` for opus.`
  - Directly after line 31 (`same repair before cachebusting, building, reinstalling, and restarting Codex.`), add a blank line and the **layout paragraph** below.
- **`README.md`:** directly after line 128 (`building, validating, and reinstalling the plugin.`), add a blank line and the same **layout paragraph**.

The **layout paragraph**:

```
When the ChatGPT app ships Codex as a `codex-cli/` package (`codex-package.json`, `layoutVersion` 1),
IronClaude launches the package's declared entrypoint (`codex-cli/bin/codex`), whose
`codex-code-mode-host` companion sits beside it, so no symlink is needed and nothing inside the signed
`CodexCLI.app` is touched. If an older `~/.local/bin/codex` or `~/.local/bin/codex-code-mode-host`
symlink still points at the removed `Resources/codex`, re-point it to `codex-cli/bin/` (or delete it).
```

**Step 3: Run the tests and the absence check.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_advisor_fallback_directive.py tests/test_worker_claude_md_template.py tests/test_activation_client_parity.py tests/test_executing_plans_skill.py
```

Expected: all pass, 0 failed.

```bash
rg -n -e "gpt-5.6-luna → gpt-5.6-terra" -e "gpt-5.6-luna→gpt-5.6-terra" -e "Terra→.gpt-5.6-sol" -e "gpt-5.6-sol. → .gpt-6-astra" /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/CODEX_SETUP.md /Users/roberthyatt/Code/ironclaude/README.md /Users/roberthyatt/Code/ironclaude/worker/skills /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/templates /Users/roberthyatt/Code/ironclaude/commander/tests
```

Expected: no output, exit 1.

**Step 4: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add AGENTS.md CODEX_SETUP.md README.md commander/src/ironclaude/templates/worker_agents.md commander/src/ironclaude/templates/worker_claude_md.md worker/skills/activate-professional-mode/SKILL.md worker/skills/advisor-fallback/SKILL.md worker/skills/executing-plans/SKILL.md commander/tests/test_worker_claude_md_template.py commander/tests/test_activation_client_parity.py commander/tests/test_advisor_fallback_directive.py commander/tests/test_executing_plans_skill.py
```

---

## Task 6: v1.1.15 version bump, CHANGELOG, README, full suites (R8)

**Depends on:** Tasks 1–5.

**No tests required:** this task edits versions and docs only; the full suites verify it.

**Files:**
- Modify: `.claude-plugin/marketplace.json`, `worker/.claude-plugin/plugin.json`, `worker/.codex-plugin/plugin.json`, `worker/mcp-servers/workspace-manager/package.json`, `worker/mcp-servers/workspace-manager/package-lock.json`, `commander/pyproject.toml`
- Modify: `CHANGELOG.md`, `README.md`

**Step 1: Get a fresh Codex stamp.**

```bash
date -u +%Y%m%d%H%M%S
```

Expected: 14 digits. Use this literal value as `<STAMP>`.

**Step 2: Bump versions with the Edit tool.**
- `"version": "1.1.14",` becomes `"version": "1.1.15",` in `marketplace.json`, `worker/.claude-plugin/plugin.json`, workspace-manager `package.json`, and both occurrences in `package-lock.json` (lines 3 and 9, not ieee754).
- `worker/.codex-plugin/plugin.json`: `"1.1.14+codex.20260929004141"` becomes `"1.1.15+codex.<STAMP>"`.
- `commander/pyproject.toml`: `version = "1.1.14"` becomes `version = "1.1.15"`.

```bash
rg -n -F 1.1.15 /Users/roberthyatt/Code/ironclaude/.claude-plugin/marketplace.json /Users/roberthyatt/Code/ironclaude/worker/.claude-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/.codex-plugin/plugin.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/package.json /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/package-lock.json /Users/roberthyatt/Code/ironclaude/commander/pyproject.toml
```

Expected: 7 lines.

**Step 3: Run the full suites.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run
```

Expected: 0 failed. Record the passed count V.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the passed count N.

**Step 4: CHANGELOG.** Insert directly above `## 1.1.14: …`, with `<V>` and `<N>` filled in:

```
## 1.1.15: Forgiving Codex model ladder and the codex-cli package layout

- **Codex model names no longer have to match a hard-coded list.** Codex moved to gpt-6 (`gpt-6-sol` by default, and gpt-6 has no terra), and the advisor broker rejected every requester outside `gpt-5.6-luna/terra/sol` and `gpt-6-astra`, blocking enforced plan review. The broker now accepts any `gpt-<version>-<luna|terra|sol|astra>` model (its tool schema publishes that pattern instead of an enum), rejects anything else, and resolves the one-tier-up reviewer to the newest installed model at the next tier from Codex's own `$CODEX_HOME/models_cache.json`, skipping a tier that has no model and falling back to a built-in `gpt-6-luna`, `gpt-5.6-terra`, `gpt-6-sol`, `gpt-6-astra` ladder when the cache is unreadable. Commander's provider config accepts any Codex model whose tier word matches its tier; its defaults are unchanged. (`advisor-review.ts`, `provider_config.py`; covered by `advisor-review.test.ts` / `test_provider_config.py`.)
- **The broker works with the new `codex-cli` package layout.** A ChatGPT.app update moved Codex into `Resources/codex-cli/` (`codex-package.json` layoutVersion 1, a `bin/codex` wrapper beside `bin/codex-code-mode-host`, and the real binary in `CodexCLI.app` with no companion), so the runtime preflight failed with `source-missing`. The preflight and the broker now launch the package's declared entrypoint whenever the launcher sits inside such a package (found within six directories), and the broker falls back to `CODEX_CLI_PATH` when PATH has no `codex`. An unusable manifest keeps the old behavior, and nothing inside the signed `CodexCLI.app` is touched. (`codex-runtime-preflight.mjs`, `advisor-review.ts`; covered by `test_codex_runtime_preflight.py` / `advisor-review.test.ts`.)
- Docs, skills and templates describe the tier-word rule and the new layout, including how to re-point a stale `~/.local/bin/codex` symlink.
- Deploy: rebuild the state-manager `dist/` into both plugin caches (Codex and Claude) and restart Commander. Suites: state-manager vitest <V>, commander <N>.
```

**Step 5: README "What's New".** Replace `## What's New in v1.1.14` with `## What's New in v1.1.15`, followed by the two bullets below. Turn the three former v1.1.14 bullets into a `### Earlier — v1.1.14` section, and delete the old `### Earlier — v1.1.13` heading and its bullets, keeping the final `- See [CHANGELOG.md](CHANGELOG.md) …` bullet.

```
- **Codex's new models just work.** IronClaude accepts any `gpt-<version>-<tier>` Codex model (for example `gpt-6-sol`) and picks the reviewer one tier up from the models Codex actually has installed, so a new Codex model family no longer blocks plan review.
- **Works with the new ChatGPT app layout.** When Codex ships as a `codex-cli/` package, IronClaude launches its declared entrypoint, so the advisor review no longer fails with `source-missing`.
```

**Step 6: Verify the docs.**

```bash
rg -n -F -e "## 1.1.15:" -e "## What's New in v1.1.15" -e "### Earlier — v1.1.14" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: 3 lines.

```bash
rg -n -F "Earlier — v1.1.13" /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: no output, exit 1.

**Step 7: Stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add .claude-plugin/marketplace.json worker/.claude-plugin/plugin.json worker/.codex-plugin/plugin.json worker/mcp-servers/workspace-manager/package.json worker/mcp-servers/workspace-manager/package-lock.json commander/pyproject.toml CHANGELOG.md README.md
```

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder-requirements.md commander/docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder-design.md commander/docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder.md commander/docs/plans/2026-09-30-v1-1-15-forgiving-codex-model-ladder.plan.json
```

Expected: exit 0.
