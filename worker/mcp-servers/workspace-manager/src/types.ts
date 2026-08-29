export type IronClaudeClient = 'claude' | 'codex';

export interface SessionIdentity {
  client: IronClaudeClient;
  sessionId: string;
  invocationThreadId: string | null;
  source: 'ppid_file' | 'codex_meta';
}

export type AssignmentLifecycle =
  | 'reserved'
  | 'materialized'
  | 'active'
  | 'ready_for_integration'
  | 'integrated'
  | 'abandoned'
  | 'cleaned';

export interface Assignment {
  workspace_guid: string;
  repository_identity: string;
  worktree_path: string;
  branch: string;
  base_commit: string;
  current_head: string;
  owner_session_id: string | null;
  worker_id: string | null;
  lifecycle_status: AssignmentLifecycle;
  integration_target: string;
  integrated_commit: string | null;
  recovery_ref: string | null;
  disposition: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateAssignmentInput {
  workspaceGuid: string;
  repositoryIdentity: string;
  worktreePath: string;
  branch: string;
  baseCommit: string;
  currentHead: string;
  ownerSessionId?: string | null;
  workerId?: string | null;
  integrationTarget: string;
}

export interface PrimaryCheckoutOwnership {
  repository_identity: string;
  workspace_guid: string;
  owner_session_id: string;
  acquired_at: string;
}

export interface AcquirePrimaryCheckoutOwnershipInput {
  repositoryIdentity: string;
  workspaceGuid: string;
  ownerSessionId: string;
}

export interface IntegrationLock {
  repository_identity: string;
  workspace_guid: string;
  target_ref: string;
  expected_target: string;
  acquired_at: string;
}

export interface AcquireIntegrationLockInput {
  repositoryIdentity: string;
  workspaceGuid: string;
  targetRef: string;
  expectedTarget: string;
}

export interface IntegrationRecord {
  id: number;
  workspace_guid: string;
  repository_identity: string;
  target_ref: string;
  integrated_commit: string;
  created_at: string;
}

export interface RecordIntegrationInput {
  workspaceGuid: string;
  repositoryIdentity: string;
  targetRef: string;
  integratedCommit: string;
}

export type HumanIntentOperation =
  | 'use-primary-checkout'
  | 'return-to-managed-worktree'
  | 'commit'
  | 'commit-and-push'
  | 'push'
  | 'reconcile'
  | 'close-out'
  | 'confirm-resolution';

export interface HumanIntent {
  intent_id: number;
  operation: HumanIntentOperation;
  human_channel: string;
  provider_root_session_id: string;
  repository_identity: string;
  workspace_guid: string;
  expected_evidence: string;
  expires_at: string;
  nonce: string;
  issued_at: string;
  consumed_at: string | null;
}

export interface CreateHumanIntentInput {
  operation: HumanIntentOperation;
  humanChannel: string;
  providerRootSessionId: string;
  repositoryIdentity: string;
  workspaceGuid: string;
  expectedEvidence: unknown;
  expiresAt: string;
  nonce: string;
}

export type ConsumeHumanIntentInput = Omit<CreateHumanIntentInput, 'expiresAt'>;

export type IssueHumanIntentInput = Omit<CreateHumanIntentInput, 'expiresAt' | 'nonce'>;

export type ConsumeMatchingHumanIntentInput = Omit<CreateHumanIntentInput, 'expiresAt' | 'nonce'>;

export interface HumanIntentReceipt {
  issued: true;
  operation: HumanIntentOperation;
}
