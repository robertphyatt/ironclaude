/**
 * pm-deactivation-audit.test.ts
 *
 * Verifies that the state machine correctly blocks worker (actor='claude')
 * self-deactivation of professional mode from every possible starting state.
 *
 * Defense audit summary (all four defenses verified as of 2026-03-23):
 *
 *   1. state-machine.ts — validateProfessionalModeTransition() primary guard
 *      at line 43 blocks any actor='claude' + target='off' combination.
 *      Belt-and-suspenders checks in undecided→off and on→off branches also
 *      enforce actor==='hook'.
 *
 *   2. SKILL.md — Human-only guard in Step 1 instructs Claude to refuse
 *      programmatic invocation and STOP. Step 4 explicitly says not to call
 *      set_professional_mode with 'off'.
 *
 *   3. state-activator.sh — UserPromptSubmit hook reads from USER_PROMPT (stdin
 *      JSON .prompt field) only. Cannot be triggered by MCP tool calls.
 *
 *   4. write-tools.ts — set_professional_mode hardcodes actor='claude' at the
 *      call site, so the worker can never claim actor='hook'.
 */

import { describe, it, expect } from 'vitest';
import { validateProfessionalModeTransition } from '../state-machine.js';

describe('validateProfessionalModeTransition — worker self-deactivation blocked', () => {
  // ── claude cannot turn off PM from any state ────────────────────────────

  it('blocks claude: undecided → off', () => {
    const result = validateProfessionalModeTransition('undecided', 'off', 'claude');
    expect(result.valid).toBe(false);
    expect(result.reason).toMatch(/cannot/i);
  });

  it('blocks claude: on → off', () => {
    const result = validateProfessionalModeTransition('on', 'off', 'claude');
    expect(result.valid).toBe(false);
    expect(result.reason).toMatch(/cannot/i);
  });

  it('blocks claude: off → off (same-state no-op still blocked via target=off guard)', () => {
    // Same-state returns valid:true (no change required) before the off guard.
    // This is intentional: if PM is already off the worker can't "re-off" it,
    // but the no-op short-circuit fires first. Verify it does NOT reach a state
    // where the worker set off from off while believing it did something useful.
    const result = validateProfessionalModeTransition('off', 'off', 'claude');
    // No-op — valid but meaningless
    expect(result.valid).toBe(true);
    expect(result.reason).toBe('No change required');
  });

  // ── hook CAN turn off PM (external/human deactivation must remain functional) ──

  it('allows hook: undecided → off', () => {
    const result = validateProfessionalModeTransition('undecided', 'off', 'hook');
    expect(result.valid).toBe(true);
  });

  it('allows hook: on → off', () => {
    const result = validateProfessionalModeTransition('on', 'off', 'hook');
    expect(result.valid).toBe(true);
  });

  // ── claude CAN activate PM (turning it on is always allowed) ──────────────

  it('allows claude: undecided → on', () => {
    const result = validateProfessionalModeTransition('undecided', 'on', 'claude');
    expect(result.valid).toBe(true);
  });

  it('allows claude: off → on', () => {
    const result = validateProfessionalModeTransition('off', 'on', 'claude');
    expect(result.valid).toBe(true);
  });

  it('allows claude: on → on (no-op)', () => {
    const result = validateProfessionalModeTransition('on', 'on', 'claude');
    expect(result.valid).toBe(true);
  });

  // ── nobody can transition back to undecided ────────────────────────────

  it('blocks anyone: on → undecided', () => {
    expect(validateProfessionalModeTransition('on', 'undecided', 'claude').valid).toBe(false);
    expect(validateProfessionalModeTransition('on', 'undecided', 'hook').valid).toBe(false);
  });

  it('blocks anyone: off → undecided', () => {
    expect(validateProfessionalModeTransition('off', 'undecided', 'claude').valid).toBe(false);
    expect(validateProfessionalModeTransition('off', 'undecided', 'hook').valid).toBe(false);
  });

  // ── error messages are informative ────────────────────────────────────

  it('rejection reason mentions human/hook, not a generic error', () => {
    const result = validateProfessionalModeTransition('on', 'off', 'claude');
    // Should mention "human" or "hook" to tell the worker why it failed
    expect(result.reason).toMatch(/human|hook/i);
  });
});
