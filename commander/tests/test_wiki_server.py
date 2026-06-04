"""Tests for tools/wiki_server.py — XSS escaping in HTML output."""
from __future__ import annotations

import sys
from pathlib import Path

# tools/ is not on the pytest pythonpath — add it explicitly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from wiki_server import _parse_md  # noqa: E402


def test_parse_md_escapes_xss_in_title(tmp_path):
    """Title from YAML frontmatter is HTML-escaped before being placed in <title>."""
    md_file = tmp_path / "test.md"
    md_file.write_text("---\ntitle: <script>alert(1)</script>\n---\n\nContent\n")
    title, _ = _parse_md(md_file)
    assert "<script>" not in title
    assert "&lt;script&gt;" in title


def test_parse_md_preserves_html_body(tmp_path):
    """Body is pre-rendered HTML from the markdown library — must not be double-escaped."""
    md_file = tmp_path / "test.md"
    md_file.write_text("# Hello\n\n**bold**\n")
    _, body = _parse_md(md_file)
    assert "<h1>" in body
    assert "&lt;h1&gt;" not in body


def test_wiki_prefix_no_slash_redirects(tmp_path, monkeypatch):
    """GET /wiki (no trailing slash) returns 301 to /wiki/."""
    import http.client
    import threading

    import wiki_server
    monkeypatch.setattr(wiki_server, "WIKI_DIR", tmp_path)
    (tmp_path / "index.md").write_text("# Index\n")

    server = wiki_server.ThreadingHTTPServer(("127.0.0.1", 0), wiki_server.WikiHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("GET", "/wiki")
    resp = conn.getresponse()

    assert resp.status == 301
    assert resp.getheader("Location") == "/wiki/"
    t.join(timeout=2)
    server.server_close()
