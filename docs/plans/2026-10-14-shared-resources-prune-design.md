# Prune Committed Music Paths from Shared Resources Design

> **Created:** 2026-10-14
> **Status:** Design Complete

Loop: full — operational edit to a git-internal worktree config (git/worktree operations risk area); one file, 20 line deletions, no code change.

Delegated authority: operator go 2026-10-14 ("Go: run the cleanup loop (Recommended)"). Routine approvals are delegated.

## Requirements

- R1. Remove exactly these 20 lines from `/Users/roberthyatt/Code/roleplaying-agents/.git/info/worktree-shared-resources`. Every other line stays, in its original order.
  - 18 under `godot/artificial-adventures/tools/music_retrieval/`: role_audio_hybrid.py, semantic_role_hybrid.py, musical_role_profiles.json, editorial_top2_development.json, `__init__.py`, native_clap_adapter.py, native_clap_install.py, native_clap_protocol.py, clap_index.py, native_whole_track.py, native_full_catalog.py, pilot_schema.py, model_install.py, native_clap_requirements.lock, musical_intent_texts.json, binary_artifacts.py, native_clap_worker.py, full_catalog.py.
  - 2 under `godot/artificial-adventures/audio/music/`: `Call It Cooperation.ogg`, `Neglected.ogg`.
- R2. Leave `.git/info/exclude` unchanged.
- R3. Take a before snapshot, take an after snapshot, and diff them. The diff must show exactly 20 deletions and no additions.
- R4. No IronClaude product change, no DB write, and no change to any worktree, branch or primary-checkout file.
- R5. Record the result in a findings note.

## Summary

These 20 paths were reaching managed worktrees only as symlinks to untracked primary-checkout files. That hid the fact that they had never been committed. They are now committed on roleplaying-agents (`fc4020814`, `c35d6d2`, `f9c4882`). Keeping them in the shared list would quietly mask the gap again for any future worktree whose base predates those commits.

## Architecture

Predicted answer: yes (delegated)

- The only reader of the config is `readSharedResourceConfig`, called from `workspace-service.ts:363-368` at worktree materialization. Removing lines therefore affects future allocations only.
- `linkSharedResources` never overwrites an existing path (`git.ts:512`), so worktrees based on the new main keep their tracked files.
- `.git/info/exclude` is kept on purpose. Active worktree `3106ccc6` holds symlinks at all 20 paths, and un-excluding them would make it read as dirty (`git.ts:268-275`), so reconcile and cleanup would refuse. Those lines go when 3106ccc6 is closed (backlog).
- The edit is a filter: write the original minus the 20 exact lines to a temp file in the scratch dir, verify it, then replace the config.

## Error Handling

Predicted answer: yes (delegated)

- If any of the 20 lines is missing from the before snapshot, or appears more than once, stop and report. Do not edit.
- If the diff shows anything other than 20 deletions, restore from the before snapshot and report.
- If the guard blocks the write because it references another checkout, stop and report. Do not work around it.

## Testing Strategy

Predicted answer: yes (delegated)

No tests are required: this is an operational config edit, not code. Verification:
- the before line count minus 20 equals the after line count;
- `diff` lists exactly the 20 paths as `<` lines, with no `>` lines;
- `grep -c` for each of the 20 paths in the after file returns 0.

## Implementation Notes

- Evidence goes in `/private/tmp/shared-prune/`.
- The DB close-out of assignment 019f7d26 is out of scope. It goes to `/reconcile` in Codex once roleplaying-agents main reaches f9c4882.

Predicted answer: Yes, invoke writing-plans (delegated)
