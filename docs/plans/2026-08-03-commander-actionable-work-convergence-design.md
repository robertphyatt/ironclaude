# Commander Actionable-Work Convergence Design

> **Created:** 2026-08-03
> **Status:** Revised After Live Acceptance Falsification
> **Scope mode:** selective
> **Roadmap relationship:** narrow post-Loop-8 parity remediation

## Goal

Make Commander's existing operator-message and worker-plan workflows converge on
their real terminal state. A non-actionable operator message that the Brain
answers directly must stop appearing as unprocessed work, and a Slack worker-plan
approval or rejection must fail closed unless its named worker and registered
session still exist.

This loop restores existing behavior and Codex/Claude parity. It does not add a
new user-facing workflow, provider, scheduler, queue, Slack control, or autonomous
action.

## Confirmed Root Causes

### Direct replies have no durable disposition

Brain rules already distinguish actionable directives from status questions and
other conversational messages. They instruct the Brain to answer non-actionable
messages directly. The daemon, however, considers an operator message processed
only when its exact Slack `source_ts` occurs in `directives`.

An isolated reproduction against the current source produced:

```text
before_direct_reply = 1 unprocessed message
after_direct_reply  = 1 unprocessed message
idle enforcement   = "...1 unprocessed operator message... Spawn workers now."
```

This creates a two-sided trap:

- follow the Brain rules and reply directly: the message remains unprocessed and
  repeatedly triggers message-aging and idle-enforcement work;
- submit a directive to silence the counter: a non-actionable status question
  becomes a false `pending_confirmation` directive and appears in heartbeat
  `WAITING ON` output.

Live directive `#1457` demonstrates the second path. Its stored interpretation
explicitly says it is a status question and authorizes no repository change or
worker, yet its status remains `pending_confirmation` and its
`interpretation_ts` is `NULL`.

### Worker-plan Slack commands trust arbitrary names

`Approve d1457`, `APPROVE TMO`, and `APPROVE AEP` were parsed as worker-plan
approvals. The daemon queued each arbitrary target, later constructed an
`ic-<target>` tmux name, ignored the failed send, and had no registered worker to
approve. The live log contains `can't find pane` for all three targets.

This is not a directive-approval syntax gap. `APPROVE <worker-id>` is an existing
worker-plan command and must remain one. Its target validation is missing.

## Fixed Scope

This loop changes only:

1. durable acknowledgment of non-actionable operator messages through the
   existing Brain orchestrator surface;
2. consistent disposed-message accounting in existing daemon enforcement and
   audit paths;
3. fail-closed validation and truthful reporting for existing Slack worker-plan
   approve/reject commands;
4. Codex startup verification for the new required orchestrator callable; and
5. administrative closure and live verification of the exact stale state that
   exposed the defects.

It does **not** change provider selection, failover, model routing, worker-tier
names, directive confirmation UI, Brain chatter threading, notification pin
policy, worker objectives, or repository authorization.

The separately verified Brain confusion between `claude-opus` as a compatibility
tier and Claude as a provider is not repaired in this implementation because it
has a different root cause and acceptance boundary. It is a confirmed IronClaude
product defect, not an operator-education problem or a valid need for provider
authorization. It is the mandatory immediately following parity loop; mixing its
source changes into message convergence would obscure both defects and weaken
regression evidence.

That follow-up remains narrow: make the existing `worker_type` contract explicitly
provider-neutral everywhere the Brain consumes it, require the Brain to let the
existing `ProviderRouter` resolve the configured worker client, and prove the same
tier request launches Claude when the worker role selects Claude and Codex Sol when
it selects Codex. The spawn result and Brain status must identify the resolved
client and model rather than misreporting the historical tier key as provider
identity.

