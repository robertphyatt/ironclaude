import Database from 'better-sqlite3';
import { spawnSync } from 'node:child_process';
import { createHash, randomUUID, timingSafeEqual } from 'node:crypto';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { discoverRepository, isAncestor, listWorktrees, runGit, worktreeHead } from './git.js';

export interface HandoffInput {
  repositoryPath: string;
  repositoryIdentity: string;
  workspaceGuid: string;
  workerId: string;
  sourceOwnerSessionId: string;
  generation: number;
  capabilityToken: string;
  expectedWorktreePath: string;
  expectedBranch: string;
  expectedBaseCommit: string;
  expectedCurrentHead: string;
  expectedIntegrationTarget: string;
}
export interface TransferHandoffInput extends HandoffInput { targetOwnerSessionId: string; }
export interface ReleaseHandoffInput { workspaceGuid: string; generation: number; capabilityToken: string; sourceOwnerSessionId: string; }
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
type HandoffRow = {
  workspace_guid: string; generation: number; capability_token_hash: string;
  source_owner_session_id: string; target_owner_session_id: string | null; grant_expires_at: string | null;
  repository_identity: string; worker_id: string; worktree_path: string; branch: string;
  base_commit: string; current_head: string; integration_target: string; phase: string;
};
const localClaims = new Map<string, { db: Database.Database; nonce: string; depth: number }>();
const hash = (token: string) => createHash('sha256').update(token).digest('hex');
function equalHash(expected: string, token: string): boolean {
  const actual = hash(token); return expected.length === actual.length && timingSafeEqual(Buffer.from(expected), Buffer.from(actual));
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
let cachedProcessIdentity: string | undefined;
function processExists(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ESRCH') return false;
    if (code === 'EPERM') return true;
    throw error;
  }
}
/**
 * Start time of `pid` as integer epoch seconds (floor), or null when no such process exists.
 * Commander compares the same representation: int(psutil.Process(pid).create_time()).
 * `ps` runs with TZ=UTC so the wall-clock text converts without local-time DST ambiguity.
 */
