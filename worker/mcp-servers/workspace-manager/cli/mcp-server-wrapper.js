#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const wrapperDirectory = dirname(fileURLToPath(import.meta.url));
const packageRoot = join(wrapperDirectory, '..');
const bundle = join(packageRoot, 'dist', 'index.js');
const nativeBinding = join(packageRoot, 'node_modules', 'better-sqlite3', 'build', 'Release', 'better_sqlite3.node');

// npm chatter must NEVER reach stdout: this wrapper's stdout IS the MCP
// JSON-RPC transport, so `stdio: 'inherit'` corrupts the protocol stream on the
// first launch after install (node_modules is not tracked, so that is the
// normal case, not an edge case). Mirrors state-manager's wrapper, which pipes
// every npm subprocess. stderr is safe and is where npm's progress belongs.
const buildStdio = ['ignore', 'pipe', 'inherit'];

function ensureRuntime() {
  if (existsSync(bundle) && existsSync(nativeBinding)) return;
  mkdirSync(join(packageRoot, 'dist'), { recursive: true });
  if (!existsSync(join(packageRoot, 'node_modules'))) {
    execFileSync('npm', ['install'], { cwd: packageRoot, stdio: buildStdio, timeout: 180_000 });
  }
  if (!existsSync(nativeBinding)) {
    execFileSync('npm', ['rebuild', 'better-sqlite3'], { cwd: packageRoot, stdio: buildStdio, timeout: 120_000 });
  }
  if (!existsSync(bundle)) {
    execFileSync('npm', ['run', 'build'], { cwd: packageRoot, stdio: buildStdio, timeout: 120_000 });
  }
}

(async () => {
  try {
    // ensureRuntime() runs BEFORE import so npm chatter never reaches the stdio
    // transport (npm's stdout is discarded via buildStdio's 'ignore').
    ensureRuntime();

    // In-process load: the bundle runs in THIS process (no spawned child dist proc).
    // CLAUDE_PPID must be set BEFORE import — the bundle reads it at module-load for
    // session resolution and the parent-death poller.
    process.env.CLAUDE_PPID = String(process.ppid);
    for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
      process.on(sig, () => { process.exit(0); });
    }

    // The bundle guards its own start on argv[1] === import.meta.url (FALSE under
    // import), so we call its exported entry point explicitly.
    const mod = await import(pathToFileURL(bundle).href);
    if (typeof mod.startWorkspaceManagerServer !== 'function')
      throw new Error('workspace-manager bundle lacks startWorkspaceManagerServer export');
    await mod.startWorkspaceManagerServer();
  } catch (error) {
    console.error(`workspace-manager startup failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  }
})();
