"""Tests for check_confirmed_directives in IroncladeDaemon."""
import time
from unittest.mock import MagicMock

import pytest

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon, _worker_matches_directive


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    # Use init_db exactly as production does (it sets row_factory=sqlite3.Row
    # itself). No extra overrides here — the tests should exercise the same
    # connection shape the daemon gets.
    return init_db(db_path)


@pytest.fixture
def brain():
    b = MagicMock()
    b.send_message.return_value = True
    return b


@pytest.fixture
def daemon(db_conn, brain):
    config = {"operator_name": "TestOp"}
    d = IroncladeDaemon(
        config=config,
        slack=MagicMock(),
        socket_handler=None,
        registry=MagicMock(),
        tmux_manager=MagicMock(),
        brain=brain,
        db_conn=db_conn,
    )
    return d


def _insert_confirmed(db_conn, directive_id, interpretation, age_seconds=360):
    """Insert a directive with status='confirmed' and updated_at age_seconds ago."""
    db_conn.execute(
        "INSERT INTO directives (id, source_ts, source_text, interpretation, status, updated_at) "
        "VALUES (?, 'ts-test', 'src', ?, 'confirmed', datetime('now', ? || ' seconds'))",
        (directive_id, interpretation, f"-{age_seconds}"),
    )
    db_conn.commit()


def _insert_pending(db_conn, directive_id, interpretation, ts):
    """Insert a directive with status='pending_confirmation' and matching interpretation_ts."""
    db_conn.execute(
        "INSERT INTO directives "
        "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
        "VALUES (?, 'src-ts', 'src', ?, 'pending_confirmation', ?)",
        (directive_id, interpretation, ts),
    )
    db_conn.commit()


def _insert_superseded(db_conn, directive_id, interpretation, ts, superseded_by):
    """Insert a directive with status='superseded' and matching interpretation_ts."""
    db_conn.execute(
        "INSERT INTO directives "
        "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
        "VALUES (?, 'src-ts-superseded', 'src', ?, 'superseded', ?, ?)",
        (directive_id, interpretation, ts, superseded_by),
    )
    db_conn.commit()


class TestReactionNotification:
    def test_reaction_notifies_brain_on_confirmation(self, daemon, db_conn):
        """👍 reaction triggers brain.send_message with confirmation text."""
        ts = "1780078185.633749"
        _insert_pending(db_conn, 100, "Do the thing", ts)
        daemon.brain.send_message.return_value = True

        daemon._handle_directive_reaction("+1", ts)

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "#100" in msg
        assert "confirmed" in msg.lower()

    def test_reaction_marks_reminder_sent_on_delivery(self, daemon, db_conn):
        """Delivered confirmation sets _directive_reminder_sent[directive_id]."""
        ts = "1780078185.633749"
        _insert_pending(db_conn, 101, "Do another thing", ts)
        daemon.brain.send_message.return_value = True

        daemon._handle_directive_reaction("+1", ts)

        assert 101 in daemon._directive_reminder_sent

    def test_reaction_does_not_mark_when_brain_down(self, daemon, db_conn):
        """Failed delivery leaves _directive_reminder_sent[directive_id] absent."""
        ts = "1780078185.633750"
        _insert_pending(db_conn, 102, "Third thing", ts)
        daemon.brain.send_message.return_value = False

        daemon._handle_directive_reaction("+1", ts)

        assert 102 not in daemon._directive_reminder_sent


