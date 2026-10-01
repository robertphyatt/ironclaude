#!/usr/bin/env node
// Validates <pluginDir>/.codex-plugin/plugin.json and every file it references before
// `codex plugin add` installs the plugin. Reports every failed check, not just the first.

import { readFile, readdir, realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STAMPED_VERSION_RE = /^\d+\.\d+\.\d+\+codex\.\d{14}$/;
const STATE_MANAGER_BUNDLE = 'mcp-servers/state-manager/dist/index.js';


function resolveInside(root, relative) {
  if (typeof relative !== 'string' || relative.length === 0 || path.isAbsolute(relative)) return null;
  const resolved = path.resolve(root, relative);
  const fromRoot = path.relative(root, resolved);
  if (fromRoot === '..' || fromRoot.startsWith(`..${path.sep}`) || path.isAbsolute(fromRoot)) return null;
  return resolved;
}


async function kind(target) {
  try {
    const value = await stat(target);
    if (value.isFile()) return 'file';
    return value.isDirectory() ? 'directory' : 'other';
  } catch {
    return 'missing';
  }
}


async function countSkills(skillsDir) {
  let found = 0;
  for (const entry of await readdir(skillsDir, { withFileTypes: true })) {
    if (entry.isDirectory() && (await kind(path.join(skillsDir, entry.name, 'SKILL.md'))) === 'file') {
      found += 1;
    }
  }
  return found;
}


export async function validatePlugin(pluginDir) {
  const root = path.resolve(pluginDir);
  let manifest;
  try {
    manifest = JSON.parse(await readFile(path.join(root, '.codex-plugin', 'plugin.json'), 'utf8'));
  } catch (error) {
    return { valid: false, errors: [`manifest unreadable: ${error.message}`] };
  }
  const errors = [];

  if (manifest?.name !== 'ironclaude') {
    errors.push(`name must be "ironclaude", got ${JSON.stringify(manifest?.name)}`);
  }
  if (typeof manifest?.version !== 'string' || !STAMPED_VERSION_RE.test(manifest.version)) {
    errors.push(`version must be X.Y.Z+codex.<14 digits>, got ${JSON.stringify(manifest?.version)}`);
  }

  const skillsDir = resolveInside(root, manifest?.skills);
  if (!skillsDir) {
    errors.push(`skills must be a relative path inside the plugin, got ${JSON.stringify(manifest?.skills)}`);
  } else if ((await kind(skillsDir)) !== 'directory') {
    errors.push(`skills directory missing: ${manifest.skills}`);
  } else if ((await countSkills(skillsDir)) === 0) {
    errors.push(`skills directory has no */SKILL.md: ${manifest.skills}`);
  }

  const servers = manifest?.mcpServers;
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
    errors.push('mcpServers must be an object');
  } else {
    for (const [name, server] of Object.entries(servers)) {
      const script = Array.isArray(server?.args) ? server.args[0] : undefined;
      const resolved = resolveInside(root, script);
      if (!resolved) {
        errors.push(`mcpServers.${name}.args[0] must be a relative path inside the plugin, got ${JSON.stringify(script)}`);
      } else if ((await kind(resolved)) !== 'file') {
        errors.push(`mcpServers.${name}.args[0] missing: ${script}`);
      }
    }
  }

  if ((await kind(path.join(root, STATE_MANAGER_BUNDLE))) !== 'file') {
    errors.push(`built bundle missing: ${STATE_MANAGER_BUNDLE}`);
  }
  return { valid: errors.length === 0, errors };
}


export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 1) {
    process.stdout.write(`${JSON.stringify({
      valid: false,
      errors: ['usage: validate-codex-plugin.mjs <pluginDir>'],
    })}\n`);
    return 1;
  }
  const result = await validatePlugin(argv[0]);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  return result.valid ? 0 : 1;
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
