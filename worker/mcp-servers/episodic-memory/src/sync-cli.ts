import { syncConversations } from './sync.js';
import { getArchiveDir, getIndexDir } from './paths.js';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { spawn } from 'child_process';
import { syncCodexConversations } from './codex-sync.js';
import { loadSettings } from './settings.js';
import { acquireLock, releaseLock, getLockPath, startWatchdog } from './sync-lock.js';
import { runMaintain, autoRunAllowed } from './maintain.js';

const args = process.argv.slice(2);

if (args.includes('--help') || args.includes('-h')) {
  console.log(`
Usage: episodic-memory sync [--background]

Sync conversations from ~/.claude/projects and ~/.codex/sessions to the archive and index them.

This command:
1. Copies new or updated .jsonl files to the conversation archive (machine sessions excluded)
2. Generates embeddings for semantic search
3. Updates the search index
4. Runs \`episodic-memory maintain\` when maintain.autoRun allows it

Only processes files that are new or have been modified since last sync.
Safe to run multiple times - subsequent runs are fast no-ops. One run at a time:
a second run while one holds the lock exits immediately.

OPTIONS:
  --background    Run sync in background (for hooks, returns immediately)

EXAMPLES:
  # Sync all new conversations
  episodic-memory sync

  # Sync in background (for hooks)
  episodic-memory sync --background
`);
  process.exit(0);
}

// If background mode, fork the process and exit immediately. The detached child's
// output goes to <index dir>/sync.log (rotated to sync.log.1 above 5 MB), so a
// background sync or maintain run leaves evidence.
if (args.includes('--background')) {
  const filteredArgs = args.filter(arg => arg !== '--background');
  const logPath = path.join(getIndexDir(), 'sync.log');
  try {
    if (fs.statSync(logPath).size > 5 * 1024 * 1024) fs.renameSync(logPath, `${logPath}.1`);
  } catch { /* no log yet */ }
  const out = fs.openSync(logPath, 'a');
  const child = spawn(process.execPath, [process.argv[1], ...filteredArgs], { detached: true, stdio: ['ignore', out, out] });
  fs.closeSync(out);
  child.unref();
  console.log(`Sync started in background (log: ${logPath})...`);
  process.exit(0);
}

const settings = loadSettings();
const lockPath = getLockPath();
const maxRuntimeMs = settings.maintain.maxRuntimeMinutes * 60_000;
const lock = acquireLock(lockPath, maxRuntimeMs * 2, 'sync');
if (!lock.acquired) {
  console.log(`Another episodic-memory sync or maintain run holds ${lockPath} (PID ${lock.holder?.pid ?? 'unknown'}); skipping.`);
  process.exit(0);
}

// A hung sync must not block every later session's sync: bound this run.
startWatchdog(maxRuntimeMs, () => {
  console.error(`Sync exceeded ${settings.maintain.maxRuntimeMinutes} minutes; exiting so the next session can sync.`);
  releaseLock(lockPath);
  process.exit(124);
});

const sourceDir = path.join(os.homedir(), '.claude', 'projects');
const codexSourceDir = path.join(os.homedir(), '.codex', 'sessions');
const destDir = getArchiveDir();

async function run(): Promise<void> {
  console.log(`=== sync ${new Date().toISOString()} (PID ${process.pid}) ===`);
  console.log('Syncing conversations...');
  console.log(`Source: ${sourceDir}`);
  console.log(`Destination: ${destDir}\n`);

  const result = await syncConversations(sourceDir, destDir);
  console.log(`\n✅ Sync complete!`);
  console.log(`  Copied: ${result.copied}`);
  console.log(`  Skipped: ${result.skipped}`);
  console.log(`  Excluded (machine): ${result.excluded}`);
  console.log(`  Indexed: ${result.indexed}`);
  console.log(`  Summarized: ${result.summarized}`);
  if (result.errors.length > 0) {
    console.log(`\n⚠️  Errors: ${result.errors.length}`);
    result.errors.forEach(err => console.log(`  ${err.file}: ${err.error}`));
  }

  console.log('\nSyncing Codex conversations...');
  console.log(`Source: ${codexSourceDir}`);
  const codexResult = await syncCodexConversations(codexSourceDir, destDir);
  console.log(`\n✅ Codex sync complete!`);
  console.log(`  Copied: ${codexResult.copied}`);
  console.log(`  Skipped: ${codexResult.skipped}`);
  console.log(`  Excluded (machine): ${codexResult.excluded}`);
  console.log(`  Indexed: ${codexResult.indexed}`);
  console.log(`  Summarized: ${codexResult.summarized}`);
  if (codexResult.errors.length > 0) {
    console.log(`\n⚠️  Codex errors: ${codexResult.errors.length}`);
    codexResult.errors.forEach(err => console.log(`  ${err.file}: ${err.error}`));
  }

  const gate = autoRunAllowed(settings);
  if (!gate.allowed) {
    console.log(`\nMaintain: skipped (${gate.reason})`);
    return;
  }
  console.log('\nRunning maintain...');
  const reports = await runMaintain({ invokedBy: 'sync', settings });
  for (const r of reports) console.log(`  ${r.phase}: ${r.action} — ${r.detail}`);
}

run()
  .then(() => { releaseLock(lockPath); process.exit(0); })
  .catch(error => { console.error('Error syncing:', error); releaseLock(lockPath); process.exit(1); });
