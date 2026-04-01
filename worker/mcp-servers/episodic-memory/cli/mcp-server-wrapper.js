#!/usr/bin/env node
/**
 * Thin launcher for episodic-memory MCP server.
 * Dependencies are committed to git — no runtime npm install needed.
 */

import { spawn, execSync } from 'child_process';
import { existsSync, cpSync, appendFileSync, mkdirSync } from 'fs';
import { dirname, join, sep } from 'path';
import { fileURLToPath } from 'url';
import { homedir } from 'os';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// File-based logging — Claude Code swallows stderr, so we need a persistent log
const LOG_FILE = join(homedir(), '.claude', 'ironclaude-mcp-episodic-memory.log');

function log(msg) {
  const ts = new Date().toISOString();
  const line = `[${ts}] ${msg}\n`;
  try { appendFileSync(LOG_FILE, line); } catch {}
  console.error(msg);
}

function ensureSqlite3() {
  try {
    execSync('command -v sqlite3', { stdio: 'pipe' });
  } catch {
    log('[startup] FATAL: sqlite3 CLI not found in PATH.');
    log('[startup] IronClaude hooks require the sqlite3 command-line tool.');
    log('[startup] Install it:');
    log('[startup]   macOS:         brew install sqlite3  (usually pre-installed)');
    log('[startup]   Ubuntu/Debian: sudo apt install sqlite3');
    log('[startup]   Alpine:        apk add sqlite');
    log('[startup]   Fedora/RHEL:   sudo dnf install sqlite');
    process.exit(1);
  }
}

// Plugin root is THREE levels up from cli/ (cli/ -> episodic-memory/ -> mcp-servers/ -> plugin root)
const PLUGIN_ROOT = join(__dirname, '..', '..', '..');

// The episodic-memory module root is one level up from cli/
const EPISODIC_MEMORY_ROOT = join(__dirname, '..');

/**
 * Auto-build: ensure dist/ bundle and native bindings exist.
 * Runs on first startup after plugin install (no install hook in Claude Code).
 */
function ensureBuildComplete() {
  const nodeModules = join(EPISODIC_MEMORY_ROOT, 'node_modules');
  const distFile = join(EPISODIC_MEMORY_ROOT, 'dist', 'mcp-server.js');
  const sqliteBinding = join(EPISODIC_MEMORY_ROOT, 'node_modules', 'better-sqlite3', 'build', 'Release', 'better_sqlite3.node');
  const sharpBinding = join(EPISODIC_MEMORY_ROOT, 'node_modules', 'sharp', 'build', 'Release');

  if (existsSync(nodeModules) && existsSync(distFile) && existsSync(sqliteBinding) && existsSync(sharpBinding)) return;

  log('[auto-build] First run detected — building artifacts...');

  // Install dependencies if node_modules missing
  if (!existsSync(nodeModules)) {
    log('[auto-build] Installing npm dependencies...');
    try {
      execSync('npm install', {
        cwd: EPISODIC_MEMORY_ROOT, stdio: 'pipe', timeout: 300000
      });
      log('[auto-build] npm install completed successfully');
    } catch (err) {
      log(`[auto-build] FAILED npm install: ${err.message}`);
      if (err.stderr) log(`[auto-build] stderr: ${err.stderr.toString().slice(0, 2000)}`);
      process.exit(1);
    }
  }

  // Build dist/ bundle if missing
  if (!existsSync(distFile)) {
    log('[auto-build] Building dist/mcp-server.js with esbuild...');
    try {
      mkdirSync(join(EPISODIC_MEMORY_ROOT, 'dist'), { recursive: true });
      execSync(
        'npx esbuild src/mcp-server.ts --bundle --platform=node --format=esm --outfile=dist/mcp-server.js --external:fsevents --external:@anthropic-ai/claude-agent-sdk --external:sharp --external:onnxruntime-node --external:better-sqlite3 --external:@xenova/transformers --external:sqlite-vec',
        { cwd: EPISODIC_MEMORY_ROOT, stdio: 'pipe', timeout: 60000 }
      );
      log('[auto-build] dist/mcp-server.js built successfully');
    } catch (err) {
      log(`[auto-build] FAILED to build dist/mcp-server.js: ${err.message}`);
      if (err.stderr) log(`[auto-build] stderr: ${err.stderr.toString().slice(0, 2000)}`);
      process.exit(1);
    }
  }

  // Rebuild better-sqlite3 if missing
  if (!existsSync(sqliteBinding)) {
    log('[auto-build] Rebuilding better-sqlite3 native binding...');
    try {
      execSync('npm rebuild better-sqlite3', {
        cwd: EPISODIC_MEMORY_ROOT, stdio: 'pipe', timeout: 120000
      });
      log('[auto-build] better-sqlite3 rebuilt successfully');
    } catch (err) {
      log(`[auto-build] FAILED to rebuild better-sqlite3: ${err.message}`);
      if (err.stderr) log(`[auto-build] stderr: ${err.stderr.toString().slice(0, 2000)}`);
      log('[auto-build] If this persists, install build tools:');
      log('[auto-build]   macOS: xcode-select --install');
      log('[auto-build]   Linux: sudo apt install build-essential');
      log('[auto-build]   Windows: npm install --global windows-build-tools');
      process.exit(1);
    }
  }

  // Rebuild sharp if missing
  if (!existsSync(sharpBinding)) {
    log('[auto-build] Rebuilding sharp native binding...');
    try {
      execSync('npm rebuild sharp', {
        cwd: EPISODIC_MEMORY_ROOT, stdio: 'pipe', timeout: 120000
      });
      log('[auto-build] sharp rebuilt successfully');
    } catch (err) {
      log(`[auto-build] FAILED to rebuild sharp: ${err.message}`);
      if (err.stderr) log(`[auto-build] stderr: ${err.stderr.toString().slice(0, 2000)}`);
      process.exit(1);
    }
  }

  log('[auto-build] All artifacts ready');
}

