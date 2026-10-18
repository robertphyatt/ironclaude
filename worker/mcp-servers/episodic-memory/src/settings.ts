/**
 * settings.ts — episodic-memory's own settings and the session classifier.
 *
 * File: <getSuperpowersDir()>/episodic-memory.json (EPISODIC_MEMORY_CONFIG_DIR
 * overrides the directory). Every key is optional; missing keys use
 * DEFAULT_SETTINGS. An array in the file REPLACES the default array.
 *
 * Nothing here names a person. "operator" means an interactive session: Claude
 * entrypoint `cli`, Codex originator `Codex Desktop` or `codex-tui`. It is derived
 * from transcript metadata, so it works for any operator.
 */
import fs from 'fs';
import path from 'path';
import readline from 'readline';
import { getSuperpowersDir } from './paths.js';

// 'archive-only': archived (and compacted) but never indexed, like subagent transcripts.
export type SessionClass = 'operator' | 'brain' | 'worker' | 'machine' | 'archive-only' | 'unknown';

export interface EpisodicSettings {
  maintain: {
    autoRun: boolean;          // default true for every install (L16); guards still apply
    liveWindowHours: number;   // archive files modified more recently are not compacted
    zstdLevel: number;
    maxRuntimeMinutes: number; // sync worker runtime budget (watchdog); lock staleness is 2x this
  };
  exclude: {
    machineProjectPatterns: string[];       // regexes on the project directory name
    machineFirstUserTextPrefixes: string[]; // first user message prefixes of machine sessions
    archiveSubagents: boolean;              // archive <proj>/<session>/subagents/*.jsonl (never indexed)
    archiveOnlyProjectPatterns: string[];   // regexes: archived + compacted, never indexed or summarized
  };
  classify: {
    brainProjectPatterns: string[];
    workerProjectPatterns: string[];
    operatorEntrypoints: string[];            // interactive surfaces
    automationEntrypoints: string[];          // exact automation values
    automationEntrypointPrefixes: string[];   // e.g. 'sdk-' (sdk-py, sdk-cli)
  };
  backup: {
    mirrorDir: string | null;  // off by default; local or mounted directory only
  };
}

export const DEFAULT_SETTINGS: EpisodicSettings = {
  maintain: { autoRun: true, liveWindowHours: 24, zstdLevel: 3, maxRuntimeMinutes: 120 },
  exclude: {
    machineProjectPatterns: [
      '--(ironclaude|tron)-grader$',  // Commander grader home (~/.ironclaude/grader)
      'claude-judge-',                // continuation-judge temp dirs
      '^-private-tmp($|-)',           // macOS /tmp scratch, including summarizer sessions
      '^-tmp($|-)',                   // Linux /tmp scratch
      '^-(private-)?var-folders-',    // macOS $TMPDIR test harnesses and judges
    ],
    machineFirstUserTextPrefixes: [
      'Output only your exact model id string and nothing else.',  // claude -p model probes (sdk-cli)
      'Review this change for security vulnerabilities.',          // automated security reviews (sdk-py)
    ],
    archiveSubagents: true,
    archiveOnlyProjectPatterns: [
      '-programmatically-control-claude-work-chunk-',  // automated knowledge-base work-chunk pipeline sessions
    ],
  },
  classify: {
    brainProjectPatterns: ['--(ironclaude|tron)-brain$'],  // Commander brain_cwd default ~/.ironclaude/brain
    workerProjectPatterns: ['--ironclaude-worktrees-'],   // managed worker worktrees
    operatorEntrypoints: ['cli', 'Codex Desktop', 'codex-tui'],
    automationEntrypoints: ['codex_exec', 'ironclaude'],
    automationEntrypointPrefixes: ['sdk-'],
  },
  backup: { mirrorDir: null },
};

export function getSettingsPath(): string {
  return path.join(getSuperpowersDir(), 'episodic-memory.json');
}

function merge<T extends object>(base: T, over: unknown): T {
  if (!over || typeof over !== 'object' || Array.isArray(over)) return base;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(over as Record<string, unknown>)) {
    if (!(key in out)) continue;
    const current = out[key];
    const bothObjects = current && typeof current === 'object' && !Array.isArray(current)
      && value && typeof value === 'object' && !Array.isArray(value);
    out[key] = bothObjects ? merge(current as object, value) : value;
  }
  return out as T;
}

export function loadSettings(): EpisodicSettings {
  const file = getSettingsPath();
  if (!fs.existsSync(file)) return DEFAULT_SETTINGS;
  try {
    return merge(DEFAULT_SETTINGS, JSON.parse(fs.readFileSync(file, 'utf-8')));
  } catch (error) {
    console.warn(`[episodic-memory] ${file} is not valid JSON; using default rules with automatic maintain disabled: ${error instanceof Error ? error.message : String(error)}`);
    return { ...DEFAULT_SETTINGS, maintain: { ...DEFAULT_SETTINGS.maintain, autoRun: false } };
  }
}

function matchesAny(value: string, patterns: string[]): boolean {
  return patterns.some(pattern => {
    try { return new RegExp(pattern).test(value); } catch { return false; }
  });
}

export function isMachineProject(project: string, settings: EpisodicSettings): boolean {
  return matchesAny(project, settings.exclude.machineProjectPatterns);
}

export function hasMachinePrefix(firstUserText: string | undefined, settings: EpisodicSettings): boolean {
  return !!firstUserText && settings.exclude.machineFirstUserTextPrefixes.some(p => firstUserText.startsWith(p));
}

export function isArchiveOnlyProject(project: string, settings: EpisodicSettings): boolean {
  return matchesAny(project, settings.exclude.archiveOnlyProjectPatterns);
}

/** Classes that never appear in the index (machine is also never archived). */
export const UNINDEXED_CLASSES: SessionClass[] = ['machine', 'archive-only'];

export function classifySession(
  s: { project: string; entrypoint?: string | null; firstUserText?: string },
  settings: EpisodicSettings
): SessionClass {
  if (isMachineProject(s.project, settings) || hasMachinePrefix(s.firstUserText, settings)) return 'machine';
  if (isArchiveOnlyProject(s.project, settings)) return 'archive-only';
  if (matchesAny(s.project, settings.classify.brainProjectPatterns)) return 'brain';
  if (matchesAny(s.project, settings.classify.workerProjectPatterns)) return 'worker';
  const ep = s.entrypoint;
  if (ep && settings.classify.operatorEntrypoints.includes(ep)) return 'operator';
  if (ep && (settings.classify.automationEntrypoints.includes(ep)
    || settings.classify.automationEntrypointPrefixes.some(p => ep.startsWith(p)))) return 'worker';
  return 'unknown'; // no provenance (older transcripts) or a surface not seen yet: never guessed
}

/** First user message text of a Claude transcript, scanning at most `maxLines` lines. */
export async function readFirstUserText(file: string, maxLines = 200): Promise<string | undefined> {
  const input = fs.createReadStream(file);
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  let n = 0;
  try {
    for await (const line of rl) {
      if (++n > maxLines) break;
      let rec: any;
      try { rec = JSON.parse(line); } catch { continue; }
      if (rec?.type !== 'user' || !rec.message) continue;
      const content = rec.message.content;
      if (typeof content === 'string') return content;
      if (Array.isArray(content)) {
        const text = content
          .filter((b: any) => b?.type === 'text' && typeof b.text === 'string')
          .map((b: any) => b.text).join('\n');
        if (text) return text;
      }
    }
  } finally {
    rl.close();
    input.destroy();
  }
  return undefined;
}
