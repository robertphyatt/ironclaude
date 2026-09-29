# v1.1.14 Slack Reply and Usage-Limit Fixes — Requirements

> **Created:** 2026-09-28
> **Scope mode:** hold
> **Operator directive:** "Ok let's take on the slack bugs first before the more esoteric stuff you had wanted to work on for v1.1.14. Let's start a new pm loop, and have that include a v1.1.14 version bump. Ensure pm is active before we start"

## Operator-approved decisions

- **Bug 1 (reply split misroute):** "A: carry tag through turn". Approved section 1 as written, including the trade-off that all later text in a reply turn goes to the operator's thread.
- **Ack closure:** "A: post the ack in your thread". Approved section 2.
- **Ack conflict resolution:** "Reply owns closure (Rec.)".
  - During planning, `system_prompt.md:378` and `rules/workflow.md:888` turned out to tell the Brain to call `acknowledge_operator_message` before every non-actionable reply, which would double-post.
  - A conversational message now gets only a `[reply-to]` reply, and the reply path records the acknowledgement.
  - `acknowledge_operator_message` now means "closing with no reply", and is posted with its reason and ✅.
- **Bug 2 (usage-limit swallow):** "A: alert + honest state". Approved section 3, including no restarts while usage-limited.
- **Release:** a v1.1.14 version bump with CHANGELOG and README entries. Approved section 4. Nothing is pushed or deployed without an explicit go.

## Requirements

### R1 — Reply target carried through the Brain turn
- R1.1: When a Brain AssistantMessage's text starts with a valid `[reply-to:<ts>]` marker, `brain_client` records `<ts>` as the current turn's reply target. A later valid marker in the same turn replaces it.
- R1.2: A later AssistantMessage in the same turn whose text has no leading marker is queued with the recorded marker prefixed. The daemon then threads it under the operator's message, records the acknowledgement and adds ✅.
- R1.3: The reply target is cleared on every `ResultMessage`, the end of the turn. It never carries into the next turn.
- R1.4: A turn with no marker behaves exactly as before: narration goes only to the heartbeat thread.
- R1.5: The ghost-post guard stays. A marker-only message (empty body) is still dropped (`main.py:3257-3260`).
- R1.6: The Brain system prompt (`system_prompt.md` reply section) requires every reply to start with `[reply-to:<ts>]` followed by real text in the same message. It no longer asks for an acknowledgement-only message first.

### R2 — Acknowledgement closure posted in the operator's thread
- R2.1: When `acknowledge_operator_message` creates a new acknowledgement row, the orchestrator posts the reason in the operator's message thread and adds ✅ to that message.
- R2.2: When the row already existed, whether from an earlier acknowledgement or a delivered threaded reply, nothing is posted.
- R2.3: The persistence layer reports whether this call created the row.
  - A new helper returns `(result, created)`.
  - `persist_operator_message_acknowledgement` keeps returning the unchanged dict, so its idempotency contract (`repeated == first`) holds.
- R2.4: A missing Slack bot or a Slack error never raises, and never undoes the recorded acknowledgement.
- R2.5: The tool description tells the Brain that the reason is posted to the operator, so the Brain writes it as a short sentence for the operator.
- R2.6: Reply owns closure. The Brain prompt (`system_prompt.md:378`) and `rules/workflow.md:888` no longer tell the Brain to call `acknowledge_operator_message` before a direct reply.
  - A non-actionable message gets a `[reply-to:<ts>]` reply, and the reply path records the acknowledgement (`main.py:3262`, `orchestrator_mcp.py:7190`).
  - `acknowledge_operator_message` is used only to close a message with no reply.

