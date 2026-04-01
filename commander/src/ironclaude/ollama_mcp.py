# src/ic/ollama_mcp.py
"""MCP server for Ollama model management.

Wraps the ollama CLI via subprocess to provide structured model management
tools. The OllamaTools class implements business logic separately from the
MCP transport layer. Tests call OllamaTools methods directly; the FastMCP
server wraps them for external consumption.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger("ironclaude.ollama_mcp")

_SAFE_MODEL_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9:._/-]*$')


def _validate_model_name(name: str) -> dict | None:
    """Return an error dict if name is empty or contains unsafe characters.

    Prevents names starting with '-' from being interpreted as CLI flags
    by the ollama argument parser (e.g., --help, --insecure, -f).
    """
    if not name or not _SAFE_MODEL_NAME_RE.fullmatch(name):
        return {"error": f"Invalid model name: {name!r}"}
    return None


class OllamaTools:
    """Business logic for Ollama model management MCP tools.

    All methods wrap ollama CLI commands via subprocess.run().
    Methods return structured dicts; errors return {"error": "message"}.
    """

    def list_models(self) -> list[dict] | dict:
        """List locally available models.

        Runs `ollama list` and parses the tabular output into a list of dicts
        with keys: name, size, modified.
        """
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return []

        models = []
        for line in lines[1:]:
            if not line.strip():
                continue
            # The output is column-aligned. Parse by splitting on multiple spaces.
            # Format: NAME  ID  SIZE  MODIFIED
            # SIZE can be "2.0 GB" (two tokens), MODIFIED can be "2 hours ago" (multiple tokens).
            # Strategy: split into tokens, first token is name, second is ID,
            # then find the size pattern (number + unit), rest is modified.
            parts = line.split()
            if len(parts) < 4:
                continue

            name = parts[0]
            # parts[1] is the ID hash
            # Find size: look for a number followed by GB/MB/KB
            size_idx = None
            for i in range(2, len(parts) - 1):
                try:
                    float(parts[i])
                    if parts[i + 1] in ("GB", "MB", "KB", "B"):
                        size_idx = i
                        break
                except ValueError:
                    continue

            if size_idx is not None:
                size = f"{parts[size_idx]} {parts[size_idx + 1]}"
                modified = " ".join(parts[size_idx + 2:])
            else:
                size = ""
                modified = ""

            models.append({
                "name": name,
                "size": size,
                "modified": modified,
            })

        return models

    def show_model(self, name: str) -> dict:
        """Show details about a specific model.

        Runs `ollama show <name>` and returns the raw output along with
        the model name.
        """
        err = _validate_model_name(name)
        if err is not None:
            return err
        result = subprocess.run(
            ["ollama", "show", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        return {
            "name": name,
            "raw": result.stdout,
        }

    def list_running(self) -> list[dict] | dict:
        """List currently running/loaded models.

        Runs `ollama ps` and parses the tabular output into a list of dicts
        with keys: name, size, processor, until.
        """
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return []

        models = []
        for line in lines[1:]:
            if not line.strip():
                continue

            parts = line.split()
            if len(parts) < 5:
                continue

            name = parts[0]
            # parts[1] is the ID hash
            # Find size: number followed by GB/MB/KB
            size_idx = None
            for i in range(2, len(parts) - 1):
                try:
                    float(parts[i])
                    if parts[i + 1] in ("GB", "MB", "KB", "B"):
                        size_idx = i
                        break
                except ValueError:
                    continue

            if size_idx is None:
                continue

            size = f"{parts[size_idx]} {parts[size_idx + 1]}"

            # After size, find processor: contains "%" (e.g., "100% GPU")
            # Then the rest is "until"
            remaining = parts[size_idx + 2:]
            # Processor field: everything up to and including the token after "%"
            processor_parts = []
            until_start = 0
            for i, token in enumerate(remaining):
                processor_parts.append(token)
                if "%" in token or token in ("GPU", "CPU"):
                    # Check if next token is GPU/CPU
                    if i + 1 < len(remaining) and remaining[i + 1] in ("GPU", "CPU"):
                        processor_parts.append(remaining[i + 1])
                        until_start = i + 2
                        break
                    until_start = i + 1
                    break

            processor = " ".join(processor_parts)
            until = " ".join(remaining[until_start:])

            models.append({
                "name": name,
                "size": size,
                "processor": processor,
                "until": until,
            })

        return models

    def pull_model(self, name: str) -> dict:
        """Pull a model from the Ollama registry.

        Runs `ollama pull <name>` with a 600-second timeout for large models.
        """
        err = _validate_model_name(name)
        if err is not None:
            return err
        result = subprocess.run(
            ["ollama", "pull", name],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        return {
            "success": True,
            "name": name,
        }

    def remove_model(self, name: str) -> dict:
        """Remove a locally stored model.

        Runs `ollama rm <name>`.
        """
        err = _validate_model_name(name)
        if err is not None:
            return err
        result = subprocess.run(
            ["ollama", "rm", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        return {
            "success": True,
            "name": name,
        }

    def create_model(
        self,
        name: str,
        from_model: str,
        num_ctx: int | None = None,
        system: str | None = None,
    ) -> dict:
        """Create a custom model with a Modelfile.

        Generates a Modelfile with FROM, optional PARAMETER num_ctx, and
        optional SYSTEM directives. Writes to a tempfile, runs
        `ollama create <name> -f <tempfile>`, then cleans up.
        """
        err = _validate_model_name(name)
        if err is not None:
            return err
        # Validate from_model to prevent Modelfile directive injection
        from_model = from_model.replace("\n", "").replace("\r", "")
        if not re.fullmatch(r"^[a-zA-Z0-9:._/-]+$", from_model):
            return {"error": f"Invalid model name: {from_model!r}"}

        # Build Modelfile content
        lines = [f"FROM {from_model}"]
        if num_ctx is not None:
            lines.append(f"PARAMETER num_ctx {num_ctx}")
        if system is not None:
            system = system.replace("\n", "").replace("\r", "")
            lines.append(f"SYSTEM {system}")
        modelfile_content = "\n".join(lines) + "\n"

        # Write to tempfile
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix="_Modelfile", delete=False, prefix="ollama_"
        )
        tmp.write(modelfile_content)
        tmp.close()
        tmp_path = tmp.name

        try:
            result = subprocess.run(
                ["ollama", "create", name, "-f", tmp_path],
                capture_output=True,
                text=True,
            )
        finally:
            os.unlink(tmp_path)

        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        return {
            "success": True,
            "name": name,
        }


def create_ollama_mcp_server() -> "FastMCP":
    """Create and configure the FastMCP server wrapping OllamaTools."""
    from mcp.server.fastmcp import FastMCP

    tools = OllamaTools()
    mcp = FastMCP("ollama")

    @mcp.tool()
    def list_models() -> str:
        """List locally available Ollama models."""
        result = tools.list_models()
        return json.dumps(result, indent=2)

    @mcp.tool()
    def show_model(name: str) -> str:
        """Show details about a specific Ollama model."""
        result = tools.show_model(name)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def list_running() -> str:
        """List currently running/loaded Ollama models."""
        result = tools.list_running()
        return json.dumps(result, indent=2)

    @mcp.tool()
    def pull_model(name: str) -> str:
        """Pull a model from the Ollama registry."""
        result = tools.pull_model(name)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def remove_model(name: str) -> str:
        """Remove a locally stored Ollama model."""
        result = tools.remove_model(name)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def create_model(
        name: str,
        from_model: str,
        num_ctx: int | None = None,
        system: str | None = None,
    ) -> str:
        """Create a custom Ollama model with a Modelfile."""
        result = tools.create_model(name, from_model, num_ctx, system)
        return json.dumps(result, indent=2)

    return mcp
