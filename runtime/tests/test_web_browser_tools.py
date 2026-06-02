from __future__ import annotations

from typing import Any
from unittest.mock import patch

from local_agent_runtime.tools.browser import build_browser_tool
from local_agent_runtime.tools.web_fetch import build_web_fetch_tool


class _Response:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._body = body

    def read(self, _size: int | None = None) -> bytes:
        return self._body


def _step_labels(result: dict[str, Any]) -> list[str]:
    return [step["label"] for step in result.get("steps", [])]


def test_web_fetch_result_includes_runtime_steps() -> None:
    tool = build_web_fetch_tool(policy_guard=None, store=None)["handler"]

    with patch("local_agent_runtime.tools.web_fetch.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _Response(b"hello", content_type="text/plain; charset=utf-8")
        result = tool({"url": "https://example.com/docs", "method": "GET"})

    assert result["status"] == "ok"
    assert _step_labels(result) == ["request", "response", "decode"]
    assert result["steps"][0]["summary"] == "GET https://example.com/docs"
    assert "HTTP 200" in result["steps"][1]["summary"]
    assert "text/plain" in result["steps"][2]["summary"]


def test_browser_read_result_includes_runtime_steps() -> None:
    tool = build_browser_tool(policy_guard=None, store=None)["handler"]
    html = b"<html><head><title>Docs</title></head><body><h1>Hello</h1><p>World</p></body></html>"

    with patch("local_agent_runtime.tools.browser.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _Response(html)
        result = tool({"url": "https://example.com/docs", "action": "read"})

    assert result["status"] == "ok"
    assert result["title"] == "Docs"
    assert "Hello" in result["content"]
    assert _step_labels(result) == ["request", "response", "decode", "extract"]
    assert "text characters" in result["steps"][-1]["summary"]
