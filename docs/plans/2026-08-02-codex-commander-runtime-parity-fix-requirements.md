# Codex Commander Runtime Parity Fix Requirements

> **Created:** 2026-08-02
> **Status:** Operator Approved
> **Authority:** Operator directives and settled brainstorming for restoring Codex Commander parity

## Goal

Restore the existing Commander directive-approval and Codex-worker routing features so Codex works as
well as Claude when Codex is selected and Claude usage is unavailable.

## Functional Requirements

1. A directive submitted by the Codex Brain must continue to post Brain chatter in the originating
   Slack thread and must also post the existing standalone directive review that the operator can
   approve with a reaction.
2. The standalone review must use the existing `submit_directive` workflow, including persistence of
   `interpretation_ts`, pinning, and source-reaction behavior. No parallel approval mechanism may be
   added.
3. The Codex orchestrator MCP must receive the existing Slack and operator environment variables it
   consumes, while secret values remain out of process arguments and diagnostic output.
4. A successful `codex login status` response that identifies ChatGPT authentication on stderr must
   make Codex available. Existing rejection behavior for API-key, unknown successful auth modes, and
   nonzero results must remain intact.
5. Existing provider ordering, fallback rules, tier mapping, quarantine policy, Brain chatter routing,
   and compatibility worker labels must remain unchanged.
6. After the source fix, Commander must be restarted, the worker provider must be explicitly reselected
   as Codex through the existing provider command, and the stale false quarantine must be cleared by
   that existing path rather than direct database mutation.
7. Completed directive #1456 and its completed Claude-native `pf2e-corpus-rules-approach` worker must
   remain unchanged. Their historical log must be checked only to establish that the worker never
   passed the usage-limit chooser; the Claude-native session must not be resumed or replaced.
8. Live routing verification must use one disposable nonce-labeled compatibility worker in a dedicated
   temporary directory outside both repositories. Commander may inject exactly its standard
   `AGENTS.md` bootstrap; the worker must create no other path. It must resolve to Codex Sol, emit an
   exact no-write readiness marker, and then be stopped through existing Commander behavior.
9. Codex worker startup must recognize its trust and ready markers when tmux pipe-log cursor sequences
   remove visual spaces, dismiss trust exactly once, and leave Claude readiness behavior unchanged.
   When the distinct Codex `Hooks need review` screen follows directory trust, Commander must select
   `Trust all and continue` exactly once with the raw option-2 shortcut and no appended Enter so
   required IronClaude hooks remain enabled. Matching must require prior directory trust plus an
   ordered, bounded exact option-2 screen shape. Readiness must occur after the latest hooks screen
   in the cumulative log; it must not choose the no-hooks path or generalize prompt acceptance.
10. Every new Slack pin must use the existing `SlackBot.pin_message()` path. If the channel has fewer
    than 100 pins, the new pin must be added without deleting any pin. If it has exactly 100 pins, the
    oldest pin by Slack pin-record `created` time must be removed before the new pin is added, whether
    that oldest item is a message, file, or file comment. An already-pinned target must not evict
    another pin, regardless of inventory count, because no new pin is being added. For an unpinned
    target, inventory response-shape failures, full-capacity candidate construction or selection
    failures, removal failures, and counts above 100 must fail closed without adding the new pin.
11. Commander setup documentation must list the `pins:read` and `pins:write` bot scopes required by
    pin inventory, addition, and removal. Live acceptance must prove the installed token can list,
    remove, and add pins without exposing it.

## Verification Requirements

1. Add RED-before-GREEN regression tests for the complete explicit MCP environment-name allowlist,
   argv secrecy, a successful stderr-only ChatGPT auth response, and the real ANSI/cursor-shaped Codex
   directory-trust/hooks-review/readiness output. Pin regressions must cover inventory exceptions and invalid shapes,
   already-pinned idempotence, all three removable item types, oldest-by-created ordering, malformed
   full inventories, over-capacity inventories with both pinned and unpinned targets, and removal
   failure.
2. Preserve existing negative authentication tests and run the focused tests plus the full Commander
   test suite with pytest cache disabled.
3. Preserve the pre-existing user changes in `AGENTS.md` and `commander/config/ironclaude.json`
   byte-for-byte and stage no unrelated paths.
4. Live verification must prove one Commander daemon, one Codex Brain app-server, and one orchestrator
   MCP; all required environment variable names in the live MCP child; usable Codex worker capability;
   successful linked-message retrieval through the live Codex Brain; a standalone diagnostic directive
   review with non-null `interpretation_ts`, source hourglass, successful capacity replacement at the
   100-pin limit, and zero dispatch; and a disposable worker registered as client `codex`, model
   `gpt-5.6-sol`. A repeated live worker proof must use a fresh probe identity, marker, and temporary
   path rather than reusing a one-shot request already present in Brain history.
5. The diagnostic directive must not be approved or dispatched. A unique nonce, a pre-submission event
   cutoff, the worker registry, and post-cutoff spawn events must provide falsifiable non-dispatch
   evidence after rejection.
6. Before-and-after Pathfinder evidence must match exactly for worktree status, tracked/nonignored
   working-file manifest and its path count, unstaged binary diff, and staged binary diff. No post-cutoff worker spawn may
   target `/Users/roberthyatt/Code/roleplaying-agents`.

## Scope Exclusions

- No additional features, generalized infrastructure, retry/outbox system, or provider-router redesign.
- No Brain chatter/thread behavior changes.
- No Slack TLS or Socket Mode repair.
- No generic directive persistence, confirmation, or dispatch changes.
- No pin history, queueing, retry service, or rollback infrastructure.
- No worker-label migration or Claude-client behavior changes.
- No direct SQLite repair.
- No Pathfinder corpus edits as part of this parity fix.
- No revival, replacement, or status mutation of completed directive #1456 or its original worker.
