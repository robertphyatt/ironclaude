#!/usr/bin/env node

// src/cli.ts
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
  integrated: ["cleaned", "active"],
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
var WORKSPACE_SENTINEL_PATTERN = /^primary:.+$/;
function requiredIntentWorkspaceRef(value, label) {
  if (!UUID_PATTERN.test(value) && !WORKSPACE_SENTINEL_PATTERN.test(value)) {
    throw new Error(`${label} must be a UUID or a primary-checkout sentinel`);
  }
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
  if (!db.prepare("SELECT 1 FROM schema_migrations WHERE version = 2").get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE human_intents_v2 (
          intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push')),
          human_channel TEXT NOT NULL,
          provider_root_session_id TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          workspace_guid TEXT NOT NULL,
          expected_evidence TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          nonce TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at TEXT
        );
        INSERT INTO human_intents_v2 (intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at)
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents WHERE workspace_guid IS NOT NULL;
        DROP TABLE human_intents;
        ALTER TABLE human_intents_v2 RENAME TO human_intents;
        CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
          ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
      `);
    })();
  }
  if (!db.prepare("SELECT 1 FROM schema_migrations WHERE version = 3").get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE human_intents_v3 (
          intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push', 'reconcile', 'close-out')),
          human_channel TEXT NOT NULL,
          provider_root_session_id TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          workspace_guid TEXT NOT NULL,
          expected_evidence TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          nonce TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at TEXT
        );
        INSERT INTO human_intents_v3 (intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at)
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents;
        DROP TABLE human_intents;
        ALTER TABLE human_intents_v3 RENAME TO human_intents;
        CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
          ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);
      `);
    })();
  }
  if (!db.prepare("SELECT 1 FROM schema_migrations WHERE version = 4").get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE IF NOT EXISTS preserved_work (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          workspace_guid TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          owner_session_id TEXT,
          kind TEXT NOT NULL CHECK (kind IN ('pending-push', 'recovery')),
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS preserved_work_repo_idx ON preserved_work(repository_identity);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);
      `);
    })();
  }
  if (!db.prepare("SELECT 1 FROM schema_migrations WHERE version = 5").get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE human_intents_v5 (
          intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push', 'reconcile', 'close-out', 'confirm-resolution')),
          human_channel TEXT NOT NULL,
          provider_root_session_id TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          workspace_guid TEXT NOT NULL,
          expected_evidence TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          nonce TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at TEXT
        );
        INSERT INTO human_intents_v5 (intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at)
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents;
        DROP TABLE human_intents;
        ALTER TABLE human_intents_v5 RENAME TO human_intents;
        CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
          ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (5);
      `);
    })();
  }
}
function insertPreservedWork(db, input) {
  const existing = db.prepare(
    "SELECT 1 FROM preserved_work WHERE workspace_guid = ? AND kind = ? AND payload = ? AND resolved_at IS NULL"
  ).get(input.workspaceGuid, input.kind, input.payload);
  if (existing) return;
  db.prepare(
    "INSERT INTO preserved_work (workspace_guid, repository_identity, owner_session_id, kind, payload) VALUES (?, ?, ?, ?, ?)"
  ).run(input.workspaceGuid, input.repositoryIdentity, input.ownerSessionId, input.kind, input.payload);
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
function reuseTerminalAssignment(db, input) {
  const workspaceGuid = requiredUuid(input.workspaceGuid, "workspaceGuid");
  const ownerSessionId = input.ownerSessionId == null ? null : requiredText(input.ownerSessionId, "ownerSessionId");
  db.transaction(() => {
    db.prepare("DELETE FROM integration_records WHERE workspace_guid = ?").run(workspaceGuid);
    const result = db.prepare(`
      UPDATE assignments
      SET repository_identity = ?, worktree_path = ?, branch = ?, base_commit = ?, current_head = ?,
          owner_session_id = ?, worker_id = ?, integration_target = ?,
          integrated_commit = NULL, recovery_ref = NULL, disposition = NULL,
          lifecycle_status = 'reserved', updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'cleaned'
    `).run(
      requiredText(input.repositoryIdentity, "repositoryIdentity"),
      requiredText(input.worktreePath, "worktreePath"),
      requiredText(input.branch, "branch"),
      requiredText(input.baseCommit, "baseCommit"),
      requiredText(input.currentHead, "currentHead"),
      ownerSessionId,
      input.workerId == null ? null : requiredText(input.workerId, "workerId"),
      requiredText(input.integrationTarget, "integrationTarget"),
      workspaceGuid
    );
    if (result.changes !== 1) throw new Error("Terminal assignment reuse target changed concurrently");
  })();
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
var PRIMARY_OWNER_TTL_MINUTES = 60;
function reapStalePrimaryOwner(db, repositoryIdentity) {
  const owner = db.prepare("SELECT * FROM primary_checkout_owners WHERE repository_identity = ?").get(repositoryIdentity);
  if (!owner) return false;
  const assignment = getAssignment(db, owner.workspace_guid);
  const terminal = assignment !== void 0 && (assignment.lifecycle_status === "integrated" || assignment.lifecycle_status === "abandoned" || assignment.lifecycle_status === "cleaned");
  const worktreeMissing = assignment !== void 0 && !fs.existsSync(assignment.worktree_path);
  const ttlExpired = db.prepare(
    "SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ? AND acquired_at < datetime('now', ?)"
  ).get(repositoryIdentity, `-${PRIMARY_OWNER_TTL_MINUTES} minutes`) !== void 0;
  const stale = assignment === void 0 || terminal || worktreeMissing || ttlExpired;
  if (!stale) return false;
  const result = db.prepare(`
    DELETE FROM primary_checkout_owners
    WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ? AND acquired_at = ?
  `).run(repositoryIdentity, owner.workspace_guid, owner.owner_session_id, owner.acquired_at);
  const reclaimed = result.changes === 1;
  if (reclaimed) {
    console.error("reapStalePrimaryOwner: reclaimed stale primary-checkout owner");
  }
  return reclaimed;
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
    if (reapStalePrimaryOwner(db, input.repositoryIdentity)) {
      try {
        db.prepare(`
          INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
          VALUES (?, ?, ?)
        `).run(input.repositoryIdentity, input.workspaceGuid, input.ownerSessionId);
        return db.prepare("SELECT * FROM primary_checkout_owners WHERE repository_identity = ?").get(input.repositoryIdentity);
      } catch {
        throw new Error("Primary checkout is already owned");
      }
    }
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
function acquireIntegrationLock(db, input) {
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== input.repositoryIdentity) {
    throw new Error("Integration lock does not match assignment repository");
  }
  try {
    db.prepare(`
      INSERT INTO integration_locks (repository_identity, workspace_guid, target_ref, expected_target)
      VALUES (?, ?, ?, ?)
    `).run(input.repositoryIdentity, input.workspaceGuid, requiredText(input.targetRef, "targetRef"), requiredText(input.expectedTarget, "expectedTarget"));
  } catch {
    throw new Error("Integration lock is already held");
  }
  return db.prepare("SELECT * FROM integration_locks WHERE repository_identity = ?").get(input.repositoryIdentity);
}
function deleteIntegrationRecord(db, workspaceGuid) {
  db.prepare("DELETE FROM integration_records WHERE workspace_guid = ?").run(workspaceGuid);
}
function recordIntegration(db, input) {
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== input.repositoryIdentity) {
    throw new Error("Integration record does not match assignment repository");
  }
  const result = db.prepare(`
    INSERT INTO integration_records (workspace_guid, repository_identity, target_ref, integrated_commit)
    VALUES (?, ?, ?, ?)
  `).run(input.workspaceGuid, input.repositoryIdentity, requiredText(input.targetRef, "targetRef"), requiredText(input.integratedCommit, "integratedCommit"));
  return db.prepare("SELECT * FROM integration_records WHERE id = ?").get(result.lastInsertRowid);
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
    requiredIntentWorkspaceRef(input.workspaceGuid, "workspaceGuid"),
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

// src/integration.ts
import { existsSync as existsSync2, rmSync, writeFileSync as writeFileSync2 } from "node:fs";
import path3 from "node:path";

// src/git.ts
import { spawnSync } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
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
function worktreeExists(cwd, worktreePath) {
  const canonical = path2.resolve(worktreePath);
  return listWorktrees(cwd).some((entry) => entry.path === canonical);
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
var SHARED_RESOURCE_CONFIG = "worktree-shared-resources";
function appendExcludeLines(repositoryIdentity, lines) {
  const infoDirectory = path2.join(repositoryIdentity, "info");
  const excludePath = path2.join(infoDirectory, "exclude");
  mkdirSync(infoDirectory, { recursive: true });
  const existing = existsSync(excludePath) ? readFileSync(excludePath) : Buffer.alloc(0);
  const present = new Set(existing.toString("utf8").split(/\r?\n/));
  let buffer = existing;
  let appended = false;
  for (const line of lines) {
    if (present.has(line)) continue;
    present.add(line);
    const separator = buffer.length === 0 || buffer[buffer.length - 1] === 10 ? "" : "\n";
    buffer = Buffer.concat([buffer, Buffer.from(`${separator}${line}
`, "utf8")]);
    appended = true;
  }
  if (appended) writeFileSync(excludePath, buffer);
}
function ensureManagedWorktreeExclusion(repositoryIdentity) {
  appendExcludeLines(repositoryIdentity, [MANAGED_WORKTREE_EXCLUSION]);
}
function ensureExcludeEntries(repositoryIdentity, entries) {
  appendExcludeLines(repositoryIdentity, entries.map((entry) => `/${entry}`));
}
function readSharedResourceConfig(repositoryIdentity) {
  const configPath = path2.join(repositoryIdentity, "info", SHARED_RESOURCE_CONFIG);
  if (!existsSync(configPath)) return [];
  return readFileSync(configPath, "utf8").split(/\r?\n/).map((line) => line.trim()).filter((line) => line.length > 0 && !line.startsWith("#"));
}
function isSafeSharedEntry(entry) {
  if (entry.length === 0) return false;
  if (entry.startsWith("!") || entry.startsWith("#")) return false;
  if (entry.startsWith("/") || path2.isAbsolute(entry)) return false;
  if (entry.endsWith("/")) return false;
  if (entry.includes("\\")) return false;
  if (/[*?[\]]/.test(entry)) return false;
  if (entry.split("/").some((segment) => segment === "..")) return false;
  return true;
}
function pathPresent(target) {
  try {
    lstatSync(target);
    return true;
  } catch {
    return false;
  }
}
function linkSharedResources(primaryCheckoutPath, worktreePath, repositoryIdentity, entries) {
  const linked = [];
  for (const entry of entries) {
    if (!isSafeSharedEntry(entry)) {
      console.error(`[workspace-manager] refusing unsafe shared-resource entry: ${entry}`);
      continue;
    }
    const source = path2.join(primaryCheckoutPath, entry);
    const target = path2.join(worktreePath, entry);
    if (!existsSync(source)) {
      console.error(`[workspace-manager] shared resource absent in primary checkout; skipping: ${entry}`);
      continue;
    }
    if (pathPresent(target)) {
      console.error(`[workspace-manager] worktree path already exists; not overwriting: ${entry}`);
      continue;
    }
    try {
      symlinkSync(source, target);
      linked.push(entry);
    } catch (error) {
      console.error(`[workspace-manager] failed to link shared resource ${entry}: ${String(error)}`);
    }
  }
  if (linked.length > 0) {
    ensureExcludeEntries(repositoryIdentity, linked);
  }
}
function removeWorktree(primaryCheckoutPath, worktreePath) {
  runGit(primaryCheckoutPath, ["worktree", "remove", "--", worktreePath]);
}
function deleteTemporaryBranch(primaryCheckoutPath, branch) {
  runGit(primaryCheckoutPath, ["branch", "-D", "--", branch]);
}
function splitPaths(output) {
  return output.split("\n").filter((line) => line.length > 0);
}
function changedPaths(cwd, a, b) {
  return splitPaths(runGit(cwd, ["diff", "--name-only", a, b]));
}
function dirtyAndUntrackedPaths(cwd) {
  const unstaged = splitPaths(runGit(cwd, ["diff", "--name-only"]));
  const staged = splitPaths(runGit(cwd, ["diff", "--name-only", "--cached"]));
  const untracked = splitPaths(runGit(cwd, ["ls-files", "--others", "--exclude-standard"]));
  return [.../* @__PURE__ */ new Set([...unstaged, ...staged, ...untracked])];
}
function carryForwardFastForward(cwd, fromCommit, toCommit) {
  runGit(cwd, ["read-tree", "-m", "-u", fromCommit, toCommit]);
}
function isAncestor(cwd, ancestor, descendant) {
  const result = spawnSync("git", ["-C", cwd, "merge-base", "--is-ancestor", ancestor, descendant], { encoding: "utf8" });
  if (result.error) throw result.error;
  if (result.status === 0) return true;
  if (result.status === 1 || result.status === 128) return false;
  throw gitError(cwd, ["merge-base", "--is-ancestor", ancestor, descendant], result.stderr || "");
}

// src/integration.ts
function encodePushDisposition(value) {
  return JSON.stringify(value);
}
function decodePushDisposition(value) {
  if (!value) return void 0;
  try {
    const parsed = JSON.parse(value);
    if (parsed.phase !== "push-pending" && parsed.phase !== "push-succeeded" && parsed.phase !== "push-failed" || typeof parsed.candidateCommit !== "string" || typeof parsed.frozenCommit !== "string" || typeof parsed.remoteName !== "string" || typeof parsed.remoteUrl !== "string" || typeof parsed.destinationRef !== "string" || parsed.expectedRemoteOldOid !== null && typeof parsed.expectedRemoteOldOid !== "string") return void 0;
    return parsed;
  } catch {
    return void 0;
  }
}
function decodeIntegrationPendingDisposition(value) {
  if (!value) return void 0;
  try {
    const parsed = JSON.parse(value);
    if (parsed.phase !== "integration-pending" || typeof parsed.frozenCommit !== "string" || typeof parsed.remoteName !== "string" || typeof parsed.remoteUrl !== "string" || typeof parsed.destinationRef !== "string" || parsed.expectedRemoteOldOid !== null && typeof parsed.expectedRemoteOldOid !== "string") return void 0;
    return parsed;
  } catch {
    return void 0;
  }
}
function targetRef(assignment) {
  return assignment.integration_target.startsWith("refs/") ? assignment.integration_target : `refs/heads/${assignment.integration_target}`;
}
function freezeRef(workspaceGuid) {
  return `refs/ironclaude/finalization/${workspaceGuid}/frozen`;
}
function candidateRef(workspaceGuid) {
  return `refs/ironclaude/finalization/${workspaceGuid}/candidate`;
}
function setDisposition(db, workspaceGuid, disposition) {
  db.prepare("UPDATE assignments SET disposition = ?, updated_at = datetime('now') WHERE workspace_guid = ?").run(disposition, workspaceGuid);
}
function remoteRefOid(cwd, remoteUrl, destinationRef) {
  const output = runGit(cwd, ["ls-remote", "--refs", remoteUrl, destinationRef]).trim();
  if (output === "") return null;
  const [oid, ref, ...extra] = output.split(/\s+/);
  if (extra.length !== 0 || ref !== destinationRef) throw new Error("Finalization remote proof is malformed");
  return oid;
}
function pushPendingSummary(disposition) {
  const decoded = decodePushDisposition(disposition);
  return decoded && (decoded.phase === "push-pending" || decoded.phase === "push-failed") ? { candidateCommit: decoded.candidateCommit, remoteUrl: decoded.remoteUrl, destinationRef: decoded.destinationRef } : void 0;
}
function cumulativeBinaryEffect(cwd, base, head) {
  return runGit(cwd, ["diff", "--binary", "--full-index", base, head]);
}
function requireExactIntegrationLock(db, assignment, ref, expectedTarget) {
  const lock = db.prepare(`
    SELECT repository_identity, workspace_guid, target_ref, expected_target
    FROM integration_locks WHERE repository_identity = ?
  `).get(assignment.repository_identity);
  if (!lock || lock.repository_identity !== assignment.repository_identity || lock.workspace_guid !== assignment.workspace_guid || lock.target_ref !== ref || lock.expected_target !== expectedTarget) {
    throw new Error("Finalization integration lock changed; preserving worktree");
  }
}
function recoveryIntegrationLockExpectedTarget(db, assignment, ref) {
  const lock = db.prepare(`
    SELECT workspace_guid, target_ref, expected_target
    FROM integration_locks WHERE repository_identity = ?
  `).get(assignment.repository_identity);
  if (!lock || lock.workspace_guid !== assignment.workspace_guid || lock.target_ref !== ref) {
    throw new Error("Crash reconciliation lacks durable pre-fast-forward lock proof; preserving worktree");
  }
  return lock.expected_target;
}
function releaseExactIntegrationLockIfHeld(db, assignment, ref, expectedTarget) {
  db.prepare(`
    DELETE FROM integration_locks
    WHERE repository_identity = ? AND workspace_guid = ? AND target_ref = ? AND expected_target = ?
  `).run(assignment.repository_identity, assignment.workspace_guid, ref, expectedTarget);
}
function requireMessage(message) {
  if (message.length === 0) throw new Error("Finalization commit message must not be empty");
}
var REQUIRED_COMMANDER_FINALIZATION_KEYS = [
  "repositoryPath",
  "workspaceGuid",
  "providerRootSessionId",
  "message",
  "canonicalBranch",
  "localRef",
  "stagedTree",
  "parentOid"
];
function requireCommanderFinalizationInput(input) {
  const required = [...REQUIRED_COMMANDER_FINALIZATION_KEYS].sort();
  const hasDispose = Object.prototype.hasOwnProperty.call(input, "dispose");
  const expected = (hasDispose ? [...required, "dispose"] : required).sort();
  const actual = Object.keys(input).sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]) || required.some((key) => typeof input[key] !== "string" || input[key].length === 0) || hasDispose && input.dispose !== "recycle" && input.dispose !== "release") {
    throw new Error("Commander finalization input is malformed");
  }
}
function validateExactCommitState(sourcePath, evidence) {
  const branch = runGit(sourcePath, ["symbolic-ref", "--quiet", "--short", "HEAD"]).trim();
  const tree = runGit(sourcePath, ["write-tree"]).trim();
  const parent = runGit(sourcePath, ["rev-parse", "--verify", "HEAD^{commit}"]).trim();
  const local = runGit(sourcePath, ["rev-parse", "--verify", `${evidence.localRef}^{commit}`]).trim();
  if (branch !== evidence.canonicalBranch || tree !== evidence.stagedTree || parent !== evidence.parentOid || local !== parent) {
    throw new Error("Reviewed commit evidence changed; preserving worktree");
  }
}
function requireAssignmentCommitBinding(evidence, assignment) {
  if (evidence.canonicalBranch !== assignment.branch || evidence.localRef !== `refs/heads/${assignment.branch}`) {
    throw new Error("Reviewed commit evidence does not bind the assignment branch");
  }
}
function createExactCommit(sourcePath, evidence, message) {
  validateExactCommitState(sourcePath, evidence);
  const commit = runGit(sourcePath, ["commit-tree", evidence.stagedTree, "-p", evidence.parentOid, "-m", message]).trim();
  runGit(sourcePath, ["update-ref", evidence.localRef, commit, evidence.parentOid]);
  if (worktreeHead(sourcePath) !== commit) throw new Error("Exact commit ref update did not update checkout HEAD");
  return commit;
}
function exactAssignment(db, repositoryPath, workspaceGuid, providerRootSessionId) {
  const repository = discoverRepository(repositoryPath);
  const assignment = getAssignment(db, workspaceGuid);
  if (!assignment || assignment.repository_identity !== repository.repositoryIdentity || assignment.owner_session_id !== providerRootSessionId) {
    throw new Error("Finalization assignment binding does not match repository and provider root");
  }
  return { assignment, primaryCheckoutPath: repository.primaryCheckoutPath };
}
function syncWorktreeToTarget(db, input) {
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const assignment = exact.assignment;
  if (assignment.lifecycle_status !== "active") {
    throw new Error("Sync requires an active assignment; preserving worktree");
  }
  const worktree = assignment.worktree_path;
  if (classifyRebaseState(worktree) !== "frozen-no-rebase") {
    throw new Error("Sync refused: a rebase is already in progress in the worktree; preserving worktree");
  }
  const heldLock = db.prepare(`
    SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?
  `).get(assignment.repository_identity, assignment.workspace_guid);
  if (heldLock) {
    throw new Error("Sync refused: an integration lock is held for this repository and workspace; preserving worktree");
  }
  const ref = targetRef(assignment);
  const target = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  const head = worktreeHead(worktree);
  if (head === target) {
    return { state: "no-op", head, baseCommit: assignment.base_commit };
  }
  if (isAncestor(worktree, head, target)) {
    runGit(worktree, ["merge", "--ff-only", target]);
    const newHead2 = worktreeHead(worktree);
    db.prepare(`
      UPDATE assignments SET base_commit = ?, current_head = ?, updated_at = datetime('now') WHERE workspace_guid = ?
    `).run(target, newHead2, assignment.workspace_guid);
    return { state: "fast-forwarded", head: newHead2, baseCommit: target };
  }
  if (!worktreeIsClean(worktree)) {
    throw new Error("Sync requires a clean worktree to rebase local commits onto the target; preserving worktree");
  }
  try {
    runGit(worktree, ["rebase", "--onto", target, assignment.base_commit]);
  } catch (error) {
    const unresolved = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
    try {
      runGit(worktree, ["rebase", "--abort"]);
    } catch {
    }
    if (worktreeHead(worktree) !== head) {
      throw new Error("Sync rebase abort did not restore the original worktree HEAD; preserving worktree");
    }
    throw new Error(
      `Sync rebase conflicted and was aborted; preserving worktree.${unresolved ? ` Unmerged paths: ${unresolved.split("\n").join(", ")}` : ""}`
    );
  }
  const newHead = worktreeHead(worktree);
  db.prepare(`
    UPDATE assignments SET base_commit = ?, current_head = ?, updated_at = datetime('now') WHERE workspace_guid = ?
  `).run(target, newHead, assignment.workspace_guid);
  return { state: "rebased", head: newHead, baseCommit: target };
}
function primaryOnRef(primaryCheckoutPath, ref) {
  try {
    return runGit(primaryCheckoutPath, ["symbolic-ref", "--quiet", "HEAD"]).trim() === ref;
  } catch {
    return false;
  }
}
function assertNoPrimaryOverlap(primaryCheckoutPath, ref, expectedTarget, integrated) {
  if (!primaryOnRef(primaryCheckoutPath, ref)) return;
  const dirty = dirtyAndUntrackedPaths(primaryCheckoutPath);
  if (dirty.length === 0) return;
  const changed = new Set(changedPaths(primaryCheckoutPath, expectedTarget, integrated));
  const overlap = dirty.filter((entry) => changed.has(entry));
  if (overlap.length > 0) {
    throw new Error(
      `Finalization primary checkout has local changes overlapping the carried-forward integration; preserving worktree. Overlapping paths: ${overlap.join(", ")}`
    );
  }
}
function verifyPrimaryTarget(primaryCheckoutPath, ref, expectedTarget) {
  const actualTarget = runGit(primaryCheckoutPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  if (actualTarget !== expectedTarget) {
    throw new Error("Finalization primary checkout is not cleanly checked out at expected target");
  }
  if (primaryOnRef(primaryCheckoutPath, ref)) {
    const actualHead = runGit(primaryCheckoutPath, ["rev-parse", "--verify", "HEAD^{commit}"]).trim();
    if (actualHead !== expectedTarget) {
      throw new Error("Finalization primary checkout is not cleanly checked out at expected target");
    }
  }
}
function verifyPrimaryAfterFastForward(primaryCheckoutPath, ref, expectedTarget, integratedCommit) {
  const target = runGit(primaryCheckoutPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  if (target !== integratedCommit) {
    throw new Error("Finalization primary checkout is inconsistent after checked fast-forward");
  }
  if (!primaryOnRef(primaryCheckoutPath, ref)) return;
  const head = runGit(primaryCheckoutPath, ["rev-parse", "--verify", "HEAD^{commit}"]).trim();
  const unmerged = runGit(primaryCheckoutPath, ["ls-files", "--unmerged"]).trim();
  if (head !== integratedCommit || unmerged !== "") {
    throw new Error("Finalization primary checkout is inconsistent after checked fast-forward");
  }
  const carried = changedPaths(primaryCheckoutPath, expectedTarget, integratedCommit);
  if (carried.length > 0) {
    const worktreeDrift = runGit(primaryCheckoutPath, ["diff", "--name-only", integratedCommit, "--", ...carried]).trim();
    const indexDrift = runGit(primaryCheckoutPath, ["diff", "--name-only", "--cached", integratedCommit, "--", ...carried]).trim();
    if (worktreeDrift !== "" || indexDrift !== "") {
      throw new Error("Finalization primary checkout is inconsistent after checked fast-forward");
    }
  }
}
function markIntegrated(db, assignment, integratedCommit, ref, hooks) {
  const integrationPending = decodeIntegrationPendingDisposition(assignment.disposition);
  if (assignment.disposition && !integrationPending) {
    throw new Error("Finalization integration disposition is malformed");
  }
  const nextDisposition = integrationPending ? encodePushDisposition({ ...integrationPending, phase: "push-pending", candidateCommit: integratedCommit }) : null;
  db.transaction(() => {
    recordIntegration(db, {
      workspaceGuid: assignment.workspace_guid,
      repositoryIdentity: assignment.repository_identity,
      targetRef: ref,
      integratedCommit
    });
    hooks?.beforeAtomicIntegrationState?.();
    const updated = db.prepare(`
      UPDATE assignments
      SET lifecycle_status = 'integrated', integrated_commit = ?, current_head = ?, disposition = ?, updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'ready_for_integration'
    `).run(integratedCommit, integratedCommit, nextDisposition, assignment.workspace_guid);
    if (updated.changes !== 1) throw new Error("Finalization assignment state changed before durable integration record");
  })();
  return getAssignment(db, assignment.workspace_guid);
}
function recycleFinalized(db, repositoryPath, assignment) {
  const current = getAssignment(db, assignment.workspace_guid);
  if (!current || current.lifecycle_status !== "integrated" || !current.integrated_commit) {
    throw new Error("Recycle requires a durable integrated assignment; preserving worktree");
  }
  if (decodePushDisposition(current.disposition)) {
    throw new Error("Refusing to discard a push-pending obligation; resolve or push it first");
  }
  const integratedCommit = current.integrated_commit;
  if (!isAncestor(repositoryPath, integratedCommit, targetRef(current))) {
    throw new Error("Recycle integration proof is unreachable from the integration target; preserving worktree");
  }
  db.transaction(() => {
    db.prepare("DELETE FROM integration_records WHERE workspace_guid = ?").run(current.workspace_guid);
    db.prepare(`
      UPDATE assignments
      SET base_commit = ?, current_head = ?, integrated_commit = NULL, disposition = NULL, updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'integrated'
    `).run(integratedCommit, integratedCommit, current.workspace_guid);
    transitionAssignment(db, current.workspace_guid, "integrated", "active");
  })();
  try {
    runGit(current.worktree_path, ["update-ref", "-d", candidateRef(current.workspace_guid)]);
  } catch {
  }
}
function releaseFinalized(db, repositoryPath, assignment) {
  const current = getAssignment(db, assignment.workspace_guid);
  if (!current || current.lifecycle_status !== "integrated" || !current.integrated_commit) {
    throw new Error("Release requires a durable integrated assignment; preserving worktree");
  }
  if (decodePushDisposition(current.disposition)) {
    throw new Error("Refusing to discard a push-pending obligation; resolve or push it first");
  }
  if (!worktreeIsClean(current.worktree_path)) {
    throw new Error("Release requires a clean worktree; preserving worktree");
  }
  const ref = targetRef(current);
  const actualHead = worktreeHead(current.worktree_path);
  const integration = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(current.workspace_guid, current.repository_identity);
  if (!integration || integration.target_ref !== ref || integration.integrated_commit !== current.integrated_commit || actualHead !== current.integrated_commit || !isAncestor(repositoryPath, current.integrated_commit, ref)) {
    throw new Error("Release integration proof is unreachable from the integration target; preserving worktree");
  }
  removeWorktree(repositoryPath, current.worktree_path);
  deleteTemporaryBranch(repositoryPath, current.branch);
  transitionAssignment(db, current.workspace_guid, "integrated", "cleaned");
}
function disposeFinalized(db, repositoryPath, assignment, dispose) {
  if (dispose === "release") {
    releaseFinalized(db, repositoryPath, assignment);
  } else {
    recycleFinalized(db, repositoryPath, assignment);
  }
}
function finishLocalIntegration(db, local) {
  if (decodePushDisposition(local.assignment.disposition)) {
    return {
      state: "integrated-local",
      integratedCommit: local.integratedCommit,
      pushError: "Remote has not proved the exact integrated candidate"
    };
  }
  recycleFinalized(db, local.repositoryPath, local.assignment);
  return { state: "cleaned", integratedCommit: local.integratedCommit };
}
function repairPrimaryCheckoutAfterInterruptedCas(repositoryPath, ref, expectedTarget, candidate) {
  const currentTarget = runGit(repositoryPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  if (currentTarget !== candidate) {
    throw new Error("Crash reconciliation primary checkout has unproved changes; preserving worktree");
  }
  if (primaryOnRef(repositoryPath, ref)) {
    assertNoPostCasOverlap(repositoryPath, expectedTarget, candidate);
    carryForwardFastForward(repositoryPath, expectedTarget, candidate);
  }
  verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, candidate);
}
function pathLines(output) {
  return output.split("\n").filter((entry) => entry.length > 0);
}
function postCasLocalPaths(repositoryPath, expectedTarget) {
  return [.../* @__PURE__ */ new Set([
    ...pathLines(runGit(repositoryPath, ["diff", "--name-only", "--cached", expectedTarget, "--"])),
    ...pathLines(runGit(repositoryPath, ["diff", "--name-only"])),
    ...pathLines(runGit(repositoryPath, ["ls-files", "--others", "--exclude-standard"]))
  ])].sort();
}
function assertNoPostCasOverlap(repositoryPath, expectedTarget, candidate) {
  const changed = new Set(changedPaths(repositoryPath, expectedTarget, candidate));
  const overlap = postCasLocalPaths(repositoryPath, expectedTarget).filter((entry) => changed.has(entry));
  if (overlap.length > 0) {
    throw new Error(
      `Crash reconciliation primary checkout has local changes overlapping the carried-forward integration; preserving worktree. Overlapping paths: ${overlap.join(", ")}`
    );
  }
}
function continueFrozenFinalization(db, repositoryPath, assignment, sourcePath, frozenCommit, hooks) {
  const ready = getAssignment(db, assignment.workspace_guid);
  if (!ready || ready.lifecycle_status !== "ready_for_integration") {
    throw new Error("Finalization requires durable ready state");
  }
  const ref = targetRef(ready);
  const expectedTarget = runGit(repositoryPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
  acquireIntegrationLock(db, {
    repositoryIdentity: ready.repository_identity,
    workspaceGuid: ready.workspace_guid,
    targetRef: ref,
    expectedTarget
  });
  let rebaseStarted = false;
  let rebaseFinished = false;
  let targetAdvanced = false;
  let integrationRecorded = false;
  try {
    const mergeBase = runGit(sourcePath, ["merge-base", frozenCommit, expectedTarget]).trim();
    if (mergeBase !== ready.base_commit) {
      throw new Error("Finalization base_commit is not the merge-base; run sync_worktree_to_target");
    }
    const reviewedEffect = cumulativeBinaryEffect(sourcePath, ready.base_commit, frozenCommit);
    rebaseStarted = true;
    runGit(sourcePath, ["rebase", "--onto", ref, ready.base_commit]);
    rebaseFinished = true;
    hooks?.beforeDescendantProof?.();
    const integratedCommit = worktreeHead(sourcePath);
    if (!isAncestor(repositoryPath, expectedTarget, integratedCommit)) {
      throw new Error("Finalization descendant proof failed; preserving worktree");
    }
    if (cumulativeBinaryEffect(sourcePath, expectedTarget, integratedCommit) !== reviewedEffect) {
      throw new Error("Finalization rebased cumulative effect differs from reviewed content; preserving worktree");
    }
    runGit(sourcePath, ["update-ref", candidateRef(ready.workspace_guid), integratedCommit]);
    hooks?.beforeCheckedFastForward?.();
    if (runGit(repositoryPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim() !== expectedTarget) {
      throw new Error("Finalization target moved; preserving worktree");
    }
    verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
    assertNoPrimaryOverlap(repositoryPath, ref, expectedTarget, integratedCommit);
    requireExactIntegrationLock(db, ready, ref, expectedTarget);
    hooks?.beforeTargetCompareAndSwap?.();
    runGit(repositoryPath, ["update-ref", ref, integratedCommit, expectedTarget]);
    targetAdvanced = true;
    hooks?.afterTargetCompareAndSwapBeforeCheckout?.();
    if (primaryOnRef(repositoryPath, ref)) {
      carryForwardFastForward(repositoryPath, expectedTarget, integratedCommit);
    }
    verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, integratedCommit);
    hooks?.afterCheckedFastForwardBeforeRecord?.();
    verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, integratedCommit);
    requireExactIntegrationLock(db, ready, ref, expectedTarget);
    const integrated = markIntegrated(db, ready, integratedCommit, ref, hooks);
    integrationRecorded = true;
    return { assignment: integrated, repositoryPath, frozenCommit, candidateCommit: integratedCommit, integratedCommit };
  } catch (error) {
    if (rebaseStarted && !rebaseFinished) {
    } else if (!targetAdvanced) {
      try {
        runGit(sourcePath, ["update-ref", "-d", candidateRef(ready.workspace_guid)]);
      } catch {
      }
      try {
        runGit(sourcePath, ["reset", "--hard", frozenCommit]);
      } catch {
      }
    }
    throw error;
  } finally {
    if (!targetAdvanced || integrationRecorded) {
      releaseExactIntegrationLockIfHeld(db, ready, ref, expectedTarget);
    }
  }
}
function finalizeLocalCommit(db, repositoryPath, assignment, sourcePath, frozenCommit, hooks) {
  if (assignment.lifecycle_status !== "active") {
    throw new Error("Only an active assignment can begin finalization");
  }
  const ref = targetRef(assignment);
  const expectedTarget = runGit(repositoryPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
  if (worktreeHead(sourcePath) !== frozenCommit) {
    throw new Error("Reviewed commit changed before freeze; preserving worktree");
  }
  runGit(sourcePath, ["update-ref", freezeRef(assignment.workspace_guid), frozenCommit]);
  if (worktreeHead(sourcePath) !== frozenCommit) {
    throw new Error("Reviewed commit changed before freeze; preserving worktree");
  }
  const dirty = runGit(sourcePath, ["status", "--porcelain=v1", "--untracked-files=all"]).trim();
  if (dirty !== "") {
    throw new Error("Managed worktree has uncommitted changes; stage or revert them before commit:\n" + dirty);
  }
  transitionAssignment(db, assignment.workspace_guid, "active", "ready_for_integration");
  return continueFrozenFinalization(db, repositoryPath, assignment, sourcePath, frozenCommit, hooks);
}
function finalizeAttestedCandidate(db, repositoryPath, assignment, candidate) {
  const ref = targetRef(assignment);
  const expectedTarget = runGit(repositoryPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
  acquireIntegrationLock(db, {
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    targetRef: ref,
    expectedTarget
  });
  let targetAdvanced = false;
  let integrationRecorded = false;
  try {
    if (worktreeHead(assignment.worktree_path) !== candidate || !isAncestor(repositoryPath, expectedTarget, candidate)) {
      throw new Error("Fresh repair authority is not an exact descendant candidate; preserving worktree");
    }
    verifyPrimaryTarget(repositoryPath, ref, expectedTarget);
    assertNoPrimaryOverlap(repositoryPath, ref, expectedTarget, candidate);
    requireExactIntegrationLock(db, assignment, ref, expectedTarget);
    runGit(repositoryPath, ["update-ref", ref, candidate, expectedTarget]);
    targetAdvanced = true;
    if (primaryOnRef(repositoryPath, ref)) {
      carryForwardFastForward(repositoryPath, expectedTarget, candidate);
    }
    verifyPrimaryAfterFastForward(repositoryPath, ref, expectedTarget, candidate);
    requireExactIntegrationLock(db, assignment, ref, expectedTarget);
    const integrated = markIntegrated(db, assignment, candidate, ref);
    integrationRecorded = true;
    const frozen = runGit(repositoryPath, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
    return { assignment: integrated, repositoryPath, frozenCommit: frozen, candidateCommit: candidate, integratedCommit: candidate };
  } finally {
    if (!targetAdvanced || integrationRecorded) {
      releaseExactIntegrationLockIfHeld(db, assignment, ref, expectedTarget);
    }
  }
}
function finalizeCommanderLocalCommit(db, input, hooks) {
  requireCommanderFinalizationInput(input);
  requireMessage(input.message);
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const isRepair = exact.assignment.lifecycle_status === "ready_for_integration";
  if (exact.assignment.lifecycle_status !== "active" && !isRepair) throw new Error("Only active or ready repair assignment can begin finalization");
  if (isRepair) {
    try {
      runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${freezeRef(exact.assignment.workspace_guid)}^{commit}`]);
    } catch {
      throw new Error("Ready repair lacks durable frozen finalization state");
    }
  }
  const commanderTarget = targetRef(exact.assignment);
  verifyPrimaryTarget(
    exact.primaryCheckoutPath,
    commanderTarget,
    runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${commanderTarget}^{commit}`]).trim()
  );
  const reviewedEvidence = {
    canonicalBranch: input.canonicalBranch,
    localRef: input.localRef,
    stagedTree: input.stagedTree,
    parentOid: input.parentOid
  };
  requireAssignmentCommitBinding(reviewedEvidence, exact.assignment);
  const committed = createExactCommit(exact.assignment.worktree_path, reviewedEvidence, input.message);
  hooks?.afterExactCommitBeforeFreeze?.();
  if (isRepair) {
    const candidate = committed;
    runGit(exact.assignment.worktree_path, ["update-ref", candidateRef(exact.assignment.workspace_guid), candidate]);
    const local2 = finalizeAttestedCandidate(db, exact.primaryCheckoutPath, exact.assignment, candidate);
    if (decodePushDisposition(local2.assignment.disposition)) {
      return { state: "integrated-local", integratedCommit: candidate, pushError: "Remote has not proved the exact integrated candidate" };
    }
    disposeFinalized(db, local2.repositoryPath, local2.assignment, input.dispose);
    return { state: "cleaned", integratedCommit: candidate };
  }
  const local = finalizeLocalCommit(
    db,
    exact.primaryCheckoutPath,
    exact.assignment,
    exact.assignment.worktree_path,
    committed,
    hooks
  );
  if (decodePushDisposition(local.assignment.disposition)) {
    return { state: "integrated-local", integratedCommit: local.integratedCommit, pushError: "Remote has not proved the exact integrated candidate" };
  }
  disposeFinalized(db, local.repositoryPath, local.assignment, input.dispose);
  return { state: "cleaned", integratedCommit: local.integratedCommit };
}
function recoverRebaseInProgress(db, exact, mode) {
  const assignment = exact.assignment;
  const worktree = assignment.worktree_path;
  const primary = exact.primaryCheckoutPath;
  const ref = targetRef(assignment);
  const frozen = runGit(primary, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
  if (mode === "abort") {
    runGit(worktree, ["rebase", "--abort"]);
    if (worktreeHead(worktree) !== frozen) {
      throw new Error("Rebase recovery abort did not restore the frozen pre-rebase commit; preserving worktree");
    }
    return { state: "rebase-aborted", detail: "Rebase aborted; frozen pre-rebase commit restored, integration target unchanged." };
  }
  const unresolved = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
  if (unresolved !== "") {
    return {
      state: "rebase-paused-conflict",
      conflicts: classifyRebaseConflicts(worktree),
      detail: "Close-out/reconcile paused: unresolved conflicts remain; automated resolution pending (M7c). Worktree preserved; nothing integrated. Not an operator task."
    };
  }
  try {
    runGit(worktree, ["-c", "core.editor=true", "rebase", "--continue"]);
  } catch (error) {
    const reconflict = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
    if (reconflict !== "") {
      return {
        state: "rebase-paused-conflict",
        conflicts: classifyRebaseConflicts(worktree),
        detail: "Close-out/reconcile paused: continuing re-conflicted; automated resolution pending (M7c). Worktree preserved; nothing integrated. Not an operator task."
      };
    }
    throw error;
  }
  const stillRebaseDir = runGit(worktree, ["rev-parse", "--git-path", "rebase-merge"]).trim();
  if (existsSync2(path3.resolve(worktree, stillRebaseDir))) {
    const reconflict = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
    throw new Error(`Rebase recovery stopped: rebase still in progress after continue; preserving worktree.${reconflict ? ` Unmerged paths: ${reconflict.split("\n").join(", ")}` : ""}`);
  }
  const head = worktreeHead(worktree);
  const expectedTarget = runGit(primary, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
  const reviewedEffect = cumulativeBinaryEffect(worktree, assignment.base_commit, frozen);
  if (!isAncestor(primary, expectedTarget, head) || cumulativeBinaryEffect(worktree, expectedTarget, head) !== reviewedEffect) {
    return {
      state: "rebase-recovery-repair-required",
      detail: "Rebase resolution changed the reviewed content; the equality proof rejected it. Integrate with a fresh commit (isRepair) authority; worktree preserved and integration target unchanged."
    };
  }
  runGit(worktree, ["update-ref", candidateRef(assignment.workspace_guid), head]);
  const local = finalizeAttestedCandidate(db, primary, assignment, head);
  return finishLocalIntegration(db, local);
}
function classifyRebaseConflicts(worktree) {
  const unmerged = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
  if (unmerged === "") return [];
  return unmerged.split("\n").map((path6) => {
    const xy = runGit(worktree, ["status", "--porcelain=v1", "--", path6]).slice(0, 2);
    let binary = false;
    try {
      binary = /^-\t-/.test(runGit(worktree, ["diff", "--numstat", `:2:${path6}`, `:3:${path6}`]).trim());
    } catch {
      binary = false;
    }
    const conflictClass = binary ? "binary" : xy === "UU" ? "overlap" : xy === "AA" ? "add-add" : xy === "UD" || xy === "DU" ? "delete-modify" : "other";
    const stageLines = (stage) => {
      try {
        return runGit(worktree, ["show", `:${stage}:${path6}`]).split("\n").length;
      } catch {
        return 0;
      }
    };
    const ours = stageLines(2);
    const theirs = stageLines(3);
    const summary = `${path6}: your reviewed work has ${theirs} line(s) here; the integration target has ${ours} line(s) (${conflictClass}).`;
    return { path: path6, conflictClass, summary };
  });
}
function classifyRebaseState(worktree) {
  const rebaseDir = runGit(worktree, ["rev-parse", "--git-path", "rebase-merge"]).trim();
  if (!existsSync2(path3.resolve(worktree, rebaseDir))) return "frozen-no-rebase";
  const unmerged = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
  return unmerged !== "" ? "rebase-paused-conflict" : "rebase-paused-clean";
}
function rerebaseFromFrozen(worktree, assignment, frozen) {
  if (worktreeHead(worktree) !== frozen) {
    throw new Error("Rerebase requires the worktree at the frozen pre-rebase commit; preserving worktree");
  }
  if (!worktreeIsClean(worktree)) {
    throw new Error("Rerebase requires a clean worktree; preserving worktree");
  }
  const ref = targetRef(assignment);
  try {
    runGit(worktree, ["rebase", "--onto", ref, assignment.base_commit]);
  } catch {
    const unmerged = runGit(worktree, ["diff", "--name-only", "--diff-filter=U"]).trim();
    throw new Error(`Rerebase conflicted; a paused rebase is preserved for continue/abort.${unmerged ? ` Unmerged paths: ${unmerged.split("\n").join(", ")}` : ""}`);
  }
  return { state: "rebase-rerebased-ready-for-repair", detail: worktreeHead(worktree) };
}
function restoreFrozen(worktree, frozen) {
  if (!worktreeIsClean(worktree)) {
    throw new Error("Restore frozen requires a clean worktree; preserving worktree");
  }
  runGit(worktree, ["reset", "--hard", frozen]);
  if (worktreeHead(worktree) !== frozen) {
    throw new Error("Restore frozen did not restore the frozen pre-rebase commit; preserving worktree");
  }
  return { state: "rebase-frozen-restored", detail: "Worktree reset to the frozen pre-rebase commit; integration target unchanged." };
}
function recoverNoPausedRebase(exact, mode) {
  const assignment = exact.assignment;
  const worktree = assignment.worktree_path;
  const state = classifyRebaseState(worktree);
  if (state !== "frozen-no-rebase") {
    throw new Error(`Rebase ${mode} requires no paused rebase; a rebase is still in progress \u2014 resolve via continue/abort first; preserving worktree`);
  }
  const frozen = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
  return mode === "rerebase" ? rerebaseFromFrozen(worktree, assignment, frozen) : restoreFrozen(worktree, frozen);
}
function reconcileFinalization(db, input) {
  const exact = exactAssignment(db, input.repositoryPath, input.workspaceGuid, input.providerRootSessionId);
  const assignment = exact.assignment;
  if (input.rebaseRecovery === "status") {
    if (assignment.lifecycle_status === "integrated") {
      return { state: "integrated" };
    }
    if (assignment.lifecycle_status === "ready_for_integration") {
      const state = classifyRebaseState(assignment.worktree_path);
      return { state, detail: `Managed finalization worktree state: ${state}.` };
    }
    return { state: "not-ready" };
  }
  if (assignment.lifecycle_status === "integrated") {
    const candidate2 = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
    if (assignment.integrated_commit !== candidate2) {
      throw new Error("Integrated assignment candidate proof differs; preserving worktree");
    }
    const disposition = decodePushDisposition(assignment.disposition);
    if (assignment.disposition && !disposition) {
      throw new Error("Integrated assignment push disposition is malformed; preserving worktree");
    }
    if (disposition) {
      if (disposition.candidateCommit !== candidate2 || runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim() !== disposition.frozenCommit) {
        throw new Error("Integrated assignment push refs differ; preserving worktree");
      }
      const sourceHead = worktreeHead(assignment.worktree_path);
      if (sourceHead !== candidate2) {
        if (sourceHead !== disposition.frozenCommit || !worktreeIsClean(assignment.worktree_path)) {
          throw new Error("Integrated assignment source HEAD differs from candidate; preserving worktree");
        }
        runGit(assignment.worktree_path, ["reset", "--hard", candidate2]);
      }
      const remote = remoteRefOid(assignment.worktree_path, disposition.remoteUrl, disposition.destinationRef);
      if (remote !== candidate2) {
        return {
          state: "integrated-local",
          integratedCommit: candidate2,
          pushError: remote === disposition.expectedRemoteOldOid || remote === null ? "Remote has not proved the exact integrated candidate" : "Remote outcome is ambiguous; preserving push-pending state"
        };
      }
      setDisposition(db, assignment.workspace_guid, null);
    } else if (worktreeHead(assignment.worktree_path) !== candidate2) {
      throw new Error("Integrated assignment source HEAD differs from candidate; preserving worktree");
    }
    recycleFinalized(db, exact.primaryCheckoutPath, assignment);
    return { state: "cleaned", integratedCommit: assignment.integrated_commit ?? void 0 };
  }
  if (assignment.lifecycle_status !== "ready_for_integration") {
    throw new Error("No ready finalization is available for reconciliation");
  }
  if (input.rebaseRecovery === "rerebase" || input.rebaseRecovery === "restore_frozen") {
    return recoverNoPausedRebase(exact, input.rebaseRecovery);
  }
  if (input.rebaseRecovery) {
    const rebaseInProgressDir = runGit(assignment.worktree_path, ["rev-parse", "--git-path", "rebase-merge"]).trim();
    if (!existsSync2(path3.resolve(assignment.worktree_path, rebaseInProgressDir))) {
      throw new Error("Rebase recovery requested but no rebase is in progress; preserving worktree");
    }
  }
  let record = db.prepare(`
    SELECT target_ref, integrated_commit FROM integration_records
    WHERE workspace_guid = ? AND repository_identity = ?
  `).get(assignment.workspace_guid, assignment.repository_identity);
  if (record) {
    const staleCheckRef = targetRef(assignment);
    const recordIsAncestorOfTarget = isAncestor(exact.primaryCheckoutPath, record.integrated_commit, staleCheckRef);
    let staleCheckCandidate;
    try {
      staleCheckCandidate = runGit(
        exact.primaryCheckoutPath,
        ["rev-parse", "--verify", `${candidateRef(assignment.workspace_guid)}^{commit}`]
      ).trim();
    } catch {
    }
    const recordIsProvenStale = recordIsAncestorOfTarget && staleCheckCandidate !== void 0 && staleCheckCandidate !== record.integrated_commit;
    if (recordIsProvenStale) {
      deleteIntegrationRecord(db, assignment.workspace_guid);
      record = void 0;
      const staleCheckSourceHead = worktreeHead(assignment.worktree_path);
      const staleCheckCurrentTarget = runGit(
        exact.primaryCheckoutPath,
        ["rev-parse", "--verify", `${staleCheckRef}^{commit}`]
      ).trim();
      if (staleCheckCandidate !== staleCheckCurrentTarget && staleCheckCandidate !== staleCheckSourceHead) {
        try {
          runGit(exact.primaryCheckoutPath, ["update-ref", "-d", candidateRef(assignment.workspace_guid)]);
        } catch {
        }
      }
    }
  }
  if (!record) {
    const sourceHead = worktreeHead(assignment.worktree_path);
    const ref2 = targetRef(assignment);
    let candidate2;
    try {
      candidate2 = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
    } catch {
    }
    if (candidate2 && sourceHead !== candidate2) {
      throw new Error("Crash reconciliation candidate and source HEAD differ; preserving worktree");
    }
    const rebaseDirectory = runGit(assignment.worktree_path, ["rev-parse", "--git-path", "rebase-merge"]).trim();
    if (existsSync2(path3.resolve(assignment.worktree_path, rebaseDirectory))) {
      if (input.rebaseRecovery) {
        return recoverRebaseInProgress(db, exact, input.rebaseRecovery === "abort" ? "abort" : "continue");
      }
      throw new Error("Crash reconciliation requires reviewed rebase-conflict repair before retry");
    }
    if (candidate2) {
      const currentTarget = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${ref2}^{commit}`]).trim();
      if (currentTarget !== candidate2) {
        throw new Error("Crash reconciliation target is not the exact candidate; preserving worktree");
      }
      const expectedTarget = recoveryIntegrationLockExpectedTarget(db, assignment, ref2);
      let integrationRecorded = false;
      try {
        requireExactIntegrationLock(db, assignment, ref2, expectedTarget);
        try {
          verifyPrimaryAfterFastForward(exact.primaryCheckoutPath, ref2, expectedTarget, candidate2);
        } catch {
          repairPrimaryCheckoutAfterInterruptedCas(exact.primaryCheckoutPath, ref2, expectedTarget, candidate2);
        }
        requireExactIntegrationLock(db, assignment, ref2, expectedTarget);
        const frozen2 = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
        if (cumulativeBinaryEffect(assignment.worktree_path, assignment.base_commit, frozen2) !== cumulativeBinaryEffect(assignment.worktree_path, expectedTarget, candidate2)) {
          throw new Error("Crash reconciliation candidate effect differs from frozen review; preserving worktree");
        }
        const integrated2 = markIntegrated(db, assignment, candidate2, ref2);
        integrationRecorded = true;
        if (decodePushDisposition(integrated2.disposition)) {
          return {
            state: "integrated-local",
            integratedCommit: candidate2,
            pushError: "Remote has not proved the exact integrated candidate"
          };
        }
        recycleFinalized(db, exact.primaryCheckoutPath, integrated2);
        return { state: "cleaned", integratedCommit: candidate2 };
      } finally {
        if (integrationRecorded) {
          releaseExactIntegrationLockIfHeld(db, assignment, ref2, expectedTarget);
        }
      }
    }
    const frozen = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
    if (!candidate2 && sourceHead !== frozen) {
      throw new Error("Crash reconciliation requires fresh trusted human repair authority");
    }
    if (!worktreeIsClean(assignment.worktree_path)) {
      throw new Error(
        "Crash reconciliation refused: managed worktree has uncommitted changes, preserving worktree"
      );
    }
    runGit(assignment.worktree_path, ["reset", "--hard", frozen]);
    const local = continueFrozenFinalization(
      db,
      exact.primaryCheckoutPath,
      assignment,
      assignment.worktree_path,
      frozen
    );
    return finishLocalIntegration(db, local);
  }
  const durableRecord = record;
  const ref = targetRef(assignment);
  if (durableRecord.target_ref !== ref || runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${ref}^{commit}`]).trim() !== durableRecord.integrated_commit) {
    throw new Error("Crash reconciliation lacks reachable integration proof; preserving worktree");
  }
  const candidate = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
  if (candidate !== durableRecord.integrated_commit || worktreeHead(assignment.worktree_path) !== candidate) {
    throw new Error("Crash reconciliation candidate/source proof differs; preserving worktree");
  }
  const integrationPending = decodeIntegrationPendingDisposition(assignment.disposition);
  const pushPending = decodePushDisposition(assignment.disposition);
  if (assignment.disposition && !integrationPending && !pushPending) {
    throw new Error("Crash reconciliation disposition is malformed; preserving worktree");
  }
  if (integrationPending || pushPending) {
    const frozen = runGit(exact.primaryCheckoutPath, ["rev-parse", "--verify", `${freezeRef(assignment.workspace_guid)}^{commit}`]).trim();
    if (integrationPending && integrationPending.frozenCommit !== frozen || pushPending && (pushPending.frozenCommit !== frozen || pushPending.candidateCommit !== candidate)) {
      throw new Error("Crash reconciliation pending push proof differs; preserving worktree");
    }
  }
  const nextDisposition = integrationPending ? encodePushDisposition({ ...integrationPending, phase: "push-pending", candidateCommit: candidate }) : pushPending ? encodePushDisposition(pushPending) : null;
  db.transaction(() => {
    const result = db.prepare(`
      UPDATE assignments SET integrated_commit = ?, current_head = ?, disposition = ?, updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'ready_for_integration'
    `).run(durableRecord.integrated_commit, durableRecord.integrated_commit, nextDisposition, assignment.workspace_guid);
    if (result.changes !== 1) throw new Error("Crash reconciliation assignment state changed concurrently");
    transitionAssignment(db, assignment.workspace_guid, "ready_for_integration", "integrated");
  })();
  const integrated = getAssignment(db, assignment.workspace_guid);
  if (nextDisposition) {
    return {
      state: "integrated-local",
      integratedCommit: durableRecord.integrated_commit,
      pushError: "Remote has not proved the exact integrated candidate"
    };
  }
  recycleFinalized(db, exact.primaryCheckoutPath, integrated);
  return { state: "cleaned", integratedCommit: durableRecord.integrated_commit };
}

// src/workspace-service.ts
import { randomUUID as randomUUID2 } from "node:crypto";
import { existsSync as existsSync3 } from "node:fs";
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
  /**
   * A repository-only match is not ownership: two different sessions on the
   * same repository must never be conflated. Ownership requires the exact
   * three-part binding (repository, workspace GUID, and owner session) that
   * `acquirePrimaryCheckoutOwnership` records.
   */
  sessionOwnsPrimary(repositoryIdentity, workspaceGuid, ownerSessionId) {
    return this.db.prepare(`
      SELECT 1 FROM primary_checkout_owners
      WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ?
    `).get(repositoryIdentity, workspaceGuid, ownerSessionId) !== void 0;
  }
  materializeManagedWorktree(repository, input) {
    const baseCommit = worktreeHead(repository.primaryCheckoutPath);
    const worktreePath = managedWorktreePath(repository.primaryCheckoutPath, input.workspaceGuid);
    const branch = managedBranch(input.workspaceGuid);
    const assignmentInput = {
      workspaceGuid: input.workspaceGuid,
      repositoryIdentity: repository.repositoryIdentity,
      worktreePath,
      branch,
      baseCommit,
      currentHead: baseCommit,
      ownerSessionId: input.ownerSessionId,
      workerId: input.workerId,
      integrationTarget: input.integrationTarget
    };
    const priorRow = getAssignment(this.db, input.workspaceGuid);
    const worktreeGone = priorRow !== void 0 && !existsSync3(priorRow.worktree_path) && !worktreeExists(repository.primaryCheckoutPath, priorRow.worktree_path);
    const reuseSpent = priorRow !== void 0 && priorRow.lifecycle_status === "cleaned" && worktreeGone;
    let assignment;
    if (reuseSpent) {
      try {
        runGit(repository.primaryCheckoutPath, ["update-ref", "-d", `refs/ironclaude/finalization/${input.workspaceGuid}/candidate`]);
      } catch {
      }
      assignment = reuseTerminalAssignment(this.db, assignmentInput);
    } else {
      assignment = createAssignment(this.db, assignmentInput);
    }
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
      linkSharedResources(
        repository.primaryCheckoutPath,
        assignment.worktree_path,
        repository.repositoryIdentity,
        readSharedResourceConfig(repository.repositoryIdentity)
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
    const assignment = assignments[0];
    this.validateManagedIdentity(repository, assignment);
    const primaryOwnedByThisSession = this.sessionOwnsPrimary(
      repository.repositoryIdentity,
      assignment.workspace_guid,
      input.ownerSessionId
    );
    let currentHead;
    try {
      currentHead = worktreeHead(assignment.worktree_path);
    } catch {
      currentHead = assignment.current_head;
    }
    return {
      status: "assigned",
      assignment,
      effectiveRoot: primaryOwnedByThisSession ? "primary" : "managed",
      primaryOwnedByThisSession,
      currentHead
    };
  }
  checkoutIntentEvidence(repository, assignment, ownerSessionId, operation) {
    this.validateManagedIdentity(repository, assignment);
    const primaryOwner = this.db.prepare(`
      SELECT workspace_guid, owner_session_id FROM primary_checkout_owners
      WHERE repository_identity = ?
    `).get(repository.repositoryIdentity);
    if (operation === "use-primary-checkout") {
      reapStalePrimaryOwner(this.db, repository.repositoryIdentity);
      if (this.db.prepare("SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?").get(repository.repositoryIdentity)) {
        throw new Error("Primary checkout is already owned");
      }
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
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status === "abandoned") return assignment;
    if (!nonterminal(assignment.lifecycle_status)) {
      throw new Error("Only unresolved managed worktrees can be abandoned");
    }
    if (this.db.prepare(`
      SELECT 1 FROM primary_checkout_owners
      WHERE repository_identity = ? AND workspace_guid = ?
    `).get(assignment.repository_identity, assignment.workspace_guid)) {
      throw new Error("Return to the managed worktree before abandoning while holding the primary checkout.");
    }
    if (input.mode === "rescue") {
      return this.rescueAbandon(repository, assignment);
    }
    return transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, "abandoned");
  }
  /**
   * Commits any uncommitted worktree content onto the worker's OWN branch
   * (never main), mints a durable `refs/ironclaude/recovery/<guid>` ref at that
   * commit and records the REF NAME as recovery evidence, transitions the
   * assignment to abandoned, then removes ONLY the worktree directory. The
   * durable ref — not the worker branch — is the anchor: a later reaper may
   * delete the branch, and the rescued commit stays reachable through the ref.
   */
  rescueAbandon(repository, assignment) {
    const worktreePresent = existsSync3(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (worktreePresent) {
      if (!worktreeIsClean(assignment.worktree_path)) {
        runGit(assignment.worktree_path, ["add", "-A"]);
        runGit(assignment.worktree_path, ["commit", "-m", "ironclaude: rescue-commit before reclaiming worktree"]);
      }
      const rescuedHead = worktreeHead(assignment.worktree_path);
      const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
      runGit(repository.primaryCheckoutPath, ["update-ref", recoveryRef, rescuedHead]);
      this.db.prepare(`
        UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
      `).run(recoveryRef, assignment.workspace_guid);
    }
    const abandoned = transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, "abandoned");
    if (worktreePresent) removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    return abandoned;
  }
  /**
   * Carve-out for a reserved row that never got as far as owning a real
   * worktree (`addWorktree` never ran or failed before it could complete):
   * there is nothing on disk to remove and no branch to preserve, so the row
   * is deleted outright. Refuses — deferring to `cleanupWorkspace`'s proven
   * proofs — the moment a worktree actually exists for this row.
   */
  cleanupReservedAssignment(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = this.getWorkspaceAssignment(input);
    if (assignment.lifecycle_status !== "reserved") {
      throw new Error("Only a reserved, never-materialized assignment is eligible for this carve-out");
    }
    if (existsSync3(assignment.worktree_path) || worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path)) {
      throw new Error("Reserved assignment has a materialized worktree; use cleanupWorkspace instead");
    }
    const result = this.db.prepare(`
      DELETE FROM assignments WHERE workspace_guid = ? AND lifecycle_status = 'reserved'
    `).run(assignment.workspace_guid);
    if (result.changes !== 1) throw new Error("Reserved assignment changed concurrently");
    return { ...assignment, lifecycle_status: "cleaned" };
  }
  /** True iff `ref` resolves in `root`; a missing or unresolvable ref returns false rather than throwing. */
  refResolves(root, ref) {
    try {
      runGit(root, ["rev-parse", "--verify", "--quiet", ref]);
      return true;
    } catch {
      return false;
    }
  }
  /** True iff `ref` is an actual git ref (not merely a resolvable object such as a raw SHA). */
  refIsDurableRef(root, ref) {
    try {
      runGit(root, ["show-ref", "--verify", "--quiet", ref]);
      return true;
    } catch {
      return false;
    }
  }
  /**
   * Guarantees a durable git ref anchors an abandoned row's recovery commit before its
   * branch can be deleted. A ref-name `recovery_ref` is returned unchanged (no-op). A
   * legacy raw-SHA `recovery_ref` that still resolves to a reachable object is upgraded:
   * mint `refs/ironclaude/recovery/<guid>` at that commit and record the REF NAME. A
   * `recovery_ref` that is neither a durable ref nor a reachable object throws, so the
   * row and its branch are preserved (never-lose-work).
   */
  ensureDurableRecoveryAnchor(repository, assignment) {
    const current = assignment.recovery_ref;
    if (!current) throw new Error("Abandoned assignment lacks recovery evidence; preserving it");
    if (this.refIsDurableRef(repository.primaryCheckoutPath, current)) return current;
    if (!this.refResolves(repository.primaryCheckoutPath, current)) {
      throw new Error("Recovery evidence is neither a durable ref nor a reachable commit; preserving it");
    }
    const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
    runGit(repository.primaryCheckoutPath, ["update-ref", recoveryRef, current]);
    this.db.prepare(`
      UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
    `).run(recoveryRef, assignment.workspace_guid);
    return recoveryRef;
  }
  /**
   * Deletes a terminal (integrated or abandoned) assignment only when its
   * recorded recovery/integration proof still holds, then removes the worktree
   * (when present) and its private branch. Every failed proof preserves work.
   *
   * Handles both the present-worktree case (proof anchored on the live worktree
   * HEAD, byte-identical to the original cleanup path) and the worktree-gone
   * case a reaper reaches after `rescueAbandon` has already removed the
   * directory: there the abandoned proof is that the durable recovery ref still
   * resolves, and branch deletion is skipped when the branch is already gone (a
   * `git branch -D` on a nonexistent branch would otherwise throw).
   */
  tombstoneTerminalAssignment(repository, assignment) {
    const present = existsSync3(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (present && !worktreeIsClean(assignment.worktree_path)) {
      throw new Error("Managed worktree is dirty; preserving it");
    }
    if (assignment.lifecycle_status === "abandoned") {
      if (present) {
        if (!assignment.recovery_ref || !isAncestor(repository.primaryCheckoutPath, worktreeHead(assignment.worktree_path), assignment.recovery_ref)) {
          throw new Error("Abandoned worktree lacks reachable durable recovery evidence; preserving it");
        }
      } else if (!assignment.recovery_ref || !this.refResolves(repository.primaryCheckoutPath, assignment.recovery_ref)) {
        throw new Error("Abandoned worktree lacks reachable durable recovery evidence; preserving it");
      }
    } else {
      const integration = this.db.prepare(`
        SELECT target_ref, integrated_commit FROM integration_records
        WHERE workspace_guid = ? AND repository_identity = ?
      `).get(assignment.workspace_guid, repository.repositoryIdentity);
      if (!assignment.integrated_commit || !integration || integration.target_ref !== integrationTargetRef(assignment.integration_target) || integration.integrated_commit !== assignment.integrated_commit || present && worktreeHead(assignment.worktree_path) !== assignment.integrated_commit || !isAncestor(repository.primaryCheckoutPath, assignment.integrated_commit, integration.target_ref)) {
        throw new Error("Integrated worktree lacks reachable durable integration evidence; preserving it");
      }
    }
    if (assignment.lifecycle_status === "abandoned") {
      this.ensureDurableRecoveryAnchor(repository, assignment);
    }
    if (present) removeWorktree(repository.primaryCheckoutPath, assignment.worktree_path);
    if (this.refResolves(repository.primaryCheckoutPath, `refs/heads/${assignment.branch}`)) {
      deleteTemporaryBranch(repository.primaryCheckoutPath, assignment.branch);
    }
    const carried = pushPendingSummary(assignment.disposition);
    return this.db.transaction(() => {
      const cleaned = transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, "cleaned");
      if (carried) {
        insertPreservedWork(this.db, {
          workspaceGuid: assignment.workspace_guid,
          repositoryIdentity: repository.repositoryIdentity,
          ownerSessionId: assignment.owner_session_id,
          kind: "pending-push",
          payload: JSON.stringify(carried)
        });
      }
      return cleaned;
    })();
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
    const present = existsSync3(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (present) this.validateManagedIdentity(repository, assignment);
    return this.tombstoneTerminalAssignment(repository, assignment);
  }
  /**
   * Owner-agnostic reaper for a LEAKED managed assignment — one whose owning
   * session is gone, so `cleanupWorkspace`'s owner-match can never fire. It
   * still proves canonical managed identity (repository, path, branch) before
   * touching anything, and preserves work at every step: a present worktree is
   * rescued (`rescueAbandon` anchors its content on a durable recovery ref), a
   * worktree-gone row mints a recovery ref at the surviving branch tip (or, when
   * even the branch is gone, at the recorded base commit) BEFORE transitioning
   * to abandoned, and a present worktree on a foreign branch is refused and
   * preserved. Only after work is anchored does it tombstone the row.
   */
  reapLeakedAssignment(input) {
    const repository = discoverRepository(input.repositoryPath);
    const assignment = getAssignment(this.db, input.workspaceGuid);
    if (!assignment || assignment.repository_identity !== repository.repositoryIdentity || assignment.worktree_path !== managedWorktreePath(repository.primaryCheckoutPath, assignment.workspace_guid) || assignment.branch !== managedBranch(assignment.workspace_guid)) {
      throw new Error("Leaked assignment does not match canonical managed identity for this repository");
    }
    if (assignment.lifecycle_status === "cleaned") return assignment;
    if (assignment.lifecycle_status === "reserved") {
      return this.reapReservedAssignment(repository, assignment);
    }
    const present = existsSync3(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
    if (present) this.validateManagedIdentity(repository, assignment);
    if (assignment.lifecycle_status !== "integrated" && assignment.lifecycle_status !== "abandoned") {
      if (present) {
        this.rescueAbandon(repository, assignment);
      } else {
        const recoveryRef = `refs/ironclaude/recovery/${assignment.workspace_guid}`;
        const branchRef = `refs/heads/${assignment.branch}`;
        const anchor = this.refResolves(repository.primaryCheckoutPath, branchRef) ? branchRef : assignment.base_commit;
        runGit(repository.primaryCheckoutPath, ["update-ref", recoveryRef, anchor]);
        this.db.prepare(`
          UPDATE assignments SET recovery_ref = ?, updated_at = datetime('now') WHERE workspace_guid = ?
        `).run(recoveryRef, assignment.workspace_guid);
        transitionAssignment(this.db, assignment.workspace_guid, assignment.lifecycle_status, "abandoned");
      }
    }
    return this.tombstoneTerminalAssignment(repository, getAssignment(this.db, input.workspaceGuid));
  }
  /**
   * Owner-agnostic variant of `cleanupReservedAssignment`'s carve-out: a
   * reserved row that never materialized a real worktree has nothing on disk to
   * remove and no branch to preserve, so the row is deleted outright. Refuses
   * the moment a worktree actually exists — that row is not a bare reservation.
   */
  reapReservedAssignment(repository, assignment) {
    if (existsSync3(assignment.worktree_path) || worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path)) {
      throw new Error("Reserved assignment has a materialized worktree; use cleanupWorkspace instead");
    }
    const result = this.db.prepare(`
      DELETE FROM assignments WHERE workspace_guid = ? AND lifecycle_status = 'reserved'
    `).run(assignment.workspace_guid);
    if (result.changes !== 1) throw new Error("Reserved assignment changed concurrently");
    return { ...assignment, lifecycle_status: "cleaned" };
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

// src/cli.ts
var INTERNAL_COMMAND_NAMES = ["allocate", "bind", "finalize", "abandon", "reconcile", "cleanup", "sync", "reap"];
function waitForCliDatabase(milliseconds) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}
function initCliDb() {
  let lastBusyError;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return initDb();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!/database is locked|SQLITE_BUSY/i.test(message)) throw error;
      lastBusyError = error;
      waitForCliDatabase(25);
    }
  }
  throw lastBusyError instanceof Error ? lastBusyError : new Error("workspace-manager database remained locked during initialization");
}
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
function optionalRebaseRecovery(args) {
  const value = args.rebase_recovery;
  if (value === void 0) return void 0;
  if (value !== "continue" && value !== "abort" && value !== "rerebase" && value !== "restore_frozen" && value !== "status") {
    throw new Error("rebase_recovery must be 'continue', 'abort', 'rerebase', 'restore_frozen', or 'status'");
  }
  return value;
}
function optionalAbandonMode(args) {
  const value = args.mode;
  if (value === void 0) return void 0;
  if (value !== "rescue") throw new Error("mode must be 'rescue'");
  return value;
}
function requiredRecord(args, key) {
  const value = args[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${key} must be an object`);
  return value;
}
function dispatchInternalCommand(name, args, dependencies) {
  switch (name) {
    case "allocate":
      return dependencies.allocate(args);
    case "bind":
      return dependencies.bind(args);
    case "finalize":
      return dependencies.finalize(args);
    case "abandon":
      return dependencies.abandon(args);
    case "reconcile":
      return dependencies.reconcile(args);
    case "cleanup":
      return dependencies.cleanup(args);
    case "sync":
      return dependencies.sync(args);
    case "reap":
      return dependencies.reap(args);
    default:
      throw new Error(`Unknown internal workspace command: ${name}`);
  }
}
function createInternalCommandDependencies(db) {
  const service = new WorkspaceService(db);
  return {
    allocate: (args) => {
      const ownerSessionId = optionalString(args, "owner_session_id");
      if (ownerSessionId !== void 0) {
        return service.ensureSessionWorktree({
          repositoryPath: requiredString(args, "repository_path"),
          workspaceGuid: optionalString(args, "workspace_guid"),
          ownerSessionId,
          workerId: optionalString(args, "worker_id"),
          integrationTarget: optionalString(args, "integration_target")
        });
      }
      return service.reserveWorkerWorktree({
        repositoryPath: requiredString(args, "repository_path"),
        workspaceGuid: requiredString(args, "workspace_guid"),
        workerId: requiredString(args, "worker_id"),
        // Omitted means "the primary checkout's current branch", resolved on the
        // host that owns the repository — which may not be this machine.
        integrationTarget: optionalString(args, "integration_target")
      });
    },
    bind: (args) => {
      const expectedLifecycle = requiredString(args, "expected_lifecycle");
      if (expectedLifecycle !== "active") throw new Error("expected_lifecycle must be active");
      return service.bindWorkerWorktree({
        repositoryPath: requiredString(args, "repository_path"),
        repositoryIdentity: requiredString(args, "repository_identity"),
        workspaceGuid: requiredString(args, "workspace_guid"),
        workerId: requiredString(args, "worker_id"),
        ownerSessionId: requiredString(args, "owner_session_id"),
        expectedLifecycle,
        expectedWorktreePath: requiredString(args, "expected_worktree_path"),
        expectedBranch: requiredString(args, "expected_branch"),
        expectedBaseCommit: requiredString(args, "expected_base_commit"),
        expectedCurrentHead: requiredString(args, "expected_current_head")
      });
    },
    finalize: (args) => {
      const command = requiredRecord(args, "command");
      return finalizeCommanderLocalCommit(db, command);
    },
    abandon: (args) => service.abandonWorkspace({
      repositoryPath: requiredString(args, "repository_path"),
      workspaceGuid: requiredString(args, "workspace_guid"),
      ownerSessionId: requiredString(args, "owner_session_id"),
      mode: optionalAbandonMode(args)
    }),
    cleanup: (args) => {
      const repositoryPath = requiredString(args, "repository_path");
      const workspaceGuid = requiredString(args, "workspace_guid");
      const ownerSessionId = requiredString(args, "owner_session_id");
      const assignment = service.getWorkspaceAssignment({ repositoryPath, workspaceGuid, ownerSessionId });
      if (assignment.lifecycle_status === "reserved") {
        return service.cleanupReservedAssignment({ repositoryPath, workspaceGuid, ownerSessionId });
      }
      return service.cleanupWorkspace({ repositoryPath, workspaceGuid, ownerSessionId });
    },
    reconcile: (args) => {
      const repositoryPath = requiredString(args, "repository_path");
      const workspaceGuid = optionalString(args, "workspace_guid");
      const ownerSessionId = optionalString(args, "owner_session_id");
      const rebaseRecovery = optionalRebaseRecovery(args);
      if (workspaceGuid || ownerSessionId) {
        if (!workspaceGuid || !ownerSessionId) {
          throw new Error("workspace_guid and owner_session_id must be provided together for finalization reconciliation");
        }
        return reconcileFinalization(db, {
          repositoryPath,
          workspaceGuid,
          providerRootSessionId: ownerSessionId,
          rebaseRecovery
        });
      }
      if (rebaseRecovery) {
        throw new Error("rebase_recovery requires workspace_guid and owner_session_id for finalization reconciliation");
      }
      return service.reconcileRepository(repositoryPath);
    },
    sync: (args) => syncWorktreeToTarget(db, {
      repositoryPath: requiredString(args, "repository_path"),
      workspaceGuid: requiredString(args, "workspace_guid"),
      providerRootSessionId: requiredString(args, "owner_session_id")
    }),
    reap: (args) => service.reapLeakedAssignment({
      repositoryPath: requiredString(args, "repository_path"),
      workspaceGuid: requiredString(args, "workspace_guid")
    })
  };
}
function runCli(argv = process.argv.slice(2), db = initDb()) {
  const command = argv[0];
  if (!command) throw new Error(`Expected one internal command: ${INTERNAL_COMMAND_NAMES.join(", ")}`);
  const raw = argv[1] ?? "{}";
  let args;
  try {
    args = JSON.parse(raw);
  } catch {
    throw new Error("Internal command payload must be valid JSON");
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    throw new Error("Internal command payload must be a JSON object");
  }
  return dispatchInternalCommand(command, args, createInternalCommandDependencies(db));
}
var invokedPath = process.argv[1] ? path5.resolve(process.argv[1]) : null;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    process.stdout.write(`${JSON.stringify(runCli(process.argv.slice(2), initCliDb()))}
`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}
`);
    process.exit(1);
  }
}
export {
  INTERNAL_COMMAND_NAMES,
  createInternalCommandDependencies,
  dispatchInternalCommand,
  initCliDb,
  runCli
};
