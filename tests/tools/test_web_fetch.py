"""Tests for the web_fetch tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.web_fetch import WebFetch, _extract


@pytest.fixture
def deps() -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock())


# --- unit tests for the HTML extractor ---

def test_extract_returns_title_and_text() -> None:
    html = "<html><head><title>Hello World</title></head><body><p>Some content.</p></body></html>"
    title, text = _extract(html)
    assert title == "Hello World"
    assert "Some content." in text


def test_extract_strips_scripts_and_styles() -> None:
    html = "<html><body><script>alert(1)</script><style>body{}</style><p>Visible</p></body></html>"
    _, text = _extract(html)
    assert "alert" not in text
    assert "body{}" not in text
    assert "Visible" in text


# --- tool integration tests ---

@pytest.mark.asyncio
async def test_web_fetch_returns_content_for_html_page(deps: ToolDependencies) -> None:
    html = "<html><head><title>Test Page</title></head><body><p>Hello from the web.</p></body></html>"
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.text = html
    mock_response.url = httpx.URL("https://example.com/")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("reachy_mini_conversation_app.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await WebFetch()(deps, url="https://example.com/")

    assert result["title"] == "Test Page"
    assert "Hello from the web." in result["content"]
    assert result["url"] == "https://example.com/"


@pytest.mark.asyncio
async def test_web_fetch_rejects_empty_url(deps: ToolDependencies) -> None:
    result = await WebFetch()(deps, url="")
    assert "error" in result


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_url(deps: ToolDependencies) -> None:
    result = await WebFetch()(deps, url="ftp://example.com/file.txt")
    assert "error" in result


@pytest.mark.asyncio
async def test_web_fetch_returns_error_on_http_404(deps: ToolDependencies) -> None:
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 404
    mock_response.url = httpx.URL("https://example.com/missing")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("reachy_mini_conversation_app.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await WebFetch()(deps, url="https://example.com/missing")

    assert "error" in result
    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_returns_error_on_timeout(deps: ToolDependencies) -> None:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with patch("reachy_mini_conversation_app.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await WebFetch()(deps, url="https://example.com/")

    assert "error" in result
    assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_truncates_large_content(deps: ToolDependencies) -> None:
    big_text = "word " * 10_000
    html = f"<html><body><p>{big_text}</p></body></html>"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = html
    mock_response.url = httpx.URL("https://example.com/big")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("reachy_mini_conversation_app.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await WebFetch()(deps, url="https://example.com/big")

    assert "[content truncated]" in result["content"]
    assert len(result["content"]) < len(big_text)
