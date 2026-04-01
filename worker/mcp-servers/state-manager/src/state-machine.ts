/**
 * State machine transition logic for the State Manager MCP Server.
 *
 * Implements validation and execution of state transitions for:
 *   - Professional mode (undecided / on / off)
 *   - Workflow chain (idle -> brainstorming -> design_ready -> design_marked_for_use -> plan_ready -> plan_marked_for_use -> final_plan_prep -> executing -> idle)
 *   - Wave computation from plan dependency graphs
 *   - Plan JSON validation (schema + dependency integrity)
 *   - Retreat logic (rollback workflow state with artifact preservation)
 */

import { z } from 'zod';
import type Database from 'better-sqlite3';
import type {
  ProfessionalMode,
  WorkflowStage,
  PlanJson,
  PlanTask,
} from './types.js';
import {
  getSession,
  insertPlanHistory,
  unconsumeDesign,
  updateSession,
  insertAuditLog,
} from './db.js';

// ---------------------------------------------------------------------------
// 1. Professional mode transitions
// ---------------------------------------------------------------------------

export function validateProfessionalModeTransition(
  current: ProfessionalMode,
  target: ProfessionalMode,
  actor: 'claude' | 'hook',
): { valid: boolean; reason: string } {
  // Same state -- no-op is always valid
  if (current === target) {
    return { valid: true, reason: 'No change required' };
  }

  // Claude can NEVER transition to 'off'
  if (actor === 'claude' && target === 'off') {
    return {
      valid: false,
      reason: 'Claude cannot transition professional mode to off — only a human (via hook) can do that',
    };
  }

  // Nobody can transition back to 'undecided' — it is the initial-only state
  if (target === 'undecided') {
    return {
      valid: false,
      reason: "Cannot transition to 'undecided' — it is only the initial session state",
    };
  }

  // undecided -> on: always valid
  if (current === 'undecided' && target === 'on') {
    return { valid: true, reason: 'Activating professional mode from undecided' };
  }

  // undecided -> off: only by hook
  if (current === 'undecided' && target === 'off') {
    if (actor === 'hook') {
      return { valid: true, reason: 'Deactivating professional mode from undecided (human action)' };
    }
    // actor === 'claude' already caught above, but belt-and-suspenders:
    return {
      valid: false,
      reason: 'Claude cannot deactivate professional mode',
    };
  }

  // on -> off: only by hook
  if (current === 'on' && target === 'off') {
    if (actor === 'hook') {
      return { valid: true, reason: 'Deactivating professional mode (human action)' };
    }
    return {
      valid: false,
      reason: 'Claude cannot deactivate professional mode',
    };
  }

  // off -> on: always valid
  if (current === 'off' && target === 'on') {
    return { valid: true, reason: 'Re-activating professional mode' };
  }

  // Shouldn't reach here, but guard against unknown states
  return { valid: false, reason: `Unknown transition: ${current} -> ${target}` };
}

// ---------------------------------------------------------------------------
// 2. Workflow chain transitions
// ---------------------------------------------------------------------------

/**
 * The set of workflow stages that can be retreated to.
 */
type RetreatTarget = 'brainstorming' | 'debugging';

/**
 * Valid forward transitions (non-retreat). Each key maps to the set of stages
 * it can move forward to.
 */
const FORWARD_TRANSITIONS: Partial<Record<WorkflowStage, readonly WorkflowStage[]>> = {
  idle:                  ['brainstorming', 'debugging'],
  brainstorming:         ['debugging', 'design_ready'],
  debugging:             ['brainstorming', 'idle'],
  design_ready:          ['plan_ready'],
  design_marked_for_use: ['plan_ready'],
  plan_ready:            ['executing'],
  plan_marked_for_use:   ['final_plan_prep'],
  final_plan_prep:       ['executing'],
  executing:             ['idle', 'execution_complete'],
  reviewing:             ['executing'],
  plan_interrupted:      ['brainstorming', 'debugging'],
  execution_complete:    ['brainstorming', 'idle', 'debugging'],
};

/**
 * Stages from which retreat is allowed (and their valid retreat targets).
 */
export const RETREAT_SOURCES: Record<string, readonly RetreatTarget[]> = {
  design_ready:          ['brainstorming', 'debugging'],
  design_marked_for_use: ['brainstorming', 'debugging'],
  plan_ready:            ['brainstorming', 'debugging'],
  plan_marked_for_use:   ['brainstorming', 'debugging'],
  final_plan_prep:       ['brainstorming', 'debugging'],
  executing:             ['brainstorming', 'debugging'],
  reviewing:             ['brainstorming', 'debugging'],
  plan_interrupted:      ['brainstorming', 'debugging'],
};

/**
 * Check if a transition from `current` to `target` is valid via either
 * the forward transition table or retreat sources. Does NOT check
 * prerequisites (design, plan) — use validateWorkflowTransition for that.
 */
