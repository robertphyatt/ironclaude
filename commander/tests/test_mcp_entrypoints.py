"""The research/ollama MCP servers must actually SERVE when launched as subprocesses.

BrainClient registers them as `sys.executable <module>.py` (brain_client.py:716-725). Without
a `__main__` entrypoint each process defines its factory and exits 0, so the Brain silently
loses those tools — the defect these tests pin.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# conftest.py installs an AUTOUSE fixture that replaces os.kill with a raising guard.
# Popen.kill()/terminate() route through os.kill, so reaping a LIVE child would raise
# inside our cleanup and orphan a real MCP server. Capture the real function at import
# time (before that fixture patches it) for the fallback path below.
_REAL_OS_KILL = os.kill

import ironclaude.ollama_mcp as ollama_mcp  # noqa: E402
import ironclaude.research_mcp as research_mcp  # noqa: E402

MODULES = [
    pytest.param(research_mcp, "create_research_mcp_server", id="research"),
    pytest.param(ollama_mcp, "create_ollama_mcp_server", id="ollama"),
]


class TestEntrypointExists:
    @pytest.mark.parametrize("module,factory_name", MODULES)
    def test_main_runs_the_created_server(self, module, factory_name, monkeypatch):
        ran = []

        class _Recorder:
            def run(self, *args, **kwargs):
                ran.append((args, kwargs))

        monkeypatch.setattr(module, factory_name, lambda *a, **k: _Recorder())
        module.main()
        assert ran, f"{module.__name__}.main() must call .run() on the created server"


class TestServesInsteadOfExiting:
    """The behavioural test that would have caught the production bug: launched exactly
    as BrainClient launches it, the process must STILL BE RUNNING (waiting on stdin)
    rather than having exited immediately."""

    @pytest.mark.parametrize("module,factory_name", MODULES)
    def test_process_stays_alive_serving_stdio(self, module, factory_name):
        module_path = Path(module.__file__)
        proc = subprocess.Popen(
            [sys.executable, str(module_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(2.0)
            status = proc.poll()
            assert status is None, (
                f"{module_path.name} exited with {status} instead of serving stdio; "
                "an MCP server subprocess must stay alive waiting for input"
            )
        finally:
            # An MCP stdio server exits on stdin EOF, so closing stdin reaps it WITHOUT
            # a signal — which matters because conftest's autouse guard blocks os.kill.
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.kill = _REAL_OS_KILL   # monkeypatch restores its own patch at teardown
                proc.kill()
                proc.wait(timeout=10)
            finally:
                for stream in (proc.stdout, proc.stderr):
                    if stream:
                        stream.close()


class TestZombieCleanupRemoved:
    """`_cleanup_zombie_mcp_processes` never fires on macOS: `os.kill(ppid, 0)` on a
    launchd-reparented orphan always succeeds, so the orphan check always skips. Dead
    code — deleted along with its `_MCP_CLEANUP_PATTERNS` helper list."""

    def test_zombie_cleanup_method_and_pattern_list_are_gone(self):
        import ironclaude.orchestrator_mcp as om
        from ironclaude.orchestrator_mcp import OrchestratorTools

        assert not hasattr(OrchestratorTools, "_cleanup_zombie_mcp_processes")
        assert not hasattr(om, "_MCP_CLEANUP_PATTERNS")
