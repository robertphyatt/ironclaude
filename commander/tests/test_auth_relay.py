import io
from ironclaude.auth_relay import AuthRelay


class FakeProc:
    def __init__(self, lines=(), returncode=None):
        self.stdout = io.StringIO("".join(lines))
        self.stdin = io.StringIO()
        self.returncode = returncode
        self._exited = returncode is not None
        self.killed = False
    def poll(self): return self.returncode if self._exited else None
    def wait(self, timeout=None): self._exited = True; return self.returncode
    def _exit(self, rc): self.returncode = rc; self._exited = True
    def kill(self): self.killed = True; self._exit(-9)


class BlockingProc:
    """stdout.readline() blocks until kill() — proves start() never reads on the caller thread."""
    def __init__(self):
        import threading
        self._ev = threading.Event()
        self.stdin = io.StringIO()
        self.returncode = None
        outer = self
        class _Stdout:
            def readline(self_inner):
                outer._ev.wait()   # blocks the READER thread, not start()
                return ""
        self.stdout = _Stdout()
    def poll(self): return self.returncode
    def kill(self): self.returncode = -9; self._ev.set()


def _mk(proc, status="new@acct", now=None):
    return AuthRelay(spawn=lambda: proc, status=lambda: status, now=now or (lambda: 1000.0))


def _drain(relay):
    # the reader thread drains the fake stdout (StringIO EOFs immediately)
    relay._reader.join(timeout=1)