export function processStartEpoch(pid: number): number | null {
  if (!processExists(pid)) return null;
  const result = spawnSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8', env: { ...process.env, LC_ALL: 'C', TZ: 'UTC' } });
  if (result.error) throw result.error;
  const text = String(result.stdout ?? '').trim();
  if (text === '') { if (!processExists(pid)) return null; throw new Error(`Process ${pid} start time is unavailable`); }
  const match = /^[A-Z][a-z]{2}\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s+(\d{4})$/.exec(text);
  const month = match ? MONTHS.indexOf(match[1]) : -1;
  if (!match || month < 0) throw new Error(`Process ${pid} start time is unparseable: ${text}`);
  return Math.floor(Date.UTC(Number(match[6]), month, Number(match[2]), Number(match[3]), Number(match[4]), Number(match[5])) / 1000);
}
/** Durable claim identity "<pid>:<start epoch seconds>"; a bare "<pid>" is the legacy form. */
export function currentProcessIdentity(): string {
  if (cachedProcessIdentity === undefined) {
    let started: number | null = null;
    try { started = processStartEpoch(process.pid); } catch { started = null; }
    cachedProcessIdentity = started === null ? `${process.pid}` : `${process.pid}:${started}`;
  }
  return cachedProcessIdentity;
}
/** live: same PID incarnation; dead: PID gone or reused; unproved: anything else, which still blocks. */
export function mutationOwnerStatus(identity: string): 'live' | 'dead' | 'unproved' {
  const match = /^(\d+)(?::(\d+))?$/.exec(identity);
  if (!match) return 'unproved';
  const pid = Number(match[1]);
  if (!Number.isSafeInteger(pid) || pid <= 0) return 'unproved';
  let started: number | null;
  try { started = processStartEpoch(pid); } catch { return 'unproved'; }
  if (started === null) return 'dead';
  if (match[2] === undefined) return 'unproved';
  return started === Number(match[2]) ? 'live' : 'dead';
}
function row(db: Database.Database, guid: string): HandoffRow | undefined {
  return db.prepare('SELECT * FROM workspace_handoffs WHERE workspace_guid = ?').get(guid) as HandoffRow | undefined;
}
function assertEvidence(db: Database.Database, input: HandoffInput, handoff: HandoffRow, phase: string, allowDescendantHead = false): void {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(input.workspaceGuid) || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(input.sourceOwnerSessionId) || !Number.isInteger(input.generation) || input.generation <= 0 || input.capabilityToken.length === 0) throw new Error('Malformed handoff capability input');
  if (handoff.phase !== phase || handoff.generation !== input.generation || !equalHash(handoff.capability_token_hash, input.capabilityToken)) throw new Error('Handoff capability or generation is invalid');
  const assignment = db.prepare('SELECT * FROM assignments WHERE workspace_guid = ?').get(input.workspaceGuid) as Record<string, unknown> | undefined;
  const expectedOwner = phase === 'transferred' ? handoff.target_owner_session_id : input.sourceOwnerSessionId;
  if (!assignment || assignment.owner_session_id !== expectedOwner || assignment.lifecycle_status !== 'active') throw new Error('Handoff source assignment is not active owner');
  const fields: Array<[keyof HandoffRow, unknown]> = [
    ['repository_identity', input.repositoryIdentity], ['worker_id', input.workerId], ['worktree_path', input.expectedWorktreePath], ['branch', input.expectedBranch],
    ['base_commit', input.expectedBaseCommit], ['current_head', input.expectedCurrentHead], ['integration_target', input.expectedIntegrationTarget],
  ];
  for (const [key, expected] of fields) if (handoff[key] !== expected || (assignment[key] !== expected && !(allowDescendantHead && key === 'current_head'))) throw new Error('Handoff durable evidence mismatch');
  if (handoff.source_owner_session_id !== input.sourceOwnerSessionId) throw new Error('Handoff source owner mismatch');
  const repository = discoverRepository(input.repositoryPath);
  if (repository.repositoryIdentity !== input.repositoryIdentity) throw new Error('Handoff repository identity mismatch');
  if (input.expectedWorktreePath !== path.join(repository.primaryCheckoutPath, '.ironclaude', 'worktrees', input.workspaceGuid) || input.expectedBranch !== `ironclaude/${input.workspaceGuid}`) throw new Error('Handoff managed identity is not canonical');
  const observed = listWorktrees(repository.primaryCheckoutPath).find((candidate) => candidate.path === input.expectedWorktreePath);
  const liveHead = observed ? worktreeHead(input.expectedWorktreePath) : '';
  // H6b: only the after-target finish accepts a HEAD that fast-forwards from the granted HEAD.
  if (!observed || observed.branch !== `refs/heads/${input.expectedBranch}` || (liveHead !== input.expectedCurrentHead && !(allowDescendantHead && isAncestor(input.expectedWorktreePath, input.expectedCurrentHead, liveHead)))) throw new Error('Handoff live Git evidence mismatch');
  for (const directory of ['rebase-merge', 'rebase-apply']) {
    const rebasePath = path.resolve(input.expectedWorktreePath, runGitPath(input.expectedWorktreePath, directory));
    if (existsSync(rebasePath)) throw new Error('Handoff cannot transfer a worktree with rebase in progress');
  }
  if (db.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ? AND workspace_guid = ?').get(input.repositoryIdentity, input.workspaceGuid)) throw new Error('Handoff cannot transfer primary checkout owner');
  if (db.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?').get(input.repositoryIdentity, input.workspaceGuid)) throw new Error('Handoff cannot transfer integration-locked assignment');
}
function runGitPath(worktreePath: string, name: string): string { return runGit(worktreePath, ['rev-parse', '--git-path', name]).trim(); }
function beginHandoffOnce(db: Database.Database, input: HandoffInput): void {
  db.transaction(() => {
    const handoff = row(db, input.workspaceGuid); if (!handoff) throw new Error('Commander handoff grant not found');
    if (db.prepare('SELECT 1 FROM workspace_mutations WHERE workspace_guid = ?').get(input.workspaceGuid)) throw new Error('Handoff cannot begin during assignment mutation');
    assertEvidence(db, input, handoff, 'granted');
    const expiresAt = handoff.grant_expires_at ? Date.parse(handoff.grant_expires_at) : NaN;
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw new Error('Commander handoff grant is expired');
    if (db.prepare("UPDATE workspace_handoffs SET phase = 'begun', updated_at = datetime('now') WHERE workspace_guid = ? AND phase = 'granted'").run(input.workspaceGuid).changes !== 1) throw new Error('Handoff grant changed concurrently');
  }).immediate();
}
export function beginHandoff(db: Database.Database, input: HandoffInput): void {
  try { beginHandoffOnce(db, input); } catch (error) {
    if (!(error instanceof Error) || error.message !== 'Handoff cannot begin during assignment mutation' || !reapDeadAssignmentMutation(db, input.workspaceGuid)) throw error;
    beginHandoffOnce(db, input);
  }
}
export function transferHandoff(db: Database.Database, input: TransferHandoffInput): void {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(input.targetOwnerSessionId) || input.targetOwnerSessionId === input.sourceOwnerSessionId) throw new Error('Handoff target owner is invalid');
  db.transaction(() => {
    const handoff = row(db, input.workspaceGuid); if (!handoff) throw new Error('Handoff not found'); assertEvidence(db, input, handoff, 'begun'); if (handoff.target_owner_session_id !== input.targetOwnerSessionId) throw new Error('Handoff target is not Commander-authorized');
    const changed = db.prepare(`UPDATE assignments SET owner_session_id = ?, updated_at = datetime('now')
      WHERE workspace_guid = ? AND owner_session_id = ? AND repository_identity = ? AND worker_id = ? AND worktree_path = ? AND branch = ? AND base_commit = ? AND current_head = ? AND integration_target = ? AND lifecycle_status = 'active'`).run(input.targetOwnerSessionId, input.workspaceGuid, input.sourceOwnerSessionId, input.repositoryIdentity, input.workerId, input.expectedWorktreePath, input.expectedBranch, input.expectedBaseCommit, input.expectedCurrentHead, input.expectedIntegrationTarget);
    if (changed.changes !== 1) throw new Error('Handoff ownership compare-and-swap failed');
    db.prepare("UPDATE human_intents SET consumed_at = datetime('now') WHERE workspace_guid = ? AND provider_root_session_id = ? AND consumed_at IS NULL").run(input.workspaceGuid, input.sourceOwnerSessionId);
    db.prepare('UPDATE preserved_work SET owner_session_id = ? WHERE workspace_guid = ? AND owner_session_id = ? AND resolved_at IS NULL').run(input.targetOwnerSessionId, input.workspaceGuid, input.sourceOwnerSessionId);
    if (db.prepare("UPDATE workspace_handoffs SET target_owner_session_id = ?, phase = 'transferred', updated_at = datetime('now') WHERE workspace_guid = ? AND phase = 'begun'").run(input.targetOwnerSessionId, input.workspaceGuid).changes !== 1) throw new Error('Handoff transfer changed concurrently');
  }).immediate();
}
/**
 * H6b: finish after the target committed. Phase must be transferred, so assertEvidence requires
 * the assignment owner to be the recorded target; the live HEAD may equal or descend from the
 * granted HEAD. The normal finishHandoff keeps the exact-HEAD check.
 */
