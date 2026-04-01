/**
 * Type definitions for the State Manager MCP Server.
 *
 * These types map directly to the SQLite tables in ~/.claude/ironclaude.db.
 */

// --- Enum-like string unions ---

export type ProfessionalMode = 'undecided' | 'on' | 'off';
export type WorkflowStage =
  | 'idle'
  | 'brainstorming'
  | 'debugging'
  | 'design_ready'
  | 'design_marked_for_use'
  | 'plan_ready'
  | 'plan_marked_for_use'
  | 'final_plan_prep'
  | 'executing'
  | 'reviewing'
  | 'plan_interrupted'
  | 'execution_complete';
export type WaveTaskStatus = 'pending' | 'in_progress' | 'submitted' | 'review_passed' | 'review_failed';
export type ReviewGrade = 'A' | 'B' | 'C' | 'D' | 'F';

export interface ReviewGradeEntry {
  id: number;
  terminal_session: string;
  wave_number: number;
  task_ids: string;       // JSON array, e.g. "[1, 2, 3]"
  grade: ReviewGrade;
  task_boundary: number;  // 1 = task boundary review, 0 = standalone
  created_at: string;
}

// --- Table row interfaces ---

export interface Session {
  terminal_session: string;
  professional_mode: ProfessionalMode;
  workflow_stage: WorkflowStage;
  active_skill: string | null;
  brainstorming_active: number;
  plan_name: string | null;
  plan_json: string | null;
  current_wave: number;
  review_pending: number;
  circuit_breaker: number;
  memory_search_required: number;
  testing_theatre_checked: number;
  project_hash: string | null;
  updated_at: string;
}

export interface WaveTask {
  id: number;
  terminal_session: string;
  task_id: number;
  wave_number: number;
  task_name: string;
  description: string;
  allowed_files: string;
  status: WaveTaskStatus;
  created_at: string;
  updated_at: string;
}

export interface PlanHistory {
  id: number;
  terminal_session: string;
  plan_name: string;
  design_file: string;
  completed_tasks: string;  // JSON array of task IDs
  total_tasks: number;
  retreat_reason: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  terminal_session: string;
  actor: string;
  action: string;
  old_value: string | null;
  new_value: string | null;
  context: string | null;
  created_at: string;
}

export interface RegisteredDesign {
  design_file: string;
  registered_at: string;
  terminal_session: string;
  consumed: number;
}

export interface SubagentSession {
  child_session: string;
  parent_session: string;
  task_number: number | null;
  created_at: string;
}

// --- State change tracking ---

export interface StateChange {
  field: string;
  old: string;
  new: string;
}

// --- Plan JSON schema types ---

export interface PlanTask {
  id: number;
  name: string;
  description: string;
  allowed_files: string[];
  depends_on: number[];
  steps: PlanStep[];
}

export interface PlanStep {
  description: string;
  command?: string;
  expected?: string;
}

export interface PlanJson {
  name: string;
  goal: string;
  design_file: string;
  tasks: PlanTask[];
}
