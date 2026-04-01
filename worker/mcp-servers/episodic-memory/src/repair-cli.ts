/**
 * repair-cli.ts — One-time retroactive backfill for grown sessions.
 *
 * Scans all session files that have been at least partially indexed,
 * finds any whose current line count exceeds their MAX(line_end) in the DB,
 * and re-indexes the gap.
 *
 * After the incremental sync fix ships, normal post-session sync handles
 * all growth automatically. This command is for historical backfill only.
 */

import fs from 'fs';
import { initDatabase, insertExchange } from './db.js';
import { parseConversation } from './parser.js';
import { initEmbeddings, generateExchangeEmbedding } from './embeddings.js';

const args = process.argv.slice(2);

if (args.includes('--help') || args.includes('-h')) {
  console.log(`
Usage: episodic-memory repair

Retroactively backfill indexing gaps in grown session files.

Scans all sessions with at least one indexed exchange, compares
MAX(line_end) to the current file line count, and re-indexes any
lines that were added after the last index run.

Run once after upgrading to the incremental sync fix to catch up
historical sessions. Normal sync handles growth going forward.

EXAMPLES:
  # Repair all indexing gaps
  episodic-memory repair
`);
  process.exit(0);
}

async function repair(): Promise<void> {
  console.log('Scanning for indexing gaps...');
  const db = initDatabase();
  await initEmbeddings();

  // Get all distinct archive paths with their max indexed line
  const archivePaths = db.prepare(`
    SELECT archive_path, MAX(line_end) as max_line, COUNT(*) as exchange_count
    FROM exchanges
    GROUP BY archive_path
  `).all() as Array<{ archive_path: string; max_line: number; exchange_count: number }>;

  console.log(`Checking ${archivePaths.length} indexed sessions...`);

  let filesChecked = 0;
  let filesWithGaps = 0;
  let linesNewlyIndexed = 0;

  for (const row of archivePaths) {
    const { archive_path, max_line } = row;
    filesChecked++;

    if (!fs.existsSync(archive_path)) {
      console.warn(`  Skipping missing file: ${archive_path}`);
      continue;
    }

    const content = fs.readFileSync(archive_path, 'utf-8');
    const currentLines = content.split('\n').length;

    if (max_line >= currentLines) {
      continue; // fully indexed
    }

    console.log(`  Gap: ${archive_path}`);
    console.log(`    Indexed to line ${max_line}, file has ${currentLines} lines`);
    filesWithGaps++;

    // Extract project name from archive path (second-to-last path segment)
    const parts = archive_path.split('/');
    const project = parts[parts.length - 2] ?? 'unknown';

    // Parse the archive file to get all exchanges
    const exchanges = await parseConversation(archive_path, project, archive_path);

    // Filter to only new exchanges (past the last indexed line)
    const newExchanges = exchanges.filter(e => e.lineStart > max_line);

    if (newExchanges.length === 0) {
      console.log(`    No new exchanges found (whitespace-only growth?)`);
      continue;
    }

    console.log(`    Indexing ${newExchanges.length} new exchanges...`);

    for (const exchange of newExchanges) {
      const toolNames = exchange.toolCalls?.map(tc => tc.toolName);
      const embedding = await generateExchangeEmbedding(
        exchange.userMessage,
        exchange.assistantMessage,
        toolNames
      );
      insertExchange(db, exchange, embedding, toolNames);
      linesNewlyIndexed += (exchange.lineEnd - exchange.lineStart + 1);
    }

    console.log(`    Done.`);
  }

  db.close();

  console.log(`\n✅ Repair complete`);
  console.log(`   Sessions checked: ${filesChecked}`);
  console.log(`   Sessions with gaps: ${filesWithGaps}`);
  console.log(`   Lines newly indexed: ${linesNewlyIndexed}`);
}

repair().catch(error => {
  console.error(`Error: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
