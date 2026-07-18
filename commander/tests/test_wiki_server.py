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


def test_wiki_prefix_no_slash_redirects():
    """GET /wiki returns 301 to /wiki/ without binding a network port."""
    import socket
    from types import SimpleNamespace

    import wiki_server

    client, handler_socket = socket.socketpair()
    try:
        client.sendall(
            b"GET /wiki HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n\r\n"
        )
        client.shutdown(socket.SHUT_WR)
        server = SimpleNamespace(server_name="localhost", server_port=80)
        wiki_server.WikiHandler(handler_socket, ("local", 0), server)
        handler_socket.shutdown(socket.SHUT_WR)

        response = b""
        while chunk := client.recv(4096):
            response += chunk
    finally:
        client.close()
        handler_socket.close()

    headers = response.decode("iso-8859-1").split("\r\n\r\n", 1)[0]
    assert headers.startswith("HTTP/1.0 301 ")
    assert "\r\nLocation: /wiki/\r\n" in f"\r\n{headers}\r\n"