class TestThinkingFaceReaction:
    def test_thinking_face_flips_to_awaiting_changes(self, daemon, db_conn):
        """🤔 reaction flips directive to awaiting_changes and notifies operator + brain."""
        ts = "1780078185.700001"
        _insert_pending(db_conn, 200, "Do the thing", ts)

        daemon._handle_directive_reaction("thinking_face", ts)

        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (200,)).fetchone()
        assert row[0] == "awaiting_changes"
        daemon.slack.post_message.assert_any_call(
            "Directive #200 → waiting for your feedback..."
        )
        daemon.brain.send_message.assert_called_once()

    def test_thinking_face_notifies_brain_with_exact_wire_protocol_phrase(self, daemon, db_conn):
        """Brain message uses the exact wire-protocol phrasing the brain rules key on."""
        ts = "1780078185.700002"
        _insert_pending(db_conn, 201, "Do the thing", ts)

        daemon._handle_directive_reaction("thinking_face", ts)

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "Their next Slack message is the requested change" in msg
        assert "submit_directive" in msg
        assert "supersedes" in msg

    def test_thinking_alias_flips_to_awaiting_changes(self, daemon, db_conn):
        """'thinking' (without _face suffix) is treated identically to 'thinking_face'."""
        ts = "1780078185.700003"
        _insert_pending(db_conn, 202, "Do the thing", ts)

        daemon._handle_directive_reaction("thinking", ts)

        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (202,)).fetchone()
        assert row[0] == "awaiting_changes"

    def test_thinking_face_updates_source_reaction(self, daemon, db_conn):
        """Source message reaction swaps hourglass for thinking_face."""
        ts = "1780078185.700004"
        _insert_pending(db_conn, 203, "Do the thing", ts)

        daemon._handle_directive_reaction("thinking_face", ts)

        daemon.slack.remove_reaction.assert_any_call("hourglass_flowing_sand", "src-ts")
        daemon.slack.add_reaction.assert_any_call("thinking_face", "src-ts")


class TestSupersededReactionGuard:
    def test_thumbsup_on_superseded_directive_posts_please_react_on_newer_and_does_not_flip(
        self, daemon, db_conn
    ):
        """Reacting on a superseded directive's message warns and does not confirm it."""
        ts = "1780078185.700005"
        _insert_superseded(db_conn, 300, "Old interpretation", ts, superseded_by=301)
        # The successor must actually exist — the chain-head walk verifies
        # each hop and treats a missing successor as a dangling pointer.
        _insert_pending(db_conn, 301, "New interpretation", "ts-successor-301")

        daemon._handle_directive_reaction("thumbsup", ts)

        posted = [call.args[0] for call in daemon.slack.post_message.call_args_list]
        assert any(
            "superseded by #301" in msg and "please react on the newer" in msg
            for msg in posted
        )
        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (300,)).fetchone()
        assert row[0] == "superseded"


class TestChangesTextAlias:
    def test_changes_text_alias_flips_to_awaiting_changes(self, daemon, db_conn):
        """'changes' text reply is treated the same as the 🤔 reaction."""
        ts = "1780078185.700006"
        _insert_pending(db_conn, 400, "Do the thing", ts)

        handled = daemon._handle_directive_confirmation("changes")

        assert handled is True
        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (400,)).fetchone()
        assert row[0] == "awaiting_changes"
        daemon.brain.send_message.assert_called_once()

    def test_changes_text_alias_notifies_brain_with_wire_protocol(self, daemon, db_conn):
        """Text-alias brain message uses the same wire-protocol phrasing as the reaction path."""
        ts = "1780078185.700007"
        _insert_pending(db_conn, 401, "Do the thing", ts)

        daemon._handle_directive_confirmation("change")

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "Their next Slack message is the requested change" in msg
        assert "submit_directive" in msg
        assert "supersedes" in msg

    def test_changes_text_alias_does_not_unpin(self, daemon, db_conn):
        """R4-I2 regression: 'changes' is a NON-terminal outcome — the
        interpretation message must stay pinned so the operator can reference
        it while composing feedback (mirrors the reaction-based 🤔 path)."""
        ts = "1780078500.300001"
        _insert_pending(db_conn, 800, "Do a thing", ts)

        daemon._handle_directive_confirmation("changes")

        daemon.slack.unpin_message.assert_not_called()

    def test_yes_text_reply_unpins_interpretation(self, daemon, db_conn):
        """Terminal 'yes' outcome must still unpin the interpretation message."""
        ts = "1780078500.300002"
        _insert_pending(db_conn, 802, "Do a thing", ts)

        daemon._handle_directive_confirmation("yes")

        daemon.slack.unpin_message.assert_called_once_with(ts)


