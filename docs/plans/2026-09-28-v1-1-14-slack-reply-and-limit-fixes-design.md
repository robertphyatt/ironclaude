# v1.1.14 Slack Reply and Usage-Limit Fixes — Design

> **Created:** 2026-09-28
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-28-v1-1-14-slack-reply-and-limit-fixes-requirements.md

## Summary

This release fixes two Brain→Slack defects and bumps the version to v1.1.14.

- **Reply split misroute.** The Brain sometimes sends a marker-only `[reply-to:<ts>]` message, then the real answer in a later AssistantMessage with no marker.
  - `brain_client` queues each AssistantMessage separately, and the daemon correctly drops the marker-only message (the ghost-post guard).
  - The answer therefore loses its thread target, lands as narration in the heartbeat thread, and the operator's message never gets ✅.
  - It happened on 2026-09-27 at 17:32 and on 2026-09-23. `daemon.log` has 8 "empty body" drops.
- **Usage-limit swallow.** On the `opus` fallback, the "You've hit your limit" reply neither raises nor gets queued.
  - The Slack limit alert never fires, the bounce counts as liveness, and the heartbeat shows "0 tokens — turn in progress" while operator messages go unanswered.

## Architecture

- **Reply target:** carried through the turn by `brain_client` (approved option A). `main.py` needs no routing change, because it already threads marker-led text.
- **Acknowledgement closure:** posted by the orchestrator, which already holds the Slack bot.
- **Usage-limit state:** tracked in `brain_client`, and shown through the existing account-limit alert and the heartbeat's Brain line.

## Components

### 1. Reply target carried through the turn (`brain_client.py`, `_run_session`, currently :810-855)

- **State:** a new field, `self._turn_reply_ts: str | None = None`.
- **On `ResultMessage`:** set `_turn_reply_ts = None`.
- **On an AssistantMessage with text** (after the non-opus unavailable/limit raise):
  - Parse the text with `parse_reply_to_marker` (`slack_interface.py:56`).
  - If it returns a `ts`, set `_turn_reply_ts = ts` and queue the text unchanged.
  - If the text has no leading marker and `_turn_reply_ts` is set, queue `f"{_NARRATION_PREFIX}[reply-to:{ts}] {text}"`.
  - Malformed markers are left alone; the daemon drops them, as it does today.
- **In the daemon:**
  - The existing strip at `main.py:3247` removes the narration prefix from marker-led text.
  - `main.py:3257-3276` persists the acknowledgement (idempotent), posts under `reply_ts`, and adds ✅.
  - `SlackBot.add_reaction` treats `already_reacted` as success (`slack_interface.py:247`), so later chunks are safe.
  - The marker-only first message is still dropped at `main.py:3258`.
- **Prompt** (`system_prompt.md`, reply section around :368-378): require every reply to start with `[reply-to:<ts>]` followed by the real answer in the same message. Remove the "brief acknowledgement first" wording, which produced marker-only messages.
- **Accepted trade-off:** everything else the Brain says later in a reply turn goes to the operator's thread, not the heartbeat thread.

### 2. Acknowledgement closure (`orchestrator_mcp.py`, `acknowledge_operator_message` :1757; `db.py`, `persist_operator_message_acknowledgement` :280)

- **`db.py`:** the body moves into a new public helper, `persist_operator_message_acknowledgement_with_status(conn, source_ts, reason) -> tuple[dict, bool]`.
  - It returns `created=True` only on the path that inserts and commits the row.
  - It returns `False` for an existing row, including the concurrent-`IntegrityError` path.
  - `persist_operator_message_acknowledgement` becomes `return persist_operator_message_acknowledgement_with_status(conn, source_ts, reason)[0]`, so the returned dict is unchanged. That keeps the idempotency and equality tests true: `test_db.py:483`, `test_orchestrator_mcp.py:10882`, and the concurrent-converge test.
  - Callers `main.py:3262` and `orchestrator_mcp.py:7190` are unchanged.
