# Boy Scout Rule and Hermetic Test Cleanup Design

> **Created:** 2026-07-18
> **Status:** Design Complete
> **Scope mode:** selective (approved core only; no Commander Codex implementation)

## Summary

IronClaude will adopt the Boy Scout Rule as a durable behavioral directive: never use a defect's pre-existing or adjacent status as a reason to ignore it. When cleanup is evidence-backed, safe, relevant, and within the user's authorized task scope, complete it through the active workflow and verify it. When cleanup would materially expand scope, alter behavior, require destructive action, affect external systems, or otherwise require new authority, present the evidence and ask permission instead of silently leaving the defect behind.

The same effort will clean up five test failures exposed by the restricted Codex runner. Four orphan-worker tests connect to the operator's default tmux server, and one wiki redirect test binds a real TCP listener. All five pass outside the sandbox and fail inside it. The tests will become hermetic without changing production behavior or weakening coverage into environment-dependent skips.

Release documentation will explicitly state the current compatibility boundary: the IronClaude Worker supports direct use with Claude Code and OpenAI Codex, while Commander continues to orchestrate Claude Code sessions only. Codex-backed Commander workers are not part of this effort.

## Root-Cause Evidence

### Orphan-worker tests

`commander/tests/test_kill_orphan_workers.py` creates fixed-name sessions through bare `tmux` commands. `TmuxManager` also invokes bare `tmux`, so the tests use the operator's default server socket at `/private/tmp/tmux-502/default`. In the restricted runner, every session creation fails with `Operation not permitted`. Outside the sandbox, the same tests pass.

The production function under test, `_kill_orphan_workers`, owns selection logic only: list `ic-*` sessions, preserve `ic-brain`, preserve registry-backed sessions, and call `kill_session` for the remainder. Existing `TmuxManager` tests cover command construction separately. Real tmux adds privilege and operator-state coupling without adding meaningful coverage to these four cases.

### Wiki redirect test

`commander/tests/test_wiki_server.py::test_wiki_prefix_no_slash_redirects` constructs `ThreadingHTTPServer(("127.0.0.1", 0), ...)`. The restricted runner rejects `socket.bind()` with `PermissionError: [Errno 1] Operation not permitted`; the test passes outside the sandbox.

A confirmed experiment sent the same raw `GET /wiki` request through `socket.socketpair()` and exercised `WikiHandler` successfully, producing `HTTP/1.0 301 Moved Permanently` and `Location: /wiki/`. The handler contract can therefore be tested without a network listener.

## Architecture

The Boy Scout Rule becomes one canonical behavioral concept propagated across current repositories, generated worker repositories, professional-mode activation, and the autonomous Brain. Its decision boundary is:

```text
Discover an evidence-backed defect
        |
        +-- Cleanup is within authorized task scope
        |       |
        |       +-- Clean it up through the active workflow and verify it
        |
        +-- Cleanup materially expands scope or needs new authority
                |
                +-- Present evidence and ask permission to clean it up
```

The rule does not authorize speculative refactoring, unrelated features, destructive cleanup, or external mutations. It complements No Premature Optimization: only observed, evidence-backed defects trigger the rule. It also complements systematic debugging: suspicion alone is insufficient evidence.

Test cleanup uses dependency boundaries already present in production. `_kill_orphan_workers` receives `TmuxManager` and `WorkerRegistry`, so deterministic doubles can verify its decisions without a live tmux server. `WikiHandler` accepts a connected request socket, so a Unix socket pair can exercise the real HTTP byte stream without binding or listening.

## Components

### Behavioral surfaces

- `CLAUDE.md`, `worker/CLAUDE.md`, and `commander/CLAUDE.md`: add the full Boy Scout directive to every tracked project instruction file.
- `.claude/rules/behavioral.md`: add the canonical repository-level directive.
- `commander/src/ironclaude/templates/worker_claude_md.md`: propagate the directive to Commander-created worker repositories.
- `worker/skills/activate-professional-mode/SKILL.md`: update the compact template, full template, semantic concept-detection table, canonical append block, and concept-count references from 10 to 11.
- `commander/src/brain/rules/behavioral.md`: add a Brain-specific form that cleans authorized findings and escalates scope expansion. This provides an authorization boundary absent from the existing broader “Fix Bugs Immediately” directive.
- `commander/src/brain/orchestrator_claude.md`: correct its stale behavioral-directive description and include the new expectation in generated Brain instructions.

