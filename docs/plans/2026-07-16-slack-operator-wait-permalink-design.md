# Slack Permalink for "Waiting on Operator" Escalations Design

> **Created:** 2026-07-16
> **Status:** Design Complete
> **Directive:** d1411

## Summary

When the IronClaude daemon posts a "Waiting on Robert" escalation to Slack (via `_maybe_capture_operator_wait` in `main.py`), the message contains no link back to the specific context requiring attention. This design adds a Slack permalink to that message, and separately adds an equivalent "Link" field to the Brain-authored `[BLOCKED]` escalation template in `workflow.md`, since the two paths generate their link from different sources (a Slack `ts` that only exists post-post, vs. Brain-authored context that exists pre-post).

## Architecture

Two independent surfaces, two mechanisms, because they're grounded in different facts at different times:

1. **Daemon-posted operator-wait alerts** (`_maybe_capture_operator_wait`): posted first via `SlackBot.post_message` (returns `ts`), then a Slack permalink for that `ts` is fetched via `chat_getPermalink`, then the message is edited in place via `chat_update` to append `Link: <permalink>` on a new line.
2. **Brain-authored `[BLOCKED]` escalations** (`workflow.md` Decision Format template): composed as prose before any Slack `ts` exists, so a self-referential permalink is impossible. Instead the template gains a required `**Link:**` field pointing to the context that grounds the decision (code location, doc, or episodic-memory reference).

Both surfaces converge on the same visual convention (`Link: <url>` on its own line).

## Components

### 1. `SlackBot.get_permalink(message_ts: str) -> str | None` — `slack_interface.py`
Calls `self._client.chat_getPermalink(channel=self._channel_id, message_ts=message_ts)`. Returns `response.get("permalink")` on success. `try/except Exception`, log WARNING, return `None` on failure. Never raises.

### 2. `SlackBot.update_message(ts: str, new_text: str) -> bool` — `slack_interface.py`
Calls `self._client.chat_update(channel=self._channel_id, ts=ts, text=new_text)`. `try/except Exception`, log WARNING, return `False` on failure, `True` on success. Never raises. Takes `new_text` as the complete, already-prefixed string — does not prepend `self._prefix` itself (caller's responsibility, via the new `prefix` property below).

### 3. `SlackBot.prefix` read-only property — `slack_interface.py`
```python
@property
def prefix(self) -> str:
    return self._prefix
```
Lets `main.py` reconstruct the exact prefixed text `post_message` would have sent, without duplicating the `"[IRONCLAUDE] "` literal or reaching into a private attribute across the module boundary.

### 4. `_maybe_capture_operator_wait` update — `main.py`
Inside the existing one-time-alert block (`if self._operator_wait_alerted.get(worker_id) != question:`):
```python
alert_body = f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
ts = self.slack.post_message(alert_body)
if ts:
    permalink = self.slack.get_permalink(ts)
    if permalink:
        self.slack.update_message(ts, f"{self.slack.prefix}{alert_body}\nLink: {permalink}")
```
Replaces the current single-line `self.slack.post_message(f"...")` call. If `post_message` returns `None` (Slack failure — message is queued for retry per existing behavior), no permalink is attempted. If `get_permalink` returns `None`, no update is attempted — the alert stands as originally posted.

### 5. `workflow.md` Decision Format — `commander/src/brain/rules/workflow.md` (~line 736)
Add a required `**Link:**` field after `**To unblock:**`, with guidance that it must point to the code/doc/episodic-memory context grounding the decision — not a self-referential Slack link (structurally impossible at authoring time).

## Data Flow

```
_maybe_capture_operator_wait(text)
  → classifier confirms awaiting_operator, worker_id/question extracted
  → dedup check: worker_id+question not yet alerted (existing logic, unchanged)
      → alert_body built (same content as today, captured into a variable)
      → ts = self.slack.post_message(alert_body)
      → if ts:
          → permalink = self.slack.get_permalink(ts)
          → if permalink:
              → self.slack.update_message(ts, f"{self.slack.prefix}{alert_body}\nLink: {permalink}")
      → any None/False short-circuits remaining steps; original posted message stands
```

`workflow.md` has no runtime data flow — it only changes what the Brain is instructed to write in its own escalation prose.

## Error Handling

- `get_permalink`/`update_message`: any Slack API exception → logged at WARNING, method returns `None`/`False`, never raises.
- The permalink-enrichment sequence in `_maybe_capture_operator_wait` is strictly additive and non-blocking: a failure at any step (post, permalink fetch, or update) leaves the daemon in a safe state — either the message wasn't posted (existing queued-retry behavior, untouched) or it was posted without a link.
- No new exception types, no new retry logic, no changes to `_notification_queue` semantics.

## Testing Strategy

Exactly the 9 scenarios specified for d1411:

- `commander/tests/test_slack_interface.py` (new file, pytest + `unittest.mock.MagicMock`, class+fixture pattern per `test_brain_monitor.py`):
  - `get_permalink`: returns URL on success; returns `None` on API exception; calls `chat_getPermalink` with correct `channel`/`message_ts`.
  - `update_message`: returns `True` on success; returns `False` on API exception; calls `chat_update` with correct `channel`/`ts`/`text`.
- `commander/tests/test_main_operator_wait.py` (new file): integration tests on `_maybe_capture_operator_wait`, mocking `post_message`/`get_permalink`/`update_message`:
  - permalink present in `update_message`'s `new_text` argument when both `post_message` and `get_permalink` succeed.
  - `get_permalink`/`update_message` NOT called when `post_message` returns `None`.
  - `update_message` NOT called when `get_permalink` returns `None`.
- Full existing suite (`cd commander && python -m pytest tests/ -v`) stays green — change is additive within the existing one-time-alert block; no altered signatures on existing tested paths.

## Implementation Notes

- Files: `commander/src/ironclaude/slack_interface.py` (`get_permalink`, `update_message`, `prefix` property), `commander/src/ironclaude/main.py` (`_maybe_capture_operator_wait`), `commander/src/brain/rules/workflow.md` (Decision Format `**Link:**` field). Tests: `commander/tests/test_slack_interface.py` (new), `commander/tests/test_main_operator_wait.py` (new).
- **Pre-existing staged work**: `main.py` and `slack_interface.py` currently carry unrelated, already-staged in-progress changes (a Slack message-threading feature for solicited Brain replies, referencing d1133 — `_REPLY_TO_RE`, `_last_heartbeat_ts`, `flush_queue`/`post_message` thread_ts preservation). This design's changes land on top of that work; no overlap in touched methods/lines, but the execution plan should account for the base state not being a clean `HEAD`.
- `docs/` is gitignored — stage design/plan with `git add -f`. No push.
- No DB migration, no config changes, no new dependencies (slack_sdk's `chat_getPermalink`/`chat_update` are already available via the existing `WebClient`).