- **`orchestrator_mcp.py`:** `acknowledge_operator_message` calls the `_with_status` helper, imported into `orchestrator_mcp`, and returns only the dict.
  - `test_orchestrator_mcp.py:10909-10924` monkeypatches `persist_operator_message_acknowledgement`. It is updated to patch the `_with_status` helper, returning `(expected, False)`.
  - When `created` is set:
    - post the reason with `self._slack.post_message(reason, thread_ts=source_ts)`;
    - add ✅ with `self._slack.add_reaction("white_check_mark", source_ts)`.
    - Both calls sit in a try/except that logs and continues.
    - With no Slack bot (`self._slack is None`), skip both.
- **MCP tool description** (the wrapper at :7467): state that the reason is posted to the operator's thread, and that the tool is for closing a message with no reply.
- **Reply owns closure** (operator decision during planning):
  - `system_prompt.md:378` and `rules/workflow.md:888` currently tell the Brain to call `acknowledge_operator_message` before every non-actionable reply. With the posting above, that would put two posts in the thread.
  - Both lines are rewritten: a non-actionable message gets a `[reply-to:<ts>]` reply, and the reply path records the acknowledgement itself (`main.py:3262`, `orchestrator_mcp.py:7190`). `acknowledge_operator_message` is only for closing a message with no reply.
  - `test_daemon.py:117-122` (`test_brain_direct_reply_requires_acknowledgement_before_threaded_reply`) pins the old rule on both surfaces, including `"must succeed before direct reply"`.
    - It is replaced by a test for the new rule on both surfaces (`_brain_instruction_surfaces`, `test_daemon.py:36`).
    - The new test asserts the reply-owns-closure wording, `acknowledge_operator_message(source_ts, reason)`, `[reply-to:<source_ts>]` and "no directive, worker, or repository action".
    - It also asserts that `"must succeed before direct reply"` is absent.

### 3. Usage-limit state (`brain_client.py`, `main.py`, `notifications.py`)

- **`brain_client.py`:**
  - New constant `_LIMIT_PREFIX` (a distinct tag, e.g. `"[BRAIN-LIMIT] "`) and new field `self._usage_limit: str | None = None`.
  - `_note_sdk_activity()` moves out of the unconditional top of the loop:
    - it runs for every non-AssistantMessage;
    - it runs for AssistantMessages that are not limit text.
  - On AssistantMessage limit text in the opus branch (where the raise is skipped):
    - set `self._usage_limit = full_text`;
    - queue `f"{_LIMIT_PREFIX}{full_text}"`;
    - do not call `_note_sdk_activity`.
  - On a non-limit AssistantMessage with text: set `self._usage_limit = None`.
  - **Liveness exception:** a `ResultMessage` that ends a limit turn must not refresh liveness either.
    - While `_usage_limit` is set, the ResultMessage clears `_executing_tool` and records usage as today, but does not call `_note_sdk_activity`.
  - **`needs_restart()`:** after the compaction and dead-thread checks, `if self._usage_limit is not None: return False`.
  - **`get_token_usage()`:** adds `"usage_limited": <reset string or None>`, computed as `detect_account_limit(self._usage_limit)` when a limit is set.
  - **Shared location:** `_ACCOUNT_LIMIT_RE`, `_WORKER_LIMIT_RE` and `detect_account_limit` move verbatim from `main.py:861-877` into `brain_client.py`. `_LIMIT_COOLDOWN_S` stays in `main`.
    - `main.py` imports them from `ironclaude.brain_client`, extending its existing import at `main.py:45`, so `ironclaude.main.detect_account_limit` still resolves.
    - This avoids a cycle: `main` imports `brain_client`, never the reverse.
    - The existing tests (`test_main_validate.py:1049-1068`) keep importing it from `ironclaude.main` unchanged.
- **`main.py`, `poll_brain_responses`:**
  - Text starting with `_LIMIT_PREFIX` runs the existing limit alert with its cooldown (`:3232-3240`), then `continue`s.
  - It is never posted as narration.
- **`notifications.py:191-200`:** when `brain_usage.get("usage_limited")` is set, append ` — usage-limited (resets …); unanswered messages retried after reset` in place of the "turn in progress" suffix.
- **Recovery:** messages that bounced during the limit were never acknowledged, so the existing aging reminder (`main.py:1951-1978`) re-sends them after recovery. No new code is needed.

### 4. v1.1.14 release

