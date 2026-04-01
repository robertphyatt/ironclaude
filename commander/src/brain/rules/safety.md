# Safety Directives

## Identity LoRA Architecture — CRITICAL CONTEXT

All LoRAs in roleplaying-agents are **identity adapters** — they do not change model behavior. Understanding this is required before diagnosing any LLM output issue.

1. **Identity adapters are routing placeholders** — architectural plumbing allowing future swap to real fine-tuned adapters without refactoring call sites
2. **Identity adapters pass input through unchanged** — the model behaves exactly as if no adapter is loaded
3. **The adapter switching code in LLMManager is forward-looking architecture** — NOT active behavior modification
4. **LoRA names like `gm_agent_narrative` or `game_state_arc`** map to identity adapters that do NOTHING — they are routing labels, not behavior

### NEVER

- **NEVER** attribute behavioral issues to LoRA adapters
- **NEVER** say "the LoRA might not handle this schema" or suggest LoRA training as a fix
- **NEVER** blame LoRA when LLM returns wrong JSON or bad content

### ALWAYS

- When diagnosing LLM output issues, look at: the prompt, the model (qwen3.5:9b capabilities), the code
- Treat LoRA adapter references as pure routing labels with zero behavioral impact

### Why This Exists

Operator has corrected this misattribution multiple times. Blaming identity adapters for behavioral issues wastes debugging time.

## ABSOLUTE HARD-FAIL MANDATE

**ZERO TOLERANCE.** The following concepts are BANNED from your vocabulary, your reasoning, your suggestions, and your decisions:

### Banned Terms and Concepts
- "graceful degradation"
- "fallback response" / "fallback behavior" / "fallback"
- "error recovery"
- "retry logic" (unless explicitly in a design doc)
- "soft failure"
- "defensive fallback"
- "safe default"
- "best effort"
- "degrade gracefully"

### Why This Exists

Every fallback hides a bug. Every "graceful degradation" is a lie — it degrades the system's ability to surface problems. When something fails, it MUST:

1. **Crash immediately** — hard error, visible to the operator
2. **Log the failure loudly** — not a warning, an ERROR
3. **Block progress** — do NOT continue as if nothing happened

### What You Must NEVER Do

- Suggest a worker "handle" a failure by returning partial results
- Propose "fallback responses" when an MCP tool fails
- Recommend "defensive coding" that catches exceptions without re-raising
- Use phrases like "if that fails, we can..." — NO. If it fails, it CRASHES.
- Suggest any try/catch that swallows errors instead of propagating them

### What You Must ALWAYS Do

- Treat every failure as a blocking error that must be fixed at the root cause
- Escalate failures — never hide them
- If a tool fails, the operation fails. Period.
- If a worker fails, investigate WHY, don't patch around it
