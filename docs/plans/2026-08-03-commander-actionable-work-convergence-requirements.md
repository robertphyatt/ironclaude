# Commander Actionable-Work Convergence Requirements

> **Created:** 2026-08-03
> **Status:** Operator Approved
> **Derived from:** operator-approved Commander convergence architecture, live acceptance evidence, and subsequent parity clarifications

## Objective

Restore existing Commander behavior so every operator message reaches one truthful,
durable disposition and existing worker-plan approvals target only the registered,
live worker named by the operator. Claude and Codex must expose equivalent behavior.

## Functional Requirements

1. A non-actionable operator message that the Brain answers directly must receive a
   durable acknowledgment keyed by its exact Slack `source_ts`.
2. Acknowledgment must be immutable and idempotent. Repeating it must return the
   original row without changing its reason or creation time.
3. One source timestamp must never be both acknowledged and owned by a directive,
   including when separate SQLite connections race. Existing multiple-directive
   supersession for one unacknowledged timestamp must remain valid.
4. Unprocessed-message lookup, aging, idle enforcement, heartbeat accounting, and
   `/audit` must share the same directive-or-acknowledgment disposition truth.
5. Brain rules must acknowledge every non-actionable message before replying through
   the existing thread path. Acknowledgment must not post, pin, react, create a
   directive, or dispatch work.
6. The existing orchestrator MCP must expose the acknowledgment operation to both
   Claude and Codex. Codex startup must fail visibly when the required callable is
   absent from a stale runtime.
7. Existing `APPROVE <worker-id>` and `REJECT <worker-id>` syntax must remain unchanged.
   Before queueing and again before consuming a decision, Commander must require the
   exact registered worker to be running and its recorded local or remote tmux session
   to exist.
8. Approval or rejection may be reported successful only after the existing session
   send operation succeeds. Missing, completed, vanished, unroutable, or send-failed
   workers must produce precise failure and no false success.
9. Directive `#1457` must be preserved and administratively completed through the
   existing directive-status tool because its status question was already answered
   and the operator attempted approval. It must not create a worker, repository
   action, replacement directive, or conflicting acknowledgment.
10. Both existing Brain-to-Slack output paths must honor the same existing leading
    `[reply-to:<exact-ts>]` marker. The shared transport parser must remove the
    marker, preserve the exact timestamp, and route the cleaned body into that
    thread. Orchestrator `post_message` must grade the cleaned body and add the
    existing check reaction only after successful delivery. Unmarked MCP messages
    remain top-level; marker-only, rejected, and failed posts create no reaction.
11. A marked direct reply must not depend on model compliance alone. Immediately
    before its first Slack post, each existing marked-reply transport must use one
    shared persistence operation to require the exact source timestamp to be
    acknowledged. An existing row and its reason remain unchanged; when the Brain
    omitted its explicit call, transport creates the row with one deterministic
    classification-only fallback reason that does not claim Slack delivery. A valid
    marked direct reply is an explicit non-actionable-disposition assertion, not
    merely transport syntax. Database absence, directive conflict, or persistence
    failure must stop the post and reaction. The helper accepts only the authoritative
    injected connection and must never open or create a database. No behavior changes
    for unmarked messages.

## Verification Requirements

1. Tests must prove persistence across database close/reopen, exact timestamp handling,
   immutable repeats, both exclusivity directions, two-connection races, preserved
   directive supersession, and invocation through the real MCP wrapper.
2. Tests must prove directly answered messages become quiescent across unprocessed,
   aging, idle-enforcement, heartbeat, audit, and daemon-restart paths.
3. Tests must exercise the full Slack command-to-decision-consumer path for missing,
   completed, vanished, remote, race-lost, send-failed, and valid workers.
4. Tests must fail if either exclusivity trigger, disposed-message projection, MCP
   wrapper, Codex required-callable check, consumer revalidation, or send-result check
   is removed.
5. Focused Commander tests and the full Commander suite must pass.
6. Live acceptance must verify `#1457` closure, one fresh operator-supplied
   non-actionable message, its acknowledgment row and threaded reply, absence of a
   directive or worker, and absence of later aging or idle-enforcement nudges.
7. Tests must prove parser behavior, daemon-path parity, cleaned-body MCP grading,
   exact `thread_ts`, post-before-reaction ordering, no reaction after failed or
   rejected delivery, marker-only suppression, and unchanged unmarked top-level
   posting.
8. Integrated tests must prove both marked-reply transports persist acknowledgment
   before their first Slack post, preserve an existing semantic reason, use the same
   fallback reason when absent, and produce no post or reaction on database absence,
   directive conflict/race, or persistence failure. Slack-failure tests must use the
   real transport entry with a failing client to prove the committed classification
   remains, no check reaction occurs, and existing queued-delivery behavior retains
   the exact thread. Tests must prove a missing connection creates no database file.

## Scope Constraints

1. This is defect repair and Claude/Codex parity only. Add no new user-facing workflow,
   provider, model, alias, routing layer, fallback, scheduler, queue, Slack control, or
   autonomous action.
2. Do not change directive confirmation UI, heartbeat chatter routing, notification
   pin behavior, worker objectives, repository authorization, provider selection,
   failover, model routing, or worker-tier names in this loop. The only threading
   change is parity for the already-defined leading reply marker.
3. Preserve existing parsing and directive supersession semantics.
4. Keep `AGENTS.md` and `commander/config/ironclaude.json` byte-identical and unstaged.
5. No commit or push is authorized by this loop.

## Mandatory Successor Parity Repair

The verified `claude-opus` confusion is an IronClaude contract defect, not an operator
problem. The immediately following bounded PM loop must make existing `worker_type`
tier labels provider-neutral wherever the Brain consumes them and prove that the
existing `ProviderRouter` launches Claude or Codex Sol according to configured worker
provider. Successful spawn results and Brain status must report the resolved client
and model rather than present the historical tier key as provider identity.

That loop must also align professional-mode activation with the MCP call deadline.
Current defaults allow three 300-second activation attempts behind a 300-second Codex
tool-call limit, so a valid launch can time out before returning. The existing spawn
path must instead return a truthful terminal success or failure within its callable
deadline, confirm that a failed attempt left no registered or live worker before a
bounded retry, and retry under existing failure policy without requesting provider
authorization from the operator. That successor adds no provider, alias, routing
layer, fallback, retry system, or operator control and is intentionally separate from
this implementation.

## Mandatory Successor Execution-Mode Repair

A separate immediately following PM loop must repair IronClaude's execution-mode
recommendation contract. For routine implementation, IronClaude must recommend
sequential subagents using the least capable suitable tier (Terra/Sonnet) and keep
orchestration, state transitions, reviews, and human gates in the main context.
Inline execution may be recommended only with a clear, task-specific explanation
of why delegation is unsuitable. Shared-file overlap, serial dependencies, or a
live gate alone do not satisfy that exception. This is an IronClaude product
behavior defect, not operator guidance to repeat manually, and the repair must add
no new execution mode or orchestration feature.