- Bump every tracked manifest from 1.1.13 to 1.1.14:
  - `.claude-plugin/marketplace.json`;
  - `worker/.claude-plugin/plugin.json`;
  - `worker/.codex-plugin/plugin.json` (`1.1.14+codex.<YYYYMMDDHHMMSS>`);
  - `worker/mcp-servers/workspace-manager/package.json`;
  - `worker/mcp-servers/workspace-manager/package-lock.json` (lines 3 and 9);
  - `commander/pyproject.toml`.
- Add CHANGELOG `## 1.1.14` and README "What's New in v1.1.14".
- Make a release commit with no trailers and tag it `v1.1.14`. No push or deploy without an explicit operator go.

### 5. End-review follow-up: limit expiry, recovery sweep, aging-nudge wording (R5)

The Fable end review of the staged Tasks 1–6 found two defects:
- **M1:** `check_message_aging` nudges each message only once, and only while it is 30 minutes to 2 hours old (`main.py:1858,1872,1951-1962`). A limit that outlasts that nudge silently loses the message, which contradicts the "retried after reset" heartbeat line and the docs.
- **M2:** `_usage_limit` has no expiry. The `needs_restart` guard suppresses the idle PING probe, the hard net and every timeout indefinitely. After the reset, no Brain turn starts, so the heartbeat stays stale, and a subprocess that hangs while limited is never recovered.

The operator chose expiry plus a recovery sweep.

- **`brain_client.py` (M2):**
  - Import `parse_reset_time` from `ironclaude.fable_availability`, which already imports nothing from brain_client.
  - New field `self._usage_limit_until: float = 0.0`.
  - On a limit bounce, set `now = time.time()` and `reset = parse_reset_time(full_text, now)`, then `self._usage_limit_until = (reset + 900) if reset is not None else now + 3600`.
  - `needs_restart()`: the guard becomes the following, placed after the dead-thread check:

    ```
    if self._usage_limit is not None:
        if time.time() < self._usage_limit_until:
            return False
        logger.info("Brain usage-limit window passed; resuming liveness checks")
        self._usage_limit = None
    ```

    It then falls through to the normal checks, so after expiry the idle probe, hard net and timeouts all apply. If messages bounced during the limit, the normal timeout may restart the Brain once, which is acceptable after the reset.
  - `restart()` also resets `_usage_limit_until = 0.0`.
- **`main.py` (M1):**
  - New `__init__` field: `self._brain_limited_since: float | None = None`.
  - `_get_unprocessed_messages` gains two keyword parameters, `oldest: float | None = None` and `limit: int = 50`. When `oldest` is given, it replaces `time.time() - 7200` as the Slack `oldest` bound. The existing callers are unchanged.
  - In `poll_brain_responses`:
    - In the `_LIMIT_PREFIX` branch, before `continue`: `if self._brain_limited_since is None: self._brain_limited_since = time.time()`.
    - At the top of each iteration, before the PING-ACK filter, for any text that does NOT start with `_LIMIT_PREFIX`: `if self._brain_limited_since is not None: self._sweep_after_usage_limit()`.
  - New `_sweep_after_usage_limit()`:
    - Call `_get_unprocessed_messages(max_age_seconds=0, oldest=self._brain_limited_since - 300, limit=200)`.
    - If the call raises, log a warning and keep the timestamp for a retry.
    - Otherwise, when messages were found, send the Brain one `[USAGE LIMIT RECOVERED]` message. It lists each `ts: text[:300]` and says: "These operator messages arrived while you were usage-limited and have no reply or directive yet. Handle each now: reply with `[reply-to:<ts>]` followed by your answer, or submit_directive()."
    - Then set `_brain_limited_since = None`.
  - `_get_unprocessed_messages` currently swallows Slack errors and returns `[]`. So that a failed fetch is retried rather than treated as "nothing to send", the sweep needs the error. The oldest-bound path therefore re-raises the Slack fetch exception when `oldest` is given; the aging caller never passes it.
