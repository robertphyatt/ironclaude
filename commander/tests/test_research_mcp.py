# tests/test_research_mcp.py
"""Tests for the research MCP server business logic."""

import pytest
from unittest.mock import MagicMock, patch

from ironclaude.research_mcp import ResearchTools, create_research_mcp_server


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


class TestWebFetch:
    def test_fetches_and_converts_html(self):
        """web_fetch fetches URL and converts HTML to text."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><h1>Hello World</h1><p>Content here</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp.requests.get", return_value=mock_response):
                result = tools.web_fetch("https://example.com")

        assert "Hello World" in result
        assert "Content here" in result
        # Should not contain raw HTML tags
        assert "<h1>" not in result

    def test_truncates_long_content(self):
        """web_fetch truncates content exceeding 10K chars."""
        long_html = "<html><body>" + "x" * 20000 + "</body></html>"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = long_html
        mock_response.raise_for_status = MagicMock()

        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp.requests.get", return_value=mock_response):
                result = tools.web_fetch("https://example.com/long")

        assert len(result) <= 10000

    def test_handles_fetch_error(self):
        """web_fetch returns error dict on exception."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp.requests.get",
                side_effect=Exception("Connection refused"),
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
    """RED tests for H1 SSRF vulnerability in web_fetch.

    Primary RED signal: mock_get.assert_not_called() fails before the fix
    (requests.get IS called without URL validation) and passes after
    (_validate_url short-circuits before the HTTP call).
    """

    def test_blocks_metadata_endpoint(self):
        """web_fetch blocks requests to link-local IP 169.254.169.254."""
        tools = ResearchTools()
        with patch("ironclaude.research_mcp.requests.get") as mock_get:
            result = tools.web_fetch("http://169.254.169.254/latest/meta-data/")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_blocks_localhost(self):
        """web_fetch blocks requests to localhost by name."""
        tools = ResearchTools()
        with patch("ironclaude.research_mcp.requests.get") as mock_get:
            result = tools.web_fetch("http://localhost/admin")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_blocks_private_10x(self):
        """web_fetch blocks requests to private IP 10.0.0.1."""
        tools = ResearchTools()
        with patch("ironclaude.research_mcp.requests.get") as mock_get:
            result = tools.web_fetch("http://10.0.0.1/internal")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_blocks_ftp_scheme(self):
        """web_fetch blocks non-http/https schemes."""
        tools = ResearchTools()
        with patch("ironclaude.research_mcp.requests.get") as mock_get:
            result = tools.web_fetch("ftp://example.com/file.txt")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_blocks_credentials_in_url(self):
        """web_fetch blocks URLs with embedded credentials."""
        tools = ResearchTools()
        with patch("ironclaude.research_mcp.requests.get") as mock_get:
            result = tools.web_fetch("https://user:pass@example.com/")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_blocks_ipv6_loopback(self):
        """web_fetch blocks IPv6 loopback address ::1."""
        tools = ResearchTools()
        with patch("ironclaude.research_mcp.requests.get") as mock_get:
            result = tools.web_fetch("http://[::1]/admin")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_blocks_hostname_resolving_to_private_ip(self):
        """web_fetch blocks hostnames that DNS-resolve to private IPs."""
        tools = ResearchTools()
        private_addr_info = [(2, 1, 6, "", ("10.0.0.1", 0))]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>ok</body></html>"
        mock_response.raise_for_status = MagicMock()
        with patch("socket.getaddrinfo", return_value=private_addr_info):
            with patch("ironclaude.research_mcp.requests.get", return_value=mock_response) as mock_get:
                result = tools.web_fetch("http://internal.corp.example.com/")
        assert isinstance(result, dict)
        assert "error" in result
        mock_get.assert_not_called()

    def test_allows_valid_https(self):
        """web_fetch allows legitimate public HTTPS URLs (regression)."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 0))]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp.requests.get", return_value=mock_response) as mock_get:
                result = tools.web_fetch("https://example.com/page")
        assert "Hello" in result
        mock_get.assert_called_once()


class TestWebFetchRedirectValidation:
    """H1: requests.get must not follow redirects automatically; each Location
    header is validated before following."""

    def test_blocks_redirect_to_private_ip(self):
        """A 302 response whose Location is a link-local IP is blocked."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp.requests.get", return_value=mock_redirect):
                result = tools.web_fetch("http://example.com/redirect")
        assert isinstance(result, dict)
        assert "error" in result

    def test_follows_valid_redirect(self):
        """A redirect to a public URL is followed and final content is returned."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "http://example.com/final"}
        mock_final = MagicMock()
        mock_final.status_code = 200
        mock_final.text = "<html><body>Final content</body></html>"
        mock_final.raise_for_status = MagicMock()
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp.requests.get",
                side_effect=[mock_redirect, mock_final],
            ):
                result = tools.web_fetch("http://example.com/redirect")
        assert "Final content" in result

    def test_blocks_too_many_redirects(self):
        """An infinite redirect loop is capped at max_redirects and returns error."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "http://example.com/loop"}
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch("ironclaude.research_mcp.requests.get", return_value=mock_redirect):
                result = tools.web_fetch("http://example.com/start")
        assert isinstance(result, dict)
        assert "error" in result


