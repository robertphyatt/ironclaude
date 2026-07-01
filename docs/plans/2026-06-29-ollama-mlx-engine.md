# Ollama MLX Engine Evaluation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Research Ollama's MLX engine and write a version-verified evaluation doc at `docs/research/2026-06-29-ollama-mlx-engine-evaluation.md`.

**Architecture:** Two-task sequential pipeline: (1) gather live environment facts via shell commands, (2) write research doc synthesizing pre-gathered web research with live environment facts. No code changes — pure research + documentation.

**Tech Stack:** Ollama CLI, Bash, Markdown

---

## Pre-gathered Research Facts (available to executor without web fetching)

These facts were gathered during brainstorming via Ollama blog + Ollama library. Use them directly when writing Task 2.

### Ollama MLX Timeline
- **0.19** (March 30, 2026): MLX preview — Apple Silicon path, initial model support (qwen3.5:35b-a3b-coding-nvfp4)
- **0.30** (June 5, 2026): GGUF+MLX integration — GGUF models also get partial MLX optimization; more models supported
- **MLX performance blog** (June 11, 2026): Gemma 4 12B featured; prefill 1,810 t/s vs 1,154 t/s (0.18 baseline); decode 112 t/s vs 58 t/s (0.18 baseline)

### Model Compatibility (from Ollama Library, verified)
| Model | Standard Tag | MLX Tag | Standard Size | MLX Size |
|-------|-------------|---------|---------------|----------|
| gemma4:e4b | gemma4:e4b (9.6GB) | gemma4:e4b-mlx | 9.6GB | 9.6GB |
| gemma4:12b | gemma4:12b (7.6GB) | gemma4:12b-mlx | 7.6GB | 6.8GB |
| qwen3.5:9b | qwen3.5:9b (6.6GB) | qwen3.5:9b-mlx | 6.6GB | 8.9GB |
| qwen3.5:27b | qwen3.5:27b (17GB) | qwen3.5:27b-mlx | 17GB | 20GB |

**Critical:** No `gemma4:9b` tag exists in Ollama library. Task references "gemma4:9b" — this is likely `gemma4:e4b` (effective 4B, 9.6GB file). Resolve via `ollama list` in Task 1.

### Activation Mechanism
- No explicit env var or flag found in official docs/blogs to enable MLX
- Activation paths:
  1. **Automatic** on Apple Silicon with Ollama 0.19+: GGUF models get partial MLX optimization
  2. **Explicit**: Pull `-mlx` tagged variant for native MLX weights (best performance, higher file size for smaller models)

### Performance Expectations
- Decode: ~2x faster (58 → 112 t/s measured on M5, 35B model)
- Prefill: ~57% faster (1,154 → 1,810 t/s on M5)
- M2 Ultra (192GB unified memory): no memory constraints; similar or better gains expected
- Quality: NVFP4 quantization (used by -mlx variants) has less quality loss than Q4_K_M

### Preliminary Recommendation
ADOPT — native MLX tags exist for all three use cases, migration is pull-only (no code changes), dev-only tooling with no production impact, 2x decode speed is significant for shadow grader latency.

---

## Task 1: Verify Ollama Environment

**Files:** (no file changes — read-only shell commands)
- None

**No tests required:** Read-only information gathering.

**Step 1: Check Ollama version**

Run:
```bash
ollama --version
```

Expected: Version string like `ollama version 0.X.Y`. Note the version number.

Interpretation:
- If version < 0.19: MLX not available yet
- If version >= 0.19 and < 0.30: MLX available for -mlx tagged models only
- If version >= 0.30: GGUF models also get partial MLX optimization

**Step 2: List installed models**

Run:
```bash
ollama list
```

Expected: Table of installed models with NAME, ID, SIZE, MODIFIED columns.

Look for:
- Any gemma4 variant (resolve the "9b" vs "e4b" discrepancy from task spec)
- Any qwen3.5 variant
- Any existing -mlx tagged models (indicates MLX already in use)

Note the exact model tag names for use in Task 2.

**Step 3: Stage (nothing to stage — read-only task)**

This task produces no file changes. Capture the version and model list in memory for use in Task 2.

---

## Task 2: Write Research Document

**Files:**
- Create: `docs/research/2026-06-29-ollama-mlx-engine-evaluation.md`

**No tests required:** Documentation task.

**Step 1: Create docs/research directory if needed**

Run:
```bash
mkdir -p docs/research
```

Expected: Directory exists (idempotent).

**Step 2: Write research document**

Create `docs/research/2026-06-29-ollama-mlx-engine-evaluation.md` with the following content structure:

