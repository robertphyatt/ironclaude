# Shared-Resource Prune: Findings (2026-10-14)

## Authority

On 2026-10-14 the operator answered "Go: run the cleanup loop (Recommended)". This loop applies that go, narrowed as recorded in the design: `docs/plans/2026-10-14-shared-resources-prune-design.md`.

## What changed

The file changed is `/Users/roberthyatt/Code/roleplaying-agents/.git/info/worktree-shared-resources`.

- The file went from 66 lines (`/private/tmp/shared-prune/before.txt`) to 46 lines (`after.txt`).
- `diff before candidate` shows 20 `<` lines and 0 `>` lines (`candidate.diff`).
- Just before the copy, the live config still matched the snapshot (`cmp` returned 0).
- After the copy, `cmp candidate after` returned 0.

The 20 removed lines, quoted from `candidate.diff`:

```
godot/artificial-adventures/tools/music_retrieval/role_audio_hybrid.py
godot/artificial-adventures/tools/music_retrieval/semantic_role_hybrid.py
godot/artificial-adventures/tools/music_retrieval/musical_role_profiles.json
godot/artificial-adventures/tools/music_retrieval/editorial_top2_development.json
godot/artificial-adventures/tools/music_retrieval/__init__.py
godot/artificial-adventures/tools/music_retrieval/native_clap_adapter.py
godot/artificial-adventures/tools/music_retrieval/native_clap_install.py
godot/artificial-adventures/tools/music_retrieval/native_clap_protocol.py
godot/artificial-adventures/tools/music_retrieval/clap_index.py
godot/artificial-adventures/tools/music_retrieval/native_whole_track.py
godot/artificial-adventures/tools/music_retrieval/native_full_catalog.py
godot/artificial-adventures/tools/music_retrieval/pilot_schema.py
godot/artificial-adventures/tools/music_retrieval/model_install.py
godot/artificial-adventures/tools/music_retrieval/native_clap_requirements.lock
godot/artificial-adventures/tools/music_retrieval/musical_intent_texts.json
godot/artificial-adventures/audio/music/Call It Cooperation.ogg
godot/artificial-adventures/audio/music/Neglected.ogg
godot/artificial-adventures/tools/music_retrieval/binary_artifacts.py
godot/artificial-adventures/tools/music_retrieval/native_clap_worker.py
godot/artificial-adventures/tools/music_retrieval/full_catalog.py
```

`.venv` and `godot/artificial-adventures/models/music_clap` are among the 46 entries that remain.

All 20 files are now committed on roleplaying-agents, in `fc4020814`, `c35d6d2` and `f9c4882`.

## Unchanged on purpose

- `.git/info/exclude` was not modified. The spot check found both `/…/model_install.py` and `/…/Neglected.ogg` still present (count 2). The exclude lines were kept because active managed worktree `3106ccc6` holds symlinks at all 20 paths, and un-excluding them would make it read as dirty (`git.ts:268-275`).
- No IronClaude code, DB row, worktree, branch or primary-checkout file was touched.

## Backlog

- Remove the 20 exclude lines once worktree 3106ccc6 is closed.
- The 019f7d26 close-out goes through `/reconcile` in the roleplaying-agents Codex session once local main reaches `f9c4882`.
- The product defect is that shared resources accept and hide untracked source files. It needs its own IronClaude loop.