class TestWebFetchDNSPinnedRedirects:
    """M1 TOCTOU fix: each redirect hop must resolve DNS once and pass the IP-literal
    URL to requests.get — never the raw hostname from the Location header."""

    def test_redirect_hop_uses_resolved_ip(self):
        """requests.get is called with the IP-literal URL for redirect hops, not the hostname."""
        tools = ResearchTools()

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "redirect.example.com":
                return [(2, 1, 6, "", ("93.184.216.100", port or 80))]
            return [(2, 1, 6, "", ("93.184.216.34", port or 80))]

        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "http://redirect.example.com/final"}

        mock_final = MagicMock()
        mock_final.status_code = 200
        mock_final.text = "<html><body>Final</body></html>"
        mock_final.raise_for_status = MagicMock()

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with patch(
                "ironclaude.research_mcp.requests.get",
                side_effect=[mock_redirect, mock_final],
            ) as mock_get:
                result = tools.web_fetch("http://example.com/start")

        assert "Final" in result
        second_call_url = mock_get.call_args_list[1][0][0]
        assert "93.184.216.100" in second_call_url, (
            f"Redirect hop must use resolved IP URL, got: {second_call_url}"
        )
        assert "redirect.example.com" not in second_call_url, (
            f"Redirect hop must not use hostname, got: {second_call_url}"
        )

    def test_redirect_hop_sets_host_header(self):
        """Host header is set to the original redirect hostname on each hop."""
        tools = ResearchTools()

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "redirect.example.com":
                return [(2, 1, 6, "", ("93.184.216.100", port or 80))]
            return [(2, 1, 6, "", ("93.184.216.34", port or 80))]

        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "http://redirect.example.com/final"}

        mock_final = MagicMock()
        mock_final.status_code = 200
        mock_final.text = "<html><body>Final</body></html>"
        mock_final.raise_for_status = MagicMock()

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with patch(
                "ironclaude.research_mcp.requests.get",
                side_effect=[mock_redirect, mock_final],
            ) as mock_get:
                tools.web_fetch("http://example.com/start")

        second_call_headers = mock_get.call_args_list[1][1].get("headers")
        host_header = second_call_headers.get("Host") if second_call_headers else None
        assert host_header == "redirect.example.com", (
            f"Expected Host: redirect.example.com on redirect hop, got: {host_header}"
        )

    def test_redirect_hop_blocks_hostname_resolving_to_private(self):
        """A redirect whose hostname DNS-resolves to a private IP is blocked."""
        tools = ResearchTools()

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "evil.internal":
                return [(2, 1, 6, "", ("10.0.0.1", port or 80))]
            return [(2, 1, 6, "", ("93.184.216.34", port or 80))]

        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "http://evil.internal/secret"}

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            with patch(
                "ironclaude.research_mcp.requests.get", return_value=mock_redirect
            ) as mock_get:
                result = tools.web_fetch("http://example.com/start")

        assert isinstance(result, dict)
        assert "error" in result
        assert mock_get.call_count == 1, (
            f"Expected 1 HTTP call (initial only), got {mock_get.call_count}"
        )


class TestWebFetchSchemeValidationOnRedirects:
    """L2: _resolve_and_validate must reject non-HTTP/HTTPS schemes on redirect targets.

    TT2 gap: _validate_url is not called for redirect Location headers; only
    _resolve_and_validate is. Before the fix, ftp/gopher/etc. schemes pass
    through unblocked. After the fix, they raise ValueError at the scheme check.

    RED signal: mock_get.call_count == 1 fails before the fix (requests.get IS
    called a second time for the non-HTTP redirect target) and passes after
    (_resolve_and_validate raises before the second HTTP call).
    """

    def test_blocks_redirect_to_ftp_scheme(self):
        """A redirect whose Location is ftp:// is blocked; requests.get called once only."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "ftp://example.com/file.txt"}
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp.requests.get", return_value=mock_redirect
            ) as mock_get:
                result = tools.web_fetch("http://example.com/start")
        assert isinstance(result, dict)
        assert "error" in result
        assert "ftp" in result["error"]
        assert mock_get.call_count == 1, (
            f"Expected 1 HTTP call (initial only), got {mock_get.call_count}"
        )

    def test_blocks_redirect_to_gopher_scheme(self):
        """A redirect whose Location is gopher:// is blocked; requests.get called once only."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "gopher://example.com/1/"}
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp.requests.get", return_value=mock_redirect
            ) as mock_get:
                result = tools.web_fetch("http://example.com/start")
        assert isinstance(result, dict)
        assert "error" in result
        assert "gopher" in result["error"]
        assert mock_get.call_count == 1, (
            f"Expected 1 HTTP call (initial only), got {mock_get.call_count}"
        )

    def test_blocks_redirect_to_file_scheme(self):
        """A redirect whose Location is file:// is blocked; requests.get called once only."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_redirect = MagicMock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "file:///etc/passwd"}
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp.requests.get", return_value=mock_redirect
            ) as mock_get:
                result = tools.web_fetch("http://example.com/start")
        assert isinstance(result, dict)
        assert "error" in result
        assert "file" in result["error"]
        assert mock_get.call_count == 1, (
            f"Expected 1 HTTP call (initial only), got {mock_get.call_count}"
        )


class TestWebFetchDNSPinning:
    """M1: DNS is resolved once and pinned; requests.get connects to the resolved IP."""

    def test_requests_get_uses_resolved_ip(self):
        """requests.get is called with the pre-resolved IP URL, not the hostname."""
        tools = ResearchTools()
        public_addr_info = [(2, 1, 6, "", ("93.184.216.34", 80))]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>ok</body></html>"
        mock_response.raise_for_status = MagicMock()
        with patch("socket.getaddrinfo", return_value=public_addr_info):
            with patch(
                "ironclaude.research_mcp.requests.get", return_value=mock_response
            ) as mock_get:
                tools.web_fetch("http://example.com/page")
        call_url = mock_get.call_args[0][0]
        assert "93.184.216.34" in call_url
        assert "example.com" not in call_url
