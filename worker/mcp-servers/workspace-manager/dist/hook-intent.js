#!/usr/bin/env node

// src/hook-intent.ts
import path7 from "node:path";
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

// src/git-authority.ts
import path5 from "node:path";

// src/git.ts
import { spawnSync } from "node:child_process";
import { existsSync, lstatSync, mkdirSync, opendirSync, readFileSync, realpathSync, statSync, symlinkSync, writeFileSync } from "node:fs";
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
function runGitEnv(cwd, args, env) {
  const result = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8", env });
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
function addSharedResourceEntries(repositoryIdentity, entries, primaryCheckoutPath, allowSecretEntries = false) {
  const configPath = path2.join(repositoryIdentity, "info", SHARED_RESOURCE_CONFIG);
  const present = new Set(readSharedResourceConfig(repositoryIdentity));
  const added = [];
  const skipped = [];
  const rejected = [];
  const secretBlocked = [];
  const secretHits = {};
  const scanTruncated = [];
  for (const entry of entries) {
    if (!isSafeSharedEntry(entry)) {
      rejected.push(entry);
      continue;
    }
    if (!allowSecretEntries && isSecretEntry(entry)) {
      secretBlocked.push(entry);
      continue;
    }
    if (!SCAN_VENDOR_SKIP.has(entry.split("/")[0])) {
      const absDir = path2.join(primaryCheckoutPath, entry);
      let isDir = false;
      try {
        isDir = statSync(absDir).isDirectory();
      } catch {
        isDir = false;
      }
      if (isDir) {
        const { hits, truncated } = directoryContainsSecret(absDir, entry);
        if (truncated) scanTruncated.push(entry);
        if (hits.length > 0 && !allowSecretEntries) {
          secretBlocked.push(entry);
          secretHits[entry] = hits;
          continue;
        }
      }
    }
    if (present.has(entry)) {
      skipped.push(entry);
      continue;
    }
    present.add(entry);
    added.push(entry);
  }
  if (added.length > 0) {
    mkdirSync(path2.dirname(configPath), { recursive: true });
    const existing = existsSync(configPath) ? readFileSync(configPath, "utf8") : "";
    const separator = existing.length === 0 || existing.endsWith("\n") ? "" : "\n";
    writeFileSync(configPath, existing + separator + added.map((entry) => `${entry}
`).join(""));
  }
  return { added, skipped, rejected, secretBlocked, entries: [...present], secretHits, scanTruncated };
}
function isSecretEntry(entry) {
  const lower = entry.split("/").map((s) => s.toLowerCase());
  const SECRET_DIRS = /* @__PURE__ */ new Set([".ssh", ".aws", ".gnupg"]);
  if (lower.some((s) => SECRET_DIRS.has(s))) return true;
  const base = lower[lower.length - 1];
  if (base.endsWith(".example")) return false;
  const SECRET_FILES = /* @__PURE__ */ new Set([
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials"
  ]);
  if (SECRET_FILES.has(base)) return true;
  if (base.startsWith(".env.")) return true;
  if (/\.(pem|key|p12|pfx)$/.test(base)) return true;
  return false;
}
var SCAN_MAX_DEPTH = 6;
var SCAN_MAX_ENTRIES = 2e4;
var SCAN_MAX_HITS = 5;
var SCAN_VENDOR_SKIP = /* @__PURE__ */ new Set(["node_modules", ".venv", "venv", "site-packages", ".git"]);
function directoryContainsSecret(absEntryDir, entryRel, limits = {}) {
  const maxDepth = limits.maxDepth ?? SCAN_MAX_DEPTH;
  const maxEntries = limits.maxEntries ?? SCAN_MAX_ENTRIES;
  const hits = [];
  let examined = 0;
  let truncated = false;
  const queue = [{ abs: absEntryDir, rel: entryRel, depth: 0 }];
  while (queue.length > 0) {
    const { abs, rel, depth } = queue.shift();
    let dir;
    try {
      dir = opendirSync(abs);
      for (let d = dir.readSync(); d !== null; d = dir.readSync()) {
        if (examined >= maxEntries) {
          truncated = true;
          return { hits, truncated };
        }
        examined++;
        const childRel = `${rel}/${d.name}`;
        if (isSecretEntry(childRel)) {
          if (hits.length < SCAN_MAX_HITS) hits.push(childRel);
          if (hits.length >= SCAN_MAX_HITS) return { hits, truncated };
        } else if (d.isDirectory() && !SCAN_VENDOR_SKIP.has(d.name)) {
          if (depth + 1 <= maxDepth) {
            queue.push({ abs: path2.join(abs, d.name), rel: childRel, depth: depth + 1 });
          } else {
            truncated = true;
          }
        }
      }
    } catch {
      truncated = true;
    } finally {
      dir?.closeSync();
    }
  }
  return { hits, truncated };
}
function isSafeSharedEntry(entry) {
  if (entry.length === 0) return false;
  if (entry.startsWith("!") || entry.startsWith("#")) return false;
  if (entry.startsWith("/") || path2.isAbsolute(entry)) return false;
  if (entry.endsWith("/")) return false;
  if (entry.includes("\\")) return false;
  if (/[*?[\]]/.test(entry)) return false;
  if (entry.split("/").some((segment) => segment === ".." || segment === "." || segment === "")) return false;
  if (entry !== entry.trim()) return false;
  if (/[\x00-\x1f]/.test(entry)) return false;
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
      mkdirSync(path2.dirname(target), { recursive: true });
      symlinkSync(source, target);
      linked.push(entry);
    } catch (error) {
      console.error(`[workspace-manager] failed to link shared resource ${entry}: ${String(error)}`);
    }
  }
  if (linked.length > 0) {
    ensureExcludeEntries(repositoryIdentity, linked);
  }
  return linked;
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

