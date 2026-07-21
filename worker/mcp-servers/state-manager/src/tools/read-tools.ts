/**
 * Read-only MCP tools for the State Manager.
 *
 * Five tools that query state without modifying it:
 *   - get_plan_status: full plan state for the current session
 *   - get_professional_mode: returns "undecided", "on", or "off"
 *   - check_workflow_ready: validates prerequisites for a target stage
 *   - is_design_consumed: checks if a design file has been consumed
 *   - get_plan_history: returns previous execution attempts for a design file
 */

import type Database from 'better-sqlite3';
import type { Session, WaveTask, PlanJson } from '../types.js';
import type { SessionIdentity } from '../session-identity.js';
import {
  verifyRuntimeActivation,
  type RuntimeFingerprintCapture,
  type RuntimeFingerprintExpectation,
} from '../runtime-fingerprint.js';
import { getSession, getWaveTasks, getPlanHistory, isDesignConsumed } from '../db.js';
import { canTransitionTo } from '../state-machine.js';
import path from 'path';
import os from 'os';
import fs from 'fs';

function requireSessionId(sessionId: string): string {
  if (!sessionId) {
    throw new Error('Resolved session ID is required before read tool handling');
  }
  return sessionId;
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

export const readToolDefinitions = [
  {
    name: 'get_plan_status',
    description:
      'Returns full plan state for the current session: plan_name, current_wave, workflow_stage, review_pending, circuit_breaker, and summaries of completed/pending wave tasks.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'get_professional_mode',
    description:
      'Returns the current professional mode setting: "undecided", "on", or "off".',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'check_workflow_ready',
    description:
      'Validates prerequisites for transitioning to a target workflow stage. Returns { ready: boolean, reason: string }.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        stage: {
          type: 'string' as const,
          enum: ['brainstorm', 'plan', 'execute'],
          description: 'Target stage to check readiness for.',
        },
      },
      required: ['stage'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'is_design_consumed',
    description:
      'Returns whether a design file has been consumed (used to generate a plan).',
    inputSchema: {
      type: 'object' as const,
      properties: {
        file: {
          type: 'string' as const,
          description: 'Path to the design file to check.',
        },
      },
      required: ['file'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'get_plan_history',
    description:
      'Returns previous execution attempts (including retreats) for a given design file.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        design_file: {
          type: 'string' as const,
          description: 'Path to the design file to look up history for.',
        },
      },
      required: ['design_file'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'run_diagnostics',
    description:
      'Run infrastructure health checks and report the provider-native startup runtime fingerprint. Optionally verifies exact expected runtime identity.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        expected_runtime: {
          type: 'object' as const,
          properties: {
            plugin_version: { type: 'string' as const },
            plugin_root: { type: 'string' as const },
            manifest_sha256: { type: 'string' as const },
            bundle_sha256: { type: 'string' as const },
            client: { type: 'string' as const, enum: ['claude', 'codex'] },
          },
          required: ['plugin_version', 'plugin_root', 'manifest_sha256', 'bundle_sha256', 'client'],
          additionalProperties: false,
        },
      },
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'get_resume_state',
    description:
      'Get full session state snapshot for post-compaction recovery. Returns workflow_stage, professional_mode, plan_name, plan_goal, current_wave, total_waves, and all wave tasks with status.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'get_testing_theatre_status',
    description:
      'Returns the testing_theatre_checked flag (0 or 1) for the current session. ' +
      'Used by the code-review skill to determine if testing-theatre-detection was invoked ' +
      'before assigning a final grade.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
];

// ---------------------------------------------------------------------------
// Read-only tool names (for routing in index.ts)
// ---------------------------------------------------------------------------

export const readToolNames = new Set(readToolDefinitions.map((t) => t.name));

// ---------------------------------------------------------------------------
// Tool handler
// ---------------------------------------------------------------------------

