import { access as fsAccess, mkdtemp as fsMkdtemp, rm as fsRm } from 'node:fs/promises';
import { constants } from 'node:fs';
import { spawn as nodeSpawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import type { SessionIdentity } from '../session-identity.js';
import type { RuntimeFingerprintCapture } from '../runtime-fingerprint.js';
import type { Session } from '../types.js';


export const MAX_PACKET_BYTES = 1024 * 1024;
export const MAX_STDOUT_BYTES = 2 * 1024 * 1024;
const MAX_STDERR_BYTES = 64 * 1024;
const DEFAULT_REVIEW_TIMEOUT_MS = 15 * 60 * 1000;
const PREFLIGHT_TIMEOUT_MS = 15 * 1000;
const TERMINATION_GRACE_MS = 1000;
const DIAGNOSTIC_LIMIT = 512;
const REQUESTER_MODELS = [
  'gpt-5.6-luna',
  'gpt-5.6-terra',
  'gpt-5.6-sol',
  'gpt-6-astra',
] as const;
type RequesterModel = typeof REQUESTER_MODELS[number];

const REVIEW_TIERS = ['same', 'one-up'] as const;
type ReviewTier = typeof REVIEW_TIERS[number];

const REVIEWER_BY_REQUESTER: Record<RequesterModel, RequesterModel> = {
  'gpt-5.6-luna': 'gpt-5.6-terra',
  'gpt-5.6-terra': 'gpt-5.6-sol',
  'gpt-5.6-sol': 'gpt-6-astra',
  'gpt-6-astra': 'gpt-6-astra',
};

const ALLOWED_ENV = [
  'PATH',
  'HOME',
  'USER',
  'LOGNAME',
  'TMPDIR',
  'LANG',
  'LC_ALL',
  'TERM',
  'COLORTERM',
  'CODEX_HOME',
  'OPENAI_API_KEY',
  'SSL_CERT_FILE',
  'SSL_CERT_DIR',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'NO_PROXY',
  'http_proxy',
  'https_proxy',
  'no_proxy',
] as const;

type ToolResult = {
  content: Array<{ type: 'text'; text: string }>;
  isError?: boolean;
};

type SpawnFunction = (
  command: string,
  args: readonly string[],
  options: {
    cwd: string;
    env: NodeJS.ProcessEnv;
      shell: false;
      stdio: ['pipe', 'pipe', 'pipe'];
      detached: boolean;
  },
) => ChildProcessWithoutNullStreams;

export type AdvisorReviewDeps = {
  spawn: SpawnFunction;
  mkdtemp: (prefix: string) => Promise<string>;
  rm: (target: string, options: { recursive: true; force: true }) => Promise<void>;
  access: (target: string, mode: number) => Promise<void>;
  env: NodeJS.ProcessEnv;
  tmpdir: string;
  now: () => number;
  reviewTimeoutMs?: number;
  terminationGraceMs?: number;
  trustedRequesterModel?: string | null;
  platform: NodeJS.Platform;
  killProcessGroup: (pid: number, signal: NodeJS.Signals) => void;
  isProcessGroupAlive: (pid: number) => boolean;
};

type ProcessResult = {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  outputOverflow: boolean;
  spawnError: string | null;
};

const DEFAULT_DEPS: AdvisorReviewDeps = {
  spawn: nodeSpawn as SpawnFunction,
  mkdtemp: fsMkdtemp,
  rm: fsRm,
  access: fsAccess,
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
};


export const advisorReviewToolDefinition = {
  name: 'run_codex_advisor_review',
  description:
    'Run one bounded, fixed-function, report-only Codex advisor review using a complete inline packet. ' +
    'The broker owns reviewer-tier mapping and exposes no shell, argv, environment, cwd, or repository-path input.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      packet: {
        type: 'string' as const,
        minLength: 1,
        maxLength: MAX_PACKET_BYTES,
        description: 'Complete inline lossless review packet.',
      },
      requester_model: {
        type: 'string' as const,
        enum: [...REQUESTER_MODELS],
        description: 'Current Codex requester model; the broker maps the reviewer exactly once.',
      },
      review_tier: {
        type: 'string' as const,
        enum: [...REVIEW_TIERS],
        description: 'Reviewer tier selection; defaults to one-up when omitted.',
      },
    },
    required: ['packet', 'requester_model'],
    additionalProperties: false,
  },
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: false,
    openWorldHint: true,
  },
};


