# Episodic Memory Size Measurement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Measure where episodic memory's roughly 80 GB actually goes, so the fix design can target the real bulk. The measurement covers:
- index composition;
- archive composition;
- how much of the archive overlaps with originals;
- how compressible the data is;
- which code paths read the archive.

**Requirements:** `docs/plans/2026-10-07-episodic-memory-size-measure-design.md` (`## Requirements`, E1–E6)

**Design:** `docs/plans/2026-10-07-episodic-memory-size-measure-design.md`

**Loop:** light. One task, read-only. It writes only to scratch logs and one findings note.

**Architecture:** Read-only queries go through `sqlite3 -readonly` against the index. The long scans run as background Bash calls. Archive sizes and overlap are measured with `find` and a small read-only scratch script. Compressibility is measured by streaming through `gzip -c | wc -c`. Code paths are found by grepping the source. Each run logs to its own file under `<SP>/epmem/`.

**Tech Stack:** sqlite3 (system), bash, find, awk, gzip, wc, grep.

## Execution invariants

- **Shell use:**
  - Shell state does not persist between steps, so every path is absolute.
  - zsh `nomatch` is on, so quote globs.
- **Paths:**
  - `<SP>` = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad`.
  - `DB` = `/Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite`.
  - `ARCH` = `/Users/roberthyatt/.config/superpowers/conversation-archive`.
- **Read-only:**
  - Every `sqlite3` call uses `-readonly`.
  - No file in `~/.config/superpowers`, `~/.claude` or `~/.codex` is modified or deleted.
  - Compression uses `gzip -c` streamed into `wc -c` and writes no file.
- **Evidence:**
  - Each step writes to its own log in `<SP>/epmem/`.
  - No evidence command uses `2>/dev/null`.
  - Record a timestamp (`date`) in every log, because the episodic-memory server may write to the index concurrently.
- **Long scans:**
  - The scans in Steps 2 and 3 run as background Bash calls with a timeout of 3600000 ms.
  - Wait for each one's completion notification.
  - If the `dbstat` table is unavailable, record the error and continue with the column sums.
- **Expected results:** all expected values mean "record what was actually measured". Nothing is predicted.

---

## Task 1: Measure and record

**Files:**
- Create: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/overlap.sh`
- Create: `docs/reviews/2026-10-07-episodic-memory-size-findings.md`
- Stage, but do not edit: `docs/plans/2026-10-07-episodic-memory-size-measure-design.md`, `docs/plans/2026-10-07-episodic-memory-size-measure.md`, `docs/plans/2026-10-07-episodic-memory-size-measure.plan.json`

**No tests required:** this task is a read-only investigation with no product code.

**Step 1: file-level sizes**

```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem; L=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/s1-pragma.log; date > "$L"; ls -l /Users/roberthyatt/.config/superpowers/conversation-index/ >> "$L"; sqlite3 -readonly /Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite "PRAGMA page_size; PRAGMA page_count; PRAGMA freelist_count; SELECT name FROM sqlite_master ORDER BY name;" >> "$L" 2>&1; echo "rc=$?" >> "$L"; cat "$L"
```

Expected: record the page size, page count and freelist count. Free bytes are `freelist_count × page_size`. Also record the full list of objects in the database.

**Step 2: per-object space from `dbstat`** (background, timeout 3600000)

```bash
L=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/s2-dbstat.log; date > "$L"; sqlite3 -readonly /Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite "SELECT name, SUM(pgsize) AS bytes, COUNT(*) AS pages FROM dbstat GROUP BY name ORDER BY bytes DESC;" >> "$L" 2>&1; echo "rc=$?" >> "$L"; date >> "$L"
```

Expected: record bytes for each table and index. If the output contains `no such table: dbstat`, record that as unavailable.

**Step 3: column bytes, size bands, top tools and top projects** (background, timeout 3600000)

```bash
L=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/s3-columns.log; date > "$L"; sqlite3 -readonly -header /Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite "SELECT COUNT(*) n, SUM(length(CAST(user_message AS BLOB))) user_b, SUM(length(CAST(assistant_message AS BLOB))) asst_b, SUM(length(embedding)) emb_b, SUM(length(CAST(archive_path AS BLOB))) path_b FROM exchanges; SELECT COUNT(*) n, SUM(length(CAST(tool_input AS BLOB))) in_b, SUM(length(CAST(tool_result AS BLOB))) res_b FROM tool_calls; SELECT CASE WHEN length(CAST(tool_result AS BLOB))>1048576 THEN '>1MB' WHEN length(CAST(tool_result AS BLOB))>102400 THEN '>100KB' WHEN length(CAST(tool_result AS BLOB))>10240 THEN '>10KB' ELSE '<=10KB' END band, COUNT(*) n, SUM(length(CAST(tool_result AS BLOB))) bytes FROM tool_calls GROUP BY band; SELECT tool_name, COUNT(*) n, SUM(length(CAST(tool_result AS BLOB))) res_b, SUM(length(CAST(tool_input AS BLOB))) in_b FROM tool_calls GROUP BY tool_name ORDER BY res_b DESC LIMIT 15; SELECT project, COUNT(*) n, SUM(length(CAST(user_message AS BLOB))+length(CAST(assistant_message AS BLOB))) text_b FROM exchanges GROUP BY project ORDER BY text_b DESC LIMIT 15;" >> "$L" 2>&1; echo "rc=$?" >> "$L"; date >> "$L"
```