// src/scoped-tree.ts
import { rmSync } from "node:fs";
import os2 from "node:os";
import path3 from "node:path";
function buildScopedStagedTree(repoPath, parentOid, allowedFiles) {
  const raw = runGit(repoPath, ["ls-files", "--stage", "-z"]);
  const index = /* @__PURE__ */ new Map();
  for (const record of raw.split("\0")) {
    if (record.length === 0) continue;
    const tab = record.indexOf("	");
    if (tab === -1) continue;
    const [mode, oid, stage] = record.slice(0, tab).split(/\s+/);
    const p = record.slice(tab + 1);
    const list = index.get(p) ?? [];
    list.push({ mode, oid, stage });
    index.set(p, list);
  }
  const tmpIndex = path3.join(os2.tmpdir(), `ironclaude-scoped-index-${process.pid}-${Date.now()}`);
  const env = { ...process.env, GIT_INDEX_FILE: tmpIndex };
  try {
    runGitEnv(repoPath, ["read-tree", parentOid], env);
    for (const rel of allowedFiles) {
      if (rel === "" || rel.endsWith("/") || path3.posix.isAbsolute(rel) || rel !== path3.posix.normalize(rel)) {
        throw new Error(`Cannot scope commit: allowed_files entry '${rel}' is not a canonical repo-relative path (no leading ./, no .., no //, no absolute path, no trailing slash)`);
      }
      const entries = index.get(rel);
      if (entries && entries.some((e) => e.stage !== "0")) {
        throw new Error(`Cannot scope commit: '${rel}' has an unresolved merge conflict (unmerged index entry); resolve it before committing`);
      }
      const staged = entries?.find((e) => e.stage === "0");
      if (staged) {
        runGitEnv(repoPath, ["update-index", "--add", "--cacheinfo", `${staged.mode},${staged.oid},${rel}`], env);
      } else {
        runGitEnv(repoPath, ["update-index", "--force-remove", "--", rel], env);
      }
    }
    return runGitEnv(repoPath, ["write-tree"], env).trim();
  } finally {
    try {
      rmSync(tmpIndex, { force: true });
    } catch {
    }
  }
}

