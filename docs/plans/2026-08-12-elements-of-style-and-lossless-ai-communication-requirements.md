# Elements of Style and Lossless AI Communication Requirements

> **Created:** 2026-08-12
> **Status:** Operator Approved

## Purpose

Require every IronClaude-controlled LLM communication path to use a recipient-appropriate communication contract. Human-facing prose must follow `elements-of-style`. AI-to-AI prose must be as efficient as possible while remaining lossless. Machine contracts must remain exact.

## Functional Requirements

### R1. Human-facing communication

IronClaude must load `elements-of-style` before the first substantive AI-to-human response in direct professional-mode Claude and Codex sessions and in Commander-managed human-facing output.

The policy must cover conversation, documentation, comments, errors, logs, commit messages, pull-request prose, API responses, and other human-readable text.

### R2. Lossless AI communication skill

IronClaude must add a packaged skill named `write-lossless-ai-messages`.

When invoked, the skill must make AI communication as efficient as possible while remaining lossless. It must remove filler, pleasantries, needless articles, repetition, and redundant context while preserving:

- Decisions and constraints.
- Authority boundaries and required approvals.
- Uncertainty and evidence.
- Identifiers and exact values.
- Dependencies and causal relationships.
- Required actions and completion conditions.

The recipient must be able to reconstruct the same actionable state and obligations from the compressed message.

### R3. Protected technical content

Neither communication skill may alter code, commands, paths, errors, schemas, sentinels, protocol fields, plan data, or other protected technical values.

Human-readable fields inside structured payloads must follow the applicable prose profile without changing the surrounding machine contract.

### R4. Recipient-based routing

IronClaude must select the communication profile by output destination:

- Human destination: `elements-of-style`.
- AI destination: `write-lossless-ai-messages`.
- Machine destination: exact declared contract.
- Mixed destination: load both prose skills and select per outbound message.

### R5. Direct-session parity

Professional-mode activation must require equivalent `elements-of-style` semantics in Claude's and Codex's client-owned instruction surfaces.

Activation must preserve unrelated operator content and validate semantic coverage after writing.

### R6. Commander-managed roles

Commander Brain must load both communication profiles and select by destination.

Managed Claude and Codex workers and AI-facing advisors must use `write-lossless-ai-messages` before substantive AI-to-AI communication.

Commander must verify a fresh worker profile activation after each invocation before sending advisor, goal, or objective traffic. Delivery of an unverified skill command or a readiness marker left by an earlier activation is insufficient.

The readiness marker applies only to explicit interactive managed-worker activation. Programmatic policy loading for Brain, graders, summarizers, hooks, or artifact generation must not emit the marker or change an output contract.

### R7. Grader coverage

Claude, Codex, local, and shadow graders must use the exact packaged `write-lossless-ai-messages` skill for natural-language feedback, reasoning, and diagnosis fields.

IronClaude must resolve the active grader provider, select that provider's declared construction path, and programmatically load the trusted skill into grader system context before the grader receives untrusted input. Generic Skill, MCP, file, execution, messaging, worktree, and agent tools must remain unavailable to isolated graders.

A missing or unreadable skill must produce a distinct grader infrastructure failure, not a semantic grade.

### R8. Closed coverage

Every IronClaude LLM construction path must declare a human, AI, machine, or mixed communication destination at its source construction seam. Validation must compare those declarations with the closed profile inventory and fail when a path lacks a profile assignment.

### R9. Existing review seams

Direct-session human-facing enforcement must reuse the existing shared Claude/Codex Stop-hook evaluation without adding another model call.

IronClaude must request source-model revision at supported review boundaries rather than silently rewriting output.

### R10. Writing-plans artifact profiles

The `writing-plans` skill must load and apply `elements-of-style` when writing the human-readable Markdown plan.

It must load and apply `write-lossless-ai-messages` when writing the machine-readable plan JSON. JSON descriptions should be as compact as possible while preserving the exact schema, task IDs, dependencies, allowed files, ordered steps, commands, expected evidence, paths, authority boundaries, and complete actionable state.

Compression must not create drift between human and machine plans. The existing parity audit must still prove equivalent requirements, task structure, file boundaries, steps, commands, tests, and expected results.

## Verification Requirements

Verification must prove:

- The new skill passes structural validation and contains the approved lossless invariant.
- Claude and Codex activation provide equivalent human-facing semantics.
- Claude and Codex worker startup provides equivalent AI-facing semantics.
- Brain, worker, advisor, Claude grader, Codex grader, local grader, and shadow grader paths receive their required profiles.
- Generic grader tool isolation remains intact.
- Structured schemas and protected values remain byte-identical.
- Redundant prose compresses without losing constraints, authority, uncertainty, evidence, identifiers, exact values, dependencies, causal relationships, actions, or completion conditions.
- Lossy shorthand and unclassified communication paths fail validation.
- Stop-hook parity remains intact without another validation request.
- `writing-plans` routes Markdown through the human profile and plan JSON through the lossless AI profile while preserving exact schema and human/machine parity.

## Scope Exclusions

This effort must not add:

- Configurable style or compression levels.
- Automatic prose rewriting or postprocessing.
- Per-message LLM grading calls.
- New messaging protocols or transport services.
- Generalized prompt-framework refactoring.
- New retry or provider-fallback behavior.
- Unrelated workflow, Commander, or communication features.