Expected: record every result set. Sizes of TEXT columns are in bytes (`CAST(… AS BLOB)`). `embedding` is a BLOB.

**Step 4: archive composition**

```bash
L=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/s4-archive.log; A=/Users/roberthyatt/.config/superpowers/conversation-archive; date > "$L"; find "$A" -type f -ls > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/archive-files.txt; awk -v A="$A/" '{p=$11; sub(A,"",p); split(p,a,"/"); s[a[1]]+=$7; c[a[1]]++; t+=$7} END {for (k in s) printf "%14d %6d %s\n", s[k], c[k], k; printf "%14d TOTAL\n", t}' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/archive-files.txt | sort -rn >> "$L"; echo "--- top 20 files" >> "$L"; sort -k7 -rn /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/archive-files.txt | head -20 | awk '{print $7, $11}' >> "$L"; cat "$L"
```

Expected: record the bytes and file count per subdirectory, the total, and the 20 largest files.

**Step 5: work out how originals map to archive copies.** Read the code. Do not guess.

```bash
grep -n -e "archive" -e "copyFile" -e "copyIfNewer" -e "projects" -e "sessions" -e "rollout" -e "mungeCwd" -e "readdirSync" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/indexer.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/paths.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/sync.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/codex-sync.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/codex-paths.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/sync-cli.ts
```

Expected: the mapping, confirmed by reading the cited ranges.
- **Claude**, `sync.ts:106-110`, non-recursive: `ARCH/<proj>/<base>.jsonl` ↔ `~/.claude/projects/<proj>/<base>.jsonl`. This covers UUID sessions and legacy `agent-<8hex>.jsonl` files.
  - Copies are gated on mtime (`copyIfNewer`, `sync.ts:44-50`).
  - The current subagent layout, `<proj>/<session>/subagents/*`, is not archived.
- **Codex**, `codex-sync.ts:30-44,102-107`: `ARCH/<mungeCwd(cwd) | codex-<id>>/rollout-*.jsonl` ↔ any `rollout-*.jsonl` under `~/.codex/sessions` (searched recursively) or `~/.codex/archived_sessions`.
- **Sync sources**: `sync-cli.ts:62-64`.
- **Legacy indexer path**: `indexer.ts:88-116`.

Record any difference you find from the above.

**Step 6: write the overlap script.** Use the Write tool to create `<SP>/epmem/overlap.sh` with exactly this content. It is read-only (`find`, `stat -f %z`, `test -e`):

