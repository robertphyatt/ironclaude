# Elements of Style and Lossless AI Communication Design

> **Created:** 2026-08-12
> **Status:** Design Complete

## Summary

IronClaude currently exposes `elements-of-style` as an optional skill, but it does not require every human-facing LLM path to use the skill. Direct professional-mode sessions, Commander Brain, workers, advisors, graders, and local validators therefore receive inconsistent communication guidance. AI-to-AI transport also lacks a named contract for removing needless language without losing operational meaning.

This design makes communication policy explicit at session construction. AI-to-human communication uses `elements-of-style`. AI-to-AI communication uses a new `write-lossless-ai-messages` skill whose governing invariant is: make communication as efficient as possible while remaining lossless. Machine contracts preserve their declared schemas and protected technical values exactly. Mixed-role sessions select the profile by message destination.

The implementation remains focused. It adds one skill, extends existing activation and prompt seams, programmatically loads the skill for isolated graders, and adds coverage tests. It does not add a rewriting service, a new messaging protocol, a per-message grading call, configuration levels, or generalized prompt infrastructure.

## Confirmed Root Cause

Repository inspection established four enforcement gaps:

1. `worker/skills/elements-of-style/SKILL.md` defines the intended human-readable prose rules, but no activation requirement or prompt-construction invariant requires other LLM paths to load it.
2. Professional-mode activation verifies workflow and behavioral concepts in client-owned instruction files, but communication style is not part of the required semantic coverage.
3. Commander Brain, worker, advisor, grader, and local-model prompts each construct instructions independently. No closed inventory assigns them a human, AI, or machine communication profile.
4. Existing output hooks grade workflow behavior, completion, rigor, prediction, option quality, and evidence. They do not consistently enforce the communication policy.

Removing `elements-of-style` would not currently change an enforced path. The root cause is optional capability without a required session profile or closed coverage invariant.

## Communication Profiles

### AI-to-human: `elements-of-style`

Load `elements-of-style` before the first substantive human-facing response. Apply it to conversation, documentation, comments, errors, logs, commit messages, pull-request prose, API responses, and other text a person will read.

The profile requires concise, active, specific, concrete, positive, evidence-backed, clear language. It does not authorize rewriting protected technical material.

### AI-to-AI: `write-lossless-ai-messages`

Add `worker/skills/write-lossless-ai-messages/SKILL.md` as a concise, self-contained skill. Its governing invariant is:

> Make AI communication as efficient as possible while remaining lossless.

The skill requires agents to remove filler, pleasantries, needless articles, repetition, and context already available to the recipient. It permits compact fragments and structured fields when they preserve meaning. It must retain every decision, constraint, authority boundary, uncertainty, evidence reference, identifier, exact value, dependency, causal relationship, required action, and completion condition.

Before sending, the agent must verify that the recipient can reconstruct the same actionable state and obligations from the compressed message. Phrases such as `the usual constraints` cannot replace exact requirements.

### Machine contract

Machine-only output follows its declared protocol rather than a prose profile. JSON keys, schema values, sentinels, paths, commands, code, errors, plan data, and other protected fields remain exact. Human-readable fields inside structured payloads use the applicable human or AI profile without changing the surrounding contract.

## Architecture

Profile selection follows the message destination, not the model identity. A single model may load more than one profile when it serves multiple destinations.

| Session or output | Required profile |
|---|---|
| Direct professional-mode Claude session speaking to an operator | `elements-of-style` |
| Direct professional-mode Codex session speaking to an operator | `elements-of-style` |
| Commander Brain posting to Slack | `elements-of-style` |
| Commander Brain sending objectives or guidance to workers | `write-lossless-ai-messages` |
| Managed Claude or Codex worker communicating with Brain | `write-lossless-ai-messages` |
| Advisor returning analysis to another AI | `write-lossless-ai-messages` |
| Grader returning machine verdict plus AI-readable feedback | machine contract plus `write-lossless-ai-messages` for feedback |
| Local or shadow grader returning AI-readable diagnosis | machine contract plus `write-lossless-ai-messages` for natural-language fields |

