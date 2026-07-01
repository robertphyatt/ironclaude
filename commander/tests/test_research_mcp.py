# tests/test_research_mcp.py
"""Tests for the research MCP server business logic."""

import pytest
from unittest.mock import MagicMock, patch

import requests

from ironclaude.research_mcp import (
    ResearchTools,
    create_research_mcp_server,
    _PinnedIPAdapter,
    _replace_host,
    _build_pinned_session,
)


def _mock_session(get_return=None, get_side_effect=None):
    """A MagicMock standing in for a pinned requests.Session."""
    session = MagicMock()
    if get_side_effect is not None:
        session.get.side_effect = get_side_effect
    else:
        session.get.return_value = get_return
    return session


class TestWebSearch:
    def test_returns_results(self):
        """web_search returns normalized results from DDGS."""
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {"title": "Result 1", "href": "https://example.com/1", "body": "Snippet 1"},
            {"title": "Result 2", "href": "https://example.com/2", "body": "Snippet 2"},
        ]

        tools = ResearchTools()
        with patch("ironclaude.research_mcp.DDGS", return_value=mock_ddgs):
            results = tools.web_search("test query")

        assert len(results) == 2
        assert results[0] == {
            "title": "Result 1",
            "url": "https://example.com/1",
            "snippet": "Snippet 1",
        }
        assert results[1] == {
            "title": "Result 2",
            "url": "https://example.com/2",
            "snippet": "Snippet 2",
        }
        mock_ddgs.text.assert_called_once_with("test query", max_results=5)

    def test_respects_max_results(self):
        """web_search passes max_results to DDGS."""
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {"title": "Only One", "href": "https://example.com/1", "body": "Snippet"},
        ]

        tools = ResearchTools()
        with patch("ironclaude.research_mcp.DDGS", return_value=mock_ddgs):
            results = tools.web_search("test query", max_results=1)

        assert len(results) == 1
        mock_ddgs.text.assert_called_once_with("test query", max_results=1)

    def test_handles_search_error(self):
        """web_search returns error dict on exception."""
        mock_ddgs = MagicMock()
        mock_ddgs.text.side_effect = Exception("Network timeout")

        tools = ResearchTools()
        with patch("ironclaude.research_mcp.DDGS", return_value=mock_ddgs):
            result = tools.web_search("failing query")

        assert isinstance(result, dict)
        assert "error" in result
        assert "Network timeout" in result["error"]


def _ok_response(text="<html><body><h1>Hello World</h1><p>Content here</p></body></html>"):
    r = MagicMock()
    r.status_code = 200
    r.text = text
    r.raise_for_status = MagicMock()
    return r


class TestWebFetch:
    def test_fetches_and_converts_html(self):
        """web_fetch fetches URL and converts HTML to text."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp._build_pinned_session",
                return_value=_mock_session(get_return=_ok_response()),
            ):
                result = tools.web_fetch("https://example.com")

        assert "Hello World" in result
        assert "Content here" in result
        assert "<h1>" not in result

    def test_truncates_long_content(self):
        """web_fetch truncates content exceeding 10K chars."""
        long_html = "<html><body>" + "x" * 20000 + "</body></html>"
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp._build_pinned_session",
                return_value=_mock_session(get_return=_ok_response(long_html)),
            ):
                result = tools.web_fetch("https://example.com/long")

        assert len(result) <= 10000

    def test_handles_fetch_error(self):
        """web_fetch returns error dict on exception."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        session = _mock_session()
        session.get.side_effect = Exception("Connection refused")
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp._build_pinned_session", return_value=session
            ):
                result = tools.web_fetch("https://example.com/bad")
        assert isinstance(result, dict)
        assert "error" in result
        assert "Connection refused" in result["error"]


class TestMCPServer:
    def test_create_research_mcp_server_returns_server(self):
        """create_research_mcp_server returns a FastMCP instance."""
        server = create_research_mcp_server()
        assert server is not None
        assert server.name == "research"