class TestContentFallbackStatusColumn:
    def test_content_fallback_matches_awaiting_changes_directive(self, daemon, db_conn):
        """R4-Q2 regression: the content-based fallback matcher must recognize
        awaiting_changes directives, like the timestamp fast path does."""
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES (?, 'src-fb', 'src', ?, 'awaiting_changes', 'ts-mismatch')",
            (801, "Fallback interpretation"),
        )
        db_conn.commit()
        # Reaction ts matches NOTHING; slack.get_message returns text that
        # content-matches the directive.
        daemon.slack.get_message.return_value = "Directive #801 needs review"

        handled = daemon._handle_directive_reaction("thumbsup", "no-such-ts")

        assert handled is True
        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (801,)).fetchone()
        assert row[0] == "confirmed"


class TestSupersededGuardProductionRowShape:
    """Defense-in-depth regression: the superseded guard must not depend on
    dict-style row access. init_db() sets row_factory=sqlite3.Row nowadays,
    but connections from other sources may be plain — this test strips the
    factory explicitly so the guard is exercised against tuple rows. A
    revert to `successor_row['superseded_by']` breaks it with TypeError.
    """

    def test_superseded_guard_works_against_plain_init_db_connection(self, tmp_path):
        db_path = str(tmp_path / "prod-shape.db")
        conn = init_db(db_path)
        # Strip the Row factory to simulate a plain connection — the access
        # pattern under test must work regardless of row_factory.
        conn.row_factory = None

        ts = "1780078200.900001"
        conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'src-ts-prod', 'src', ?, 'superseded', ?, ?)",
            (500, "Old prod interpretation", ts, 42),
        )
        # The successor must actually exist — the chain-head walk verifies
        # each hop and treats a missing successor as a dangling pointer.
        conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES (?, 'src-ts-prod-42', 'src', 'New prod interpretation', "
            "'pending_confirmation', 'ts-successor-42')",
            (42,),
        )
        conn.commit()

        config = {"operator_name": "TestOp"}
        daemon = IroncladeDaemon(
            config=config,
            slack=MagicMock(),
            socket_handler=None,
            registry=MagicMock(),
            tmux_manager=MagicMock(),
            brain=MagicMock(),
            db_conn=conn,
        )

        # Would raise TypeError under dict-style access on tuple rows.
        # Just calling this exercises the guard.
        daemon._handle_directive_reaction("thumbsup", ts)

        posted = [call.args[0] for call in daemon.slack.post_message.call_args_list]
        assert any(
            "superseded by #42" in msg and "please react on the newer" in msg
            for msg in posted
        )


class TestChainHeadWalk:
    """Regression MAIN-01 + MAIN-02: after multiple 🤔 revision rounds, the
    supersession chain has 3+ rows. The superseded-notice must point at the
    current head (superseded_by IS NULL), and the fast-path lookup must
    prefer the head over stale rows sharing the same source_ts."""

    def test_superseded_notice_points_to_chain_head_after_multiple_rounds(
        self, daemon, db_conn
    ):
        # Chain: #100 (superseded_by=101) -> #101 (superseded_by=102) -> #102 (head)
        ts_100 = "1780078300.100001"
        ts_101 = "1780078300.100002"
        ts_102 = "1780078300.100003"
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'chain-src-ts', 'src', ?, 'superseded', ?, ?)",
            (100, "old interp v1", ts_100, 101),
        )
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'chain-src-ts', 'src', ?, 'superseded', ?, ?)",
            (101, "old interp v2", ts_101, 102),
        )
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'chain-src-ts', 'src', ?, 'pending_confirmation', ?, NULL)",
            (102, "new interp v3", ts_102),
        )
        db_conn.commit()

        daemon._handle_directive_reaction("thumbsup", ts_100)

        posted = [call.args[0] for call in daemon.slack.post_message.call_args_list]
        assert any("#102" in m for m in posted), (
            f"Expected head #102 in Slack post, not immediate successor #101. Got: {posted}"
        )
        assert not any("#101" in m and "superseded by" in m.lower() for m in posted), (
            f"Slack post should NOT point at intermediate #101 (which is itself superseded). Got: {posted}"
        )

    def test_fast_path_prefers_head_over_superseded_row_for_shared_source_ts(
        self, daemon, db_conn
    ):
        # Two rows share source_ts. #100 is superseded, #101 is the head.
        # React on the shared source_ts (not on interpretation_ts).
        # Should flip #101 to confirmed; #100 should remain untouched.
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'shared-src-ts', 'src', ?, 'superseded', 'ts-old', ?)",
            (100, "old interp", 101),
        )
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'shared-src-ts', 'src', ?, 'pending_confirmation', 'ts-new', NULL)",
            (101, "new interp"),
        )
        db_conn.commit()

        daemon._handle_directive_reaction("thumbsup", "shared-src-ts")

        head_status = db_conn.execute(
            "SELECT status FROM directives WHERE id=?", (101,),
        ).fetchone()
        old_status = db_conn.execute(
            "SELECT status FROM directives WHERE id=?", (100,),
        ).fetchone()
        assert head_status[0] == "confirmed", (
            f"Head #101 should be confirmed, is {head_status[0]}"
        )
        assert old_status[0] == "superseded", (
            f"Old #100 should stay superseded, is {old_status[0]}"
        )


