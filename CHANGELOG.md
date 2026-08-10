# Changelog

> **Versioning.** IronClaude is not strict semver: within a minor series both
> features and fixes increment the patch number (`1.0.N`), and a minor bump
> (`1.1.0`) marks a release significant enough to warrant one. The version is declared in
> `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`,
> `worker/.codex-plugin/plugin.json`, `worker/mcp-servers/workspace-manager/package.json`, and
> `.claude-plugin/marketplace.json`, kept in lockstep by
> `commander/tests/test_version_consistency.py`. Each release commit is tagged
> `vX.Y.Z`. Land changes under `## [Unreleased]` as you go, then rename that
> heading to the new version at release time so the entry matches what shipped.

## 1.1.5: self-recovering managed worktrees, Opus 4.8 pinning, Codex grader diagnostics

- Managed worktrees now recover themselves in-session — no external terminal, ever. Finalize
  verifies a clean tree *before* it freezes (fail fast on a dirty tree, no freeze), and every
  integration path recycles the worktree in place rather than removing it, so the cwd, GUID,
  worktree, and branch survive across sequential commits and the per-loop `integration_records`
  row and candidate ref are cleared so a second finalize on the same GUID no longer collides. When
  a finalize is interrupted and an assignment freezes at `ready_for_integration`, a new
  ownership-gated `reconcile_finalization` MCP tool completes or aborts it with **no fresh human
  intent and no sqlite surgery**: `reconcileFinalization` deletes a provably-stale
  `integration_records` row (gated on both an ancestry proof and a present, differing candidate
  ref — an absent candidate refuses) so a reused-GUID strand reaches the existing self-healing
  path, and it never pushes. The frozen guard now permits read-only git and a memory-path write
  while frozen, so the agent can inspect state and record findings instead of being locked out.
- Operator-intervention gaps on the mechanical failure paths are closed. Stale primary-checkout
  locks auto-reap at every ownership seam while a live owner renews its lock on each file operation
  (heartbeat), so an active session is never reaped but a genuinely dead one still is; quoted
  `git -C <owned-worktree>` is recognized as internal instead of falsely rejected; a session writes
  its own gitignored `docs/plans` and `docs/reviews` artifacts without the human-only
  `/use-primary-checkout`. Integration recovery is completed end-to-end: the status probe is
  non-mutating, Commander triggers the deferred integrated-cleanup so an integrated-but-uncleaned
  worktree no longer leaks, and a conflict/rebase resolution that changes the reviewed content
  routes to a repair channel (fresh commit on approval, restore-frozen on decline) instead of
  dead-ending.
- End-to-end usability fixes from live field reports. The status payload gains `effectiveRoot`,
  `primaryOwnedByThisSession` (an exact three-part ownership match), and a live `currentHead`, so a
  row can tell an isolated session apart from one whose writes land in the primary checkout and no
  longer reports a stale head. Worktree isolation is decoupled from professional mode (an owner
  redirects writes into its worktree even with mode off), `git -C <path> add` is allowed
  pre-execution, deactivation preserves `workflow_stage` mid-plan, isolated worktrees inherit
  explicitly-configured shared resources (models, `.venv`) as anchored-exclude symlinks, and
  `node_modules/` is ignored at the repo root so test-tool caches never dirty the managed worktree.
- Opus tier pinned to `claude-opus-4-8`. Every path that passed the bare `opus` string to the
  Claude CLI now uses `claude-opus-4-8` explicitly — config, defaults, fallback literals, provider
  `EXPECTED_MODELS`, and the corresponding test assertions — preventing silent drift to Opus 5 as
  the `opus` alias resolution changes. IronClaude currently **recommends Opus 4.8 over Opus 5**,
  which tends to be too easily distracted to follow the structured workflow reliably; see the
  README's Model Configuration section.
- Codex grader failures now say what actually went wrong. On a nonzero exit the diagnostic was
  `stderr or stdout` — and because `codex exec --json` streams JSONL, the `or` discarded stdout
  whenever stderr was non-empty, while the surviving text was truncated to 300 characters *from the
  head*, which on a JSON stream is `thread.started` boilerplate. The real error was cut off exactly
  when it was needed. The diagnostic is now extracted tail-first (errors surface late in a stream),
  bounded, labelled by channel, and reported from both streams. The extractor is deliberately
  schema-agnostic: Codex's error-event shape is unconfirmed, so it does not key on a guessed event
  type.

## 1.1.4: opt-in managed worktrees, human-only Git authority, Codex guard parity

- Managed Git worktrees, opt-in per session. `/use-managed-worktree` assigns a worktree named from
  the session GUID and transparently redirects that session's writes into it, so concurrent Claude
  Code and Codex sessions stop sharing one checkout and one index. Activation itself changes
  nothing: professional mode works in the primary checkout by default, because redirecting where an
  operator's files land is their decision rather than a side effect of enabling workflow
  discipline. `/use-primary-checkout` returns an isolated session to the real checkout, and
  allocation failures leave the session in the primary checkout rather than half-entering
  isolation.
- Commander workers always allocate a worktree — isolation is the reason to run several at once,
  and they are not an interactive operator who can be surprised by it. They use the same lifecycle.
  Commander may create a reviewed local commit through its control plane, but cannot push;
  direct-session commit and push operations remain operator-only commands.
- Git mutations became human-only commands. `/commit`, `/commit-and-push`, `/push`,
  `/use-primary-checkout`, and `/return-to-managed-worktree` are recognized by the
  `UserPromptSubmit` hook, which mints a single-use, evidence-bound intent held server-side. The
  consuming skill re-observes the exact staged tree, parent OID, and local ref before acting, so
  the model issues the call but cannot supply the authority that makes it succeed. Prose, quoting,
  model-generated text, and programmatic invocation are not authority.
- Runtime diagnostics identify the installed state-manager bundle, workspace-manager bundle,
  workspace-manager CLI, and hook-intent helper so activation cannot silently validate a mixed
  source/cache installation.
- Codex parity for the guard surface: the PreToolUse matcher now covers Codex's native
  `apply_patch` and `exec_command`, which previously never reached the guard at all, and both are
  normalized to their Claude-native equivalents before the human-only config gate and the
  Commander-only transport gate — closing a route by which a Codex patch could rewrite
  `~/.claude/ironclaude-hooks-config.json`.
- Known limitation — subagent fencing is not yet at parity. Codex reports `thread_source`, so a
  Codex subagent is genuinely refused when it tries to consume human Git authority. A Claude
  subagent shares its root session's PPID file, so the provider-root check reads whichever subagent
  marker the transport supplies and fails closed if one appears; no such marker is known to be sent
  today. The practical ceiling is narrow, because an intent binds the exact tree and parent OID — a
  subagent could at most reproduce the commit the human just authorized — but this is a prepared
  seam, not proven isolation, and it is documented as such in `session-identity.ts`.
- Portability: replaced the GNU-only `realpath -m` (which silently returned the raw, unresolved
  path on BSD/macOS, leaving path-identity guards inert) with a portable canonicalizer, and fixed
  a `bash` 4-only parameter expansion that made the worktree escape check fail open on macOS's
  stock bash 3.2. `make test` now runs every hook suite, and CI exercises them on both shells.

## 1.1.3: Codex Commander parity, convergent Slack control, and deterministic reviews

This release closes the remaining behavioral gaps between Codex and Claude in Commander while
hardening the state transitions around directives, professional mode, and plan review. The focus is
operational correctness: provider-specific transport is isolated behind adapters, operator messages
receive one durable disposition, and workflow gates now reject ambiguous or duplicate state instead
of relying on prompt discipline.

### Fixed

- **Codex Brain now uses the same Commander control plane as Claude.** The Codex app-server adapter
  receives the orchestrator environment, exposes the required MCP tools, handles directory and hook
  trust prompts during startup, and preserves threaded Brain replies. Provider-specific details stay
  at the adapter boundary rather than leaking into directive or Slack logic.
- **Directive approval cards are posted and correlated reliably.** Slack message timestamps are
  persisted with directives, reactions are applied only after successful delivery, and approval or
  rejection is sent only to a registered, running worker with a live local or remote tmux session.
  Delivery failures remain visible instead of being reported as success.
- **Every operator message converges to one durable disposition.** Messages are either represented by
  a directive or by an immutable acknowledgement, enforced in both directions with SQLite triggers.
  Aging alerts and `/audit` consume that ledger, so acknowledged replies stop resurfacing as stale
  work and the audit output reports non-overlapping directive, acknowledged, and unresolved totals.
- **Acknowledgement persistence no longer commits a caller-owned transaction.** The helper now fails
  closed when its injected SQLite connection already has an active transaction, preventing unrelated
  writes from being committed or rolled back as a side effect.
- **Threaded Brain replies share one strict parser across daemon and MCP transports.** Valid
  `[reply-to:<Slack timestamp>]` markers are removed before delivery, malformed or marker-only inputs
  are dropped, and the acknowledgement is persisted before the Slack post and reaction. This keeps
  retries idempotent and prevents malformed control text from becoming visible chatter.
- **Codex token usage no longer renders as zero.** Commander reads the cumulative
  `tokenUsage.total` breakdown emitted by current Codex app-server events while retaining support for
  the older flat payload shape.
- **Professional-mode activation and deactivation verify the correct Codex session.** Source-less
  desktop roots are accepted only when all trusted identifiers agree and no subagent ancestry is
  present; malformed child identities remain fail-closed. Deactivation reports confirmed off,
  confirmed not-off, or unknown rather than treating an identity error as proof that the hook failed.
- **The Codex Desktop deactivation skill chip now works like the slash command.** The human-only hook
  recognizes Codex's exact standalone Markdown skill-link envelope and normalizes it to the existing
  command. Anchored negative cases continue to reject prose mentions, code spans, URLs, relative
  paths, wrong skills, case changes, and suffixes.
- **A plan lineage can receive at most one blind review.** Lineage identity is stored on sessions and
  review rows, duplicate blind reviews are rejected before audit mutation and by a database trigger,
  and migrations preserve historical review rows while selecting one canonical review. A
  `HAS-ISSUES` verdict now converges through a non-blind fix advisor and `advisor-remediated` record
  instead of launching another blind review.
- **Hook bootstrap and workflow transitions preserve review-lineage state.** Idempotent transitions do
  not advance lineage counters, while a real new design transition does. The bundled state-manager
  implementation and source tests cover fresh databases, migrations, reopen behavior, and raw-SQL
  constraint enforcement.
- **Slack pin saturation is handled deterministically.** Before pinning a new directive review at the
  channel limit, Commander removes the oldest pin and then adds the new one; it does not silently
  drop the requested pin.

### Changed

- **The shipped Commander configuration enables Codex and prefers it for worker and grader roles.**
  Claude remains an explicitly configured alternative; provider selection is data-driven rather
  than hard-coded into orchestration logic.
- **Workflow guidance now treats on-disk plans and state-manager records as the durable checkpoint.**
  Agents are directed to use an investigation loop when stage restrictions block required evidence,
  rather than handing read-only shell work back to the operator or inventing an extra checkpoint.
- **Review and execution guidance explicitly favors bounded sequential delegation for routine work.**
  The main context retains state transitions, review invocation, task sequencing, and runtime
  activation boundaries.

### Verification

- Added protocol-shaped regression coverage for Codex startup, token-usage events, Slack threading,
  directive acknowledgement, approval delivery, professional-mode identity, and skill-link
  normalization.
- Added state-manager migration and reopen tests for review-lineage uniqueness and transition
  idempotency, with source-to-bundle parity checks for the shipped JavaScript artifact.
- Exercised the deployed stable hook against the exact Codex Desktop payload in an isolated SQLite
  harness while proving the live professional-mode session was not mutated by agent-run tests.

## 1.1.2: suite isolation, a home-resolution seam, and the sonnet spawn default

A maintenance release with one behavioural change. The bulk of it closes a class of defect the
test suite had been carrying for months: tests that read and wrote the operator's real state
under `~/.claude` and `~/.ironclaude`. Fixing that surfaced a second problem — import-time frozen
path constants that no `HOME` redirect can reach — which is what the new `paths.py` seam exists
for. The behavioural change is that the Brain no longer carries an instruction steering it away
from `claude-sonnet` workers.

### Added