### R3 — Honest usage-limit state on the opus fallback
- R3.1: When the Brain is on `opus` and receives usage-limit text ("hit your limit"), `brain_client` records the limit and queues the text under a dedicated limit prefix. The text is no longer silently dropped.
- R3.2: `poll_brain_responses` sends the existing account-limit Slack alert (`detect_account_limit`, 30-minute cooldown) for limit-prefixed text. It never posts that text as narration.
- R3.3: A limit reply, meaning the AssistantMessage and the ResultMessage that ends its turn, does not refresh Brain liveness (`_last_response_time`).
- R3.4: While usage-limited, `needs_restart()` does not restart the Brain on timeout. The dead-thread and compaction checks still run first.
- R3.5: The usage-limited state clears on the next non-limit AssistantMessage.
- R3.6: `get_token_usage()` reports the usage-limited state. The heartbeat's Brain line then shows "usage-limited (resets …)" in place of "turn in progress".
- R3.7: The non-opus path, which raises and falls back to opus, is unchanged.
- R3.8: `codex_brain_client` is out of scope.

### R4 — v1.1.14 release bump
- R4.1: Every tracked version manifest moves from 1.1.13 to 1.1.14:
  - `.claude-plugin/marketplace.json`;
  - `worker/.claude-plugin/plugin.json`;
  - `worker/.codex-plugin/plugin.json` (`1.1.14+codex.<stamp>`);
  - `worker/mcp-servers/workspace-manager/package.json`, plus both version fields in its `package-lock.json`;
  - `commander/pyproject.toml`.
- R4.2: `test_version_consistency.py` passes.
- R4.3: CHANGELOG gains `## 1.1.14`, and README gains "What's New in v1.1.14". Both cover R1–R3.
- R4.4: The full commander pytest suite is green.
- R4.5: A release commit with no trailers, and a tag. No push and no deploy without an explicit operator go.

### R5 — End-review follow-up (Fable HAS-ISSUES M1/M2; operator: "Expiry + recovery sweep (Rec.)", "Yes, write it up (Rec.)")
- R5.1 (M2): The usage-limited state expires.
  - `brain_client` records `_usage_limit_until` on each limit bounce: the reset time from `fable_availability.parse_reset_time`, plus 15 minutes; when the text has no reset time, now plus 1 hour.
  - `needs_restart()` suppresses restarts only before that time. Once it passes, `_usage_limit` is cleared and logged, and every normal liveness check resumes: the idle PING probe, the hard net, and the timeouts.
- R5.2 (M1): Recovery sweep.
  - The daemon records `_brain_limited_since` at the first limit-tagged Brain response.
  - On the next Brain response that isn't limit-tagged (including a `[PING-ACK]`), it fetches operator messages from Slack since `_brain_limited_since` minus 300 seconds that have no directive or acknowledgement.
  - It sends the Brain one `[USAGE LIMIT RECOVERED]` message listing each `ts` and text, and tells it to reply with `[reply-to:<ts>]` or call `submit_directive()`.
  - It then clears `_brain_limited_since`.
  - When the fetch fails, the timestamp is kept and the sweep retries on the next response.
  - When no message qualifies, nothing is sent.
- R5.3: The aging nudge (`check_message_aging`) says to reply with `[reply-to:<ts>]` or call `submit_directive()`, and to use `acknowledge_operator_message` only to close a message with no reply. It no longer says "submit_directive() or acknowledge it".
- R5.4: The CHANGELOG `## 1.1.14` entry describes the expiry and the recovery sweep. The suite count is re-measured.

### R6 — Second end-review follow-up (Fable HAS-ISSUES M1/M2; operator: "Fix M1+M2 + obs 3/5 (Rec.)")
- R6.1 (M1): The recovery sweep never re-hands the Brain a message it is answering, or one that arrived after the limit.
  - The daemon records `_brain_limited_last`, the time of the latest limit bounce, at every bounce.
  - The sweep includes only operator messages whose `ts` is at or before `_brain_limited_last`.
  - The sweep runs after the whole response batch has been processed, so a reply in the same batch has already recorded its acknowledgement.