class TestWebFetchSSRF:
    """web_fetch must reject SSRF targets BEFORE building a session / making any request."""

    def _assert_blocked(self, url, addr_info=None):
        tools = ResearchTools()
        with patch("ironclaude.research_mcp._build_pinned_session") as mock_build:
            if addr_info is not None:
                with patch("socket.getaddrinfo", return_value=addr_info):
                    result = tools.web_fetch(url)
            else:
                result = tools.web_fetch(url)
        assert isinstance(result, dict)
        assert "error" in result
        mock_build.assert_not_called()

    def test_blocks_metadata_endpoint(self):
        self._assert_blocked("http://169.254.169.254/latest/meta-data/")

    def test_blocks_localhost(self):
        self._assert_blocked("http://localhost/admin")

    def test_blocks_private_10x(self):
        self._assert_blocked("http://10.0.0.1/internal")

    def test_blocks_ftp_scheme(self):
        self._assert_blocked("ftp://example.com/file.txt")

    def test_blocks_credentials_in_url(self):
        self._assert_blocked("https://user:pass@example.com/")

    def test_blocks_ipv6_loopback(self):
        self._assert_blocked("http://[::1]/admin")

    def test_blocks_hostname_resolving_to_private_ip(self):
        """A hostname that DNS-resolves to a private IP is blocked before any request."""
        self._assert_blocked(
            "http://internal.corp.example.com/",
            addr_info=[(2, 1, 6, "", ("10.0.0.1", 0))],
        )

    def test_allows_valid_https(self):
        """web_fetch allows legitimate public HTTPS URLs (regression)."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 0))]
        session = _mock_session(get_return=_ok_response("<html><body><p>Hello</p></body></html>"))
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp._build_pinned_session", return_value=session
            ) as mock_build:
                result = tools.web_fetch("https://example.com/page")
        assert "Hello" in result
        mock_build.assert_called_once()
        session.get.assert_called_once()


class TestPinnedIPAdapter:
    """CR-5: connect to the validated IP but verify TLS against the real hostname."""

    def test_replace_host_preserves_port_and_path(self):
        assert _replace_host("https://example.com/p?q=1", "1.2.3.4") == "https://1.2.3.4/p?q=1"
        assert _replace_host("http://example.com:8080/x", "1.2.3.4") == "http://1.2.3.4:8080/x"

    def test_adapter_dials_ip_but_verifies_hostname(self):
        """send() targets the pinned IP, keeps Host=hostname, and forces SNI/verify to hostname."""
        adapter = _PinnedIPAdapter("example.com", "93.184.216.34")
        request = MagicMock()
        request.url = "https://example.com/page"
        request.headers = {}

        captured = {}

        def fake_super_send(req, **kwargs):
            captured["url"] = req.url
            captured["host"] = req.headers.get("Host")
            return MagicMock()

        with patch("requests.adapters.HTTPAdapter.send", side_effect=fake_super_send):
            adapter.send(request)

        # Connection dials the validated IP; cert/SNI verify the real host.
        assert "93.184.216.34" in captured["url"]
        assert "example.com" not in captured["url"]
        assert captured["host"] == "example.com"
        assert adapter.poolmanager.connection_pool_kw["server_hostname"] == "example.com"
        assert adapter.poolmanager.connection_pool_kw["assert_hostname"] == "example.com"

    def test_build_pinned_session_mounts_adapter(self):
        session = _build_pinned_session("example.com", "93.184.216.34")
        try:
            https_adapter = session.get_adapter("https://example.com/")
            assert isinstance(https_adapter, _PinnedIPAdapter)
            assert https_adapter._pinned_ip == "93.184.216.34"
            assert https_adapter._hostname == "example.com"
        finally:
            session.close()

    def test_https_fetch_keeps_hostname_url_and_does_not_disable_verify(self):
        """The request URL passed to the session keeps the original https host (SNI/cert),
        and TLS verification is never disabled."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        session = _mock_session(get_return=_ok_response("<html><body>ok</body></html>"))
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp._build_pinned_session", return_value=session
            ):
                tools.web_fetch("https://example.com/page")
        call_url = session.get.call_args[0][0]
        assert call_url == "https://example.com/page"  # hostname retained for TLS
        assert session.get.call_args.kwargs.get("verify", True) is not False


