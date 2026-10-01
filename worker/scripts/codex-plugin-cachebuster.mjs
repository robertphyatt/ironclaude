#!/usr/bin/env node
// Stamps <pluginDir>/.codex-plugin/plugin.json with a fresh +codex.<UTC YYYYMMDDHHMMSS>
// cachebuster so Codex installs every release build as a new plugin version. Only the
// version text changes; the rest of the file is left byte-for-byte as it was.

import { readFile, realpath, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const VERSION_RE = /^(\d+\.\d+\.\d+)(\+codex\.\d{14})?$/;
const STAMP_RE = /^\d{14}$/;
const USAGE = 'usage: codex-plugin-cachebuster.mjs <pluginDir> [--stamp YYYYMMDDHHMMSS]';


export function utcStamp(date = new Date()) {
  return date.toISOString().replace(/[-:T]/g, '').slice(0, 14);
}


export async function stampPlugin(pluginDir, stamp = utcStamp()) {
  if (!STAMP_RE.test(stamp)) throw new Error(`invalid stamp: ${stamp}`);
  const manifestPath = path.join(pluginDir, '.codex-plugin', 'plugin.json');
  const text = await readFile(manifestPath, 'utf8');
  const previous = JSON.parse(text).version;
  const match = typeof previous === 'string' ? VERSION_RE.exec(previous) : null;
  if (!match) throw new Error(`invalid version: ${JSON.stringify(previous)}`);
  const needle = `"version": ${JSON.stringify(previous)}`;
  const first = text.indexOf(needle);
  if (first === -1 || text.indexOf(needle, first + 1) !== -1) {
    throw new Error(`expected exactly one ${needle} in ${manifestPath}`);
  }
  const version = `${match[1]}+codex.${stamp}`;
  await writeFile(manifestPath, text.replace(needle, `"version": ${JSON.stringify(version)}`));
  return { previous, version };
}


function parseArgs(argv) {
  const [pluginDir, ...rest] = argv;
  if (!pluginDir || pluginDir.startsWith('--')) return null;
  if (rest.length === 0) return { pluginDir };
  if (rest.length === 2 && rest[0] === '--stamp') return { pluginDir, stamp: rest[1] };
  return null;
}


export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  if (!args) {
    process.stderr.write(`${JSON.stringify({ error: USAGE })}\n`);
    return 1;
  }
  try {
    const result = await stampPlugin(args.pluginDir, args.stamp);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return 0;
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ error: error.message })}\n`);
    return 1;
  }
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