function result(body: Record<string, unknown>, isError = false): ToolResult {
  return {
    content: [{ type: 'text', text: JSON.stringify(body) }],
    ...(isError ? { isError: true } : {}),
  };
}


function failure(reason: string, extra: Record<string, unknown> = {}): ToolResult {
  return result({ success: false, reason, ...extra }, true);
}


function diagnostic(value: string): string {
  return value.replace(/\s+/g, ' ').trim().slice(-DIAGNOSTIC_LIMIT);
}


export function resolveTrustedCodexRequesterModel(requestMeta: unknown): string | null {
  if (!requestMeta || typeof requestMeta !== 'object' || Array.isArray(requestMeta)) return null;
  const turn = (requestMeta as Record<string, unknown>)['x-codex-turn-metadata'];
  if (!turn || typeof turn !== 'object' || Array.isArray(turn)) return null;
  const model = (turn as Record<string, unknown>).model;
  return typeof model === 'string' && model.length > 0 ? model : null;
}


function sanitizedEnv(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const clean: NodeJS.ProcessEnv = {};
  for (const key of ALLOWED_ENV) {
    const value = source[key];
    if (typeof value === 'string') clean[key] = value;
  }
  return clean;
}


async function resolveCodexExecutable(deps: AdvisorReviewDeps): Promise<string | null> {
  for (const directory of (deps.env.PATH ?? '').split(path.delimiter)) {
    if (!directory) continue;
    const candidate = path.resolve(directory, process.platform === 'win32' ? 'codex.exe' : 'codex');
    try {
      await deps.access(candidate, constants.X_OK);
      return candidate;
    } catch {
      // Continue to the next PATH entry.
    }
  }
  return null;
}


export async function runFixedProcess(
  executable: string,
  argv: readonly string[],
  cwd: string,
  env: NodeJS.ProcessEnv,
  input: string,
  timeoutMs: number,
  deps: AdvisorReviewDeps,
): Promise<ProcessResult> {
  return await new Promise((resolve) => {
    let child: ChildProcessWithoutNullStreams;
    try {
      child = deps.spawn(executable, argv, {
        cwd,
        env,
        shell: false,
        stdio: ['pipe', 'pipe', 'pipe'],
        detached: deps.platform !== 'win32',
      });
    } catch (error) {
      resolve({
        exitCode: null,
        stdout: '',
        stderr: '',
        timedOut: false,
        outputOverflow: false,
        spawnError: error instanceof Error ? error.message : String(error),
      });
      return;
    }

    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let timedOut = false;
    let outputOverflow = false;
    let settled = false;
    let forceTimer: NodeJS.Timeout | undefined;
    let pollTimer: NodeJS.Timeout | undefined;
    let verificationTimer: NodeJS.Timeout | undefined;
    let terminating = false;

    const finish = (exitCode: number | null, spawnError: string | null = null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (forceTimer) clearTimeout(forceTimer);
      if (pollTimer) clearTimeout(pollTimer);
      if (verificationTimer) clearTimeout(verificationTimer);
      resolve({
        exitCode,
        stdout: Buffer.concat(stdoutChunks, stdoutBytes).toString('utf8'),
        stderr: Buffer.concat(stderrChunks, stderrBytes).toString('utf8'),
        timedOut,
        outputOverflow,
        spawnError,
      });
    };

    const terminate = () => {
      if (settled || terminating) return;
      terminating = true;
      if (deps.platform === 'win32' || typeof child.pid !== 'number') {
        finish(null, 'bounded process-group termination unavailable');
        return;
      }
      const pid = child.pid;
      try {
        deps.killProcessGroup(pid, 'SIGTERM');
      } catch (error) {
        finish(null, `process-group SIGTERM failed: ${error instanceof Error ? error.message : String(error)}`);
        return;
      }
      const poll = () => {
        if (settled) return;
        if (!deps.isProcessGroupAlive(pid)) {
          finish(null);
          return;
        }
        pollTimer = setTimeout(poll, 10);
      };
      pollTimer = setTimeout(poll, 10);
      forceTimer = setTimeout(() => {
        if (settled) return;
        try {
          deps.killProcessGroup(pid, 'SIGKILL');
        } catch (error) {
          finish(null, `process-group SIGKILL failed: ${error instanceof Error ? error.message : String(error)}`);
          return;
        }
        verificationTimer = setTimeout(() => {
          if (settled) return;
          if (deps.isProcessGroupAlive(pid)) {
            finish(null, 'process-group survived SIGKILL');
          } else {
            finish(null);
          }
        }, deps.terminationGraceMs ?? TERMINATION_GRACE_MS);
      }, deps.terminationGraceMs ?? TERMINATION_GRACE_MS);
    };

    const timer = setTimeout(() => {
      timedOut = true;
      terminate();
    }, timeoutMs);

    child.stdout.on('data', (chunk: Buffer | string) => {
      if (outputOverflow) return;
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      const remaining = Math.max(0, MAX_STDOUT_BYTES - stdoutBytes);
      if (remaining > 0) {
        const retained = bytes.subarray(0, remaining);
        stdoutChunks.push(retained);
        stdoutBytes += retained.byteLength;
      }
      if (bytes.byteLength > remaining) {
        outputOverflow = true;
        terminate();
      }
    });
    child.stderr.on('data', (chunk: Buffer | string) => {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      const remaining = Math.max(0, MAX_STDERR_BYTES - stderrBytes);
      if (remaining > 0) {
        const retained = bytes.subarray(0, remaining);
        stderrChunks.push(retained);
        stderrBytes += retained.byteLength;
      }
    });
    child.once('error', (error) => finish(null, error.message));
    child.once('close', (code) => {
      if (!terminating) finish(code);
    });
    child.stdin.once('error', () => undefined);
    child.stdin.end(input);
  });
}


