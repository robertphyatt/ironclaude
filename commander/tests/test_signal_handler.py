# tests/test_signal_handler.py
"""Tests for SA_SIGINFO-based signal handler in ironclaude daemon."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

import pytest

import ironclaude.main as main_module


@pytest.fixture(autouse=True)
def restore_signal_handlers():
    """Restore signal handlers to SIG_DFL after each test."""
    yield
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    main_module._sigaction_callback = None


class TestInstallNoError:
    def test_install_succeeds_and_sets_callback(self):
        """_install_sigaction_handler installs without error and sets module-level callback."""
        with patch.object(main_module, "_handle_shutdown", return_value=None):
            main_module._install_sigaction_handler()
        assert main_module._sigaction_callback is not None


class TestSelfSigtermCapturesOwnPid:
    def test_self_sigterm_logs_sender_pid(self):
        """Sending SIGTERM to self: logged message contains FROM pid=<our pid>."""
        log_messages = []

        def capture_warning(msg, *args, **kwargs):
            log_messages.append(msg % args if args else msg)

        with patch.object(main_module, "_handle_shutdown", return_value=None), \
             patch.object(main_module.logger, "warning", side_effect=capture_warning):
            main_module._install_sigaction_handler()
            os.kill(os.getpid(), signal.SIGTERM)

        assert any(f"FROM pid={os.getpid()}" in m for m in log_messages), (
            f"Expected 'FROM pid={os.getpid()}' in log messages: {log_messages}"
        )


class TestFallbackOnSigactionFailure:
    def test_fallback_to_signal_signal_on_error(self):
        """If ctypes.CDLL raises, falls back to signal.signal for both SIGTERM and SIGINT."""
        registered = {}
        warnings = []

        def fake_signal(signum, handler):
            registered[signum] = handler

        with patch("ctypes.CDLL", side_effect=OSError("libc not found")), \
             patch.object(main_module.signal, "signal", side_effect=fake_signal), \
             patch.object(main_module.logger, "warning", side_effect=lambda m: warnings.append(m)):
            main_module._install_sigaction_handler()

        assert signal.SIGTERM in registered, "SIGTERM not registered in fallback"
        assert signal.SIGINT in registered, "SIGINT not registered in fallback"
        assert registered[signal.SIGTERM] is main_module._handle_shutdown
        assert registered[signal.SIGINT] is main_module._handle_shutdown
        assert any("sigaction setup failed" in w for w in warnings), (
            f"Expected 'sigaction setup failed' warning; got: {warnings}"
        )


class TestCrossProcessPidCapture:
    def test_cross_process_sender_pid_logged(self, tmp_path):
        """Parent sends SIGTERM to child; child's log contains FROM pid=<parent pid>."""
        log_file = tmp_path / "signal_log.txt"
        ready_file = tmp_path / "ready"
        parent_pid = os.getpid()

        src_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src",
        )

        script = textwrap.dedent(f"""\
            import os, signal, sys
            sys.path.insert(0, {repr(src_path)})
            import ironclaude.main as main_module
            from unittest.mock import patch

            log_messages = []

            def capture_warning(msg, *args, **kwargs):
                log_messages.append(msg % args if args else msg)

            with patch.object(main_module, "_handle_shutdown", return_value=None), \\
                 patch.object(main_module.logger, "warning", side_effect=capture_warning):
                main_module._install_sigaction_handler()
                open({repr(str(ready_file))}, "w").close()
                signal.pause()

            with open({repr(str(log_file))}, "w") as f:
                for msg in log_messages:
                    f.write(msg + "\\n")
        """)

        script_file = tmp_path / "child.py"
        script_file.write_text(script)

        proc = subprocess.Popen([sys.executable, str(script_file)])

        for _ in range(50):
            if ready_file.exists():
                break
            time.sleep(0.1)
        else:
            proc.kill()
            pytest.fail("Child process did not become ready within 5s")

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        log_contents = log_file.read_text() if log_file.exists() else ""
        assert f"FROM pid={parent_pid}" in log_contents, (
            f"Expected 'FROM pid={parent_pid}' in log; got: {log_contents!r}"
        )