Direct interactive enforcement begins when professional mode activates. Installing IronClaude while leaving professional mode off does not rewrite repository instructions. Commander-managed roles receive their profiles when Commander constructs their sessions.

One compact profile helper supplies canonical communication-policy text to Commander-created prompts. A closed inventory maps each known LLM construction path to a profile. Tests fail when a new path lacks a profile assignment.

## Components

### `write-lossless-ai-messages` skill

The new skill contains only `SKILL.md` and required client metadata if the packaged skill system requires it. It needs no scripts, references, assets, README, configuration, or auxiliary documentation.

Its description must trigger for AI-to-AI instructions, worker objectives, advisor reports, grader feedback, orchestration messages, and other model-directed natural language. Its body defines the retention invariant, protected content, allowed compression, prohibited lossy shortcuts, and a final reconstructability check.

### Professional-mode activation

Extend activation's semantic coverage for both clients:

- Codex activation manages only root `AGENTS.md`.
- Claude activation manages `CLAUDE.md` and its existing behavioral-rules surface.
- Both clients require the same AI-to-human communication semantics.
- Existing incomplete files receive the missing policy while preserving unrelated operator content.
- Activation read-back validates semantics rather than a single marker phrase.

### Plan artifact routing

The `writing-plans` skill produces two destinations and must profile them separately:

- Human-readable Markdown plans load and apply `elements-of-style`.
- Machine-readable plan JSON loads and applies `write-lossless-ai-messages` to descriptions while preserving the exact schema, task IDs, dependencies, allowed files, ordered steps, commands, expected evidence, paths, authority boundaries, and actionable state.

The JSON may use compact fragments and omit prose already represented by exact fields. It may not remove information needed to execute, review, resume, or compare the plan. The existing human/machine parity audit remains authoritative.

### Direct-session Stop enforcement

The shared Claude/Codex Stop wrapper remains the parity seam. Extend its existing semantic rigor evaluation to recognize clear human-facing communication failures without adding another model call. The evaluation excludes protected technical content and quoted source material.

This check does not become a general rewriting engine. It blocks only clear violations at an existing review boundary and asks the source model to revise. Existing stop-cycle limits and infrastructure behavior remain unchanged.

### Commander Brain and managed sessions

Commander Brain loads both communication skills because it produces Slack prose and AI transport. Its instructions select the profile by destination.

Managed worker objectives require `write-lossless-ai-messages` before substantive AI-to-AI communication. Claude and Codex receive client-native invocation instructions through their existing startup prompts. Advisor sessions receive the same profile.

The skill emits one exact readiness marker only when explicitly invoked to activate an interactive managed-worker session. Commander records the session-log boundary before dispatch and observes the marker only in bytes appended after that boundary. A stale marker from an earlier activation cannot satisfy readiness. Commander uses its existing polling seam and sends no advisor, goal, or objective traffic until fresh activation succeeds. Programmatic skill loading for Brain, graders, summarizers, hooks, or artifact generation suppresses session activation behavior and preserves each output contract.

### Graders

Graders must use `write-lossless-ai-messages`; copied approximations are not sufficient.

The current Claude grader intentionally disables the generic `Skill` tool because it evaluates worker-controlled text with broad permission bypass. Enabling all installed skills would weaken grader isolation. Instead, IronClaude programmatically reads the exact trusted `write-lossless-ai-messages/SKILL.md` and prepends it to grader system context before untrusted input is evaluated.

The same loader supplies identical skill content to Claude, Codex, local, and shadow grader providers. Its wrapper explicitly disables the interactive readiness marker for programmatic loading. Generic Skill, MCP, file, execution, worktree, messaging, and agent tools remain unavailable. Structured verdict schemas remain unchanged. The skill governs only natural-language fields such as `feedback`, `reasoning`, and `diagnosis`.

Programmatic loading is the grader's skill invocation. It applies the policy before untrusted input, avoids an extra model turn, preserves tool isolation, and creates one deterministic test seam.

## Data Flow

