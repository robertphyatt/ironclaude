# Disk Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Free disk and stop the roleplaying-agents `.git` growth, per the operator's decisions C1–C6.

**Requirements:** `docs/plans/2026-10-11-disk-cleanup-design.md` (`## Requirements`, C1–C6).

**Architecture:** Ordered shell operations, each scoped by an exact path or `find` predicate and each checked before and after. There is one IronClaude doc change, the findings results. The other file changes are a one-line `.gitignore` entry and a local commit in roleplaying-agents, plus one config edit.

**Loop:** full — destructive deletions, a git commit in another repo, and a config change.

## Execution invariants

- Use absolute paths everywhere. Never glob-delete outside the paths named here.
- Use `find … -delete` with exact predicates rather than `rm -rf` globs.
- Before each step, list what will be deleted. Recheck `df -h /System/Volumes/Data` after each task.
- Never push, never pattern-kill processes, and never touch `~/Downloads`.
- No `2>/dev/null` on evidence commands.
- No trailers in the roleplaying-agents commit (standing operator rule).
- If a guard blocks the roleplaying-agents `git rm --cached` or `git commit`, stop and report the exact message. The operator can run that one command.

---

## Task 1: Delete 8 old catalog snapshots (C1)

No tests required: file deletion with a before-and-after listing.

Scope: only the 9 `successor-inventory-*` snapshots the operator approved. The 11 `vector-db-*/catalog-before.sqlite3` snapshots (September, about 0.64 GB in total) are NOT approved and must not be touched.

**Step 1.** List the approved set:

```bash
find /Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/probe_out -path '*/successor-inventory-*' -name 'catalog-before.sqlite3' -exec ls -la -T {} +
```

Expected: exactly 9 files. The newest by mtime is `successor-inventory-fantasy-battlemaps-20261004-recovered/catalog-before.sqlite3` (Oct 4 05:52). **Gate:** if the count is not 9, or the newest is a different file, stop and report without deleting anything. Also run the same command with `-path '*/vector-db-*'` and record its count; it must be unchanged after Step 2.

**Step 2.** Delete the approved set except the newest:

```bash
find /Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/probe_out -path '*/successor-inventory-*' -name 'catalog-before.sqlite3' -not -path '*/successor-inventory-fantasy-battlemaps-20261004-recovered/*' -print -delete
```

Expected: exactly 8 paths printed, all `successor-inventory-*`.

**Step 3.** Rerun the Step 1 command. Expected: exactly 1 file, the kept one. Then rerun the `vector-db-*` count; it must equal the Step 1 value. Then run `df -h /System/Volumes/Data` and record the result.

---

## Task 2: Clear caches (C5)

No tests required: cache deletion with a recorded listing.

**Step 1.** Record the Hugging Face model list:

```bash
du -sh /Users/roberthyatt/.cache/huggingface/hub/models--* /Users/roberthyatt/.cache/huggingface/xet
```

Record the output verbatim.

**Step 2.** Delete the Hugging Face model cache, keeping the login:

```bash
find /Users/roberthyatt/.cache/huggingface/hub /Users/roberthyatt/.cache/huggingface/xet -mindepth 1 -delete
```

Then `ls -la /Users/roberthyatt/.cache/huggingface`. Expected: `token`, `stored_tokens` and `modules` still present; `hub` and `xet` empty.

**Step 3.** Delete the pip and claude-cli caches:

```bash
find /Users/roberthyatt/Library/Caches/pip /Users/roberthyatt/Library/Caches/claude-cli-nodejs -mindepth 1 -delete
```

Errors on files in use are recorded as errors, not hidden. Then run `du -sh /Users/roberthyatt/.cache/huggingface /Users/roberthyatt/Library/Caches/pip /Users/roberthyatt/Library/Caches/claude-cli-nodejs` and `df -h /System/Volumes/Data`, and record both.

---

## Task 3: Episodic-memory rollout (C2)

No tests required: operational run of the existing, reviewed `maintain` command with its own verification gates.

**Step 1.** Rerun the dry-run:

```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/cli/episodic-memory maintain --dry-run
```

Expected: `slim: would-run` (no longer `refused`). If it still refuses, stop and report the free-space numbers.