- **A `sys.addaudithook` tripwire that fails any test touching real operator state.** Occurrence
  five of "tests mutate the operator's live files" prompted a conftest-scope guard over
  `~/.claude`, `~/.ironclaude` and `~/.claude.json`. It audits the `open`, `os.remove`,
  `os.rename`, `os.mkdir`, `os.rmdir`, `os.symlink`, `os.link`, `shutil.copyfile` and
  `sqlite3.connect` events — so it catches reads that go through `open`, but being an in-process
  audit hook it does **not** see `os.stat`, `os.listdir`, `glob`, or anything a subprocess does.
  The containment is real for the paths that caused the five recorded incidents; it is not a total
  seal. Its exception
  derives from `BaseException`, not `Exception`, for a specific reason: the handlers that hid this
  bug through four prior occurrences are `except Exception`, so a `RuntimeError` tripwire would
  abort the operation and then die silently inside them. A trip ledger backs it for handlers that
  would swallow even that, capturing the offending production `file:line` machine-side rather than
  from scrolled terminal output. It deliberately carries **no exit-status backstop** for a trip
  occurring after final teardown: such a trip is recorded but does not fail the run. No late trip was
  ever observed, and an exit-status signal is the one this repo's convention discards ("grade the
  summary line, never the exit status").
- **`commander/src/ironclaude/paths.py`, the single home-resolution seam.** Four public accessors
  (`home`, `hooks_config`, `brain_sessions_dir`, `allowed_log_prefixes`) and two private helpers,
  function-only with **zero module-level path constants** — a constant is precisely what froze each
  site it replaces, so the module replacing them must not contain one. It imports only `os` and
  `pathlib`, making it a leaf any importer can take without a cycle. A broader set was sketched and
  deliberately not shipped, because some of the candidates were the wrong shape: `brain_cwd` and
  `grader_home` apply `expanduser` to a config-supplied *value*, so a zero-arg accessor cannot
  replace them, and shipping one would have frozen a signature the next migration must break.
- **A review checklist that code review actually loads.** `worker/rules/review-checklist.md` now
  carries three checks with numbered detection steps and explicit "DO NOT flag" suppressions:
  falsifiability evaluated at the plan's *end state*, provenance for factual claims, and
  widened-guard scope. Each exists because that defect shipped into a real diff here and was caught
  by a reviewer rather than the author. Two matching archetypes were added to the blind plan-review
  contract in `executing-plans`: a guard defused by a later step of the same plan, and a factual
  claim whose provenance is an agent summary rather than a file the author opened.
- **`test_rules_references_resolve.py`.** For every skill, each `rules/` reference must resolve to a
  file that exists when joined to that skill's own directory. A companion test asserts the scanner
  finds at least five references, so a broken extraction regex cannot make the resolution test pass
  by examining the empty set.
- **A guard proving the transitional review checklist can be deleted losslessly.** Two files are
  named `review-checklist.md` — a project-local `.claude/rules/` copy carrying 3 checks, and the
  canonical `worker/rules/` file carrying 11. The existing tests asserted three known names in each
  file independently, which says nothing about whether deleting the transitional copy would drop a
  check. The new guard asserts the transitional file's check set is a *subset* of canonical's — set
  equality would be wrong, since canonical carries more — with a non-vacuity assertion so a
  heading-format change cannot make the comparison silently true.
- **A model-tier step in the Subagent Prompt Construction Guide.** `executing-plans` specified
  `subagent_type` and `max_turns` and said nothing about model tier, so even correctly-delegated
  work picked its tier blind. The new step carries a tier table, is conditioned on `IC_ROLE=worker`
  (exported on all six spawn paths), and is explicitly INFORM-only.

### Fixed

- **The guard treated write-capable git subcommands as read-only.** `professional-mode-guard.sh`
  exempts "read-only git commands" at any non-executing stage, exiting 0 with the log line
  "read-only git command". The alternation admitted whole *subcommands*, so `git stash`,
  `git stash drop`, `git branch -D`, `git tag`, `git remote add` and `git reflog expire` all took
  that exemption. `git stash drop` discards stashed work permanently and `git reflog expire
  --expire=now` destroys the recovery log — both reachable through a branch that logs them as
  read-only. `is_readonly_git` now uses two greps: one keeping the always-read-only subcommands
  byte-exact (trailing `\b` included, so `git diff-index` and `git show-ref` still pass), and a
  second admitting only pinned read-only *forms* — `stash list`, `branch` with read-only flags,
  `remote`/`remote -v`, `tag`/`tag -l`, `reflog`/`reflog show` — each anchored so no argument can
  follow. Separating them leaves the proven expression untouched and isolates all new risk in the
  new one. Whitelist-only throughout: nothing enumerates destructive flags, so an unrecognised form
  falls through to blocked — a blocklist of `-d|-D|-m` would leave `--delete` as the loophole.
  Twenty assertions in both directions; the ten negatives were each observed failing beforehand,
  including `git -C /repo stash drop`, proving `-C` cannot reopen what the narrowing closed.
- **Both reviewing-stage block messages listed the pre-widening allowlist**, omitting `diff`, the
  `git -C` forms and env-prefixed pytest — so a reader could not distinguish "Bash is blocked here"
  from "this command is not on the list". Both now match `is_review_allowed`, guarded by a test that
  checks each single-word member by comma-token *equality*: `"diff" in text` is satisfied by
  "git diff/…" and `"ls"` by "ls-files", so a substring check on those could never fail.
- **`IC_OLLAMA_CONFIG_PATH` resolved two different ways.** `orchestrator_mcp.py` expanded a leading
  `~`; `paths.hooks_config()` returned the override verbatim. A single-quoted `~/custom.json`
  therefore worked through one consumer and stayed literal through the other. The accessor now
  expands, and the orchestrator resolves through it — so there is one resolver rather than two that
  merely agree. This also means the orchestrator now honours `IRONCLAUDE_HOME`, which is what the
  seam exists for.

- **The test suite planted a real 24-hour Fable blackout.** `test_brain_client.py:211` injects a
  mocked model-unavailable error, and `BrainClient`'s real error path ran unisolated — writing the
  operator's actual `~/.ironclaude/state/fable_unavailable.json`. `classify_reason` mapped it to
  `model_unavailable` with a 24h TTL, which then silently downgraded tier-up plan reviews from Fable
  to Opus *while Fable was working*. Observed 2026-07-17, 07-21 and 07-30; every occurrence was a
  test run, never an outage. Fixed with an autouse conftest fixture using `setattr` rather than
  `setenv`, because `_STATE_PATH` is read from the environment once at import time and a
  fixture-time `setenv` would run too late and silently do nothing.
- **The suite deleted rows from the operator's live database.** Four `TestRunMaintenance` tests ran
  `DELETE FROM audit_log` against the real `~/.claude/ironclaude.db` and unlinked real `~/.claude`
  session-id files, with exceptions swallowed either side so they passed regardless. Eight more read
  the operator's real hooks config, making their assertions depend on personal config values.
  Harvesting with the tripwire but no containment found **92 failures across 8 production sites**;
  static enumeration had found two of them.
- **Fable was quarantined for 24 hours when the rule says one.** The operator's rule has two
  clauses: honour an explicit "unavailable until \<time\>" when the provider supplies one, otherwise
  retry after an hour. Clause 2 was built for `unknown` but never for `model_unavailable`, which
  kept a flat 24h. `model_unavailable` is by definition a failure with no provider reset time, so
  clause 2 governs it. Four documented false positives sat in that gap; no case was found where the
  24h window was vindicated by a real outage. Two of the three tests guarding the window
  **could never have failed** — they asserted the computed window against the very constant that
  produces it, so they stayed green at 86400 and would have stayed green at 3600. The third encoded
  the window as a bare literal `86000`, invisible to the constant-name grep the first audit used.
  `usage_limit` is untouched: it still honours the parsed reset and still keeps Fable, because an
  account-wide limit throttles Opus equally.
- **Five skill references pointed at files that never existed.** Skills named
  `.claude/rules/<file>` for two files that ship at `<plugin_root>/rules/`. `code-review` Step 4.5
  named `.claude/rules/review-checklist.md` from the initial commit onward while
  `worker/rules/review-checklist.md` sat unread — so **every code review in this repo silently took
  Step 4.5's fallback branch**, because nothing checked. References are now anchored to each skill's
  own directory, the one location a skill is always given at load time; `../../rules/<file>`
  resolves in all three installs (repo, Claude cache, Codex cache), each confirmed.
- **The Brain was instructed away from sonnet workers.** `system_prompt.md:217` told the Brain to
  follow the grader's recommendation "when it recommends opus or fable" — placing a *sonnet*
  recommendation outside what must be followed — and then argued that context-compaction cost
  exceeds the sonnet/opus price difference. Both halves pushed upward, contradicting
  `workflow.md:692`, which already calls `claude-sonnet` the default choice for most implementation
  work. Recommendation-following is now symmetric in either direction. The auto-escalate-on-retry
  sentence stays, because that describes real behaviour rather than a bias.

### Changed

- **Guard matchers became testable predicates, and four allowlists widened.** `git -C <path> <sub>`
  was rejected while `writing-plans`' own execution invariants *require* it (the Bash cwd is
  `commander/`), so a subagent could not run its staging command and silently dropped `-C` — a plan
  instruction that did not execute as written. The review allowlist permitted bare `pytest` but this
  repo needs `.venv/bin/python -m pytest`; `diff` was on no allowlist despite being the documented
  way to verify a hook deploy; and the block message named the stage but not the permitted set, so a
  reader could not distinguish "Bash is blocked here" from "this command is not on the list". The
  matchers previously lived inline with no real coverage — only mirror functions that replicate the
  logic rather than exercise it, 39 assertions that could not fail against actual behaviour.
  Extraction landed first and **byte-exact**, keeping a trailing `\b`: `\b` matches before any
  non-word character where `([[:space:]]|$)` does not, so substituting would have silently blocked
  `make test-unit`, `git diff-index` and `git show-ref` while the mirror suite kept passing.
  `is_review_allowed` deliberately uses two greps — a single grep with the env-prefix group in front
  of the whole alternation would admit `FOO=1 sqlite3 db "UPDATE x"` and `FOO=1 find . -delete`,
  both of which then bypass the raw-anchored write checks.
- **Four frozen path constants now resolve through the seam.** `grader.py`, `shadow_grader.py`,
  `brain_client.py` and `orchestrator_mcp.py` each froze a home-derived path at import. Three bare
  `LocalGrader()` constructions therefore change behaviour, since `hooks_config()` now honours
  `IC_OLLAMA_CONFIG_PATH` for all callers — an override `orchestrator_mcp` had and the graders did
  not. Nothing in the repo sets that variable, and conftest deletes it autouse.
- **The grader recommends sonnet by default, and says so without gating.** All three grader menus
  now mark `claude-sonnet` the default. Each also carries an explicit line that tier choice alone
  never lowers the grade or affects approval — load-bearing, because the same grader holds
  approve/reject power and grades worker-type correctness, so a biased menu without that carve-out
  would have quietly become an enforcement gate.

### Known gaps

Recorded rather than fixed, so they are not mistaken for covered ground:

- **`notifications.py:318` still says "for the next 24h".** Its only callers pass `spawn-died`,
  which maps to `unknown` and was already on a 1h window, so that text was wrong before the Fable
  change and is untouched by it.
- **`.claude/rules/review-checklist.md` deliberately survives as a duplicate.** Deleting it takes
  effect immediately while the repointed skill text waits for the next skill load, and the gap
  between is a window where no checklist resolves at all. It goes once a relaunched session is
  observed loading the canonical file.
- **`code-review` Step 4.5 names the checklist by a relative path** while the workflow's Bash cwd is
  `commander/`, so a reviewer resolving it against its cwd may find nothing and fall back silently.
  Only the checklist's *content* effect is demonstrated; the path-resolution half is unproven.
- **`git stash show` is read-only and is nonetheless refused** by the narrowed allowlist below. It
  sits outside the approved admitted set, and fail-closed is the correct default for a guard whose
  failure mode is data loss. Admitting it is a one-line change if it proves to be friction.
- **The deployed guard remains revertible.** Hooks execute from `~/.claude/ironclaude-hooks/`, and a
  session running an older plugin version copies its own hooks over that shared directory — so a
  stale build can undo the fix below. Unfixed; it is the single thing standing between this
  narrowing and permanence.
- **The quoted-pipe false positive** — a `|` inside a quoted regex read as a shell pipe — remains,
  as do six mirror functions in the security suite and the guard's session-scoped stage sensitivity,
  which lets one task's review gate block a sibling's commands under parallel execution.
- **The sonnet spawn default is not yet demonstrated in behaviour.** No test here shows the Brain
  spawning more sonnet workers; that needs live dispatch. The prompt and grader changes require a
  **daemon restart** to take effect, and the `executing-plans` change is inert until the next skill
  load.
- **Editing all three grader prompts resets the `get_shadow_concordance_stats` baseline**, which
  `workflow.md:698` asks to be reviewed before such changes.