1. IronClaude constructs or activates an LLM session.
2. The construction path declares its output destination: human, AI, machine, or mixed.
3. Human paths load `elements-of-style`; AI paths load `write-lossless-ai-messages`; machine paths retain their declared contract. Mixed paths load both prose skills and select per outbound message.
4. The model produces output under the selected profile.
5. Existing review or delivery boundaries validate profile presence and protected-content integrity. IronClaude never silently rewrites generated text.
6. A supported violation returns to the source model for revision. Machine payloads remain unchanged.

For graders, IronClaude resolves the provider, selects its declared construction path, and loads the exact skill content before untrusted input enters the prompt. Each provider receives the same augmented system context and the same output schema. Provider choice cannot change the communication contract.

## Error Handling

- Missing `elements-of-style` during AI-to-human startup blocks profile activation and reports an incomplete IronClaude installation.
- Missing `write-lossless-ai-messages` blocks AI-to-AI session creation.
- A missing or unreadable grader skill produces a distinct grader infrastructure failure. IronClaude does not fabricate a semantic grade.
- An unknown communication destination fails closed at session construction. No default profile masks an unclassified path.
- Protected-content mutation fails validation. IronClaude requests source revision only where an existing review boundary supports it.
- Provider errors retain their existing behavior. This feature adds no retries, fallback provider, recovery loop, or default output.
- Existing Stop-hook retry and cycle limits remain unchanged.

## Testing Strategy

Implementation follows RED-before-GREEN for every changed behavior.

### Skill validation

- Validate the new skill's name, frontmatter, description, structure, and concise body.
- Verify its trigger language covers workers, advisors, graders, and other AI-directed transport.
- Forward-test representative compression prompts without leaking expected answers.

### Activation parity

- Prove Claude and Codex activation require equivalent AI-to-human semantics.
- Prove incomplete instruction files receive the missing policy without losing existing content.
- Prove read-back rejects nominal markers that lack the required semantics.

### Prompt coverage

- Maintain a closed inventory of Brain, worker, advisor, grader, local-model, shadow-grader, direct-session, summarizer, and hook-validator construction paths.
- Require every path to declare a communication destination and load the matching profile.
- Compare source-seam declarations with the inventory and add a negative control showing that an unclassified path fails validation.
- Prove `writing-plans` selects the human profile for Markdown and the AI profile for plan JSON without changing the JSON schema or human/machine parity.

### Grader isolation and parity

- Prove Claude, Codex, local, and shadow graders receive the exact skill content.
- Prove a missing skill returns an infrastructure failure.
- Prove the generic Skill tool and all existing agentic, MCP, file, and execution tools remain unavailable.
- Prove structured verdict schemas and protected values remain byte-identical.

### Semantic behavior

- Show redundant prose compresses while every actionable fact remains reconstructable.
- Preserve constraints, authority, uncertainty, evidence, identifiers, exact values, dependencies, and causal relationships.
- Reject lossy substitutions such as `the usual constraints`.
- Preserve code, commands, paths, errors, schemas, sentinels, and protocol fields exactly.
- Distinguish concise lossless transport from terse but incomplete transport.

### Stop-hook parity

- Prove the existing Claude and Codex wrappers enforce the same human-facing rule.
- Prove no additional validation call is introduced.
- Prove quoted source material and protected technical content do not trigger style failure.

## Scope Boundaries

This effort includes:

- Required `elements-of-style` semantics for AI-to-human IronClaude sessions.
- New `write-lossless-ai-messages` skill.
- Required lossless-skill loading for AI-to-AI sessions, advisors, and all grader providers.
- Recipient-based routing for mixed sessions.
- Cross-client and cross-provider verification.

This effort excludes:

- Configurable style or compression levels.
- Automatic prose rewriting or postprocessing.
- Per-message LLM grading calls.
- New messaging protocols or transport services.
- Generalized prompt-framework refactoring.
- Unrelated workflow, Commander, or communication features.

## Implementation Notes

- Reuse existing activation, worker-template, Brain-prompt, grader-launch, and Stop-hook seams.
- Keep one trusted skill body and load it directly where runtime invocation would weaken isolation.
- Preserve exact schemas and protected values before optimizing prose.
- Treat communication-profile coverage as a closed invariant rather than a best-effort convention.
- Keep the new skill concise; context tokens are part of the efficiency requirement.