**Step 2.** Real run, in the background with a Bash timeout of 7200000. Log to `/private/tmp/episodic-maintain-2026-10-11.log`:

```bash
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/cli/episodic-memory maintain --keep-bak > /private/tmp/episodic-maintain-2026-10-11.log 2>&1; echo "maintain exit=$?" >> /private/tmp/episodic-maintain-2026-10-11.log
```

Wait with a foreground `until grep -q 'maintain exit=' /private/tmp/episodic-maintain-2026-10-11.log; do sleep 10; done` loop, Bash timeout 600000, repeated as needed. Expected: `maintain exit=0`, and per-phase lines show `slim` done, `backfill` done and `compact` done. Record the log. If the exit code is nonzero, stop, record the log and report; do not retry blindly.

**Step 3.** Verify:
- `ls -la /Users/roberthyatt/.config/superpowers/conversation-index` shows a `db.sqlite` of about 7.8 GB and a `db.sqlite.bak` present.
- `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/cli/episodic-memory search "codex brain parity"` returns at least 1 result.
- `episodic-memory show <first result path>` prints that conversation.

Record all three outputs (show output: first 20 lines).

Note for the operator report: `--keep-bak` keeps the 42 GB `db.sqlite.bak` for now, so slim frees nothing until it is removed. Once `autoRun` is true, the next background sync runs `maintain` without `--keep-bak`, re-verifies the new index and then deletes `db.sqlite.bak` (`maintain.ts:240-247`, `sync-cli.ts:118`). If slim reports `refused` (a busy WAL), the CLI still exits 0, so check the per-phase lines rather than only the exit code.

**Step 4.** Write `{"maintain":{"autoRun":true}}` to `/Users/roberthyatt/.config/superpowers/episodic-memory.json` with the Write tool, then `cat` it. Expected: exactly that JSON.

**Step 5.** Run `df -h /System/Volumes/Data` and `du -sh /Users/roberthyatt/.config/superpowers/conversation-index /Users/roberthyatt/.config/superpowers/conversation-archive`. Record both.

---

## Task 4: Replace build GGUF copies with APFS clones (C3)

No tests required: hash-verified file replacement.

**Step 1.** Hash the originals and the 9 build copies. Bash timeout 1800000:

```bash
cd /Users/roberthyatt/Code/roleplaying-agents/godot/artificial-adventures && shasum -a 256 models/gemma-4-E4B-it-qat-Q4_0.gguf models/e4b-download/gemma-4-E4B-it-mmproj.gguf models/qwen3-tts-gguf/qwen-talker-1.7b-customvoice-Q4_K_M.gguf "build/macos/Artificial Adventures.app/Contents/Resources/models/gemma-4-E4B-it-qat-Q4_0.gguf" "build/macos/Artificial Adventures.app/Contents/Resources/models/e4b-download/gemma-4-E4B-it-mmproj.gguf" "build/macos/Artificial Adventures.app/Contents/Resources/models/qwen3-tts-gguf/qwen-talker-1.7b-customvoice-Q4_K_M.gguf" "build/macos/High Gate Campaigns.app/Contents/Resources/models/gemma-4-E4B-it-qat-Q4_0.gguf" "build/macos/High Gate Campaigns.app/Contents/Resources/models/e4b-download/gemma-4-E4B-it-mmproj.gguf" "build/macos/High Gate Campaigns.app/Contents/Resources/models/qwen3-tts-gguf/qwen-talker-1.7b-customvoice-Q4_K_M.gguf" build/model_tree_stage/models/gemma-4-E4B-it-qat-Q4_0.gguf build/model_tree_stage/models/e4b-download/gemma-4-E4B-it-mmproj.gguf build/model_tree_stage/models/qwen3-tts-gguf/qwen-talker-1.7b-customvoice-Q4_K_M.gguf
```

Record all 12 lines.

**Step 2.** Replace each build copy whose hash equals its original's. A copy that differs is skipped and reported. Do one copy per command, using this pattern:

```bash
cp -c "<original>" "<copy>.clone-tmp" && mv -f "<copy>.clone-tmp" "<copy>"
```

**Step 3.** Rehash the 9 copies with the Step 1 command. Expected: each matches its original. Then run `df -h /System/Volumes/Data` and record it.

---

## Task 5: Stop the `.git` growth (C4)