export function handleReadTool(
  name: string,
  args: Record<string, unknown>,
  db: Database.Database,
  sessionId: string,
  identity?: SessionIdentity,
  runtimeFingerprint?: RuntimeFingerprintCapture,
): { content: Array<{ type: string; text: string }> } {
  const resolvedId = requireSessionId(sessionId);

  switch (name) {
    // ----- get_plan_status -----
    case 'get_plan_status': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return {
          content: [{ type: 'text', text: JSON.stringify({ error: 'Session not found', session_id: resolvedId }) }],
        };
      }

      // Gather wave tasks for this session
      const allTasks = getWaveTasks(db, resolvedId);

      const completedTasks = allTasks.filter(
        (t) => t.status === 'review_passed'
      );
      const pendingTasks = allTasks.filter(
        (t) => t.status !== 'review_passed'
      );

      const result = {
        plan_name: session.plan_name,
        current_wave: session.current_wave,
        workflow_stage: session.workflow_stage,
        review_pending: session.review_pending === 1,
        circuit_breaker: session.circuit_breaker,
        completed_tasks: completedTasks.map((t) => ({
          task_id: t.task_id,
          wave_number: t.wave_number,
          task_name: t.task_name,
          status: t.status,
        })),
        pending_tasks: pendingTasks.map((t) => ({
          task_id: t.task_id,
          wave_number: t.wave_number,
          task_name: t.task_name,
          status: t.status,
        })),
        session_id: resolvedId,
      };

      return {
        content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
      };
    }

    // ----- get_professional_mode -----
    case 'get_professional_mode': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return {
          content: [{ type: 'text', text: JSON.stringify({ professional_mode: 'undecided', note: 'Session not found, returning default' }) }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({ professional_mode: session.professional_mode }),
          },
        ],
      };
    }

    // ----- check_workflow_ready -----
    case 'check_workflow_ready': {
      const stage = args.stage as string;

      // Map friendly names to internal workflow stage names
      const stageMap: Record<string, string> = {
        brainstorm: 'brainstorming',
        plan: 'plan_ready',
        execute: 'executing',
      };

      const targetStage = stageMap[stage];
      if (!targetStage) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                ready: false,
                reason: `Unknown stage: "${stage}". Must be one of: brainstorm, plan, execute`,
              }),
            },
          ],
        };
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                ready: false,
                reason: `Session not found: ${resolvedId}`,
              }),
            },
          ],
        };
      }

      // Check prerequisites based on the target stage
      if (targetStage === 'brainstorming') {
        if (canTransitionTo(session.workflow_stage, 'brainstorming')) {
          return {
            content: [
              {
                type: 'text',
                text: JSON.stringify({
                  ready: true,
                  reason: `Can transition from ${session.workflow_stage} to brainstorming`,
                }),
              },
            ],
          };
        }
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                ready: false,
                reason: `Cannot transition from ${session.workflow_stage} to brainstorming`,
              }),
            },
          ],
        };
      }

      if (targetStage === 'plan_ready') {
        if (!canTransitionTo(session.workflow_stage, 'plan_ready')) {
          return {
            content: [
              {
                type: 'text',
                text: JSON.stringify({
                  ready: false,
                  reason: `Cannot transition from ${session.workflow_stage} to plan_ready`,
                }),
              },
            ],
          };
        }

        // Check if there is a consumed design
        const consumedDesign = db
          .prepare(
            `SELECT design_file FROM registered_designs WHERE terminal_session = ? AND consumed = 1 LIMIT 1`
          )
          .get(resolvedId) as { design_file: string } | undefined;

        if (!consumedDesign) {
          return {
            content: [
              {
                type: 'text',
                text: JSON.stringify({
                  ready: false,
                  reason: 'Design has not been consumed yet',
                }),
              },
            ],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                ready: true,
                reason: `Can transition from ${session.workflow_stage} to plan_ready, design is consumed`,
              }),
            },
          ],
        };
      }

      if (targetStage === 'executing') {
        const issues: string[] = [];

        if (!canTransitionTo(session.workflow_stage, 'executing')) {
          issues.push(
            `Cannot transition from ${session.workflow_stage} to executing`
          );
        }

        if (!session.plan_json) {
          issues.push('No plan has been created');
        }

        if (issues.length > 0) {
          return {
            content: [
              {
                type: 'text',
                text: JSON.stringify({
                  ready: false,
                  reason: issues.join('; '),
                }),
              },
            ],
          };
        }

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                ready: true,
                reason: `Can transition from ${session.workflow_stage} to executing, plan exists`,
              }),
            },
          ],
        };
      }

      // Should not reach here
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              ready: false,
              reason: `Unhandled target stage: ${targetStage}`,
            }),
          },
        ],
      };
    }

    // ----- is_design_consumed -----
    case 'is_design_consumed': {
      const file = args.file as string;
      if (!file) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({ error: 'Missing required parameter: file' }),
            },
          ],
        };
      }

      const consumed = isDesignConsumed(db, file);
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({ file, consumed }),
          },
        ],
      };
    }

    // ----- get_plan_history -----
    case 'get_plan_history': {
      const designFile = args.design_file as string;
      if (!designFile) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                error: 'Missing required parameter: design_file',
              }),
            },
          ],
        };
      }

      const history = getPlanHistory(db, designFile);

      const result = {
        design_file: designFile,
        attempts: history.map((h) => ({
          plan_name: h.plan_name,
          completed_tasks: h.completed_tasks,
          total_tasks: h.total_tasks,
          retreat_reason: h.retreat_reason,
          created_at: h.created_at,
        })),
        total_attempts: history.length,
      };

      return {
        content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
      };
    }

    // ----- run_diagnostics -----
    case 'run_diagnostics': {
      const results: Array<{test: string, status: string, detail: string}> = [];

      // Test 0: Per-call provider-native identity resolved
      if (!identity || identity.sessionId !== resolvedId) {
        throw new Error('Resolved session identity context is required for diagnostics');
      }
      results.push({
        test: 'Session identity resolved',
        status: 'PASS',
        detail: `client=${identity.client}, session=${identity.sessionId}, source=${identity.source}, invocation=${identity.invocationThreadId ?? 'root'}`,
      });

      // Test 1: DB file exists
      const dbPath = path.join(os.homedir(), '.claude', 'ironclaude.db');
      const dbExists = fs.existsSync(dbPath);
      results.push({ test: 'DB file exists', status: dbExists ? 'PASS' : 'FAIL', detail: dbPath });

      // Test 2: WAL mode
      let walOk = false;
      try {
        const mode = db.pragma('journal_mode', { simple: true }) as string;
        walOk = mode === 'wal';
        results.push({ test: 'WAL mode active', status: walOk ? 'PASS' : 'FAIL', detail: `journal_mode=${mode}` });
      } catch (e) {
        results.push({ test: 'WAL mode active', status: 'FAIL', detail: String(e) });
      }

      // Test 3: Session row exists
      let sessionExists = false;
      try {
        const row = db.prepare('SELECT COUNT(*) as cnt FROM sessions WHERE terminal_session = ?').get(resolvedId) as {cnt: number} | undefined;
        sessionExists = (row?.cnt ?? 0) > 0;
        results.push({ test: 'Session row exists', status: sessionExists ? 'PASS' : 'FAIL', detail: `session=${resolvedId}, count=${row?.cnt}` });
      } catch (e) {
        results.push({ test: 'Session row exists', status: 'FAIL', detail: String(e) });
      }

      // Test 4: Read professional_mode
      try {
        const row = db.prepare('SELECT professional_mode FROM sessions WHERE terminal_session = ?').get(resolvedId) as {professional_mode: string} | undefined;
        results.push({ test: 'Read professional_mode', status: row ? 'PASS' : 'FAIL', detail: `value=${row?.professional_mode}` });
      } catch (e) {
        results.push({ test: 'Read professional_mode', status: 'FAIL', detail: String(e) });
      }

      // Test 5: Read workflow_stage
      try {
        const row = db.prepare('SELECT workflow_stage FROM sessions WHERE terminal_session = ?').get(resolvedId) as {workflow_stage: string} | undefined;
        results.push({ test: 'Read workflow_stage', status: row ? 'PASS' : 'FAIL', detail: `value=${row?.workflow_stage}` });
      } catch (e) {
        results.push({ test: 'Read workflow_stage', status: 'FAIL', detail: String(e) });
      }

      // Tests 6-8: transactional write/read-back/audit probe. Always emit all
      // three named checks and roll back, even when an earlier operation fails.
      const diagTs = new Date().toISOString();
      let savepointActive = false;
      try {
        db.exec('SAVEPOINT ironclaude_diagnostics');
        savepointActive = true;
      } catch (e) {
        const detail = `Could not open diagnostics savepoint: ${String(e)}`;
        results.push({ test: 'Write test (updated_at)', status: 'FAIL', detail });
        results.push({ test: 'Read-back verification', status: 'FAIL', detail });
        results.push({ test: 'Audit log writable', status: 'FAIL', detail });
      }

      if (savepointActive) {
        try {
          const info = db.prepare('UPDATE sessions SET updated_at = ? WHERE terminal_session = ?').run(diagTs, resolvedId);
          results.push({ test: 'Write test (updated_at)', status: info.changes > 0 ? 'PASS' : 'FAIL', detail: `changes=${info.changes}` });
        } catch (e) {
          results.push({ test: 'Write test (updated_at)', status: 'FAIL', detail: String(e) });
        }

        try {
          const row = db.prepare('SELECT updated_at FROM sessions WHERE terminal_session = ?').get(resolvedId) as {updated_at: string} | undefined;
          const matches = row?.updated_at === diagTs;
          results.push({ test: 'Read-back verification', status: matches ? 'PASS' : 'FAIL', detail: `expected=${diagTs}, got=${row?.updated_at}` });
        } catch (e) {
          results.push({ test: 'Read-back verification', status: 'FAIL', detail: String(e) });
        }

        try {
          const auditInfo = db.prepare("INSERT INTO audit_log (terminal_session, actor, action, old_value, new_value, context) VALUES (?, ?, ?, ?, ?, ?)").run(resolvedId, 'diagnostics', 'diag_test', null, diagTs, null);
          results.push({ test: 'Audit log writable', status: auditInfo.changes > 0 ? 'PASS' : 'FAIL', detail: `changes=${auditInfo.changes}` });
        } catch (e) {
          results.push({ test: 'Audit log writable', status: 'FAIL', detail: String(e) });
        }

        try {
          db.exec('ROLLBACK TO ironclaude_diagnostics');
        } finally {
          db.exec('RELEASE ironclaude_diagnostics');
        }
      }

      // Test 9: No stale port/token files
      const portExists = fs.existsSync(path.join(os.homedir(), '.claude', '.hook-port'));
      const tokenExists = fs.existsSync(path.join(os.homedir(), '.claude', '.hook-token'));
      results.push({ test: 'No stale port/token files', status: (!portExists && !tokenExists) ? 'PASS' : 'WARN', detail: `port=${portExists}, token=${tokenExists}` });

      // Test 10: provider-specific identity transport
      if (identity.client === 'claude') {
        const claudePpid = process.env.CLAUDE_PPID ?? String(process.ppid);
        const ppidFile = path.join(os.homedir(), '.claude', `ironclaude-session-${claudePpid}.id`);
        const ppidFileExists = fs.existsSync(ppidFile);
        let ppidFileDetail = `CLAUDE_PPID=${process.env.CLAUDE_PPID ?? 'NOT SET'}, path=${ppidFile}, exists=${ppidFileExists}`;
        if (ppidFileExists) {
          try {
            const content = fs.readFileSync(ppidFile, 'utf-8').trim();
            ppidFileDetail += `, content=${content}`;
          } catch {
            ppidFileDetail += ', content=UNREADABLE';
          }
        }
        results.push({ test: 'PPID file exists (MCP-only)', status: ppidFileExists ? 'PASS' : 'WARN', detail: ppidFileDetail });
      } else {
        results.push({
          test: 'Codex metadata binding',
          status: 'PASS',
          detail: `client=codex, session=${identity.sessionId}, source=${identity.source}`,
        });
      }

      if (runtimeFingerprint?.ok) {
        results.push({
          test: 'Runtime fingerprint',
          status: 'PASS',
          detail: `client=${runtimeFingerprint.runtime.client}, version=${runtimeFingerprint.runtime.plugin_version}, root=${runtimeFingerprint.runtime.plugin_root}`,
        });
      } else {
        results.push({
          test: 'Runtime fingerprint',
          status: 'FAIL',
          detail: runtimeFingerprint?.error ?? 'Runtime fingerprint capture is missing',
        });
      }

      const expectedRuntime = args.expected_runtime as RuntimeFingerprintExpectation | undefined;
      if (expectedRuntime !== undefined) {
        const verification = verifyRuntimeActivation(runtimeFingerprint, expectedRuntime);
        results.push({
          test: 'Runtime activation match',
          status: verification.ok ? 'PASS' : 'FAIL',
          detail: verification.ok ? 'All expected runtime fields match startup capture' : verification.errors.join('; '),
        });
      }

      const passed = results.filter(r => r.status === 'PASS').length;
      const total = results.length;
      const summary = results.map(r => `${r.test} ... ${r.status} (${r.detail})`).join('\n');

      return {
        content: [{
          type: 'text',
          text: `Diagnostics: ${passed}/${total} PASSED\n\n${summary}\n\n${JSON.stringify({
            results,
            passed,
            total,
            ...(runtimeFingerprint?.ok ? { runtime: runtimeFingerprint.runtime } : {}),
          }, null, 2)}`,
        }],
      };
    }

    // ----- get_resume_state -----
    case 'get_resume_state': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return {
          content: [{ type: 'text', text: JSON.stringify({ error: 'Session not found', session_id: resolvedId }) }],
        };
      }

      const allTasks = getWaveTasks(db, resolvedId);

      const totalWavesRow = db.prepare(
        'SELECT MAX(wave_number) as max_wave FROM wave_tasks WHERE terminal_session = ?'
      ).get(resolvedId) as { max_wave: number | null };

      let plan_goal: string | null = null;
      if (session.plan_json) {
        try {
          const planObj = JSON.parse(session.plan_json);
          plan_goal = (planObj as { goal?: string }).goal ?? null;
        } catch {
          plan_goal = null;
        }
      }

      const result = {
        workflow_stage: session.workflow_stage,
        professional_mode: session.professional_mode,
        plan_name: session.plan_name ?? null,
        plan_goal,
        current_wave: session.current_wave,
        total_waves: totalWavesRow?.max_wave ?? null,
        tasks: allTasks.map((t) => ({
          id: t.task_id,
          name: t.task_name,
          wave: t.wave_number,
          status: t.status,
        })),
        session_id: resolvedId,
      };

      return {
        content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
      };
    }

    // ----- get_testing_theatre_status -----
    case 'get_testing_theatre_status': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return {
          content: [{ type: 'text', text: JSON.stringify({ error: 'Session not found', session_id: resolvedId }) }],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              testing_theatre_checked: session.testing_theatre_checked,
              session_id: resolvedId,
            }),
          },
        ],
      };
    }

    default:
      throw new Error(`Unknown read tool: ${name}`);
  }
}
