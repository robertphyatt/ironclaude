# v1.2.0 Security Fixes Remediation Design

> **Created:** 2026-10-18
> **Status:** Design Complete (revised 2026-10-18 after the shell-expansion analysis)

Loop: full — guard and security code (both Brain git checks, the Brain hook, the shared read-only predicate, the Codex Brain start check).

## Summary

The staged security fixes (`docs/plans/2026-10-18-v120-security-fixes-design.md`, Tasks 1-4) left two holes, both confirmed with primary evidence: the Codex Brain start check counts servers Codex reports as disabled, so every Codex Brain start fails; and shell expansion rewrites a command after every IronClaude check has approved it.

Every command check in IronClaude reads the command string before the shell expands it. The checks block `; & | \` < > $(` and newlines, but never braces `{a,b}`, globs `* ? [` or `~`. This design closes every expansion case that lets an approved command write, delete or change something, and records the rest.

Operator decisions, 2026-10-18:
- "Refuse all braces (Recommended)", then, after asking to "Deeply understand how this can cause problems in every case", "Targeted rule now, tool next (Recommended)". The targeted rule replaces the all-braces rule: only braces the shell expands are refused, so `@{u}` and `HEAD@{1}` keep working.
- "Yes, plan it (Recommended)".
- Brain `git add`/`git commit`: the operator asked the agent to decide. Evidence: the Claude Brain's transcripts (`~/.claude/projects/-Users-roberthyatt--ironclaude-brain`) hold about 270 Bash git calls, including 12 `git -C <worktree> add` and 2 `git -C <repo> commit -m`. Decision: keep `add` and `commit` (the 2026-03-09 decision stands); revisit in the git-tool loop.

## Requirements

1. **1a-R — disabled servers are not extras.** Codex `mcpServerStatus/list` lists every configured server, including those the trim disables, as `{"name": ..., "runtimeStatus": "disabled", "tools": {}}` (codex-cli 0.163.0-alpha.10.2; raw response `/private/tmp/claude-502/taf3/b1-inventory-raw.json`, probe `/private/tmp/claude-502/taf3/b1-probe.sh`). `_verify_orchestrator_mcp` counts a server outside `_TOOL_SERVERS` as extra unless its `runtimeStatus` is exactly `"disabled"`. A missing or any other status still counts (fail closed).
2. **1b-R — no expanding braces.** A brace group the shell expands contains `,` or `..` (`{a,b}`, `{1..3}`); `@{u}`, `HEAD@{1}`, `stash@{0}` and `find -exec … {}` do not expand. The rule refuses any command matching `\{[^{}]*(,|\.\.)[^{}]*\}`, checked on the raw command before any `-C` normalization, in:
   - both Python Brain git checks (`brain_client.py` Bash branch, `codex_brain_client._git_command_allowed`);
   - `commander/hooks/brain-orchestrator-guard.sh`, right after its chaining check, so it covers Brain git and Codex Brain research commands, and `git -C {.,diff,--output=/tmp/x} status` cannot lose its braces to normalization;
   - `worker/hooks/bash-readonly-guard.sh` `_has_blocked_metachars`, so every worker read-only stage, the review allowlist and the Codex Brain research path refuse `find /tmp {-delete,}`.
3. **1c-R — no globs in Brain git.** A glob hands a file named like an option to git (`git diff *` with a file named `--output=x` in the directory). Brain git commands containing `*`, `?` or `[` are refused, in both Python checks and in the hook's git path (checked on the raw command saved before `-C` normalization).

## Costs accepted

- Expanding braces are refused even when quoted: `rg 'a{2,3}'` in a worker read-only stage or Codex Brain research, and `git log --format='{a,b}'`.
- Brain git loses quoted glob pathspecs (`git log -- '*.py'`), `[` in `--grep`, and `*`, `?` or `[` inside a commit message (`git commit -m "fix?"`).

## Not changed, with reasons

- **Bare `$` and `~`.** `cat $HOME/x` and `cat ~/x` only read a path the caller can already name directly, so blocking them adds nothing, and `$` would break `rg 'foo$'`. Both Python git checks already refuse `$`.
- **The Claude Brain's Python check.** The 2026-10-10 parity design records that `_tool_guard_logic` never runs under `permission_mode="bypassPermissions"` (`brain_client.py:825`); `brain-orchestrator-guard.sh` is then the only gate on Claude Brain git. The Python check is kept identical to the Codex one for parity; not probed live here.

## Next loop (backlog)

- An argument-list `run_git(repo_path, argv)` orchestrator tool for both Brains, with git denied in Bash/exec, `make test*` and Codex research reads kept. Investigation found no blocking regression; adaptations: server-side `~` expansion of `repo_path`, output cap, own audit line, own `push` denial, prompt updates (`system_prompt.md:199-208`, `orchestrator_claude.md:55`, `workflow.md`, `retro.md`), about five test files.
- X1: git runs programs named in repo config (textconv, external diff, pager, fsmonitor, hooksPath) on plain `git diff`/`log`/`show`/`blame`; harden with `-c` overrides, `--no-ext-diff --no-textconv` and config env.
- Worker glob case: `find . -name *` with a file named `-delete` in the directory.
- `rg --pre <program>` runs a program and passes the research predicate.
- Delete or wire in the dead Claude Python check; `behavioral.md:54` tells the Brain to run `jq`, which every guard blocks.
- Pre-existing: abbreviated `git commit --amen`; `git branch --set-upstream-to`, `--unset-upstream`, `--edit-description`; the hook's branch arm admits `-q newname` and `--delete x`; no test for the quarantine cap with an unparsable event time.

## Architecture

- **Inventory.** `extra = sorted(name for name, server in inventory.items() if name not in _TOOL_SERVERS and server.get("runtimeStatus") != "disabled")`. `_install_verified_tool_inventory` already reads only `_TOOL_SERVERS`.
- **Python checks.** One module-level regex and one glob set per file, applied after the metacharacter check; the two checks stay equivalent (a test compares every row).
- **Hook.** Brace check right after the chaining check (before the Codex research predicate and before `-C` normalization); `RAW_CMD` saved before normalization and a glob check right after the must-be-git check.
- **Shared predicate.** `_has_blocked_metachars` gains the same brace regex.

## Error Handling

Unchanged shapes: Claude returns a reason (expanding braces and globs get their own messages), Codex returns `False`, the hook exits 2 with a `BLOCKED` reason; an extra live server fails the start through `_fail_start` with the names.

## Testing Strategy

RED first.
- Python (both suites, compared row by row): refused `git diff {--output=/tmp/x,}`, `git log {--output=/tmp/x,-1}`, `git diff *`, `git log -- '*.py'`; allowed `git log HEAD@{1}`, `git diff @{u}`.
- Inventory: `node_repl` and `computer-use` at `"disabled"` pass; a server at `"ready"` is refused (bounds the skip); the no-status row still fails.
- Hook: refused for Claude Bash and Codex `exec_command` `git diff {--output=/tmp/x,}`; Codex `git -C {.,diff,--output=/tmp/x} status`; `git diff *`; Codex `find /tmp {-delete,}`; allowed `git log HEAD@{1}`.
- Shared predicate: block `find /tmp {-delete,}`, `ls {a,b}`, `cat {1..3}`; allow `find . -name x -print`, `ls HEAD@{1}`-style literal braces (`cat a{b}`).
- Full Commander suite, `make test-hooks`, state-manager suite; new Opus end review of the combined staged diff.
