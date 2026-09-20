import { describe, it, expect } from 'vitest';
import { spawn } from 'node:child_process';
import path from 'node:path';

const packageRoot = path.resolve(__dirname, '../..');
const serverEntry = path.join(packageRoot, 'dist', 'index.js');

describe('parent-death-exit', () => {
  it(
    'exits when stdin receives EOF',
    async () => {
      const child = spawn(process.execPath, [serverEntry], {
        stdio: ['pipe', 'ignore', 'ignore'],
        cwd: packageRoot,
        env: {
          ...process.env,
          IRONCLAUDE_CLIENT: 'claude',
        },
      });

      try {
        const exitPromise = new Promise<number | null>((resolve, reject) => {
          const timer = setTimeout(() => {
            reject(new Error('timed out waiting for child exit after stdin EOF'));
          }, 5000);
          child.on('exit', (code) => {
            clearTimeout(timer);
            resolve(code);
          });
        });

        child.stdin!.end();

        const code = await exitPromise;
        expect(code).toBe(0);
      } finally {
        if (!child.killed) {
          child.kill('SIGKILL');
        }
      }
    },
    { timeout: 10000 }
  );

  it(
    'exits when the CLAUDE_PPID process dies',
    async () => {
      const helper = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1e9)']);

      const child = spawn(process.execPath, [serverEntry], {
        stdio: ['pipe', 'ignore', 'ignore'],
        cwd: packageRoot,
        env: {
          ...process.env,
          IRONCLAUDE_CLIENT: 'claude',
          CLAUDE_PPID: String(helper.pid),
          IC_PPID_POLL_MS: '500',
        },
      });

      try {
        const exitPromise = new Promise<number | null>((resolve, reject) => {
          const timer = setTimeout(() => {
            reject(new Error('timed out waiting for child exit after PPID death'));
          }, 5000);
          child.on('exit', (code) => {
            clearTimeout(timer);
            resolve(code);
          });
        });

        helper.kill('SIGKILL');

        const code = await exitPromise;
        expect(code).toBe(0);
      } finally {
        if (!helper.killed) {
          helper.kill('SIGKILL');
        }
        if (!child.killed) {
          child.kill('SIGKILL');
        }
      }
    },
    { timeout: 10000 }
  );
});
