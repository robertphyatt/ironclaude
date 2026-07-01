# Ollama MLX Engine Evaluation

> **Date:** 2026-06-29
> **Directive:** d1261
> **Hardware:** Apple Silicon workstation (192GB unified memory)
> **Scope:** Dev/pipeline tooling only. Ollama is not shipped with a tabletop game project.

## Executive Summary

**PARTIAL ADOPT** — already on Ollama 0.30.7 (GGUF models automatically get partial MLX optimization). Do not switch shadow grader or PF2e models to `-mlx` native weights: installed models are QAT-quantized for quality reasons, and `-mlx` tags use NVFP4 instead. Only adopt native MLX weights for game inference (qwen3.5:9b-mlx) when that model is next set up.

Key findings:
1. Current version (0.30.7) already activates Ollama's MLX engine for GGUF models on Apple Silicon — the performance win is partially realized without any action.
2. Task spec incorrectly named models as "gemma4:9b" and "qwen3.5:9b". Actual installed models are all 12b IT-QAT variants (shadow grader/PF2e) and e4b-QAT (game). No qwen3.5 is currently installed.
3. Native `-mlx` tags use NVFP4 quantization, not QAT. Switching the 12b IT-QAT models would sacrifice the QAT quality benefit. Not worth it.
4. For game inference (qwen3.5), no model is currently installed. When that slot is next set up, prefer `qwen3.5:9b-mlx` over `qwen3.5:9b`.
5. Performance benchmarks were measured on M5 hardware. M2 Ultra gains should be similar — unified memory bandwidth is the key factor, not chip generation.

---

## Current Environment

- **Ollama version:** 0.30.7
- **MLX availability:** Full — GGUF models get automatic partial MLX optimization (0.30+); native `-mlx` tags available for maximum performance
- **Installed models:**

| Model Tag | Size | Context | Use Case |
|-----------|------|---------|----------|
| `ic-gemma4-12b-it-qat-131072:latest` | 7.2GB | 128K | Shadow grader |
| `gemma4-12b-256k:latest` | 7.2GB | 256K | PF2e vision extraction (256K context) |
| `gemma4-12b-128k:latest` | 7.2GB | 128K | PF2e vision extraction (128K context) |
| `gemma4:12b-it-qat` | 7.2GB | — | Gemma4 12B IT-QAT base |
| `gemma4-e4b-qat-game:latest` | 5.2GB | — | Game inference |
| `gemma4:e4b` | 9.6GB | — | Standard e4b (GGUF) |
| `gemma4:31b-it-qat` | 18GB | — | Heavy tasks |
| `gemma4:31b` | 19GB | — | Heavy tasks (standard) |
| `gemma4:26b` | 17GB | — | — |
| `nomic-embed-text:v1.5` | 274MB | — | Embeddings |

**No `-mlx` tags installed. No qwen3.5 installed.**

---

## What the MLX Engine Is

Ollama's MLX engine replaces llama.cpp as the inference backend on Apple Silicon. It uses Apple's MLX framework and unified memory architecture, reducing unnecessary CPU↔GPU data movement and combining GPU operations into larger Metal kernels. The result is higher throughput with the same hardware.

Two activation modes:
1. **Automatic (0.30+):** Existing GGUF models run through the MLX engine by default on Apple Silicon. No model changes required.
2. **Native MLX weights (via `-mlx` tags):** Models quantized specifically for MLX (using NVFP4 format) for maximum performance. Requires pulling different model tags.

---

## Ollama MLX Timeline

| Version | Date | What Changed |
|---------|------|--------------|
| 0.18 | (baseline) | llama.cpp backend only |
| 0.19 | March 30, 2026 | MLX preview — `-mlx` tagged models on Apple Silicon |
| 0.30 | June 5, 2026 | GGUF+MLX integration — GGUF models also routed through MLX engine |
| 0.30.7 | Current | Running on this machine |

---

## Model Compatibility Assessment

### Task Spec vs. Reality

The original task referenced "gemma4:9b" and "qwen3.5:9b". Neither tag exists. Actual installed models:

| Use Case (from task) | Claimed Model | Actual Installed Model |
|----------------------|--------------|----------------------|
| PF2e vision extraction | gemma4:9b | `gemma4-12b-256k:latest` (7.2GB, 256K ctx) |
| Shadow grader | gemma4:9b | `ic-gemma4-12b-it-qat-131072:latest` (7.2GB, 128K ctx) |
| Game inference | qwen3.5:9b | `gemma4-e4b-qat-game:latest` (5.2GB) — *qwen3.5 not installed* |

### MLX Tag Availability for Actual Models

| Installed Model | Native MLX Equivalent | MLX Tag Available | Size |
|-----------------|-----------------------|-------------------|------|
| `ic-gemma4-12b-it-qat-131072` | `gemma4:12b-mlx` | Yes | 6.8GB |
| `gemma4-12b-256k` | `gemma4:12b-mlx` | Yes | 6.8GB |
| `gemma4-e4b-qat-game` | `gemma4:e4b-mlx` | Yes | 9.6GB |
| `qwen3.5:9b` (not installed) | `qwen3.5:9b-mlx` | Yes | 8.9GB |