// src/plan-scope.ts
import Database2 from "better-sqlite3";
import os3 from "node:os";
import path4 from "node:path";
function stateDbPath() {
  return process.env.STATE_MANAGER_DB_PATH ?? path4.join(os3.homedir(), ".claude", "ironclaude.db");
}
function readSessionAllowedFiles(providerRootSessionId) {
  let sdb;
  try {
    sdb = new Database2(stateDbPath(), { readonly: true, fileMustExist: true, timeout: 1e4 });
  } catch (e) {
    throw new Error(`Cannot read plan scope: state DB unreadable (${e.message})`);
  }
  try {
    const rows = sdb.prepare("SELECT allowed_files FROM wave_tasks WHERE terminal_session = ?").all(providerRootSessionId);
    const set = /* @__PURE__ */ new Set();
    for (const r of rows) {
      if (!r.allowed_files) continue;
      const arr = JSON.parse(r.allowed_files);
      if (Array.isArray(arr)) {
        for (const f of arr) if (typeof f === "string" && f.length > 0) set.add(f);
      }
    }
    if (set.size === 0) {
      throw new Error("Cannot read plan scope: no allowed_files for this session (no active plan)");
    }
    return [...set].sort();
  } finally {
    sdb.close();
  }
}

// src/git-authority.ts
var OID = /^[0-9a-f]{40,64}$/i;
function denyEvidence() {
  throw new Error("Direct Git authority evidence changed or is malformed");
}
function closeOutIntentEvidence(assignment) {
  return {
    checkoutMode: "managed",
    canonicalBranch: assignment.branch,
    localRef: `refs/heads/${assignment.branch}`,
    headOid: ""
  };
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
function resolveEffectiveCheckout(db, input, workspaceGuid) {
  const repository = discoverRepository(input.repositoryPath);
  const assignment = getAssignment(db, workspaceGuid);
  if (!assignment || assignment.repository_identity !== repository.repositoryIdentity || assignment.owner_session_id !== input.providerRootSessionId) {
    throw new Error("Direct Git authority provider root, repository, or workspace binding does not match");
  }
  const expectedPath = path5.join(repository.primaryCheckoutPath, ".ironclaude", "worktrees", assignment.workspace_guid);
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
    return { assignment, mode: "managed", path: assignment.worktree_path };
  }
  return { assignment, mode: "primary", path: repository.primaryCheckoutPath };
}
function resolveUnassignedPrimaryCheckout(db, repositoryPath, providerRootSessionId) {
  const repository = discoverRepository(repositoryPath);
  const active = db.prepare(`
    SELECT COUNT(*) AS n FROM assignments
    WHERE repository_identity = ? AND owner_session_id = ?
      AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
  `).get(repository.repositoryIdentity, providerRootSessionId);
  if (active.n !== 0) throw new Error("Unassigned-primary direct-Git requires zero active assignments for this session and repository");
  const primaryOwner = db.prepare(`
    SELECT owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?
  `).get(repository.repositoryIdentity);
  if (primaryOwner && primaryOwner.owner_session_id !== providerRootSessionId) {
    throw new Error("Primary checkout is owned by another session");
  }
  let branch;
  try {
    branch = runGit(repository.primaryCheckoutPath, ["symbolic-ref", "--quiet", "--short", "HEAD"]).trim();
  } catch {
    throw new Error("Unassigned-primary direct-Git requires a checked-out branch (HEAD is detached)");
  }
  if (branch.length === 0) throw new Error("Unassigned-primary direct-Git requires a checked-out branch (HEAD is detached)");
  return { mode: "primary-unassigned", path: repository.primaryCheckoutPath };
}
function observeUnassignedCommitEvidence(path8, allowedFiles) {
  const canonicalBranch = runGit(path8, ["symbolic-ref", "--quiet", "--short", "HEAD"]).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const parentOid = runGit(path8, ["rev-parse", "--verify", "HEAD^{commit}"]).trim();
  const stagedTree = buildScopedStagedTree(path8, parentOid, allowedFiles);
  if (stagedTree === runGit(path8, ["rev-parse", "--verify", `${parentOid}^{tree}`]).trim()) {
    throw new Error("Nothing to commit within this session's allowed_files (only foreign or unchanged files are staged)");
  }
  return {
    checkoutMode: "primary-unassigned",
    canonicalBranch,
    localRef,
    stagedTree,
    parentRef: "HEAD",
    parentOid,
    allowedFiles
  };
}
function assertFastForwardPush(worktreePath, evidence) {
  if (evidence.expectedRemoteOldOid === null) return;
  if (!isAncestor(worktreePath, evidence.expectedRemoteOldOid, evidence.localOid)) {
    throw new Error("Unassigned-primary push must be fast-forward; non-fast-forward to a shared branch is refused");
  }
}
function observeUnassignedPushEvidence(path8) {
  const canonicalBranch = runGit(path8, ["symbolic-ref", "--quiet", "--short", "HEAD"]).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const remoteName = "origin";
  const remoteUrl = runGit(path8, ["remote", "get-url", remoteName]).trim();
  const pushUrl = runGit(path8, ["remote", "get-url", "--push", remoteName]).trim();
  if (remoteUrl !== pushUrl) denyEvidence();
  const evidence = {
    checkoutMode: "primary-unassigned",
    canonicalBranch,
    localRef,
    localOid: runGit(path8, ["rev-parse", "--verify", `${localRef}^{commit}`]).trim(),
    remoteName,
    remoteUrl,
    destinationRef: localRef,
    expectedRemoteOldOid: remoteOldOid(path8, remoteName, localRef)
  };
  assertFastForwardPush(path8, evidence);
  return evidence;
}
function observeUnassignedCommitAndPushEvidence(path8, allowedFiles) {
  const canonicalBranch = runGit(path8, ["symbolic-ref", "--quiet", "--short", "HEAD"]).trim();
  const localRef = `refs/heads/${canonicalBranch}`;
  const remoteName = "origin";
  const remoteUrl = runGit(path8, ["remote", "get-url", remoteName]).trim();
  const pushUrl = runGit(path8, ["remote", "get-url", "--push", remoteName]).trim();
  if (remoteUrl !== pushUrl) denyEvidence();
  const parentOid = runGit(path8, ["rev-parse", "--verify", "HEAD^{commit}"]).trim();
  const expectedRemoteOldOid = remoteOldOid(path8, remoteName, localRef);
  if (expectedRemoteOldOid !== null && !isAncestor(path8, expectedRemoteOldOid, parentOid)) {
    throw new Error("Unassigned-primary push must be fast-forward; non-fast-forward to a shared branch is refused");
  }
  const stagedTree = buildScopedStagedTree(path8, parentOid, allowedFiles);
  if (stagedTree === runGit(path8, ["rev-parse", "--verify", `${parentOid}^{tree}`]).trim()) {
    throw new Error("Nothing to commit within this session's allowed_files (only foreign or unchanged files are staged)");
  }
  return {
    checkoutMode: "primary-unassigned",
    canonicalBranch,
    localRef,
    stagedTree,
    parentRef: "HEAD",
    parentOid,
    remoteName,
    remoteUrl,
    destinationRef: localRef,
    expectedRemoteOldOid,
    allowedFiles
  };
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
  if (operation === "reconcile" || operation === "close-out" || operation === "confirm-resolution") {
    if (checkout.mode !== "managed") denyEvidence();
    return {
      ...branch,
      checkoutMode: "managed",
      headOid: runGit(checkout.path, ["rev-parse", "--verify", "HEAD^{commit}"]).trim()
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
  if (input.workspaceGuid === void 0) {
    if (input.operation !== "commit" && input.operation !== "push" && input.operation !== "commit-and-push") {
      throw new Error("Unassigned-primary lane supports commit, push, and commit-and-push only");
    }
    const repository = discoverRepository(input.repositoryPath);
    const unassigned = resolveUnassignedPrimaryCheckout(db, input.repositoryPath, input.providerRootSessionId);
    const allowedFiles = input.operation === "push" ? void 0 : readSessionAllowedFiles(input.providerRootSessionId);
    const evidence2 = input.operation === "commit" ? observeUnassignedCommitEvidence(unassigned.path, allowedFiles) : input.operation === "push" ? observeUnassignedPushEvidence(unassigned.path) : observeUnassignedCommitAndPushEvidence(unassigned.path, allowedFiles);
    return issueHumanIntent(db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.providerRootSessionId,
      repositoryIdentity: repository.repositoryIdentity,
      workspaceGuid: `primary:${repository.repositoryIdentity}`,
      expectedEvidence: evidence2
    });
  }
  const checkout = resolveEffectiveCheckout(db, input, input.workspaceGuid);
  const assignment = checkout.assignment;
  if (input.operation === "close-out") {
    return issueHumanIntent(db, {
      operation: input.operation,
      humanChannel: input.humanChannel,
      providerRootSessionId: input.providerRootSessionId,
      repositoryIdentity: assignment.repository_identity,
      workspaceGuid: assignment.workspace_guid,
      expectedEvidence: closeOutIntentEvidence(assignment)
    });
  }
  const evidence = observeDirectEvidence(checkout, input.operation);
  return issueHumanIntent(db, {
    operation: input.operation,
    humanChannel: input.humanChannel,
    providerRootSessionId: input.providerRootSessionId,
    repositoryIdentity: assignment.repository_identity,
    workspaceGuid: assignment.workspace_guid,
    expectedEvidence: evidence
  });
}

// src/workspace-service.ts
import { randomUUID as randomUUID2 } from "node:crypto";
import { existsSync as existsSync2 } from "node:fs";
import path6 from "node:path";

// src/integration.ts
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
function pushPendingSummary(disposition) {
  const decoded = decodePushDisposition(disposition);
  return decoded && (decoded.phase === "push-pending" || decoded.phase === "push-failed") ? { candidateCommit: decoded.candidateCommit, remoteUrl: decoded.remoteUrl, destinationRef: decoded.destinationRef } : void 0;
}

// src/workspace-service.ts
function managedWorktreePath(primaryCheckoutPath, workspaceGuid) {
  return path6.join(primaryCheckoutPath, ".ironclaude", "worktrees", workspaceGuid);
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
    const worktreeGone = priorRow !== void 0 && !existsSync2(priorRow.worktree_path) && !worktreeExists(repository.primaryCheckoutPath, priorRow.worktree_path);
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
    const worktreePresent = existsSync2(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
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
    if (existsSync2(assignment.worktree_path) || worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path)) {
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
    const present = existsSync2(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
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
    const present = existsSync2(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
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
    const present = existsSync2(assignment.worktree_path) && worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path);
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
    if (existsSync2(assignment.worktree_path) || worktreeExists(repository.primaryCheckoutPath, assignment.worktree_path)) {
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
    const knownPaths = new Set(assignments.map((assignment) => path6.resolve(assignment.worktree_path)));
    const managedRoot = path6.join(repository.primaryCheckoutPath, ".ironclaude", "worktrees") + path6.sep;
    const ambiguousWorktreePaths = observed.filter((worktree) => worktree.path.startsWith(managedRoot) && worktree.branch?.startsWith("refs/heads/ironclaude/") && !knownPaths.has(worktree.path)).map((worktree) => worktree.path).sort();
    return {
      repositoryIdentity: repository.repositoryIdentity,
      knownWorktreePaths: assignments.map((assignment) => path6.resolve(assignment.worktree_path)).filter((worktreePath) => observedPaths.has(worktreePath)).sort(),
      missingWorktreePaths: assignments.map((assignment) => path6.resolve(assignment.worktree_path)).filter((worktreePath) => !observedPaths.has(worktreePath)).sort(),
      ambiguousWorktreePaths
    };
  }
  /**
   * Current explicit shared-resource entries configured for the repository,
   * wrapped in an object. The return MUST be an object (not a bare array): the
   * Commander's WorkspaceClient._decode rejects any non-object JSON response, so a
   * bare array would make the orchestrator list tool error on every real call.
   */
  listSharedResources(input) {
    const repository = discoverRepository(input.repositoryPath);
    return { entries: readSharedResourceConfig(repository.repositoryIdentity) };
  }
  /**
   * Add explicit shared-resource entries for a repository and relink the newly
   * added ones into every currently-live MANAGED worktree, so a running worker
   * gets the data without a respawn. Only live managed worktrees (rows in the
   * assignments table, non-terminal, still on disk) are relinked — operator-created
   * or orphaned worktrees are never touched. `relinked` reports the entries actually
   * planted per worktree (a source-absent entry is written to config but not linked).
   */
  configureSharedResources(input) {
    const repository = discoverRepository(input.repositoryPath);
    const written = addSharedResourceEntries(
      repository.repositoryIdentity,
      input.entries,
      repository.primaryCheckoutPath,
      input.allowSecretEntries ?? false
    );
    const relinked = {};
    const toRelink = [...written.added, ...written.skipped];
    if (toRelink.length > 0) {
      const liveManaged = this.db.prepare(`
        SELECT worktree_path FROM assignments
        WHERE repository_identity = ?
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
      `).all(repository.repositoryIdentity);
      for (const { worktree_path } of liveManaged) {
        if (!existsSync2(worktree_path)) continue;
        const planted = linkSharedResources(
          repository.primaryCheckoutPath,
          worktree_path,
          repository.repositoryIdentity,
          toRelink
        );
        if (planted.length > 0) relinked[worktree_path] = planted;
      }
    }
    return { ...written, relinked };
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
  if (operation === "close-out") {
    const closeable = db.prepare(`
      SELECT * FROM assignments
      WHERE repository_identity = ? AND owner_session_id = ?
        AND lifecycle_status IN ('active', 'ready_for_integration', 'integrated')
      ORDER BY created_at ASC
    `).all(repository.repositoryIdentity, ownerSessionId);
    if (closeable.length !== 1) {
      throw new Error("Human intent issuance requires exactly one closeable assignment for provider root and repository");
    }
    const closeableAssignment = closeable[0];
    const requestedCloseGuid = optionalString(args, "workspace_guid");
    if (requestedCloseGuid !== void 0 && requestedCloseGuid !== closeableAssignment.workspace_guid) {
      throw new Error("Human intent workspace binding does not match closeable assignment");
    }
    return issueDirectGitHumanIntent(db, {
      repositoryPath,
      workspaceGuid: closeableAssignment.workspace_guid,
      providerRootSessionId: ownerSessionId,
      humanChannel,
      operation: "close-out"
    });
  }
  const assignments = db.prepare(`
    SELECT * FROM assignments
    WHERE repository_identity = ? AND owner_session_id = ?
      AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned')
    ORDER BY created_at ASC
  `).all(repository.repositoryIdentity, ownerSessionId);
  const requestedGuid = optionalString(args, "workspace_guid");
  if (assignments.length === 0) {
    if (requestedGuid === void 0 && (operation === "commit" || operation === "push" || operation === "commit-and-push")) {
      return issueDirectGitHumanIntent(db, {
        repositoryPath,
        providerRootSessionId: ownerSessionId,
        humanChannel,
        operation
      });
    }
    throw new Error("Human intent issuance requires exactly one active assignment for provider root and repository");
  }
  if (assignments.length !== 1) {
    throw new Error("Human intent issuance requires exactly one active assignment for provider root and repository");
  }
  const assignment = assignments[0];
  if (requestedGuid !== void 0 && requestedGuid !== assignment.workspace_guid) {
    throw new Error("Human intent workspace binding does not match active assignment");
  }
  if (operation === "commit" || operation === "commit-and-push" || operation === "push" || operation === "reconcile" || operation === "confirm-resolution") {
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
var invokedPath = process.argv[1] ? path7.resolve(process.argv[1]) : null;
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
