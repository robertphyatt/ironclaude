import { describe, it, expect } from 'vitest';
import { mungeCwd } from './codex-paths.js';

describe('mungeCwd', () => {
  it('reproduces the real Claude project-dir key for a known cwd', () => {
    // Grounded from `ls ~/.claude/projects` (2026-07-21).
    expect(mungeCwd('/Users/roberthyatt/Code/ironclaude'))
      .toBe('-Users-roberthyatt-Code-ironclaude');
  });

  it('maps both "/" and "." to "-" per character (so "/." becomes "--")', () => {
    // Real key observed: -Users-roberthyatt--ironclaude  (cwd /Users/roberthyatt/.ironclaude)
    expect(mungeCwd('/Users/roberthyatt/.ironclaude'))
      .toBe('-Users-roberthyatt--ironclaude');
  });
});
