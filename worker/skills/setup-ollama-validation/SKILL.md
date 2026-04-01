---
name: setup-ollama-validation
description: Configure Ollama as the validation backend for professional mode hooks
---

# Setup Ollama Validation

## Purpose

Configure Ollama as the validation backend for professional mode hooks. This replaces the slow Claude Haiku CLI calls (~12-15s) with near-instant local Ollama calls (~1s).

## When to Use

- When you want faster hook validation
- After installing Ollama locally
- To switch from Haiku to Ollama backend

## Process

### Step 1: Display explanation

Display:
```
🔧 Hook Validation Backend Setup

Professional mode hooks that use LLM validation (benefiting from Ollama):
- Checks if messages relate to the active plan (topic-change-detector) — uses LLM
- Confirms task completion (task-completion-validator) — uses LLM

Other professional mode hooks (rule-based, no LLM):
- Validates that code changes match plan scope (professional-mode-guard)
- Detects when subagents complete (subagent-drift-detector)

By default, this uses Claude Haiku via the CLI, but it's slow (~12-15 seconds per call)
due to CLI startup overhead.

Ollama runs locally with near-instant response times (<1 second).
This setup configures Ollama as your validation backend.
```

### Step 2: Check Ollama installation

Run:
```bash
ollama --version
```

If command not found, display:
```
❌ Ollama not found.

Install Ollama: https://ollama.com/download
After installing, run: ollama serve
Then retry this setup.
```
Then STOP.

If version < 0.5.0, display:
```
❌ Ollama version X.X.X found, but 0.5.0+ required for JSON schema support.

Update Ollama: https://ollama.com/download
```
Then STOP.

### Step 3: Confirm URL

Use AskUserQuestion:
```
Ollama detected. Is http://localhost:11434 the correct URL?
```
Options:
- A) Yes, use localhost:11434 (Recommended)
- B) No, I have a custom URL

If B, prompt for custom URL.

### Step 3.5: Configure fallback URL (optional)

Use AskUserQuestion:
```
Would you like to configure a fallback Ollama URL?
This is useful when your primary Ollama runs on a remote machine.
If the primary is unreachable (2s timeout), hooks will try the fallback.
```
Options:
- A) No fallback needed (Recommended for local-only setups)
- B) Yes, add localhost as fallback
- C) Yes, custom fallback URL

If A: No fallback_url in config.
If B: Set `ollama.fallback_url` to `http://localhost:11434`.
If C: Prompt for custom fallback URL.

### Step 4: List and recommend model

Run:
```bash
ollama list
```

Analyze the output and recommend the fastest model that's at least Haiku-quality.

Known good models (in order of preference for this task):
- qwen3:8b (recommended, reliable JSON output, fast on GPU)
- llama3.2:3b (fast, good quality)
- gemma2:2b (good quality)
- qwen2.5:1.5b (fast, acceptable quality)

Models NOT recommended:
- phi3:mini (generates hallucinated garbage in reasoning fields, unreliable for structured evaluation)

If no suitable models found, display:
```
⚠️ No suitable models found for hook validation.

Recommended models:
  • ollama pull qwen3:8b       (5.2 GB, recommended, reliable JSON)
  • ollama pull llama3.2:3b    (2.0 GB, fast, good quality)
  • ollama pull gemma2:2b      (1.6 GB, lightweight, good quality)

After pulling a model, run this setup again.
```
Then STOP.

Use AskUserQuestion to let user pick from available suitable models.

### Step 5: Test configuration

Run a test call:
```bash
curl -s --max-time 10 "$URL/api/generate" \
  -d '{"model": "MODEL", "prompt": "Respond with only: {\"ok\": true}", "stream": false, "format": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}'
```

Verify response contains valid JSON with `"ok": true`.

If test fails, display:
```
❌ Configuration test failed.

Options:
A) Try a different model
B) Keep config anyway (may have parsing issues)
C) Cancel setup and keep Haiku default
```

### Step 6: Save configuration

Write to `~/.claude/ironclaude-hooks-config.json`:
```json
{
  "validation_backend": "ollama",
  "ollama": {
    "url": "URL",
    "fallback_url": "FALLBACK_URL (omit if not configured)",
    "model": "MODEL_NAME"
  },
  "timeout_seconds": 60
}
```

Display:
```
✅ Ollama validation backend configured successfully!

Config saved to: ~/.claude/ironclaude-hooks-config.json
Backend: ollama
Model: MODEL_NAME
URL: URL
Fallback URL: FALLBACK_URL (or "none")

Hook validation will now use Ollama instead of Haiku.
Expected response time: <1 second (vs ~12-15 seconds with Haiku)

To revert to Haiku: rm ~/.claude/ironclaude-hooks-config.json
```

## Key Principles

- Always explain WHY before doing setup
- Fail with clear instructions, never leave user confused
- Test before saving config
- Provide easy revert instructions