Live follow-up evidence also exposed an impossible timeout contract: `spawn_worker`
defaults permit three 300-second professional-mode activation attempts, while the
Codex MCP call itself expires after 300 seconds. The first PF2e launch reached that
outer deadline before registration; a bounded 240-second, one-attempt retry completed
in 11 seconds and registered `client=codex`, `model=gpt-5.6-sol`, even though the
Brain announced “Claude Opus.” The successor loop must keep activation inside the
callable deadline, return a truthful terminal result, confirm cleanup/absence before
retry, and let the existing bounded failure policy retry without asking the operator
to choose a provider. It adds no provider, alias, routing layer, fallback, retry
system, or operator control.

A second mandatory successor PM loop repairs execution-mode recommendations.
Routine implementation must default to sequential delegated Terra/Sonnet work,
with orchestration and gates retained in the main context. Inline execution is an
exception that requires a clear task-specific explanation of why delegation is
unsuitable; shared files, serial dependencies, or a live gate alone are not that
explanation. This is an IronClaude product behavior defect, not operator training,
and adds no execution mode or orchestration feature.

### Live acceptance exposed split reply transport

The first implementation passed focused and full Commander suites, restarted
cleanly, durably acknowledged an existing status question, and created no
directive or worker for it. It nevertheless failed the required Slack acceptance
gate: the Brain called orchestrator `post_message` with a leading
`[reply-to:<exact-ts>]` marker, but Slack received a top-level message and no
threaded reply.

Source inspection confirmed two established Brain-to-Slack paths:

- daemon `poll_brain_responses()` parses the marker, strips it, posts in the
  requested thread, and adds the check reaction after complete delivery; and
- orchestrator MCP `post_message()` grades content and posts it top-level without
  parsing the marker.

The earlier design treated the first path as the only reply transport seam.
Prompt changes cannot repair the second path because the Brain already emitted
the correct transport marker. This loop therefore includes one narrow transport
parity repair before repeating live acceptance.

Combined regression after the transport repair exposed one stale test in
`test_main_validate.py` that explicitly expected malformed leading markers to
fall through as heartbeat chatter. That expectation conflicts with this design's
existing fail-closed rule. The implementation architecture remains valid; the
test inventory must replace that single stale expectation with suppression
coverage before regression and live acceptance resume.

## Architecture

### 1. Immutable non-actionable acknowledgment

Add an `operator_message_acknowledgements` table:

- `source_ts TEXT PRIMARY KEY` — exact Slack timestamp string;
- `reason TEXT NOT NULL` — Brain's nonempty reason for classifying the message as
  non-actionable; and
- `created_at TEXT NOT NULL DEFAULT (datetime('now'))`.

`source_ts` is sufficient for the current architecture because all affected
message intake and history are scoped to one configured Slack channel, and
directives already use the exact timestamp as their identity. The value is never
parsed as a float, trimmed, normalized, or rewritten. Blank or malformed Slack
timestamps are rejected.

Expose `acknowledge_operator_message(source_ts, reason)` through the existing
orchestrator MCP. The operation is immutable and idempotent:

- first valid call stores the acknowledgment;
- a repeated call returns the existing row without changing its timestamp or
  reason, whether the repeated reason is identical or different; and
- a source timestamp already owned by any directive is rejected.

Brain rules require this call whenever the current non-actionable branch says to
respond directly. It does not post, pin, react, create a directive, or start work.

### 2. Atomic disposition exclusivity

Each operator message has exactly one durable disposition:

```text
actionable     -> one or more directive rows sharing source_ts through the
                  existing supersession chain
non-actionable -> one acknowledgment row
unresolved     -> neither
```

Application-level check-then-insert is insufficient because the daemon and Brain
MCP use separate SQLite connections. Add reciprocal SQLite `BEFORE INSERT`
triggers:

- reject an acknowledgment when any directive owns its `source_ts`;
- reject a directive when an acknowledgment owns its `source_ts`.

The directive-side trigger does not prohibit multiple directive rows sharing the
same timestamp, so existing revision/supersession behavior remains intact. The
database, not call ordering, enforces the cross-table invariant under concurrent
connections.

### 3. One disposed-timestamp projection

Define one daemon helper that returns the union of exact timestamps in
`directives` and `operator_message_acknowledgements`. Use it wherever Commander
asks whether an operator message has already been handled:

