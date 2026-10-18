# Inkarnate Asset Catalog Backup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Put a verified, compressed snapshot of the 3.65 GB Inkarnate asset catalog database in Google Drive.

**Requirements:** `docs/plans/2026-10-11-inkarnate-catalog-backup-design.md` (`## Requirements`, B1–B5).

**Architecture:** An SQLite online backup to `/private/tmp`, verified by integrity check and per-table row counts, then compressed with zstd and copied to Drive with a checksum file. The Drive copy is read back and its decompressed hash is verified.

**Loop:** light — a read-only snapshot plus a copy; no code, git, DB writes or API changes.

## Execution invariants

- Use absolute paths and quote the Drive path, which contains spaces.
- Never write to the source database.
- `--long=27` is required on every zstd compress and decompress.
- No `2>/dev/null` on evidence commands.
- Commands that may run long get a Bash timeout of 1800000.

The literal values used below:
- **SRC:** `/Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`
- **TMP:** `/private/tmp/inkarnate-catalog-backup`
- **DRV:** `/Users/roberthyatt/Library/CloudStorage/GoogleDrive-robert.p.hyatt@gmail.com/My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog`

---

## Task 1: Snapshot, verify, compress, copy to Drive, verify Drive copy

No tests required: an operational backup with built-in verification, and no executable code.

**Step 1.** Check free space with `df -h /System/Volumes/Data`. It needs at least about 10 GiB: the snapshot, the `.zst` and the Drive cache copy. Then take the snapshot, opening the source **read-only** so its `-wal` and `-shm` sidecars are never checkpointed or deleted. Bash timeout 1800000:

```bash
mkdir -p /private/tmp/inkarnate-catalog-backup && /usr/bin/sqlite3 -readonly /Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db ".backup '/private/tmp/inkarnate-catalog-backup/asset_catalog-2026-10-11.db'" && ls -la /private/tmp/inkarnate-catalog-backup
```

Expected: the snapshot exists. Its size need not equal the source exactly, because a backup writes only used pages.

**Step 2.** Integrity check. Bash timeout 1800000:

```bash
/usr/bin/sqlite3 /private/tmp/inkarnate-catalog-backup/asset_catalog-2026-10-11.db "PRAGMA integrity_check;"
```

Expected: exactly `ok`. Otherwise stop.

**Step 3.** Row counts per table, source vs snapshot. Bash timeout 1800000:

```bash
for db in /Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db /private/tmp/inkarnate-catalog-backup/asset_catalog-2026-10-11.db; do echo "== $db"; for t in $(/usr/bin/sqlite3 -readonly "$db" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"); do echo "$t $(/usr/bin/sqlite3 -readonly "$db" "SELECT count(*) FROM \"$t\";")"; done; done
```

Expected: the two lists are identical, with the same table names and counts. Otherwise stop.

**Step 4.** Hash the snapshot, then compress and test:

```bash
cd /private/tmp/inkarnate-catalog-backup && shasum -a 256 asset_catalog-2026-10-11.db > SHA256SUMS && /opt/homebrew/bin/zstd -19 -T0 --long=27 -q asset_catalog-2026-10-11.db -o asset_catalog-2026-10-11.db.zst && /opt/homebrew/bin/zstd -t --long=27 asset_catalog-2026-10-11.db.zst && shasum -a 256 asset_catalog-2026-10-11.db.zst >> SHA256SUMS && S=$(shasum -a 256 asset_catalog-2026-10-11.db | cut -d' ' -f1) && D=$(/opt/homebrew/bin/zstd -dc --long=27 asset_catalog-2026-10-11.db.zst | shasum -a 256 | cut -d' ' -f1) && [ "$S" = "$D" ] && echo "DECOMPRESS MATCH $S" || echo "DECOMPRESS MISMATCH"; md5 -q asset_catalog-2026-10-11.db.zst; stat -f %z asset_catalog-2026-10-11.db.zst; cat SHA256SUMS
```

Expected: `DECOMPRESS MATCH`; `MISMATCH` stops the task. Record the `.zst` MD5 and byte size for Step 7b. Bash timeout 1800000.

**Step 5.** Add a restore note to `SHA256SUMS`, as two `#` lines:

```bash
printf '# Restore: zstd -d --long=27 asset_catalog-2026-10-11.db.zst -o asset_catalog.db\n# Source: roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db (3654037504 bytes, mtime 2026-10-06 02:11), SQLite online backup taken 2026-10-11\n' >> /private/tmp/inkarnate-catalog-backup/SHA256SUMS
```

**Step 6.** Copy to Drive. Bash timeout 1800000:

```bash
mkdir -p "/Users/roberthyatt/Library/CloudStorage/GoogleDrive-robert.p.hyatt@gmail.com/My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog" && cp /private/tmp/inkarnate-catalog-backup/asset_catalog-2026-10-11.db.zst /private/tmp/inkarnate-catalog-backup/SHA256SUMS "/Users/roberthyatt/Library/CloudStorage/GoogleDrive-robert.p.hyatt@gmail.com/My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog/" && ls -la "/Users/roberthyatt/Library/CloudStorage/GoogleDrive-robert.p.hyatt@gmail.com/My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog"
```

**Step 7a.** Verify the local Drive-cache copy by reading it back. Bash timeout 1800000:

```bash
/opt/homebrew/bin/zstd -dc --long=27 "/Users/roberthyatt/Library/CloudStorage/GoogleDrive-robert.p.hyatt@gmail.com/My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog/asset_catalog-2026-10-11.db.zst" | shasum -a 256
```

Expected: equals the snapshot hash in `SHA256SUMS`. This proves only the local cache; the cloud is checked in 7b.

**Step 7b.** Verify the upload in the cloud with the Google Drive connector, not the mount.
- Run `mcp__claude_ai_Google_Drive__search_files` with query `title = 'asset_catalog-2026-10-11.db.zst'` and `excludeContentSnippets: true`, then `mcp__claude_ai_Google_Drive__get_file_metadata` on the result.
- **Pass:** the cloud file exists, its byte size equals the Step 4 size, and, if the connector reports an MD5 checksum, it equals the Step 4 MD5.
- While the upload is running, the file may be missing or smaller. Recheck at intervals of about 2 minutes, for up to 60 minutes.
- **Fail:** if the connector exposes no size at all, or the file is still not confirmed after 60 minutes, stop. Do NOT delete the temp directory. Report to the operator, who can confirm in drive.google.com.

**Step 8.** Remove the temp directory, only after **Step 7b** passes: `rm -r /private/tmp/inkarnate-catalog-backup`. Then append a "Catalog backup" section to `docs/reviews/2026-10-11-disk-usage-findings.md` with the hashes, sizes, Drive path and restore command, and stage it:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-11-disk-usage-findings.md docs/plans/2026-10-11-inkarnate-catalog-backup-design.md docs/plans/2026-10-11-inkarnate-catalog-backup.md docs/plans/2026-10-11-inkarnate-catalog-backup.plan.json
```
