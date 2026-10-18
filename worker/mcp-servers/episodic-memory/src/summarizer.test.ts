import { describe, it, expect, afterEach, vi } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const RESOLUTION_CASES_PATH = path.join(here, '../../../config-schema/resolution-cases.json');
const resolutionCases = JSON.parse(fs.readFileSync(RESOLUTION_CASES_PATH, 'utf-8')) as Array<{
  name: string;
  config: unknown;
  spot: string;
  expect: { backend: string; model: string; url: string };
}>;

let tmpDir: string | undefined;
let originalFetch: typeof global.fetch | undefined;

afterEach(() => {
  if (tmpDir) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = undefined;
  }
  delete process.env.IC_OLLAMA_CONFIG_PATH;
  if (originalFetch) {
    global.fetch = originalFetch;
    originalFetch = undefined;
  }
  vi.restoreAllMocks();
});

function stubFetch(): ReturnType<typeof vi.fn> {
  originalFetch = global.fetch;
  const mockFetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ choices: [{ message: { content: '{"summary":"x"}' } }] })
  });
  global.fetch = mockFetch as unknown as typeof global.fetch;
  return mockFetch;
}

function writeIcOllamaConfig(config: object): string {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'summarizer-test-'));
  const configPath = path.join(tmpDir, 'ironclaude-hooks-config.json');
  fs.writeFileSync(configPath, JSON.stringify(config), 'utf-8');
  process.env.IC_OLLAMA_CONFIG_PATH = configPath;
  return configPath;
}

describe('callOpenAi', () => {
  it('POSTs to the openai-compatible chat/completions endpoint with a bearer token and returns the parsed summary', async () => {
    const { callOpenAi } = await import('./summarizer.js');
    const mockFetch = stubFetch();

    const config = {
      backend: 'openai' as const,
      openai: { base_url: 'http://llm-host/v1', model: 'example-model-a', max_tokens: 1024 }
    };

    const result = await callOpenAi('summarize this', config as any);

    expect(result).toBe('x');
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe('http://llm-host/v1/chat/completions');
    expect(options.headers['Authorization']).toMatch(/^Bearer /);
  });
});

describe('callClaude dispatch (via IC_OLLAMA_CONFIG_PATH)', () => {
  it('honors IC_OLLAMA_CONFIG_PATH and dispatches the summarizer spot to callOpenAi when backend resolves to openai', async () => {
    writeIcOllamaConfig({
      backend: 'openai',
      openai: { base_url: 'http://llm-host/v1', model: 'example-model-a', max_tokens: 1024 }
    });
    const mockFetch = stubFetch();

    const { callClaude } = await import('./summarizer.js');
    const result = await callClaude('summarize this');

    expect(result).toBe('x');
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe('http://llm-host/v1/chat/completions');
  });
});

describe('spots.summarizer.model override honored on the wire', () => {
  it('passes the resolved spot model to callOpenAi so it is sent on the wire', async () => {
    writeIcOllamaConfig({
      backend: 'openai',
      openai: { base_url: 'http://h/v1', model: 'a' },
      spots: { summarizer: { model: 'b' } }
    });
    const mockFetch = stubFetch();

    const { callClaude } = await import('./summarizer.js');
    await callClaude('summarize this');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, options] = mockFetch.mock.calls[0];
    const body = JSON.parse(options.body);
    expect(body.model).toBe('b');
  });

  it('passes the resolved spot model to callOllama so it is sent on the wire', async () => {
    writeIcOllamaConfig({
      validation_backend: 'ollama',
      ollama: { url: 'http://o', model: 'a' },
      spots: { summarizer: { model: 'b' } }
    });
    const mockFetch = stubFetch();

    const { callClaude } = await import('./summarizer.js');
    await callClaude('summarize this');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, options] = mockFetch.mock.calls[0];
    const body = JSON.parse(options.body);
    expect(body.model).toBe('b');
  });

  it('keeps the llama3.2:1b default for callOllama when no model is configured anywhere', async () => {
    writeIcOllamaConfig({
      validation_backend: 'ollama',
      ollama: { url: 'http://o' }
    });
    const mockFetch = stubFetch();

    const { callClaude } = await import('./summarizer.js');
    await callClaude('summarize this');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, options] = mockFetch.mock.calls[0];
    const body = JSON.parse(options.body);
    expect(body.model).toBe('llama3.2:1b');
  });
});

describe('resolveBackend against the shared resolution-cases.json fixture', () => {
  it.each(resolutionCases)('$name', async (testCase) => {
    const { resolveBackend } = await import('./summarizer.js');
    const result = resolveBackend(testCase.config as any, testCase.spot as any);
    expect(result.backend).toBe(testCase.expect.backend);
    expect(result.model).toBe(testCase.expect.model);
    expect(result.url).toBe(testCase.expect.url);
  });

  it('defaults the summarizer spot to the SDK path (not ollama, not openai) when config is empty', async () => {
    const { resolveBackend } = await import('./summarizer.js');
    const result = resolveBackend({}, 'summarizer');
    expect(result.backend).not.toBe('ollama');
    expect(result.backend).not.toBe('openai');
  });
});

describe('callClaude fresh calls do not persist sessions', () => {
  afterEach(() => {
    delete process.env.EPISODIC_MEMORY_CLAUDE_PATH;
    vi.doUnmock('@anthropic-ai/claude-agent-sdk');
  });

  function mockQuery(seen: any[]) {
    vi.doMock('@anthropic-ai/claude-agent-sdk', () => ({
      query: ({ options }: any) => {
        seen.push(options);
        return (async function* () { yield { type: 'result', result: '<summary>ok</summary>' }; })();
      },
    }));
  }

  it('runs the system claude with --no-session-persistence on a fresh call, not on resume', async () => {
    vi.resetModules();
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'summ-claude-'));
    const fakeClaude = path.join(dir, 'claude');
    fs.writeFileSync(fakeClaude, '#!/bin/sh\nexit 0\n');
    fs.chmodSync(fakeClaude, 0o755);
    process.env.EPISODIC_MEMORY_CLAUDE_PATH = fakeClaude;
    const seen: any[] = [];
    mockQuery(seen);
    writeIcOllamaConfig({});
    const { callClaude } = await import('./summarizer.js');
    await callClaude('fresh');
    await callClaude('resumed', 'session-1');
    expect(seen[0].pathToClaudeCodeExecutable).toBe(fakeClaude);
    expect(seen[0].extraArgs).toEqual({ 'no-session-persistence': null });
    expect('extraArgs' in seen[1]).toBe(false);
    expect('pathToClaudeCodeExecutable' in seen[1]).toBe(false);
  });

  it('keeps the bundled CLI and warns when no system claude is found', async () => {
    vi.resetModules();
    process.env.EPISODIC_MEMORY_CLAUDE_PATH = path.join(os.tmpdir(), 'no-such-claude-binary');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const seen: any[] = [];
    mockQuery(seen);
    writeIcOllamaConfig({});
    const { callClaude } = await import('./summarizer.js');
    await callClaude('fresh');
    expect('extraArgs' in seen[0]).toBe(false);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('no system claude'));
    warn.mockRestore();
  });
});
