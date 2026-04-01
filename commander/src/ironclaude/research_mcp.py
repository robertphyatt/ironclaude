# src/ic/research_mcp.py
"""MCP server for web research tools.

Provides web_search and web_fetch capabilities for the brain daemon,
allowing it to search the web and fetch/convert web pages to text.

The ResearchTools class implements business logic separately from
the MCP transport layer. Tests call ResearchTools methods directly;
the FastMCP server wraps them for the brain's Claude Agent SDK session.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from markdownify import markdownify as md
import requests
from duckduckgo_search import DDGS

logger = logging.getLogger("ironclaude.research_mcp")

MAX_CONTENT_LENGTH = 10000


def _validate_url(url: str) -> str | None:
    """Return an error string if url is unsafe, or None if it is safe.

    Blocks: non-http/https schemes, embedded credentials, literal private/
    loopback/link-local/reserved IPs, the hostnames 'localhost' and '0.0.0.0',
    and hostnames that DNS-resolve to private/internal addresses.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"

    if parsed.scheme not in ("http", "https"):
        return f"Blocked URL scheme: {parsed.scheme!r}"

    if parsed.username or parsed.password:
        return "URLs with embedded credentials are not allowed"

    hostname = parsed.hostname
    if not hostname:
        return "URL has no hostname"

    # Fast-path: literal IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return f"Blocked private/internal IP: {hostname}"
    except ValueError:
        # Not a literal IP — check hostname and then DNS
        if hostname.lower() in ("localhost", "0.0.0.0"):
            return f"Blocked hostname: {hostname}"

    # DNS resolution check
    try:
        results = socket.getaddrinfo(hostname, None)
        for _family, _stype, _proto, _canon, sockaddr in results:
            addr = sockaddr[0]
            try:
                resolved_ip = ipaddress.ip_address(addr)
                if (
                    resolved_ip.is_private
                    or resolved_ip.is_loopback
                    or resolved_ip.is_link_local
                    or resolved_ip.is_reserved
                ):
                    return f"Blocked: {hostname!r} resolves to private/internal address {addr}"
            except ValueError:
                pass
    except socket.gaierror:
        pass  # DNS failure — let requests handle it

    return None


def _resolve_and_validate(url: str) -> tuple[str, str]:
    """Resolve the hostname in *url* once, validate all returned IPs, and return
    a (resolved_url, hostname) tuple where resolved_url has the IP literal in
    place of the hostname so the HTTP request never re-resolves DNS.

    Raises ValueError if any resolved address is private/internal, or if the
    URL contains a literal private/internal IP address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f'Blocked URL scheme: {parsed.scheme!r}')
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # Fast-path: reject literal private/reserved IPs without a DNS lookup.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and (
        literal_ip.is_private
        or literal_ip.is_loopback
        or literal_ip.is_link_local
        or literal_ip.is_reserved
    ):
        raise ValueError(f"Blocked private/internal IP: {hostname}")

    results = socket.getaddrinfo(hostname, port)
    for _family, _stype, _proto, _canon, sockaddr in results:
        addr = sockaddr[0]
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(
                f"Blocked: {hostname!r} resolves to private/internal address {addr}"
            )
    resolved_ip = results[0][4][0]
    resolved_url = url.replace(hostname, resolved_ip, 1)
    return resolved_url, hostname


def _safe_get(
    url: str, max_redirects: int = 5, headers: dict | None = None
) -> requests.Response:
    """GET *url* without following redirects automatically.

    The first request uses *url* and *headers* as provided (caller is
    responsible for pre-resolving via _resolve_and_validate).  Each redirect
    hop calls _resolve_and_validate() on the Location header to pin DNS before
    following.  Raises ValueError for blocked redirect targets or too many redirects.
    """
    is_redirect = False
    for _ in range(max_redirects):
        if is_redirect:
            resolved_url, hostname = _resolve_and_validate(url)
            url = resolved_url
            headers = {"Host": hostname}
        else:
            error = _validate_url(url)
            if error:
                raise ValueError(error)
        response = requests.get(url, timeout=30, allow_redirects=False, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308):
            url = response.headers.get("Location", "")
            is_redirect = True
            continue
        return response
    raise ValueError("Too many redirects")


class ResearchTools:
    """Business logic for research MCP tools.

    All methods are synchronous and return structured data.
    Errors are caught and returned as {error: "message"} dicts.
    """

    def web_search(self, query: str, max_results: int = 5) -> list[dict] | dict:
        """Search the web using DuckDuckGo.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return. Defaults to 5.

        Returns:
            List of dicts with {title, url, snippet} keys, or
            {error: "message"} dict on failure.
        """
        try:
            ddgs = DDGS()
            raw_results = ddgs.text(query, max_results=max_results)
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in raw_results
            ]
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}")
            return {"error": str(e)}

    def web_fetch(self, url: str, prompt: str = "") -> str | dict:
        """Fetch a URL and convert its HTML to plain text.

        Args:
            url: The URL to fetch.
            prompt: Optional prompt describing what to extract (for context).

        Returns:
            Plain text content (truncated to 10K chars), or
            {error: "message"} dict on failure.
        """
        error = _validate_url(url)
        if error:
            return {"error": error}

        try:
            resolved_url, hostname = _resolve_and_validate(url)
            response = _safe_get(resolved_url, headers={"Host": hostname})
            response.raise_for_status()

            text = md(response.text, strip=["img"])

            if len(text) > MAX_CONTENT_LENGTH:
                text = text[:MAX_CONTENT_LENGTH]

            return text
        except Exception as e:
            logger.error(f"Fetch failed for URL '{url}': {e}")
            return {"error": str(e)}


def create_research_mcp_server(tools: ResearchTools | None = None):
    """Create and configure the FastMCP server wrapping ResearchTools."""
    from mcp.server.fastmcp import FastMCP

    if tools is None:
        tools = ResearchTools()

    mcp = FastMCP("research")

    @mcp.tool()
    def web_search(query: str, max_results: int = 5) -> str:
        """Search the web using DuckDuckGo and return results."""
        import json

        result = tools.web_search(query, max_results)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def web_fetch(url: str, prompt: str = "") -> str:
        """Fetch a URL and convert its HTML content to plain text."""
        import json

        result = tools.web_fetch(url, prompt)
        if isinstance(result, dict):
            return json.dumps(result, indent=2)
        return result

    return mcp
