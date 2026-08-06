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
3. Call `list_active_assignments` for the canonical project root.
   - One assignment already bound to this provider root: the session is already
     isolated. Read it back with `get_workspace_status` and report it. Do not
     allocate a second.
   - More than one: stop and report the ambiguity. Do not select one.
   - None: continue.
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

To go back to the primary checkout: /use-primary-checkout
```

## Key Principles

- **Opt-in**: never invoked automatically by activation
- **Never destructive**: a dirty checkout stops the command, it does not get cleaned
- **Verified**: allocation is proven by an independent read-back before disclosure
- **Reversible**: `/use-primary-checkout` returns the session to the real checkout