- **Aging-nudge wording (R5.3, `main.py:1960`):** "Read this message and reply with [reply-to:<ts>] followed by your answer, or submit_directive(); use acknowledge_operator_message only to close it without a reply."
- **Tests:**
  - `brain_client`:
    - the expiry time with a reset (reset + 900) and without one (now + 3600);
    - `needs_restart` is False before expiry, and after expiry clears `_usage_limit` and returns the normal result (True for a stale timeout);
    - `restart` resets `_usage_limit_until`.
  - `main`:
    - a limit then a narration sends exactly one `[USAGE LIMIT RECOVERED]` listing only the undispositioned operator messages since the limit;
    - a PING-ACK also triggers the sweep;
    - no qualifying message means nothing is sent;
    - a fetch failure keeps the timestamp, and the next response retries;
    - a second non-limit response doesn't re-sweep.
  - `test_enforcement.py`: the aging nudge carries the new wording and not "or acknowledge it".
- **Docs:** the CHANGELOG `## 1.1.14` third bullet describes the expiry and the sweep, and the suite count is re-measured. The README bullet stays true.

### 6. Second end-review follow-up (R6)

A second Fable end review found two defects. I verified both against the code.

- **M1:** `_sweep_after_usage_limit` runs at the top of the per-response loop (`main.py:3245`), before that same response's reply acknowledgement is recorded (`main.py:~3301`). The operator message that the first post-reset reply answers is therefore swept as "unanswered", and the Brain replies twice.
- **M2:** the bounce path never clears `_ping_sent_at`. Only `_note_sdk_activity` (`brain_client.py:998`) and `restart()` (`:1135`) do. When the window expires, the unanswered-PING check (`:1050-1060`) restarts a live Brain labelled "no response to [PING]", roughly every 2 hours for as long as the limit lasts.

The operator chose to fix M1 and M2 and observations 3 and 5.

- **`brain_client.py`:**
  - **Bounce branch:** add `self._ping_sent_at = 0.0`. The probe got an answer (the bounce), but liveness is still not credited: `_last_response_time` is untouched.
  - **Reset cap (obs 3):** `if _reset is not None: _reset = min(_reset, _now + 6 * 3600)`, placed before `_usage_limit_until` is computed.
- **`main.py`:**
  - **New field:** `self._brain_limited_last: float | None = None`, set to `time.time()` at every `_LIMIT_PREFIX` bounce, not only the first.
  - **Sweep trigger:** `poll_brain_responses` no longer sweeps inside the loop. It sets a local `sweep_due = True` when a non-limit response arrives while `_brain_limited_since` is set. After the loop, `if sweep_due: self._sweep_after_usage_limit()`.
  - **`_sweep_after_usage_limit`:**
    - It drops messages with `float(m["ts"]) > self._brain_limited_last`. When `_brain_limited_last` is None, it uses `_brain_limited_since`.
    - If `send_message(...)` returns False, it logs a warning and keeps both timestamps.
    - Otherwise it clears both `_brain_limited_since` and `_brain_limited_last`. An empty result clears both too.
- **Tests:**
  - **`brain_client`:**
    - a bounce with `_ping_sent_at` set clears it and leaves `_last_response_time` at 0;
    - the cap: a parsed reset 20 hours away gives `until == now + 6h + 900`.
  - **`main`:**
    - A limit bounce at T, then in ONE batch a reply `[reply-to:<ts2>] …` to an operator message ts2 that arrived after T: no `[USAGE LIMIT RECOVERED]` is sent for ts2. A message ts1 < T with no disposition is still swept.
    - `_brain_limited_last` updates on every bounce.
    - A failed send (`send_message` returns False) keeps the timestamps.
    - The existing sweep tests set `_brain_limited_last`, and the send return is MagicMock-truthy.
- **Docs:** the CHANGELOG third bullet mentions that only messages that arrived up to the last bounce are handed over, and that a bounced probe never triggers a restart. The suite count is re-measured.

### 7. Third end-review follow-up: sweep lifecycle and aging suppression (R7)

The final Fable review found two more defects, both verified against the code:
- **M1:** `_sweep_after_usage_limit` clears its state when `send_message` merely queues the sweep. A re-limit or a Brain restart before that turn is handled loses the messages, because the next sweep starts from the new bounce.
- **M2:** the aging nudge can hand over the same message as the sweep.

The operator chose to fix both, and approved this design.

- **`main.py` state:** new `__init__` field `self._brain_sweep_sent_at: float | None = None`. The test fixture `_make_poll_daemon` mirrors it.
- **Bounce branch (`_LIMIT_PREFIX`):**
  - as before, set `_brain_limited_since` if None and `_brain_limited_last = now`;
  - in addition, `self._brain_sweep_sent_at = None`, which re-arms the sweep while keeping the original `since`.
