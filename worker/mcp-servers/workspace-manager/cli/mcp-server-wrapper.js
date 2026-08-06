#!/usr/bin/env node

import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

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

try {
  ensureRuntime();
  const child = spawn(process.execPath, [bundle], {
    env: { ...process.env, CLAUDE_PPID: String(process.ppid) },
    shell: false,
    stdio: 'inherit',
  });
  process.on('SIGTERM', () => child.kill('SIGTERM'));
  process.on('SIGINT', () => child.kill('SIGINT'));
  child.on('error', (error) => {
    console.error(`workspace-manager spawn failed: ${error.message}`);
    process.exit(1);
  });
  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code ?? 0);
  });
} catch (error) {
  console.error(`workspace-manager startup failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
