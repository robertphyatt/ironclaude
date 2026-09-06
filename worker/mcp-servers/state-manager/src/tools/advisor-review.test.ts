import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import {
  spawn as nodeSpawn,
  type ChildProcessWithoutNullStreams,
  type SpawnOptionsWithoutStdio,
} from 'node:child_process';
import { access, mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import type { SessionIdentity } from '../session-identity.js';
import type { RuntimeFingerprintCapture } from '../runtime-fingerprint.js';
import type { Session } from '../types.js';
import {
  MAX_PACKET_BYTES,
  MAX_STDOUT_BYTES,
  advisorReviewToolDefinition,
  resolveTrustedCodexRequesterModel,
  runCodexAdvisorReview,
  runFixedProcess,
  type AdvisorReviewDeps,
} from './advisor-review.js';


const identity: SessionIdentity = {
  client: 'codex',
  sessionId: 'root-session',
  invocationThreadId: 'root-session',
  source: 'codex_meta',
};

const session: Session = {
  terminal_session: identity.sessionId,
  professional_mode: 'on',
  workflow_stage: 'final_plan_prep',
  active_skill: null,
  brainstorming_active: 0,
  plan_name: null,
  plan_json: null,
  current_wave: 0,
  review_pending: 0,
  review_block_count: 0,
  plan_lineage: 1,
  inherit_review: 0,
  circuit_breaker: 0,
  memory_search_required: 0,
  testing_theatre_checked: 0,
  project_hash: null,
  updated_at: '2026-08-31 00:00:00',
};

const runtime: RuntimeFingerprintCapture = {
  ok: true,
  runtime: {
    plugin_name: 'ironclaude',
    plugin_version: '1.1.7+codex.test',
    plugin_root: '/installed/ironclaude',
    manifest_path: '/installed/ironclaude/.codex-plugin/plugin.json',
    manifest_sha256: 'a'.repeat(64),
    state_manager_bundle_path: '/installed/ironclaude/mcp-servers/state-manager/dist/index.js',
    state_manager_bundle_sha256: 'b'.repeat(64),
    workspace_manager_bundle_path: '/installed/ironclaude/mcp-servers/workspace-manager/dist/index.js',
    workspace_manager_bundle_sha256: 'c'.repeat(64),
    workspace_manager_cli_path: '/installed/ironclaude/mcp-servers/workspace-manager/dist/cli.js',
    workspace_manager_cli_sha256: 'd'.repeat(64),
    workspace_manager_hook_intent_path: '/installed/ironclaude/mcp-servers/workspace-manager/dist/hook-intent.js',
    workspace_manager_hook_intent_sha256: 'e'.repeat(64),
    client: 'codex',
  },
};

type ProcessFixture = {
  stdout?: string;
  stderr?: string;
  code?: number;
  hang?: boolean;
  ignoreSigterm?: boolean;
};

let nextFakePid = 20000;

class FakeChild extends EventEmitter {
  stdin = new PassThrough();
  stdout = new PassThrough();
  stderr = new PassThrough();
  killed = false;
  alive = true;
  pid = nextFakePid++;
  killSignals: Array<NodeJS.Signals | number | undefined> = [];

  constructor(private fixture: ProcessFixture) {
    super();
    if (!fixture.hang) {
      queueMicrotask(() => {
        this.stdout.end(fixture.stdout ?? '');
        this.stderr.end(fixture.stderr ?? '');
        queueMicrotask(() => {
          this.alive = false;
          this.emit('close', fixture.code ?? 0, null);
        });
      });
    } else if (fixture.stdout || fixture.stderr) {
      queueMicrotask(() => {
        if (fixture.stdout) this.stdout.write(fixture.stdout);
        if (fixture.stderr) this.stderr.write(fixture.stderr);
      });
    }
  }

  kill(signal?: NodeJS.Signals | number): boolean {
    this.killed = true;
    this.killSignals.push(signal);
    if (signal !== 'SIGTERM' || !this.fixture.ignoreSigterm) {
      this.alive = false;
      queueMicrotask(() => this.emit('close', null, signal ?? 'SIGTERM'));
    }
    return true;
  }
}

function healthyPreflight() {
  return JSON.stringify({
    schema_version: 1,
    mode: 'check',
    status: 'healthy',
    invoked_launcher: '/trusted/bin/codex',
    resolved_launcher: '/trusted/app/codex',
    source_companion: '/trusted/app/codex-code-mode-host',
    destination_companion: '/trusted/bin/codex-code-mode-host',
    action: 'none',
    reason: 'destination-equivalent',
  }) + '\n';
}

function agentMessage(text: string) {
  return JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text } }) + '\n';
}