- **`_sweep_after_usage_limit`:**
  - an empty pending set clears since, last and sent_at;
  - a failed send (`sent is False`) keeps everything;
  - a successful send sets `self._brain_sweep_sent_at = time.time()` and does NOT clear since or last.
- **New `_confirm_usage_limit_sweep()`:**
  - It runs the same query and filter (a shared helper, `_usage_limit_pending()`, returns the filtered list or raises).
  - Exception: log a warning and keep the state.
  - Empty result: clear all and log.
  - Otherwise: `self.slack.post_message("⚠️ After the Brain's usage limit, N operator message(s) still have no reply or directive:\n" + "\n".join(f"• ts={m['ts']}: {text[:100]}"))`, then clear all.
- **End of `poll_brain_responses`:**

  ```
  if sweep_due and self._brain_limited_since is not None:
      if self._brain_sweep_sent_at is None:
          self._sweep_after_usage_limit()
      elif time.time() - self._brain_sweep_sent_at >= 600:
          self._confirm_usage_limit_sweep()
  ```

- **`check_message_aging` (M2):** after the 300-second throttle, `if self._brain_limited_since is not None: return`. The sweep owns hand-over while a limit or recovery is pending.
- **Tests (`test_main_validate.py`, `test_enforcement.py`):**
  - a successful sweep keeps since and last and sets sent_at;
  - a re-bounce after the send clears sent_at and keeps since, and the next non-limit batch re-sweeps with `oldest = original since - 300`;
  - confirmation before 600 seconds makes no Slack fetch;
  - confirmation at 600 seconds or later: with none pending it clears and posts no alert; with some pending it posts one alert listing the ts and clears;
  - a confirmation fetch failure keeps the state;
  - the aging nudge is suppressed while `_brain_limited_since` is set.
  - Existing sweep tests that asserted `_brain_limited_since is None` after a successful send are updated to assert that it is kept and that sent_at is set.
- **Docs:** the CHANGELOG third bullet describes the confirmation, the alert and the aging suppression. The suite count is re-measured.

### 8. Simplification (R8): supersedes the sweep parts of components 5–7

Fable's rabbit-hole assessment found that the sweep defends a case never observed: the 9/26 limit window had no operator messages. Each review round also added state that the next round found bugs in. The operator chose to simplify. Component 7 (round 3) is abandoned.

- **Remove from `main.py`:**
  - `_brain_limited_last`, both the `__init__` field and the bounce-branch assignment;
  - `_sweep_after_usage_limit`;
  - the `sweep_due` local, its in-loop assignment and the post-loop call;
  - the `oldest`/`limit` parameters and the `if oldest is not None: raise` branches in `_get_unprocessed_messages`. It returns to its original signature, `_get_unprocessed_messages(self, max_age_seconds: int = 1800)`, with the fixed `limit=50`, a 7200 s lookback, and errors swallowed to `[]`.
- **Keep:** `_brain_limited_since`, set at the first bounce.
- **Recovery notice (R8.2):** at the top of the `poll_brain_responses` loop, before the PING-ACK filter:

  ```
  if self._brain_limited_since is not None and not text.startswith(_LIMIT_PREFIX):
      since = datetime.fromtimestamp(self._brain_limited_since).strftime("%H:%M")
      self.slack.post_message(
          f"✅ Brain recovered from its usage limit (limited since {since}). Unanswered "
          "messages from that window get the normal aging reminder; resend anything older "
          "than 2 hours."
      )
      self._brain_limited_since = None
  ```

  `datetime` is already imported in `main.py`.
- **Aging suppression (R8.3):** in `check_message_aging`, directly after `self._last_message_aging_check = now`: `if self._brain_limited_since is not None: return`.
- **Heartbeat (R8.4):** `notifications.py` changes "unanswered messages retried after reset" to "resend urgent messages after reset".
- **Tests:**
  - Remove the 8 `test_recovery_sweep_*` tests, `test_brain_limited_last_tracks_every_bounce`, and `d._brain_limited_last = None` from `_make_poll_daemon`.
  - Add a test that a bounce and then a narration post exactly one recovery notice and clear the timestamp, and that a second response posts no second notice.
  - Add a test that a PING-ACK after a bounce also posts the notice.
  - Add a test (`test_enforcement.py`) that the aging nudge is suppressed while `_brain_limited_since` is set.
  - Update the heartbeat test string.
