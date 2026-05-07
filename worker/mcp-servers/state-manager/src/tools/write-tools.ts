/**
 * Write MCP tools for the State Manager.
 *
 * Write tools that modify state with validation:
 *   - register_design: registers a design file
 *   - consume_design: marks a design as consumed, transitions to design_ready
 *   - create_plan: validates and stores a plan, computes Wave 1
 *   - start_execution: transitions from plan_ready to executing
 *   - get_next_tasks: returns next available tasks (computes next wave if needed)
 *   - submit_task: marks a wave task as submitted for review
 *   - retreat: retreats to brainstorming or debugging
 *   - reset_session: clears all execution state
 *   - set_professional_mode: sets professional mode (on/off) with state machine validation
 *   - set_log_session_ids: toggles session ID display in statusline and messages
 */

import type Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';
import os from 'os';
import type { PlanJson, Session, WaveTask, WorkflowStage } from '../types.js';
import {
  getSession,
  updateSession,
  upsertSession,
  upsertWaveTask,
  getWaveTasks,
  registerDesign as dbRegisterDesign,
  consumeDesign as dbConsumeDesign,
  getDesign,
  isDesignConsumed as dbIsDesignConsumed,
  insertAuditLog,
  updateWaveTaskStatus,
  insertReviewGrade,
  clearReviewGrades,
} from '../db.js';
import {
  validateWorkflowTransition,
  validateProfessionalModeTransition,
  validatePlanJson,
  computeNextWave,
  executeRetreat,
  canTransitionTo,
} from '../state-machine.js';

// ---------------------------------------------------------------------------
// Session ID management (mirrors read-tools.ts pattern)
// ---------------------------------------------------------------------------

let _currentSessionId: string | null = null;

export function setCurrentSession(sessionId: string): void {
  _currentSessionId = sessionId;
}

/**
 * Resolve the session ID to use. Hard-fails if no session ID is available.
 * NEVER falls back to "most recent session" — that silently uses the wrong session.
 */
function resolveSessionId(db: Database.Database, explicitId?: string): string {
  if (explicitId) {
    return explicitId;
  }
  if (_currentSessionId) {
    return _currentSessionId;
  }

  throw new Error(
    'No session ID available. ' +
    'CLAUDE_SESSION_ID env var not set and no explicit session_id provided. ' +
    `CLAUDE_SESSION_ID=${process.env.CLAUDE_SESSION_ID || 'unset'}. ` +
    'Check plugin.json env config and session-init.sh.'
  );
}

// ---------------------------------------------------------------------------
// Helper: build a standard success/error response
// ---------------------------------------------------------------------------

type ToolResult = { content: Array<{ type: string; text: string }> };

function ok(data: Record<string, unknown>): ToolResult {
  return { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] };
}

