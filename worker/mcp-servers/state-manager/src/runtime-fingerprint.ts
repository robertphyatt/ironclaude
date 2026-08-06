import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { IronClaudeClient } from './session-identity.js';

const MANIFEST_BY_CLIENT: Record<IronClaudeClient, string> = {
  codex: '.codex-plugin/plugin.json',
  claude: '.claude-plugin/plugin.json',
};

export type RuntimeFingerprint = {
  plugin_name: string;
  plugin_version: string;
  plugin_root: string;
  manifest_path: string;
  manifest_sha256: string;
  state_manager_bundle_path: string;
  state_manager_bundle_sha256: string;
  workspace_manager_bundle_path: string;
  workspace_manager_bundle_sha256: string;
  workspace_manager_cli_path: string;
  workspace_manager_cli_sha256: string;
  workspace_manager_hook_intent_path: string;
  workspace_manager_hook_intent_sha256: string;
  client: IronClaudeClient;
};

export type RuntimeFingerprintCapture =
  | { ok: true; runtime: RuntimeFingerprint }
  | { ok: false; error: string };

export type RuntimeFingerprintExpectation = Pick<
  RuntimeFingerprint,
  | 'plugin_version'
  | 'plugin_root'
  | 'manifest_sha256'
  | 'state_manager_bundle_sha256'
  | 'workspace_manager_bundle_sha256'
  | 'workspace_manager_cli_sha256'
  | 'workspace_manager_hook_intent_sha256'
  | 'client'
>;

export type RuntimeActivationVerification =
  | { ok: true }
  | { ok: false; errors: string[] };

function sha256(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex');
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function assertPathUnderPluginRoot(pluginRoot: string, artifactPath: string): void {
  const relative = path.relative(pluginRoot, artifactPath);
  if (relative === '' || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`Runtime artifact ${artifactPath} is not under plugin root ${pluginRoot}`);
  }
}

function readRuntimeArtifact(label: string, artifactPath: string): Buffer {
  try {
    return readFileSync(artifactPath);
  } catch (error) {
    throw new Error(`Could not read runtime ${label} ${artifactPath}: ${errorMessage(error)}`);
  }
}

export function captureRuntimeFingerprintFromPaths(
  pluginRoot: string,
  bundlePath: string,
  client: IronClaudeClient,
): RuntimeFingerprintCapture {
  const resolvedRoot = path.resolve(pluginRoot);
  const stateManagerBundlePath = path.resolve(bundlePath);
  const manifestPath = path.join(resolvedRoot, MANIFEST_BY_CLIENT[client]);
  const workspaceManagerBundlePath = path.join(resolvedRoot, 'mcp-servers', 'workspace-manager', 'dist', 'index.js');
  const workspaceManagerCliPath = path.join(resolvedRoot, 'mcp-servers', 'workspace-manager', 'dist', 'cli.js');
  const workspaceManagerHookIntentPath = path.join(
    resolvedRoot,
    'mcp-servers',
    'workspace-manager',
    'dist',
    'hook-intent.js',
  );

  try {
    const artifactPaths = [
      manifestPath,
      stateManagerBundlePath,
      workspaceManagerBundlePath,
      workspaceManagerCliPath,
      workspaceManagerHookIntentPath,
    ];
    for (const artifactPath of artifactPaths) assertPathUnderPluginRoot(resolvedRoot, artifactPath);

    const manifestBytes = readRuntimeArtifact('manifest', manifestPath);
    const stateManagerBundleBytes = readRuntimeArtifact('state-manager bundle', stateManagerBundlePath);
    const workspaceManagerBundleBytes = readRuntimeArtifact('workspace-manager bundle', workspaceManagerBundlePath);
    const workspaceManagerCliBytes = readRuntimeArtifact('workspace-manager CLI', workspaceManagerCliPath);
    const workspaceManagerHookIntentBytes = readRuntimeArtifact(
      'workspace-manager hook intent',
      workspaceManagerHookIntentPath,
    );
    let manifest: unknown;
    try {
      manifest = JSON.parse(manifestBytes.toString('utf8'));
    } catch (error) {
      throw new Error(`Could not parse runtime manifest ${manifestPath}: ${errorMessage(error)}`);
    }
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
      throw new Error(`Runtime manifest ${manifestPath} must contain a JSON object`);
    }
    const record = manifest as Record<string, unknown>;
    if (record.name !== 'ironclaude') {
      throw new Error(`Runtime manifest ${manifestPath} must have name "ironclaude"`);
    }
    if (typeof record.version !== 'string' || record.version.trim().length === 0) {
      throw new Error(`Runtime manifest ${manifestPath} must have a non-empty string version`);
    }

    return {
      ok: true,
      runtime: Object.freeze({
        plugin_name: record.name,
        plugin_version: record.version,
        plugin_root: resolvedRoot,
        manifest_path: manifestPath,
        manifest_sha256: sha256(manifestBytes),
        state_manager_bundle_path: stateManagerBundlePath,
        state_manager_bundle_sha256: sha256(stateManagerBundleBytes),
        workspace_manager_bundle_path: workspaceManagerBundlePath,
        workspace_manager_bundle_sha256: sha256(workspaceManagerBundleBytes),
        workspace_manager_cli_path: workspaceManagerCliPath,
        workspace_manager_cli_sha256: sha256(workspaceManagerCliBytes),
        workspace_manager_hook_intent_path: workspaceManagerHookIntentPath,
        workspace_manager_hook_intent_sha256: sha256(workspaceManagerHookIntentBytes),
        client,
      }),
    };
  } catch (error) {
    return {
      ok: false,
      error: `Runtime fingerprint capture failed under plugin root ${resolvedRoot}: ${errorMessage(error)}`,
    };
  }
}

export function captureRuntimeFingerprint(
  moduleUrl: string,
  client: IronClaudeClient,
): RuntimeFingerprintCapture {
  try {
    const bundlePath = fileURLToPath(moduleUrl);
    const pluginRoot = path.resolve(path.dirname(bundlePath), '..', '..', '..');
    return captureRuntimeFingerprintFromPaths(pluginRoot, bundlePath, client);
  } catch (error) {
    return { ok: false, error: `Runtime fingerprint module URL is invalid: ${errorMessage(error)}` };
  }
}

export function verifyRuntimeActivation(
  capture: RuntimeFingerprintCapture | undefined,
  expected: RuntimeFingerprintExpectation,
): RuntimeActivationVerification {
  if (!capture) {
    return { ok: false, errors: ['Runtime fingerprint capture is missing'] };
  }
  if (!capture.ok) {
    return { ok: false, errors: [`Runtime fingerprint capture failed: ${capture.error}`] };
  }

  const fields: Array<keyof RuntimeFingerprintExpectation> = [
    'plugin_version',
    'plugin_root',
    'manifest_sha256',
    'state_manager_bundle_sha256',
    'workspace_manager_bundle_sha256',
    'workspace_manager_cli_sha256',
    'workspace_manager_hook_intent_sha256',
    'client',
  ];
  const errors = fields.flatMap((field) => {
    const actual = capture.runtime[field];
    const wanted = expected?.[field];
    return actual === wanted
      ? []
      : [`${field} mismatch: expected=${String(wanted)}, actual=${String(actual)}`];
  });
  return errors.length === 0 ? { ok: true } : { ok: false, errors };
}
