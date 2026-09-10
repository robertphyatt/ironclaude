---
name: use-managed-worktree
description: Move this session into an isolated managed Git worktree so concurrent sessions do not share a checkout
---

# Use Managed Worktree

Managed worktrees are opt-in. Professional mode works in your primary checkout by
default; this skill is how a session asks for isolation instead.

Use it when more than one session — another Claude Code or Codex session, or a
Commander worker — will touch this repository at the same time. Two sessions
sharing one checkout and one index overwrite each other silently, and the damage
is only visible later in a diff nobody wrote.

Unlike `use-primary-checkout`, `commit`, `commit-and-push`, and `push`, this
command consumes no human-authority intent. Entering isolation only *restricts*
what the session can reach, so it is safe to grant; leaving isolation expands
reach and therefore stays human-only.

## Process

1. Read professional mode and current workspace status through the active
   client's provider-native state-manager and workspace-manager. Require
   professional mode `on` and exact provider-root identity.
2. If the project root is not inside a Git worktree, stop and report that
   isolation is not applicable to a non-Git project. Do not call
   workspace-manager.
3. Call `get_workspace_status` for this session's provider root (Claude Code:
   `mcp__plugin_ironclaude_workspace-manager__get_workspace_status`; Codex:
   Codex `workspace-manager` `get_workspace_status`), passing `provider_root`.
   Branch on the returned `status` and, when assigned, `effectiveRoot` — never
   on an assignment count.
   - `status: "assigned"` and `effectiveRoot: "managed"`: the session is
     already correctly isolated. Report it. Do not allocate a second.
   - `status: "assigned"` and `effectiveRoot: "primary"`: the assignment
     exists, but writes are landing in the primary checkout, not the managed
     worktree. Report this truthfully. Do not call this "isolated." Point the
     operator to `/return-to-managed-worktree` to re-enter isolation. Do not
     allocate a second assignment.
   - `status: "unassigned"`: continue.
4. Require a clean primary checkout. If it is dirty, stop and report the exact
   dirty paths. Do not stash, commit, copy, reset, clean, or move those changes —
   the operator decides what happens to their own uncommitted work.
5. Call `activate_session_workspace` with only the canonical `repository_path`.
   Then call `get_workspace_status` using the returned `workspace_guid`.
6. The activation response and read-back must agree on `workspace_guid`,
   `repository_identity`, `worktree_path`, `branch`, `base_commit`,
   `owner_session_id`, `integration_target`, and active lifecycle. The owner must
   equal this session's provider-native root. Do not continue on partial,
   mismatched, or unregistered evidence.

On any failure, the session stays in the primary checkout. Report the exact
error. Do not half-enter isolation.

Display only after verified success:

```text
Managed worktree isolation ENABLED.

Workspace assignment: <workspace_guid>
Managed worktree: <worktree_path>
Managed branch: <branch>
Base commit: <base_commit>

What this changes:
• File writes and Bash commands now go to the managed worktree above — NOT your
  primary checkout. `git status` in the primary checkout will not show them.
• Any uncommitted work already in your primary checkout is untouched.
• Reads are never redirected away from you.
• Gitignored project data (model weights, assets, local caches) that lives only in
  the primary checkout is NOT copied into the worktree. Configured shared resources
  are symlinked in automatically on allocation; anything not configured is absent.

To go back to the primary checkout: /use-primary-checkout
```

## Shared resources (gitignored project data)

If your task needs gitignored data that is present in the primary checkout but
absent from this worktree, do NOT hand-write an `ln -s` and do NOT ask the operator
to touch the worktree. The data is provisioned through an explicit per-repository
allowlist at `<git-common-dir>/info/worktree-shared-resources` (one relative path
per line), symlinked in on every allocation and kept out of `git status` via
`info/exclude`. To add an entry self-serve:

- **Commander mode:** report the exact missing relative path(s) to the Brain as a
  blocker; the Brain calls the orchestrator `configure_shared_resources` tool, which
  appends the entries and relinks them into your live worktree — no respawn needed.
- Entries are explicit relative paths only (no globs, `..`, absolute paths, trailing
  slashes, or `!`/`#` prefixes). Shared paths are write-through symlinks into the
  primary checkout — treat them as shared state.

## Key Principles

- **Opt-in**: never invoked automatically by activation
- **Never destructive**: a dirty checkout stops the command, it does not get cleaned
- **Verified**: allocation is proven by an independent read-back before disclosure
- **Reversible**: `/use-primary-checkout` returns the session to the real checkout
- **Operator-free provisioning**: missing gitignored data is added via
  `configure_shared_resources` — never by hand-symlinking or asking the operator to
  touch a worktree