- `_get_unprocessed_messages()`;
- message-aging alert cleanup;
- idle enforcement, through `_get_unprocessed_messages()`; and
- `/audit` classification.

`/audit` retains its existing purpose and output shape but reports three truthful
categories: directive, acknowledged, and unresolved. This is accounting repair,
not a new control or workflow.

### 4. Required Brain capability

Add `acknowledge_operator_message` to Codex Brain's required orchestrator callable
inventory. A Codex Brain must fail visibly at startup when its runtime is stale
and lacks the tool required by its rules.

Claude and Codex use the same orchestrator MCP implementation. Tests and live
restart evidence verify the callable is present on both surfaces; no Claude-only
startup policy is invented.

### 5. Shared existing reply-marker transport

Move the strict leading `[reply-to:<ts>]` parser from daemon-local code into one
shared Slack-interface helper. Both existing output paths use it:

- daemon polling retains its current routing, chunking, retry-thread
  preservation, heartbeat fallback, control-echo handling, and reaction timing;
- orchestrator `post_message()` parses the same marker, grades the cleaned
  semantic body, posts that body with `thread_ts`, and adds the check reaction
  only after successful Slack delivery.

Messages without a marker retain their current top-level MCP behavior. A
marker-only or empty cleaned body produces no Slack post or reaction. Rejected
proactive content and failed Slack delivery likewise produce no success reaction.

Fresh Codex live acceptance exposed one remaining false premise: prompt text and a
callable inventory do not guarantee the model invokes the acknowledgment tool. For
source timestamp `1785801581.255109`, Codex emitted a valid marked final response
without any acknowledgment call; daemon transport still posted and reacted while
the acknowledgment table remained empty.

Move the existing acknowledgment persistence logic into one small DB helper used by
the MCP tool and by both marked-reply transports. For a valid marked reply, after
any existing content grading and immediately before the first Slack post:

- return an existing acknowledgment unchanged so the Brain's semantic reason wins;
- otherwise insert one acknowledgment using the stable fallback reason
  `Marked direct reply classified operator message as non-actionable.`;
- commit before Slack delivery; and
- on missing DB, directive conflict, or any persistence error, post and react to
  nothing. The daemon logs the blocked reply; the MCP path returns its existing
  structured-error style.

Acknowledgment is a durable actionability classification, not a delivery receipt.
The valid marked-reply contract is therefore also an explicit assertion that the
source message is non-actionable. Existing directive exclusivity remains the
fail-closed defense against a conflicting assertion; transport cannot silently
convert a timestamp already owned by a directive.

SQLite and Slack cannot form one transaction. A post failure after commit leaves
the classification in place, queues the exact threaded message through existing
SlackBot behavior, and creates no check reaction. No outbox, rollback protocol,
delivery state, or retry system is added. Tests exercise that existing queue at the
real Slack transport entry. Successful delivery still gates the check reaction.
Unmarked messages never touch acknowledgment persistence.

The shared helper accepts an already-open authoritative SQLite connection, never a
path. `None` or a persistence exception fails closed; the helper never opens or
creates a replacement database.

This makes the existing marked direct-reply contract enforce its already-specified
non-actionable disposition; it does not add a classifier or new decision surface.
It adds no marker syntax, queue, scheduler, retry system, directive routing,
automatic pinning, or chatter redesign. `pin_message` remains a separate explicit
operation.

### 6. Existing worker-plan command validation

Keep parsing unchanged: `APPROVE <worker-id>` and `REJECT <worker-id>` remain
worker-plan commands.

Before queueing a decision, the daemon verifies:

- a registry row exists for the exact worker ID;
- its status is `running`; and
- its registered tmux session exists on its recorded local or remote host.

The decision consumer repeats the same checks because a worker can exit after the
command is queued. It resolves the stored `tmux_session` and existing SSH routing
instead of constructing a local-only name. It reports success only when
`send_keys()` returns success. Missing, completed, vanished, or send-failed
workers produce a precise Slack failure and no false approval/rejection claim.