```markdown
# Ollama MLX Engine Evaluation

> **Date:** 2026-06-29
> **Directive:** d1261
> **Hardware:** Mac Studio M2 Ultra (192GB unified memory)

## Executive Summary

[ADOPT / WAIT / REJECT] — [one sentence rationale]

## Current Environment

- **Ollama version:** [from Task 1 output]
- **MLX availability:** [based on version: not available / available for -mlx tags only / available for all models]
- **Installed models:** [list from Task 1 — use actual tag names]

## What the MLX Engine Is

[2-3 sentences: MLX replaces llama.cpp as inference backend on Apple Silicon. Uses Apple's MLX framework and unified memory architecture. Reduces unnecessary data movement, combines GPU ops into larger Metal kernels.]

## Ollama MLX Timeline

| Version | Date | What Changed |
|---------|------|--------------|
| 0.19 | March 30, 2026 | MLX preview — -mlx tagged models on Apple Silicon |
| 0.30 | June 5, 2026 | GGUF+MLX integration — GGUF models also get partial MLX optimization |

## Model Compatibility Assessment

### Our Models

| Use Case | Current Tag | MLX Tag | MLX Available | Size Delta |
|----------|------------|---------|----------------|------------|
| PF2e vision extraction | [actual tag from ollama list] | [corresponding -mlx tag] | Yes | [size change] |
| Shadow grader | [actual tag from ollama list] | [corresponding -mlx tag] | Yes | [size change] |
| Game inference | qwen3.5:9b | qwen3.5:9b-mlx | Yes | +2.3GB (6.6→8.9GB) |

**Note on gemma4 naming:** [If task said "gemma4:9b" but ollama list shows "gemma4:e4b", note the discrepancy here and clarify what model is actually in use]

### Activation Mechanism

[Explain: no explicit env var needed. Two paths: (1) automatic partial optimization for GGUF models with 0.30+, (2) explicit -mlx tag for native MLX weights and best performance]

## Performance Expectations on M2 Ultra

[Note: Official benchmarks were run on M5/M5 Pro. M2 Ultra (192GB) should see similar or better gains due to more memory bandwidth.]

| Metric | Before MLX (0.18) | With MLX (-mlx tag) | Delta |
|--------|------------------|---------------------|-------|
| Prefill speed | 1,154 t/s | 1,810 t/s | +57% |
| Decode speed | 58 t/s | 112 t/s | +93% (~2x) |

*Benchmark model: qwen3.5:35B on M5. Extrapolated — actual M2 Ultra numbers require empirical testing.*

### Impact by Use Case

**PF2e vision extraction (gemma4):** Batch pipeline, not interactive. 57% prefill improvement helps with long image+prompt processing. 2x decode is nice-to-have.

**Shadow grader (gemma4):** Runs on every session. 2x decode speed = half the wait time per evaluation. High value.

**Game inference (qwen3.5:9b):** Development inference, interactive use. 2x decode makes dev iteration noticeably faster. High value.

## Configuration Requirements

To adopt MLX:

1. Verify Ollama version >= 0.19 (from Task 1)
2. Pull MLX variants:
   ```bash
   ollama pull gemma4:[actual-tag]-mlx   # replace [actual-tag] with model from ollama list
   ollama pull qwen3.5:9b-mlx
   ```
3. Update any scripts/config that reference model tags to use -mlx variants
4. No Ollama configuration changes required — -mlx tags automatically use MLX engine

## Recommendation

**[ADOPT / WAIT / REJECT]**

Rationale:
- [Version check result: is MLX available?]
- Model compatibility: gemma4 -mlx tags exist for all sizes; qwen3.5:9b-mlx confirmed
- Performance gain: ~2x decode speed on Apple Silicon — meaningful for interactive use cases
- Risk: minimal — dev-only tooling, pull-only migration, fully reversible (keep old tag)
- No code changes required — only model tag updates in scripts

If adopting:
1. `ollama pull [gemma4-mlx-tag]`
2. `ollama pull qwen3.5:9b-mlx`
3. Update shadow grader config to use -mlx tag
4. Update PF2e pipeline config to use -mlx tag
5. Verify game inference scripts use qwen3.5:9b-mlx

## References

- Ollama MLX Preview: https://ollama.com/blog/mlx (March 30, 2026)
- Ollama MLX Performance: https://ollama.com/blog/mlx-performance (June 11, 2026)
- GGUF+MLX Integration: https://ollama.com/blog/improved-performance-and-model-support-with-gguf (June 5, 2026)
- XDA article: https://www.xda-developers.com/ollama-new-mlx-engine-local-llm-mac-twice-fast/
```

Fill in all [bracketed] sections using Task 1 output before writing the file.

**Step 3: Stage research document**

Run:
```bash
git add -f docs/research/2026-06-29-ollama-mlx-engine-evaluation.md
```

Expected: File staged. (`docs/` is in .gitignore — `-f` is required for new files. Existing docs/ files are already tracked.) Professional mode blocks commit — do not commit.

**Step 4: Verify staging**

Run:
```bash
git status
```

Expected: `docs/research/2026-06-29-ollama-mlx-engine-evaluation.md` listed under "Changes to be committed".