```bash
#!/bin/bash
# overlap.sh — archive-vs-original overlap. Read-only: find, stat -f %z, test -e.
# Claude  sync.ts:106-110: ARCH/<proj>/<base>.jsonl <-> ~/.claude/projects/<proj>/<base>.jsonl
# Codex   codex-sync.ts:102-107: ARCH/<proj>/rollout-*.jsonl <-> rollout-*.jsonl under ~/.codex/sessions or ~/.codex/archived_sessions (basename index)
set -u
ARCH=/Users/roberthyatt/.config/superpowers/conversation-archive
CLAUDE_PROJ=/Users/roberthyatt/.claude/projects
CODEX_SESS=/Users/roberthyatt/.codex/sessions
CODEX_ARCHIVED=/Users/roberthyatt/.codex/archived_sessions
SP=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem
LOG=$SP/s6-overlap.log
IDX=$SP/codex-rollout-index.txt
TAB=$'\t'

mkdir -p "$SP"
date > "$LOG"

find "$CODEX_SESS" "$CODEX_ARCHIVED" -type f -name 'rollout-*.jsonl' \
  | awk -F/ '{ print $NF "\t" $0 }' | sort > "$IDX"
echo "codex originals indexed: $(wc -l < "$IDX")" >> "$LOG"

find "$ARCH" -type f -print0 | while IFS= read -r -d '' f; do
  rel=${f#"$ARCH"/}
  proj=${rel%%/*}
  base=${rel#*/}
  size=$(stat -f %z "$f")
  kind=unknown; orig=""; class=UNMAPPED

  case "$base" in
    */*)                   kind=nested ;;
    *-summary.txt|*.tmp.*) kind=generated; class=GENERATED ;;
    rollout-*.jsonl)       kind=codex-rollout
                           orig=$(grep -m1 -F -- "${base}${TAB}" "$IDX" | cut -f2-) ;;
    agent-*.jsonl)         kind=claude-agent;   orig=$CLAUDE_PROJ/$proj/$base ;;
    *.jsonl)               kind=claude-session; orig=$CLAUDE_PROJ/$proj/$base ;;
  esac

  if [ "$class" != GENERATED ] && [ "$kind" != nested ] && [ "$kind" != unknown ]; then
    if [ -n "$orig" ] && [ -e "$orig" ]; then
      osize=$(stat -f %z "$orig")
      if [ "$osize" = "$size" ]; then class=ORIG_SAME_SIZE; else class=ORIG_DIFF_SIZE; fi
    else
      class=ORIG_MISSING
    fi
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$class" "$kind" "$size" "$f" "${orig:--}" >> "$LOG"
done

echo "--- summary (bytes count class)" >> "$LOG"
awk -F'\t' '$1 ~ /^(ORIG_|GENERATED|UNMAPPED)/ { b[$1]+=$3; c[$1]++ }
  END { for (k in b) printf "%14d %6d %s\n", b[k], c[k], k }' "$LOG" | sort -rn >> "$LOG"
echo "--- summary by class/kind (bytes count class kind)" >> "$LOG"
awk -F'\t' '$1 ~ /^(ORIG_|GENERATED|UNMAPPED)/ { k=$1" "$2; b[k]+=$3; c[k]++ }
  END { for (k in b) printf "%14d %6d %s\n", b[k], c[k], k }' "$LOG" | sort -rn >> "$LOG"
date >> "$LOG"
tail -30 "$LOG"
```

The script treats `ORIG_DIFF_SIZE` as "original still present". The original may have been appended to or rewritten after the mtime-gated copy.

**Step 7: run the overlap script** (Bash timeout 600000):

```bash
bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/overlap.sh; tail -30 /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/s6-overlap.log
```

Expected: record the bytes and count for each class, and for each class broken down by kind.

**Step 8: compressibility.** Sample the 3 largest archive files and 5 others from different projects. Take the file names from `archive-files.txt` and list them in the log. Then:

```bash
L=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/s8-gzip.log; date > "$L"; for f in <the 8 chosen absolute paths>; do o=$(stat -f %z "$f"); g=$(gzip -c "$f" | wc -c); echo "$o $g $f" >> "$L"; done; sqlite3 -readonly /Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite "SELECT tool_result FROM tool_calls WHERE length(tool_result) > 10240 LIMIT 200;" > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/toolres-sample.txt; o=$(stat -f %z /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/toolres-sample.txt); g=$(gzip -c /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/toolres-sample.txt | wc -c); echo "$o $g toolres-sample" >> "$L"; cat "$L"
```

Expected: record the original bytes, gzip bytes and ratio for each sample.

**Step 9: code paths that read the archive or use tool data**

```bash
grep -rn -e "archive_path" -e "line_start" -e "readFileSync" -e "createReadStream" -e "tool_result" -e "tool_input" -e "embed" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src '--include=*.ts'
```

Expected: list each reader and what it reads, citing `file:line`. Say whether search, display or embeddings use `tool_result` or `tool_input`. Read the ranges you cite to confirm them.

**Step 10: write `docs/reviews/2026-10-07-episodic-memory-size-findings.md`.** Include:
- the measurement times;
- for the index: file size, free bytes, each object's bytes (from `dbstat`, or "unavailable"), and the column byte totals, size bands, top tools and top projects;
- for the archive: total, per-project breakdown, top files, and the overlap classes with bytes;
- compression ratios;
- the code paths found;
- an estimated saving for each candidate fix, each with the measured numbers it rests on:
  - slimming the index (strip or truncate tool data);
  - VACUUM;
  - cloning or skipping archive copies whose originals still exist;
  - compressing the archive;
  - prune rules (scratch `/private/tmp` projects, huge rollouts, sidechains).

Mark anything that was not measured as UNVERIFIED. Two further notes:
- `-readonly` sessions take read-marks in `db.sqlite-shm`, which is SQLite's own locking and not user data.
- The current subagent transcripts (`<proj>/<session>/subagents/*`) are not archived.

**Step 11: stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-07-episodic-memory-size-findings.md docs/plans/2026-10-07-episodic-memory-size-measure-design.md docs/plans/2026-10-07-episodic-memory-size-measure.md docs/plans/2026-10-07-episodic-memory-size-measure.plan.json
```

Expected: no output, exit 0.