function err(message: string, extra?: Record<string, unknown>): ToolResult {
  return {
    content: [
      { type: 'text', text: JSON.stringify({ error: message, ...extra }, null, 2) },
    ],
  };
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

export const writeToolDefinitions = [
  {
    name: 'register_design',
    description:
      'Registers a design file for the current session. The session must exist and workflow must be brainstorming or idle.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        file: {
          type: 'string' as const,
          description: 'Path to the design file to register.',
        },
      },
      required: ['file'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'consume_design',
    description:
      'Consumes a registered design file. The design must exist and not yet be consumed. Transitions workflow to design_ready.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        file: {
          type: 'string' as const,
          description: 'Path to the design file to consume.',
        },
      },
      required: ['file'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'create_plan',
    description:
      'Creates a plan from validated plan JSON. Validates the plan schema and dependency graph, computes Wave 1, stores plan and wave tasks. Transitions workflow to plan_ready.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        plan_json: {
          type: 'object' as const,
          description: 'The full PlanJson object with name, goal, design_file, and tasks.',
        },
      },
      required: ['plan_json'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'start_execution',
    description:
      'Transitions from plan_ready to executing. Requires an existing plan.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'get_next_tasks',
    description:
      'Returns the next available tasks. If the current wave is fully reviewed, computes the next wave. Returns {status: "complete"} if all tasks are done, or {status: "wave_in_progress", pending: [...]} if the current wave is not yet complete.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'submit_task',
    description:
      'Marks a wave task as submitted for review. The task must be in the current wave and have status in_progress.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        task_id: {
          type: 'number' as const,
          description: 'The plan task ID to submit for review.',
        },
      },
      required: ['task_id'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'claim_task',
    description:
      'Transitions a wave task from pending to in_progress. Must be called before submit_task. The task must be in the current wave and have status pending.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        task_id: {
          type: 'number' as const,
          description: 'The plan task ID to claim.',
        },
      },
      required: ['task_id'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'record_review_verdict',
    description:
      'Records an A-F code review grade for the current wave. Called by code-review Skill after task-boundary reviews. GBTW reads the grade to advance or block submitted tasks.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        grade: {
          type: 'string' as const,
          enum: ['A', 'B', 'C', 'D', 'F'],
          description: 'The code review grade: A=excellent/no issues, B=minor issues only, C=important issues, D=multiple important or borderline critical, F=Stage1 FAIL or any critical issue.',
        },
        task_boundary: {
          type: 'boolean' as const,
          description: 'true when called from --task-boundary code review (advances tasks via GBTW), false for standalone review (informational only).',
        },
      },
      required: ['grade', 'task_boundary'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'mark_design_ready',
    description:
      'Transitions workflow from brainstorming to design_ready. If file is provided, auto-registers and consumes the design. Call at end of brainstorming skill after writing design document.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        file: {
          type: 'string' as const,
          description: 'Path to the design file. If provided, auto-registers and consumes the design.',
        },
      },
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'mark_plan_ready',
    description:
      'Transitions workflow from design_marked_for_use to plan_ready. Call at end of writing-plans skill after writing plan files to disk.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'mark_brainstorming',
    description:
      'Transitions workflow to brainstorming from any valid source state (idle, debugging, execution_complete, plan_interrupted). Uses FORWARD_TRANSITIONS table as single source of truth.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'mark_debugging',
    description:
      'Transitions workflow to debugging from any valid source state (idle, brainstorming). Uses FORWARD_TRANSITIONS table as single source of truth.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'mark_executing',
    description:
      'Transitions workflow from reviewing back to executing. Call in executing-plans skill after code-review sub-skill returns.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'retreat',
    description:
      'Retreats to brainstorming or debugging. Always allowed from design_ready, plan_ready, or executing. Preserves artifacts per retreat logic.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        to: {
          type: 'string' as const,
          enum: ['brainstorming', 'debugging'],
          description: 'Target stage to retreat to.',
        },
        reason: {
          type: 'string' as const,
          description: 'Reason for the retreat.',
        },
      },
      required: ['to', 'reason'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
    },
  },
  {
    name: 'reset_session',
    description:
      'Clears all execution state (plan_json, wave tasks, plan_name, current_wave), resets workflow to idle, clears review_pending and circuit_breaker. Preserves professional_mode.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: true,
      idempotentHint: true,
    },
  },
  {
    name: 'set_professional_mode',
    description:
      'Sets the professional mode for the current session. Claude can set "on" (from any state) but cannot set "off" (human-only via hooks). Validates transitions via state machine.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        value: {
          type: 'string' as const,
          enum: ['on', 'off'],
          description: 'The professional mode value to set.',
        },
      },
      required: ['value'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
  {
    name: 'set_log_session_ids',
    description:
      'Toggles session ID display in statusline and state change messages. Updates log_session_ids in ~/.claude/ironclaude-hooks-config.json.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        value: {
          type: 'boolean' as const,
          description: 'Whether to show session IDs (true/false).',
        },
      },
      required: ['value'],
      additionalProperties: false,
    },
    annotations: {
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
    },
  },
];

// ---------------------------------------------------------------------------
// Write tool names (for routing in index.ts)
// ---------------------------------------------------------------------------

export const writeToolNames = new Set(writeToolDefinitions.map((t) => t.name));

// ---------------------------------------------------------------------------
// Tool handler
// ---------------------------------------------------------------------------