- R6.2 (M2): A limit bounce clears an outstanding `[PING]` probe (`_ping_sent_at = 0.0`) without crediting liveness (`_last_response_time` is unchanged). A PING that bounces therefore never causes a "no response to [PING]" restart at window expiry.
- R6.3 (obs 3): The parsed reset time is capped at 6 hours after the bounce, before the 15-minute margin is added. This matches `fable_availability`'s clamp.
- R6.4 (obs 5): When `send_message` of the `[USAGE LIMIT RECOVERED]` message returns False, the sweep keeps `_brain_limited_since` so the next response retries.
- R6.5: The CHANGELOG `## 1.1.14` entry reflects R6. The suite count is re-measured.

### R7 — Third end-review follow-up (Fable HAS-ISSUES M1/M2; operator: "Fix M1+M2, then ship (Rec.)", then approved the design "Yes, write it up (Rec.)")
- R7.1 (M1): A successful sweep send does NOT clear `_brain_limited_since`/`_brain_limited_last`. It records `_brain_sweep_sent_at`.
- R7.2 (M1): A limit bounce while a sweep is outstanding resets `_brain_sweep_sent_at` to None and keeps `_brain_limited_since`, so the next recovery re-sweeps from the original start.
- R7.3 (M1): Confirmation.
  - On a batch with a non-limit response at least 600 seconds after `_brain_sweep_sent_at`, the daemon re-runs the same query (since − 300, ts ≤ last, no disposition).
  - Empty result: it clears all sweep state.
  - Non-empty result: it posts ONE Slack message listing the still-unanswered operator messages, then clears all sweep state.
  - A query failure keeps the state for the next batch.
  - Before 600 seconds, nothing is queried.
- R7.4 (M2): `check_message_aging` sends no `[UNPROCESSED MESSAGE]` nudge while `_brain_limited_since` is set; the sweep owns hand-over.
- R7.5: The CHANGELOG entry reflects R7. The suite count is re-measured.

### R8 — Simplification (operator: "Simplify per Fable (Rec.)", then "Yes, write it up (Rec.)")

This supersedes R5.2, R6.1, R6.4 and R7, which are removed or never built. R5.1, R5.3, R6.2 and R6.3 stay.

Fable's assessment, from the logs:
- The only real opus-limit event (9/26 16:54–20:09) had no operator messages.
- The sweep machinery guards a case with an observed frequency of zero.
- The review rounds were not converging.

Requirements:
- R8.1: Remove the recovery sweep:
  - `_sweep_after_usage_limit`;
  - `_brain_limited_last`;
  - the `sweep_due` flag and its post-batch call;
  - the `oldest`/`limit`/re-raise changes to `_get_unprocessed_messages`, which returns to its pre-v1.1.14 form;
  - their tests.
- R8.2: Keep `_brain_limited_since`, set at the first `_LIMIT_PREFIX` bounce. On the first Brain response afterwards that isn't limit-tagged (a `[PING-ACK]` counts), the daemon posts ONE top-level Slack line and clears `_brain_limited_since`. The line: "✅ Brain recovered from its usage limit (limited since HH:MM). Unanswered messages from that window get the normal aging reminder; resend anything older than 2 hours."
- R8.3: `check_message_aging` sends no nudge, and marks nothing, while `_brain_limited_since` is set. The nudge then fires normally after recovery.
- R8.4: The heartbeat's usage-limited line reads "usage-limited (resets …); resend urgent messages after reset". It no longer promises retries.
- R8.5: README and CHANGELOG match R8. The CHANGELOG records a known issue: a multi-day limit keeps the Brain on opus, cycling through bounce, expiry and probe, with no automatic return to sonnet. The suite count is re-measured.

## Non-goals
- The v1.1.14 locator, footer, `kill_worker` skip, summarizer and vitest items. They stay deferred.
- Codex Brain limit handling.
- Any change to the directive gate or to narration routing for turns with no marker.