/**
 * Cache repair: Claude Code may not copy all directories to the plugin cache.
 * If hooks/ is missing, copy from the marketplace clone.
 */
function repairCacheIfNeeded() {
  const hooksDir = join(PLUGIN_ROOT, 'hooks');
  if (existsSync(hooksDir)) return;

  log('Cache incomplete — hooks/ missing, attempting repair...');

  const parts = PLUGIN_ROOT.split(sep);
  const cacheIndex = parts.indexOf('cache');
  if (cacheIndex === -1) return;

  const marketplace = parts[cacheIndex + 1];
  const plugin = parts[cacheIndex + 2];
  const pluginsDir = parts.slice(0, cacheIndex).join(sep);
  const marketplaceSource = join(pluginsDir, 'marketplaces', marketplace, 'plugins', plugin);

  if (!existsSync(marketplaceSource)) return;

  for (const dir of ['hooks', 'agents', '.claude-plugin', 'rules']) {
    const srcDir = join(marketplaceSource, dir);
    const dstDir = join(PLUGIN_ROOT, dir);
    if (existsSync(srcDir) && !existsSync(dstDir)) {
      try {
        cpSync(srcDir, dstDir, { recursive: true });
        log(`[cache-repair] Copied ${dir}/`);
      } catch (err) {
        log(`[cache-repair] Failed to copy ${dir}/: ${err.message}`);
      }
    }
  }
}

async function main() {
  try {
    log(`Starting episodic-memory wrapper (PID=${process.pid}, PPID=${process.ppid})`);

    ensureSqlite3();
    repairCacheIfNeeded();
    ensureBuildComplete();

    const mcpServerPath = join(EPISODIC_MEMORY_ROOT, 'dist', 'mcp-server.js');
    log(`Launching MCP server: ${mcpServerPath}`);

    const child = spawn(process.execPath, [mcpServerPath], {
      stdio: 'inherit',
      shell: false,
      env: { ...process.env, CLAUDE_PPID: String(process.ppid) }
    });

    process.on('SIGTERM', () => child.kill('SIGTERM'));
    process.on('SIGINT', () => child.kill('SIGINT'));

    child.on('exit', (code, signal) => {
      log(`MCP server exited: code=${code} signal=${signal}`);
      if (signal) process.kill(process.pid, signal);
      else process.exit(code || 0);
    });

    child.on('error', (err) => {
      log(`MCP server spawn error: ${err.message}`);
      process.exit(1);
    });

  } catch (error) {
    log(`FATAL: ${error.message}`);
    process.exit(1);
  }
}

main().catch((error) => {
  log(`Unexpected error: ${error.message}`);
  process.exit(1);
});