export function finishHandoffAfterTarget(db: Database.Database, input: HandoffInput): void {
  db.transaction(() => { const handoff = row(db, input.workspaceGuid); if (!handoff) throw new Error('Handoff not found'); assertEvidence(db, input, handoff, 'transferred', true);
    if (db.prepare("UPDATE workspace_handoffs SET phase = 'finished', updated_at = datetime('now') WHERE workspace_guid = ? AND phase = 'transferred'").run(input.workspaceGuid).changes !== 1) throw new Error('Handoff finish changed concurrently'); }).immediate();
}
export function finishHandoff(db: Database.Database, input: HandoffInput): void {
  db.transaction(() => { const handoff = row(db, input.workspaceGuid); if (!handoff) throw new Error('Handoff not found'); assertEvidence(db, input, handoff, 'transferred');
    if (db.prepare("UPDATE workspace_handoffs SET phase = 'finished', updated_at = datetime('now') WHERE workspace_guid = ? AND phase = 'transferred'").run(input.workspaceGuid).changes !== 1) throw new Error('Handoff finish changed concurrently'); }).immediate();
}
/**
 * H5/H6a: finish a granted or begun source hold, only while the assignment owner is still the
 * source. A recorded (authorized) target owner does not block release before the transfer
 * lands; the owner-still-source check is the invariant. Replaying a finished release is a no-op.
 */