function parseLastAgentMessage(stdout: string): string | null {
  let report: string | null = null;
  for (const rawLine of stdout.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    let event: unknown;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (!event || typeof event !== 'object' || Array.isArray(event)) continue;
    const record = event as Record<string, unknown>;
    if (record.type !== 'item.completed') continue;
    const item = record.item;
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
    const itemRecord = item as Record<string, unknown>;
    if (itemRecord.type === 'agent_message' && typeof itemRecord.text === 'string' && itemRecord.text.trim()) {
      report = itemRecord.text;
    }
  }
  return report;
}


function validateArgs(args: Record<string, unknown>):
  | { ok: true; packet: string; requesterModel: RequesterModel; reviewTier: ReviewTier }
  | { ok: false; reason: string } {
  const keys = Object.keys(args);
  if (keys.some((key) => key !== 'packet' && key !== 'requester_model' && key !== 'review_tier')) {
    return { ok: false, reason: 'unexpected-input-field' };
  }
  if (typeof args.packet !== 'string' || args.packet.trim().length === 0) {
    return { ok: false, reason: 'packet-empty' };
  }
  if (Buffer.byteLength(args.packet, 'utf8') > MAX_PACKET_BYTES) {
    return { ok: false, reason: 'packet-too-large' };
  }
  if (!REQUESTER_MODELS.includes(args.requester_model as RequesterModel)) {
    return { ok: false, reason: 'invalid-requester-model' };
  }
  if (args.review_tier !== undefined && !REVIEW_TIERS.includes(args.review_tier as ReviewTier)) {
    return { ok: false, reason: 'invalid-review-tier' };
  }
  return {
    ok: true,
    packet: args.packet,
    requesterModel: args.requester_model as RequesterModel,
    reviewTier: (args.review_tier ?? 'one-up') as ReviewTier,
  };
}


