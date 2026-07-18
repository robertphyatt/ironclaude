"""Non-blocking relay driving `claude auth login`, relaying its URL to Slack.

A background thread drains the subprocess stdout so the single-threaded daemon
poll loop NEVER blocks on a login that waits silently, and the pipe can't fill.
start() spawns + starts the reader and returns immediately. tick() (called each
poll cycle) yields, in order: a one-time {"url"} when the reader captures the
sign-in URL; then on exit {"success"} (ONLY if `claude auth status` confirms an
account — the daemon restarts only then), {"verify_failed"} (exited 0 but status
could not confirm — NO restart), {"already_logged_in"} (exited 0 without ever
emitting a URL), {"error"} (non-zero), or {"timeout"} (killed; an incomplete
login never replaces the credential). submit_code() feeds a device-code. All
external effects (spawn, status, clock) are injected so tests never run a real
login. Mechanic-independent: works for localhost auto-detect and device-code.

Loop-2 probe (2026-07-15) confirmed the live CLI uses the DEVICE-CODE / paste-back
mechanic (URL on stdout, `Paste code here if prompted >`, hosted redirect_uri),
so the submit_code/stdin path is the exercised one; the exit-0-on-own path stays
as harmless dead-safe coverage.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger("ironclaude.auth_relay")

_URL_RE = re.compile(r"https?://\S+")


def _default_spawn() -> subprocess.Popen:
    return subprocess.Popen(
        ["claude", "auth", "login", "--claudeai"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )


def _default_status() -> Optional[str]:
    # Field names verified empirically (login-investigation findings Q2): `claude auth status --json`
    # returns a top-level {"loggedIn": bool, "email": str, "authMethod": ...}.
    try:
        import json
        out = subprocess.run(["claude", "auth", "status", "--json"],
                             capture_output=True, text=True, timeout=5).stdout
        data = json.loads(out)
        return data.get("email") if data.get("loggedIn") else None
    except Exception as exc:
        logger.debug("status check failed: %s", exc)
        return None


class AuthRelay:
    TIMEOUT_S = 300           # bounded so a hung login can't wedge the switch; the kill is non-destructive
    VERIFY_MAX_ATTEMPTS = 3   # re-check `claude auth status` across up to N ticks before verify_failed
    WAITING_FEEDBACK_INTERVAL_S = 60   # throttle for periodic "still working" notice after a code is submitted

    def __init__(self, spawn: Callable[[], subprocess.Popen] = _default_spawn,
                 status: Callable[[], Optional[str]] = _default_status,
                 now: Callable[[], float] = time.time):
        self._spawn = spawn
        self._status = status
        self._now = now
        self._proc: Optional[subprocess.Popen] = None
        self._started = 0.0
        self._lock = threading.Lock()
        self._buf: List[str] = []
        self._url: Optional[str] = None
        self._url_relayed = False
        self._reader: Optional[threading.Thread] = None
        self._gen = 0             # session generation — a stale reader from a prior login must
                                  # never write into a new session's state (review I2)
        self._verifying = False   # login exited 0 with a URL; confirming the account (review I1)
        self._verify_attempts = 0
        self._code_submitted_at: Optional[float] = None   # set by submit_code(); basis for the waiting-timer
        self._last_feedback_ts: Optional[float] = None     # throttles repeated "waiting" events
        self._needs_code = False                           # one-shot: a re-prompt was seen after submission
        self._paste_prompt_seen = False                    # distinguishes initial request from rejected-code re-prompt

    def in_progress(self) -> bool:
        return self._proc is not None or self._verifying

    def _read_loop(self, proc, gen: int) -> None:
        try:
            if not proc.stdout:
                return
            for line in iter(proc.stdout.readline, ""):
                with self._lock:
                    if gen != self._gen:
                        return   # a newer session has started — this reader is stale, do not bleed
                    self._buf.append(line)
                    if self._url is None:
                        m = _URL_RE.search(line)
                        if m:
                            self._url = m.group(0)
                    if "Paste code here" in line:
                        if self._paste_prompt_seen and self._code_submitted_at is not None:
                            self._needs_code = True   # CLI re-prompted after a code was already sent
                        self._paste_prompt_seen = True
        except Exception as exc:
            logger.debug("reader loop ended: %s", exc)

    def start(self) -> dict:
        if self.in_progress():
            return {"state": "busy"}
        with self._lock:
            self._gen += 1        # bump the generation FIRST so any lingering prior reader is invalidated,
            gen = self._gen       # then reset the shared state under the same lock (closes the I2 race)
            self._buf = []
            self._url = None
            self._url_relayed = False
            self._code_submitted_at = None
            self._last_feedback_ts = None
            self._needs_code = False
            self._paste_prompt_seen = False
        proc = self._spawn()
        self._proc = proc
        self._started = self._now()
        self._verifying = False
        self._verify_attempts = 0
        self._reader = threading.Thread(target=self._read_loop, args=(proc, gen), daemon=True)
        self._reader.start()
        return {"state": "started"}   # non-blocking; the URL is relayed by tick()

    def submit_code(self, code: str) -> str:
        """'idle' if no login subprocess is live, 'sent' on write, 'failed' on write error."""
        if self._proc is None or self._proc.stdin is None:
            return "idle"
        submitted_at = self._now()
        with self._lock:
            self._code_submitted_at = submitted_at
            self._last_feedback_ts = None
            self._needs_code = False
        try:
            self._proc.stdin.write(code + "\n")
            self._proc.stdin.flush()
            return "sent"
        except Exception as exc:
            with self._lock:
                if self._code_submitted_at == submitted_at:
                    self._code_submitted_at = None
                    self._last_feedback_ts = None
            logger.warning("submit_code failed: %s", exc)
            return "failed"

    def tick(self) -> Optional[dict]:
        # Verify phase: the login exited 0 having emitted a URL (a real sign-in). Confirm the
        # (possibly new) account across up to VERIFY_MAX_ATTEMPTS ticks so a transient
        # `claude auth status` flake doesn't produce a false verify_failed. (review I1)
        if self._verifying:
            account = self._status()
            if account:
                self._verifying = False
                return {"state": "success", "account": account}
            self._verify_attempts += 1
            if self._verify_attempts >= self.VERIFY_MAX_ATTEMPTS:
                self._verifying = False
                return {"state": "verify_failed"}
            return None
        if self._proc is None:
            return None
        proc = self._proc
        with self._lock:
            url = self._url
        if url and not self._url_relayed:
            self._url_relayed = True
            return {"state": "url", "url": url}
        with self._lock:
            if self._needs_code:
                self._needs_code = False
                self._code_submitted_at = None   # pause the waiting-timer until a fresh code is sent
                return {"state": "needs_code"}
        rc = proc.poll()
        if rc is not None:
            if self._reader is not None:
                self._reader.join(timeout=1)   # drain final output before classifying (closes the url/exit race)
            with self._lock:
                had_url = self._url is not None
            self._proc = None
            if rc == 0:
                if not had_url:
                    return {"state": "already_logged_in", "account": self._status()}
                # A real sign-in completed — verify the account (this call is attempt 1).
                account = self._status()
                if account:
                    return {"state": "success", "account": account}
                self._verifying = True
                self._verify_attempts = 1
                return None   # keep verifying on the next tick(s)
            return {"state": "error", "detail": self._tail()}
        if self._now() - self._started > self.TIMEOUT_S:
            logger.error("AuthRelay: login timed out after %ds — subprocess killed", self.TIMEOUT_S)
            try:
                proc.kill()
                proc.wait(timeout=5)   # reap the child so it doesn't linger as a zombie
            except Exception:
                pass
            if self._reader is not None:
                self._reader.join(timeout=1)
            self._proc = None
            return {"state": "timeout"}
        with self._lock:
            if self._code_submitted_at is not None:
                now = self._now()
                since_submit = now - self._code_submitted_at
                since_feedback = (now - self._last_feedback_ts) if self._last_feedback_ts else since_submit
                if since_submit > self.WAITING_FEEDBACK_INTERVAL_S and since_feedback >= self.WAITING_FEEDBACK_INTERVAL_S:
                    self._last_feedback_ts = now
                    return {"state": "waiting"}
        return None

    def abort(self) -> None:
        """Kill an in-progress login (e.g., before an external daemon restart). Non-destructive:
        an incomplete login never replaces the credential, and the reader is invalidated so it
        can't bleed into a later session. (review M6)"""
        proc = self._proc
        self._proc = None
        self._verifying = False
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        with self._lock:
            self._gen += 1   # invalidate the reader
        if self._reader is not None:
            self._reader.join(timeout=1)

    def _tail(self) -> str:
        with self._lock:
            return "".join(self._buf[-10:])[:500]
