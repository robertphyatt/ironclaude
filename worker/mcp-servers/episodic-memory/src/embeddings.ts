// NOTE: Flipping IC_EMBEDDING_BACKEND to 'ollama' requires a ONE-TIME re-index.
// Xenova and Ollama MiniLM vectors are the same family (384-dim) but not
// bit-identical; mixing vectors from both backends in the same index degrades
// similarity search. Re-indexing is an operator step, not automatic.

import { pipeline, Pipeline, FeatureExtractionPipeline } from '@xenova/transformers';

let embeddingPipeline: FeatureExtractionPipeline | null = null;

export function getEmbeddingBackend(): string {
  return process.env.IC_EMBEDDING_BACKEND ?? 'local';
}

export async function initEmbeddings(): Promise<void> {
  if (!embeddingPipeline) {
    console.log('Loading embedding model (first run may take time)...');
    embeddingPipeline = await pipeline(
      'feature-extraction',
      'Xenova/all-MiniLM-L6-v2'
    );
    console.log('Embedding model loaded');
  }
}

async function generateLocalEmbedding(truncated: string): Promise<number[]> {
  if (!embeddingPipeline) {
    await initEmbeddings();
  }

  const output = await embeddingPipeline!(truncated, {
    pooling: 'mean',
    normalize: true
  });

  return Array.from(output.data);
}

export async function generateEmbedding(text: string): Promise<number[]> {
  // Truncate text to avoid token limits (512 tokens max for this model)
  const truncated = text.substring(0, 2000);

  if (getEmbeddingBackend() === 'ollama') {
    try {
      const base = process.env.IC_EMBEDDING_BASE_URL ?? 'http://localhost:11434';
      const model = process.env.IC_EMBEDDING_MODEL ?? 'all-minilm';
      const res = await fetch(`${base}/api/embeddings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, prompt: truncated })
      });
      if (!res.ok) {
        throw new Error(`ollama embeddings HTTP ${res.status}`);
      }
      const json = await res.json();
      if (!Array.isArray(json.embedding)) {
        throw new Error('ollama embeddings: no embedding array');
      }
      return json.embedding;
    } catch (err) {
      console.error('[embeddings] ollama backend failed, falling back to local:', err);
    }
  }

  return generateLocalEmbedding(truncated);
}

export async function generateExchangeEmbedding(
  userMessage: string,
  assistantMessage: string,
  toolNames?: string[]
): Promise<number[]> {
  // Combine user question, assistant answer, and tools used for better searchability
  let combined = `User: ${userMessage}\n\nAssistant: ${assistantMessage}`;

  // Include tool names in embedding for tool-based searches
  if (toolNames && toolNames.length > 0) {
    combined += `\n\nTools: ${toolNames.join(', ')}`;
  }

  return generateEmbedding(combined);
}
