# Inkarnate Asset Catalog Backup Design

> **Created:** 2026-10-11
> **Status:** Design Complete

Loop: light — a read-only snapshot of one database, compressed and copied to Google Drive, with verification; no code, git, DB writes or API changes; one destination folder plus a results note.

## Requirements

Operator, 2026-10-11, after git stopped tracking it: "Um we do want to backup the inkarnate asset catalog db somehow...", "It took over a week to build. Let's not have it get blown away easily 🙏". The operator chose "A: Google Drive (Rec.)" and said "please put a copy on google drive".

- **B1. Snapshot.** Source: `/Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` (3,654,037,504 bytes, mtime 2026-10-06 02:11). Something holds it open (`-shm` was modified 2026-10-11 18:29), so use SQLite's online backup (`sqlite3 <src> ".backup <dst>"`), which is safe while it is open. Write to `/private/tmp/inkarnate-catalog-backup/`. The source is never modified.
- **B2. Verify the snapshot.** `PRAGMA integrity_check` returns `ok`. The row count of every table in the snapshot equals the source's.
- **B3. Compress.** `zstd -19 -T0 --long=27` produces `asset_catalog-2026-10-11.db.zst`. `zstd -t` passes, and the decompressed SHA-256 equals the snapshot's SHA-256.
- **B4. Copy to Google Drive.** Copy the `.zst` plus a `SHA256SUMS` file (snapshot and `.zst` hashes) to `~/Library/CloudStorage/GoogleDrive-robert.p.hyatt@gmail.com/My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog/`. Read the Drive copy back, decompress it to a hash, and require it to equal the snapshot hash.
- **B5. Clean up** the temp directory after B4 verifies, and record the results in `docs/reviews/2026-10-11-disk-usage-findings.md`.

## Non-goals

No scheduled or recurring backup (offer it afterwards), no change to the source database, no git.
