# tests/test_ollama_mcp.py
"""Tests for the Ollama management MCP server business logic."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from ironclaude.ollama_mcp import OllamaTools


@pytest.fixture
def tools():
    """Create an OllamaTools instance."""
    return OllamaTools()


class TestListModels:
    def test_returns_parsed_models(self, tools):
        """list_models parses ollama list output into structured dicts."""
        fake_output = (
            "NAME                    ID              SIZE      MODIFIED\n"
            "llama3.2:latest         a80c4f17acd5    2.0 GB    2 hours ago\n"
            "qwen2.5-coder:7b       2b0496514b09    4.7 GB    3 days ago\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_output
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tools.list_models()

        mock_run.assert_called_once_with(
            ["ollama", "list"],
            capture_output=True,
            text=True,
        )
        assert len(result) == 2
        assert result[0]["name"] == "llama3.2:latest"
        assert result[0]["size"] == "2.0 GB"
        assert result[0]["modified"] == "2 hours ago"
        assert result[1]["name"] == "qwen2.5-coder:7b"
        assert result[1]["size"] == "4.7 GB"

    def test_handles_error(self, tools):
        """list_models returns error dict on failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: could not connect to ollama"

        with patch("subprocess.run", return_value=mock_result):
            result = tools.list_models()

        assert "error" in result
        assert "could not connect to ollama" in result["error"]


