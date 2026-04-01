#!/usr/bin/env node
/**
 * Thin launcher for state-manager MCP server.
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
const LOG_FILE = join(homedir(), '.claude', 'ironclaude-mcp-state-manager.log');

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

// Plugin root is THREE levels up from cli/ (cli/ -> state-manager/ -> mcp-servers/ -> plugin root)
const PLUGIN_ROOT = join(__dirname, '..', '..', '..');

// The state-manager module root is one level up from cli/
const STATE_MANAGER_ROOT = join(__dirname, '..');

/**
 * Auto-build: ensure dist/ bundle and native bindings exist.
 * Runs on first startup after plugin install (no install hook in Claude Code).
 */
function ensureBuildComplete() {
  const nodeModules = join(STATE_MANAGER_ROOT, 'node_modules');
  const distFile = join(STATE_MANAGER_ROOT, 'dist', 'index.js');
  const nativeBinding = join(STATE_MANAGER_ROOT, 'node_modules', 'better-sqlite3', 'build', 'Release', 'better_sqlite3.node');

  if (existsSync(nodeModules) && existsSync(distFile) && existsSync(nativeBinding)) return;

  log('[auto-build] First run detected — building artifacts...');

  // Install dependencies if node_modules missing
  if (!existsSync(nodeModules)) {
    log('[auto-build] Installing npm dependencies...');
    try {
      execSync('npm install', {
        cwd: STATE_MANAGER_ROOT, stdio: 'pipe', timeout: 180000
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
    log('[auto-build] Building dist/index.js with esbuild...');
    try {
      mkdirSync(join(STATE_MANAGER_ROOT, 'dist'), { recursive: true });
      execSync(
        'npx esbuild src/index.ts --bundle --platform=node --format=esm --outfile=dist/index.js --external:fsevents --external:better-sqlite3',
        { cwd: STATE_MANAGER_ROOT, stdio: 'pipe', timeout: 60000 }
      );
      log('[auto-build] dist/index.js built successfully');
    } catch (err) {
      log(`[auto-build] FAILED to build dist/index.js: ${err.message}`);
      if (err.stderr) log(`[auto-build] stderr: ${err.stderr.toString().slice(0, 2000)}`);
      process.exit(1);
    }
  }

  // Rebuild native binding if missing
  if (!existsSync(nativeBinding)) {
    log('[auto-build] Rebuilding better-sqlite3 native binding...');
    try {
      execSync('npm rebuild better-sqlite3', {
        cwd: STATE_MANAGER_ROOT, stdio: 'pipe', timeout: 120000
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
    log(`Starting state-manager wrapper (PID=${process.pid}, PPID=${process.ppid})`);

    ensureSqlite3();
    repairCacheIfNeeded();
    ensureBuildComplete();

    const mcpServerPath = join(STATE_MANAGER_ROOT, 'dist', 'index.js');
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
