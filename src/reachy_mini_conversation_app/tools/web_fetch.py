import logging
from html.parser import HTMLParser
from typing import Any

import httpx

from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 12_000
_TIMEOUT_S = 15.0
_SKIP_TAGS = {"script", "style", "noscript", "svg", "path"}


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text converter using stdlib only."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0
        self._title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self._title_parts).strip()

    @property
    def text(self) -> str:
        import re
        raw = "".join(self._parts)
        # Collapse runs of whitespace/newlines
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _extract(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.title, parser.text


class WebFetch(Tool):
    """Fetch the text content of a web page by URL."""

    name = "web_fetch"
    description = (
        "Fetch the visible text content of a public web page. "
        "Use this to look up real-time information such as news, documentation, weather, or any URL the user mentions. "
        "Returns the page title and a plain-text excerpt of the content."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch (must start with https:// or http://).",
            },
        },
        "required": ["url"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        url: str = (kwargs.get("url") or "").strip()
        if not url:
            return {"error": "url must be a non-empty string"}
        if not (url.startswith("https://") or url.startswith("http://")):
            return {"error": "url must start with https:// or http://"}

        logger.info("Tool call: web_fetch url=%s", url[:200])

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=_TIMEOUT_S,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ReachyMiniBot/1.0)"},
            ) as client:
                response = await client.get(url)
        except httpx.TimeoutException:
            return {"error": f"Request timed out after {_TIMEOUT_S:.0f}s"}
        except httpx.RequestError as exc:
            return {"error": f"Network error: {exc}"}

        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}", "url": str(response.url)}

        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            title, text = _extract(response.text)
        elif "text" in content_type:
            title, text = "", response.text.strip()
        else:
            return {"error": f"Unsupported content type: {content_type}", "url": str(response.url)}

        if len(text) > _MAX_CONTENT_CHARS:
            text = text[:_MAX_CONTENT_CHARS] + "\n\n[content truncated]"

        result: dict[str, Any] = {"url": str(response.url), "content": text}
        if title:
            result["title"] = title
        return result