class TestShowModel:
    def test_returns_model_details(self, tools):
        """show_model returns parsed model details."""
        fake_output = (
            "  Model\n"
            "    architecture    llama\n"
            "    parameters      8.0B\n"
            "    quantization    Q4_0\n"
            "\n"
            "  Parameters\n"
            "    stop    <|start_header_id|>\n"
            "\n"
            "  License\n"
            "    LLAMA 3.2 COMMUNITY LICENSE\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_output
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tools.show_model("llama3.2:latest")

        mock_run.assert_called_once_with(
            ["ollama", "show", "llama3.2:latest"],
            capture_output=True,
            text=True,
        )
        assert result["name"] == "llama3.2:latest"
        assert "raw" in result
        assert "llama" in result["raw"]


class TestListRunning:
    def test_returns_running_models(self, tools):
        """list_running parses ollama ps output into structured dicts."""
        fake_output = (
            "NAME                ID              SIZE      PROCESSOR    UNTIL\n"
            "llama3.2:latest     a80c4f17acd5    5.1 GB    100% GPU     4 minutes from now\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_output
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tools.list_running()

        mock_run.assert_called_once_with(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
        )
        assert len(result) == 1
        assert result[0]["name"] == "llama3.2:latest"
        assert result[0]["size"] == "5.1 GB"
        assert result[0]["processor"] == "100% GPU"
        assert result[0]["until"] == "4 minutes from now"


class TestPullModel:
    def test_pulls_successfully(self, tools):
        """pull_model returns success on clean pull."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "pulling manifest\npulling a80c4f17acd5... 100%\nverifying sha256 digest\nwriting manifest\nsuccess\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tools.pull_model("llama3.2:latest")

        mock_run.assert_called_once_with(
            ["ollama", "pull", "llama3.2:latest"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result["success"] is True
        assert result["name"] == "llama3.2:latest"

    def test_handles_pull_error(self, tools):
        """pull_model returns error dict on failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: pull model manifest: file does not exist"

        with patch("subprocess.run", return_value=mock_result):
            result = tools.pull_model("nonexistent:model")

        assert "error" in result
        assert "file does not exist" in result["error"]


class TestRemoveModel:
    def test_removes_successfully(self, tools):
        """remove_model returns success on clean removal."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "deleted 'llama3.2:latest'\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tools.remove_model("llama3.2:latest")

        mock_run.assert_called_once_with(
            ["ollama", "rm", "llama3.2:latest"],
            capture_output=True,
            text=True,
        )
        assert result["success"] is True
        assert result["name"] == "llama3.2:latest"


class TestCreateModel:
    def test_creates_with_modelfile(self, tools):
        """create_model writes a Modelfile, runs ollama create, and cleans up."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("ironclaude.ollama_mcp.tempfile.NamedTemporaryFile") as mock_tmpfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/Modelfile_abc123"
                mock_tmpfile.return_value = mock_file

                with patch("ironclaude.ollama_mcp.os.unlink") as mock_unlink:
                    result = tools.create_model(
                        name="my-model",
                        from_model="llama3.2:latest",
                        num_ctx=8192,
                        system="You are a helpful assistant.",
                    )

                    mock_unlink.assert_called_once_with("/tmp/Modelfile_abc123")

        mock_run.assert_called_once_with(
            ["ollama", "create", "my-model", "-f", "/tmp/Modelfile_abc123"],
            capture_output=True,
            text=True,
        )
        assert result["success"] is True
        assert result["name"] == "my-model"

    def test_handles_create_error(self, tools):
        """create_model returns error dict on failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: model 'nonexistent' not found"

        with patch("subprocess.run", return_value=mock_result):
            with patch("ironclaude.ollama_mcp.tempfile.NamedTemporaryFile") as mock_tmpfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/Modelfile_abc123"
                mock_tmpfile.return_value = mock_file

                with patch("ironclaude.ollama_mcp.os.unlink"):
                    result = tools.create_model(
                        name="bad-model",
                        from_model="nonexistent",
                    )

        assert "error" in result
        assert "not found" in result["error"]


class TestCreateModelModelfile:
    def test_generates_correct_modelfile(self, tools):
        """Verify Modelfile content includes FROM, PARAMETER num_ctx, and SYSTEM."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success\n"
        mock_result.stderr = ""

        written_content = None

        with patch("subprocess.run", return_value=mock_result):
            with patch("ironclaude.ollama_mcp.tempfile.NamedTemporaryFile") as mock_tmpfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/Modelfile_test"
                mock_tmpfile.return_value = mock_file

                def capture_write(content):
                    nonlocal written_content
                    written_content = content

                mock_file.write.side_effect = capture_write

                with patch("ironclaude.ollama_mcp.os.unlink"):
                    tools.create_model(
                        name="custom-model",
                        from_model="llama3.2:latest",
                        num_ctx=4096,
                        system="You are a pirate.",
                    )

        assert written_content is not None, "Modelfile content was never written"
        assert "FROM llama3.2:latest" in written_content
        assert "PARAMETER num_ctx 4096" in written_content
        assert "SYSTEM You are a pirate." in written_content


class TestCreateModelSecurity:
    """RED tests for M1 Modelfile directive injection in create_model.

    Primary RED signal: mock_run.assert_not_called() fails before the fix
    (subprocess.run IS called without from_model validation) and passes
    after (validation short-circuits before the subprocess call).
    """

    def test_blocks_newline_injection(self, tools):
        """create_model rejects from_model containing a newline."""
        with patch("subprocess.run") as mock_run:
            result = tools.create_model(
                name="evil",
                from_model="llama3.2:latest\nFROM scratch",
            )
        assert isinstance(result, dict)
        assert "error" in result
        mock_run.assert_not_called()

    def test_blocks_carriage_return(self, tools):
        """create_model rejects from_model containing a carriage return."""
        with patch("subprocess.run") as mock_run:
            result = tools.create_model(
                name="evil",
                from_model="llama3.2:latest\rFROM scratch",
            )
        assert isinstance(result, dict)
        assert "error" in result
        mock_run.assert_not_called()

    def test_blocks_invalid_chars(self, tools):
        """create_model rejects from_model with spaces or special chars."""
        with patch("subprocess.run") as mock_run:
            result = tools.create_model(
                name="evil",
                from_model="llama3.2 !injected",
            )
        assert isinstance(result, dict)
        assert "error" in result
        mock_run.assert_not_called()

    def test_allows_valid_model_names(self, tools):
        """create_model accepts well-formed model names (regression)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("ironclaude.ollama_mcp.tempfile.NamedTemporaryFile") as mock_tmpfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/Modelfile_test"
                mock_tmpfile.return_value = mock_file
                with patch("ironclaude.ollama_mcp.os.unlink"):
                    result = tools.create_model(
                        name="my-model",
                        from_model="llama3.2:latest",
                    )
        mock_run.assert_called()

    def test_sanitizes_system_newline_injection(self, tools):
        """create_model strips newlines from system to prevent Modelfile injection.

        RED signal: before fix, written_content contains '\\nFROM scratch' as an
        injected Modelfile directive. After fix, newlines are stripped and
        subprocess.run is called (sanitize path, not error path).
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success\n"
        mock_result.stderr = ""

        written_content = None

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("ironclaude.ollama_mcp.tempfile.NamedTemporaryFile") as mock_tmpfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/Modelfile_test"
                mock_tmpfile.return_value = mock_file

                def capture_write(content):
                    nonlocal written_content
                    written_content = content

                mock_file.write.side_effect = capture_write

                with patch("ironclaude.ollama_mcp.os.unlink"):
                    tools.create_model(
                        name="evil",
                        from_model="valid:model",
                        system="helper\nFROM scratch",
                    )

        # Must have proceeded (sanitize, not reject)
        mock_run.assert_called_once()
        assert written_content is not None
        # Injected newline must not appear as a separate Modelfile directive
        assert "\nFROM scratch" not in written_content


class TestNameValidation:
    """RED tests for M3: name parameter not validated in OllamaTools methods.

    RED signal: mock_run.assert_not_called() fails before the fix
    (subprocess.run IS called with flag-like names) and passes after
    (validation short-circuits before the subprocess call).
    """

    def test_show_model_rejects_flag(self, tools):
        """show_model rejects a name starting with '--'."""
        with patch("subprocess.run") as mock_run:
            result = tools.show_model("--help")
        assert "error" in result
        mock_run.assert_not_called()

    def test_show_model_rejects_empty(self, tools):
        """show_model rejects an empty string name."""
        with patch("subprocess.run") as mock_run:
            result = tools.show_model("")
        assert "error" in result
        mock_run.assert_not_called()

    def test_show_model_accepts_valid(self, tools):
        """show_model accepts a well-formed model name (regression)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "details"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tools.show_model("llama3.2:latest")
        mock_run.assert_called_once()
        assert "error" not in result

    def test_pull_model_rejects_flag(self, tools):
        """pull_model rejects a name starting with '-'."""
        with patch("subprocess.run") as mock_run:
            result = tools.pull_model("-f")
        assert "error" in result
        mock_run.assert_not_called()

    def test_pull_model_accepts_valid(self, tools):
        """pull_model accepts a well-formed model name (regression)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = tools.pull_model("my-model/v1")
        assert result["success"] is True

    def test_remove_model_rejects_flag(self, tools):
        """remove_model rejects a name starting with '--'."""
        with patch("subprocess.run") as mock_run:
            result = tools.remove_model("--insecure")
        assert "error" in result
        mock_run.assert_not_called()

    def test_remove_model_accepts_valid(self, tools):
        """remove_model accepts a well-formed model name (regression)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "deleted"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = tools.remove_model("llama3.2:latest")
        assert result["success"] is True

    def test_create_model_rejects_flag(self, tools):
        """create_model rejects a name starting with '--'.

        No tempfile/unlink mocking needed — name validation runs before
        tempfile creation, so subprocess is never reached.
        """
        with patch("subprocess.run") as mock_run:
            result = tools.create_model(name="--help", from_model="llama3.2:latest")
        assert "error" in result
        mock_run.assert_not_called()

    def test_create_model_accepts_valid(self, tools):
        """create_model accepts a well-formed name (regression)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "success"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with patch("ironclaude.ollama_mcp.tempfile.NamedTemporaryFile") as mock_tmpfile:
                mock_file = MagicMock()
                mock_file.name = "/tmp/Modelfile_test"
                mock_tmpfile.return_value = mock_file
                with patch("ironclaude.ollama_mcp.os.unlink"):
                    result = tools.create_model(name="my-model/v1", from_model="llama3.2:latest")
        assert result["success"] is True
