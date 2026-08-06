"""No-push Commander adapter for workspace-manager's internal JSON CLI."""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path
import subprocess
from typing import Any, Callable


class WorkspaceClientError(RuntimeError):
    """Workspace-manager transport or response failure."""


_COMMANDS = frozenset({"allocate", "bind", "finalize", "abandon", "reconcile"})


class WorkspaceClient:
    def __init__(
        self,
        plugin_root: str | Path,
        *,
        runner: Callable[..., Any] = subprocess.run,
        ssh_manager: Any | None = None,
        timeout: int = 60,
        commander_version: str | None = None,
        home_dir: str | Path | None = None,
    ) -> None:
        self._plugin_root = Path(plugin_root)
        self._runner = runner
        self._ssh_manager = ssh_manager
        self._timeout = timeout
        self._commander_version = commander_version or metadata.version("ironclaude-commander")
        self._home_dir = Path(home_dir) if home_dir is not None else Path.home()

    @staticmethod
    def _cli_path(plugin_root: str | Path) -> str:
        return str(Path(plugin_root) / "mcp-servers" / "workspace-manager" / "dist" / "cli.js")

    def set_ssh_manager(self, ssh_manager: Any) -> None:
        self._ssh_manager = ssh_manager

    @staticmethod
    def _decode(command: str, result: Any) -> dict[str, Any]:
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.returncode != 0:
            detail = stderr.strip() or stdout.strip() or "no diagnostic"
            raise WorkspaceClientError(f"workspace-manager {command} exited with exit {result.returncode}: {detail}")
        try:
            value = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WorkspaceClientError(f"workspace-manager {command} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise WorkspaceClientError(f"workspace-manager {command} JSON response must be an object")
        return value

    @staticmethod
    def _provider_layout(client_name: str) -> tuple[str, str]:
        if client_name == "claude":
            return ".claude", ".claude-plugin/plugin.json"
        if client_name == "codex":
            return ".codex", ".codex-plugin/plugin.json"
        raise WorkspaceClientError(f"unsupported workspace provider: {client_name}")

    @staticmethod
    def _base_version(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return value.split("+", 1)[0]

    def _select_discovered_root(self, client_name: str, evidence: list[dict[str, Any]]) -> str:
        matches = [
            item for item in evidence
            if item.get("base_version") == self._base_version(self._commander_version)
            and item.get("cli_exists") is True
            and item.get("manifest_valid") is True
        ]
        if len(matches) == 1:
            return str(matches[0]["plugin_root"])
        state = "zero" if not matches else "ambiguous"
        bounded = evidence[:20]
        raise WorkspaceClientError(
            f"workspace-manager installed runtime discovery found {state} matching roots "
            f"for provider={client_name} commander_version={self._commander_version}; "
            f"candidates={json.dumps(bounded, sort_keys=True, separators=(',', ':'))}"
        )

    def _local_discovery_evidence(self, client_name: str) -> list[dict[str, Any]]:
        cache_dir, manifest_rel = self._provider_layout(client_name)
        base = self._home_dir / cache_dir / "plugins/cache/ironclaude/ironclaude"
        roots = sorted((entry for entry in base.iterdir() if entry.is_dir()), key=lambda item: item.name) \
            if base.is_dir() else []
        evidence: list[dict[str, Any]] = []
        for root in roots:
            manifest_path = root / manifest_rel
            cli_path = Path(self._cli_path(root))
            item: dict[str, Any] = {
                "plugin_root": str(root),
                "manifest_path": str(manifest_path),
                "cli_path": str(cli_path),
                "cli_exists": cli_path.is_file(),
                "manifest_valid": False,
                "version": None,
                "base_version": None,
            }
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                version = manifest.get("version") if isinstance(manifest, dict) else None
                item.update({
                    "manifest_valid": isinstance(version, str) and bool(version),
                    "version": version,
                    "base_version": self._base_version(version),
                })
            except (OSError, json.JSONDecodeError) as exc:
                item["manifest_error"] = type(exc).__name__
            evidence.append(item)
        return evidence

    def _remote_discovery_evidence(self, client_name: str, ssh_host: str) -> list[dict[str, Any]]:
        if self._ssh_manager is None:
            raise WorkspaceClientError("ssh_host requires an SSH manager")
        cache_dir, manifest_rel = self._provider_layout(client_name)
        home_result = self._ssh_manager.run_argv(
            ssh_host, ["python3", "-c", "import os; print(os.path.expanduser('~'))"],
        )
        if home_result.returncode != 0 or not isinstance(home_result.stdout, str) or not home_result.stdout.strip():
            raise WorkspaceClientError("remote workspace runtime discovery could not resolve home directory")
        remote_home = home_result.stdout.strip()
        cache_base = f"{remote_home}/{cache_dir}/plugins/cache/ironclaude/ironclaude"
        list_result = self._ssh_manager.run_argv(
            ssh_host,
            ["find", cache_base, "-mindepth", "1", "-maxdepth", "1", "-type", "d", "-print"],
        )
        roots = [] if list_result.returncode != 0 or not isinstance(list_result.stdout, str) else sorted({
            line.strip() for line in list_result.stdout.splitlines()
            if line.strip().startswith(f"{cache_base}/") and "/" not in line.strip()[len(cache_base) + 1:]
        })
        evidence: list[dict[str, Any]] = []
        for root in roots:
            manifest_path = f"{root}/{manifest_rel}"
            cli_path = self._cli_path(root)
            manifest_result = self._ssh_manager.run_argv(ssh_host, ["cat", manifest_path])
            cli_result = self._ssh_manager.run_argv(ssh_host, ["test", "-f", cli_path])
            item: dict[str, Any] = {
                "plugin_root": root,
                "manifest_path": manifest_path,
                "cli_path": cli_path,
                "cli_exists": cli_result.returncode == 0,
                "manifest_valid": False,
                "version": None,
                "base_version": None,
            }
            if manifest_result.returncode == 0 and isinstance(manifest_result.stdout, str):
                try:
                    manifest = json.loads(manifest_result.stdout)
                    version = manifest.get("version") if isinstance(manifest, dict) else None
                    item.update({
                        "manifest_valid": isinstance(version, str) and bool(version),
                        "version": version,
                        "base_version": self._base_version(version),
                    })
                except json.JSONDecodeError:
                    item["manifest_error"] = "JSONDecodeError"
            else:
                item["manifest_error"] = "unreadable"
            evidence.append(item)
        return evidence

    def discover_installed_plugin_root(self, client_name: str, *, ssh_host: str | None = None) -> str:
        evidence = (
            self._remote_discovery_evidence(client_name, ssh_host)
            if ssh_host is not None
            else self._local_discovery_evidence(client_name)
        )
        return self._select_discovered_root(client_name, evidence)

    def _invoke(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        ssh_host: str | None = None,
        plugin_root: str | Path | None = None,
        remote_plugin_root: str | Path | None = None,
    ) -> dict[str, Any]:
        if command not in _COMMANDS:
            raise WorkspaceClientError(f"workspace-manager command is not allowed: {command}")
        if not isinstance(payload, dict):
            raise TypeError("workspace-manager payload must be a dict")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if ssh_host is None:
            if remote_plugin_root is not None:
                raise WorkspaceClientError("remote_plugin_root requires ssh_host")
            argv = ["node", self._cli_path(plugin_root or self._plugin_root), command, encoded]
            result = self._runner(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                shell=False,
            )
        else:
            if self._ssh_manager is None:
                raise WorkspaceClientError("ssh_host requires an SSH manager")
            if plugin_root is not None:
                raise WorkspaceClientError("plugin_root is local-only")
            if remote_plugin_root is None:
                raise WorkspaceClientError("ssh_host requires remote_plugin_root")
            argv = ["node", self._cli_path(remote_plugin_root), command, encoded]
            result = self._ssh_manager.run_argv(ssh_host, argv)
        return self._decode(command, result)

    def allocate(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        return self._invoke("allocate", payload, **transport)

    def bind(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        return self._invoke("bind", payload, **transport)

    def finalize(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        return self._invoke("finalize", payload, **transport)

    def abandon(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        return self._invoke("abandon", payload, **transport)

    def reconcile(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        return self._invoke("reconcile", payload, **transport)
