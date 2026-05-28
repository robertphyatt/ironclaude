#!/usr/bin/env python3
"""Serves ~/.ironclaude/brain/wiki/ as HTML over HTTP on port 8091."""
import html
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import markdown as mdlib

WIKI_DIR = Path.home() / ".ironclaude" / "brain" / "wiki"
PORT = 8091
HOST = "127.0.0.1"
PATH_PREFIX = "/wiki"

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 900px; margin: 40px auto; padding: 0 20px;
       color: #222; line-height: 1.6; }}
a {{ color: #0366d6; }}
h1 {{ border-bottom: 1px solid #eee; padding-bottom: 0.3em; }}
h2 {{ border-bottom: 1px solid #eee; padding-bottom: 0.2em; }}
pre {{ background: #f6f8fa; padding: 16px; border-radius: 6px; overflow: auto; }}
code {{ background: #f6f8fa; padding: 0.2em 0.4em; border-radius: 3px; font-size: 0.9em; }}
pre code {{ background: none; padding: 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #dfe2e5; padding: 8px 12px; text-align: left; }}
th {{ background: #f6f8fa; }}
nav {{ margin-bottom: 24px; font-size: 0.9em; color: #666; }}
nav a {{ color: #0366d6; text-decoration: none; }}
</style>
</head>
<body>
<nav><a href="/wiki/">← Index</a></nav>
{body}
</body>
</html>
"""


def _parse_md(path: Path) -> tuple[str, str]:
    """Return (title, html_body). Strips YAML frontmatter if present."""
    text = path.read_text(encoding="utf-8")
    title = path.stem.replace("-", " ").title()
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].lstrip()
            for line in frontmatter.splitlines():
                m = re.match(r"^title:\s*(.+)$", line.strip())
                if m:
                    title = m.group(1).strip("\"'")
                    break

    md = mdlib.Markdown(extensions=["tables", "fenced_code"])
    return html.escape(title), md.convert(body)


class WikiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        if path.startswith(PATH_PREFIX):
            path = path[len(PATH_PREFIX):]

        if path in ("/", ""):
            md_path = WIKI_DIR / "index.md"
        else:
            slug = path.lstrip("/")
            if not slug.endswith(".md"):
                slug += ".md"
            md_path = WIKI_DIR / slug
            if not md_path.resolve().is_relative_to(WIKI_DIR.resolve()):
                self._send_404()
                return

        if not md_path.exists() or not md_path.is_file():
            self._send_404()
            return

        title, body = _parse_md(md_path)
        self._send_html(200, _HTML_TEMPLATE.format(title=title, body=body))

    def _send_html(self, code: int, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_404(self) -> None:
        pages = sorted(p.stem for p in WIKI_DIR.glob("*.md") if p.stem != "index")
        items = "\n".join(
            f'<li><a href="/wiki/{html.escape(p)}.md">{html.escape(p)}</a></li>'
            for p in pages
        )
        body = f"<h1>404 Not Found</h1><ul>{items}</ul>"
        self._send_html(404, _HTML_TEMPLATE.format(title="404 Not Found", body=body))

    def log_message(self, fmt, *args):  # noqa: ANN001
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), WikiHandler)
    print(f"Wiki server listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
