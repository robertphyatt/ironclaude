"""Pytest configuration and shared fixtures."""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback
import types
from pathlib import Path

import pytest

collect_ignore = ["test_signal_handler_destructive.py"]

# ── Real-path tripwire ────────────────────────────────────────────────────────
# Occurrence #5 of "tests mutate real operator state". Per-site fixes recurred
# four times; the one conftest-scoped DENY (_guard_os_kill) held.


class RealHomeAccess(BaseException):
    """A test touched one of the operator's real IronClaude paths.

    BaseException, NOT Exception: main.py:1000-1001 and :1020-1021 are
    `except Exception as e: logger.warning(...)` — the exact handlers that kept
    this bug invisible for five occurrences. A RuntimeError tripwire would abort
    the operation and then die silently in them.
    """


_AUDITED_EVENTS = frozenset(
    {
        "open",
        "os.remove",
        "os.rename",
        "os.mkdir",
        "shutil.copyfile",
        "sqlite3.connect",
        "os.rmdir",
        "os.symlink",
        "os.link",
    }
)

_REAL_HOME = ""
_DENIED_REAL_PREFIXES: tuple = ()
_TRIP_LEDGER: list = []


def _real_path_tripwire(event, args):
    if event not in _AUDITED_EVENTS:
        return
    for arg in args:
        if not isinstance(arg, (str, bytes, os.PathLike)):
            continue
        try:
            text = os.fsdecode(arg)
        except (TypeError, ValueError):
            continue
        for prefix in _DENIED_REAL_PREFIXES:
            if text == prefix or text.startswith(prefix + os.sep):
                _TRIP_LEDGER.append(
                    (event, text, "".join(traceback.format_stack()))
                )
                raise RealHomeAccess(
                    f"Test touched the operator's real IronClaude path "
                    f"via {event!r}: {text}\n"
                    f"Production code resolved a path under the real home. Fix "
                    f"the resolver (step 3 of "
                    f"docs/plans/2026-07-31-home-redirect-test-isolation-design.md). "
                    f"Do NOT add an exemption."
                )


def _report_and_clear_trips(when):
    """Raise if anything tripped, then clear so the next test starts clean.

    Never blind-clear: a clear() without a check silently discards trips that
    occurred during collection or session-scoped setup, and harvest fidelity is
    this loop's entire product.
    """
    if not _TRIP_LEDGER:
        return
    trips = list(_TRIP_LEDGER)
    _TRIP_LEDGER.clear()
    entries = "\n".join(f"  {ev} -> {p}" for ev, p, _ in trips)
    raise AssertionError(
        f"Real IronClaude path(s) touched {when}:\n{entries}\n"
        f"Stack of first trip:\n{trips[0][2]}"
    )


def pytest_configure(config):
    """Freeze the real-home deny-set BEFORE any redirect fixture can run.

    Computed after redirection it would guard the fake home and protect nothing.
    """
    global _REAL_HOME, _DENIED_REAL_PREFIXES
    real_home = Path.home()
    _REAL_HOME = str(real_home)
    _DENIED_REAL_PREFIXES = (
        str(real_home / ".claude"),
        str(real_home / ".ironclaude"),
        str(real_home / ".claude.json"),
    )
    sys.addaudithook(_real_path_tripwire)


@pytest.fixture(scope="session")
def real_home():
    """The operator's actual home, captured before any redirect."""
    return _REAL_HOME


@pytest.fixture(scope="session")
def tripwire_error():
    """The tripwire exception class, for pytest.raises in the controls."""
    return RealHomeAccess


@pytest.fixture
def trip_ledger():
    """The live ledger — canary control only. Do NOT use it to clear a trip your
    test caused; fix the resolver instead."""
    return _TRIP_LEDGER


@pytest.fixture(autouse=True)
def _assert_no_real_path_trips():
    """Backstop for handlers that swallow even a BaseException.

    A bare `except:` or contextlib.suppress would absorb RealHomeAccess. The
    ledger records the trip regardless, so the test still fails here.
    """
    _report_and_clear_trips("before this test (collection or session setup)")
    yield
    _report_and_clear_trips("during this test")


# ── Fix ironclaude.plugins namespace shadowing ────────────────────────────────
# src/ironclaude/plugins.py (a module) shadows src/ironclaude/plugins/ (a package).
# Pre-register namespace entries in sys.modules so inline imports of
# ironclaude.plugins.scan.pipeline.* in test files resolve correctly.

_SRC = Path(__file__).parent.parent / "src"


def _ensure_namespace(name: str, path: Path) -> None:
    """Register a stub package in sys.modules if not already present."""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]  # type: ignore[attr-defined]
        mod.__package__ = name
        sys.modules[name] = mod


# Load plugins.py as the actual module, then set __path__ to make it also a package
_plugins_path = _SRC / "ironclaude/plugins.py"
_plugins_spec = importlib.util.spec_from_file_location("ironclaude.plugins", _plugins_path)
_plugins_mod = importlib.util.module_from_spec(_plugins_spec)  # type: ignore[arg-type]
_plugins_mod.__path__ = [str(_SRC / "ironclaude/plugins")]  # type: ignore[attr-defined]
sys.modules["ironclaude.plugins"] = _plugins_mod
_plugins_spec.loader.exec_module(_plugins_mod)  # type: ignore[union-attr]