class TestWebFetchRedirectValidation:
    def test_blocks_redirect_to_private_ip(self):
        """A 302 whose Location is a link-local IP is blocked on the next hop."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        session = _mock_session(get_return=redirect)
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp._build_pinned_session", return_value=session):
                result = tools.web_fetch("http://example.com/redirect")
        assert isinstance(result, dict)
        assert "error" in result

    def test_follows_valid_redirect(self):
        """A redirect to a public URL is followed and final content returned."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "http://example.com/final"}
        final = _ok_response("<html><body>Final content</body></html>")
        session = _mock_session(get_side_effect=[redirect, final])
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp._build_pinned_session", return_value=session):
                result = tools.web_fetch("http://example.com/redirect")
        assert "Final content" in result

    def test_blocks_too_many_redirects(self):
        """An infinite redirect loop is capped and returns error."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "http://example.com/loop"}
        session = _mock_session(get_return=redirect)
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp._build_pinned_session", return_value=session):
                result = tools.web_fetch("http://example.com/start")
        assert isinstance(result, dict)
        assert "error" in result

    def test_relative_redirect_resolved_via_urljoin(self):
        """A relative Location is resolved against the current URL, not treated as hostname=None."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "/final"}  # relative
        final = _ok_response("<html><body>Reached</body></html>")
        session = _mock_session(get_side_effect=[redirect, final])
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp._build_pinned_session", return_value=session):
                result = tools.web_fetch("https://example.com/start")
        assert "Reached" in result
        second_url = session.get.call_args_list[1][0][0]
        assert second_url == "https://example.com/final"


class TestWebFetchSchemeValidationOnRedirects:
    """Non-HTTP(S) redirect targets are rejected on the next hop (validated every hop)."""

    def _assert_scheme_blocked(self, location, scheme_token):
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": location}
        session = _mock_session(get_return=redirect)
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp._build_pinned_session", return_value=session):
                result = tools.web_fetch("http://example.com/start")
        assert isinstance(result, dict)
        assert "error" in result
        assert scheme_token in result["error"]
        # Only the initial hop issued a request.
        assert session.get.call_count == 1

    def test_blocks_redirect_to_ftp_scheme(self):
        self._assert_scheme_blocked("ftp://example.com/file.txt", "ftp")

    def test_blocks_redirect_to_gopher_scheme(self):
        self._assert_scheme_blocked("gopher://example.com/1/", "gopher")

    def test_blocks_redirect_to_file_scheme(self):
        self._assert_scheme_blocked("file:///etc/passwd", "file")


class TestWebFetchDNSPinning:
    """DNS is resolved once and pinned to the validated IP via the session adapter,
    while the request URL keeps the hostname so TLS verification uses the real host."""

    def test_session_get_uses_hostname_url_and_pins_ip(self):
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        session = _mock_session(get_return=_ok_response("<html><body>ok</body></html>"))
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp._build_pinned_session", return_value=session
            ) as mock_build:
                tools.web_fetch("http://example.com/page")
        # URL keeps the hostname (adapter pins the IP internally).
        call_url = session.get.call_args[0][0]
        assert call_url == "http://example.com/page"
        # The pinned IP was supplied to the adapter builder.
        build_args = mock_build.call_args[0]
        assert build_args[0] == "example.com"
        assert build_args[1] == "93.184.216.34"

    def test_redirect_hop_pins_resolved_ip(self):
        """Each redirect hop resolves+pins the hop's own hostname to its validated IP."""
        tools = ResearchTools()

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "redirect.example.com":
                return [(2, 1, 6, "", ("93.184.216.100", port or 80))]
            return [(2, 1, 6, "", ("93.184.216.34", port or 80))]

        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "http://redirect.example.com/final"}
        final = _ok_response("<html><body>Final</body></html>")
        session = _mock_session(get_side_effect=[redirect, final])
        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with patch(
                "ironclaude.research_mcp._build_pinned_session", return_value=session
            ) as mock_build:
                result = tools.web_fetch("http://example.com/start")
        assert "Final" in result
        # Second hop built a session pinned to the redirect host's IP.
        second_build = mock_build.call_args_list[1][0]
        assert second_build[0] == "redirect.example.com"
        assert second_build[1] == "93.184.216.100"
        # And issued the request against the hostname URL, not the IP.
        assert session.get.call_args_list[1][0][0] == "http://redirect.example.com/final"
