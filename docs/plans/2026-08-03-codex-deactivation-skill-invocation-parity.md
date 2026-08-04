# Codex Deactivation Skill-Invocation Parity Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task in subagent-sequential mode with Terra workers.

**Goal:** Make the existing Codex Desktop deactivation skill chip enter the same human-only hook path as `/deactivate-professional-mode`.

**Requirements:** `docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity-requirements.md`

**Design:** `docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity-design.md`

**Architecture:** Normalize only the exact standalone Markdown link emitted by the Codex skill chip into the already-supported bare dollar invocation. Reuse all existing session, SQL, workflow-stage, audit, and verification behavior unchanged.

**Tech Stack:** Bash 3.2+, jq, SQLite, shell integration tests, pytest.

## Execution invariants

- Shell state does not persist between steps; commands use literal paths and no prior exports.
- Execution cwd is `commander/`; Git commands use `git -C /Users/roberthyatt/Code/ironclaude`.
- Quote globs; zsh `nomatch` must not decide evidence.
- Do not use foreground `sleep`.
- `docs/` is ignored; workflow artifacts are staged with `git add -f`.
- Empty output is not success. Test commands and byte comparisons return nonzero on failure.
- RED must fail on the actual Codex Markdown payload before production code changes.
- No agent may directly turn professional mode off, mutate the session database, or synthesize a human prompt for live acceptance.
- No state-manager, skill, Commander, provider, activation, packaging, version, commit, or push change is authorized.

---

## Task 1: Normalize the exact Codex deactivation skill envelope with TDD

**Files:**

- Modify: `worker/hooks/tests/test-professional-mode-deactivation.sh`
- Modify: `worker/hooks/state-activator.sh`

### Step 1: Add protocol-shaped positive and fail-closed negative fixtures

In `worker/hooks/tests/test-professional-mode-deactivation.sh`, first make the existing hook path
overrideable for deployed-artifact verification without changing its default:

```bash
HOOK="${HOOK:-${SCRIPT_DIR}/../state-activator.sh}"
```

Then add these assertions after the
existing bare-dollar positive fixtures and before the existing negative fixtures:

```bash
assert_deactivates "Codex skill-link invocation" '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)'
assert_deactivates "Codex skill-link invocation with outer whitespace" '  [$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)  '

assert_ignored "skill link embedded in prose" 'please [$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "skill link with wrong skill path" '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/activate-professional-mode/SKILL.md)'
assert_ignored "skill link with relative target" '[$ironclaude:deactivate-professional-mode](skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "skill link with URL target" '[$ironclaude:deactivate-professional-mode](https://example.invalid/skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "uppercase skill-link label" '[$IRONCLAUDE:DEACTIVATE-PROFESSIONAL-MODE](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "skill link in code span" '`[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)`'
assert_ignored "skill link with suffix" '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md) now'
```

Retain every existing fixture and state assertion.

