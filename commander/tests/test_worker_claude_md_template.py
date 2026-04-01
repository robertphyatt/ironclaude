# tests/test_worker_claude_md_template.py
"""Tests for the worker CLAUDE.md template file."""

from pathlib import Path


TEMPLATE_PATH = Path(__file__).parent.parent / "src" / "ironclaude" / "templates" / "worker_claude_md.md"


class TestWorkerClaudeMdTemplate:
    def test_template_file_exists(self):
        """Template file must exist at expected path."""
        assert TEMPLATE_PATH.exists(), f"Template not found at {TEMPLATE_PATH}"

    def test_template_contains_workflow_requirement(self):
        """Template must contain the workflow requirement blockquote."""
        content = TEMPLATE_PATH.read_text()
        assert "WORKFLOW REQUIREMENT" in content

    def test_template_contains_all_nine_directives(self):
        """Template must contain all 9 numbered behavioral directives."""
        content = TEMPLATE_PATH.read_text()
        expected = [
            "Challenge Assumptions",
            "Verify with Evidence",
            "Refuse Impossible Requests",
            "Persistent Questioning",
            "No Premature Optimization",
            "Search Before Guessing",
            "Subagent Discipline",
            "No Sycophantic Responses",
            "Handle Large Files with Decomposition",
        ]
        for directive in expected:
            assert directive in content, f"Missing directive: {directive}"
