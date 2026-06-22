"""WikiTools — brain wiki page management (single source of truth).

Extracted verbatim from OrchestratorTools so both the orchestrator daemon and
the ic-wiki CLI share one implementation. Behavior is byte-identical to the
original orchestrator methods.
"""
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone, date
from pathlib import Path

logger = logging.getLogger("ironclaude.wiki_tools")

_WIKI_COMMIT_HASH_RE = re.compile(r'\b[0-9a-f]{7,40}\b', re.IGNORECASE)
_WIKI_STATUS_COMPLETE_RE = re.compile(
    r'Status:\s*Complete\s*\(\s*20\d\d-\d\d-\d\d,\s*commit', re.IGNORECASE
)


class WikiTools:
    """Filesystem-backed brain wiki tools, scoped to a single wiki directory."""

    def __init__(self, wiki_dir: str):
        self._wiki_dir = wiki_dir

    @staticmethod
    def _parse_wiki_frontmatter(raw: str) -> tuple[str, str, str]:
        """Parse YAML frontmatter from a wiki page, returning (title, updated, body)."""
        if not raw.startswith("---"):
            return ("", "", raw)
        parts = raw.split("---", 2)
        if len(parts) < 3:
            return ("", "", raw)
        title, updated = "", ""
        for line in parts[1].strip().splitlines():
            if line.startswith("title:"):
                title = line[len("title:"):].strip()
            elif line.startswith("updated:"):
                updated = line[len("updated:"):].strip()
        return (title, updated, parts[2].strip())

    @staticmethod
    def _extract_summary(body: str) -> str:
        """Extract first sentence (up to 120 chars) from wiki body text."""
        if not body:
            return ""
        first_line = body.split("\n")[0].strip()
        summary = first_line
        for i, ch in enumerate(first_line):
            if ch in ".!?" and i > 0:
                summary = first_line[:i + 1]
                break
        if len(summary) > 120:
            summary = summary[:117] + "..."
        return summary

    def _rebuild_wiki_index(self) -> None:
        """Rebuild wiki/index.md from all page files (derived state)."""
        wiki_dir = self._wiki_dir
        entries = []
        for fname in sorted(os.listdir(wiki_dir)):
            if not fname.endswith(".md") or fname in ("index.md", "log.md"):
                continue
            fpath = os.path.join(wiki_dir, fname)
            with open(fpath) as f:
                raw = f.read()
            title, updated, body = self._parse_wiki_frontmatter(raw)
            if not title:
                title = fname[:-3]
            summary = self._extract_summary(body)
            entries.append((fname[:-3], title, summary, updated))

        lines = ["# Wiki Index\n"]
        if entries:
            lines.append("| Page | Summary | Updated |")
            lines.append("|------|---------|---------|")
            for page_name, title, summary, updated in entries:
                lines.append(f"| [{title}]({page_name}.md) | {summary} | {updated} |")
        else:
            lines.append("*No wiki pages yet.*")
        lines.append("")

        with open(os.path.join(wiki_dir, "index.md"), "w") as f:
            f.write("\n".join(lines))

    def _wiki_log_append(self, entry: str) -> None:
        """Append a timestamped entry to wiki/log.md."""
        log_path = os.path.join(self._wiki_dir, "log.md")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not os.path.exists(log_path):
            with open(log_path, "w") as f:
                f.write("# Wiki Log\n\n")
        with open(log_path, "a") as f:
            f.write(f"- {timestamp} — {entry}\n")

    @staticmethod
    def _wiki_keywords(text: str) -> set:
        """Extract meaningful keywords (>=4 chars) from a page name or title."""
        return {w.lower() for w in re.split(r'[\s\-_]+', text) if len(w) >= 4}

    def _wiki_duplicate_warning(self, page: str, title: str) -> str | None:
        """Return conflicting page name if any existing page has >60% keyword overlap, else None."""
        wiki_dir = self._wiki_dir
        if not os.path.isdir(wiki_dir):
            return None
        incoming = self._wiki_keywords(page) | self._wiki_keywords(title)
        if not incoming:
            return None
        for fname in os.listdir(wiki_dir):
            if not fname.endswith('.md') or fname in ('index.md', 'log.md'):
                continue
            existing_page = fname[:-3]
            if existing_page == page:
                continue
            existing_kw = self._wiki_keywords(existing_page)
            if not existing_kw:
                continue
            overlap = len(incoming & existing_kw) / len(incoming | existing_kw)
            if overlap > 0.60:
                return existing_page
        return None

    # ── Wiki tools ───────────────────────────────────────────────────

    def wiki_write(self, page: str, title: str, content: str) -> str:
        """Create or update a wiki page with frontmatter, rebuild index, append log."""
        wiki_dir = self._wiki_dir
        os.makedirs(wiki_dir, exist_ok=True)

        if re.match(r'^d\d+', page):
            return (
                f"Invalid page name '{page}': directive-number prefixes (d<N>) are not allowed. "
                "Wiki pages must be concept-focused, not directive logs. "
                "Use a descriptive name like 'worker-lifecycle' or 'state-update-patterns'."
            )
        if re.search(r'-d\d{1,4}(?:-|$)', page):
            return (
                f"Invalid page name '{page}': directive-number suffixes (-d<N>) are not allowed. "
                "Wiki pages must be concept-focused, not directive logs. "
                "Use a descriptive name like 'worker-lifecycle' or 'state-update-patterns'."
            )
        if re.search(r'\d{4}-\d{2}', page):
            return (
                f"Invalid page name '{page}': date-stamped names are not allowed. "
                "Wiki pages are persistent concepts, not log entries. "
                "Use a descriptive name like 'deployment-patterns' or 'rollout-strategy'."
            )
        if re.search(r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{4}', page, re.IGNORECASE):
            return (
                f"Invalid page name '{page}': date-stamped names are not allowed. "
                "Wiki pages are persistent concepts, not log entries. "
                "Use a descriptive name like 'deployment-patterns' or 'rollout-strategy'."
            )

        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
            return f"Path traversal rejected: {page}"
        if len(content.strip()) < 50:
            return (
                f"Invalid content for '{page}': content must be at least 50 characters after stripping whitespace. "
                "Placeholder pages are not allowed."
            )
        if title.strip().lower() == 'title':
            return (
                f"Invalid title '{title}': placeholder titles are not allowed. "
                "Use a descriptive title like 'Worker Lifecycle' or 'State Update Patterns'."
            )
        stripped_content = re.sub(r'\s', '', content.lower())
        if stripped_content and len(set(stripped_content)) < 4:
            return (
                f"Invalid content for '{page}': content appears to be garbage "
                "(fewer than 4 unique non-whitespace characters). "
                "Placeholder pages are not allowed."
            )
        is_update = os.path.exists(page_path)

        today = date.today().isoformat()
        page_content = f"---\ntitle: {title}\nupdated: {today}\n---\n\n{content}\n"
        with open(page_path, "w") as f:
            f.write(page_content)

        self._rebuild_wiki_index()
        action = "Updated" if is_update else "Created"
        self._wiki_log_append(f"{action} {page}.md")

        repo_root = os.path.dirname(self._wiki_dir)
        verb = "updated" if is_update else "created"
        r_add = subprocess.run(["git", "add", "wiki/"], cwd=repo_root, capture_output=True, text=True)
        if r_add.returncode != 0:
            return f"{page_path} (git commit failed: {r_add.stderr.strip()})"
        r_commit = subprocess.run(
            ["git", "commit", "-m", f"wiki: {verb} {page}"],
            cwd=repo_root, capture_output=True, text=True,
        )
        if r_commit.returncode != 0:
            return f"{page_path} (git commit failed: {r_commit.stderr.strip()})"
        if _WIKI_COMMIT_HASH_RE.search(title) or _WIKI_STATUS_COMPLETE_RE.search(content):
            logger.warning(
                "wiki_write: '%s' looks like a directive log (commit hash in title or "
                "Status: Complete pattern in content) — consider absorbing into a concept page",
                page,
            )
        conflict = self._wiki_duplicate_warning(page, title)
        if conflict:
            logger.warning(
                "wiki_write: '%s' has >60%% keyword overlap with existing page '%s' — "
                "consider consolidating",
                page, conflict,
            )
        return page_path

    def wiki_delete(self, page: str) -> str:
        """Delete a wiki page, rebuild index, append log. Idempotent for missing pages."""
        wiki_dir = self._wiki_dir
        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
            return f"Path traversal rejected: {page}"
        if not os.path.exists(page_path):
            return f"Page {page}.md not found, no action taken."
        os.remove(page_path)
        self._rebuild_wiki_index()
        self._wiki_log_append(f"Deleted {page}.md")

        repo_root = os.path.dirname(self._wiki_dir)
        r_add = subprocess.run(["git", "add", "wiki/"], cwd=repo_root, capture_output=True, text=True)
        if r_add.returncode != 0:
            return f"Deleted {page}.md (git commit failed: {r_add.stderr.strip()})"
        r_commit = subprocess.run(
            ["git", "commit", "-m", f"wiki: delete {page}"],
            cwd=repo_root, capture_output=True, text=True,
        )
        if r_commit.returncode != 0:
            return f"Deleted {page}.md (git commit failed: {r_commit.stderr.strip()})"
        return f"Deleted {page}.md"

    def wiki_query(self, keywords: str, limit: int = 20) -> str:
        """Search wiki pages by keywords. Returns JSON array of matches."""
        wiki_dir = self._wiki_dir
        if not os.path.isdir(wiki_dir):
            return json.dumps([])

        kw_list = keywords.lower().split()
        if not kw_list:
            return json.dumps([])

        results: dict[str, dict] = {}

        # Phase 1: scan index for matches (match_source=index)
        index_path = os.path.join(wiki_dir, "index.md")
        if os.path.exists(index_path):
            with open(index_path) as f:
                for line in f:
                    if not line.startswith("| ["):
                        continue
                    lower_line = line.lower()
                    if any(kw in lower_line for kw in kw_list):
                        parts = [p.strip() for p in line.split("|")[1:-1]]
                        if len(parts) >= 3:
                            m = re.match(r"\[(.+?)\]\((.+?)\.md\)", parts[0])
                            if m:
                                results[m.group(2)] = {
                                    "path": f"{m.group(2)}.md",
                                    "title": m.group(1),
                                    "summary": parts[1],
                                    "updated": parts[2],
                                    "match_source": "index",
                                }

        # Phase 2: scan page content for matches not already found in index
        for fname in sorted(os.listdir(wiki_dir)):
            if not fname.endswith(".md") or fname in ("index.md", "log.md"):
                continue
            page_name = fname[:-3]
            if page_name in results:
                continue
            fpath = os.path.join(wiki_dir, fname)
            with open(fpath) as f:
                raw = f.read()
            if any(kw in raw.lower() for kw in kw_list):
                title, updated, body = self._parse_wiki_frontmatter(raw)
                if not title:
                    title = page_name
                results[page_name] = {
                    "path": fname,
                    "title": title,
                    "summary": self._extract_summary(body),
                    "updated": updated,
                    "match_source": "content",
                }

        sorted_results = sorted(
            results.values(),
            key=lambda r: (0 if r["match_source"] == "index" else 1, r["path"]),
        )
        return json.dumps(sorted_results[:limit], indent=2)

    def wiki_log(self, entry: str) -> str:
        """Append a free-form entry to the wiki log."""
        os.makedirs(self._wiki_dir, exist_ok=True)
        self._wiki_log_append(entry)
        return "Log entry appended."
