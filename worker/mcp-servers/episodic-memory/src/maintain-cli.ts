import fs from 'fs';
import { pathToFileURL } from 'url';
import { runMaintain, PHASES, Phase } from './maintain.js';
import { acquireLock, releaseLock, getLockPath } from './sync-lock.js';
import { loadSettings } from './settings.js';

export const USAGE = `Usage: episodic-memory maintain [--dry-run] [--only=slim|backfill|compact|mirror] [--keep-bak]

Reclaims episodic-memory disk space without losing information:
  slim      one-time rebuild of a legacy index without the unused tool_calls table and
            machine-session rows (needs free disk >= 2x the slim size; keeps db.sqlite.bak)
  backfill  one-time indexing of AskUserQuestion answers and session provenance
  compact   compress archived transcripts that are no longer live to .jsonl.zst
            (sha256-verified; restore any file with: zstd -d <file>.jsonl.zst)
  mirror    copy verified .zst files to backup.mirrorDir, if configured

OPTIONS:
  --dry-run       Report what each phase would do; change nothing
  --only=PHASE    Run one phase
  --keep-bak      Keep db.sqlite.bak (otherwise a manual run removes it once the slim index verifies)
`;

export async function main(argv: string[]): Promise<number> {
  if (argv.includes('--help') || argv.includes('-h')) { console.log(USAGE); return 0; }
  let only: Phase | undefined;
  for (const arg of argv) {
    if (arg === '--dry-run' || arg === '--keep-bak') continue;
    const phase = arg.startsWith('--only=') ? arg.slice('--only='.length) : '';
    if (phase && (PHASES as string[]).includes(phase)) { only = phase as Phase; continue; }
    console.error(`Unknown option: ${arg}\n\n${USAGE}`);
    return 2;
  }
  const settings = loadSettings();
  const lockPath = getLockPath();
  const lock = acquireLock(lockPath, settings.maintain.maxRuntimeMinutes * 2 * 60_000, 'cli');
  if (!lock.acquired) {
    console.log(`Another episodic-memory sync or maintain run holds ${lockPath} (PID ${lock.holder?.pid ?? 'unknown'}); try again later.`);
    return 3;
  }
  try {
    const reports = await runMaintain({
      dryRun: argv.includes('--dry-run'), keepBackup: argv.includes('--keep-bak'), only, invokedBy: 'cli', settings,
    });
    for (const r of reports) console.log(`${r.phase}: ${r.action} — ${r.detail}`);
    return reports.some(r => r.action === 'error') ? 1 : 0;
  } finally {
    releaseLock(lockPath);
  }
}

const entry = process.argv[1];
if (entry && fs.existsSync(entry) && import.meta.url === pathToFileURL(fs.realpathSync(entry)).href) {
  main(process.argv.slice(2)).then(code => process.exit(code), error => { console.error(error); process.exit(1); });
}