function harness(fixtures: ProcessFixture[] = [
  { stdout: healthyPreflight() },
  { stdout: agentMessage('review report') },
]) {
  const children: FakeChild[] = [];
  const inputs: string[] = [];
  const spawn = vi.fn((
    _command: string,
    _args: readonly string[],
    _options: SpawnOptionsWithoutStdio,
  ) => {
    const fixture = fixtures[children.length] ?? { code: 99, stderr: 'unexpected spawn' };
    const child = new FakeChild(fixture);
    let input = '';
    child.stdin.on('data', (chunk) => { input += chunk.toString(); });
    child.stdin.on('finish', () => { inputs.push(input); });
    children.push(child);
    return child as unknown as ChildProcessWithoutNullStreams;
  });
  const rm = vi.fn(async () => undefined);
  const deps: AdvisorReviewDeps = {
    spawn,
    mkdtemp: vi.fn(async () => '/private/tmp/ironclaude-advisor-fixed'),
    rm,
    access: vi.fn(async () => undefined),
    env: {
      PATH: '/trusted/bin:/usr/bin',
      HOME: '/Users/test',
      CODEX_HOME: '/Users/test/.codex',
      OPENAI_API_KEY: 'allowed-provider-credential',
      NODE_OPTIONS: '--require=/tmp/evil.js',
      EVIL: 'drop-me',
    },
    tmpdir: '/private/tmp',
    now: (() => {
      let value = 1000;
      return () => value += 10;
    })(),
    trustedRequesterModel: 'gpt-5.6-sol',
    platform: 'darwin',
    killProcessGroup: vi.fn((pid, signal) => {
      const child = children.find((candidate) => candidate.pid === pid);
      if (!child) throw new Error(`unknown process group ${pid}`);
      child.kill(signal);
    }),
    isProcessGroupAlive: vi.fn((pid) => children.some(
      (candidate) => candidate.pid === pid && candidate.alive,
    )),
  };
  return { deps, spawn, children, inputs, rm };
}

function payload(result: Awaited<ReturnType<typeof runCodexAdvisorReview>>) {
  return JSON.parse(result.content[0].text) as Record<string, unknown>;
}


describe('run_codex_advisor_review definition', () => {
  it('exposes optional bounded review_tier alongside required packet and requester_model', () => {
    expect(advisorReviewToolDefinition.inputSchema).toEqual(expect.objectContaining({
      required: ['packet', 'requester_model'],
      additionalProperties: false,
    }));
    expect(Object.keys(advisorReviewToolDefinition.inputSchema.properties)).toEqual([
      'packet',
      'requester_model',
      'review_tier',
    ]);
    expect(advisorReviewToolDefinition.inputSchema.properties.review_tier).toEqual({
      type: 'string',
      enum: ['same', 'one-up'],
      description: 'Reviewer tier selection; defaults to one-up when omitted.',
    });
    expect(advisorReviewToolDefinition.annotations).toEqual({
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: true,
    });
  });
});


describe('resolveTrustedCodexRequesterModel', () => {
  it('reads only the provider-authenticated turn metadata model', () => {
    expect(resolveTrustedCodexRequesterModel({
      model: 'gpt-5.6-luna',
      'x-codex-turn-metadata': { model: 'gpt-5.6-sol' },
    })).toBe('gpt-5.6-sol');
  });

  it.each([undefined, {}, { 'x-codex-turn-metadata': {} }, { 'x-codex-turn-metadata': { model: '' } }])(
    'fails closed for missing or invalid trusted model: %#',
    (meta) => expect(resolveTrustedCodexRequesterModel(meta)).toBeNull(),
  );
});