class TestAuthRelay:
    def test_start_is_nonblocking(self):
        relay = _mk(FakeProc(lines=["Visit https://claude.ai/oauth?x=1\n"]))
        assert relay.start() == {"state": "started"}
        assert relay.in_progress() is True

    def test_tick_relays_url_once(self):
        relay = _mk(FakeProc(lines=["Visit https://claude.ai/oauth?x=1 to sign in\n"]))
        relay.start(); _drain(relay)
        assert relay.tick() == {"state": "url", "url": "https://claude.ai/oauth?x=1"}
        assert relay.tick() is None   # still alive, URL already relayed

    def test_success_only_after_verify(self):
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        relay = _mk(proc, status="switched@acct")
        relay.start(); _drain(relay)
        assert relay.tick()["state"] == "url"
        proc._exit(0)
        assert relay.tick() == {"state": "success", "account": "switched@acct"}
        assert relay.in_progress() is False

    def test_verify_failed_when_status_none(self):
        # I1: status never confirms -> verify_failed after VERIFY_MAX_ATTEMPTS ticks (NO restart).
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        relay = _mk(proc, status=None)
        relay.start(); _drain(relay); relay.tick()   # url
        proc._exit(0)
        ev = None
        for _ in range(AuthRelay.VERIFY_MAX_ATTEMPTS + 2):
            ev = relay.tick()
            if ev is not None:
                break
        assert ev == {"state": "verify_failed"}
        assert relay.in_progress() is False

    def test_verify_retries_then_succeeds(self):
        # I1: a transient status flake on the first check must NOT produce a false verify_failed.
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        calls = {"n": 0}
        def flaky_status():
            calls["n"] += 1
            return "acct@x" if calls["n"] >= 2 else None   # first check flakes, second confirms
        relay = AuthRelay(spawn=lambda: proc, status=flaky_status, now=lambda: 1000.0)
        relay.start(); _drain(relay); relay.tick()   # url
        proc._exit(0)
        assert relay.tick() is None                                    # attempt 1 flakes -> verifying
        assert relay.tick() == {"state": "success", "account": "acct@x"}

    def test_abort_kills_and_resets(self):
        # M6: abort() kills the in-progress login (non-destructive) and clears state.
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        relay = _mk(proc)
        relay.start()
        relay.abort()
        assert proc.killed is True
        assert relay.in_progress() is False

    def test_stale_reader_ignored(self):
        # I2: a reader tagged with an older generation must not stamp the new session's _url.
        import io as _io
        relay = _mk(FakeProc(lines=[]))
        relay._gen = 5
        stale = type("P", (), {"stdout": _io.StringIO("stale https://claude.ai/OLD\n")})()
        relay._read_loop(stale, gen=4)   # gen 4 != current 5
        assert relay._url is None

    def test_already_logged_in_no_url(self):
        relay = _mk(FakeProc(lines=[], returncode=0), status="me@acct")
        relay.start(); _drain(relay)
        assert relay.tick() == {"state": "already_logged_in", "account": "me@acct"}
        assert relay.in_progress() is False

    def test_error_on_nonzero_exit(self):
        relay = _mk(FakeProc(lines=["boom\n"], returncode=2))
        relay.start(); _drain(relay)
        assert relay.tick()["state"] == "error"

    def test_timeout_kills(self):
        t = {"v": 1000.0}
        proc = FakeProc(lines=["https://claude.ai/x\n"])   # returncode None -> stays alive
        relay = _mk(proc, now=lambda: t["v"])
        relay.start(); _drain(relay); relay.tick()   # url
        t["v"] = 1000.0 + AuthRelay.TIMEOUT_S + 1
        assert relay.tick() == {"state": "timeout"}
        assert proc.killed is True

    def test_needs_code_after_reprompt(self):
        proc = FakeProc(lines=["https://claude.ai/x\n", "Paste code here if prompted >\n"])
        relay = _mk(proc)
        relay.start(); _drain(relay)
        assert relay.tick()["state"] == "url"
        relay.submit_code("CODE-1")
        reprompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
        relay._read_loop(reprompt, gen=relay._gen)
        assert relay.tick() == {"state": "needs_code"}
        assert relay._code_submitted_at is None

    def test_reprompt_during_submit_is_not_lost(self):
        proc = FakeProc(lines=["Paste code here if prompted >\n"])
        relay = _mk(proc)
        relay.start(); _drain(relay)

        class RacingStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)
            def flush(self): pass

        proc.stdin = RacingStdin()
        assert relay.submit_code("CODE-1") == "sent"
        assert relay.tick() == {"state": "needs_code"}
        assert relay._code_submitted_at is None

    def test_delayed_initial_prompt_during_submit_is_not_a_reprompt(self):
        proc = FakeProc(lines=[])
        relay = _mk(proc)
        relay.start(); _drain(relay)

        class InitialPromptStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)
            def flush(self): pass

        proc.stdin = InitialPromptStdin()
        assert relay.submit_code("CODE-1") == "sent"
        assert relay.tick() is None
        assert relay._code_submitted_at is not None

    def test_new_login_resets_initial_prompt_tracking(self):
        first = FakeProc(lines=["Paste code here if prompted >\n"], returncode=0)
        second = FakeProc(lines=[])
        procs = iter([first, second])
        relay = AuthRelay(spawn=lambda: next(procs), status=lambda: "acct", now=lambda: 1000.0)

        relay.start(); _drain(relay)
        assert relay._paste_prompt_seen is True
        assert relay.tick() == {"state": "already_logged_in", "account": "acct"}

        relay.start(); _drain(relay)

        class InitialPromptStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)
            def flush(self): pass

        second.stdin = InitialPromptStdin()
        assert relay.submit_code("CODE-2") == "sent"
        assert relay.tick() is None

    def test_first_prompt_before_submit_is_not_needs_code(self):
        proc = FakeProc(lines=["https://claude.ai/x\n", "Paste code here if prompted >\n"])
        relay = _mk(proc)
        relay.start(); _drain(relay)
        assert relay.tick() == {"state": "url", "url": "https://claude.ai/x"}
        assert relay.tick() is None   # first "Paste code here" (pre-submission) must not trigger needs_code

    def test_waiting_after_60s_since_submit(self):
        t = {"v": 1000.0}
        proc = FakeProc(lines=["https://claude.ai/x\n"])   # stays alive (returncode None)
        relay = _mk(proc, now=lambda: t["v"])
        relay.start(); _drain(relay); relay.tick()   # url
        relay.submit_code("CODE-1")
        t["v"] = 1000.0 + AuthRelay.WAITING_FEEDBACK_INTERVAL_S + 1
        assert relay.tick() == {"state": "waiting"}
        assert relay.tick() is None   # throttled — no repeat within the interval
        t["v"] += AuthRelay.WAITING_FEEDBACK_INTERVAL_S
        assert relay.tick() == {"state": "waiting"}

    def test_no_waiting_before_code_submitted(self):
        t = {"v": 1000.0}
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        relay = _mk(proc, now=lambda: t["v"])
        relay.start(); _drain(relay); relay.tick()   # url
        t["v"] = 1000.0 + AuthRelay.WAITING_FEEDBACK_INTERVAL_S + 1
        assert relay.tick() is None   # no code submitted yet — no waiting message

    def test_timeout_logs_error(self, caplog):
        t = {"v": 1000.0}
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        relay = _mk(proc, now=lambda: t["v"])
        relay.start(); _drain(relay); relay.tick()   # url
        t["v"] = 1000.0 + AuthRelay.TIMEOUT_S + 1
        with caplog.at_level("ERROR", logger="ironclaude.auth_relay"):
            assert relay.tick() == {"state": "timeout"}
        assert any("timed out" in r.message for r in caplog.records)

    def test_busy_when_in_progress(self):
        relay = _mk(FakeProc(lines=["https://claude.ai/x\n"]))
        relay.start()
        assert relay.start() == {"state": "busy"}

    def test_submit_code_states(self):
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        relay = _mk(proc)
        assert relay.submit_code("X") == "idle"     # not started
        relay.start()
        assert relay.submit_code("CODE-1") == "sent"
        assert "CODE-1" in proc.stdin.getvalue()

    def test_submit_code_failed_on_write_error(self):
        class BadStdin:
            def write(self, *_): raise OSError("stdin closed")
            def flush(self): pass
        proc = FakeProc(lines=["https://claude.ai/x\n"])
        proc.stdin = BadStdin()
        relay = _mk(proc)
        relay.start(); _drain(relay)
        assert relay.submit_code("x") == "failed"
        assert relay._code_submitted_at is None
        assert relay._last_feedback_ts is None
        assert relay._needs_code is False

    def test_submit_code_failed_on_flush_rolls_back_state(self):
        class FlushFailStdin:
            def write(self, value): return len(value)
            def flush(self): raise OSError("flush failed")
        proc = FakeProc(lines=[])
        proc.stdin = FlushFailStdin()
        relay = _mk(proc)
        relay.start(); _drain(relay)

        assert relay.submit_code("CODE-1") == "failed"
        assert relay._code_submitted_at is None
        assert relay._last_feedback_ts is None
        assert relay._needs_code is False

    def test_submit_code_flush_failure_preserves_concurrent_reprompt(self):
        proc = FakeProc(lines=["Paste code here if prompted >\n"])
        relay = _mk(proc)
        relay.start(); _drain(relay)

        class RePromptThenFlushFailStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)
            def flush(self): raise OSError("flush failed")

        proc.stdin = RePromptThenFlushFailStdin()
        assert relay.submit_code("CODE-1") == "failed"
        assert relay._code_submitted_at is None
        assert relay.tick() == {"state": "needs_code"}

    def test_start_returns_even_if_stdout_blocks(self):
        # Anti-theatre: if start() read on the caller thread it would block. Run start() under an
        # in-test watchdog so a regression FAILS the assertion instead of hanging the whole suite
        # (there is no pytest-timeout dependency).
        import threading
        proc = BlockingProc()
        relay = _mk(proc)
        result = {}
        t = threading.Thread(target=lambda: result.__setitem__("r", relay.start()))
        t.start(); t.join(timeout=5)
        proc.kill()   # release the reader thread so it can exit
        assert not t.is_alive(), "start() blocked on the caller thread"
        assert result.get("r") == {"state": "started"}