export function handleWriteTool(
  name: string,
  args: Record<string, unknown>,
  db: Database.Database,
  sessionId: string,
): ToolResult {
  const resolvedId = resolveSessionId(db, sessionId);

  switch (name) {
    // ----- register_design -----
    case 'register_design': {
      const file = args.file as string;
      if (!file || typeof file !== 'string' || file.trim() === '') {
        return err('Missing or empty required parameter: file');
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const validStages: WorkflowStage[] = ['brainstorming', 'idle', 'execution_complete'];
      if (!validStages.includes(session.workflow_stage)) {
        return err(
          `Cannot register design: workflow must be brainstorming or idle, currently ${session.workflow_stage}`,
        );
      }

      dbRegisterDesign(db, file, resolvedId);

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'register_design',
        old_value: null,
        new_value: file,
        context: `Registered design file: ${file}`,
      });

      return ok({ success: true, file, session_id: resolvedId });
    }

    // ----- consume_design -----
    case 'consume_design': {
      const file = args.file as string;
      if (!file || typeof file !== 'string' || file.trim() === '') {
        return err('Missing or empty required parameter: file');
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      // Check design exists and is not consumed
      const design = getDesign(db, file);
      if (!design) {
        return err(`Design not found: ${file}`);
      }
      if (design.consumed === 1) {
        return err(`Design already consumed: ${file}`);
      }

      // Only transition to design_ready if moving forward; don't regress state
      const validConsumeStages: WorkflowStage[] = ['brainstorming', 'design_ready', 'design_marked_for_use'];
      if (!validConsumeStages.includes(session.workflow_stage)) {
        return err(
          `Cannot consume design: workflow must be brainstorming, design_ready, or design_marked_for_use, currently ${session.workflow_stage}`,
        );
      }

      // Consume design
      dbConsumeDesign(db, file);

      // Only transition to design_ready if coming from brainstorming; preserve later stages
      if (session.workflow_stage === 'brainstorming') {
        updateSession(db, resolvedId, { workflow_stage: 'design_ready' });
      }

      const newStage = session.workflow_stage === 'brainstorming' ? 'design_ready' : session.workflow_stage;

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'consume_design',
        old_value: session.workflow_stage,
        new_value: newStage,
        context: `Consumed design file: ${file}`,
      });

      return ok({
        success: true,
        file,
        workflow_stage: newStage,
        session_id: resolvedId,
      });
    }

    // ----- create_plan -----
    case 'create_plan': {
      let planJsonInput = args.plan_json;

      // Accept string (JSON.parse) or object
      if (typeof planJsonInput === 'string') {
        try {
          planJsonInput = JSON.parse(planJsonInput);
        } catch {
          return err('Invalid plan_json: not valid JSON string');
        }
      }

      if (!planJsonInput || typeof planJsonInput !== 'object') {
        return err('Missing or invalid required parameter: plan_json');
      }

      // Validate plan JSON
      const validation = validatePlanJson(planJsonInput);
      if (!validation.valid) {
        return err('Plan validation failed', { errors: validation.errors });
      }

      const planJson = planJsonInput as PlanJson;

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      // Check workflow stage
      const validStages: WorkflowStage[] = ['design_ready', 'design_marked_for_use', 'plan_ready', 'plan_marked_for_use'];
      if (!validStages.includes(session.workflow_stage)) {
        return err(
          `Cannot create plan: workflow must be design_ready, design_marked_for_use, plan_ready, or plan_marked_for_use, currently ${session.workflow_stage}`,
        );
      }

      // Compute Wave 1
      const wave1Tasks = computeNextWave(planJson, []);

      // Store plan in session (clear stale review_pending from prior plans)
      updateSession(db, resolvedId, {
        plan_json: JSON.stringify(planJson),
        plan_name: planJson.name,
        current_wave: 1,
        workflow_stage: 'final_plan_prep',
        review_pending: 0,
      });

      // Clear stale review grades from any prior plan run
      clearReviewGrades(db, resolvedId);

      // Clear stale wave tasks from any prior plan run (prevents ghost completed tasks)
      db.prepare('DELETE FROM wave_tasks WHERE terminal_session = ?').run(resolvedId);

      // Create wave_task rows for Wave 1
      for (const task of wave1Tasks) {
        upsertWaveTask(db, {
          terminal_session: resolvedId,
          task_id: task.id,
          wave_number: 1,
          task_name: task.name,
          description: task.description,
          allowed_files: JSON.stringify(task.allowed_files),
          status: 'pending',
        });
      }

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'create_plan',
        old_value: session.workflow_stage,
        new_value: 'final_plan_prep',
        context: `Created plan "${planJson.name}" with ${planJson.tasks.length} tasks, Wave 1 has ${wave1Tasks.length} tasks`,
      });

      return ok({
        success: true,
        plan_name: planJson.name,
        total_tasks: planJson.tasks.length,
        wave_1_tasks: wave1Tasks.map((t) => ({ id: t.id, name: t.name })),
        workflow_stage: 'final_plan_prep',
        session_id: resolvedId,
      });
    }

    // ----- start_execution -----
    case 'start_execution': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      // Validate transition
      const transitionResult = validateWorkflowTransition(
        session.workflow_stage,
        'executing',
        {
          hasDesign: true, // Already past design stage
          designConsumed: true,
          hasPlan: !!session.plan_json,
        },
      );

      if (!transitionResult.valid) {
        return err(transitionResult.reason);
      }

      // Update workflow (clear stale review_pending from prior plans)
      updateSession(db, resolvedId, { workflow_stage: 'executing', review_pending: 0 });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'start_execution',
        old_value: session.workflow_stage,
        new_value: 'executing',
        context: 'Transitioned to executing',
      });

      return ok({
        success: true,
        workflow_stage: 'executing',
        session_id: resolvedId,
      });
    }

    // ----- get_next_tasks -----
    case 'get_next_tasks': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const validExecStages: WorkflowStage[] = ['executing', 'reviewing'];
      if (!validExecStages.includes(session.workflow_stage)) {
        return err(
          `Cannot get next tasks: workflow must be executing or reviewing, currently ${session.workflow_stage}`,
        );
      }

      if (!session.plan_json) {
        return err('No plan exists for this session');
      }

      let planJson: PlanJson;
      try {
        planJson = JSON.parse(session.plan_json) as PlanJson;
      } catch {
        return err('Failed to parse plan_json from session');
      }

      const allWaveTasks = getWaveTasks(db, resolvedId);
      const currentWave = session.current_wave;

      // Get tasks in the current wave
      const currentWaveTasks = allWaveTasks.filter(
        (t) => t.wave_number === currentWave,
      );

      // Check if current wave is in progress (some tasks not review_passed)
      const pendingInWave = currentWaveTasks.filter(
        (t) => t.status !== 'review_passed',
      );

      if (currentWaveTasks.length > 0 && pendingInWave.length > 0) {
        return ok({
          status: 'wave_in_progress',
          current_wave: currentWave,
          pending: pendingInWave.map((t) => ({
            task_id: t.task_id,
            task_name: t.task_name,
            status: t.status,
          })),
        });
      }

      // Current wave fully reviewed (or no wave started) -- compute next wave
      const completedTaskIds = allWaveTasks
        .filter((t) => t.status === 'review_passed')
        .map((t) => t.task_id);

      const nextWaveTasks = computeNextWave(planJson, completedTaskIds);

      // All tasks done
      if (nextWaveTasks.length === 0) {
        updateSession(db, resolvedId, { workflow_stage: 'execution_complete' });
        insertAuditLog(db, {
          terminal_session: resolvedId,
          actor: 'claude',
          action: 'get_next_tasks',
          old_value: session.workflow_stage,
          new_value: 'execution_complete',
          context: 'All plan tasks complete',
        });
        return ok({ status: 'complete', workflow_stage: 'execution_complete', session_id: resolvedId });
      }

      // Create next wave
      const nextWaveNumber = currentWave + 1;

      for (const task of nextWaveTasks) {
        upsertWaveTask(db, {
          terminal_session: resolvedId,
          task_id: task.id,
          wave_number: nextWaveNumber,
          task_name: task.name,
          description: task.description,
          allowed_files: JSON.stringify(task.allowed_files),
          status: 'pending',
        });
      }

      // Update session wave counter
      updateSession(db, resolvedId, { current_wave: nextWaveNumber });

      return ok({
        status: 'next_wave',
        wave_number: nextWaveNumber,
        tasks: nextWaveTasks.map((t) => ({
          id: t.id,
          name: t.name,
          description: t.description,
          allowed_files: t.allowed_files,
          depends_on: t.depends_on,
          steps: t.steps,
        })),
        session_id: resolvedId,
      });
    }

    // ----- submit_task -----
    case 'submit_task': {
      const taskId = args.task_id as number;
      if (taskId === undefined || taskId === null || typeof taskId !== 'number') {
        return err('Missing or invalid required parameter: task_id');
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const currentWave = session.current_wave;

      // Find the wave task by task_id in the current wave
      const waveTask = db.prepare(
        `SELECT * FROM wave_tasks WHERE terminal_session = ? AND task_id = ? AND wave_number = ?`
      ).get(resolvedId, taskId, currentWave) as WaveTask | undefined;

      if (!waveTask) {
        return err(
          `Task ${taskId} not found in current wave ${currentWave}`,
          { session_id: resolvedId },
        );
      }

      if (waveTask.status !== 'in_progress') {
        return err(
          `Task ${taskId} status must be 'in_progress' to submit, currently '${waveTask.status}'`,
        );
      }

      // Update status to submitted using the DB row id
      updateWaveTaskStatus(db, waveTask.id, 'submitted');

      // Set review_pending
      updateSession(db, resolvedId, { review_pending: 1 });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'submit_task',
        old_value: 'in_progress',
        new_value: 'submitted',
        context: `Submitted task ${taskId} ("${waveTask.task_name}") for review`,
      });

      return ok({
        success: true,
        task_id: taskId,
        task_name: waveTask.task_name,
        status: 'submitted',
        review_pending: true,
        session_id: resolvedId,
      });
    }

    // ----- claim_task -----
    case 'claim_task': {
      const taskId = args.task_id as number;
      if (taskId === undefined || taskId === null || typeof taskId !== 'number') {
        return err('Missing or invalid required parameter: task_id');
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const currentWave = session.current_wave;

      const waveTask = db.prepare(
        `SELECT * FROM wave_tasks WHERE terminal_session = ? AND task_id = ? AND wave_number = ?`
      ).get(resolvedId, taskId, currentWave) as WaveTask | undefined;

      if (!waveTask) {
        return err(
          `Task ${taskId} not found in current wave ${currentWave}`,
          { session_id: resolvedId },
        );
      }

      if (waveTask.status !== 'pending') {
        return err(
          `Task ${taskId} status must be 'pending' to claim, currently '${waveTask.status}'`,
        );
      }

      updateWaveTaskStatus(db, waveTask.id, 'in_progress');

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'claim_task',
        old_value: 'pending',
        new_value: 'in_progress',
        context: `Claimed task ${taskId} ("${waveTask.task_name}")`,
      });

      return ok({
        success: true,
        task_id: taskId,
        task_name: waveTask.task_name,
        status: 'in_progress',
        session_id: resolvedId,
      });
    }

    // ----- record_review_verdict -----
    case 'record_review_verdict': {
      const grade = args.grade as string;
      const taskBoundary = args.task_boundary as boolean;

      if (!['A', 'B', 'C', 'D', 'F'].includes(grade)) {
        return err(`Invalid grade: "${grade}". Must be one of: A, B, C, D, F`);
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const validReviewStages: WorkflowStage[] = ['executing', 'reviewing'];
      if (!validReviewStages.includes(session.workflow_stage)) {
        return err(
          `Cannot record review: workflow must be executing or reviewing, currently ${session.workflow_stage}`,
        );
      }

      // Auto-determine submitted task_ids from current wave (filtered by wave_number)
      const submittedRows = db.prepare(
        `SELECT task_id FROM wave_tasks WHERE terminal_session = ? AND wave_number = ? AND status = 'submitted' ORDER BY task_id`,
      ).all(resolvedId, session.current_wave) as { task_id: number }[];
      const taskIds = submittedRows.map((r) => r.task_id);

      insertReviewGrade(db, resolvedId, session.current_wave, taskIds, grade, taskBoundary);

      // Advance submitted tasks on passing grade (A/B) with task_boundary
      let advancedCount = 0;
      if (taskBoundary && ['A', 'B'].includes(grade)) {
        const result = db.prepare(
          `UPDATE wave_tasks SET status = 'review_passed', updated_at = datetime('now')
           WHERE terminal_session = ? AND wave_number = ? AND status = 'submitted'`,
        ).run(resolvedId, session.current_wave);
        advancedCount = result.changes;
        updateSession(db, resolvedId, { review_pending: 0 });
      }

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'record_review_verdict',
        old_value: null,
        new_value: grade,
        context: `wave=${session.current_wave}, task_boundary=${taskBoundary}, task_ids=${JSON.stringify(taskIds)}`,
      });

      return ok({
        success: true,
        grade,
        task_boundary: taskBoundary,
        wave_number: session.current_wave,
        task_ids: taskIds,
        advanced_count: advancedCount,
      });
    }

    // ----- mark_design_ready -----
    case 'mark_design_ready': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      if (session.workflow_stage !== 'brainstorming') {
        return err(
          `Cannot mark design ready: workflow_stage must be 'brainstorming', got '${session.workflow_stage}'`,
        );
      }

      // Auto-register and consume design if file provided
      const file = args.file as string | undefined;
      if (file) {
        dbRegisterDesign(db, file, resolvedId);
        dbConsumeDesign(db, file);
      }

      updateSession(db, resolvedId, { workflow_stage: 'design_ready' });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'mark_design_ready',
        old_value: 'brainstorming',
        new_value: 'design_ready',
        context: file ? `Brainstorming complete, design ready (auto-registered: ${file})` : 'Brainstorming complete, design ready',
      });

      return ok({ success: true, workflow_stage: 'design_ready', session_id: resolvedId });
    }

    // ----- mark_plan_ready -----
    case 'mark_plan_ready': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const validPlanReadyFrom: WorkflowStage[] = ['design_ready', 'design_marked_for_use'];
      if (!validPlanReadyFrom.includes(session.workflow_stage)) {
        return err(
          `Cannot mark plan ready: workflow_stage must be 'design_ready' or 'design_marked_for_use', got '${session.workflow_stage}'`,
        );
      }

      updateSession(db, resolvedId, { workflow_stage: 'plan_ready' });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'mark_plan_ready',
        old_value: session.workflow_stage,
        new_value: 'plan_ready',
        context: 'Plan files written to disk',
      });

      return ok({ success: true, workflow_stage: 'plan_ready', session_id: resolvedId });
    }

    // ----- mark_brainstorming -----
    case 'mark_brainstorming': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      if (!canTransitionTo(session.workflow_stage, 'brainstorming')) {
        return err(
          `Cannot mark brainstorming: invalid transition from '${session.workflow_stage}'`,
        );
      }

      updateSession(db, resolvedId, { workflow_stage: 'brainstorming' });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'mark_brainstorming',
        old_value: session.workflow_stage,
        new_value: 'brainstorming',
        context: `Transitioning to brainstorming from ${session.workflow_stage}`,
      });

      return ok({ success: true, workflow_stage: 'brainstorming', session_id: resolvedId });
    }

    // ----- mark_debugging -----
    case 'mark_debugging': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      if (!canTransitionTo(session.workflow_stage, 'debugging')) {
        return err(
          `Cannot mark debugging: invalid transition from '${session.workflow_stage}'`,
        );
      }

      updateSession(db, resolvedId, { workflow_stage: 'debugging' });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'mark_debugging',
        old_value: session.workflow_stage,
        new_value: 'debugging',
        context: `Transitioning to debugging from ${session.workflow_stage}`,
      });

      return ok({ success: true, workflow_stage: 'debugging', session_id: resolvedId });
    }

    // ----- mark_executing -----
    case 'mark_executing': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      if (session.workflow_stage !== 'reviewing') {
        return err(
          `Cannot mark executing: workflow_stage must be 'reviewing', got '${session.workflow_stage}'`,
        );
      }

      const passingReview = db.prepare(
        `SELECT 1 FROM review_grades
         WHERE terminal_session = ? AND wave_number = ? AND grade IN ('A', 'B')
         ORDER BY created_at DESC LIMIT 1`,
      ).get(resolvedId, session.current_wave);

      if (!passingReview) {
        return err(
          'Cannot mark executing: no passing review verdict (A or B) recorded for current wave. ' +
          'Call record_review_verdict first.',
        );
      }

      updateSession(db, resolvedId, { workflow_stage: 'executing', review_pending: 0 });

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'mark_executing',
        old_value: 'reviewing',
        new_value: 'executing',
        context: 'Code review complete, returning to executing',
      });

      return ok({ success: true, workflow_stage: 'executing', session_id: resolvedId });
    }

    // ----- retreat -----
    case 'retreat': {
      const to = args.to as 'brainstorming' | 'debugging';
      const reason = args.reason as string;

      if (!to || (to !== 'brainstorming' && to !== 'debugging')) {
        return err(
          "Missing or invalid required parameter: to (must be 'brainstorming' or 'debugging')",
        );
      }
      if (!reason || typeof reason !== 'string' || reason.trim() === '') {
        return err('Missing or empty required parameter: reason');
      }

      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const from = session.workflow_stage;

      // Validate retreat is allowed via transition table
      if (!canTransitionTo(from, to)) {
        return err(
          `Cannot retreat from '${from}' to '${to}': transition not allowed`,
        );
      }

      // Execute retreat (handles artifact preservation, audit log, state updates)
      executeRetreat(db, resolvedId, from, to, reason);

      return ok({
        success: true,
        from,
        to,
        reason,
        session_id: resolvedId,
      });
    }

    // ----- reset_session -----
    case 'reset_session': {
      const session = getSession(db, resolvedId);
      if (!session) {
        return err('Session not found', { session_id: resolvedId });
      }

      const oldStage = session.workflow_stage;

      // Clear plan state, wave tasks, reset workflow
      updateSession(db, resolvedId, {
        plan_json: null,
        plan_name: null,
        current_wave: 0,
        workflow_stage: 'idle',
        review_pending: 0,
        circuit_breaker: 0,
      });

      // Delete wave tasks and review grades for this session
      db.prepare(
        `DELETE FROM wave_tasks WHERE terminal_session = ?`
      ).run(resolvedId);
      db.prepare(
        `DELETE FROM review_grades WHERE terminal_session = ?`
      ).run(resolvedId);

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'reset_session',
        old_value: oldStage,
        new_value: 'idle',
        context: 'Reset session: cleared plan, wave tasks, review_pending, circuit_breaker',
      });

      return ok({
        success: true,
        workflow_stage: 'idle',
        session_id: resolvedId,
      });
    }

    // ----- set_professional_mode -----
    case 'set_professional_mode': {
      const value = args.value as string;
      if (!value || (value !== 'on' && value !== 'off')) {
        return err("Missing or invalid required parameter: value (must be 'on' or 'off')");
      }

      const session = getSession(db, resolvedId);
      const currentMode = session?.professional_mode ?? 'undecided';

      // Validate transition via state machine
      const transition = validateProfessionalModeTransition(
        currentMode,
        value as 'on' | 'off',
        'claude',
      );

      if (!transition.valid) {
        return err(transition.reason, {
          current: currentMode,
          requested: value,
        });
      }

      // If no session exists, create one
      if (!session) {
        upsertSession(db, {
          terminal_session: resolvedId,
          professional_mode: value,
        });
      } else {
        updateSession(db, resolvedId, { professional_mode: value });
      }

      insertAuditLog(db, {
        terminal_session: resolvedId,
        actor: 'claude',
        action: 'set_professional_mode',
        old_value: currentMode,
        new_value: value,
        context: `Set professional mode to ${value} (via MCP tool)`,
      });

      return ok({
        success: true,
        professional_mode: value,
        previous: currentMode,
        session_id: resolvedId,
      });
    }

    // ----- set_log_session_ids -----
    case 'set_log_session_ids': {
      const value = args.value as boolean;
      if (value === undefined || typeof value !== 'boolean') {
        return err('Missing or invalid required parameter: value (must be boolean)');
      }

      const configPath = path.join(os.homedir(), '.claude', 'ironclaude-hooks-config.json');
      let config: Record<string, unknown> = {};

      try {
        if (fs.existsSync(configPath)) {
          config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
        }
      } catch {
        // Config file missing or malformed, start fresh
      }

      const previous = config.log_session_ids ?? true;
      config.log_session_ids = value;
      fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n');

      return ok({
        success: true,
        log_session_ids: value,
        previous,
      });
    }

    default:
      throw new Error(`Unknown write tool: ${name}`);
  }
}