class TestCheckConfirmedDirectives:
    def test_sends_reminder_when_no_worker(self, daemon, db_conn):
        """Confirmed directive previously notified but no worker fires ACTION REQUIRED reminder."""
        _insert_confirmed(db_conn, 999, "Do something", age_seconds=360)
        daemon.registry.get_running_workers.return_value = []
        # Seed as previously notified 11 minutes ago so reminder path fires
        daemon._directive_reminder_sent[999] = time.time() - 660

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "#999" in msg
        assert "ACTION REQUIRED" in msg

    def test_no_reminder_when_worker_has_directive_id(self, daemon, db_conn):
        """No reminder when a running worker's description contains #N."""
        _insert_confirmed(db_conn, 888, "Do something else", age_seconds=360)
        daemon.registry.get_running_workers.return_value = [
            {"id": "w1", "tmux_session": "ic-w1", "description": "Implement directive #888"},
        ]

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_not_called()

    def test_immediate_notification_when_never_notified(self, daemon, db_conn):
        """Confirmed directive with no prior notification fires immediately regardless of age."""
        _insert_confirmed(db_conn, 333, "Fresh directive", age_seconds=30)
        daemon.registry.get_running_workers.return_value = []

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "#333" in msg

    def test_dedup_suppresses_second_reminder(self, daemon, db_conn):
        """Second call within 10 minutes does not send a second reminder."""
        _insert_confirmed(db_conn, 666, "Dedup test", age_seconds=360)
        daemon.registry.get_running_workers.return_value = []

        daemon.check_confirmed_directives()
        assert daemon.brain.send_message.call_count == 1

        daemon.brain.send_message.reset_mock()
        daemon.check_confirmed_directives()
        daemon.brain.send_message.assert_not_called()

    def test_dedup_resends_after_interval(self, daemon, db_conn):
        """Reminder re-fires after 10 minutes have elapsed since last send."""
        _insert_confirmed(db_conn, 555, "Resend test", age_seconds=360)
        daemon.registry.get_running_workers.return_value = []

        # Seed as if sent 11 minutes ago
        daemon._directive_reminder_sent[555] = time.time() - 660

        daemon.check_confirmed_directives()
        daemon.brain.send_message.assert_called_once()

    def test_skips_worker_with_null_description(self, daemon, db_conn):
        """Worker with None description does not crash the worker_found check."""
        _insert_confirmed(db_conn, 444, "Null desc test", age_seconds=360)
        daemon.registry.get_running_workers.return_value = [
            {"id": "w2", "tmux_session": "ic-w2", "description": None},
        ]

        daemon.check_confirmed_directives()

        # None description treated as no match — reminder still fires
        daemon.brain.send_message.assert_called_once()

    def test_no_reminder_when_worker_id_has_directive_prefix(self, daemon, db_conn):
        """No reminder when a running worker's ID is prefixed with d{N}-."""
        _insert_confirmed(db_conn, 900, "Persist knowledge", age_seconds=360)
        daemon.registry.get_running_workers.return_value = [
            {"id": "d900-knowledge-persistence", "tmux_session": "ic-d900-kp", "description": ""},
        ]

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_not_called()