Queue-time validation gives immediate operator feedback; consumption-time
validation closes the race. Neither layer creates a worker or changes a
directive.

### 7. Existing `#1457` reconciliation

Preserve directive row `#1457` and capture its complete before-state. Through the
existing `update_directive_status` tool, transition it directly to `completed`:

- the operator explicitly attempted to approve it;
- the status question was already answered;
- no worker or repository action was authorized or performed; and
- `rejected` would falsely record operator rejection and add an `x` reaction.

Record the transition as administrative closure of a misclassified, already
answered status question. Do not delete the row, create a replacement directive,
or add a conflicting acknowledgment for its `source_ts`.

## Data Flow

### Non-actionable message

```text
operator Slack message
  -> daemon forwards exact ts + text to Brain
  -> Brain classifies it as non-actionable under existing rules
  -> acknowledge_operator_message(exact ts, reason)
  -> SQLite records immutable acknowledgment
  -> Brain emits the existing leading reply marker through either output path
  -> shared parser routes cleaned content into the exact Slack thread
  -> successful delivery adds the existing check reaction
  -> aging, idle enforcement, heartbeat, and audit see message as disposed
```

### Actionable message

```text
operator Slack message
  -> Brain classifies it as actionable
  -> submit_directive(... exact source_ts ...)
  -> SQLite rejects if timestamp was already acknowledged
  -> otherwise existing approval-card and directive workflow continues unchanged
```

### Worker-plan approval

```text
APPROVE <worker-id>
  -> validate registered running worker + registered session
  -> queue existing decision file
  -> consume decision and revalidate
  -> send to recorded local/remote session
  -> report success only on successful send
```

## Error Handling

- Empty/malformed `source_ts` or empty reason: reject without mutation.
- Timestamp already owned by a directive: reject acknowledgment without
  mutation.
- Timestamp already acknowledged: reject directive submission without mutation.
- Repeated acknowledgment: return the immutable stored disposition.
- SQLite lock/error: fail visibly; do not classify the message as disposed.
- Missing acknowledgment table on an old database: `init_db` creates it and both
  invariant triggers before normal use.
- Missing required Codex callable: Brain startup fails visibly.
- Malformed or marker-only reply content: post nothing and react to nothing.
- Proactiveness rejection or Slack delivery failure: preserve existing failure
  result and do not add a success reaction.
- Unknown/non-running worker at command intake: post precise failure; queue
  nothing.
- Worker disappears before decision consumption: post precise failure; send
  nothing.
- Remote routing unavailable or `send_keys()` returns false: post failure; never
  claim approval/rejection succeeded.

## Testing Strategy

Use RED-before-GREEN tests at the actual behavioral seams.

### Durable disposition

- legacy file-backed database migration, close, reopen, and acknowledgment
  persistence;
- exact string preservation and malformed/blank rejection;
- same-reason and different-reason idempotent repeats;
- acknowledgment rejected after a directive exists;
- directive submission rejected after an acknowledgment exists;
- two SQLite connections racing acknowledgment versus directive submission,
  proving exactly one disposition commits;
- supersession still allows multiple directive rows for one unacknowledged
  `source_ts`; and
- actual MCP wrapper invocation, not direct fixture-only insertion.

### Convergent daemon behavior

- a directly answered status question is absent from
  `_get_unprocessed_messages()` after acknowledgment;
- message aging emits no alert after acknowledgment;
- idle enforcement remains silent across its real tier thresholds after
  acknowledgment;
- actionable, unresolved, and acknowledged messages retain distinct behavior;
- `/audit` classifies directive, acknowledged, and unresolved messages correctly;
  and
- a restarted daemon reads the same disposed state.

### Worker-plan commands

- full `poll_slack_commands -> decision file -> process_brain_decisions` flow;
- missing worker, completed worker, missing tmux session, remote worker, worker
  disappearing after queue, and `send_keys()` returning false;
