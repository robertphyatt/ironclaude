# Wiki Write Validation Strengthening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Add three validation guard clauses to `wiki_write` (directive suffixes, YYYY-MM date patterns, content minimum) and update existing test fixtures to use a `VALID_CONTENT` constant.

**Architecture:** Three sequential guard clauses added to `wiki_write`'s validation block in `orchestrator_mcp.py`, each returning an error string before any file I/O. Tests updated with a class-level `VALID_CONTENT = "A" * 60` constant to survive the 50-char minimum check.

**Tech Stack:** Python, pytest, re (stdlib)

---

## Task 1: Update Existing Test Fixtures with VALID_CONTENT Constant

**Files:**
- Modify: `commander/tests/test_orchestrator_mcp.py`

No TDD cycle required — this is a pure test refactor with no behavior change. Tests must pass before and after this task.

**Step 1: Locate TestWikiTools class definition**

Open `commander/tests/test_orchestrator_mcp.py` and find the `class TestWikiTools:` declaration (around line 3882).

**Step 2: Add VALID_CONTENT constant**

Immediately after the class docstring (or the `class TestWikiTools:` line if there's no docstring), add:

```python
    VALID_CONTENT = "A" * 60
```

Place it before the first `def test_` method in the class.

**Step 3: Replace all short content strings**

Replace every content string shorter than 50 characters in the `TestWikiTools` class with `self.VALID_CONTENT`. The exhaustive list of test methods and their replacements:

| Test method | Old content | Replace all with |
|---|---|---|
| `test_wiki_write_creates_page` | `"Some content here."` | `self.VALID_CONTENT` |
| `test_wiki_write_frontmatter` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_write_rebuilds_index` | `"Alpha content."`, `"Beta content."` | `self.VALID_CONTENT` (both) |
| `test_wiki_write_appends_log` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_write_update_existing` | `"Original."`, `"Updated."` | `self.VALID_CONTENT` (both) |
| `test_wiki_write_creates_wiki_dir` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_delete_removes_page` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_delete_updates_index` | both `"Content."` calls | `self.VALID_CONTENT` (both) |
| `test_wiki_delete_appends_log` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_query_matches_index` | `"How the grader evaluates worker output."` (39 chars) | `self.VALID_CONTENT` |
| `test_wiki_log_appends_entry` | `"Content."` | `self.VALID_CONTENT` |
| `test_rebuild_index_derived_state` | `"Content A."`, `"Content B."`, `"Content C."` | `self.VALID_CONTENT` (all three) |
| `test_wiki_write_hard_failure` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_write_creates_git_commit` | `"Some content."` | `self.VALID_CONTENT` |
| `test_wiki_write_update_commits_to_git` | `"Version 1."`, `"Version 2."` | `self.VALID_CONTENT` (both) |
| `test_wiki_delete_commits_to_git` | `"Content."` | `self.VALID_CONTENT` |
| `test_wiki_write_accepts_concept_names` | `"Content"` | `self.VALID_CONTENT` |

Do NOT replace content in these tests (they are rejected before content is checked):
- `test_wiki_write_rejects_path_traversal`
- `test_wiki_write_rejects_directive_prefix`
- `test_wiki_write_rejects_bare_directive_number`
- `test_wiki_write_rejects_date_stamped_name`

Do NOT replace content in these tests (content already ≥50 chars):
- `test_wiki_query_matches_content` (`"Workers are spawned via tmux. The grader timeout is 30 seconds."` — 63 chars)
- `test_wiki_query_deduplicates` (`"The grader evaluates worker output using grading criteria."` — 58 chars)

**Step 4: Run the full TestWikiTools suite — verify all pass**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools -v
```

Expected: All tests pass (zero failures, same count as before).

**Step 5: Stage changes**

```bash
git add commander/tests/test_orchestrator_mcp.py
```

Expected: Changes staged.

---

## Task 2: Directive Suffix Validation

**Files:**
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`

**Depends on:** Task 1

**Step 1: Add two failing tests**

In `commander/tests/test_orchestrator_mcp.py`, inside `TestWikiTools`, add these two tests immediately after `test_wiki_write_rejects_bare_directive_number`:

```python
def test_wiki_write_rejects_directive_suffix(self, wiki_tools, tmp_path):
    """wiki_write rejects page names with directive-number suffixes (-d<N>)."""
    result = wiki_tools.wiki_write("sqlite-contention-fix-d681", "Title", self.VALID_CONTENT)
    assert "directive-number" in result
    assert not (tmp_path / "brain" / "wiki" / "sqlite-contention-fix-d681.md").exists()

def test_wiki_write_rejects_directive_suffix_short(self, wiki_tools, tmp_path):
    """wiki_write rejects page names ending in short directive suffixes like -d12."""
    result = wiki_tools.wiki_write("state-d12", "Title", self.VALID_CONTENT)
    assert "directive-number" in result
    assert not (tmp_path / "brain" / "wiki" / "state-d12.md").exists()
```

**Step 2: Run new tests — verify both FAIL**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_directive_suffix tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_directive_suffix_short -v
```

Expected: Both FAIL (page gets created instead of rejected).

**Step 3: Add directive suffix guard clause to wiki_write**

In `commander/src/ironclaude/orchestrator_mcp.py`, locate the directive prefix check in `wiki_write`:

```python
        if re.match(r'^d\d+', page):
            return (
                f"Invalid page name '{page}': directive-number prefixes (d<N>) are not allowed. "
                "Wiki pages must be concept-focused, not directive logs. "
                "Use a descriptive name like 'worker-lifecycle' or 'state-update-patterns'."
            )
```

Immediately after that block, insert:

```python
        if re.search(r'-d\d{1,4}(?:-|$)', page):
            return (
                f"Invalid page name '{page}': directive-number suffixes (-d<N>) are not allowed. "
                "Wiki pages must be concept-focused, not directive logs. "
                "Use a descriptive name like 'worker-lifecycle' or 'state-update-patterns'."
            )
```

**Step 4: Run new tests — verify both PASS**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_directive_suffix tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_directive_suffix_short -v
```

Expected: Both PASS.

**Step 5: Run full TestWikiTools suite — verify no regressions**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools -v
```

Expected: All tests pass.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 3: Expanded Date Validation

**Files:**
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`

**Depends on:** Task 2

**Step 1: Add two failing tests**

In `commander/tests/test_orchestrator_mcp.py`, inside `TestWikiTools`, add these two tests immediately after `test_wiki_write_rejects_date_stamped_name`:

```python
def test_wiki_write_rejects_year_month_pattern(self, wiki_tools, tmp_path):
    """wiki_write rejects page names with YYYY-MM date patterns (no full date required)."""
    result = wiki_tools.wiki_write("ideaservice-adversarial-review-2026-05", "Title", self.VALID_CONTENT)
    assert "date-stamped" in result
    assert not (tmp_path / "brain" / "wiki" / "ideaservice-adversarial-review-2026-05.md").exists()

def test_wiki_write_rejects_month_year_pattern(self, wiki_tools, tmp_path):
    """wiki_write rejects page names with month-name+year patterns like may2026."""
    result = wiki_tools.wiki_write("ironclaude-releases-may2026", "Title", self.VALID_CONTENT)
    assert "date-stamped" in result
    assert not (tmp_path / "brain" / "wiki" / "ironclaude-releases-may2026.md").exists()
```

**Step 2: Run new tests — verify both FAIL**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_year_month_pattern tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_month_year_pattern -v
```

Expected: Both FAIL (pages get created instead of rejected).

**Step 3: Expand the date guard clause in wiki_write**

In `commander/src/ironclaude/orchestrator_mcp.py`, locate the existing date check in `wiki_write`:

```python
        if re.search(r'\d{4}-\d{2}-\d{2}', page):
            return (
                f"Invalid page name '{page}': date-stamped names are not allowed. "
                "Wiki pages are persistent concepts, not log entries. "
                "Use a descriptive name like 'deployment-patterns' or 'rollout-strategy'."
            )
```

Replace `r'\d{4}-\d{2}-\d{2}'` with `r'\d{4}-\d{2}'` (widens the check to also catch YYYY-MM without a day component):

```python
        if re.search(r'\d{4}-\d{2}', page):
            return (
                f"Invalid page name '{page}': date-stamped names are not allowed. "
                "Wiki pages are persistent concepts, not log entries. "
                "Use a descriptive name like 'deployment-patterns' or 'rollout-strategy'."
            )
```

Immediately after that block, add a second date guard clause for month-name+year patterns:

```python
        if re.search(r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{4}', page, re.IGNORECASE):
            return (
                f"Invalid page name '{page}': date-stamped names are not allowed. "
                "Wiki pages are persistent concepts, not log entries. "
                "Use a descriptive name like 'deployment-patterns' or 'rollout-strategy'."
            )
```

**Step 4: Run new tests — verify both PASS**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_year_month_pattern tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_month_year_pattern -v
```

Expected: Both PASS.

**Step 5: Run full TestWikiTools suite — verify no regressions**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools -v
```

Expected: All tests pass. Confirm `test_wiki_write_rejects_date_stamped_name` (the original full-date test) still passes with the widened regex.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 4: Content Minimum Validation

**Files:**
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`

**Depends on:** Task 3

**Step 1: Add four failing tests**

In `commander/tests/test_orchestrator_mcp.py`, inside `TestWikiTools`, add these four tests immediately after `test_wiki_write_accepts_concept_names`:

```python
def test_wiki_write_rejects_empty_content(self, wiki_tools, tmp_path):
    """wiki_write rejects empty content strings."""
    result = wiki_tools.wiki_write("valid-page", "Title", "")
    assert "content" in result.lower()
    assert not (tmp_path / "brain" / "wiki" / "valid-page.md").exists()

def test_wiki_write_rejects_whitespace_content(self, wiki_tools, tmp_path):
    """wiki_write rejects whitespace-only content."""
    result = wiki_tools.wiki_write("valid-page", "Title", "   \n\t  ")
    assert "content" in result.lower()
    assert not (tmp_path / "brain" / "wiki" / "valid-page.md").exists()

def test_wiki_write_rejects_short_content(self, wiki_tools, tmp_path):
    """wiki_write rejects content under 50 characters after stripping whitespace."""
    result = wiki_tools.wiki_write("valid-page", "Title", "x" * 49)
    assert "content" in result.lower()
    assert not (tmp_path / "brain" / "wiki" / "valid-page.md").exists()

def test_wiki_write_accepts_minimum_content(self, wiki_tools, tmp_path):
    """wiki_write accepts content of exactly 50 characters after stripping whitespace."""
    result = wiki_tools.wiki_write("valid-page", "Title", "x" * 50)
    assert "valid-page" in result
    assert (tmp_path / "brain" / "wiki" / "valid-page.md").exists()
```

**Step 2: Run new tests — verify RED**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_empty_content tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_whitespace_content tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_short_content tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_accepts_minimum_content -v
```

Expected: First three FAIL (pages get created), fourth PASS (it creates a page — which it already does). Confirm `test_wiki_write_accepts_minimum_content` passes before implementing.

**Step 3: Add content minimum guard clause to wiki_write**

In `commander/src/ironclaude/orchestrator_mcp.py`, locate the path traversal check in `wiki_write`:

```python
        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
            return f"Path traversal rejected: {page}"
        is_update = os.path.exists(page_path)
```

Insert the content minimum check between the path traversal return and `is_update`:

```python
        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
            return f"Path traversal rejected: {page}"
        if len(content.strip()) < 50:
            return (
                f"Invalid content for '{page}': content must be at least 50 characters after stripping whitespace. "
                "Placeholder pages are not allowed."
            )
        is_update = os.path.exists(page_path)
```

**Step 4: Run new tests — verify all four PASS**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_empty_content tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_whitespace_content tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_short_content tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_accepts_minimum_content -v
```

Expected: All four PASS.

**Step 5: Run full TestWikiTools suite — verify no regressions**

```bash
cd commander && .venv/bin/python -m pytest tests/test_orchestrator_mcp.py::TestWikiTools -v
```

Expected: All tests pass. Count should be original count + 8 new tests.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```