### Step 2: Run the focused hook test and prove RED

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```

Expected: exit 1; the two named `Codex skill-link invocation` assertions report
`expected=off|idle actual=on|brainstorming`. Existing slash, bare-dollar, negative, active-task,
session-isolation, and warning assertions remain PASS. Any other causal failure stops execution.

### Step 3: Normalize the exact standalone Codex envelope

In `worker/hooks/state-activator.sh`, replace the current non-slash branch at lines 54-61 with:

```bash
else
  # Codex submits a selected plugin skill as a Markdown link. Normalize only the
  # exact standalone deactivation envelope to the existing canonical token.
  TRIMMED_PROMPT=$(printf '%s' "$USER_PROMPT" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
  CODEX_DEACTIVATE_LINK_RE='^\[\$ironclaude:deactivate-professional-mode\]\(/[^)[:cntrl:]]*/skills/deactivate-professional-mode/SKILL\.md\)$'
  if [[ "$TRIMMED_PROMPT" =~ $CODEX_DEACTIVATE_LINK_RE ]]; then
    TRIMMED_PROMPT='$ironclaude:deactivate-professional-mode'
  fi
  # Keep canonical matching case-sensitive and exact so prose, code spans,
  # escaped dollars, and prefix/suffix variants cannot deactivate the session.
  if [ "$TRIMMED_PROMPT" = '$ironclaude:deactivate-professional-mode' ]; then
    DEACTIVATE_REQUEST="true"
  fi
fi
```

Do not change the slash matcher or any code after `DEACTIVATE_REQUEST` is set.

### Step 4: Prove GREEN with focused hook and Commander parity suites

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh && \
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_deactivation_client_parity.py -q
```

Expected: both commands exit 0; every hook assertion passes and Commander reports `4 passed`.
This catches both recognition regressions and accidental drift in the existing human-only skill
contract.

### Step 5: Inspect and stage only the bounded implementation files

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --check && \
git -C /Users/roberthyatt/Code/ironclaude diff -- worker/hooks/state-activator.sh worker/hooks/tests/test-professional-mode-deactivation.sh && \
git -C /Users/roberthyatt/Code/ironclaude add -- worker/hooks/state-activator.sh worker/hooks/tests/test-professional-mode-deactivation.sh && \
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-only | /usr/bin/python3 -c 'import sys; paths=[line.strip() for line in sys.stdin if line.strip()]; required={"worker/hooks/state-activator.sh","worker/hooks/tests/test-professional-mode-deactivation.sh"}; workflow={"docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity-requirements.md","docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity-design.md","docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity.md","docs/plans/2026-08-03-codex-deactivation-skill-invocation-parity.plan.json"}; unexpected=set(paths)-(required|workflow); missing=required-set(paths); print("STAGED_PATHS"); print("\n".join(paths)); print("MISSING_TASK_PATHS="+("none" if not missing else ",".join(sorted(missing)))); print("UNEXPECTED_PATHS="+("none" if not unexpected else ",".join(sorted(unexpected)))); raise SystemExit(0 if not missing and not unexpected else 1)'
```

Expected: `diff --check` exits 0; diff contains only exact-envelope recognition and its fixtures;
staged paths are the four workflow artifacts plus the two Task 1 files, with no unrelated paths.
Professional mode blocks commit.

---

## Task 2: Deploy through the existing hook path and verify runtime parity

**Files:**

- Read-only verification authority: `worker/hooks/state-activator.sh`
- Read-only verification authority: `worker/hooks/tests/test-professional-mode-deactivation.sh`

No repository file edits are permitted in Task 2. Any failure requiring a repair returns through
the fix-first review path to Task 1's two bounded files.

### Step 1: Re-run focused acceptance before deployment

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh && \
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_deactivation_client_parity.py -q
```

Expected: both commands exit 0; hook fixtures all pass and Commander reports `4 passed`.

### Step 2: Deploy with the existing bounded target

Run:

```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```

Expected: exit 0; all existing worker hooks are copied to
`/Users/roberthyatt/.claude/ironclaude-hooks`, and the target either reports its existing Claude
plugin-cache copy or its existing bounded warning. No deployment code or version metadata changes.

### Step 3: Prove the file that runs is the reviewed source

Run:

```bash
cmp -s /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh /Users/roberthyatt/.claude/ironclaude-hooks/state-activator.sh && \
shasum -a 256 /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh /Users/roberthyatt/.claude/ironclaude-hooks/state-activator.sh
```

Expected: `cmp` exits 0 and both printed SHA-256 values are identical. This fails if deployment
left the hook path executed by `worker/hooks/hooks.json` stale.

### Step 4: Run the deployed hook against the exact Codex payload in the isolated harness

Run:

```bash
HOOK=/Users/roberthyatt/.claude/ironclaude-hooks/state-activator.sh bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```

Expected: exit 0 with every assertion passing. This proves the deployed artifact accepts the real
Codex envelope without an agent mutating the live session database or synthesizing a human prompt.

### Step 5: Reconfirm professional mode remained on during agent-run verification

Call provider-native `get_professional_mode` for session
`019fc109-7175-7b72-8925-f21d9347e22a`.

Expected: `professional_mode='on'`, `client='codex'`, and the same session ID. An off result proves
an unsafe test touched live state and must be reported immediately; do not reactivate silently.

### Step 6: Report the human acceptance boundary

Report that source and the executed stable hook passed the exact UI payload regression. State that
the next standalone human skill-chip invocation is the live acceptance event; the agent did not
trigger it because professional-mode deactivation is intentionally human-only. Do not add a
fallback, directly update SQLite, call `set_professional_mode(off)`, or ask the operator to run a
read-only diagnostic.

No staging step is required because Task 2 must not edit repository files.