export async function runCodexAdvisorReview(
  args: Record<string, unknown>,
  identity: SessionIdentity,
  session: Session | undefined,
  runtime: RuntimeFingerprintCapture | undefined,
  overrides: Partial<AdvisorReviewDeps> = {},
): Promise<ToolResult> {
  const deps: AdvisorReviewDeps = { ...DEFAULT_DEPS, ...overrides };
  const validated = validateArgs(args);
  if (!validated.ok) return failure(validated.reason);

  if (
    identity.client !== 'codex' ||
    identity.source !== 'codex_meta' ||
    identity.invocationThreadId !== identity.sessionId
  ) {
    return failure('trusted-codex-root-required');
  }
  if (!session) return failure('session-not-found');
  if (session.terminal_session !== identity.sessionId) return failure('session-identity-mismatch');
  if (session.professional_mode !== 'on') return failure('professional-mode-required');
  if (!runtime?.ok) return failure('runtime-fingerprint-invalid');
  if (runtime.runtime.client !== 'codex') return failure('runtime-client-mismatch');
  if (deps.platform === 'win32') return failure('advisor-process-group-unsupported');

  if (deps.trustedRequesterModel !== validated.requesterModel) {
    return failure('requester-model-mismatch', {
      trusted_requester_model: deps.trustedRequesterModel ?? null,
    });
  }

  const codexExecutable = await resolveCodexExecutable(deps);
  if (!codexExecutable) return failure('codex-executable-not-found');

  const helper = path.join(runtime.runtime.plugin_root, 'scripts', 'codex-runtime-preflight.mjs');
  const env = sanitizedEnv(deps.env);
  const preflight = await runFixedProcess(
    process.execPath,
    [helper, '--mode', 'check', '--codex-path', codexExecutable],
    runtime.runtime.plugin_root,
    env,
    '',
    PREFLIGHT_TIMEOUT_MS,
    deps,
  );
  let preflightPayload: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(preflight.stdout);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      preflightPayload = parsed as Record<string, unknown>;
    }
  } catch {
    // Failure below carries a bounded diagnostic.
  }
  const resolvedLauncher = preflightPayload?.resolved_launcher;
  const preflightValid =
    preflight.exitCode === 0 &&
    preflightPayload?.schema_version === 1 &&
    preflightPayload?.mode === 'check' &&
    preflightPayload?.status === 'healthy' &&
    preflightPayload?.invoked_launcher === codexExecutable &&
    typeof resolvedLauncher === 'string' &&
    resolvedLauncher.length > 0 &&
    preflightPayload?.source_companion === `${resolvedLauncher}-code-mode-host` &&
    preflightPayload?.destination_companion === `${codexExecutable}-code-mode-host` &&
    preflightPayload?.action === 'none' &&
    typeof preflightPayload?.reason === 'string';
  if (!preflightValid) {
    return failure('codex-runtime-incomplete', {
      diagnostic: diagnostic(preflight.stderr || preflight.stdout || preflight.spawnError || 'preflight failed'),
    });
  }

  const reviewerModel = validated.reviewTier === 'same'
    ? validated.requesterModel
    : REVIEWER_BY_REQUESTER[validated.requesterModel];
  const startedAt = deps.now();
  let privateCwd: string | null = null;
  let outcome: ToolResult;
  try {
    privateCwd = await deps.mkdtemp(path.join(deps.tmpdir, 'ironclaude-advisor-'));
    const review = await runFixedProcess(
      codexExecutable,
      [
        'exec',
        '--json',
        '--ephemeral',
        '--ignore-user-config',
        '--ignore-rules',
        '--skip-git-repo-check',
        '-s',
        'read-only',
        '-m',
        reviewerModel,
        '-',
      ],
      privateCwd,
      env,
      validated.packet,
      deps.reviewTimeoutMs ?? DEFAULT_REVIEW_TIMEOUT_MS,
      deps,
    );
    const provenance = {
      requester_model: validated.requesterModel,
      reviewer_model: reviewerModel,
      review_tier: validated.reviewTier,
      transport: 'codex-exec-read-only',
      attempt_count: 1,
      timed_out: review.timedOut,
      packet_bytes: Buffer.byteLength(validated.packet, 'utf8'),
      duration_ms: Math.max(0, deps.now() - startedAt),
      plugin_version: runtime.runtime.plugin_version,
      plugin_root: runtime.runtime.plugin_root,
    };
    if (review.timedOut) {
      outcome = failure('reviewer-timeout', provenance);
    } else if (review.outputOverflow) {
      outcome = failure('reviewer-output-too-large', provenance);
    } else if (review.spawnError) {
      outcome = failure('reviewer-spawn-failed', {
        ...provenance,
        diagnostic: diagnostic(review.spawnError),
      });
    } else if (review.exitCode !== 0) {
      outcome = failure('reviewer-nonzero', {
        ...provenance,
        diagnostic: diagnostic(review.stderr || review.stdout || `exit ${String(review.exitCode)}`),
      });
    } else {
      const report = parseLastAgentMessage(review.stdout);
      outcome = report
        ? result({ success: true, report, ...provenance })
        : failure('reviewer-output-invalid', {
        ...provenance,
        diagnostic: diagnostic(review.stderr || review.stdout || 'no completed agent_message'),
      });
    }
  } catch (error) {
    outcome = failure('advisor-broker-failed', {
      diagnostic: diagnostic(error instanceof Error ? error.message : String(error)),
    });
  }

  if (privateCwd) {
    try {
      await deps.rm(privateCwd, { recursive: true, force: true });
    } catch (error) {
      return failure('advisor-cleanup-failed', {
        diagnostic: diagnostic(error instanceof Error ? error.message : String(error)),
      });
    }
  }
  return outcome;
}