### Critical Quality Trade-off

The shadow grader and PF2e models are **IT-QAT (instruction-tuned, quantization-aware trained)**. QAT trains the model during quantization to compensate for precision loss, producing better quality than post-training quantization at the same file size. This was the reason these specific variants were chosen (from d1007 and d1248 research).

Native `-mlx` tags use **NVFP4 quantization** (post-training). Switching would:
- Gain: maximum MLX throughput (potentially 2x decode vs llama.cpp baseline)
- Lose: QAT quality advantage, custom context-window configuration (`ic-gemma4-12b-it-qat-131072` has 131072-token context tuned for shadow grader)

**The quality trade is not worth it for shadow grader or PF2e.** Accuracy of evaluation and card text extraction matters more than raw speed.

However, since Ollama 0.30.7 already routes GGUF models through the MLX engine, the QAT models are already getting partial MLX acceleration. The delta between "current state" and "switch to -mlx tags" is narrower than the headline "2x faster" figure suggests.

---

## Performance Expectations on M2 Ultra

Official benchmarks (Ollama blog, June 2026) were measured on M5/M5 Pro with a 35B model:

| Metric | Ollama 0.18 (llama.cpp) | Ollama 0.19 (MLX native) | Delta |
|--------|------------------------|--------------------------|-------|
| Prefill speed | 1,154 t/s | 1,810 t/s | +57% |
| Decode speed | 58 t/s | 112 t/s | +93% (~2x) |

*These numbers are for 35B on M5. Our 12B models on M2 Ultra will differ.*

For the M2 Ultra running 12B models:
- The 2x decode improvement measured on llama.cpp → MLX native is the **upper bound** for what switching tags would gain.
- GGUF-via-MLX (current state on 0.30.7) captures an unknown fraction of this improvement. The exact split is undocumented by Ollama.
- M2 Ultra has 192GB unified memory and enormous memory bandwidth — the improvement baseline may already be high, making marginal gains from switching tags smaller.

### Impact Assessment by Use Case

**Shadow grader (`ic-gemma4-12b-it-qat-131072`):**  
Already on 0.30.7 → partially accelerated via MLX engine. Switching to `gemma4:12b-mlx` would lose QAT quality and the custom 131K context configuration. **Do not switch.**

**PF2e vision extraction (`gemma4-12b-256k`):**  
Batch pipeline — not interactive. 256K context window is essential for long PDF chapters. No `gemma4:12b-256k-mlx` exists. Would need to use `gemma4:12b-mlx` (loses 256K context). **Do not switch.**

**Game inference (`gemma4-e4b-qat-game`):**  
qwen3.5 is not installed. The game model appears to be `gemma4-e4b-qat-game` (QAT). Same trade-off applies: keep QAT for game model quality. If qwen3.5 is added back for game inference, prefer `qwen3.5:9b-mlx` since there's no QAT qwen3.5 variant available.

---

## Recommendation: PARTIAL ADOPT

**Already done (no action needed):** Ollama 0.30.7 routes all GGUF models through the MLX engine on Apple Silicon. The three use cases are already partially accelerated.

**Do not switch to `-mlx` tags for:**
- Shadow grader: `ic-gemma4-12b-it-qat-131072` — custom IT-QAT model with tuned context window. No MLX equivalent preserves both properties.
- PF2e pipeline: `gemma4-12b-256k` — 256K context essential for chapter extraction. No 256K MLX variant exists.
- Game model: `gemma4-e4b-qat-game` — QAT quality is worth keeping for generation quality.

**Adopt native MLX if/when:**
- qwen3.5 is re-added for game inference: pull `qwen3.5:9b-mlx` (no QAT variant exists for qwen3.5)
- Ollama releases QAT+MLX combined variants for gemma4 (watch for `gemma4:12b-it-qat-mlx` or similar)
- Benchmarks on this specific hardware show insufficient decode speed with current QAT models

**Monitor:**  
`ollama.com/blog` for combined QAT+MLX releases. The GGUF+MLX integration path (0.30) suggests Ollama is actively improving GGUF model performance via MLX — future releases may close the gap between GGUF-via-MLX and native-MLX without sacrificing QAT.

---

## Configuration Requirements (if adopting for qwen3.5)

No Ollama configuration changes needed. To add native MLX game inference with qwen3.5:

```bash
ollama pull qwen3.5:9b-mlx
```

Then update the game inference script/config to use `qwen3.5:9b-mlx` instead of `qwen3.5:9b`.

For the shadow grader and PF2e pipeline: no changes. Current models already benefit from 0.30.7 MLX routing.

---

## References

- Ollama MLX Preview: https://ollama.com/blog/mlx (March 30, 2026)
- Ollama MLX Performance: https://ollama.com/blog/mlx-performance (June 11, 2026)
- GGUF+MLX Integration: https://ollama.com/blog/improved-performance-and-model-support-with-gguf (June 5, 2026)
- XDA article: https://www.xda-developers.com/ollama-new-mlx-engine-local-llm-mac-twice-fast/
- Prior QAT evaluation: d1007 (2026-06-05) — why IT-QAT variants were chosen
- Shadow grader architecture: d1248 (2026-06-27)
