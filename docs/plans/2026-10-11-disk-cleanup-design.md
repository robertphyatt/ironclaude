# Disk Cleanup Design

> **Created:** 2026-10-11
> **Status:** Design Complete

Loop: full — destructive deletions, a git commit in another repository, and a config change; no IronClaude code changes.

## Requirements

The operator gave these decisions on 2026-10-11, based on `docs/reviews/2026-10-11-disk-usage-findings.md`. Free space before the cleanup: 13 GiB (99%).

- **C1. Catalog snapshots.** "We can get rid of most of those. Maybe keep the last snapshot if it is recent?"
  - Delete the 8 older `tools/inkarnate_mcp/probe_out/*/catalog-before.sqlite3` in roleplaying-agents.
  - Keep the newest, `successor-inventory-fantasy-battlemaps-20261004-recovered/catalog-before.sqlite3` (2026-10-04 05:52).
  - Only those 8 files go; every other probe_out file stays.
- **C2. Episodic-memory rollout** ("Yes, run rollout"):
  - run `episodic-memory maintain --keep-bak`;
  - check `search` and `show`;
  - set `~/.config/superpowers/episodic-memory.json` to `{"maintain":{"autoRun":true}}`.
- **C3. Duplicate build GGUFs.** The operator chose "APFS clones" over symlinks.
  - For each GGUF under `godot/artificial-adventures/build/` whose `shasum -a 256` equals an original under `godot/artificial-adventures/models/`, replace it with an APFS clone (`cp -c`) of that original. The 3 files per copy are `gemma-4-E4B-it-qat-Q4_0.gguf`, `e4b-download/gemma-4-E4B-it-mmproj.gguf` and `qwen3-tts-gguf/qwen-talker-1.7b-customvoice-Q4_K_M.gguf`, in 3 copies: two `.app` bundles and `model_tree_stage`.
  - A copy whose hash does not match is left untouched and reported.
- **C4. Stop the `.git` growth** ("Yes, untrack + clean"). In roleplaying-agents:
  - `git rm --cached tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`, add a `.gitignore` entry, and make a local commit with no push. The working file stays.
  - Delete `.git/objects/pack/tmp_pack_*` and `.git/index.stash.*`.
  - The operator quits ChatGPT.app first, so that no Codex Desktop turn-diff snapshot runs during the git work. The operator asked: "Please let me know if you need me to stop chatgpt.app while you work on stuff".
- **C5. Caches** ("List huggingface, Clear pip/claude-cli, Clear out the caches. We don't need them but we do need the disk space to handle the episodic memory stuff"):
  - Record the Hugging Face model list with sizes, then delete the contents of `~/.cache/huggingface/hub` and `~/.cache/huggingface/xet`. Keep `token`, `stored_tokens` and `modules`, so the login stays.
  - Purge the pip cache (`~/Library/Caches/pip`).
  - Delete the contents of `~/Library/Caches/claude-cli-nodejs` (MCP logs).
  - `~/Downloads` and the other caches are out of scope.
- **C6. Record before and after.** Record `df -h /System/Volumes/Data` before and after each step, and write a results section in the findings doc.

## Order

1. C1 (about 18.6 GB) and C5 (up to about 40 GB) free space first.
2. C2 then has room: slim needs at least 2× the 7.8 GB new index.
3. C3.
4. C4 last, with ChatGPT.app quit.

## Non-goals

- No history rewrite (filter-repo) and no `git gc`. The 3.65 GB blob stays in history.
- Nothing in `~/Downloads` is touched, and nothing else in roleplaying-agents.
- No IronClaude code changes.