- no decision file or tmux send for an invalid queue-time target; and
- valid live worker approval/rejection behavior unchanged.

### Runtime and regression

- shared-parser unit cases cover marked, unmarked, malformed, and marker-only
  content;
- daemon tests prove existing marker routing and reaction timing use the shared
  parser without changing chunking or heartbeat fallback;
- orchestrator tests prove cleaned-body grading, exact `thread_ts`, post-before-
  reaction ordering, and no post/reaction after rejection or delivery failure;
- shared-DB tests prove immutable explicit reasons and the deterministic transport
  fallback use one persistence implementation;
- daemon and orchestrator integration tests assert the acknowledgment row is
  committed before their first Slack post, preserve a preexisting reason, and fail
  closed before Slack/reaction on missing DB, directive conflict, or persistence
  failure;
- real SlackBot failure tests at both transport entries prove classification remains
  committed, no check reaction occurs, and the exact threaded reply is retained by
  existing queued-delivery behavior;
- missing-connection tests prove no SQLite open/create attempt, and directive/ack
  race coverage exercises the real shared helper rather than a mock;
- the existing cross-module daemon validation test proves malformed leading
  marker-like content produces no heartbeat chatter, Brain feedback, or reaction;
- Codex required-callable inventory rejects a runtime without the new tool;
- Claude and Codex orchestrator inventories expose the tool after restart;
- focused DB, orchestrator, enforcement, daemon, Slack, and Codex Brain tests;
- full Commander suite once; and
- live Commander restart followed by:
  1. before/after evidence for administrative closure of `#1457`;
  2. a fresh operator-supplied non-actionable status message;
  3. exact acknowledgment row evidence;
  4. direct threaded Brain reply;
  5. no directive or worker for that message; and
  6. no aging or idle-enforcement nudge after the applicable thresholds.

Tests must be capable of failing when the acknowledgment query, shared persistence
helper, either transport precondition, either trigger, MCP wrapper, required-tool
inventory, consumer revalidation, or send result check is removed.

## Shipping Boundary

Expected production files:

- `commander/src/ironclaude/db.py`
- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/src/ironclaude/main.py`
- `commander/src/ironclaude/slack_interface.py`
- `commander/src/ironclaude/codex_brain_client.py`
- `commander/src/brain/system_prompt.md`
- `commander/src/brain/rules/workflow.md`

Expected test files are limited to the corresponding existing Commander DB,
orchestrator, enforcement/daemon, Slack-command, and Codex Brain test modules.
Exact test allowlists will be fixed in the implementation plan after inspecting
the narrowest current fixtures.

Protected user-modified files remain byte-identical and unstaged:

- `AGENTS.md`
- `commander/config/ironclaude.json`

No commit or push is authorized by this design.

## Acceptance

- Directly answered non-actionable messages become durably quiescent across
  restart without becoming directives.
- Actionable messages cannot be hidden by a competing acknowledgment.
- Directive supersession behavior remains unchanged.
- Claude and Codex Brain surfaces expose the same acknowledgment capability;
  Codex fails visibly on stale inventory.
- Existing worker-plan approve/reject commands never target an absent or dead
  session and never report success after a failed send.
- `#1457` is preserved and administratively completed with no worker or repository
  action.
- Existing directive cards, reactions, pins, Brain chatter, worker routing,
  provider selection, and failover behavior remain unchanged.
- Both established Brain output paths honor the same existing leading reply
  marker and persist its acknowledgment before Slack delivery even when a model
  omits the explicit tool call; unmarked MCP messages remain top-level and pinning
  remains explicit.
- The provider-tier naming defect is carried as a mandatory next parity loop, not
  accepted as operator guidance or treated as solved by a Slack workaround.
- The execution-mode recommendation defect is carried as its own mandatory PM
  loop, with sequential Terra/Sonnet delegation as the routine default.
- Focused and full Commander tests pass.
- Protected user-modified files remain byte-identical and unstaged.
- No unrelated files, features, abstractions, or controls are added.
