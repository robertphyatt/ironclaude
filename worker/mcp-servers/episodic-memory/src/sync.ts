import fs from 'fs';
import path from 'path';
import { SUMMARIZER_CONTEXT_MARKER } from './constants.js';
import { getExcludedProjects } from './paths.js';
import { archiveNeedsCopy, copyIntoArchive, readArchiveText } from './archive-io.js';
import { loadSettings, isMachineProject, isArchiveOnlyProject, hasMachinePrefix, classifySession, readFirstUserText } from './settings.js';

const EXCLUSION_MARKERS = [
  '<INSTRUCTIONS-TO-EPISODIC-MEMORY>DO NOT INDEX THIS CHAT</INSTRUCTIONS-TO-EPISODIC-MEMORY>',
  'Only use NO_INSIGHTS_FOUND',
  SUMMARIZER_CONTEXT_MARKER,
];

function shouldSkipConversation(filePath: string): boolean {
  try {
    // Raw or compacted (.jsonl.zst): a compacted marked file must stay unsummarized.
    const content = readArchiveText(filePath);
    return EXCLUSION_MARKERS.some(marker => content.includes(marker));
  } catch (error) {
    // If we can't read the file, don't skip it
    return false;
  }
}

export interface SyncResult {
  copied: number;
  skipped: number;
  indexed: number;
  summarized: number;
  excluded: number;  // machine sessions: never archived, never indexed (L9)
  errors: Array<{ file: string; error: string }>;
}

export interface SyncOptions {
  skipIndex?: boolean;
  skipSummaries?: boolean;
  summaryLimit?: number; // Max summaries to generate per run (default: 10)
}

function extractSessionIdFromPath(filePath: string): string | null {
  // Extract session ID from filename: /path/to/abc-123-def.jsonl -> abc-123-def
  const basename = path.basename(filePath, '.jsonl');
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(basename)) {
    return basename;
  }
  return null;
}

/** Archive <project>/<session>/subagents/**.jsonl. Archived (and later compacted), never indexed (L10). */
function archiveSubagents(projectPath: string, destProjectDir: string, result: SyncResult): void {
  for (const entry of fs.readdirSync(projectPath, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const subRoot = path.join(projectPath, entry.name, 'subagents');
    if (!fs.existsSync(subRoot)) continue;
    const stack = [subRoot];
    while (stack.length) {
      const dir = stack.pop()!;
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) { stack.push(full); continue; }
        if (!e.isFile() || !e.name.endsWith('.jsonl')) continue;
        try {
          if (copyIntoArchive(full, path.join(destProjectDir, path.relative(projectPath, full)))) result.copied++;
          else result.skipped++;
        } catch (error) {
          result.errors.push({ file: full, error: error instanceof Error ? error.message : String(error) });
        }
      }
    }
  }
}

