# tests/test_signal_handler.py
"""Tests for SA_SIGINFO-based signal handler in ironclaude daemon."""

import os
import signal
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


class TestRogueSigtermRespawner:
    """_handle_shutdown must respect _sigterm_trusted when setting _clean_shutdown."""

    def _null_daemon(self, main_module):
        """Null out _daemon so _handle_shutdown doesn't attempt brain shutdown."""
        original = main_module._daemon
        main_module._daemon = None
        return original

    def test_rogue_sigterm_leaves_clean_shutdown_false(self):
        """_handle_shutdown with _sigterm_trusted=False must NOT set _clean_shutdown=True."""
        original_daemon = self._null_daemon(main_module)
        original_trusted = main_module._sigterm_trusted
        original_clean = main_module._clean_shutdown
        try:
            main_module._sigterm_trusted = False
            main_module._clean_shutdown = False
            main_module._handle_shutdown(signal.SIGTERM, None)
            assert main_module._clean_shutdown is False, (
                f"Rogue SIGTERM must NOT set _clean_shutdown=True; got {main_module._clean_shutdown}"
            )
        finally:
            main_module._sigterm_trusted = original_trusted
            main_module._clean_shutdown = original_clean
            main_module._daemon = original_daemon

    def test_trusted_sigterm_sets_clean_shutdown_true(self):
        """_handle_shutdown with _sigterm_trusted=True sets _clean_shutdown=True."""
        original_daemon = self._null_daemon(main_module)
        original_trusted = main_module._sigterm_trusted
        original_clean = main_module._clean_shutdown
        try:
            main_module._sigterm_trusted = True
            main_module._clean_shutdown = False
            main_module._handle_shutdown(signal.SIGTERM, None)
            assert main_module._clean_shutdown is True, (
                f"Trusted SIGTERM must set _clean_shutdown=True; got {main_module._clean_shutdown}"
            )
        finally:
            main_module._sigterm_trusted = original_trusted
            main_module._clean_shutdown = original_clean
            main_module._daemon = original_daemon

    def test_sigterm_trusted_resets_to_true_after_handle_shutdown(self):
        """_sigterm_trusted resets to True after each _handle_shutdown call."""
        original_daemon = self._null_daemon(main_module)
        original_trusted = main_module._sigterm_trusted
        original_clean = main_module._clean_shutdown
        try:
            main_module._sigterm_trusted = False
            main_module._handle_shutdown(signal.SIGTERM, None)
            assert main_module._sigterm_trusted is True, (
                "_sigterm_trusted must reset to True after _handle_shutdown"
            )
        finally:
            main_module._sigterm_trusted = original_trusted
            main_module._clean_shutdown = original_clean
            main_module._daemon = original_daemon
