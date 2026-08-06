#!/usr/bin/env node

// src/hook-intent.ts
import path5 from "node:path";
import { fileURLToPath } from "node:url";

// src/db.ts
import Database from "better-sqlite3";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
var UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
var TRANSITIONS = {
  reserved: ["materialized", "abandoned"],
  materialized: ["active", "abandoned"],
  active: ["ready_for_integration", "abandoned"],
  ready_for_integration: ["active", "integrated", "abandoned"],
  integrated: ["cleaned"],
  abandoned: ["cleaned"],
  cleaned: []
};
function getDbPath() {
  if (process.env.WORKSPACE_MANAGER_DB_PATH) return process.env.WORKSPACE_MANAGER_DB_PATH;
  return path.join(os.homedir(), ".claude", "ironclaude-workspaces.db");
}
function requiredText(value, label) {
  if (value.length === 0) throw new Error(`${label} must not be empty`);
  return value;
}
function requiredUuid(value, label) {
  if (!UUID_PATTERN.test(value)) throw new Error(`${label} must be a UUID`);
  return value;
}
function canonicalIsoTimestamp(value, label) {
  if (value.length === 0 || Number.isNaN(Date.parse(value))) {
    throw new Error(`${label} must be an ISO timestamp`);
  }
  return new Date(value).toISOString();
}
function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("expectedEvidence must be JSON serializable");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const record = value;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
  }
  throw new Error("expectedEvidence must be JSON serializable");
}
function migrateSchema(db) {
  db.transaction(() => {
    db.exec(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS assignments (
        workspace_guid TEXT PRIMARY KEY,
        repository_identity TEXT NOT NULL,
        worktree_path TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_commit TEXT NOT NULL,
        current_head TEXT NOT NULL,
        owner_session_id TEXT,
        worker_id TEXT,
        lifecycle_status TEXT NOT NULL DEFAULT 'reserved'
          CHECK (lifecycle_status IN ('reserved', 'materialized', 'active', 'ready_for_integration', 'integrated', 'abandoned', 'cleaned')),
        integration_target TEXT NOT NULL,
        integrated_commit TEXT,
        recovery_ref TEXT,
        disposition TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS primary_checkout_owners (
        repository_identity TEXT PRIMARY KEY,
        workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid),
        owner_session_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS integration_locks (
        repository_identity TEXT PRIMARY KEY,
        workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid),
        target_ref TEXT NOT NULL,
        expected_target TEXT NOT NULL,
        acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS integration_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_guid TEXT NOT NULL UNIQUE REFERENCES assignments(workspace_guid),
        repository_identity TEXT NOT NULL,
        target_ref TEXT NOT NULL,
        integrated_commit TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS human_intents (
        intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push')),
        human_channel TEXT NOT NULL,
        provider_root_session_id TEXT NOT NULL,
        repository_identity TEXT NOT NULL,
        workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid),
        expected_evidence TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        nonce TEXT NOT NULL UNIQUE,
        issued_at TEXT NOT NULL DEFAULT (datetime('now')),
        consumed_at TEXT
      );

      CREATE UNIQUE INDEX IF NOT EXISTS active_assignment_owner_repository
        ON assignments(owner_session_id, repository_identity)
        WHERE owner_session_id IS NOT NULL
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned');
      CREATE INDEX IF NOT EXISTS assignments_repository_idx ON assignments(repository_identity);
      CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
        ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);

      CREATE TRIGGER IF NOT EXISTS prevent_workspace_guid_mutation
      BEFORE UPDATE OF workspace_guid ON assignments
      WHEN NEW.workspace_guid <> OLD.workspace_guid
      BEGIN
        SELECT RAISE(ABORT, 'workspace GUID is immutable');
      END;

      INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
    `);
  })();
}
function initDb(dbPath) {
  const resolvedPath = dbPath || getDbPath();
  if (resolvedPath !== ":memory:") fs.mkdirSync(path.dirname(resolvedPath), { recursive: true });
  const db = new Database(resolvedPath, { timeout: 1e4 });
  db.pragma("foreign_keys = ON");
  db.pragma("journal_mode = WAL");
  migrateSchema(db);
  return db;
}
function getAssignment(db, workspaceGuid) {
  return db.prepare("SELECT * FROM assignments WHERE workspace_guid = ?").get(workspaceGuid);
}
function createAssignment(db, input) {
  const workspaceGuid = requiredUuid(input.workspaceGuid, "workspaceGuid");
  const ownerSessionId = input.ownerSessionId == null ? null : requiredText(input.ownerSessionId, "ownerSessionId");
  db.prepare(`
    INSERT INTO assignments (
      workspace_guid, repository_identity, worktree_path, branch, base_commit, current_head,
      owner_session_id, worker_id, integration_target
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    workspaceGuid,
    requiredText(input.repositoryIdentity, "repositoryIdentity"),
    requiredText(input.worktreePath, "worktreePath"),
    requiredText(input.branch, "branch"),
    requiredText(input.baseCommit, "baseCommit"),
    requiredText(input.currentHead, "currentHead"),
    ownerSessionId,
    input.workerId == null ? null : requiredText(input.workerId, "workerId"),
    requiredText(input.integrationTarget, "integrationTarget")
  );
  return getAssignment(db, workspaceGuid);
}
function bindAssignmentOwner(db, workspaceGuid, ownerSessionId) {
  const existing = getAssignment(db, workspaceGuid);
  if (!existing) throw new Error("Assignment not found");
  const owner = requiredText(ownerSessionId, "ownerSessionId");
  if (existing.owner_session_id === owner) return existing;
  if (existing.owner_session_id !== null) throw new Error("Assignment owner is already bound");
  const result = db.prepare(`
    UPDATE assignments SET owner_session_id = ?, updated_at = datetime('now')
    WHERE workspace_guid = ? AND owner_session_id IS NULL
  `).run(owner, workspaceGuid);
  if (result.changes !== 1) throw new Error("Assignment owner binding changed concurrently");
  return getAssignment(db, workspaceGuid);
}
function transitionAssignment(db, workspaceGuid, expectedStatus, nextStatus) {
  if (!TRANSITIONS[expectedStatus].includes(nextStatus)) {
    throw new Error(`Invalid lifecycle transition: ${expectedStatus} -> ${nextStatus}`);
  }
  const result = db.prepare(`
    UPDATE assignments SET lifecycle_status = ?, updated_at = datetime('now')
    WHERE workspace_guid = ? AND lifecycle_status = ?
  `).run(nextStatus, workspaceGuid, expectedStatus);
  if (result.changes !== 1) throw new Error("Assignment lifecycle state changed concurrently or assignment was not found");
  return getAssignment(db, workspaceGuid);
}
function acquirePrimaryCheckoutOwnership(db, input) {
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== input.repositoryIdentity || assignment.owner_session_id !== input.ownerSessionId) {
    throw new Error("Primary checkout ownership does not match assignment binding");
  }
  try {
    db.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(input.repositoryIdentity, input.workspaceGuid, input.ownerSessionId);
  } catch (error) {
    const owner = db.prepare("SELECT * FROM primary_checkout_owners WHERE repository_identity = ?").get(input.repositoryIdentity);
    if (owner?.workspace_guid === input.workspaceGuid && owner.owner_session_id === input.ownerSessionId) return owner;
    throw new Error("Primary checkout is already owned");
  }
  return db.prepare("SELECT * FROM primary_checkout_owners WHERE repository_identity = ?").get(input.repositoryIdentity);
}
function releasePrimaryCheckoutOwnership(db, repositoryIdentity, workspaceGuid, ownerSessionId) {
  const result = db.prepare(`
    DELETE FROM primary_checkout_owners
    WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ?
  `).run(repositoryIdentity, workspaceGuid, ownerSessionId);
  if (result.changes !== 1) throw new Error("Primary checkout ownership was not held by this assignment");
}
function createHumanIntent(db, input) {
  const expiresAt = canonicalIsoTimestamp(input.expiresAt, "expiresAt");
  const result = db.prepare(`
    INSERT INTO human_intents (
      operation, human_channel, provider_root_session_id, repository_identity,
      workspace_guid, expected_evidence, expires_at, nonce
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    input.operation,
    requiredText(input.humanChannel, "humanChannel"),
    requiredText(input.providerRootSessionId, "providerRootSessionId"),
    requiredText(input.repositoryIdentity, "repositoryIdentity"),
    requiredUuid(input.workspaceGuid, "workspaceGuid"),
    canonicalJson(input.expectedEvidence),
    expiresAt,
    requiredText(input.nonce, "nonce")
  );
  return db.prepare("SELECT * FROM human_intents WHERE intent_id = ?").get(result.lastInsertRowid);
}
function issueHumanIntent(db, input, clock = () => /* @__PURE__ */ new Date(), nonceFactory = randomUUID) {
  const issuedAt = canonicalIsoTimestamp(clock().toISOString(), "server clock");
  const expiresAt = canonicalIsoTimestamp(new Date(Date.parse(issuedAt) + 5 * 60 * 1e3).toISOString(), "expiresAt");
  return db.transaction(() => {
    db.prepare(`
      UPDATE human_intents SET consumed_at = ?
      WHERE operation = ?
        AND human_channel = ?
        AND provider_root_session_id = ?
        AND repository_identity = ?
        AND workspace_guid = ?
        AND consumed_at IS NULL
    `).run(
      issuedAt,
      input.operation,
      input.humanChannel,
      input.providerRootSessionId,
      input.repositoryIdentity,
      input.workspaceGuid
    );
    createHumanIntent(db, {
      ...input,
      expiresAt,
      nonce: requiredText(nonceFactory(), "nonce")
    });
    return { issued: true, operation: input.operation };
  })();
}
function consumeHumanIntent(db, input, clock = () => /* @__PURE__ */ new Date()) {
  const evidence = canonicalJson(input.expectedEvidence);
  const now = canonicalIsoTimestamp(clock().toISOString(), "server clock");
  return db.transaction(() => {
    const result = db.prepare(`
      UPDATE human_intents SET consumed_at = ?
      WHERE operation = ?
        AND human_channel = ?
        AND provider_root_session_id = ?
        AND repository_identity = ?
        AND workspace_guid = ?
        AND expected_evidence = ?
        AND nonce = ?
        AND consumed_at IS NULL
        AND expires_at > ?
    `).run(
      now,
      input.operation,
      input.humanChannel,
      input.providerRootSessionId,
      input.repositoryIdentity,
      input.workspaceGuid,
      evidence,
      input.nonce,
      now
    );
    if (result.changes !== 1) return void 0;
    return db.prepare("SELECT * FROM human_intents WHERE nonce = ?").get(input.nonce);
  })();
}
function consumeMatchingHumanIntent(db, input, clock = () => /* @__PURE__ */ new Date()) {
  const evidence = canonicalJson(input.expectedEvidence);
  const now = canonicalIsoTimestamp(clock().toISOString(), "server clock");
  return db.transaction(() => {
    const candidate = db.prepare(`
      SELECT intent_id FROM human_intents
      WHERE operation = ?
        AND human_channel = ?
        AND provider_root_session_id = ?
        AND repository_identity = ?
        AND workspace_guid = ?
        AND expected_evidence = ?
        AND consumed_at IS NULL
        AND expires_at > ?
      ORDER BY intent_id DESC
      LIMIT 1
    `).get(
      input.operation,
      input.humanChannel,
      input.providerRootSessionId,
      input.repositoryIdentity,
      input.workspaceGuid,
      evidence,
      now
    );
    if (!candidate) return void 0;
    const result = db.prepare(`
      UPDATE human_intents SET consumed_at = ?
      WHERE intent_id = ? AND consumed_at IS NULL AND expires_at > ?
    `).run(now, candidate.intent_id, now);
    if (result.changes !== 1) return void 0;
    return db.prepare("SELECT * FROM human_intents WHERE intent_id = ?").get(candidate.intent_id);
  })();
}

// src/git-authority.ts
import path3 from "node:path";

// src/git.ts
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import path2 from "node:path";
var MANAGED_WORKTREE_EXCLUSION = "/.ironclaude/worktrees/";
function gitError(cwd, args, stderr) {
  const detail = stderr.trim() || "Git command failed";
  return new Error(`${detail} (git -C ${cwd} ${args.join(" ")})`);
}
function runGit(cwd, args) {
  const result = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw gitError(cwd, args, result.stderr || "");
  return result.stdout || "";
}
function absoluteFrom(cwd, value) {
  return path2.resolve(cwd, value);
}
function canonicalPath(value) {
  return realpathSync(value);
}
function listWorktrees(cwd) {
  const output = runGit(cwd, ["worktree", "list", "--porcelain"]);
  const entries = [];
  let current;
  for (const line of output.split("\n")) {
    if (line === "") {
      if (current?.path) {
        entries.push({ path: canonicalPath(current.path), head: current.head ?? null, branch: current.branch ?? null, bare: current.bare === true });
      }
      current = void 0;
      continue;
    }
    const separator = line.indexOf(" ");
    const key = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1);
    if (key === "worktree") current = { path: value, bare: false };
    else if (!current) throw new Error("Malformed git worktree porcelain output");
    else if (key === "HEAD") current.head = value;
    else if (key === "branch") current.branch = value;
    else if (key === "bare") current.bare = true;
  }
  if (current?.path) {
    entries.push({ path: canonicalPath(current.path), head: current.head ?? null, branch: current.branch ?? null, bare: current.bare === true });
  }
  return entries;
}
function discoverRepository(cwd) {
  const commonDirectory = runGit(cwd, ["rev-parse", "--git-common-dir"]).trim();
  const repositoryIdentity = canonicalPath(absoluteFrom(cwd, commonDirectory));
  const worktrees = listWorktrees(cwd);
  const primary = worktrees[0];
  if (!primary || primary.bare) throw new Error("Repository has no primary checkout");
  return { repositoryIdentity, primaryCheckoutPath: primary.path };
}
function worktreeIsClean(worktreePath) {
  return runGit(worktreePath, ["status", "--porcelain=v1", "--untracked-files=all"]) === "";
}
function worktreeHead(worktreePath) {
  return runGit(worktreePath, ["rev-parse", "HEAD"]).trim();
}
function primaryBranch(primaryCheckoutPath) {
  let ref;
  try {
    ref = runGit(primaryCheckoutPath, ["symbolic-ref", "--quiet", "HEAD"]).trim();
  } catch {
    throw new Error("Primary checkout is in detached HEAD; supply integration_target explicitly");
  }
  if (!ref.startsWith("refs/heads/")) {
    throw new Error("Primary checkout is not on a branch; supply integration_target explicitly");
  }
  return ref.slice("refs/heads/".length);
}
function addWorktree(primaryCheckoutPath, worktreePath, branch, baseCommit) {
  if (existsSync(worktreePath)) throw new Error(`Managed worktree path already exists: ${worktreePath}`);
  runGit(primaryCheckoutPath, ["worktree", "add", "-b", branch, "--", worktreePath, baseCommit]);
}
function ensureManagedWorktreeExclusion(repositoryIdentity) {
  const infoDirectory = path2.join(repositoryIdentity, "info");
  const excludePath = path2.join(infoDirectory, "exclude");
  mkdirSync(infoDirectory, { recursive: true });
  const existing = existsSync(excludePath) ? readFileSync(excludePath) : Buffer.alloc(0);
  const hasExactEntry = existing.toString("utf8").split(/\r?\n/).some((line) => line === MANAGED_WORKTREE_EXCLUSION);
  if (hasExactEntry) return;
  const separator = existing.length === 0 || existing[existing.length - 1] === 10 ? "" : "\n";
  writeFileSync(excludePath, Buffer.concat([
    existing,
    Buffer.from(`${separator}${MANAGED_WORKTREE_EXCLUSION}
`, "utf8")
  ]));
}
function removeWorktree(primaryCheckoutPath, worktreePath) {
  runGit(primaryCheckoutPath, ["worktree", "remove", "--", worktreePath]);
}
function deleteTemporaryBranch(primaryCheckoutPath, branch) {
  runGit(primaryCheckoutPath, ["branch", "-D", "--", branch]);
}
function isAncestor(cwd, ancestor, descendant) {
  const result = spawnSync("git", ["-C", cwd, "merge-base", "--is-ancestor", ancestor, descendant], { encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.status === 0) return true;
  if (result.status === 1 || result.status === 128) return false;
  throw gitError(cwd, ["merge-base", "--is-ancestor", ancestor, descendant], result.stderr || "");
}

// src/git-authority.ts
var OID = /^[0-9a-f]{40,64}$/i;
function denyEvidence() {
  throw new Error("Direct Git authority evidence changed or is malformed");
}
function remoteOldOid(worktreePath, remoteName, destinationRef) {
  const output = runGit(worktreePath, ["ls-remote", "--refs", remoteName, destinationRef]).trim();
  if (output === "") return null;
  const lines = output.split("\n");
  if (lines.length !== 1) denyEvidence();
  const [remoteOid, remoteRef, ...extra] = lines[0].split(/\s+/);
  if (extra.length !== 0 || remoteRef !== destinationRef || !OID.test(remoteOid)) denyEvidence();
  return remoteOid;
}
function integrationDestinationRef(assignment) {
  return assignment.integration_target.startsWith("refs/") ? assignment.integration_target : `refs/heads/${assignment.integration_target}`;
}
function resolveEffectiveCheckout(db, input) {
  const repository = discoverRepository(input.repositoryPath);
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== repository.repositoryIdentity || assignment.owner_session_id !== input.providerRootSessionId) {
    throw new Error("Direct Git authority provider root, repository, or workspace binding does not match");
  }
  const expectedPath = path3.join(repository.primaryCheckoutPath, ".ironclaude", "worktrees", assignment.workspace_guid);
  if (assignment.worktree_path !== expectedPath || assignment.branch !== `ironclaude/${assignment.workspace_guid}`) {
    throw new Error("Direct Git authority managed workspace identity does not match");
  }
  const worktree = listWorktrees(repository.primaryCheckoutPath).find((candidate) => candidate.path === assignment.worktree_path);
  if (!worktree || worktree.branch !== `refs/heads/${assignment.branch}`) {
    throw new Error("Direct Git authority managed workspace Git identity does not match");
  }
  const primaryOwner = db.prepare(`
    SELECT workspace_guid, owner_session_id FROM primary_checkout_owners
    WHERE repository_identity = ?
  `).get(repository.repositoryIdentity);
  if (!primaryOwner) return { assignment, mode: "managed", path: assignment.worktree_path };
  if (primaryOwner.workspace_guid !== assignment.workspace_guid || primaryOwner.owner_session_id !== input.providerRootSessionId) {
    throw new Error("Direct Git authority primary checkout is owned by another assignment or provider root");
  }
  return { assignment, mode: "primary", path: repository.primaryCheckoutPath };
}
function observeDirectEvidence(checkout, operation) {
  const { assignment } = checkout;
  const canonicalBranch = runGit(checkout.path, ["symbolic-ref", "--quiet", "--short", "HEAD"]).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const branch = { checkoutMode: checkout.mode, canonicalBranch, localRef };
  if (checkout.mode === "managed" && canonicalBranch !== assignment.branch) denyEvidence();
  if (operation === "commit") {
    return {
      ...branch,
      stagedTree: runGit(checkout.path, ["write-tree"]).trim(),
      parentRef: "HEAD",
      parentOid: runGit(checkout.path, ["rev-parse", "--verify", "HEAD^{commit}"]).trim()
    };
  }
  const remoteName = "origin";
  const remoteUrl = runGit(checkout.path, ["remote", "get-url", remoteName]).trim();
  const pushUrl = runGit(checkout.path, ["remote", "get-url", "--push", remoteName]).trim();
  if (remoteUrl !== pushUrl) denyEvidence();
  const destinationRef = operation === "commit-and-push" ? integrationDestinationRef(assignment) : localRef;
  const remote = {
    remoteName,
    remoteUrl,
    destinationRef,
    expectedRemoteOldOid: remoteOldOid(checkout.path, remoteName, destinationRef)
  };
  if (operation === "commit-and-push") {
    return {
      ...branch,
      stagedTree: runGit(checkout.path, ["write-tree"]).trim(),
      parentRef: "HEAD",
      parentOid: runGit(checkout.path, ["rev-parse", "--verify", "HEAD^{commit}"]).trim(),
      ...remote
    };
  }
  if (operation === "push") {
    return {
      ...branch,
      localOid: runGit(checkout.path, ["rev-parse", "--verify", `${localRef}^{commit}`]).trim(),
      ...remote
    };
  }
  throw new Error("Direct Git authority operation is not allowed");
}
function issueDirectGitHumanIntent(db, input) {
  const checkout = resolveEffectiveCheckout(db, input);
  const evidence = observeDirectEvidence(checkout, input.operation);
  return issueHumanIntent(db, {
    operation: input.operation,
    humanChannel: input.humanChannel,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: checkout.assignment.repository_identity,
    workspaceGuid: checkout.assignment.workspace_guid,
    expectedEvidence: evidence
  });
}

// src/workspace-service.ts
import { randomUUID as randomUUID2 } from "node:crypto";
import path4 from "node:path";
function managedWorktreePath(primaryCheckoutPath, workspaceGuid) {
  return path4.join(primaryCheckoutPath, ".ironclaude", "worktrees", workspaceGuid);
}
function managedBranch(workspaceGuid) {
  return `ironclaude/${workspaceGuid}`;
}
function integrationTargetRef(target) {
  return target.startsWith("refs/") ? target : `refs/heads/${target}`;
}
var UUID_PATTERN2 = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function validUuid(value) {
  return UUID_PATTERN2.test(value);
}
function selectWorkspaceGuid(input) {
  if (input.workspaceGuid !== void 0) {
    if (!validUuid(input.workspaceGuid)) throw new Error("workspaceGuid must be a UUID");
    return input.workspaceGuid;
  }
  return validUuid(input.ownerSessionId) ? input.ownerSessionId : randomUUID2();
}
function nonterminal(status) {
  return status !== "integrated" && status !== "abandoned" && status !== "cleaned";
}
function waitForConcurrentReservation(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}
var WorkspaceService = class {
  constructor(db) {
    this.db = db;
  }
  /**
   * Proves a durable row still designates exactly its own managed worktree.
   * Existence alone is insufficient: a different branch at a reused path is
   * ambiguous and must be preserved for reconciliation.
   */
  validateManagedIdentity(repository, assignment) {
    const expectedPath = managedWorktreePath(repository.primaryCheckoutPath, assignment.workspace_guid);
    const expectedBranch = managedBranch(assignment.workspace_guid);
    if (assignment.worktree_path !== expectedPath || assignment.branch !== expectedBranch) {
      throw new Error("Durable assignment no longer matches canonical managed identity; reconciliation must preserve it");
    }
    const observed = listWorktrees(repository.primaryCheckoutPath).find((worktree) => worktree.path === expectedPath);
    if (!observed || observed.branch !== `refs/heads/${assignment.branch}`) {
      throw new Error("Managed worktree Git identity does not match durable assignment; reconciliation must preserve it");
    }
  }
  primaryCheckoutIsOwned(repositoryIdentity) {
    return this.db.prepare("SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?").get(repositoryIdentity) !== void 0;
  }
  materializeManagedWorktree(repository, input) {
    const baseCommit = worktreeHead(repository.primaryCheckoutPath);
    const worktreePath = managedWorktreePath(repository.primaryCheckoutPath, input.workspaceGuid);
    const branch = managedBranch(input.workspaceGuid);
    const assignment = createAssignment(this.db, {
      workspaceGuid: input.workspaceGuid,
      repositoryIdentity: repository.repositoryIdentity,
      worktreePath,
      branch,
      baseCommit,
      currentHead: baseCommit,
      ownerSessionId: input.ownerSessionId,
      workerId: input.workerId,
      integrationTarget: input.integrationTarget
    });
    return this.materializeReservedAssignment(repository, assignment);
  }
  materializeReservedAssignment(repository, assignment) {
    try {
      addWorktree(
        repository.primaryCheckoutPath,
        assignment.worktree_path,
        assignment.branch,
        assignment.base_commit
      );
      transitionAssignment(this.db, assignment.workspace_guid, "reserved", "materialized");
      return transitionAssignment(this.db, assignment.workspace_guid, "materialized", "active");
    } catch (error) {
      throw error;
    }
  }
  ensureSessionWorktree(input) {
    const repository = discoverRepository(input.repositoryPath);
    const workspaceGuid = selectWorkspaceGuid(input);
    const existing = this.db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND owner_session_id = ?
        AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
      ORDER BY created_at ASC
      LIMIT 1
    `).get(repository.repositoryIdentity, input.ownerSessionId);
    if (existing) {
      if (existing.workspace_guid !== workspaceGuid) {
        throw new Error("Provider root is already bound to a different managed workspace");
      }
      this.validateManagedIdentity(repository, existing);
      return existing;
    }
    ensureManagedWorktreeExclusion(repository.repositoryIdentity);
    return this.materializeManagedWorktree(repository, {
      workspaceGuid,
      ownerSessionId: input.ownerSessionId,
      workerId: input.workerId,
      integrationTarget: input.integrationTarget ?? primaryBranch(repository.primaryCheckoutPath)
    });
  }
  reserveWorkerWorktree(input) {
    if (!validUuid(input.workspaceGuid)) throw new Error("workspaceGuid must be a UUID");
    if (input.workerId.length === 0) throw new Error("workerId must not be empty");
    if (input.integrationTarget !== void 0 && input.integrationTarget.length === 0) {
      throw new Error("integrationTarget must not be empty");
    }
    const repository = discoverRepository(input.repositoryPath);
    const baseCommit = worktreeHead(repository.primaryCheckoutPath);
    const worktreePath = managedWorktreePath(repository.primaryCheckoutPath, input.workspaceGuid);
    const branch = managedBranch(input.workspaceGuid);
    const claim = this.db.transaction(() => {
      const existing = getAssignment(this.db, input.workspaceGuid);
      if (existing) {
        this.assertMatchingWorkerReservation(repository, existing, input);
        return { assignment: existing, created: false };
      }
      const other = this.db.prepare(`
        SELECT * FROM assignments
        WHERE repository_identity = ? AND worker_id = ?
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
        ORDER BY created_at ASC LIMIT 1
      `).get(repository.repositoryIdentity, input.workerId);
      if (other) throw new Error("Worker is already reserved to a different managed workspace");
      return {
        assignment: createAssignment(this.db, {
          workspaceGuid: input.workspaceGuid,
          repositoryIdentity: repository.repositoryIdentity,
          worktreePath,
          branch,
          baseCommit,
          currentHead: baseCommit,
          ownerSessionId: null,
          workerId: input.workerId,
          integrationTarget: input.integrationTarget ?? primaryBranch(repository.primaryCheckoutPath)
        }),
        created: true
      };
    }).immediate();
    if (claim.created) {
      ensureManagedWorktreeExclusion(repository.repositoryIdentity);
      return this.materializeReservedAssignment(repository, claim.assignment);
    }
    return this.waitForActiveWorkerReservation(repository, input);
  }
  assertMatchingWorkerReservation(repository, assignment, input) {
    if (assignment.repository_identity !== repository.repositoryIdentity || assignment.worker_id !== input.workerId || assignment.integration_target !== input.integrationTarget || assignment.owner_session_id !== null || !["reserved", "materialized", "active"].includes(assignment.lifecycle_status)) {
      throw new Error("Durable worker reservation does not match requested allocation");
    }
  }
  waitForActiveWorkerReservation(repository, input) {
    const deadline = Date.now() + 1e4;
    while (Date.now() < deadline) {
      const current = getAssignment(this.db, input.workspaceGuid);
      if (!current) throw new Error("Durable worker reservation disappeared during allocation");
      this.assertMatchingWorkerReservation(repository, current, input);
      if (current.lifecycle_status === "active") {
        this.validateManagedIdentity(repository, current);
        if (worktreeHead(current.worktree_path) !== current.current_head) {
          throw new Error("Durable worker reservation HEAD does not match materialized worktree");
        }
        return current;
      }
      waitForConcurrentReservation(25);
    }
    throw new Error("Matching worker reservation is still materializing; preserving durable assignment");
  }
  bindWorkerWorktree(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment || input.repositoryIdentity !== repository.repositoryIdentity || assignment.repository_identity !== input.repositoryIdentity || assignment.worker_id !== input.workerId || input.expectedLifecycle !== "active" || assignment.lifecycle_status !== input.expectedLifecycle || assignment.worktree_path !== input.expectedWorktreePath || assignment.branch !== input.expectedBranch || assignment.base_commit !== input.expectedBaseCommit || assignment.current_head !== input.expectedCurrentHead) {
      throw new Error("Worker reservation evidence does not match durable assignment");
    }
    this.validateManagedIdentity(repository, assignment);
    if (worktreeHead(assignment.worktree_path) !== assignment.current_head) {
      throw new Error("Worker reservation Git HEAD does not match durable assignment");
    }
    return bindAssignmentOwner(this.db, assignment.workspace_guid, input.ownerSessionId);
  }
  getWorkspaceAssignment(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment || assignment.repository_identity !== repository.repositoryIdentity || assignment.owner_session_id !== input.ownerSessionId) {
      throw new Error("Workspace assignment binding does not match repository and provider root");
    }
    return assignment;
  }
  getWorkspaceStatusForRoot(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignments = this.db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND owner_session_id = ?
        AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
      ORDER BY created_at ASC
    `).all(repository.repositoryIdentity, input.ownerSessionId);
    if (assignments.length === 0) {
      return {
        status: "unassigned",
        repositoryIdentity: repository.repositoryIdentity,
        ownerSessionId: input.ownerSessionId
      };
    }
    if (assignments.length !== 1) {
      throw new Error("Workspace status is ambiguous for provider root and repository");
    }
    this.validateManagedIdentity(repository, assignments[0]);
    return { status: "assigned", assignment: assignments[0] };
  }
  checkoutIntentEvidence(repository, assignment, ownerSessionId, operation) {
    this.validateManagedIdentity(repository, assignment);
    const primaryOwner = this.db.prepare(`
      SELECT workspace_guid, owner_session_id FROM primary_checkout_owners
      WHERE repository_identity = ?
    `).get(repository.repositoryIdentity);
    if (operation === "use-primary-checkout") {
      if (primaryOwner) throw new Error("Primary checkout is already owned");
    } else if (!primaryOwner || primaryOwner.workspace_guid !== assignment.workspace_guid || primaryOwner.owner_session_id !== ownerSessionId) {
      throw new Error("Primary checkout ownership does not match assignment binding");
    }
    return {
      checkoutMode: operation === "use-primary-checkout" ? "managed" : "primary",
      primaryCheckoutPath: repository.primaryCheckoutPath,
      managedWorktreePath: assignment.worktree_path,
      branch: assignment.branch,
      currentHead: worktreeHead(assignment.worktree_path),
      lifecycleStatus: assignment.lifecycle_status
    };
  }
  issueCheckoutHumanIntent(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    const expectedEvidence = this.checkoutIntentEvidence(
      repository,
      assignment,
      input.ownerSessionId,
      input.operation
    );
    return issueHumanIntent(this.db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.ownerSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence
    });
  }
  /** Human approval is required before logical ownership of primary checkout. */
  usePrimaryCheckout(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (input.expectedEvidence === void 0 !== (input.nonce === void 0)) {
      throw new Error("Primary checkout switching authority input is malformed");
    }
    const legacyAuthority = input.expectedEvidence !== void 0;
    if (legacyAuthority) this.validateManagedIdentity(repository, assignment);
    const expectedEvidence = legacyAuthority ? input.expectedEvidence : this.checkoutIntentEvidence(repository, assignment, input.ownerSessionId, "use-primary-checkout");
    const intentInput = {
      operation: "use-primary-checkout",
      humanChannel: input.humanChannel,
      providerRootSessionId: input.ownerSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence
    };
    const intent = input.nonce === void 0 ? consumeMatchingHumanIntent(this.db, intentInput) : consumeHumanIntent(this.db, { ...intentInput, nonce: input.nonce });
    if (!intent) throw new Error("Primary checkout switching requires a matching human intent");
    acquirePrimaryCheckoutOwnership(this.db, {
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId: input.ownerSessionId
    });
    return { primaryCheckoutPath: repository.primaryCheckoutPath, assignment };
  }
  /** Human approval is also required to release primary checkout ownership. */
  returnToManagedWorktree(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (input.expectedEvidence === void 0 !== (input.nonce === void 0)) {
      throw new Error("Managed worktree switching authority input is malformed");
    }
    const legacyAuthority = input.expectedEvidence !== void 0;
    if (legacyAuthority) this.validateManagedIdentity(repository, assignment);
    const expectedEvidence = legacyAuthority ? input.expectedEvidence : this.checkoutIntentEvidence(repository, assignment, input.ownerSessionId, "return-to-managed-worktree");
    const intentInput = {
      operation: "return-to-managed-worktree",
      humanChannel: input.humanChannel,
      providerRootSessionId: input.ownerSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence
    };
    const intent = input.nonce === void 0 ? consumeMatchingHumanIntent(this.db, intentInput) : consumeHumanIntent(this.db, { ...intentInput, nonce: input.nonce });
    if (!intent) throw new Error("Managed worktree switching requires a matching human intent");
    releasePrimaryCheckoutOwnership(this.db, repository.repositoryIdentity, assignment.workspace_guid, input.ownerSessionId);
    return { managedWorktreePath: assignment.worktree_path, assignment };
  }
  abandonWorkspace(input) {
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status === "abandoned") return assignment;
    if (!nonterminal(assignment.lifecycle_status)) {
      throw new Error("Only unresolved managed worktrees can be abandoned");
    }
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, "abandoned");
  }
  /**
   * Deletes only a terminal assignment whose recorded recovery/integration
   * proof still reaches its actual Git HEAD. Every failed proof preserves work.
   */
  cleanupWorkspace(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status !== "integrated" && assignment.lifecycle_status !== "abandoned") {
      throw new Error("Only integrated or abandoned worktrees are eligible for cleanup");
    }
    this.validateManagedIdentity(repository, assignment);
    if (!worktreeIsClean(assignment.worktree_path)) {
      throw new Error("Managed worktree is dirty; preserving it");
    }
    if (this.primaryCheckoutIsOwned(repository.repositoryIdentity)) {
      throw new Error("Primary checkout remains owned; preserving managed worktree");
    }
    const actualHead = worktreeHead(assignment.worktree_path);
    if (assignment.lifecycle_status === "abandoned") {
      if (!assignment.recovery_ref || !isAncestor(repository.primaryCheckoutPath, actualHead, assignment.recovery_ref)) {
        throw new Error("Abandoned worktree lacks reachable durable recovery evidence; preserving it");
      }
    } else {
      const integration = this.db.prepare(`
        SELECT target_ref, integrated_commit FROM integration_records
        WHERE workspace_guid = ? AND repository_identity = ?
      `).get(assignment.workspace_guid, repository.repositoryIdentity);
      if (!assignment.integrated_commit || !integration || integration.target_ref !== integrationTargetRef(assignment.integration_target) || integration.integrated_commit !== assignment.integrated_commit || actualHead !== assignment.integrated_commit || !isAncestor(repository.primaryCheckoutPath, assignment.integrated_commit, integration.target_ref)) {
        throw new Error("Integrated worktree lacks reachable durable integration evidence; preserving it");
      }
    }
    removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    deleteTemporaryBranch(repository.primaryCheckoutPath, assignment.branch);
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, "cleaned");
  }
  /** Read-only reconciliation intentionally never deletes missing or unknown worktrees. */
  reconcileRepository(repositoryPath) {
    const repository = discoverRepository(repositoryPath);
    const assignments = this.db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND lifecycle_status <> 'cleaned'
    `).all(repository.repositoryIdentity);
    const observed = listWorktrees(repository.primaryCheckoutPath);
    const observedPaths = new Set(observed.map((worktree) => worktree.path));
    const knownPaths = new Set(assignments.map((assignment) => path4.resolve(assignment.worktree_path)));
    const managedRoot = path4.join(repository.primaryCheckoutPath, ".ironclaude", "worktrees") + path4.sep;
    const ambiguousWorktreePaths = observed.filter((worktree) => worktree.path.startsWith(managedRoot) && worktree.branch?.startsWith("refs/heads/ironclaude/") && !knownPaths.has(worktree.path)).map((worktree) => worktree.path).sort();
    return {
      repositoryIdentity: repository.repositoryIdentity,
      knownWorktreePaths: assignments.map((assignment) => path4.resolve(assignment.worktree_path)).filter((worktreePath) => observedPaths.has(worktreePath)).sort(),
      missingWorktreePaths: assignments.map((assignment) => path4.resolve(assignment.worktree_path)).filter((worktreePath) => !observedPaths.has(worktreePath)).sort(),
      ambiguousWorktreePaths
    };
  }
};