export async function syncConversations(
  sourceDir: string,
  destDir: string,
  options: SyncOptions = {}
): Promise<SyncResult> {
  const result: SyncResult = { copied: 0, skipped: 0, indexed: 0, summarized: 0, excluded: 0, errors: [] };

  if (!fs.existsSync(sourceDir)) {
    return result;
  }

  const settings = loadSettings();
  const filesToIndex: string[] = [];
  const filesToSummarize: Array<{ path: string; sessionId: string }> = [];

  const projects = fs.readdirSync(sourceDir);
  const excludedProjects = getExcludedProjects();

  for (const project of projects) {
    if (excludedProjects.includes(project) || isMachineProject(project, settings)) {
      result.excluded++;
      continue;
    }

    const projectPath = path.join(sourceDir, project);
    const stat = fs.statSync(projectPath);
    if (!stat.isDirectory()) continue;

    const files = fs.readdirSync(projectPath).filter(f => f.endsWith('.jsonl'));
    // Archive-only projects (L16): archived and later compacted, never indexed or summarized.
    const archiveOnly = isArchiveOnlyProject(project, settings);

    for (const file of files) {
      const srcFile = path.join(projectPath, file);
      const destFile = path.join(destDir, project, file);

      try {
        if (archiveNeedsCopy(srcFile, destFile)) {
          // Probe and automated-review sessions are machine traffic: never archived.
          if (hasMachinePrefix(await readFirstUserText(srcFile), settings)) {
            result.excluded++;
            continue;
          }
          copyIntoArchive(srcFile, destFile);
          result.copied++;
          if (!archiveOnly) filesToIndex.push(destFile);
        } else {
          result.skipped++;
        }

        // Check if this file needs a summary (whether newly copied or existing)
        if (!options.skipSummaries && !archiveOnly) {
          const summaryPath = destFile.replace('.jsonl', '-summary.txt');
          if (!fs.existsSync(summaryPath) && !shouldSkipConversation(destFile)) {
            const sessionId = extractSessionIdFromPath(destFile);
            if (sessionId) {
              filesToSummarize.push({ path: destFile, sessionId });
            }
          }
        }
      } catch (error) {
        result.errors.push({
          file: srcFile,
          error: error instanceof Error ? error.message : String(error)
        });
      }
    }

    if (settings.exclude.archiveSubagents) {
      archiveSubagents(projectPath, path.join(destDir, project), result);
    }
  }

  // Index copied files (unless skipIndex is set)
  if (!options.skipIndex && filesToIndex.length > 0) {
    const { initDatabase, insertExchange } = await import('./db.js');
    const { initEmbeddings, generateExchangeEmbedding } = await import('./embeddings.js');
    const { parseConversation } = await import('./parser.js');

    const db = initDatabase();
    await initEmbeddings();

    for (const file of filesToIndex) {
      try {
        if (shouldSkipConversation(file)) {
          continue; // Skip indexing but file is already copied
        }

        const project = path.basename(path.dirname(file));
        const exchanges = await parseConversation(file, project, file);
        const sessionClass = classifySession({
          project,
          entrypoint: exchanges[0]?.entrypoint,
          firstUserText: exchanges[0]?.userMessage,
        }, settings);
        if (sessionClass === 'machine') {
          result.excluded++;
          continue;
        }
        if (sessionClass === 'archive-only') continue;

        for (const exchange of exchanges) {
          exchange.sessionClass = sessionClass;
          const toolNames = exchange.toolCalls?.map(tc => tc.toolName);
          const embedding = await generateExchangeEmbedding(
            exchange.userMessage,
            exchange.assistantMessage,
            toolNames
          );
          insertExchange(db, exchange, embedding, toolNames);
        }

        result.indexed++;
      } catch (error) {
        result.errors.push({
          file,
          error: error instanceof Error ? error.message : String(error)
        });
      }
    }

    db.close();
  }

  // Generate summaries for files that need them
  if (!options.skipSummaries && filesToSummarize.length > 0) {
    const { parseConversation } = await import('./parser.js');
    const { summarizeConversation } = await import('./summarizer.js');

    const summaryLimit = options.summaryLimit ?? 10;
    const toSummarize = filesToSummarize.slice(0, summaryLimit);
    const remaining = filesToSummarize.length - toSummarize.length;

    console.log(`Generating summaries for ${toSummarize.length} conversation(s)...`);
    if (remaining > 0) {
      console.log(`  (${remaining} more need summaries - will process on next sync)`);
    }

    for (const { path: filePath } of toSummarize) {
      try {
        const project = path.basename(path.dirname(filePath));
        const exchanges = await parseConversation(filePath, project, filePath);

        if (exchanges.length === 0) {
          continue; // Skip empty conversations
        }

        console.log(`  Summarizing ${path.basename(filePath)} (${exchanges.length} exchanges)...`);
        const summary = await summarizeConversation(exchanges);

        const summaryPath = filePath.replace('.jsonl', '-summary.txt');
        fs.writeFileSync(summaryPath, summary, 'utf-8');
        result.summarized++;
      } catch (error) {
        result.errors.push({
          file: filePath,
          error: `Summary generation failed: ${error instanceof Error ? error.message : String(error)}`
        });
      }
    }
  }

  return result;
}