export function canTransitionTo(current: WorkflowStage, target: WorkflowStage): boolean {
  // Check forward transitions
  const forwardTargets = FORWARD_TRANSITIONS[current];
  if (forwardTargets && forwardTargets.includes(target)) {
    return true;
  }
  // Check retreat sources
  const retreatTargets = RETREAT_SOURCES[current];
  if (retreatTargets && (retreatTargets as readonly string[]).includes(target)) {
    return true;
  }
  return false;
}

export function validateWorkflowTransition(
  current: WorkflowStage,
  target: WorkflowStage,
  context: {
    hasDesign: boolean;
    designConsumed: boolean;
    hasPlan: boolean;
  },
): { valid: boolean; reason: string } {
  // No-op
  if (current === target) {
    return { valid: true, reason: 'No change required' };
  }

  // Check retreat first — retreat targets are brainstorming / debugging from
  // design_ready, plan_ready, or executing.
  const retreatTargets = RETREAT_SOURCES[current];
  if (retreatTargets && (retreatTargets as readonly string[]).includes(target)) {
    return {
      valid: true,
      reason: `Retreat from ${current} to ${target}`,
    };
  }

  // Check forward transitions
  const forwardTargets = FORWARD_TRANSITIONS[current];
  if (!forwardTargets || !forwardTargets.includes(target)) {
    return {
      valid: false,
      reason: `Invalid workflow transition: ${current} -> ${target}`,
    };
  }

  // Forward prerequisite checks
  if (target === 'design_ready') {
    if (!context.hasDesign) {
      return {
        valid: false,
        reason: 'Cannot enter design_ready: no registered design exists',
      };
    }
  }

  if (target === 'plan_ready') {
    if (!context.designConsumed) {
      return {
        valid: false,
        reason: 'Cannot enter plan_ready: design has not been consumed yet',
      };
    }
  }

  if (target === 'executing') {
    if (!context.hasPlan) {
      return {
        valid: false,
        reason: 'Cannot enter executing: no plan has been created',
      };
    }
  }

  return {
    valid: true,
    reason: `Valid forward transition: ${current} -> ${target}`,
  };
}

// ---------------------------------------------------------------------------
// 3. Wave computation
// ---------------------------------------------------------------------------

/**
 * Compute the next wave of tasks that are ready to execute.
 *
 * A task is ready when:
 *   1. It has not already been completed (its ID is NOT in completedTaskIds).
 *   2. All of its depends_on task IDs ARE in completedTaskIds.
 *
 * This naturally produces the "frontier" of the dependency DAG — all tasks
 * whose prerequisites have been satisfied.
 */
export function computeNextWave(
  planJson: PlanJson,
  completedTaskIds: number[],
): PlanTask[] {
  const completedSet = new Set(completedTaskIds);

  return planJson.tasks.filter((task) => {
    // Skip already-completed tasks
    if (completedSet.has(task.id)) {
      return false;
    }

    // All dependencies must be completed
    return task.depends_on.every((depId) => completedSet.has(depId));
  });
}

// ---------------------------------------------------------------------------
// 4. Plan JSON validation (Zod schemas + structural checks)
// ---------------------------------------------------------------------------

export const PlanStepSchema = z.object({
  description: z.string(),
  command: z.string().optional(),
  expected: z.string().optional(),
});

export const PlanTaskSchema = z.object({
  id: z.number().int().positive(),
  name: z.string().min(1),
  description: z.string(),
  allowed_files: z.array(z.string().min(1)),
  depends_on: z.array(z.number().int()),
  steps: z.array(PlanStepSchema),
});

export const PlanJsonSchema = z.object({
  name: z.string().min(1),
  goal: z.string().min(1),
  design_file: z.string().min(1),
  tasks: z.array(PlanTaskSchema).min(1),
});

/**
 * Validate a plan JSON object. Performs:
 *   1. Zod schema validation (types, required fields)
 *   2. Referential integrity: all depends_on IDs reference existing task IDs
 *   3. Cycle detection: no circular dependencies (topological sort)
 *   4. Content check: every task has non-empty allowed_files
 */