// src/hook-intent.ts
function requiredString(args, key) {
  const value = args[key];
  if (typeof value !== "string" || value.length === 0) throw new Error(`${key} must be a non-empty string`);
  return value;
}
function optionalString(args, key) {
  const value = args[key];
  if (value === void 0) return void 0;
  if (typeof value !== "string" || value.length === 0) throw new Error(`${key} must be a non-empty string`);
  return value;
}
function issueHumanIntentFromHook(db, args) {
  if (requiredString(args, "hook_event_name") !== "UserPromptSubmit" || requiredString(args, "invocation_source") !== "human") {
    throw new Error("Human intent issuance requires the trusted UserPromptSubmit hook");
  }
  const operation = requiredString(args, "operation");
  const humanChannel = requiredString(args, "human_channel");
  if (humanChannel !== "claude-user-prompt" && humanChannel !== "codex-user-prompt") {
    throw new Error("human_channel is not a trusted provider prompt channel");
  }
  const repositoryPath = requiredString(args, "repository_path");
  const ownerSessionId = requiredString(args, "owner_session_id");
  const repository = discoverRepository(repositoryPath);
  const assignments = db.prepare(`
    SELECT * FROM assignments
    WHERE repository_identity = ? AND owner_session_id = ?
      AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
    ORDER BY created_at ASC
  `).all(repository.repositoryIdentity, ownerSessionId);
  if (assignments.length !== 1) {
    throw new Error("Human intent issuance requires exactly one active assignment for provider root and repository");
  }
  const assignment = assignments[0];
  const requestedGuid = optionalString(args, "workspace_guid");
  if (requestedGuid !== void 0 && requestedGuid !== assignment.workspace_guid) {
    throw new Error("Human intent workspace binding does not match active assignment");
  }
  if (operation === "commit" || operation === "commit-and-push" || operation === "push") {
    return issueDirectGitHumanIntent(db, {
      repositoryPath,
      workspaceGuid: assignment.workspace_guid,
      providerRootSessionId: ownerSessionId,
      humanChannel,
      operation
    });
  }
  if (operation === "use-primary-checkout" || operation === "return-to-managed-worktree") {
    return new WorkspaceService(db).issueCheckoutHumanIntent({
      repositoryPath,
      workspaceGuid: assignment.workspace_guid,
      ownerSessionId,
      humanChannel,
      operation
    });
  }
  throw new Error("Human intent operation is not allowed");
}
function runHookIntent(argv = process.argv.slice(2), db = initDb()) {
  if (argv.length !== 1) throw new Error("Hook intent helper expects exactly one JSON payload");
  let args;
  try {
    args = JSON.parse(argv[0]);
  } catch {
    throw new Error("Hook intent payload must be valid JSON");
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    throw new Error("Hook intent payload must be a JSON object");
  }
  return issueHumanIntentFromHook(db, args);
}
var invokedPath = process.argv[1] ? path5.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    process.stdout.write(`${JSON.stringify(runHookIntent())}
`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}
`);
    process.exit(1);
  }
}
export {
  issueHumanIntentFromHook,
  runHookIntent
};