- **Docs:**
  - CHANGELOG: rewrite the third-bullet sweep sentences into the notice plus aging behaviour; add the multi-day-limit known issue; re-measure the suite count.
  - README: bullet 3 says you're told when it recovers and should resend anything urgent.

## Data Flow

- **Reply turn:**
  1. The Brain sends `[reply-to:T]` → queued → the daemon drops it (empty body), as it does today.
  2. The Brain calls a tool, then sends "answer" → `brain_client` prefixes `[reply-to:T]` → the daemon threads it under T and adds ✅.
  3. `ResultMessage` clears T.
- **Acknowledgement:** the Brain calls `acknowledge_operator_message(T, reason)` → the row is inserted (`created`) → the reason is posted in T's thread and T gets ✅. A repeat call posts nothing.
- **Limit:**
  1. A limit AssistantMessage arrives → `_usage_limit` is set, and the text is queued with `_LIMIT_PREFIX`.
  2. The daemon sends the cooldown-gated alert. Liveness is not refreshed, and there are no restarts.
  3. The heartbeat shows usage-limited.
  4. The next real reply clears the state, or the window expires at the reset time (components 5 and 6). The first non-limit response posts one recovery notice to Slack, and the aging reminder, which was quiet while the Brain was limited, resumes (component 8).

## Error Handling

- Slack failures in the acknowledgement closure are logged, never raised. The acknowledgement row stays.
- Malformed reply markers are not treated as a turn target. The daemon keeps dropping them.
- If `brain_client` is stuck limited, the dead-thread check still fires, so a dead Brain is still restarted.

## Testing Strategy

TDD throughout: RED, then GREEN.

- **`brain_client`:**
  - a marker-only message then an unmarked answer: the answer is queued with the marker;
  - the target is cleared after a `ResultMessage`;
  - a turn with no marker is unchanged;
  - on opus, limit text is queued with `_LIMIT_PREFIX`, and `_last_response_time` is unchanged by the limit AssistantMessage and its ResultMessage;
  - `needs_restart()` is False while limited, even past the timeout;
  - the state clears on a normal reply;
  - `get_token_usage()["usage_limited"]` is set, then cleared;
  - the non-opus limit path still raises and falls back.
- **`main`:**
  - a tagged answer is posted in the thread with ✅;
  - a marker-only message is still dropped;
  - limit-prefixed text alerts once within the cooldown and posts no narration.
- **`orchestrator_mcp` / `db`:**
  - a first acknowledgement posts and reacts;
  - a repeat acknowledgement, or one after a delivered reply, does not post;
  - `slack=None` is safe, and a Slack exception is swallowed;
  - `persist_operator_message_acknowledgement_with_status` returns `created` True, then False, and the plain function's dict is unchanged.
- **`notifications`:** the usage-limited line renders, and "turn in progress" is absent while limited.
- **Prompt surfaces (`test_daemon.py`):**
  - the reply-owns-closure rule appears on both `system_prompt.md` and `rules/workflow.md`, and the old pre-reply acknowledgement rule is gone;
  - `system_prompt.md` no longer contains "brief acknowledgement or ETA first".
- **Existing exact-dict test:** `test_brain_client.py:2707` (`test_get_token_usage_initial_zeros`) compares the usage dict exactly. It gains `"usage_limited": None`.
- **Release:**
  - `test_version_consistency.py` checks only that the sources agree. The plan adds an explicit `rg -F 1.1.14` presence check on every manifest, and an `rg -F 1.1.13` absence check on the manifests.
  - Then the full commander suite runs with `PYTHONUNBUFFERED=1`.

## Implementation Notes

- Every check must be falsifiable against the end state: each new test must fail if its fix is reverted.
- `docs/` is gitignored, so plan artifacts need `git add -f`.
- This session's cwd is the repo root (`/Users/roberthyatt/Code/ironclaude`). Docs live under the repo-root `docs/plans/`, and `allowed_files` are `commander/`-prefixed git-root paths.
- The deferred lineage-137 artifacts stay untouched.