class TestDirectiveReviewE2E:
    """End-to-end: submit_directive → 🤔 → brain re-submit (supersession) →
    👍 on new head → 👍 on old (superseded) row rejected.

    Uses OrchestratorTools.submit_directive for the daemon-side INSERTs so
    the actual production path (INSERT + supersession UPDATE + Slack post via
    format_directive_review) is exercised, not just synthetic row inserts.
    """

    @pytest.fixture
    def mock_slack(self):
        """Slack mock whose post_message returns a monotonically-increasing
        message_ts, so each posted directive gets a distinct interpretation_ts."""
        slack = MagicMock()
        counter = {"n": 0}

        def _post(msg):
            counter["n"] += 1
            return f"ts-{counter['n']:04d}"

        slack.post_message.side_effect = _post
        return slack

    @pytest.fixture
    def tools(self, db_conn, mock_slack, tmp_path):
        """OrchestratorTools with the daemon's own db_conn so state is shared
        with the IroncladeDaemon fixture below (both operate on the same DB)."""
        from ironclaude.orchestrator_mcp import OrchestratorTools
        registry = MagicMock()
        mock_tmux = MagicMock()
        ledger_path = str(tmp_path / "ledger.json")
        return OrchestratorTools(
            registry, mock_tmux, ledger_path,
            slack_bot=mock_slack, db_conn=db_conn,
        )

    @pytest.fixture
    def e2e_daemon(self, db_conn, mock_slack, brain):
        """Daemon sharing db_conn + slack with the tools fixture."""
        config = {"operator_name": "TestOp"}
        return IroncladeDaemon(
            config=config,
            slack=mock_slack,
            socket_handler=None,
            registry=MagicMock(),
            tmux_manager=MagicMock(),
            brain=brain,
            db_conn=db_conn,
        )

    def _submit(self, tools, interpretation, planned_worker_type="claude-sonnet",
                planned_use_goal=False, planned_prompt="do the thing",
                supersedes=None, source_ts="op-msg-1"):
        return tools.submit_directive(
            source_ts=source_ts,
            source_text="operator's original request",
            interpretation=interpretation,
            planned_worker_type=planned_worker_type,
            planned_use_goal=planned_use_goal,
            planned_prompt=planned_prompt,
            planned_worker_type_reason="routine implementation — Sonnet is right-sized",
            planned_use_goal_reason="single deliverable, /goal adds no value",
            planned_prompt_reason="narrows to the specific ask",
            supersedes=supersedes,
        )

    def test_full_supersession_and_confirmation_flow(
        self, tools, e2e_daemon, mock_slack, brain, db_conn
    ):
        # ── Scenario 1: submit_directive posts a formatted review with the 3 planned fields
        result_1 = self._submit(
            tools, "Do the initial task",
            planned_worker_type="claude-sonnet",
            planned_use_goal=False,
            planned_prompt="do the initial task",
        )
        did_1 = result_1["id"]
        assert result_1["status"] == "pending_confirmation"

        # The formatted Slack review post should contain the 3 planned-field markers.
        first_post = mock_slack.post_message.call_args_list[0].args[0]
        assert "claude-sonnet" in first_post
        assert "/goal" in first_post  # the /goal line appears in the review
        assert "do the initial task" in first_post  # the prompt is in the fenced block
        assert "React 👍" in first_post and "🤔" in first_post

        # ── Scenario 2: 🤔 reaction flips to awaiting_changes + brain notified
        # Look up the directive's interpretation_ts (which is what the operator
        # actually reacts on in Slack).
        row = db_conn.execute(
            "SELECT interpretation_ts FROM directives WHERE id=?", (did_1,),
        ).fetchone()
        interp_ts_1 = row[0]
        assert interp_ts_1 is not None, "submit_directive should have persisted interpretation_ts"

        e2e_daemon._handle_directive_reaction("thinking_face", interp_ts_1)

        status_row = db_conn.execute(
            "SELECT status FROM directives WHERE id=?", (did_1,),
        ).fetchone()
        assert status_row[0] == "awaiting_changes"

        # Brain got the wire-protocol message the brain rules key on.
        brain_msg = brain.send_message.call_args_list[-1].args[0]
        assert "Their next Slack message is the requested change" in brain_msg
        assert "submit_directive" in brain_msg
        assert f"supersedes={did_1}" in brain_msg

        # ── Scenario 3: brain re-submits with supersedes=did_1
        result_2 = self._submit(
            tools, "Do the revised task (Opus after feedback)",
            planned_worker_type="claude-opus",   # brain revised the tier per feedback
            planned_use_goal=True,               # and enabled /goal per feedback
            planned_prompt="do the revised task",
            supersedes=did_1,
        )
        did_2 = result_2["id"]
        assert result_2["status"] == "pending_confirmation"
        assert did_2 != did_1

        # Old row is now superseded and linked to new.
        old = db_conn.execute(
            "SELECT status, superseded_by FROM directives WHERE id=?", (did_1,),
        ).fetchone()
        assert old[0] == "superseded"
        assert old[1] == did_2

        # New posted message includes the '(revised from #N)' header.
        revised_post = mock_slack.post_message.call_args_list[-1].args[0]
        assert f"(revised from #{did_1})" in revised_post
        assert "claude-opus" in revised_post

        # ── Scenario 4: 👍 on the new (head) row confirms it
        row_new = db_conn.execute(
            "SELECT interpretation_ts FROM directives WHERE id=?", (did_2,),
        ).fetchone()
        interp_ts_2 = row_new[0]
        e2e_daemon._handle_directive_reaction("thumbsup", interp_ts_2)

        status_new = db_conn.execute(
            "SELECT status FROM directives WHERE id=?", (did_2,),
        ).fetchone()
        assert status_new[0] == "confirmed"

        # ── Scenario 5: 👍 on the OLD (superseded) row is rejected with a "react on newer" post
        posts_before = len(mock_slack.post_message.call_args_list)
        e2e_daemon._handle_directive_reaction("thumbsup", interp_ts_1)

        # Old row status must still be 'superseded' — not confirmed.
        status_old_after = db_conn.execute(
            "SELECT status FROM directives WHERE id=?", (did_1,),
        ).fetchone()
        assert status_old_after[0] == "superseded"

        # A "react on the newer message" post must have been added.
        new_posts = mock_slack.post_message.call_args_list[posts_before:]
        assert any(
            f"superseded by #{did_2}" in call.args[0]
            and "please react on the newer" in call.args[0]
            for call in new_posts
        )