export function releaseHandoff(db: Database.Database, input: ReleaseHandoffInput): void {
  if (!UUID_PATTERN.test(input.workspaceGuid) || !UUID_PATTERN.test(input.sourceOwnerSessionId) || !Number.isInteger(input.generation) || input.generation <= 0 || input.capabilityToken.length === 0) throw new Error('Malformed handoff release input');
  db.transaction(() => {
    const handoff = row(db, input.workspaceGuid);
    if (!handoff || handoff.generation !== input.generation) {
      if (handoff && handoff.phase !== 'finished') throw new Error('Handoff capability or generation is invalid');
      return;
    }
    if (!equalHash(handoff.capability_token_hash, input.capabilityToken)) throw new Error('Handoff capability or generation is invalid');
    if (handoff.source_owner_session_id !== input.sourceOwnerSessionId) throw new Error('Handoff source owner mismatch');
    if (handoff.phase === 'finished') return;
    const assignment = db.prepare('SELECT owner_session_id FROM assignments WHERE workspace_guid = ?').get(input.workspaceGuid) as { owner_session_id: string | null } | undefined;
    if (!assignment || assignment.owner_session_id !== input.sourceOwnerSessionId) throw new Error('Handoff source no longer owns assignment; release refused');
    if (handoff.phase !== 'granted' && handoff.phase !== 'begun') throw new Error('Handoff phase cannot be released');
    if (db.prepare("UPDATE workspace_handoffs SET phase = 'finished', updated_at = datetime('now') WHERE workspace_guid = ? AND generation = ? AND phase IN ('granted', 'begun')").run(input.workspaceGuid, input.generation).changes !== 1) throw new Error('Handoff release changed concurrently');
  }).immediate();
}
export function assertNoPendingHandoff(db: Database.Database, workspaceGuid: string): void {
  if (db.prepare("SELECT 1 FROM workspace_handoffs WHERE workspace_guid = ? AND phase <> 'finished'").get(workspaceGuid)) throw new Error('Assignment handoff is pending');
}
function insertMutationClaim(db: Database.Database, workspaceGuid: string, operation: string, nonce: string): boolean {
  let inserted = false;
  db.transaction(() => { assertNoPendingHandoff(db, workspaceGuid);
    try { db.prepare('INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) VALUES (?, ?, ?, ?)').run(workspaceGuid, operation, nonce, currentProcessIdentity()); inserted = true; } catch { inserted = false; } }).immediate();
  return inserted;
}
/** Reaps the claim on one assignment only when its owner is proved dead (H11). */
export function reapDeadAssignmentMutation(db: Database.Database, workspaceGuid: string): boolean {
  const claim = db.prepare('SELECT nonce, process_identity FROM workspace_mutations WHERE workspace_guid = ?').get(workspaceGuid) as { nonce: string; process_identity: string } | undefined;
  if (!claim || mutationOwnerStatus(claim.process_identity) !== 'dead') return false;
  return db.prepare('DELETE FROM workspace_mutations WHERE workspace_guid = ? AND nonce = ? AND process_identity = ?').run(workspaceGuid, claim.nonce, claim.process_identity).changes === 1;
}
/** Startup sweep (H12): reaps every proved-dead claim; live and unproved claims stay. */
export function reapStaleMutations(db: Database.Database): string[] {
  const claims = db.prepare('SELECT workspace_guid FROM workspace_mutations ORDER BY workspace_guid').all() as { workspace_guid: string }[];
  return claims.filter((claim) => reapDeadAssignmentMutation(db, claim.workspace_guid)).map((claim) => claim.workspace_guid);
}
export function withAssignmentMutation<T>(db: Database.Database, workspaceGuid: string, operation: string, fn: () => T): T {
  const nested = localClaims.get(workspaceGuid); if (nested) { if (nested.db !== db) throw new Error('Assignment mutation token belongs to another database'); nested.depth++; try { return fn(); } finally { nested.depth--; } }
  const nonce = randomUUID();
  if (!insertMutationClaim(db, workspaceGuid, operation, nonce)
    && !(reapDeadAssignmentMutation(db, workspaceGuid) && insertMutationClaim(db, workspaceGuid, operation, nonce))) {
    throw new Error('Assignment mutation is already claimed');
  }
  localClaims.set(workspaceGuid, { db, nonce, depth: 1 });
  try { return fn(); } finally { localClaims.delete(workspaceGuid); db.prepare('DELETE FROM workspace_mutations WHERE workspace_guid = ? AND nonce = ?').run(workspaceGuid, nonce); }
}
