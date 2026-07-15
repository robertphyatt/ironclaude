# Heartbeat Two-Section Waiting Display Design

> **Created:** 2026-07-14
> **Status:** Design Complete

## Summary

The Slack heartbeat currently shows a single "⏳ *WAITING ON YOU*" block listing workers holding for the human operator's reply (added by the 2026-07-02 "Slack Waiting on Operator" feature, already shipped). This design splits that single block into two always-paired, explicitly labeled sections — "WAITING ON COMMANDER" and "WAITING ON {operator_name}" — each independently falling back to "there is nothing" when empty. Per-worker inline tags follow the same relabeling. This is a formatting-layer change only: no new wait-detection logic is introduced. The codebase has no existing concept of a worker "waiting on the Commander" (confirmed by code search — the only wait-tracking mechanism, `self._operator_waits`, is entirely human-operator-facing), so the COMMANDER section is a scaffold that always renders "there is nothing" in production until a future task defines and populates commander-side waits.

## Architecture

`format_heartbeat()` gains two new parameters: `operator_name: str = "Operator"` and `commander_waits: dict | None = None`. `commander_waits` mirrors the existing `waits` parameter structurally (same `{worker_id: {"question": str, ...}}` shape) but no caller in `main.py` ever populates it — it exists purely so the formatter's two-section/fallback logic is symmetric and independently testable, without requiring any new daemon-side tracking.

The trigger condition for rendering the wait block generalizes from `if waits:` to `if waits or commander_waits:`. This is functionally identical to today's behavior in production (commander_waits is always `None`/empty), and preserves the untouched "No active workers" early-return path when neither workers nor any wait exists.

## Components

### 1. `format_heartbeat()` — `commander/src/ironclaude/notifications.py`

Signature:
```python
def format_heartbeat(
    workers: list[dict],
    brain_usage: dict | None = None,
    waits: dict | None = None,
    commander_waits: dict | None = None,
    operator_name: str = "Operator",
) -> str:
```

Body changes (replacing the current single `if waits:` block at lines 105-110):
- `waits = waits or {}`; `commander_waits = commander_waits or {}`
- If `waits or commander_waits`:
  - Append `"⏳ *WAITING ON COMMANDER:*"`. If `commander_waits` non-empty, itemize `  • \`{wid}\` — {question}` per entry; else append `"  there is nothing"`.
  - Append `f"⏳ *WAITING ON {operator_name}:*"`. If `waits` non-empty, itemize the same way; else append `"  there is nothing"`.
- Per-worker loop (existing `for w in workers:` block): tag becomes `f" — ⏳ waiting on commander"` if `w["id"] in commander_waits`, `f" — ⏳ waiting on {operator_name}"` elif `w["id"] in waits`, else `""` (unchanged no-tag case).

### 2. `post_heartbeat` callsite — `commander/src/ironclaude/main.py` (~line 2446)

Read `operator_name = self.config.get("operator_name", "Operator")` locally (mirrors the existing pattern at main.py:962, 1041, 1217, 1262). Pass into `format_heartbeat(worker_details, brain_usage=brain_usage, waits=dict(self._operator_waits), operator_name=operator_name)`. `commander_waits` omitted (defaults to `None`).

### 3. One-time alert — `_maybe_capture_operator_wait` — `commander/src/ironclaude/main.py` (~lines 1411-1415)

Same `config.get` read. Literal changes from:
```python
f"⏳ *Waiting on you:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
```
to:
```python
f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
```

## Data Flow

Unchanged from the existing 2026-07-02 design: brain "Still holding…" message → `poll_brain_responses` → classifier → `_operator_waits` upsert (+ one-time alert) → `post_heartbeat` reads `_operator_waits` and `config["operator_name"]` → `format_heartbeat` renders both sections every beat the wait block fires. No new data sources are introduced; `commander_waits` is always `None` at the only call site.

## Error Handling

No new failure modes. `commander_waits` defaults to `None`/`{}` so its absence is not an error condition — it is the expected, permanent state until (and unless) a future task adds commander-side wait tracking. `operator_name` follows the existing `config.get(..., "Operator")` fallback pattern used elsewhere in `main.py`.

## Testing Strategy

`commander/tests/test_notifications.py`:
1. Update existing `"WAITING ON YOU"` / `"waiting on you"` assertions to the new two-section, labeled format.
2. Two sections always present together when the wait block fires: `format_heartbeat(workers, waits={"w1": {"question": "q?"}}, operator_name="Robert")` contains both `"⏳ *WAITING ON COMMANDER:*"` and `"⏳ *WAITING ON Robert:*"`.
3. "there is nothing" fallback, both directions:
   - `waits` non-empty, `commander_waits` omitted → `"WAITING ON COMMANDER:"` section shows `"  there is nothing"`.
   - `commander_waits` non-empty, `waits={}` (passed directly to the formatter to exercise the symmetric fallback, even though `main.py` never produces this combination today) → `"WAITING ON Robert:"` section shows `"  there is nothing"`.
4. Per-worker tag: worker id in `waits` → line contains `"⏳ waiting on Robert"`; worker id in `commander_waits` → line contains `"⏳ waiting on commander"`; worker id in neither → no tag suffix.
5. Regression guard: `format_heartbeat([], operator_name="Robert")` still returns `"*Heartbeat* | No active workers"` (early-return path untouched).
6. `operator_name` default: omitting the argument falls back to `"Operator"` in rendered output.

No new tests required in `test_main_validate.py` or `test_daemon.py` — the `main.py` changes are mechanical `config.get` reads and callsite argument passing, not new logic.

## Implementation Notes

- Files: `commander/src/ironclaude/notifications.py` (`format_heartbeat`), `commander/src/ironclaude/main.py` (`post_heartbeat` callsite ~2446, `_maybe_capture_operator_wait` ~1385-1417), `commander/tests/test_notifications.py`.
- No remaining `"WAITING ON YOU"` or `"waiting on you"` literals should exist in `notifications.py` or its tests after this change.
- `commander_waits` is intentionally never populated by any `main.py` code path in this change — it is a scaffold parameter only. A future task must define what "waiting on commander" means operationally (what state/detection populates it) before this section can show real data.
- No DB migration, no new daemon state, no push. Deploy: daemon restart to pick up `main.py`/`notifications.py`.
- `docs/` is gitignored — stage design/plan with `git add -f`.
