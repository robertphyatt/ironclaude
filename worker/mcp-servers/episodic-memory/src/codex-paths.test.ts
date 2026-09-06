import { describe, it, expect } from 'vitest';
import { mungeCwd } from './codex-paths.js';

describe('mungeCwd', () => {
  it('reproduces the real Claude project-dir key for a known cwd', () => {
    // Grounded from `ls ~/.claude/projects` (2026-07-21).
    expect(mungeCwd('/Users/example/Code/ironclaude'))
      .toBe('-Users-example-Code-ironclaude');
  });

  it('maps both "/" and "." to "-" per character (so "/." becomes "--")', () => {
    // Real key observed: -Users-example--ironclaude  (cwd /Users/example/.ironclaude)
    expect(mungeCwd('/Users/example/.ironclaude'))
      .toBe('-Users-example--ironclaude');
  });
});
