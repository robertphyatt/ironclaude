import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import {
  captureRuntimeFingerprint,
  captureRuntimeFingerprintFromPaths,
  verifyRuntimeActivation,
  type RuntimeFingerprint,
  type RuntimeFingerprintCapture,
  type RuntimeFingerprintExpectation,
} from '../runtime-fingerprint.js';

const roots: string[] = [];

function sha256(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex');
}

function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'ironclaude-runtime-'));
  roots.push(root);
  const codexManifest = path.join(root, '.codex-plugin', 'plugin.json');
  const claudeManifest = path.join(root, '.claude-plugin', 'plugin.json');
  const bundle = path.join(root, 'mcp-servers', 'state-manager', 'dist', 'index.js');
  mkdirSync(path.dirname(codexManifest), { recursive: true });
  mkdirSync(path.dirname(claudeManifest), { recursive: true });
  mkdirSync(path.dirname(bundle), { recursive: true });
  const codexBytes = Buffer.from('{"name":"ironclaude","version":"1.1.0+codex.test"}\n');
  const claudeBytes = Buffer.from('{"name":"ironclaude","version":"1.1.0"}\n');
  const bundleBytes = Buffer.from('console.log("fixture-a");\n');
  writeFileSync(codexManifest, codexBytes);
  writeFileSync(claudeManifest, claudeBytes);
  writeFileSync(bundle, bundleBytes);
  return { root, codexManifest, claudeManifest, bundle, codexBytes, claudeBytes, bundleBytes };
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe('runtime fingerprint capture', () => {
  it.each([
    ['codex', '.codex-plugin/plugin.json', '1.1.0+codex.test'],
    ['claude', '.claude-plugin/plugin.json', '1.1.0'],
  ] as const)('selects only the %s provider manifest', (client, manifestSuffix, version) => {
    const files = fixture();
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, client);
    expect(capture.ok).toBe(true);
    if (!capture.ok) return;
    const manifestBytes = readFileSync(path.join(files.root, manifestSuffix));
    expect(capture.runtime).toEqual({
      plugin_name: 'ironclaude',
      plugin_version: version,
      plugin_root: files.root,
      manifest_path: path.join(files.root, manifestSuffix),
      manifest_sha256: sha256(manifestBytes),
      bundle_path: files.bundle,
      bundle_sha256: sha256(files.bundleBytes),
      client,
    });
  });

  it('derives plugin root and bundle from the loaded module URL', () => {
    const files = fixture();
    expect(captureRuntimeFingerprint(pathToFileURL(files.bundle).href, 'codex')).toEqual(
      captureRuntimeFingerprintFromPaths(files.root, files.bundle, 'codex'),
    );
  });

  it('keeps immutable fixture-A values after disk changes to fixture B', () => {
    const files = fixture();
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, 'codex');
    const snapshot = structuredClone(capture);
    writeFileSync(files.codexManifest, '{"name":"ironclaude","version":"1.1.0+codex.b"}\n');
    writeFileSync(files.bundle, 'console.log("fixture-b");\n');
    expect(capture).toEqual(snapshot);
  });

  it.each(['codex', 'claude'] as const)('does not fall back from a missing %s manifest', (client) => {
    const files = fixture();
    rmSync(client === 'codex' ? files.codexManifest : files.claudeManifest);
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, client);
    expect(capture).toEqual(expect.objectContaining({ ok: false }));
    if (!capture.ok) expect(capture.error).toContain(client === 'codex' ? '.codex-plugin' : '.claude-plugin');
  });

  it.each([
    ['malformed manifest', '{not-json', /parse|JSON/i],
    ['wrong plugin name', '{"name":"other","version":"1.1.0"}', /name/i],
    ['empty version', '{"name":"ironclaude","version":""}', /version/i],
  ])('fails for %s', (_name, contents, expected) => {
    const files = fixture();
    writeFileSync(files.codexManifest, contents);
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, 'codex');
    expect(capture).toEqual(expect.objectContaining({ ok: false }));
    if (!capture.ok) expect(capture.error).toMatch(expected as RegExp);
  });

  it('fails for a missing bundle', () => {
    const files = fixture();
    rmSync(files.bundle);
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, 'codex');
    expect(capture).toEqual(expect.objectContaining({ ok: false }));
    if (!capture.ok) expect(capture.error).toContain(files.bundle);
  });

  it('fails when the bundle path is a directory', () => {
    const files = fixture();
    rmSync(files.bundle);
    mkdirSync(files.bundle);
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, 'codex');
    expect(capture).toEqual(expect.objectContaining({ ok: false }));
    if (!capture.ok) expect(capture.error).toMatch(/bundle/i);
  });
});

describe('verifyRuntimeActivation', () => {
  function success(): Extract<RuntimeFingerprintCapture, { ok: true }> {
    const files = fixture();
    const capture = captureRuntimeFingerprintFromPaths(files.root, files.bundle, 'codex');
    if (!capture.ok) throw new Error(capture.error);
    return capture;
  }

  function expected(runtime: RuntimeFingerprint): RuntimeFingerprintExpectation {
    const { plugin_version, plugin_root, manifest_sha256, bundle_sha256, client } = runtime;
    return { plugin_version, plugin_root, manifest_sha256, bundle_sha256, client };
  }

  it('accepts an exact immutable startup match', () => {
    const capture = success();
    expect(verifyRuntimeActivation(capture, expected(capture.runtime))).toEqual({ ok: true });
  });

  it('rejects missing and failed captures', () => {
    const capture = success();
    const want = expected(capture.runtime);
    expect(verifyRuntimeActivation(undefined, want)).toEqual({ ok: false, errors: ['Runtime fingerprint capture is missing'] });
    expect(verifyRuntimeActivation({ ok: false, error: 'fixture failed' }, want)).toEqual({
      ok: false,
      errors: ['Runtime fingerprint capture failed: fixture failed'],
    });
  });

  it.each([
    ['plugin_version', 'stale'],
    ['plugin_root', '/stale'],
    ['manifest_sha256', '0'.repeat(64)],
    ['bundle_sha256', '1'.repeat(64)],
    ['client', 'claude'],
  ] as const)('rejects a mismatched %s', (field, value) => {
    const capture = success();
    const want = { ...expected(capture.runtime), [field]: value } as RuntimeFingerprintExpectation;
    const result = verifyRuntimeActivation(capture, want);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors).toEqual([expect.stringContaining(field)]);
  });
});