- **Every "Known gap" listed under 1.1.1 remains open**, including Codex's native `apply_patch`
  receiving no professional-mode enforcement and no CI running any shell hook suite.

## 1.1.1: Codex Brain tool gating, provider capability quarantine, and one plan review per lineage

Closes the parity defects that a blind full-tree recertification found in the 1.1.0 Codex
surface, and replaces the unbounded plan-review retry loop with a single review plus a tier-up
advisor.

### Added

- **Codex Brain tool gating.** `worker/hooks/codex-brain-gated-actions.sh` is a PreToolUse gate for the Codex Brain covering episodic-memory, orchestrator, and ollama MCP actions. MCP tool calls never reach codex's approval channel, so the hook is the only control on that path; the gate uses an arm-then-act pattern (first call denies with an explanation, an immediate repeat proceeds) so a destructive action cannot fire on a single unconsidered call. Ships with a self-contained test suite.
- **Provider capability quarantine.** A new `directive_capability_blocks` table plus a backoff and notification state machine records when a directive cannot run because a provider capability is unavailable, instead of retrying it blindly. Blocked capabilities surface in the heartbeat and in Slack status, and `ProviderState.unavailable_capabilities()` exposes them to the router. Recovery is reported once and the directive is released.
- **`research` and `ollama` MCP servers on the Codex Brain.** `CodexBrainClient._optional_mcp_overrides()` registers both through `-c mcp_servers.*` overrides, existence-guarded so a missing module is skipped rather than fatal. It deliberately does **not** clone `default_tools_approval_mode`: doing so would auto-approve ollama's `pull_model`/`remove_model`/`create_model`, which are precisely the actions the gate above exists to hold.
- **Codex reasoning effort.** The Codex Brain and the Codex grader now pass `-c model_reasoning_effort="<level>"`. Previously the effort level was exported as `CLAUDE_CODE_EFFORT_LEVEL`, a Claude-only environment variable with no effect on a `codex exec` process, so the operator's effort selection was silently dropped on both.
- **Provider-native professional-mode activation.** `activate-professional-mode` now binds its file operations to explicit `<READ_INSTRUCTION_FILE>` / `<WRITE_INSTRUCTION_FILE>` tokens resolved per client (Claude: `Read`/`Write`; Codex: a `node_repl` program and native `apply_patch`), adds a `verify-only` mode that checks an already-active surface without writing, and specifies an exact diagnostic contract that names every uncovered behavioral concept rather than reporting a count.
- **`get_professional_mode` returns the trusted client and session id.** The tool now answers `{professional_mode, client, session_id}`. Skills previously had to infer the active client from tool availability or environment variables to pick a provider-native branch; that inference is now unnecessary and explicitly forbidden.
- **Codex root-`AGENTS.md` bootstrap.** `professional-mode-guard.sh` accepts exactly one native `apply_patch` targeting root `AGENTS.md` while professional mode is `undecided`, so a Codex session can write its own instruction surface during activation. The payload is validated strictly — exact tool name, a `tool_input` whose only key is `command`, one Add/Update operation, no `Move to:`, no symlinked or externally-owned target.

### Fixed

- **Codex session metadata was truncated at 64KB.** `codex-sync.ts` read a fixed 64KB buffer to locate the first line of a rollout file. A session whose first JSON line exceeded that — reachable with a large instruction payload — produced a partial line, failed to parse, and the session was skipped from episodic-memory sync. It now reads until the first newline regardless of length.
- **The Codex Stop hook emitted a verdict shape Codex rejects.** The shared Stop hook returned Claude's `{decision, reason}` JSON. `get-back-to-work-claude.sh` is now a client-aware wrapper that translates the impl's verdict into the shape the active client accepts, with an explicit failure path when the verdict cannot be verified after continuation.
- **The Codex Brain gate watched MCP tool-name forms Codex may never emit.** The gate and the `hooks.json` matchers were keyed to a single prefix. Both now accept the plugin-prefixed and bare forms (`mcp__(plugin_ironclaude_)?episodic[-_]memory__*`, and the same for the state-manager PostToolUse matcher), so a gate cannot be bypassed by a prefix the manifest did not anticipate. **Which form Codex emits at runtime remains unverified** — see "Known gaps" below.
- **The ollama arms of the Codex Brain gate covered only the plugin prefix.** Registering the ollama server through `-c` overrides makes `pull_model`, `remove_model`, and `create_model` reachable under the bare `mcp__ollama__*` form, which the gate did not match — the destructive tools were reachable but ungated. Both forms are now covered.
- **Directive 6 told Codex workers to use a tool they do not have.** The behavioral directive instructed workers to search episodic memory via the `ironclaude:search-conversations` agent and forbade "raw MCP tools". The Codex plugin declares no `agents`, while `episodic-memory` *is* registered for it — so the instruction was inverted, forbidding the only path Codex has. The directive now diverges per client, naming the server and capability rather than a Claude-specific tool.

### Changed

- **A plan lineage now gets exactly one blind review.** `HAS-ISSUES` was previously a retry signal: verify findings, revise, dispatch a brand-new blind reviewer, repeat. Measured across 20 sessions, 142 tier-up reviews formed 42 chains averaging 3.38 rounds, and the 69% of chains needing more than one round consumed 91% of review spend — because the model that wrote the flawed plan also fixed it, made correlated mistakes, and failed the next review. `HAS-ISSUES` is now terminal. It dispatches a **mandatory one-tier-up advisor** that returns, per finding, `CONFIRMED` (the specific change), `REJECTED` (the evidence refuting it, so a non-defect is not "fixed"), or `REQUIRES-RETREAT` (the broken design premise a plan-level fix cannot repair). After the advisor-guided revision the author records the new `advisor-remediated` verdict, and `start_execution` accepts either `SOLID` at the current plan hash, or `HAS-ISSUES` at an earlier hash paired with `advisor-remediated` at the current one. A bare `advisor-remediated` with no preceding `HAS-ISSUES` is rejected. Verification is not removed, it moves: per-task code review still runs at every task boundary, so a defect the advisor misses surfaces there — later, cheaper, and against real code rather than a document.
- **`writing-plans` gained execution invariants and live-source grounding.** Plans must now state the invariants their commands satisfy, so a blind reviewer can check the commands against a declared standard: shell state does not persist between steps, `docs/` is gitignored, an empty result must be distinguishable from a failed command, and — the two that caused real defects — never author an `expected:` value you have not measured, and prove every verification can fail. `executing-plans` gained matching reviewer archetypes for a predicted-rather-than-measured `expected:` and a guard whose expected value the change itself moves.
- **The professional-mode guard no longer hard-fails on a missing session row.** It previously blocked every tool call when the session row was absent; it now resolves to `undecided`, which permits read-only tools, the mode-toggle skills, and the root instruction files. This is a deliberate relaxation to let a provider-native session bootstrap its own instruction surface, and it applies to both clients.

### Known gaps

Recorded from the blind recertification rather than fixed, so they are not mistaken for covered ground:

- **Codex's native `apply_patch` receives no professional-mode enforcement.** `apply_patch` appears in no PreToolUse matcher, and the guard's branches are gated on Claude tool names, so a Codex worker's writes bypass the config anti-tamper, the non-executing write block, the `allowed_files` whitelist, and the review-pending gate. Codex workers additionally spawn with `--dangerously-bypass-approvals-and-sandbox`, making hooks the only remaining control. A design and plan exist; the change is not in this release.
- **No CI runs any shell hook suite.** `test-guard-security.sh`, `test-codex-brain-gated-actions.sh`, and `test-stop-wrapper.sh` are manual-invocation only — no build file references them and there is no workflow directory — so "the suite passes" from `make test` says nothing about the hook layer where the Codex parity work lives.
- **Batch-spawned Codex workers get no advisor.** Single spawn sends the Codex advisor instruction; `spawn_workers` gates the advisor block on `client == "claude"`.
- **Reasoning effort reaches two of four Codex surfaces.** The Brain and grader pass it; Codex worker spawn and resume do not.
- **`make deploy-hooks` does not ship `hooks.json`.** It copies `worker/hooks/*.sh` only, so matcher changes reach a runtime through plugin reinstall, not that target.
- **Unverified without a live Codex:** which MCP tool-name prefix Codex actually emits, whether Codex loads `worker/hooks/hooks.json` at all (neither plugin manifest declares a `hooks` key), and the interaction between `default_tools_approval_mode` and PreToolUse hooks.

## 1.1.0: Codex worker/grader parity, the Codex Brain, and Slack provider controls

### Added

- **Codex as a worker and grader peer.** A provider router resolves the client and model per role, so `worker` and `grader` can run on OpenAI Codex (`gpt-5.6-luna` / `gpt-5.6-terra` / `gpt-5.6-sol`) by enabling `providers.clients.codex` and adding `"codex"` to a role's `clients`. See [CODEX_SETUP.md](CODEX_SETUP.md).
- **Codex Brain (workflow + memory).** `BRAIN_CLIENT=codex` runs the Brain as a persistent `codex app-server`: read-only sandbox, on-request approval, a git-command allowlist on exec approvals, the operator-selected model resolved through the codex tier map, and an `IC_ROLE=brain` marker scoped to its own spawn. It drives the full brainstorm → plan → execute workflow and episodic memory.
- **Slack `/provider`.** `/ironclaude provider` reports each role's preferred client, allowed clients, and the client the router will actually use; `/ironclaude provider <role> <client>` sets it. Changes the router would silently ignore (client absent from the role's `clients` list, or globally disabled) are rejected with a specific message instead of being persisted.
- **Session-artifact sweep.** Hourly maintenance prunes stale `idle`/`undecided` session rows and dead-PID session id files, guarded so a live or active session is never touched.

### Fixed