### Propagation guards

- Add a focused presence test modeled on `test_advisor_fallback_directive.py`.
- Require every instruction and generation surface to contain the Boy Scout concept.
- Require both semantic branches: clean up within authorized scope and ask permission when cleanup expands scope.
- Require every activation propagation vector and the updated 11-concept count strings.
- Update `test_worker_claude_md_template.py` so the worker template cannot silently drop the directive.

### Hermetic tests

- `commander/tests/test_kill_orphan_workers.py`: replace subprocess-created tmux sessions with deterministic manager and registry doubles while preserving four behaviors: kill unregistered sessions, preserve registered sessions, preserve `ic-brain`, and ignore non-`ic-` sessions.
- `commander/tests/test_wiki_server.py`: replace the TCP listener and `HTTPConnection` with a raw request over `socket.socketpair()`. Preserve assertions for status 301 and `Location: /wiki/`, and close both endpoints deterministically.
- Do not add capability skips, sandbox detection, or production-only test hooks.

### Release documentation

- `README.md`: use “OpenAI Codex” on first mention and add an explicit compatibility note that direct Worker mode supports Claude Code and OpenAI Codex, while Commander remains Claude Code-only.
- `CHANGELOG.md`: document the Boy Scout directive, hermetic test cleanup, and the same direct-mode compatibility boundary in v1.0.24.
- Amend all resulting changes into the existing unpushed v1.0.24 commit. Do not push.

## Data Flow

Current repositories load the nearest applicable `CLAUDE.md` plus `.claude/rules/behavioral.md`; both will carry compatible Boy Scout instructions. New repositories activated through professional mode receive a compact `CLAUDE.md` plus the full behavioral-rules file. Existing repositories undergo semantic concept detection across both files; if the concept is absent, activation appends its canonical form to `.claude/rules/behavioral.md` without overwriting customized `CLAUDE.md` content. Commander-created workers receive the same rule from `worker_claude_md.md`. Brain receives its role-appropriate version from the source-controlled Brain instructions.

The orphan-worker tests feed session names and registered-worker records into `_kill_orphan_workers`, then inspect `kill_session` calls. No subprocess or tmux socket participates. The wiki test writes a raw HTTP request to one end of a socket pair, gives the connected endpoint to `WikiHandler`, and parses the returned HTTP headers. No TCP port is opened.

## Error Handling and Safety

Claude must verify that a finding is a defect before invoking the Boy Scout Rule. When cleanup stays inside authorized scope, it follows the active brainstorm, plan, execute, review, and verification requirements. When cleanup expands scope or requires authority, Claude reports what it found, the evidence, the cleanup scope and risk, and asks permission. If cleanup is blocked or unsafe, the finding is recorded rather than suppressed.

Tests will not translate infrastructure denial into skips. If deterministic tmux doubles cannot preserve a tested behavior, or if the socket-pair harness does not exercise the HTTP handler contract on a supported platform, execution returns to design instead of adding a workaround.

Git operations remain scoped. The unrelated untracked `AGENTS.md` is preserved. Only approved files and workflow artifacts are staged. The v1.0.24 commit is amended locally, and no push is performed.

## Testing Strategy

- Run the Boy Scout presence test and worker-template tests, verifying every propagation surface and both authorization branches.
- Run all four orphan-worker cases and the wiki redirect test through the restricted absolute Python executable that reproduced the failures.
- Verify the affected tests do not invoke live tmux or bind a TCP listener.
- Run the complete Commander suite in the restricted environment. Baseline: `1961 passed, 1 skipped, 5 failed`. The five failures become passes and the propagation guard adds three tests, so the target is `1969 passed, 1 skipped, 0 failed`.
- Run all 11 hook test scripts.
- Run version-consistency tests and Codex plugin validation.
- Inspect staged diff and amended commit, confirm `AGENTS.md` remains untracked, confirm `main` is exactly one commit ahead of `origin/main`, and confirm no push occurred.

## Implementation Notes

- No production runtime code changes are expected.
- No Commander Codex worker support is included. Commander continues to launch and manage Claude Code sessions only.
- Do not weaken tests into environment-dependent skips.
- Do not treat the Boy Scout Rule as implicit permission for unrelated work.
- Preserve normal human-facing English in README, changelog, design, plan, and commit messages.