export function validatePlanJson(json: unknown): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  // 1. Zod schema validation
  const parseResult = PlanJsonSchema.safeParse(json);
  if (!parseResult.success) {
    for (const issue of parseResult.error.issues) {
      errors.push(`Schema: ${issue.path.join('.')} — ${issue.message}`);
    }
    return { valid: false, errors };
  }

  const plan = parseResult.data;
  const taskIds = new Set(plan.tasks.map((t) => t.id));

  // 2. Referential integrity: all depends_on point to existing task IDs
  for (const task of plan.tasks) {
    for (const depId of task.depends_on) {
      if (!taskIds.has(depId)) {
        errors.push(
          `Task ${task.id} ("${task.name}"): depends_on references non-existent task ID ${depId}`,
        );
      }
      if (depId === task.id) {
        errors.push(
          `Task ${task.id} ("${task.name}"): depends_on references itself`,
        );
      }
    }
  }

  // 3. Cycle detection via topological sort (Kahn's algorithm)
  const inDegree = new Map<number, number>();
  const adjacency = new Map<number, number[]>();

  for (const task of plan.tasks) {
    inDegree.set(task.id, 0);
    adjacency.set(task.id, []);
  }

  for (const task of plan.tasks) {
    for (const depId of task.depends_on) {
      if (taskIds.has(depId)) {
        // depId -> task.id edge (depId must come before task.id)
        adjacency.get(depId)!.push(task.id);
        inDegree.set(task.id, (inDegree.get(task.id) ?? 0) + 1);
      }
    }
  }

  // Kahn's algorithm
  const queue: number[] = [];
  for (const [id, degree] of inDegree) {
    if (degree === 0) {
      queue.push(id);
    }
  }

  let sortedCount = 0;
  while (queue.length > 0) {
    const current = queue.shift()!;
    sortedCount++;

    for (const neighbor of adjacency.get(current) ?? []) {
      const newDegree = (inDegree.get(neighbor) ?? 1) - 1;
      inDegree.set(neighbor, newDegree);
      if (newDegree === 0) {
        queue.push(neighbor);
      }
    }
  }

  if (sortedCount !== plan.tasks.length) {
    errors.push(
      'Circular dependency detected: not all tasks can be topologically sorted',
    );
  }

  // 4. Every task must have non-empty allowed_files
  // (Zod already enforces the array has min(1)-length strings, but let's also
  //  check the array itself is non-empty — Zod schema allows empty arrays)
  for (const task of plan.tasks) {
    if (task.allowed_files.length === 0) {
      errors.push(
        `Task ${task.id} ("${task.name}"): allowed_files must not be empty`,
      );
    }
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

// ---------------------------------------------------------------------------
// 5. Retreat logic
// ---------------------------------------------------------------------------

/**
 * Execute a retreat from a later workflow stage back to brainstorming or
 * debugging. Handles artifact preservation per the design doc:
 *
 *   From EXECUTING:
 *     - Snapshot progress to plan_history (plan_name, design_file, completed
 *       tasks, total tasks, retreat reason)
 *     - Clear plan state (plan_json, current_wave, plan_name)
 *
 *   From PLAN_READY or EXECUTING:
 *     - Unconsume design (mark consumed=0 so it's available again)
 *
 *   Always:
 *     - Update session: workflow_stage = to, review_pending = 0, circuit_breaker = 0
 *     - Insert audit log entry
 */
export function executeRetreat(
  db: Database.Database,
  sessionId: string,
  from: WorkflowStage,
  to: 'brainstorming' | 'debugging',
  reason: string,
): void {
  // 1. Read current session
  const session = getSession(db, sessionId);
  if (!session) {
    throw new Error(`Session not found: ${sessionId}`);
  }

  // 2. If retreating from 'executing': snapshot progress, clear plan state
  if (from === 'executing' && session.plan_json) {
    let planData: PlanJson | null = null;
    try {
      planData = JSON.parse(session.plan_json) as PlanJson;
    } catch {
      // plan_json was malformed; still proceed with retreat
    }

    if (planData) {
      // Determine completed tasks from wave_tasks table
      const completedRows = db
        .prepare(
          `SELECT task_id FROM wave_tasks
           WHERE terminal_session = ? AND status = 'review_passed'`,
        )
        .all(sessionId) as Array<{ task_id: number }>;

      const completedTaskIds = completedRows.map((r) => r.task_id);

      insertPlanHistory(db, {
        terminal_session: sessionId,
        plan_name: session.plan_name ?? planData.name,
        design_file: planData.design_file,
        completed_tasks: JSON.stringify(completedTaskIds),
        total_tasks: planData.tasks.length,
        retreat_reason: reason,
      });
    }

    // Clear plan state
    updateSession(db, sessionId, {
      plan_json: null,
      current_wave: 0,
      plan_name: null,
    });
  }

  // 3. If retreating from plan-phase or execution-phase: unconsume design
  if (['plan_ready', 'plan_marked_for_use', 'final_plan_prep', 'executing', 'reviewing', 'plan_interrupted'].includes(from)) {
    // Find the design file associated with this session
    let designFile: string | null = null;

    if (session.plan_json) {
      try {
        const planData = JSON.parse(session.plan_json) as PlanJson;
        designFile = planData.design_file;
      } catch {
        // plan_json was malformed; try to find design from registered_designs
      }
    }

    // If plan_json didn't have it (or from plan_ready before plan was set),
    // look for the most recent consumed design for this session
    if (!designFile) {
      const row = db
        .prepare(
          `SELECT design_file FROM registered_designs
           WHERE terminal_session = ? AND consumed = 1
           ORDER BY registered_at DESC LIMIT 1`,
        )
        .get(sessionId) as { design_file: string } | undefined;
      if (row) {
        designFile = row.design_file;
      }
    }

    if (designFile) {
      unconsumeDesign(db, designFile);
    }
  }

  // 4. Update session: workflow_stage, review_pending, circuit_breaker
  updateSession(db, sessionId, {
    workflow_stage: to,
    review_pending: 0,
    circuit_breaker: 0,
  });

  // 5. Audit log
  insertAuditLog(db, {
    terminal_session: sessionId,
    actor: 'system',
    action: 'retreat',
    old_value: from,
    new_value: to,
    context: reason,
  });
}