describe('runCodexAdvisorReview', () => {
  it('maps requester once and launches exact fixed read-only argv with confined inputs', async () => {
    const h = harness();
    h.deps.trustedRequesterModel = 'gpt-5.6-luna';
    const packet = 'complete inline review packet';

    const result = await runCodexAdvisorReview(
      { packet, requester_model: 'gpt-5.6-luna' }, identity, session, runtime, h.deps,
    );
    await new Promise((resolve) => setImmediate(resolve));
    const body = payload(result);

    expect(result.isError).toBeUndefined();
    expect(body).toEqual(expect.objectContaining({
      success: true,
      report: 'review report',
      requester_model: 'gpt-5.6-luna',
      reviewer_model: 'gpt-5.6-terra',
      transport: 'codex-exec-read-only',
      attempt_count: 1,
      timed_out: false,
      plugin_version: '1.1.7+codex.test',
    }));
    expect(h.spawn).toHaveBeenCalledTimes(2);
    expect(h.spawn.mock.calls[0][0]).toBe(process.execPath);
    expect(h.spawn.mock.calls[0][1]).toEqual([
      '/installed/ironclaude/scripts/codex-runtime-preflight.mjs', '--mode', 'check',
      '--codex-path', '/trusted/bin/codex',
    ]);
    expect(h.spawn.mock.calls[1][0]).toBe('/trusted/bin/codex');
    expect(h.spawn.mock.calls[1][1]).toEqual([
      'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
      '--skip-git-repo-check', '-s', 'read-only', '-m', 'gpt-5.6-terra', '-',
    ]);
    const options = h.spawn.mock.calls[1][2];
    expect(options).toEqual(expect.objectContaining({
      cwd: '/private/tmp/ironclaude-advisor-fixed',
      shell: false,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: true,
    }));
    expect(options.env).toEqual(expect.objectContaining({
      PATH: '/trusted/bin:/usr/bin',
      HOME: '/Users/test',
      CODEX_HOME: '/Users/test/.codex',
      OPENAI_API_KEY: 'allowed-provider-credential',
    }));
    expect(options.env).not.toHaveProperty('NODE_OPTIONS');
    expect(options.env).not.toHaveProperty('EVIL');
    expect(h.inputs).toEqual(['', packet]);
    expect(h.rm).toHaveBeenCalledOnce();
  });

  it.each([
    ['gpt-5.6-luna', 'gpt-5.6-terra'],
    ['gpt-5.6-terra', 'gpt-5.6-sol'],
    ['gpt-5.6-sol', 'gpt-6-astra'],
    ['gpt-6-astra', 'gpt-6-astra'],
  ])('owns exact one-step mapping %s -> %s', async (requester, reviewer) => {
    const h = harness();
    h.deps.trustedRequesterModel = requester;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: requester }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reviewer_model).toBe(reviewer);
  });

  it.each([
    ['gpt-5.6-sol', 'same', 'gpt-5.6-sol'],
    ['gpt-5.6-sol', 'one-up', 'gpt-6-astra'],
    ['gpt-6-astra', 'one-up', 'gpt-6-astra'],
    ['gpt-5.6-sol', undefined, 'gpt-6-astra'],
  ])('selects %s reviewer for authenticated %s requester', async (requester, reviewTier, reviewer) => {
    const h = harness();
    h.deps.trustedRequesterModel = requester;
    const args: Record<string, unknown> = { packet: 'packet', requester_model: requester };
    if (reviewTier !== undefined) args.review_tier = reviewTier;

    const result = await runCodexAdvisorReview(args, identity, session, runtime, h.deps);

    expect(payload(result)).toEqual(expect.objectContaining({
      success: true,
      requester_model: requester,
      reviewer_model: reviewer,
      review_tier: reviewTier ?? 'one-up',
    }));
    expect(h.spawn.mock.calls[1][1]).toContain(reviewer);
  });

  it('launches Astra through exact fixed read-only argv for an authenticated Astra requester', async () => {
    const h = harness();
    h.deps.trustedRequesterModel = 'gpt-6-astra';

    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-6-astra' }, identity, session, runtime, h.deps,
    );

    expect(payload(result)).toEqual(expect.objectContaining({
      success: true,
      requester_model: 'gpt-6-astra',
      reviewer_model: 'gpt-6-astra',
    }));
    expect(h.spawn.mock.calls[1][1]).toEqual([
      'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
      '--skip-git-repo-check', '-s', 'read-only', '-m', 'gpt-6-astra', '-',
    ]);
  });

  it.each([
    [{ packet: '', requester_model: 'gpt-5.6-sol' }, 'packet-empty'],
    [{ packet: 'x'.repeat(MAX_PACKET_BYTES + 1), requester_model: 'gpt-5.6-sol' }, 'packet-too-large'],
    [{ packet: 'ok', requester_model: 'gpt-5.6-unknown' }, 'invalid-requester-model'],
    [{ packet: 'ok', requester_model: 'gpt-5.6-sol', review_tier: 'sideways' }, 'invalid-review-tier'],
    [{ packet: 'ok', requester_model: 'gpt-5.6-sol', cwd: '/repo' }, 'unexpected-input-field'],
    [{ packet: 'ok', requester_model: 'gpt-5.6-sol', argv: ['--danger'] }, 'unexpected-input-field'],
    [{ packet: 'ok', requester_model: 'gpt-5.6-sol', reviewer_model: 'gpt-5.6-sol' }, 'unexpected-input-field'],
  ])('rejects invalid or caller-controlled input %#', async (args, reason) => {
    const h = harness();
    const result = await runCodexAdvisorReview(args, identity, session, runtime, h.deps);
    expect(result.isError).toBe(true);
    expect(payload(result).reason).toBe(reason);
    expect(h.spawn).not.toHaveBeenCalled();
  });

  it('fails closed for non-root, non-Codex, PM-off, missing-session, or bad runtime identity', async () => {
    const cases: Array<[SessionIdentity, Session | undefined, RuntimeFingerprintCapture, string]> = [
      [{ ...identity, client: 'claude', source: 'ppid_file' }, session, runtime, 'trusted-codex-root-required'],
      [{ ...identity, invocationThreadId: 'child' }, session, runtime, 'trusted-codex-root-required'],
      [identity, undefined, runtime, 'session-not-found'],
      [identity, { ...session, professional_mode: 'off' }, runtime, 'professional-mode-required'],
      [identity, { ...session, terminal_session: 'other' }, runtime, 'session-identity-mismatch'],
      [identity, session, { ok: false, error: 'capture failed' }, 'runtime-fingerprint-invalid'],
      [identity, session, { ...runtime, runtime: { ...(runtime as { ok: true; runtime: typeof runtime extends { ok: true; runtime: infer T } ? T : never }).runtime, client: 'claude' } } as RuntimeFingerprintCapture, 'runtime-client-mismatch'],
    ];
    for (const [candidateIdentity, candidateSession, candidateRuntime, reason] of cases) {
      const h = harness();
      const result = await runCodexAdvisorReview(
        { packet: 'packet', requester_model: 'gpt-5.6-sol' },
        candidateIdentity,
        candidateSession,
        candidateRuntime,
        h.deps,
      );
      expect(payload(result).reason).toBe(reason);
      expect(h.spawn).not.toHaveBeenCalled();
    }
  });

  it('rejects a caller-asserted requester model that disagrees with trusted turn metadata', async () => {
    const h = harness();
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-luna' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'requester-model-mismatch',
      trusted_requester_model: 'gpt-5.6-sol',
    }));
    expect(h.spawn).not.toHaveBeenCalled();
  });

  it('rejects an Astra claim from a trusted Sol turn before spawn', async () => {
    const h = harness();
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-6-astra' }, identity, session, runtime, h.deps,
    );

    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'requester-model-mismatch',
      trusted_requester_model: 'gpt-5.6-sol',
    }));
    expect(h.spawn).not.toHaveBeenCalled();
  });

  it('fails closed before spawn where isolated process groups are unavailable', async () => {
    const h = harness();
    h.deps.platform = 'win32';
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reason).toBe('advisor-process-group-unsupported');
    expect(h.spawn).not.toHaveBeenCalled();
  });

  it('fails before reviewer dispatch when installed companion preflight is not healthy', async () => {
    const h = harness([{ code: 2, stdout: JSON.stringify({ status: 'repairable' }) }]);
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'codex-runtime-incomplete',
    }));
    expect(h.spawn).toHaveBeenCalledTimes(1);
  });

  it.each([
    [{ ...JSON.parse(healthyPreflight()), schema_version: 2 }, 'schema'],
    [{ ...JSON.parse(healthyPreflight()), mode: 'repair' }, 'mode'],
    [{ ...JSON.parse(healthyPreflight()), invoked_launcher: '/other/codex' }, 'launcher'],
  ])('rejects an invalid installed preflight contract: %s', async (preflightPayload) => {
    const h = harness([{ stdout: JSON.stringify(preflightPayload) }]);
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).reason).toBe('codex-runtime-incomplete');
    expect(h.spawn).toHaveBeenCalledTimes(1);
  });

  it('returns the last completed agent message and cleans the private cwd', async () => {
    const h = harness([
      { stdout: healthyPreflight() },
      { stdout: agentMessage('first') + '{"type":"item.completed","item":{"type":"reasoning","text":"ignore"}}\n' + agentMessage('last') },
    ]);
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result).report).toBe('last');
    expect(h.rm).toHaveBeenCalledWith('/private/tmp/ironclaude-advisor-fixed', { recursive: true, force: true });
  });

  it.each([
    [{ code: 7, stdout: agentMessage('partial'), stderr: 'X'.repeat(2000) }, 'reviewer-nonzero'],
    [{ code: 0, stdout: 'not-json\n' }, 'reviewer-output-invalid'],
  ])('bounds failure diagnostics for %#', async (reviewerFixture, reason) => {
    const h = harness([{ stdout: healthyPreflight() }, reviewerFixture]);
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    const body = payload(result);
    expect(body.reason).toBe(reason);
    expect(String(body.diagnostic).length).toBeLessThanOrEqual(512);
    expect(h.rm).toHaveBeenCalledOnce();
  });

  it('kills one timed-out reviewer attempt and still cleans up', async () => {
    const h = harness([{ stdout: healthyPreflight() }, { hang: true }]);
    h.deps.reviewTimeoutMs = 1;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'reviewer-timeout',
      attempt_count: 1,
      timed_out: true,
    }));
    expect(h.spawn).toHaveBeenCalledTimes(2);
    expect(h.children[1].killed).toBe(true);
    expect(h.rm).toHaveBeenCalledOnce();
  });

  it('force-settles a reviewer that ignores SIGTERM', async () => {
    const h = harness([{ stdout: healthyPreflight() }, { hang: true, ignoreSigterm: true }]);
    h.deps.reviewTimeoutMs = 1;
    h.deps.terminationGraceMs = 1;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'reviewer-timeout',
      attempt_count: 1,
      timed_out: true,
    }));
    expect(h.children[1].killSignals).toEqual(['SIGTERM', 'SIGKILL']);
    expect(h.rm).toHaveBeenCalledOnce();
  });

  it('force-settles output overflow when the reviewer ignores SIGTERM', async () => {
    const h = harness([
      { stdout: healthyPreflight() },
      { stdout: 'x'.repeat(MAX_STDOUT_BYTES + 1), hang: true, ignoreSigterm: true },
    ]);
    h.deps.terminationGraceMs = 1;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'reviewer-output-too-large',
      attempt_count: 1,
      timed_out: false,
    }));
    expect(h.children[1].killSignals).toEqual(['SIGTERM', 'SIGKILL']);
    expect(h.rm).toHaveBeenCalledOnce();
  });

  it('kills a real SIGTERM-resistant parent and grandchild process group', async () => {
    if (process.platform === 'win32') return;
    const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), 'ironclaude-process-tree-test-'));
    const heartbeat = path.join(fixtureRoot, 'grandchild-heartbeat');
    const deps: AdvisorReviewDeps = {
      spawn: nodeSpawn as AdvisorReviewDeps['spawn'],
      mkdtemp,
      rm,
      access,
      env: process.env,
      tmpdir: os.tmpdir(),
      now: Date.now,
      platform: process.platform,
      killProcessGroup: (pid, signal) => process.kill(-pid, signal),
      isProcessGroupAlive: (pid) => {
        try {
          process.kill(-pid, 0);
          return true;
        } catch (error) {
          return (error as NodeJS.ErrnoException).code !== 'ESRCH';
        }
      },
      terminationGraceMs: 100,
      trustedRequesterModel: null,
    };
    const grandchild = [
      'const fs=require("node:fs");',
      `const heartbeat=${JSON.stringify(heartbeat)};`,
      'fs.writeFileSync(heartbeat,"x");',
      'process.stdout.write("ready\\n");',
      'setInterval(()=>fs.appendFileSync(heartbeat,"x"),10);',
      'process.on("SIGTERM",()=>{});',
    ].join('');
    const parent = [
      'const {spawn}=require("node:child_process");',
      `const child=spawn(process.execPath,["-e",${JSON.stringify(grandchild)}],{stdio:["ignore","pipe","ignore"]});`,
      'child.stdout.once("data",()=>process.stdout.write(String(child.pid)+"\\n"));',
      'process.on("SIGTERM",()=>{});',
      'setInterval(()=>{},1000);',
    ].join('');
    try {
      const result = await runFixedProcess(
        process.execPath,
        ['-e', parent],
        process.cwd(),
        process.env,
        '',
        200,
        deps,
      );
      expect(result.timedOut).toBe(true);
      expect(result.spawnError).toBeNull();
      const grandchildPid = Number(result.stdout.trim());
      expect(Number.isInteger(grandchildPid)).toBe(true);
      const before = (await readFile(heartbeat)).byteLength;
      await new Promise((resolve) => setTimeout(resolve, 80));
      const after = (await readFile(heartbeat)).byteLength;
      expect(after).toBe(before);
    } finally {
      await rm(fixtureRoot, { recursive: true, force: true });
    }
  });

  it('fails closed when private cwd cleanup fails', async () => {
    const h = harness();
    h.deps.rm = vi.fn(async () => { throw new Error('cleanup denied'); });
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'advisor-cleanup-failed',
    }));
  });
});
