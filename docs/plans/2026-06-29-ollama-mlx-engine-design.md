# Ollama MLX Engine Evaluation Design

> **Created:** 2026-06-29
> **Status:** Design Complete
> **Directive:** d1261

## Summary

Research whether Ollama's new MLX engine (replacing llama.cpp backend on Apple Silicon) should be adopted for our three dev/pipeline use cases: PF2e vision extraction, IronClaude shadow grader, and game inference. We do not ship Ollama — this is pure dev tooling. Deliverable is a research doc at `docs/research/2026-06-29-ollama-mlx-engine-evaluation.md`.

## Background (Pre-gathered Facts)

### Ollama MLX Timeline
- **0.19** (March 30, 2026): MLX preview — Apple Silicon path, initial model support (qwen3.5:35b-a3b-coding-nvfp4 only)
- **0.30** (June 5, 2026): GGUF+MLX integration — existing GGUF models also get partial MLX optimization; more models supported
- **MLX performance blog** (June 11, 2026): Gemma 4 12B featured; prefill 1,810 t/s vs 1,154 t/s (0.18); decode 112 t/s vs 58 t/s (0.18)

### Model Compatibility (from Ollama Library)
| Model | Standard Tag | MLX Tag | Standard Size | MLX Size |
|-------|-------------|---------|---------------|----------|
| gemma4:e4b | ✓ (9.6GB) | gemma4:e4b-mlx | 9.6GB | 9.6GB |
| gemma4:12b | ✓ (7.6GB) | gemma4:12b-mlx | 7.6GB | 6.8GB |
| qwen3.5:9b | ✓ (6.6GB) | qwen3.5:9b-mlx | 6.6GB | 8.9GB |
| qwen3.5:27b | ✓ (17GB) | qwen3.5:27b-mlx | 17GB | 20GB |

**Critical discrepancy:** User task references `gemma4:9b` but no such Ollama tag exists. Closest match is `gemma4:e4b` (effective 4B parameters, 9.6GB file). Must verify actual running model during execution.

### Activation Mechanism
No explicit environment variable or flag found in Ollama docs or blog posts to enable MLX. Activation appears to be:
1. **Automatic** for compatible Apple Silicon hardware with 0.19+ (GGUF models get partial optimization)
2. **Explicit** via pulling `-mlx` tagged variants for native MLX weights (best performance)

### Performance Expectations (M5-based benchmarks, extrapolate to M2 Ultra)
- Decode: ~2x faster (58 → 112 t/s on 35B model)
- Prefill: ~57% faster (1,154 → 1,810 t/s on 35B model)
- Quality: NVFP4 quantization reduces quality loss vs Q4_K_M
- Memory: MLX variants slightly larger for smaller models (qwen3.5:9b: +2.3GB)

## Execution Plan

### Task 1: Version and Environment Check
- Run `ollama --version` to confirm current version
- Run `ollama list` to see which model tags are actually installed
- Resolve gemma4 naming discrepancy (e4b vs 9b claim in task)

### Task 2: Write Research Document
Write `docs/research/2026-06-29-ollama-mlx-engine-evaluation.md` incorporating:
- Current Ollama version and whether MLX is already active
- Model compatibility matrix (with actual installed model names)
- Performance expectations for M2 Ultra
- Migration steps (if recommending adopt)
- Recommendation: Adopt / Wait / Reject

### Recommendation Preview (to be confirmed in doc)
- **Preliminary: ADOPT** — native MLX tags exist for all three use cases
- Migration is pull-only (no code changes), fully reversible
- 2x decode speed is significant for shadow grader interactive latency
- Low risk: dev-only tooling, no production impact

## Testing Strategy

No automated tests — this is a research task. Verification:
- `ollama --version` confirms version facts
- `ollama list` confirms installed models
- Research doc includes version-verified facts, not guesses

## Implementation Notes

- `docs/research/` directory may not exist — create if needed
- Stage doc after writing (`git add docs/research/...`) per task spec
- Do NOT commit
- Resolve gemma4 naming before writing compatibility section
