"""
DESTRUCTIVE TESTS — DO NOT RUN WITH THE LIVE DAEMON

These tests send real SIGTERM signals during execution. When the test suite
runs alongside the live ironclaude daemon, signals can leak across process
groups and kill the daemon.

Run these manually, only when the daemon is not running:
    pytest tests/test_signal_handler_destructive.py
"""

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


@pytest.mark.destructive
class TestSelfSigtermCapturesOwnPid:
    def test_self_sigterm_logs_sender_pid(self):
        """Sending SIGTERM to self: logged message contains FROM pid=<our pid>."""
        log_messages = []

        def capture_warning(msg, *args, **kwargs):
            log_messages.append(msg % args if args else msg)

        with patch.object(main_module, "_handle_shutdown", return_value=None), \
             patch.object(main_module.logger, "warning", side_effect=capture_warning):
            main_module._install_sigaction_handler()
            #os.kill(os.getpid(), signal.SIGTERM) #commented out. You must uncomment before trying to use. THIS WILL KILL THE DAEMON

        assert any(f"FROM pid={os.getpid()}" in m for m in log_messages), (
            f"Expected 'FROM pid={os.getpid()}' in log messages: {log_messages}"
        )


@pytest.mark.destructive
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
            #proc.kill() #commented out. You must uncomment before trying to use. THIS WILL KILL THE DAEMON
            pytest.fail("Child process did not become ready within 5s")

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

        log_contents = log_file.read_text() if log_file.exists() else ""
        assert f"FROM pid={parent_pid}" in log_contents, (
            f"Expected 'FROM pid={parent_pid}' in log; got: {log_contents!r}"
        )