- **`research` and `ollama` MCP servers never started.** Both modules lacked a `__main__` entrypoint, so launching them as subprocesses defined a factory and exited 0 — the Brain silently had neither toolset. They now serve stdio.
- **A rejected `turn/start` could be reported as success.** `_await_response` consumed a shared cursor, so an out-of-order or concurrent waiter could skip past another's response and time out; `send_message` then returned success and skipped the retry. It now scans without mutating shared state.
- **`/provider` status could misreport routing.** It showed the stored sticky client even when the router ignores it (not in the role's `clients` list) and falls back to `preferred`; it now reports the effective client and names any ignored stored value.
- **Slack Brain narration** is threaded under the latest heartbeat instead of being dropped or looping back through the directive gate.

### Changed

- README documents the codex compatibility surface and carries an explicit as-of date on the project comparison; new [CODEX_SETUP.md](CODEX_SETUP.md) covers setup, model tiers, security posture, and limitations.
- The Commander test suite runs warning-clean (`filterwarnings = ["error"]`).

### Not in this release

Codex-Brain worker orchestration (the orchestrator MCP server is not wired for the Codex Brain) and
Brain tool-gating; Codex advisor wiring (Commander Codex workers spawn advisor-less); cross-provider
failover; Codex workers via batch `spawn_workers`; remote Codex over SSH.

## 1.0.27: Codex peer parity — Stop-hook fix, grader + worker adapters, selective reviews

### Fixed

- **Codex Stop-hook enforcement.** The shared `worker/hooks/get-back-to-work-claude.sh` emitted Claude's `{decision,reason}` JSON, which Codex's hook-output schema rejects ("invalid stop hook JSON output"). The fixed-path hook is now a client-aware wrapper over a byte-identical relocated `get-back-to-work-impl.sh`: non-codex `exec`s the impl (byte-identical stdout+exit); codex captures and translates the verdict to codex's shape (approve→`{}`, block→`{systemMessage}`). Client detection uses `PLUGIN_ROOT`/`CLAUDE_PLUGIN_ROOT` (the real Stop-hook env). Live-verified (codex Stop failure marker 2→0).

### Added

- **Codex grader client (router-wired).** `OrchestratorTools._call_grader` now resolves the grader client/model through the committed `ProviderRouter` and can dispatch to a Codex grader (`codex exec --json --output-schema`), returning the identical verdict contract + never-raise fallback. The `claude -p` grader path is byte-identical when the grader role resolves to claude; a legacy config with no `providers` block falls back to it.
- **Codex worker adapter (router-wired, local).** Worker spawn resolves the worker client/model through `ProviderRouter`; a Codex worker spawns interactively (`codex --dangerously-bypass-approvals-and-sandbox`), dismisses its trust dialog, activates professional mode via a process-subtree-walk of the SessionStart id-file (Codex keys it to an intermediate PID, not the tmux pane_pid), uses a client-aware ready marker, and gates the Claude-only `/advisor`+`/goal` slash commands. Resolved client+model persist on the `workers` row. Claude/ollama worker path byte-identical (full commander suite green).

### Changed

- **Selective LLM-judgment tier-up reviews.** `executing-plans` Step 1.5 now defaults to a same-tier blind plan review with an LLM blast-radius judgment that escalates to a one-tier-up review only when a change warrants it; a new Phase-3 tier-up adversarial review over the staged diff runs under the same judgment. In interactive sessions the commander surfaces the tier-up as a suggestion (AskUserQuestion) on both reviews. Per-task reviews unchanged. Skill-only; activates on relaunch.

## 1.0.26: plan-authoring fidelity

### Fixed

- **Plan-authoring fidelity.** A v1.1.0 blind review surfaced two fidelity defects no plan revision could repair: the human plan embedded six rounds of prior-review history (breaking blind review — MP-W02/MP-R07, confirmed empirically when a fresh reviewer reported receiving those rounds through the plan), and the plan pair failed a canonical-PlanJson/byte-parity contract. Investigation showed the operator requirement MP-W10 asks only for "semantically identical" human and machine plans; the v1.1 design had unilaterally escalated that to byte parity with a deterministic renderer, contradicting the already-shipped anti-flailing design's explicit "instruction-and-test contract, not a new renderer" decision. This release restores the v1.1 design's parity wording to the requirement, adds a live-source grounding step to `writing-plans` (every asserted file, symbol, signature, column, key, and command is verified against current source before it is written — the missing half of MP-W10 that produced two fabricated-symbol defects during v1.0.25 authoring), and prohibits plan artifacts from containing review history in both `writing-plans` and the `executing-plans` regeneration path. Instruction-and-test only: no renderer, no MCP tool, no schema change, no `dist` rebuild.

## 1.0.25: plan-review verdict calibration

Fixes a plan-review loop that could not terminate, and completes a working set whose staged subset would not have compiled.

### Fixed

- **Plan-review verdict calibration.** The tier-up plan review could not converge: one recorded session made 15 `submit_tier_up_review` calls including a run of 8 consecutive `HAS-ISSUES` without ever reaching `SOLID`, and reviewers routinely described a plan as "largely SOLID" while scoring it `HAS-ISSUES` anyway. Three defects in the reviewer prompt caused it — `SOLID` was never defined, a `Minor` severity tier had no stated effect on the verdict, and an open-ended "hidden risks, ambiguities, or edge cases" criterion licensed unbounded nitpicking. A materiality standard already existed but lived in the orchestrator's instructions where the reviewer never saw it, while `start_execution` gates on the verdict — so the standard was structurally unable to take effect. The reviewer prompt now carries an explicit MATERIAL decision test, a mechanical verdict rubric (`SOLID` = zero material findings; "no material defect found", not "nothing could be improved"), a latent-defect hunt naming five failure archetypes, and a capped `Observations` section where non-material findings land without touching the verdict. The orchestrator's `HAS-ISSUES` handling now applies the same test, making its pre-existing "repeat only while evidence identifies a material defect" rule coherent for the first time. This fix is prompt-only: it required no change to verdict values, the MCP schema, or the compiled bundle.

### Changed

- Completed the state-manager working set so the committed tree compiles and starts. `src/session-identity.ts` (value-imported by `index.ts`, plus three type importers), `src/db.ts` (`getLatestTierUpReview`, called from `write-tools.ts`), and `worker/.mcp.json` (`IRONCLAUDE_CLIENT=claude`, read at MCP module load and fatal when unset) now ship together with the already-tracked code that depends on them. Previously these sat outside the index while their consumers were staged.

## 1.0.24: workflow durability, Codex compatibility, and Commander hardening

Teaches the Worker that plan/design/task-state artifacts on disk are already durable, adds native direct-mode OpenAI Codex packaging, introduces scope-aware Boy Scout cleanup guidance, makes restricted-runner tests hermetic, and hardens Commander Slack interactions around account switching and operator-decision links.

### Added
- **New skill `ironclaude:workflow-durability`** teaches artifact durability under professional mode; names two anti-patterns (checkpoint anxiety, query offloading) and points at the correct workflow surfaces (`plan-interruption`, investigation PM loop).
- **Shared multi-line `_ic_is_antipattern_proposal` lexicon helper** added to `worker/hooks/hook-logger.sh`, consumed by both `get-back-to-work-claude.sh` and `subagent-drift-detector.sh`. The predicate iterates lines and returns true if any line matches a checkpoint or query-offload lexicon and is not meta-discussion (heading, blockquote, table row, or code fence).
- **New numbered behavioral rule "No Workflow Avoidance Under Stage/Context Restrictions"** added to the `activate-professional-mode` template (compact CLAUDE.md template + full behavioral.md template + concept detection table + canonical-texts library) so all plugin consumers pick it up on next activate. Repo dogfood copies (`.claude/rules/behavioral.md`, `worker/CLAUDE.md`) also updated.
- **"Common Rationalizations" rows** in `executing-plans`, `code-review`, and `brainstorming` SKILL.md files calling out the checkpoint-anxiety, review-banking, and query-offloading rationalizations respectively.
- **New tests** `worker/hooks/tests/{test-antipattern-lexicon,test-gbtw-antipattern-override,test-sad-antipattern}.sh` follow the existing `GBTW_TEST_MODE=1` source-and-call seam used by `test-gbtw-waiting.sh` / `test-gbtw-inflight.sh`.
- **Native Codex plugin manifest** at `worker/.codex-plugin/plugin.json` registers Worker skills and embeds plugin-relative launch configuration for the `episodic-memory` and `state-manager` MCP servers. Claude Code's existing direct-map `.mcp.json` remains unchanged.
- **Scope-aware Boy Scout Rule.** Every current and generated behavioral-instruction surface, including tracked root `AGENTS.md` for Codex repository guidance, rejects “pre-existing” as a reason for silence: clean up evidence-backed defects within authorized scope; otherwise describe the finding, evidence, proposed cleanup scope, and risk and ask permission. Blocked or unsafe findings are recorded rather than suppressed. A propagation guard covers every listed surface.

### Changed
- **`get-back-to-work-claude.sh` stop-hook**: a new `_gbtw_should_rearm_check` predicate is wired into **three** `FIRE_CONTINUATION=false` paths — the brainstorming case (previously an uncovered gap for AP-2 query-offloading, which happens exclusively in Bash-blocked design stages), the bg-tool suppression, and the holding/waiting suppression. An `AskUserQuestion` that IS a checkpoint or offload proposal now re-arms the continuation check rather than silently suppressing it. `CONTINUATION_PROMPT` extended with two new D/F examples plus a PROPOSING-vs-DESCRIBING guardrail (naming the anti-pattern in a design doc or skill discussion remains grade A).
- **`subagent-drift-detector.sh`** (previously a 46-line no-op that only cleaned up the subagent_sessions link) now reads the subagent's last assistant text from the transcript and blocks anti-pattern proposals via `block_stop` across five workflow stages (`executing`, `reviewing`, `brainstorming`, `plan_ready`, `final_plan_prep`). Existing `DELETE FROM subagent_sessions` cleanup and `db_audit_log` run BEFORE the block check so no rows leak on a blocked stop.
- **Codex-compatible background sync hook.** The SessionStart handler no longer sets Claude Code's `"async": true` metadata. Its existing `--background` CLI path already spawns a detached process and returns immediately, so behavior stays asynchronous without asking Codex to run an unsupported async hook.
- **Operator-wait links now require matching decision context.** Commander retains only fully delivered top-level Brain posts as link candidates and adds a permalink only when the candidate references the same worker extracted from the wait. Threaded chatter, partial deliveries, unrelated workers, and missing context produce a linkless alert rather than a misleading link.
- **Direct OpenAI Codex compatibility is explicit.** Direct Worker mode supports Claude Code and OpenAI Codex. Commander continues to orchestrate Claude Code sessions only; Codex-backed Commander workers are not included in v1.0.24.

### Fixed
- **Brainstorming-stage coverage gap for query offloading.** Prior wiring only touched the two downstream suppression blocks; the mainline `case *brainstorming*)` at the top of the case statement set `FIRE_CONTINUATION=false` unconditionally, so a subagent-based Stop event in brainstorming (the primary AP-2 surface — the stage where Bash is blocked) escaped the check entirely.
- **Codex imports no longer omit episodic-memory sync or the state-manager MCP.** Codex skips handlers marked `async`, and the Claude-only plugin manifest did not expose either MCP server to Codex. The new Codex manifest plus synchronous hook declaration removes both registration failures.
- **Slack `/login` handles noisy paste-back flows and silent waits.** Login-code parsing trims appended fragments, query strings, and URLs that cannot be part of a device code. The relay detects a CLI re-prompt after submission, emits throttled “still completing” feedback, surfaces a request for a fresh code, and logs the bounded hard timeout before killing and reaping the child process. Failed or incomplete sign-ins continue to preserve the previous account.
- **Restricted-runner tests are hermetic.** Four orphan-worker tests now exercise tmux-selection contracts through deterministic doubles instead of the operator's live server, and the wiki redirect test uses `socket.socketpair()` instead of binding a TCP listener. Coverage is preserved without environment-dependent skips.
- **Workflow-avoidance enforcement preserves multiline text and ordinary proposal grammar.** SubagentStop now classifies the complete final assistant text block, and the shared deterministic predicate recognizes common permission forms such as “Would you like me…”, “Do you want me…”, “Could we…”, and “May I…” while retaining line-scoped documentation exemptions.
- **Slack login no longer loses an immediate rejected-code re-prompt.** Submission state is published before stdin delivery under the relay synchronization boundary and rolled back on delivery failure, so a concurrent CLI re-prompt reliably asks the operator for a fresh code.
- **Hook regression harness fails on missing assertion paths.** The SubagentStop shell test rejects unexpected output and requires its exact expected pass count instead of allowing an unentered conditional to exit successfully.

## 1.0.23

Reorganizes how the Commander's autonomous "Brain" talks to you over Slack — so the channel is neither too noisy nor too quiet — and hardens the Fable-model fallback so a state file that can't be cleared can't strand the Brain on Opus.

**Background for readers new to Commander mode:** the Commander runs an autonomous "Brain" session that drives worker sessions and reports to you in a Slack channel; every ~15 minutes it also posts a **heartbeat** — a short status summary of what the workers are doing. To keep the channel readable, a prior release started silently discarding any Brain message that didn't reference a tracked work item (a "directive"). That removed cryptic status spam, but it also discarded the Brain's *direct answers to your own questions*, so asking the Brain something in Slack could get no visible response.

### Fixed
- **Direct answers to operator questions were silently dropped.** They are now delivered as **threaded replies** under the message you sent, and your message gets a ✅ reaction once it's answered, so you can see a reply landed without scanning the channel. The Brain marks a reply by prefixing it with `[reply-to:<slack-timestamp>]`, echoing the timestamp of the message it's answering — a convention documented in its system prompt. If the Brain omits the marker, the message falls through to the tactical-chatter path (below) instead of a drop path, so it still reaches you. Router in `poll_brain_responses` (`commander/src/ironclaude/main.py`); covered by `test_daemon.py` and `test_main_validate.py`.
- **A Fable-outage state file that couldn't be cleared stranded the Brain on Opus.** The Commander records "Fable is temporarily unavailable" in a small on-disk flag so it can fall back to Opus without a live API call. If *clearing* that flag failed — a disk-full or permission error on the file unlink, both observed in production — the flag stayed on disk with a future expiry and kept Fable suppressed for up to 24h even after it had recovered. `clear_fable_unavailable` (`commander/src/ironclaude/fable_availability.py`) is now fail-open: when it can't delete the file it truncates it to empty (which needs no disk allocation, so it survives the `ENOSPC` that defeats unlink and atomic-replace), and the read path already treats an empty flag as "Fable available." Covered by `test_fable_availability.py`.
- **The grader emitted an invalid model id for non-opus models, which was the *source* of the spurious Fable outages above.** The LLM grader spawns `claude -p --model <grader_model>[1m]`, but the `[1m]` 1M-context suffix is only valid for models that need it (opus); Fable 5 and Sonnet 5 have 1M natively and reject it, so a `fable`/`sonnet` grader produced `fable[1m]`/`sonnet[1m]` — the exact "issue with the selected model" error that tripped the Fable-availability flag. `_call_grader` (`commander/src/ironclaude/orchestrator_mcp.py`) now reuses `brain_client._model_needs_1m_beta` to append `[1m]` only for opus; Fable/Sonnet launch bare. The worker-spawn path was already correct. Covered by `test_orchestrator_mcp.py`.
- **The gemma4 shadow grader could fall into token-repetition loops.** The shadow grader's Ollama requests now pass `repeat_penalty: 1.3` alongside `temperature`/`num_ctx` (`commander/src/ironclaude/shadow_grader.py`), stopping gemma4 from looping on repeated tokens. Covered by `test_shadow_grader.py`.
- **Long Brain replies and tactical chatter never reached Slack, and the ✅ "answered" reaction fired even when the reply post failed.** The reply and heartbeat-threaded chatter paths in `poll_brain_responses` skipped the 39000-char chunking the directive-status path had, so a Brain message near Slack's ~40000-char per-message limit was rejected, caught, and retried indefinitely — never delivered, with a permanent stuck queue entry. Separately, the reply branch stamped the operator's message with a ✅ reaction unconditionally, ignoring `post_message`'s return value, so a reply that failed to post still marked the operator's question "answered." All three `*Brain:*` post branches (reply, chatter, directive-status) now route through one `_post_brain_message` helper (`commander/src/ironclaude/main.py`) that chunks under a shared `_BRAIN_POST_CHUNK = 39000` and uses **all-chunks-delivered** semantics — it returns the ts of the first successful chunk only when every chunk landed, and `None` if any chunk failed — so the reply-branch ✅ waits for full delivery instead of firing on the first chunk. Failed chunks still queue for retry via `SlackBot.post_message`'s existing except path. An empty-text guard short-circuits the helper on empty/whitespace-only input, so a bare `[reply-to:<ts>]` marker no longer produces a `*Brain:* ` ghost post; the reply branch logs at INFO when the helper returns `None` (empty body or chunk failure) so the drop is observable in `daemon.log`. Covered by `test_main_validate.py` and `test_enforcement.py`.

### Added
- **Tactical detail is threaded, not dumped.** Routine Brain chatter (progress notes, retries) is now threaded under the most recent heartbeat instead of the main channel — expand the thread to read it, ignore it otherwise. Directive-status updates still post to the main channel. The one message class still suppressed is the Brain echoing the Commander's own internal control markers back at it, which would otherwise form a feedback loop in the thread. The daemon records each heartbeat's Slack timestamp to thread chatter under it.
- **"Waiting on you" escalations link back to context.** When the daemon flags that it needs an operator decision, the Slack alert now includes a permalink to the relevant message, so you can jump straight to what needs attention. New `SlackBot.get_permalink` / `update_message` helpers (`commander/src/ironclaude/slack_interface.py`) fetch the permalink and edit the alert in place to append a `Link:` line; the Brain-authored `[BLOCKED]` escalation template (`commander/src/brain/rules/workflow.md`) gains a matching `Link:` field for the case where no Slack message exists yet. Covered by `test_main_operator_wait.py`.

### Changed
- **Retries keep their thread.** The Slack notification queue now stores each queued message together with its thread and re-posts a failed message back into that thread, so a transient Slack outage no longer leaks a threaded reply into the main channel (`commander/src/ironclaude/slack_interface.py`).

### Removed
- The obsolete "your last message wasn't posted" nudge the daemon used to send the Brain on a dropped message — nothing meaningful is dropped anymore, so it no longer applies.

Version 1.0.22 → 1.0.23 across `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` (kept in lockstep by `commander/tests/test_version_consistency.py`).

> **Deploy note:** the Brain learns the `[reply-to:]` convention from its system prompt, which is read once at daemon startup — restart the daemon to pick it up. Until then, replies fall through to heartbeat-threaded chatter (the safe path above), so nothing breaks in the interim.

## 1.0.22

Commander Slack-responsiveness overhaul (the brain never goes silent on a slow/unreachable Ollama), a Slack `/login` account-switch flow, a reason-aware Fable-availability gate, and several brain/heartbeat/guardrail directives.

### Added
- **Slack `/login` — switch the Anthropic account for the Brain + workers from Slack.** `login` (plain text or `/ironclaude login`) spawns `claude auth login`, relays the sign-in **URL** to Slack; the operator authorizes in a browser and pastes the code back with `login code <…>` (the live flow is device-code / paste-back — confirmed against `claude auth login --claudeai`). On a **verified** sign-in (`claude auth status` confirms the account) the daemon SIGHUP-restarts onto the new credential; an unverified, failed, or timed-out attempt never restarts and leaves the previous account intact. Implemented as a non-blocking, background-reader `AuthRelay` (`auth_relay.py`) wired into the daemon dispatch + poll loop (`main.py`), with `login`/`login code` parsing in `slack_interface.py`. Hardened (adversarial review): a per-session **generation guard** so a killed session's reader can't bleed a stale URL; a bounded **verify-retry** across ticks so a transient `claude auth status` flake doesn't produce a false "previous account intact" claim; the SIGHUP restart handler **aborts** an in-progress relay so its `claude auth login` child isn't orphaned; the success notice is **flushed** before `execvp`. Suites: `test_auth_relay.py`, plus login-wiring tests in `test_main_validate.py`.
- **Usage-limit alert.** When a Brain response signals `You've hit your limit` (or a worker rate-limit / session-limit), the daemon posts a **throttled** (per-reset-window cooldown) "⚠️ Usage limit hit — send `login` to switch accounts" prompt so the operator knows when to switch (`detect_account_limit` in `main.py`). Shares its signal set with the Fable-availability gate.

### Fixed
- **The brain→Slack path no longer blocks on a slow/unreachable Ollama.** A Brain-message validator on the brain→Slack egress path shared the grader's 600s timeout, and `OllamaClient` never failed over to the localhost fallback on a *read* timeout — so a hung endpoint silenced Slack for 10-minute stretches (repeated `Ollama timed out after 600s` with queued messages flushing the instant each timeout fired). Fixes:
  - `ollama_client.py`: a unified `_attempt` loop fixes the **failover-on-read-timeout** regression (a read timeout now tries the fallback, not just a connection error) and adds a **URL-keyed circuit breaker** (`_CircuitBreakerRegistry`, `threading.Lock`-guarded) — opens on the first transport failure, admits a single half-open prober, backs off exponentially (5s ×2, cap 300s), routes to the fallback while a URL is open, and fails open with `{"infrastructure_error": True}` only when both endpoints are down. Parse/format failures never trip the breaker. HTTP 4xx/5xx are treated as healthy (not an outage). Timeout messages now report connect-vs-read honestly.
  - `grader.py`: `LocalGrader` gains `timeout` and `keep_alive` constructor overrides; the message-path graders (`main.py`, `brain_client.py`) use a short **15s** timeout with bounded classifier input (`truncate_middle`) and a warm-model `keep_alive`, while the real grader path (`orchestrator_mcp.py`, 600s) is left untouched. Config is **hot-reloaded** on file-mtime change (no full daemon restart to pick up a URL edit).
  - `notifications.py`: the heartbeat surfaces a "validator degraded (Ollama endpoint(s) down)" marker (in both the normal and no-workers paths) when a breaker is open.
  - Existing per-site fail-open defaults are preserved via the existing `infrastructure_error` sentinel — zero call-site logic changes. New/expanded suites: `test_ollama_circuit_breaker.py`, `test_ollama_client.py`, `test_grader.py`, `test_main_validate.py`, `test_notifications.py`, plus an autouse breaker-reset fixture in `conftest.py`.
  - **Deploy:** commander code — restart the daemon once (loads the code *and* picks up the corrected Ollama URL the daemon had been caching stale).
- **d1374 — heartbeat no longer claims "Waiting on <operator>" with nothing to act on.** The heartbeat suppressed the false-positive "waiting" line when there is nothing pending on that audience.
- **d1364 — `restart_daemon` no longer thrashes in a restart loop.** Added a `directive_id` parameter that atomically marks the directive completed in the DB *before* the fork/SIGHUP, so a new Brain session doesn't see the directive as still `in_progress` and re-trigger `restart_daemon` (~every 55s).
- **d1389 — heartbeat no longer emits false-positive "WAITING ON ROBERT" lines.** A fast-path regex + an empty-`COMMANDER` guard suppress spurious "waiting" heartbeats, and the operator-wait TTL is lowered 1800→600s.
- **d1391 — guard hooks normalize a `make -C <dir>` invocation before the `make test*` allowlist check** (`brain-orchestrator-guard.sh`, `professional-mode-guard.sh`), so the allowlist matches regardless of the `-C` working-directory form.
- **d1398 — heartbeat shows an `*Active Workers:*` header** before the running-workers list when waits are present, so active workers no longer appear under the `WAITING ON` banner (`format_heartbeat` in `notifications.py`).

### Changed
- **d1362 — heartbeat two-section waiting display.** Waits are now split into `WAITING ON COMMANDER` / `WAITING ON <operator_name>` sections that always appear together when anything is holding on either audience.
- **d1384 — BrainClient default model switched from Opus to Sonnet** (native 1M context), for the brain orchestrator loop.
- **Reason-aware Fable-availability gate.** `fable_availability` now classifies *why* Fable is unavailable and sizes the recheck window to the cause instead of a blanket 24h blackout: a genuine model outage keeps the 24h window + downgrade to a working Opus, while a `brain-detected`/`spawn-died`/overload cause re-probes in ~1h. The Brain's detection sites (`brain_client.py`) forward the real error text so an outage classifies correctly, and `resolve_worker_type`/`resolve_advisor_model` keep Fable (rather than downgrading to an equally-throttled Opus) when the block is an account-wide usage limit (`classify_reason`/`parse_reset_time`/`fable_block_category`; suites `test_fable_availability.py`, `test_brain_client.py`). A live usage-limit *detector* that would exercise the keep-Fable path is deferred (needs a captured real usage-limit error string — see the `/login` usage-limit signal, which is the shared source). Hardened (adversarial review): an unambiguous model-outage anchor classifies `model_unavailable` even if the text incidentally mentions a usage word; a genuine outage that begins during a keep-Fable `usage_limit` window can re-mark to escalate the category (so `resolve_*` stops keeping Fable); and the orchestrator's "Fable recovered" clear no longer fires on mere tmux readiness while a `usage_limit` is still active.

## 1.0.21

A GBTW stop-hook fix so a worker legitimately waiting on a persistent `Monitor` is no longer falsely blocked with "TASKS STILL IN PROGRESS," plus a brain-notification fix for the turn-in-progress context when the token count is zero.

### Fixed
- **GBTW tasks-in-progress gate is now Monitor-aware.** The hard "TASKS STILL IN PROGRESS" gate in `get-back-to-work-claude.sh` only suppressed on completion-aware background Agent/Bash jobs (`_gbtw_extract_in_flight`), which is blind to a persistent `Monitor` — so a worker watching a long-running suite via a Monitor was blocked on every stop and thrashed against the block-throttle for the run's duration. Added `_gbtw_recent_waiting_tool` (detects `Monitor`/`ScheduleWakeup`/`TaskOutput`/`AskUserQuestion` in the last 3 assistant turns) and wired it into the gate (gate = classifier OR helper). Gate-only: the continuation check already handles Monitor and is left untouched; `run_in_background` is deliberately excluded from the helper (it is covered completion-aware by the classifier, so matching it here would leave the gate suppressed for up to 3 turns after a bg job finished). Unit-tested via the `GBTW_TEST_MODE` seam (`worker/hooks/tests/test-gbtw-waiting.sh` + 8 fixtures); the existing in-flight suite stays green. **Deploy:** `make deploy-hooks` to copy the hook into `~/.claude/ironclaude-hooks/`.
- **Brain notifications surface turn-in-progress context when the token count is zero.** `notifications.py`/`brain_client.py` previously suppressed the turn-in-progress context on a zero token count; it now surfaces correctly. Covered by `test_notifications.py` / `test_brain_client.py`.

### Changed
- Version is 1.0.21 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

## 1.0.20

A grader-transport overhaul (the inline grader now runs a tool-free `claude -p` subprocess with a hard timeout instead of scraping a persistent tmux pane) plus a new **Advisor Fallback** behavioral directive that makes "advisor unavailable" mean "spawn a top-tier subagent for the same review," never "skip it."

### Added
- **Advisor Fallback directive** (`.claude/rules/behavioral.md` #10, `commander/src/brain/rules/behavioral.md` #23, and the `activate-professional-mode` templates + concept-detection table). When the harness-injected `advisor` tool is unavailable, Claude must spawn a top-tier subagent (`Agent`, `model=fable` if Fable is available, else `model=opus`) to perform the same adversarial review rather than skipping it. Baked into the activation skill so it propagates to every IronClaude project (new projects at creation, existing projects on next `/activate-professional-mode`). Presence-guarded by `commander/tests/test_advisor_fallback_directive.py`.
- **`kill_worker` directive fast-path.** `kill_worker` takes an optional `directive_id`; when that directive's status is already `completed`, the kill is approved immediately and the inline grader is skipped (avoids a redundant Opus grade blocking cleanup of already-confirmed work). Opt-in and backward-compatible — absent `directive_id` the grade-or-warn behavior is unchanged, and a failed directive lookup falls through to grading. Note: this is a deliberate, opt-in relaxation of kill-grader enforcement — there is no worker↔directive linkage, so a caller can skip the kill-grade by pointing at any `completed` directive.

### Changed
- **Grader transport replaced: persistent tmux session → per-grade `claude -p` headless subprocess.** The inline grader (`OrchestratorTools._call_grader`) previously drove a persistent `ic-grader` tmux Claude session and read the verdict by **scraping the tmux pane** for a nonce delimiter + a strict single-line-JSON regex. On a large grading prompt the delimiter scrolled out of the 500-line capture window, so a valid verdict the grader produced in ~40s was never parsed → the poll loop ran the full `GRADER_TIMEOUT_SECONDS` and returned a false `F "timed out"`. Worse, `_call_grader` held `_grader_lock` and blocked the brain **synchronously** for up to 600s × a retry ≈ 1200s (~20 min) per grade, freezing the daemon. It now runs one `claude -p` subprocess per grade: the (possibly very large) grading prompt on **stdin**, the avatar system prompt via `--system-prompt-file`, the verdict as `--json-schema`-validated structured output parsed from the `--output-format json` event envelope (both the array and single-object envelope shapes are handled), and a **hard `subprocess.run(timeout=…)`** that kills a hung grade deterministically. `GRADER_TIMEOUT_SECONDS` lowered 600 → **120** (typical grade ~40s; now a real hard kill, not a false poll-timeout; prompt length is logged on timeout so systematic F-on-large-prompt stays diagnosable). Batch (multi-decision) spawn grading wraps its verdict array in an **object** schema (`{"verdicts": [...]}`) — the API rejects a top-level array `--json-schema` — and a lone uncertain decision is graded individually. Verified live end-to-end against the installed `claude` CLI (single-object and object-wrapped batch schemas both accepted).
- **Grader is billing-pinned to Claude Max.** The subprocess env strips every provider/billing routing var — `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` (the local Ollama worker path exports this), `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` — so grading can never be misrouted to Ollama/Bedrock/Vertex or metered API billing.

### Removed
- The entire tmux grader transport: `_ensure_grader`, `_spawn_grader`, `_is_grader_alive`, `_wait_for_grader_clear`, `_do_grader_send_and_poll`, the `GRADER_RESPONSE_<nonce>` scheme, `_grader_session`/`_grader_ready`, the `_GRADER_DEBUG` log globals, the `secrets` import, and `_deactivate_pm_via_sqlite` (its only runtime caller was the deleted spawn path). Obsolete transport tests removed; `test_orchestrator_debug_log.py` deleted.

### Behavior changes (intentional, reduced rigor)
- **The grader is starved of file/exec, agentic, and MCP tools.** The built-in `Task,Bash,Read,Edit,Write,NotebookEdit,Grep,Glob,WebFetch,WebSearch` **and** the agentic tools (`Skill,Workflow,ToolSearch,SendMessage,EnterWorktree,ExitWorktree,CronCreate,CronDelete,CronList,ScheduleWakeup,RemoteTrigger`) are disallowed, and `--strict-mcp-config` drops the plugin MCP servers so no `mcp__*` state-manager mutators are reachable — important because the `kill_worker` grading prompt embeds a worker-controlled log tail (treat it as untrusted). The grader still makes the one `StructuredOutput` verdict call. The three grader system prompts (spawn_worker / approve_plan / kill_worker) instruct it to evaluate **only from the inline evidence** in the prompt; it can no longer independently read a diff or run a command — it grades what it is given.
- **The shadow-concordance Slack report's "Opus tool calls" column is now always empty (M7).** `_last_grader_delta` is permanently `""` (there is no pane to scrape), so `_parse_tool_calls_from_delta` returns nothing for the primary grader.

**Deploy:** (1) **restart the daemon** — the grader-transport change is daemon code; (2) `claude plugin update` + `/reload-plugins` to pick up the `activate-professional-mode` skill change (the Advisor Fallback directive + version bump ship in the worker plugin); the `behavioral.md` directives ship with the repo and reach a project on its next `/activate-professional-mode`.

## 1.0.19

A config-controlled, **unbypassable** tier-up plan review (a higher-tier model reviews each plan with blind, fresh eyes before execution, and the commander cannot skip it or weaken the policy), a grader-reliability fix that stops `kill_worker` grading from timing out on large sessions, and a fix for a latent `create_plan` bug uncovered while building the above.

### Added
- **Config-controlled, unbypassable tier-up plan review.** A higher-tier model reviews each plan with fresh, blind eyes before execution; enforcement is governed by `tier_up_review_policy` in `~/.claude/ironclaude-hooks-config.json`:
  - **`enforced`** (default, fail-secure) — the commander **cannot** reach execution without a genuine tier-up review of the exact plan; the only way to not run it is for a **human** to change the policy.
  - **`commander-choice`** — the former default-yes-but-skippable behavior.
  - **`off`** — no review.
  - **How the gate is unbypassable:** the hard gate lives **inside the TypeScript state-manager MCP server** (MCP calls are not PreToolUse-hookable, so a hook alone would leave a direct-MCP bypass). `start_execution` refuses the `final_plan_prep → executing` transition, under `enforced`, unless a `tier_up_reviews` row matches `sha256(session.plan_json)` — mirroring the existing `mark_executing`/`review_grades` gate. A new `submit_tier_up_review` MCP tool records the review; the **server** computes the plan hash (the agent cannot forge the binding). New `tier_up_reviews` table (`db.ts`, mirrored in `worker/hooks/session-init.sh`). Missing/unreadable/invalid config resolves to `enforced`.
  - **Anti-tamper (agent-can't-weaken-its-own-guardrails):** there is deliberately **no MCP setter** for the policy, and `professional-mode-guard.sh` denies the agent writing `~/.claude/ironclaude-hooks-config.json` via `Edit`/`Write`/`MultiEdit` **and** common `Bash` write vectors (`>`, `>>`, `tee`, `sed -i`, `cp`/`mv`, `ln`/`rsync`, `truncate`, `dd`) in every stage, ahead of the `debug_allow_config_writes` bypass. Honest limitation: this defeats drift, "trivial"-rationalization, and the obvious tamper vectors — not a determined agent with arbitrary `Bash` in the executing stage (that needs OS-level sandboxing); it is the same rigor as every other IronClaude gate.
  - **The review itself** stays blind and report-only, one tier up (Sonnet→Opus, Opus→Fable-unless-unavailable→Opus via the on-disk `fable_unavailable.json` flag, Haiku→Sonnet, Fable→top-tier self-attest), dispatched via the Agent tool with only the plan/design paths and fixed criteria — never the writing rationale, conversation, prior findings, or a diff. On **Revise**, the operator's fixes are applied and `create_plan` is re-called to reload the revised plan (see Changed), then a brand-new blind reviewer runs against it.
- **Presence guards** `commander/tests/test_writing_plans_skill.py` (asserts the old Phase 4.5 is **absent** from writing-plans) and `commander/tests/test_executing_plans_skill.py` (asserts the relocated policy-gated step is present, blind, and policy-aware), so a future skill edit can't silently move or delete the gate.

### Fixed
- **`create_plan` no longer throws `SQLITE_LOCKED`.** `create_plan` wraps its mutations in a `db.transaction`, but `updateSession` (called inside it) ran a WAL checkpoint unconditionally — and a checkpoint on the same connection that holds an open write transaction raises `SQLITE_LOCKED`. This would have broken `create_plan` for every session on deploy; it was latent because no test had ever exercised `create_plan` through its transaction. `walCheckpoint` now returns early when `db.inTransaction` is true (a checkpoint is flush *timing* only — it defers to the next non-transactional write). The only `db.transaction` in the codebase is `create_plan`'s, so this is the complete blast radius.
- **`kill_worker` grader no longer times out on large sessions.** The inline grader was allowed to `Read` the worker's full session log to judge a `kill_worker` decision; on a long-running worker that log is large enough that the grader's investigation blew past `GRADER_TIMEOUT_SECONDS` (600s), failing the grade on infrastructure rather than merit. `kill_worker` now reads a **capped** log excerpt itself (`GRADER_LOG_MAX_LINES`, default 500, env-overridable) and passes it inline in the grader's user prompt, and the grader system prompt instructs it to evaluate log evidence from that excerpt rather than re-reading the file (it may still `Read`/`Bash` for other evidence — diffs, test output). The ssh-host / session / remote-log-dir resolution is hoisted above the grader call so the tail can be read for remote workers too. Covered by `commander/tests/test_kill_worker_log_cap.py`.

### Changed
- **The tier-up review relocated from `writing-plans` (former Phase 4.5) into `executing-plans` (new Step 1.5, after `create_plan`).** Running it after the plan is loaded means the server has the exact, stable plan string to hash, which eliminates file-reading and cross-representation hash mismatches and removes the soft, unenforceable Phase 4.5. `create_plan`'s valid source stages now include `final_plan_prep` so the Revise flow can re-call it to reload a revised plan (rebuilding `wave_tasks`) — otherwise a post-review revision would never reach execution.
- Version is 1.0.19 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

**Deploy (all three layers, or the change is partially inert):** (1) `make deploy-hooks` — the anti-tamper deny (`professional-mode-guard.sh`) and the schema mirror (`session-init.sh`) run from `~/.claude/ironclaude-hooks/`, not the repo; (2) `claude plugin update` + `/reload-plugins` — loads the rebuilt MCP server (`dist/index.js`, committed) and the updated skills; (3) restart the daemon so workers spawn against the rebuilt, gate-enforcing server.

## 1.0.18

Two reliability fixes to the operational plumbing behind the daemon: a daemon restart now always brings worker hooks current (no more forgotten `make deploy-hooks`), and the gemma4 shadow grader is no longer starved of context or silently failing to record its results.

### Fixed
- **Daemon auto-deploys worker hooks at startup.** Worker hooks run from the stable directory `~/.claude/ironclaude-hooks/` (deliberately, so the volatile repo working tree can't be read mid-edit by concurrent workers), and the only way to refresh that directory was the manual `make deploy-hooks` target — a step easy to forget, leaving daemons and worker sessions on stale hooks (exactly what happened on 2026-07-08: GBTW fixes committed, daemon "restarted", hooks unchanged on disk). `main()` now calls `_deploy_worker_hooks(repo_root)` in the same startup-sync family that already copies the brain CLAUDE.md/rules/grader files, mirroring the Makefile target: `worker/hooks/*.sh` → the stable dir (mandatory; a missing source dir or copy failure exits the daemon, matching the adjacent brain-file syncs) and → the latest plugin-cache hooks dir (best-effort; absent cache warns and continues). The latest cache version is chosen by numeric sort (`1.0.16` beats `1.0.9` — lexicographic would invert it, same reason the Makefile uses `sort -V`), and `shutil.copy2` preserves executable bits. Because this runs on every start, including SIGHUP `execvp` restarts, "restart the daemon" now implies current hooks. The `make deploy-hooks` target is retained for hook-only iteration without a restart. Covered by 6 tests (`commander/tests/test_main_validate.py::TestDeployWorkerHooks`): stable-dir copy + exec-bit preservation, numeric latest-version selection, non-version cache dirs ignored, cache-absent warn-and-continue, source-missing `SystemExit`, non-`.sh` files skipped — all with injected paths so no test touches the real home directory.
- **Shadow grader (gemma4) `num_ctx` truncation.** Both Ollama request payloads sent `options: {"temperature": 0.1}` only, so the raw model ran at Ollama's default 4096-token context while the grading conversation (grader system prompts + objective + up to 5×8000-char tool results) far exceeded it; Ollama silently drops the *oldest* context first — i.e. the grading instructions. Both payloads now carry `num_ctx` from a new `shadow_num_ctx` config key (default 32768, matching the proven `ollama_worker_num_ctx`; same root cause as the 2026-06-18 worker num_ctx finding, which the grader never received).
- **Shadow grader concordance rows were never persisted (cross-thread SQLite crash).** The shadow grader runs in a fire-and-forget daemon thread, but the concordance `INSERT` used the daemon's main-thread SQLite connection, so every event posted its Slack concordance report and then failed with `SQLite objects created in a thread can only be used in that same thread` (observed in production logs) — the `shadow_concordance` table never accumulated a single real row, starving every downstream measurement. `_fire_shadow_thread` now resolves the DB file path on the main thread (`PRAGMA database_list`) and the background thread opens its own short-lived connection for the write (WAL mode, set by `init_db`, makes the concurrent writer safe); failure logs an ERROR and drops the row without raising. The regression test runs the `INSERT` from a real `threading.Thread` against a real on-disk `init_db` database — an in-memory or same-thread test would mask exactly this bug class.
- **Shadow grader dropped the model's own analysis.** When gemma4 answered without a tool call, the tool loop broke without appending the assistant `content`, so the final verdict call graded from a transcript missing the model's reasoning. The analysis is now preserved in the transcript before the verdict request.
- **Shadow grader `grep_files` was a dead tool without ripgrep.** The tool shelled to `rg`; on hosts without ripgrep every call failed with `[Errno 2] No such file or directory: 'rg'` (observed in logs), wasting investigation rounds. The grep command is resolved once at import (`rg` if present, else a BSD-compatible `grep -r -m 20 -e` fallback).

### Added
- **`get_shadow_concordance_stats` MCP tool** (read-only): windowed aggregation over `shadow_concordance` (default 7 days, production rows only) returning concordance counts, disagreement-confidence breakdown, and opus-vs-shadow grade pairs — so the brain and operator can review shadow-grading trends without raw SQL. A one-line brain-rules addition (`workflow.md`) directs the brain to review this tool before proposing any grader prompt/model changes: tune against evidence, not impressions. Prompt/rubric/model tuning is deliberately deferred until clean concordance data accumulates (the d1278 assessment's own sequencing: persist → exercise → re-assess → tune), which the persistence fix above finally unblocks. Covered by 3 tests (real-thread persistence; windowed aggregation excluding `test_mode` and out-of-window rows; error dict on a dropped table) plus 4 shadow-grader tests (num_ctx on both payloads, analysis preserved, grep fallback shape).

### Changed
- Version bumped to 1.0.18 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

**Deploy:** daemon restart. As of this release the restart also auto-deploys the worker hooks, so no separate `make deploy-hooks` is needed; the brain-rules line takes effect on the next brain session.

## 1.0.17

Stop-hook false-positive fix: a worker legitimately waiting on a background subagent (dispatched via the `Agent`/Task tool with `run_in_background`) is no longer blocked by GBTW.

### Fixed
- **GBTW "tasks still in progress" gate now detects genuinely in-flight background jobs.** The suppression check in `worker/hooks/get-back-to-work-claude.sh` inspected only the last 3 assistant `requestId`s (`tail -3`); after a worker posted a few text-only "holding" turns while a background subagent was still running, the dispatch scrolled out of that window and the hook blocked — repeatedly, until "max blocks reached, likely false positives" fired. Confirmed against the real failing transcript (`7331628f-1c4d-4ee1-9c9d-347758be418d`). The `tail -3` window is replaced with true in-flight detection: over `tail -n 4000` of the transcript, collect launched background ids (Agent `toolUseResult.agentId`, Bash `Command running in background with ID: <id>`) and completed ids (delivered `<task-notification>` turns with `<status>` ∈ {completed,failed,killed,stopped}, plus the resumed plain-text `agentId: … subagent_tokens:` shape from `SendMessage`), and suppress the block iff at least one launched id has no matching completion. Diagnostics: every invocation logs the launched/completed/in-flight counts. Fail-safe: on missing jq or parse errors, falls through to today's block (never bypasses).
- Covered by 8 fixture-driven bash tests at `worker/hooks/tests/test-gbtw-inflight.sh` (subagent in-flight, subagent completed, in-flight past the old 3-turn window, resumed plain-text completion, background Bash in flight, two-dispatched-one-completed, all four terminal statuses, malformed JSONL line resilience). All 8 GREEN.
- **Fable availability caching and graceful fallback.** Fable is being removed for subscription users on 2026-07-07. Previously, only the always-on Brain had a Fable → Opus fallback (v1.0.15); on-demand Fable uses (spawn a `claude-fable` worker, or send `/advisor fable` to an Opus worker) would silently degrade or hard-fail. The daemon now caches Fable-unavailability in a small state file (`~/.ironclaude/state/fable_unavailable.json`, 24-hour TTL) and redirects at every source: `claude-fable` spawns become `claude-opus`; `/advisor fable` becomes `/advisor opus`; a `session died before ready` on a `claude-fable` spawn sets the flag, posts a one-time `⚠️ Fable unavailable` Slack alert (with the redirect target and the manual re-probe hint), and retries as `claude-opus`. Recovery is intrinsic: when the flag has expired and the next `claude-fable` spawn succeeds, the daemon posts `✅ Fable is back` to Slack. The Brain-side v1.0.15 fallback now also records the flag when it fires on a Fable model, so the worker/advisor paths pick it up automatically. Idempotent per detection episode — one alert per Fable outage, not one per operation. Fail-safe: any state-file error is treated as "Fable available (probe again)" so a corrupt file can never spuriously suppress Fable.
- Covered by 37 new tests: `commander/tests/test_fable_availability.py` (15, atomic write + TTL + transition semantics + resolve helpers, all hermetic via `monkeypatch`), `commander/tests/test_notifications.py::TestFableNotifications` (11, formatter content + mrkdwn escaping), and integration tests in `commander/tests/test_orchestrator_mcp.py` (5, worker-type redirect + advisor redirect + spawn retry + idempotency + recovery), `commander/tests/test_main_validate.py` (2, file-decision-path redirect + passthrough), and `commander/tests/test_brain_client.py::TestModelUnavailableFableTransition` (4, mark-on-fable-only + optional callback + transition-only-callback + no-callback default). All pass; no regressions.

### Changed
- Version bumped to 1.0.17 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

**Deploy:** `make deploy-hooks` (hooks run from `~/.claude/ironclaude-hooks`, not the repo) plus a daemon restart (the Fable-availability + Slack-alerts wiring lives in the commander daemon, not the hooks).

## 1.0.16

Slack observability fix: an intentionally-held worker no longer looks stuck — the operator sees exactly what is waiting on them, in every heartbeat.

### Fixed
- **"Waiting on operator" is now surfaced in every Slack heartbeat.** When the Brain held a worker for the operator's reply, its "Still holding. Awaiting …" status was silently discarded by the no-directive-ref message filter and the heartbeat showed only the raw `executing` stage — so a worker blocked on a human decision looked stuck indefinitely. The daemon now classifies a holding message at the drop boundary (via the grader, for every Brain message — so it works whether or not the message would pass the directive-ref gate) into an in-memory `operator_waits` signal, posts a one-time "⏳ Waiting on you: `<worker>` — <what it needs>" alert, and renders a "⏳ WAITING ON YOU" block in every heartbeat plus a tag on that worker's line. The state clears on the operator's next Slack message (self-healing: if the Brain is still holding it re-affirms next cycle), with a TTL backstop and a bounded map.

### Added
- Bounded Brain feedback on dropped non-waiting messages: the Brain now gets one `[FYI]` notice when a message is dropped for lacking a directive reference, so it stops blindly re-emitting. Guarded against reopening the `CONTEXT_REQUIRED` feedback loop (that the silent drop exists to break) by two mechanisms: it skips messages echoing our own `[CONTEXT REQUIRED]`/`[FYI]` markers, and throttles to at most 2 nudges per 10-minute window, reset on any successful post.

### Changed
- Version bumped to 1.0.16 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

## 1.0.15

Model-tiering release: right-size the whole system around capability-on-demand. The always-on brain runs on Sonnet and reaches Opus/Fable only when a task warrants it, backed by one-tier-up advisors — Fable-level capability on the hardest work without burning the top tier continuously.

### Added
- **Right-Size Every Subagent** behavioral directive — a new Core Principle telling every worker to delegate to subagents liberally and match the subagent model to task difficulty (Fable → Opus → Sonnet → Haiku; use the least capable model that will reliably succeed). Added to all synchronized directive copies: the worker template (`commander/src/ironclaude/templates/worker_claude_md.md`), `worker/CLAUDE.md`, the repo-root and `commander/` `CLAUDE.md`, and `.claude/rules/behavioral.md`. Harmonizes with the existing Subagent Discipline principle.
- **Sonnet default brain (user-overridable).** `brain_model` default changed `fable` → `sonnet`; still overridable via `BRAIN_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, or config. `default_opus_model` stays decoupled (`opus`) so `claude-opus` workers are unaffected. The brain handles routine orchestration itself and escalates to stronger workers on demand rather than running the top tier every cycle.
- **`claude-fable` as a first-class worker + brain escalation policy.** The brain's system prompt now lists `claude-fable` and an escalation policy: routine work → `claude-sonnet`; harder-than-it-can-decide → consult a `claude-opus` worker, then spawn `claude-opus`/`claude-fable` as advised (the brain delegates "fable-worthiness" to Opus rather than judging it itself). The spawn-time grader can recommend `claude-fable`, and an approved `claude-opus` spawn escalates to `claude-fable` only when the grader explicitly recommends it (no unconditional bump).
- **One-tier-up worker advisors.** Advisor model is now selected by worker type via `advisor.advisor_models` (`claude-sonnet` → `opus`, `claude-opus` → `fable`), with the scalar `advisor.advisor_model` as a fallback for unmapped types; `claude-fable` workers get no advisor (top tier). Applied in both the MCP and file-decision spawn paths.
- **Config-flagged `/goal` autonomous dispatch** (`dispatch.use_goal`, default off): when enabled, a spawned worker is given a `/goal` completion condition after professional-mode activation and advisor setup, for more autonomous, less-babysat execution.

### Fixed
- **Brain `fable[1m]` startup crash.** The brain unconditionally appended the `[1m]` suffix + `context-1m-2025-08-07` beta to its model string. Fable 5 and Sonnet 5 have a 1M context window natively and reject that beta, so `fable[1m]` errored on every cycle and wedged the brain. The suffix/beta is now applied only to models that need it to unlock 1M (opus); 1M-native models launch with the bare alias.
- **Message-shaped model-unavailable fallback.** The brain's fallback-to-opus fired only on raised exceptions, but the SDK returned model-unavailability as a normal assistant message (`"There's an issue with the selected model … may not have access"`), so the brain never recovered. It now also detects that message signature and falls back to opus.
- **GBTW Stop hook accepts a waiting state.** The get-back-to-work hook now treats a "holding for … / waiting for … / standing by / awaiting" final sentence as a legitimate stop — suppressing only the continuation nudge while keeping the code-review, memory-search, tasks-in-progress, and bypass gates intact — so it no longer fights `/goal`-driven or legitimately-waiting workers.
- **`scripts/bump-version.sh` now updates the correct files.** It previously targeted a nonexistent `plugins/ironclaude/.claude-plugin/plugin.json` and never touched `commander/pyproject.toml`, so it errored and left the version out of sync. It now updates `commander/pyproject.toml`, `worker/.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` (the three files `test_version_consistency.py` enforces), with version-format and file-existence validation.
- **Grader test drift.** Three `TestPersistentGrader` tests injected the grader response via `read_log_tail`, but the poll loop reads `capture_pane`; the mocks were repointed so the tests exercise the real poll path instead of timing out.

### Changed
- Version bumped to 1.0.15 across `pyproject.toml`, `plugin.json`, and `marketplace.json`.

## 1.0.14

### Added
- `ironclaude restart` CLI subcommand — sends SIGHUP to the daemon via PID file. Standalone `cli.py` with `pyproject.toml` console script entry point. Covered by unit tests and a real-signal integration test that spawns a subprocess with a SIGHUP handler
- `resume_session` MCP tool — resume any Claude Code conversation by session ID into a fresh managed tmux session
- `claude-fable` worker type — routing, grader prompts, and dispatch test
- `wiki_write` description frontmatter field for improved episodic memory search routing
- Ollama worker complexity gate, grader tier matrix, and batch spawn playbook injection fix
- Ollama-powered session summarization for `list_claude_sessions`
- Session adoption — `list_claude_sessions` + `adopt_session` MCP tools for taking over manually-started Claude Code sessions
- `.claude/rules/behavioral.md` — project-level behavioral directives for Claude Code rules system
- Research docs: Ollama MLX engine evaluation, Ollama worker 72h performance analysis, Obsidian Skills evaluation
- Design docs: rate-limit recovery + stuck-worker escalation, Ollama MLX engine, session sample truncation

### Fixed
- Grader feedback text corruption — replaced log-tail delta with `capture_pane`, fixed greedy feedback regex
- Brain timeout false positives during long MCP tool chains — added `_executing_tool` flag with 1800s hard safety net
- Reduced `list_claude_sessions` sample from 2000 to 200 chars to prevent 64KB+ output bloating Brain context
- Shadow grader Ollama read timeout increased 120→300s default to prevent gemma4 tool-call timeouts
- Shadow grader plan JSON fix (null → empty string for command field)

### Changed
- `brain_model` config set to `opus` (Fable currently unavailable)
- Version bumped to 1.0.14 across `pyproject.toml`, `plugin.json`, and `marketplace.json`

## 1.0.13

### Added
- gemma4 **shadow grader** — a local Ollama grader that runs alongside the primary grader and reports tool-calling concordance between the two, surfaced through a Slack command. Verdicts enforce a JSON grammar/schema (replacing the previous regex fallback chain) with argument type-safety and non-JSON robustness (code-fence stripping, stray `tools`-key removal, symmetric verdict instructions). New `shadow_grader.py` + Ollama tool-calling support in `ollama_client.py`; covered by `test_shadow_grader.py`, `test_ollama_client.py`, `test_slack_commands.py`, and orchestrator tests
- `worker/hooks/bash-readonly-guard.sh` — a sourceable predicate lib (`is_readonly_research_bash`, `_has_blocked_metachars`, `_find_has_write_action`) shared by `professional-mode-guard.sh`, with a DB-free 36-assertion unit test (`worker/hooks/tests/test-bash-readonly-guard.sh`)
- Manual-session wiki tooling — the brain-wiki operations were extracted from `OrchestratorTools` into a standalone `WikiTools` class (single source of truth for `write`/`delete`/`query`/`log`: page-name validation, derived `index.md` rebuild, changelog append, brain-repo commit), and surfaced through a new `ic-wiki` console script so they are usable from any shell, not just the daemon. No new MCP server — the daemon now delegates to `WikiTools` (−~270 lines, behaviour unchanged). `ic-wiki` resolves the brain directory the same way the daemon does (`IC_BRAIN_CWD`, then `~/.ironclaude/brain`). Covered by `test_wiki_tools.py` (7) and `test_wiki_cli.py` (1) against a git-initialised temporary brain
- `commander/tests/test_version_consistency.py` — asserts the version string is identical across `pyproject.toml`, `plugin.json`, and `marketplace.json`, so a missed source can't silently drift on a release
- macOS Prerequisites section in the README — Apple ships Bash 3.2 but the hooks need 4+ (symlink a Homebrew Bash into the default PATH), and `better-sqlite3` builds against `node@24`; documents the symptoms when either is wrong

### Fixed
- Read-only research Bash (`cat head tail wc grep rg find ls`) is now allowed in **all** non-executing workflow stages, not just brainstorming/idle. Previously `debugging` (and other stages) fell through to the catch-all write-block, and because this Claude Code build exposes no `Grep`/`Glob` tool, Bash is the only filesystem-enumeration mechanism — so an agent told to inspect logs while debugging had no way to do so. The allowlist is enforced by one hardened predicate that blocks command chaining, output redirection (`> <`), embedded newlines, and the complete GNU/BSD `find` write/exec action set (`-exec -execdir -delete -fls -fprint* -ok*`). All edits live inside the `WORKFLOW != executing` branch, so execution mode (plan-aligned Bash, per-task `allowed_files`, the `review_pending` gate) is unchanged. The same hardened check also closes pre-existing redirection bypasses on the `git add`, read-only-`git` (`git diff > out`), `make test`, and reviewing-stage allowlists
- Read-only-git exception in `professional-mode-guard.sh` now rejects shell chaining operators (`; & | \` $()`), closing a bypass where a write command could ride past the guard by appending a permitted `git diff`/`status`/`log`/etc. — mirrors the anti-chaining guard already on the `git add` exception
- `make deploy-hooks` no longer pins a plugin-cache version in the `Makefile`; it derives the latest installed version dir at runtime. A pinned version desynced from the installed cache on every release and silently skipped the plugin-cache hook copy
- Local grader strips leaked chat-template tokens (e.g. `<|tool_response>`) before `json.loads`, eliminating recurring `Non-JSON response` warnings from the Ollama-backed grader
- Slack App initialization retries on transient DNS failures during daemon startup, so a flaky resolver no longer aborts the boot sequence
- Restored a green commander test suite via two rounds of test-only fixes — no production-code changes: (1) 35 failures in the orchestrator cluster (`IC_BRAIN_CWD` environment leakage → autouse isolation fixture, stale Ollama exception mocks, the `kill_worker` dict-return / inline-grader cluster); (2) 8 further stale tests in the grader/enforcement/db modules that asserted superseded contracts (config moved into `LocalGrader`, the directive-ref pre-filter's `no_directive_ref` sentinel + silent-drop, and schema growth to 8 tables)

### Changed
- Version set to 1.0.13 across `pyproject.toml`, `marketplace.json`, and `plugin.json`. The `Makefile` is no longer a version source — it derives the installed plugin-cache version at runtime

## 1.0.12

### Added
- Ollama worker recommended settings + scaffolding — `spawn_worker(worker_type="ollama")` now auto-ensures a `num_ctx`-fixed model variant (`ic-<base>-<num_ctx>`, default 32768) via `/api/create` and launches against it, because Ollama's 4096 default truncated ~84% of Claude Code's first turn and left local-model workers non-functional. A principle-based worker playbook is injected via `--append-system-prompt` so small models (e.g. `gemma4:12b-it-qat`) follow the workflow rail instead of re-deriving it on every tool call. Optional `CLAUDE_CODE_MAX_OUTPUT_TOKENS` cap via `ollama_worker_max_output_tokens`. Validated end-to-end against a live Ollama (`OllamaClient.create_model`, `_ensure_ollama_ctx_variant`, `ollama_playbook.py`)
- `ollama_worker_num_ctx` config knob (default 32768) controls the worker variant's context window — 32k (~7.5 GB) fits under the 8 GB VRAM ceiling out of the box; larger context (e.g. 128k) requires raising `ollama_vram_block_threshold_gb` too. Surfaced in `config/ironclaude.json.example` and the README ("Running Ollama workers on Apple Silicon")

### Fixed
- `get-back-to-work` hook now detects `Monitor`/`TaskOutput`/`ScheduleWakeup` as waiting tools (not just `Bash` `run_in_background`), preventing false-positive interrupts when workers wait on long-running background tasks (d1171)
- Ollama VRAM spawn gate respects a config-overridable **8.0 GB ceiling on already-loaded Ollama VRAM** (`ollama_vram_block_threshold_gb`); raise it on larger-memory hosts. The 8 GB default suits Apple Silicon unified memory. (Corrects an earlier description of this gate as "host-aware / scales to half of total system memory", which was inaccurate — the daemon always populates the threshold from config defaults.) README updated: the threshold is a ceiling on loaded VRAM, not a minimum required

### Changed
- Version bumped to 1.0.12 across `pyproject.toml`, `Makefile` hook-cache path, `marketplace.json`, and `plugin.json`

## 1.0.11

### Added
- Heartbeat-level stuck detection — fires an `[ACTION REQUIRED]` escalation when a worker's `(stage, log_bytes)` fingerprint is unchanged across two consecutive heartbeats (~30 min), regardless of workflow stage. Closes a gap where `AskUserQuestion` menus raised during brainstorming were invisible to the prior PM-gate-only detector. Additive to the d1132 `check_stuck_workers` path (d1162)

### Fixed
- `review_pending` Flavor B deadlock — three root causes resolved: the `subagent-drift-detector` hook no longer writes `review_pending` to the DB (`submit_task` is now the sole authority for that flag), `plan-task-context` gained a dual-check auto-clear for submitted tasks in the current wave, and the state-manager `dist` was rebuilt to include `set_testing_theatre_checked` (d1157)

### Changed
- Version bumped to 1.0.11 across `pyproject.toml`, `Makefile` hook-cache path, `marketplace.json`, and `plugin.json`

## 1.0.10

### Added
- LLM-based semantic grading replacing regex/keyword judgment — `LocalGrader` extraction with 3 call sites migrated (d1078)
- Stuck-worker detection with two-step Slack escalation — stuck-alert and 30-minute thresholds, hash-dedup bypass for prompt-waiting workers, liveness deferral cap (d1074/d1076/d1081, d1132)
- Brain proactiveness enforcement (d1074/d1076/d1081)
- `clear_stale_review_pending` MCP tool plus automatic clearing of stale `review_pending` deadlocks — hook dual-checks for submitted tasks in the current wave before blocking edits (d1141)
- Directive-ref pre-filter for Brain Slack message validation — messages without `#N`/`dN` references are filtered before the LLM grader, restoring the `CONTEXT_REQUIRED` feedback loop for conversational Brain responses (d1133)
- Auto-resolve brain model to opus when the configured model is unavailable (d1106)
- Windows setup guide (`WINDOWS_SETUP.md`) and startup-lookback-enforcer hook
- Research Directive Completion section 6b in workflow rules (d1086)

### Fixed
- Strip professional-mode preamble from heartbeat worker summaries — heartbeat now shows actual task descriptions instead of repeated "Professional mode is active…" text (d1142)
- Mid-execution state corruption guards — `claim_task` guard, `state-activator` protection, and `mark_executing` consistency enforcement (d1083)
- Clear `review_pending` on wave transition in `get_next_tasks` — prevents stale flag after compaction (d1097)
- Ollama worker professional-mode integration — expanded git allowlist in `professional-mode-guard`, `ENABLE_STOP_REVIEW` check in the stop hook, and `deploy-hooks` copying all hooks (d1095)
- Inject `ANTHROPIC_BASE_URL` into Ollama worker spawn commands and fix the attribution header — fixes Claude Code unable to reach the Ollama endpoint (d1074, d1084)

### Changed
- README rewritten for post-v1.0.5 accuracy — state machine stages, hook system table, worker types, stuck detection, Ollama config, and configuration reference; adversarial-review accuracy fixes (d1084, d1099)
- Reverted `brain_model` to opus while Fable is unavailable (d1100, follow-up revert)
- Version bumped to 1.0.10 across `pyproject.toml`, `Makefile` hook-cache path, `marketplace.json`, and `plugin.json`

## 1.0.9

### Added
- Ollama model discovery with classification — `discover_models` MCP tool inventories local models by capability tier
- Paginated `get_directives` MCP tool with date filtering and text search
- PM timeout/retry parameters wired through `spawn_worker` pipeline (`pm_timeout`, `pm_max_retries`)
- Brain behavioral directive #19: never auto-switch workers to usage credits on rate limit
- Pin/decision-format enforcement for blocked-task escalations to operator
- Auto-unpin Brain escalation messages when tasks unblock
- Wiki page name validation — reject directive-number and date-stamped slugs
- Wiki server `/wiki` → `/wiki/` redirect for correct relative link resolution
- Security-guidance plugin integration for workers (Stage 1+3 active, Stage 2 disabled)
- `conftest.py` with `os.kill` guard for safe test isolation

### Fixed
- SessionStart hook race condition on Windows — pre-flight `COUNT(*)` check in `episodic-memory-sync.sh` exits cleanly when session row doesn't exist yet
- Heartbeat shows all alive workers using tmux as ground truth instead of DB status
- Immediate Brain notification on directive confirmation, removed 5-minute delay from reminders
- SQLite lock rollback in push sweep with confirmed-directive reminder
- Model config: switch opus defaults to short alias, remove `[1m]` suffix (Max plan auto-enables 1M context)

### Changed
- Default model updated to `claude-opus-4` (short alias) across worker commands
- Brain model uses `[1m]` suffix for explicit 1M context window opt-in

## 1.0.8

### Added
- Wiki knowledge layer implementing [Karpathy's LLM wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — Brain-maintained markdown pages synthesized from episodic memory
- Wiki MCP tools: `wiki_write`, `wiki_delete`, `wiki_query`, `wiki_log`
- Dual-flag gate: gated actions now require both episodic memory search AND wiki query
- Brain rules for wiki workflows: post-directive ingest, periodic sweeps, search-triggered synthesis
- Wiki auto-commit: `wiki_write` and `wiki_delete` stage and commit after each mutation
- Wiki synthesis enforcer hook for Brain wiki compliance
- Task ledger persistence to `wiki/tasks.md` — survives daemon restarts
- `IC_ROLE` environment variable: workers get `IC_ROLE=worker` at spawn and bypass brain-orchestrator-guard restrictions
- Audit log entries for daemon-side professional mode deactivation writes

### Fixed
- Directive staleness prevention — mandatory status updates, text confirmation detection, sweep cross-referencing
- Pass missing `effort` argument to `make_opus_command()` calls — prevents TypeError when spawning opus workers
- Professional mode guard: allow `.claude/rules/` writes and `mkdir` during undecided state — unblocks first-time activation bootstrap
- Detect `AskUserQuestion` menus in `send_to_worker` — navigate to free-text option instead of accidentally selecting default menu item
- Background job detection in get-back-to-work hook to reduce false positives
- Notification heartbeat messages now show actual task description instead of repeated preamble text

### Changed
- Default model switched to `opus` (short alias) for 1M context window
- Restored illustrative override examples in README model config section
- Removed internal workflow artifacts (docs/) from repository tracking