# Now set up subpackage namespaces
_ensure_namespace("ironclaude.plugins.scan", _SRC / "ironclaude/plugins/scan")
_ensure_namespace(
    "ironclaude.plugins.scan.pipeline", _SRC / "ironclaude/plugins/scan/pipeline"
)

# Load orphan_remediation directly so its inline import in tests resolves.
_orm_path = _SRC / "ironclaude/plugins/scan/pipeline/orphan_remediation.py"
if _orm_path.exists():
    _orm_spec = importlib.util.spec_from_file_location(
        "ironclaude.plugins.scan.pipeline.orphan_remediation", _orm_path
    )
    _orm_mod = importlib.util.module_from_spec(_orm_spec)  # type: ignore[arg-type]
    sys.modules["ironclaude.plugins.scan.pipeline.orphan_remediation"] = _orm_mod
    _orm_spec.loader.exec_module(_orm_mod)  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def _guard_os_kill(monkeypatch):
    def _blocked_kill(pid, sig):
        raise RuntimeError(
            f"os.kill({pid!r}, {sig!r}) called without a mock. "
            f"Real signals are banned in tests — a MagicMock PID converts to 0 via __index__ "
            f"and kills the entire process group. "
            f"Add: monkeypatch.setattr(os, 'kill', lambda pid, sig: None) "
            f"or: with patch('os.kill', ...)"
        )

    def _blocked_killpg(pgid, sig):
        raise RuntimeError(
            f"os.killpg({pgid!r}, {sig!r}) called without a mock — "
            f"same ban applies to killpg. Mock it explicitly."
        )

    monkeypatch.setattr(os, "kill", _blocked_kill)
    if hasattr(os, "killpg"):
        monkeypatch.setattr(os, "killpg", _blocked_killpg)


@pytest.fixture(autouse=True)
def _isolate_ic_env(monkeypatch):
    """Prevent the developer's real IC_* environment from leaking into tests.

    OrchestratorTools.__init__ resolves brain_cwd / ollama config / machines
    from IC_* env vars BEFORE falling back to the injected `config` dict. On a
    machine running a live brain (IC_BRAIN_CWD set), that override made wiki/
    ledger tests write to and git-commit into the REAL brain repo, and the
    tmp_path assertions failed. Unset them so injected config is honored.
    """
    for var in (
        "IC_BRAIN_CWD",
        "IC_OLLAMA_CONFIG_PATH",
        "IC_MACHINES_CONFIG",
        "IC_LEDGER_PATH",
        "IC_LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _reset_ollama_breakers():
    """The Ollama circuit breaker is module-level global state with a real clock.
    Reset it before every test so no test contaminates another's URL breakers."""
    from ironclaude.ollama_client import _BREAKERS
    _BREAKERS.reset()
    yield
    _BREAKERS.reset()


@pytest.fixture(autouse=True)
def _isolate_fable_state(tmp_path, monkeypatch):
    """Never let the suite write the operator's real Fable-unavailability flag.

    BrainClient's real error path writes ~/.ironclaude/state/fable_unavailable.json
    on any 'fable' model failure, so a test injecting a model-unavailable error
    plants a real 24h blackout that silently downgrades tier-up plan reviews.
    Per-test patching missed TestBrainModelFallback (test_brain_client.py:211);
    this makes opting out impossible by default.

    setattr, not setenv: fable_availability._STATE_PATH is evaluated from
    IRONCLAUDE_FABLE_STATE_PATH once at import time, so setting the env var here
    would be too late and would silently do nothing.
    """
    from ironclaude import fable_availability

    monkeypatch.setattr(
        fable_availability, "_STATE_PATH", tmp_path / "fable_unavailable.json"
    )


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    """Point $HOME at a per-test fake home.

    Seeded with a .gitconfig because the wiki tests run real `git commit`, and a
    bare redirect strips git identity.

    IRONCLAUDE_HOME is deliberately NOT set: nothing reads it until paths.py
    exists (step 3), and paths.home() will fall back to Path.home(), which
    honors HOME.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    (fake_home / ".gitconfig").write_text(
        "[user]\n\tname = IronClaude Tests\n\temail = tests@ironclaude.invalid\n"
    )
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


@pytest.fixture
def base_config():
    def make_config():
        return {
            "clients": {
                "claude": {
                    "enabled": True,
                    "path": "claude",
                    "models": {
                        "haiku": "haiku",
                        "sonnet": "sonnet",
                        "opus": "opus",
                        "fable": "fable",
                    },
                },
                "codex": {
                    "enabled": False,
                    "path": "codex",
                    "models": {
                        "haiku": "gpt-5.6-luna",
                        "sonnet": "gpt-5.6-terra",
                        "opus": "gpt-5.6-sol",
                    },
                },
            },
            "roles": {
                "brain": {"preferred": "claude", "clients": ["claude"]},
                "worker": {"preferred": "claude", "clients": ["claude"]},
                "grader": {"preferred": "claude", "clients": ["claude"]},
                "advisor": {"preferred": "claude", "clients": ["claude"]},
            },
            "shadow_mode": False,
        }

    return make_config