No tests required: git index change plus debris deletion, verified by `git status` and listings.

**Step 1.** AskUserQuestion asking the operator to quit ChatGPT.app now and confirm. Then confirm with `pgrep -lx ChatGPT`: expected no output, exit 1.

**Step 2.** First confirm no git process is running anywhere: `pgrep -lx git`. Expected: no output, exit 1. Commander `codex-tui` workers share this object store, so if a git process is listed, stop and ask the operator. The 3 `objects/??/tmp_obj_*` leftovers are out of scope and stay. Then list the debris to delete and delete it:

```bash
find /Users/roberthyatt/Code/roleplaying-agents/.git/objects/pack -maxdepth 1 -name 'tmp_pack_*' -print -delete
find /Users/roberthyatt/Code/roleplaying-agents/.git -maxdepth 1 -name 'index.stash.*' -print -delete
```

**Step 3.** Untrack the database.

The real index already holds unrelated staged work (for example `MM asset_semantics.py`, `MM frontier_inspection.py`). A pathspec commit (`git commit -- <paths>`) would re-stage the modified 3.65 GB file from the working tree, so neither form may be used. Build the commit in a temporary index containing exactly the two changes, then make the same two changes in the real index.

(a) Record the real index's staged set: `git -C /Users/roberthyatt/Code/roleplaying-agents diff --cached --name-status`.

(b) Append `asset_catalog_raw/asset_catalog.db` as a new line to `/Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/.gitignore` with the Edit tool.

(c) Run each command separately:

```bash
GIT_INDEX_FILE=/private/tmp/ra-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents read-tree HEAD
GIT_INDEX_FILE=/private/tmp/ra-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents rm --cached tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db
GIT_INDEX_FILE=/private/tmp/ra-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents add tools/inkarnate_mcp/.gitignore
GIT_INDEX_FILE=/private/tmp/ra-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents diff --cached --name-status
```

Expected: exactly `D tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` and `M tools/inkarnate_mcp/.gitignore`. Anything else: stop.

(d) Commit from the temporary index, with no pathspec:

```bash
GIT_INDEX_FILE=/private/tmp/ra-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents commit -m "Stop tracking the 3.65 GB inkarnate asset catalog database" -m "Every Codex Desktop turn-diff snapshot re-stored the modified database and left hundreds of MB of tmp packs behind. The file stays on disk and is now ignored."
```

(e) Make the same two changes in the real index:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents rm --cached tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db
git -C /Users/roberthyatt/Code/roleplaying-agents add tools/inkarnate_mcp/.gitignore
```

Then delete `/private/tmp/ra-untrack.index`. Do not push.

**Step 4.** Verify with checks that fail when the file is still tracked:
- `git -C /Users/roberthyatt/Code/roleplaying-agents show --stat HEAD` contains `asset_catalog.db | Bin 6033408 -> 0 bytes` (HEAD's blob `2785c415…` is 6,033,408 bytes; the 3.65 GB is only the uncommitted working file) and the `.gitignore` line, and nothing else.
- `git -C /Users/roberthyatt/Code/roleplaying-agents ls-files --error-unmatch tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` must FAIL (nonzero).
- `git -C /Users/roberthyatt/Code/roleplaying-agents check-ignore -v tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` must print the `.gitignore` rule.
- `git -C /Users/roberthyatt/Code/roleplaying-agents diff --cached --name-status` must equal the Step 3(a) record exactly. The unrelated staged work is still staged, and the two committed paths do not appear, because they now match HEAD.
- `ls -la /Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` shows the file still present at 3654037504 bytes.
- `du -sh /Users/roberthyatt/Code/roleplaying-agents/.git` and `df -h /System/Volumes/Data`.

Record everything. Tell the operator ChatGPT.app can be restarted.

---

## Task 6: Record results (C6)

No tests required: documentation.

**Step 1.** Append a "Results (2026-10-11 cleanup)" section to `docs/reviews/2026-10-11-disk-usage-findings.md` with every recorded before-and-after value from Tasks 1–5. Then:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-11-disk-usage-findings.md docs/plans/2026-10-11-disk-cleanup-design.md docs/plans/2026-10-11-disk-cleanup.md docs/plans/2026-10-11-disk-cleanup.plan.json
```

Expected: no output.
