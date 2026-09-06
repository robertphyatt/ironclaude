#!/usr/bin/env node

import { access, lstat, realpath, stat, symlink } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';


export const SCHEMA_VERSION = 1;
export const EXIT = Object.freeze({ ok: 0, repairable: 2, blocked: 3 });
const COMPANION = 'codex-code-mode-host';


async function pathKind(candidate) {
  try {
    const value = await lstat(candidate);
    if (value.isSymbolicLink()) return 'symlink';
    if (value.isFile()) return 'file';
    if (value.isDirectory()) return 'directory';
    return 'other';
  } catch (error) {
    if (error?.code === 'ENOENT' || error?.code === 'ENOTDIR') return 'missing';
    throw error;
  }
}


async function isExecutableRegularFile(candidate) {
  try {
    const value = await stat(candidate);
    if (!value.isFile()) return false;
    await access(candidate, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}


async function sameFile(left, right) {
  try {
    const [leftStat, rightStat] = await Promise.all([stat(left), stat(right)]);
    return leftStat.dev === rightStat.dev && leftStat.ino === rightStat.ino;
  } catch {
    return false;
  }
}


function baseObservation(mode, invokedLauncher = '', resolvedLauncher = '') {
  const sourceCompanion = resolvedLauncher
    ? path.join(path.dirname(resolvedLauncher), COMPANION)
    : '';
  const destinationCompanion = invokedLauncher
    ? path.join(path.dirname(invokedLauncher), COMPANION)
    : '';
  return {
    schema_version: SCHEMA_VERSION,
    mode,
    status: 'blocked',
    invoked_launcher: invokedLauncher,
    resolved_launcher: resolvedLauncher,
    source_companion: sourceCompanion,
    destination_companion: destinationCompanion,
    action: 'none',
    reason: 'unclassified',
  };
}


export async function resolveCodexPath(options = {}) {
  const explicit = options.codexPath;
  if (explicit !== undefined) {
    if (typeof explicit !== 'string' || !path.isAbsolute(explicit)) {
      return { error: 'invalid-codex-path', invokedLauncher: '' };
    }
    return { invokedLauncher: path.normalize(explicit) };
  }

  const searchPath = options.pathEnv ?? process.env.PATH ?? '';
  for (const directory of searchPath.split(path.delimiter)) {
    if (!directory) continue;
    const candidate = path.resolve(directory, 'codex');
    try {
      await access(candidate, constants.X_OK);
      const kind = await pathKind(candidate);
      if (kind === 'file' || kind === 'symlink') {
        return { invokedLauncher: candidate };
      }
    } catch {
      // Continue to the next PATH entry.
    }
  }
  return { error: 'launcher-not-found', invokedLauncher: '' };
}


export async function inspectRuntime(options = {}) {
  const mode = options.mode ?? 'check';
  const resolved = await resolveCodexPath(options);
  if (resolved.error) {
    return { ...baseObservation(mode, resolved.invokedLauncher), reason: resolved.error };
  }

  const invokedLauncher = resolved.invokedLauncher;
  let resolvedLauncher = '';
  try {
    resolvedLauncher = await realpath(invokedLauncher);
  } catch (error) {
    const reason = error?.code === 'ENOENT' ? 'launcher-not-found' : 'launcher-unresolvable';
    return { ...baseObservation(mode, invokedLauncher), reason };
  }

  const observation = baseObservation(mode, invokedLauncher, resolvedLauncher);
  if (!(await isExecutableRegularFile(resolvedLauncher))) {
    return { ...observation, reason: 'launcher-not-executable' };
  }

  const sourceKind = await pathKind(observation.source_companion);
  if (sourceKind === 'missing') return { ...observation, reason: 'source-missing' };
  if (sourceKind === 'directory') return { ...observation, reason: 'source-directory' };
  if (sourceKind !== 'file' && sourceKind !== 'symlink') {
    return { ...observation, reason: 'source-non-regular' };
  }
  if (!(await isExecutableRegularFile(observation.source_companion))) {
    return { ...observation, reason: 'source-non-executable' };
  }

  if (path.normalize(observation.source_companion) === path.normalize(observation.destination_companion)) {
    return { ...observation, status: 'healthy', reason: 'source-is-destination' };
  }

  const destinationKind = await pathKind(observation.destination_companion);
  if (destinationKind === 'missing') {
    return { ...observation, status: 'repairable', reason: 'destination-missing' };
  }
  if (await sameFile(observation.source_companion, observation.destination_companion)) {
    return { ...observation, status: 'healthy', reason: 'destination-equivalent' };
  }
  return { ...observation, reason: 'destination-conflict' };
}


export async function repairRuntime(observation, deps = {}) {
  if (observation.status !== 'repairable') return observation;
  if (process.platform === 'win32') {
    return { ...observation, status: 'blocked', reason: 'repair-unsupported-platform' };
  }

  const createSymlink = deps.symlink ?? symlink;
  let created = false;
  try {
    await createSymlink(observation.source_companion, observation.destination_companion);
    created = true;
  } catch (error) {
    if (error?.code !== 'EEXIST') {
      return { ...observation, status: 'blocked', reason: 'repair-create-failed' };
    }
  }

  const after = await inspectRuntime({
    mode: observation.mode,
    codexPath: observation.invoked_launcher,
  });
  if (after.status !== 'healthy') return after;
  if (!created) return after;
  if (after.reason === 'source-is-destination') return after;
  return {
    ...after,
    status: 'repaired',
    action: 'create-symlink',
    reason: 'created-equivalent-symlink',
  };
}


export function renderResult(observation, mode = observation.mode) {
  return {
    schema_version: SCHEMA_VERSION,
    mode,
    status: observation.status,
    invoked_launcher: observation.invoked_launcher,
    resolved_launcher: observation.resolved_launcher,
    source_companion: observation.source_companion,
    destination_companion: observation.destination_companion,
    action: observation.action,
    reason: observation.reason,
  };
}


function parseArgs(argv) {
  let mode;
  let codexPath;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--mode' && index + 1 < argv.length) {
      mode = argv[++index];
    } else if (argument === '--codex-path' && index + 1 < argv.length) {
      codexPath = argv[++index];
    } else {
      return { error: 'invalid-arguments', mode: mode ?? 'check' };
    }
  }
  if (mode !== 'check' && mode !== 'repair') {
    return { error: 'invalid-arguments', mode: 'check' };
  }
  return { mode, codexPath };
}


