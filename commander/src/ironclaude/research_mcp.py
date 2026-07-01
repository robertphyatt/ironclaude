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
from urllib.parse import urljoin, urlparse

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


def _replace_host(url: str, new_host: str) -> str:
    """Return *url* with its host component replaced by *new_host*, preserving port."""
    parsed = urlparse(url)
    netloc = new_host
    if parsed.port:
        netloc = f"{new_host}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


class _PinnedIPAdapter(requests.adapters.HTTPAdapter):
    """Connect to a pre-validated IP while using the original hostname for TLS.

    Closes the DNS-rebinding TOCTOU (the socket targets the exact validated IP)
    WITHOUT breaking HTTPS: SNI and certificate hostname verification still use
    the real hostname (via urllib3 server_hostname/assert_hostname), and the
    Host header is preserved. Session-local, so it is thread-safe (unlike a
    global socket.getaddrinfo monkeypatch).
    """

    def __init__(self, hostname: str, pinned_ip: str, *args, **kwargs) -> None:
        self._hostname = hostname
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        real_host = urlparse(request.url).hostname
        if real_host == self._hostname:
            # Verify TLS against the real host even though we dial the IP.
            self.poolmanager.connection_pool_kw["server_hostname"] = self._hostname
            self.poolmanager.connection_pool_kw["assert_hostname"] = self._hostname
            request.headers["Host"] = self._hostname
            request.url = _replace_host(request.url, self._pinned_ip)
        return super().send(request, **kwargs)


def _resolve_and_validate(url: str) -> tuple[str, str, int]:
    """Resolve the hostname in *url* once and validate every returned IP.

    Returns (hostname, pinned_ip, port). The caller dials pinned_ip while keeping
    the hostname for TLS SNI/cert verification (see _PinnedIPAdapter), so DNS is
    resolved exactly once and cannot rebind between validation and connection.

    Raises ValueError for a non-http(s) scheme, a literal private/internal IP,
    or a hostname that resolves to a private/internal address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f'Blocked URL scheme: {parsed.scheme!r}')
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")
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
    return hostname, resolved_ip, port


def _build_pinned_session(hostname: str, pinned_ip: str) -> requests.Session:
    """Build a Session that dials *pinned_ip* but verifies TLS against *hostname*."""
    session = requests.Session()
    adapter = _PinnedIPAdapter(hostname, pinned_ip)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _safe_get(url: str, max_redirects: int = 5) -> requests.Response:
    """GET *url* without auto-following redirects, pinning DNS on every hop.

    Each hop: re-validate the URL (scheme + SSRF), resolve+pin the IP, and issue
    the request against the ORIGINAL hostname URL through a pinned-IP session so
    TLS verification uses the real host. Relative Location headers are resolved
    against the current URL via urljoin before re-validation.
    Raises ValueError for a blocked target or too many redirects.
    """
    for _ in range(max_redirects):
        error = _validate_url(url)
        if error:
            raise ValueError(error)
        hostname, pinned_ip, _port = _resolve_and_validate(url)
        session = _build_pinned_session(hostname, pinned_ip)
        try:
            response = session.get(url, timeout=30, allow_redirects=False)
        finally:
            session.close()
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            url = urljoin(url, location)
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
            response = _safe_get(url)
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
