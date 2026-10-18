import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import {
  DEFAULT_SETTINGS, loadSettings, classifySession, isMachineProject, readFirstUserText, getSettingsPath,
} from './settings.js';

let tmp: string;
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'em-settings-'));
  process.env.EPISODIC_MEMORY_CONFIG_DIR = path.join(tmp, 'config');
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.EPISODIC_MEMORY_CONFIG_DIR;
});

const CASES: Array<[string, string | undefined, string]> = [
    ['-Users-alice--ironclaude-grader', undefined, 'machine'],
    ['-Users-alice--tron-grader', undefined, 'machine'],
    ['-private-var-folders-02-3n1lytx537v4l1dknbj7892w0000gp-T-claude-judge--3wdjdv1', undefined, 'machine'],
    ['-private-tmp', undefined, 'machine'],
    ['-private-tmp-claude-502--Users-alice-Code-app-scratchpad-probe', 'cli', 'machine'],
    ['-var-folders-02-abc-T-tmp-BRUONHDqhq', 'cli', 'machine'],
    ['-tmp-scratch', 'cli', 'machine'],
    ['-Users-alice--ironclaude-brain', undefined, 'brain'],
    ['-Users-alice-Code-repo--ironclaude-worktrees-352afa8c-9e57-42c1-bada-de222093de5c', 'sdk-py', 'worker'],
    ['-Users-alice-Code-repo', 'cli', 'operator'],
    ['-Users-alice-Code-repo', 'Codex Desktop', 'operator'],
    ['-Users-alice-Code-repo', 'codex-tui', 'operator'],
    ['-Users-alice-Code-repo', 'sdk-py', 'worker'],
    ['-Users-alice-Code-repo', 'codex_exec', 'worker'],
    ['-Users-alice-Code-repo', 'ironclaude', 'worker'],
    ['-Users-alice-Code-repo', 'some-new-ide', 'unknown'],
    ['-Users-alice-Code-repo', undefined, 'unknown'],
    ['-Users-alice-Code-autograder', 'cli', 'operator'],
    ['-Users-alice-Code-tmp-tools', 'cli', 'operator'],
    ['-Users-alice-Code-rp-knowledge-base-programmatically-control-claude-work-chunk-CHUNK-PZO2105-APG-112-131', 'sdk-py', 'archive-only'],
    ['-Users-alice-Code-rp-knowledge-base-programmatically-control-claude', 'cli', 'operator'],
];

describe('classifySession with the shipped defaults (real directory shapes, any user name)', () => {
  it.each(CASES)('%s + %s -> %s', (project, entrypoint, expected) => {
    expect(classifySession({ project, entrypoint }, DEFAULT_SETTINGS)).toBe(expected);
  });

  it('first-user-text prefixes mark probe and automated-review sessions as machine, and nothing else', () => {
    const p = '-Users-alice-Code-repo';
    expect(classifySession({ project: p, entrypoint: 'sdk-cli', firstUserText: 'Output only your exact model id string and nothing else.' }, DEFAULT_SETTINGS)).toBe('machine');
    expect(classifySession({ project: p, entrypoint: 'sdk-py', firstUserText: 'Review this change for security vulnerabilities.\n\nChanged files' }, DEFAULT_SETTINGS)).toBe('machine');
    expect(classifySession({ project: p, entrypoint: 'cli', firstUserText: 'Please review this change' }, DEFAULT_SETTINGS)).toBe('operator');
  });

  it('defaults name no user', () => {
    expect(JSON.stringify(DEFAULT_SETTINGS)).not.toContain(os.userInfo().username);
  });

  it('the automatic maintain run is on by default (L16)', () => {
    expect(DEFAULT_SETTINGS.maintain.autoRun).toBe(true);
    expect(loadSettings().maintain.autoRun).toBe(true);
  });
});

describe('loadSettings', () => {
  it('returns defaults when the file is absent; file arrays replace default arrays', () => {
    expect(getSettingsPath()).toBe(path.join(tmp, 'config', 'episodic-memory.json'));
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
    fs.writeFileSync(getSettingsPath(), JSON.stringify({
      exclude: { machineProjectPatterns: ['-scratchy$'] },
      maintain: { autoRun: false },
      backup: { mirrorDir: '/Volumes/Backup/em' },
    }));
    const s = loadSettings();
    expect(isMachineProject('-Users-a-scratchy', s)).toBe(true);
    expect(isMachineProject('-Users-a--ironclaude-grader', s)).toBe(false);
    expect(s.maintain.autoRun).toBe(false);
    expect(s.maintain.liveWindowHours).toBe(24);
    expect(s.backup.mirrorDir).toBe('/Volumes/Backup/em');
    expect(s.exclude.archiveSubagents).toBe(true);
  });

  it('invalid JSON keeps default rules but disables the automatic maintain run', () => {
    fs.mkdirSync(path.dirname(getSettingsPath()), { recursive: true });
    fs.writeFileSync(getSettingsPath(), '{not json');
    const s = loadSettings();
    expect(s.maintain.autoRun).toBe(false);
    expect(s.exclude).toEqual(DEFAULT_SETTINGS.exclude);
  });

  it('readFirstUserText returns the first user text and skips other records', async () => {
    const f = path.join(tmp, 's.jsonl');
    fs.writeFileSync(f, [
      JSON.stringify({ type: 'queue-operation', operation: 'enqueue' }),
      JSON.stringify({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', content: 'x' }] } }),
      JSON.stringify({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'Hello there' }] } }),
    ].join('\n') + '\n');
    expect(await readFirstUserText(f)).toBe('Hello there');
    const g = path.join(tmp, 'none.jsonl');
    fs.writeFileSync(g, JSON.stringify({ type: 'summary' }) + '\n');
    expect(await readFirstUserText(g)).toBeUndefined();
  });
});