export async function main(argv = process.argv.slice(2), deps = {}) {
  const parsed = parseArgs(argv);
  let observation;
  if (parsed.error) {
    observation = { ...baseObservation(parsed.mode), reason: parsed.error };
  } else {
    const inspect = deps.inspectRuntime ?? inspectRuntime;
    const repair = deps.repairRuntime ?? repairRuntime;
    try {
      observation = await inspect({
        mode: parsed.mode,
        codexPath: parsed.codexPath,
      });
      if (parsed.mode === 'repair') observation = await repair(observation);
    } catch {
      observation = {
        ...baseObservation(parsed.mode, parsed.codexPath ?? ''),
        reason: 'unexpected-filesystem-error',
      };
    }
  }

  const result = renderResult(observation, parsed.mode);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.status === 'healthy' || result.status === 'repaired') return EXIT.ok;
  if (result.status === 'repairable') return EXIT.repairable;
  process.stderr.write(`codex-runtime-preflight: ${result.reason}\n`);
  return EXIT.blocked;
}


async function isDirectEntry(argvEntry = process.argv[1]) {
  if (!argvEntry) return false;
  try {
    const physicalEntry = await realpath(path.resolve(argvEntry));
    return pathToFileURL(physicalEntry).href === import.meta.url;
  } catch {
    return false;
  }
}


if (await isDirectEntry()) {
  process.exitCode = await main();
}
