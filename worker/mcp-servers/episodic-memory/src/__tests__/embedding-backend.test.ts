import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@xenova/transformers', () => ({
  pipeline: vi.fn(async () => {
    return async () => ({ data: new Float32Array(384) });
  }),
}));

describe('embedding backend selection', () => {
  const originalEnv = process.env.IC_EMBEDDING_BACKEND;
  const originalFetch = global.fetch;

  beforeEach(() => {
    delete process.env.IC_EMBEDDING_BACKEND;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.IC_EMBEDDING_BACKEND;
    } else {
      process.env.IC_EMBEDDING_BACKEND = originalEnv;
    }
    global.fetch = originalFetch;
    vi.resetModules();
  });

  it('defaults to local when IC_EMBEDDING_BACKEND is unset', async () => {
    delete process.env.IC_EMBEDDING_BACKEND;
    const { getEmbeddingBackend } = await import('../embeddings.js');
    expect(getEmbeddingBackend()).toBe('local');
  });

  it('falls back to local when ollama backend fails', async () => {
    process.env.IC_EMBEDDING_BACKEND = 'ollama';
    global.fetch = vi.fn().mockRejectedValue(new Error('boom'));

    const { generateEmbedding } = await import('../embeddings.js');
    const result = await generateEmbedding('hi');

    expect(global.fetch).toHaveBeenCalled();
    expect(result.length).toBe(384);
  });

  it('returns the remote vector when ollama backend succeeds', async () => {
    process.env.IC_EMBEDDING_BACKEND = 'ollama';
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ embedding: new Array(384).fill(0.5) }),
    });

    const { generateEmbedding } = await import('../embeddings.js');
    const result = await generateEmbedding('hi');

    expect(result).toEqual(new Array(384).fill(0.5));
    expect(result.length).toBe(384);
  });
});