class TestAwaitingChangesExplicitConfirm:
    """I-3 regression: 👍/👎 on an awaiting_changes directive must be an
    EXPLICIT confirm/reject that cancels the pending change request — both
    in the operator-facing Slack post and the brain notification — never a
    silent flip that leaves the brain waiting for feedback."""

    def test_thumbsup_on_awaiting_changes_confirms_with_explicit_cancel_message(
        self, daemon, db_conn
    ):
        ts = "1780078400.200001"
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES (?, 'src-ac', 'src', ?, 'awaiting_changes', ?)",
            (500, "Do the thing", ts),
        )
        db_conn.commit()

        daemon._handle_directive_reaction("thumbsup", ts)

        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (500,)).fetchone()
        assert row[0] == "confirmed"
        posted = [c.args[0] for c in daemon.slack.post_message.call_args_list]
        assert any("cancelling the pending change request" in m for m in posted), posted
        brain_msgs = [c.args[0] for c in daemon.brain.send_message.call_args_list]
        assert any("change request is cancelled" in m and "do NOT wait for feedback" in m for m in brain_msgs), brain_msgs

    def test_thumbsdown_on_awaiting_changes_rejects_with_explicit_cancel_message(
        self, daemon, db_conn
    ):
        ts = "1780078400.200002"
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES (?, 'src-ac2', 'src', ?, 'awaiting_changes', ?)",
            (501, "Do the other thing", ts),
        )
        db_conn.commit()

        daemon._handle_directive_reaction("thumbsdown", ts)

        row = db_conn.execute("SELECT status FROM directives WHERE id=?", (501,)).fetchone()
        assert row[0] == "rejected"
        posted = [c.args[0] for c in daemon.slack.post_message.call_args_list]
        assert any("cancelling the pending change request" in m for m in posted), posted
        # The brain was told "their next Slack message is the requested change"
        # — it MUST be told to stand down, or it will misinterpret the
        # operator's next message as feedback for this rejected directive.
        brain_msgs = [c.args[0] for c in daemon.brain.send_message.call_args_list]
        assert any(
            "change request is cancelled" in m and "do NOT wait for feedback" in m
            for m in brain_msgs
        ), brain_msgs


