import fs from 'fs';
import path from 'path';
import { SyncResult, SyncOptions } from './sync.js';          // types only — sync.ts stays zero-diff
import { SUMMARIZER_CONTEXT_MARKER } from './constants.js';
import { mungeCwd } from './codex-paths.js';
import { parseCodexConversation } from './codex-parser.js';

const EXCLUSION_MARKERS = [
  '<INSTRUCTIONS-TO-EPISODIC-MEMORY>DO NOT INDEX THIS CHAT</INSTRUCTIONS-TO-EPISODIC-MEMORY>',
  'Only use NO_INSIGHTS_FOUND',
  SUMMARIZER_CONTEXT_MARKER,
];

function shouldSkip(filePath: string): boolean {
  try { return EXCLUSION_MARKERS.some(m => fs.readFileSync(filePath, 'utf-8').includes(m)); }
  catch { return false; }
}

// Re-implemented (NOT imported) so sync.ts stays zero-diff.
function copyIfNewer(src: string, dest: string): boolean {
  const destDir = path.dirname(dest);
  if (!fs.existsSync(destDir)) fs.mkdirSync(destDir, { recursive: true });
  if (fs.existsSync(dest) && fs.statSync(dest).mtimeMs >= fs.statSync(src).mtimeMs) return false;
  const tmp = dest + '.tmp.' + process.pid;
  fs.copyFileSync(src, tmp);
  fs.renameSync(tmp, dest);
  return true;
}

function walkRollouts(dir: string): string[] {
  const out: string[] = [];
  const stack = [dir];
  while (stack.length) {
    const d = stack.pop()!;
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) stack.push(full);
      else if (e.isFile() && /^rollout-.*\.jsonl$/.test(e.name)) out.push(full);
    }
  }
  return out;
}

function readSessionMeta(file: string): { sessionId?: string; cwd?: string } {
  try {
    const fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(65536);
    const bytes = fs.readSync(fd, buf, 0, buf.length, 0);
    fs.closeSync(fd);
    const firstLine = buf.subarray(0, bytes).toString('utf-8').split('\n')[0];
    const rec = JSON.parse(firstLine);
    if (rec.type === 'session_meta' && rec.payload) {
      return { sessionId: rec.payload.session_id, cwd: rec.payload.cwd };
    }
  } catch { /* fall through */ }
  return {};
}

function projectFor(file: string, cwd?: string): string {
  return cwd ? mungeCwd(cwd) : `codex-${path.basename(file, '.jsonl')}`;
}

export async function syncCodexConversations(
  sourceDir: string, destDir: string, options: SyncOptions = {}
): Promise<SyncResult> {
  const result: SyncResult = { copied: 0, skipped: 0, indexed: 0, summarized: 0, errors: [] };
  if (!fs.existsSync(sourceDir)) return result;

  const filesToIndex: string[] = [];
  const filesToSummarize: Array<{ path: string }> = [];

  for (const srcFile of walkRollouts(sourceDir)) {
    try {
      const meta = readSessionMeta(srcFile);
      const project = projectFor(srcFile, meta.cwd);
      const destFile = path.join(destDir, project, path.basename(srcFile));
      if (copyIfNewer(srcFile, destFile)) { result.copied++; filesToIndex.push(destFile); }
      else result.skipped++;
      if (!options.skipSummaries && meta.sessionId) {
        const summaryPath = destFile.replace('.jsonl', '-summary.txt');
        if (!fs.existsSync(summaryPath) && !shouldSkip(destFile)) filesToSummarize.push({ path: destFile });
      }
    } catch (error) {
      result.errors.push({ file: srcFile, error: error instanceof Error ? error.message : String(error) });
    }
  }

  if (!options.skipIndex && filesToIndex.length > 0) {
    const { initDatabase, insertExchange } = await import('./db.js');
    const { initEmbeddings, generateExchangeEmbedding } = await import('./embeddings.js');
    const db = initDatabase();
    await initEmbeddings();
    for (const file of filesToIndex) {
      try {
        if (shouldSkip(file)) continue;
        const meta = readSessionMeta(file);
        const exchanges = await parseCodexConversation(file, projectFor(file, meta.cwd), file);
        for (const ex of exchanges) {
          const toolNames = ex.toolCalls?.map(tc => tc.toolName);
          const embedding = await generateExchangeEmbedding(ex.userMessage, ex.assistantMessage, toolNames);
          insertExchange(db, ex, embedding, toolNames);
        }
        result.indexed++;
      } catch (error) {
        result.errors.push({ file, error: error instanceof Error ? error.message : String(error) });
      }
    }
    db.close();
  }

  if (!options.skipSummaries && filesToSummarize.length > 0) {
    const { summarizeConversation } = await import('./summarizer.js');
    const limit = options.summaryLimit ?? 10;
    for (const { path: filePath } of filesToSummarize.slice(0, limit)) {
      try {
        const meta = readSessionMeta(filePath);
        const exchanges = await parseCodexConversation(filePath, projectFor(filePath, meta.cwd), filePath);
        if (exchanges.length === 0) continue;
        const summary = await summarizeConversation(exchanges);
        fs.writeFileSync(filePath.replace('.jsonl', '-summary.txt'), summary, 'utf-8');
        result.summarized++;
      } catch (error) {
        result.errors.push({ file: filePath, error: `Summary generation failed: ${error instanceof Error ? error.message : String(error)}` });
      }
    }
  }
  return result;
}