class TestChainWalkCapSlackSignal:
    """N-2 regression: hitting _walk_to_chain_head's safety cap must post an
    operator-visible Slack warning, not just a daemon log line."""

    def test_walk_cap_hit_posts_slack_warning(self, daemon, db_conn):
        ts = "1780078400.200003"
        # Build a 2-cycle: 600.superseded_by=601, 601.superseded_by=600
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'src-cyc', 'src', 'interp A', ?, ?, ?)",
            (600, "pending_confirmation", ts, 601),
        )
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'src-cyc2', 'src', 'interp B', ?, 'ts-cyc-b', ?)",
            (601, "pending_confirmation", 600),
        )
        # Both rows must be status='superseded' to route into the walk.
        db_conn.execute("UPDATE directives SET status='superseded' WHERE id IN (600, 601)")
        db_conn.commit()

        daemon._handle_directive_reaction("thumbsup", ts)

        posted = [c.args[0] for c in daemon.slack.post_message.call_args_list]
        assert any("safety cap" in m for m in posted), posted
        # R5-N1: the walk must NOT hand back a mid-cycle node — the caller
        # should fall into the corruption-fallback message rather than
        # pointing the operator at another broken row.
        assert not any("superseded by #601" in m for m in posted), posted
        assert any("please react on the current head" in m for m in posted), posted


class TestSummaryAwaitingChanges:
    def test_summary_includes_awaiting_changes_directives(self, daemon, db_conn):
        """R5-Q2: /summary must surface awaiting_changes — the state where
        the daemon is waiting on the OPERATOR's feedback."""
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES (?, 'src-sum', 'src', ?, 'awaiting_changes', 'ts-sum')",
            (900, "Directive needing my feedback"),
        )
        db_conn.commit()
        daemon.registry.get_running_workers.return_value = []

        daemon._handle_summary()

        posted = [c.args[0] for c in daemon.slack.post_message.call_args_list]
        assert any(
            "#900" in m and "Directive needing my feedback" in m and "Awaiting Your Feedback" in m
            for m in posted
        ), posted

    def test_walk_dangling_pointer_warns_and_returns_last_existing_id(
        self, daemon, db_conn
    ):
        """R3-Q1 regression: a superseded_by pointing at a NONEXISTENT row
        (dangling pointer) must post an operator-visible warning — like the
        cyclic-cap case — and the superseded-notice must reference the last
        EXISTING id, never the dangling one."""
        ts = "1780078400.200004"
        db_conn.execute(
            "INSERT INTO directives "
            "(id, source_ts, source_text, interpretation, status, interpretation_ts, superseded_by) "
            "VALUES (?, 'src-dang', 'src', 'interp D', 'superseded', ?, ?)",
            (700, ts, 999999),
        )
        db_conn.commit()

        daemon._handle_directive_reaction("thumbsup", ts)

        posted = [c.args[0] for c in daemon.slack.post_message.call_args_list]
        assert any("dangling" in m.lower() for m in posted), posted
        # The dangling id must never be presented to the operator as a target.
        assert not any("#999999" in m and "superseded by" in m for m in posted), posted
