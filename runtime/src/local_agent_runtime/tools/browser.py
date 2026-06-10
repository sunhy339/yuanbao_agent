"""browser tool — fetch web pages and extract readable text content."""

from __future__ import annotations

import re
import urllib.request
import urllib.error
from typing import Any

_DEFAULT_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def _html_to_text(html: str) -> str:
    """Crude HTML-to-text: strip tags, decode entities, collapse whitespace."""
    # Remove scripts and styles
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert block tags to newlines
    text = re.sub(r"<(br|p|div|li|h[1-6]|tr|hr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_browser_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def browser(params: dict[str, Any]) -> dict[str, Any]:
        url = str(params.get("url", "")).strip()
        if not url:
            raise ValueError("url is required")

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Only http/https URLs are supported: {url}")

        action = str(params.get("action", "read"))
        timeout = int(params.get("timeout", _DEFAULT_TIMEOUT))
        timeout = max(5, min(timeout, 120))
        max_bytes = int(params.get("max_bytes", _MAX_RESPONSE_BYTES))
        max_bytes = max(4096, min(max_bytes, 2 * 1024 * 1024))
        steps = [
            _step("request", "running", f"{action} {url}"),
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; YuanbaoAgent/1.0; +https://github.com/yuanbao-agent)",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        }

        request = urllib.request.Request(url, headers=headers)

        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:4096]
            except Exception:  # noqa: BLE001
                pass
            return {
                "status": "http_error",
                "toolName": "browser",
                "statusCode": exc.code,
                "url": url,
                "action": action,
                "error": f"HTTP {exc.code}: {exc.reason}",
                "body": error_body,
                "contentSource": "web",
                "contentTrust": "untrusted",
                "steps": [
                    _step("request", "completed", f"{action} {url}"),
                    _step("response", "blocked", f"HTTP {exc.code}: {exc.reason}"),
                ],
            }
        except urllib.error.URLError as exc:
            return {
                "status": "network_error",
                "toolName": "browser",
                "url": url,
                "action": action,
                "error": str(exc.reason),
                "contentSource": "web",
                "contentTrust": "untrusted",
                "steps": [
                    _step("request", "blocked", f"{action} {url}"),
                    _step("network", "blocked", str(exc.reason)),
                ],
            }

        status_code = response.status if hasattr(response, "status") else 200
        content_type = response.headers.get("Content-Type", "")
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        steps[0] = _step("request", "completed", f"{action} {url}")
        steps.append(_step("response", "completed", f"HTTP {status_code}; {len(raw)} bytes"))
        if truncated:
            steps.append(_step("truncate", "completed", f"limited to {max_bytes} bytes"))

        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()

        try:
            html = raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = raw.decode("utf-8", errors="replace")
            encoding = "utf-8"
        steps.append(_step("decode", "completed", f"{encoding}; {content_type or 'unknown content type'}"))

        if action == "read":
            text = _html_to_text(html)
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""
            title = re.sub(r"<[^>]+>", "", title)
            steps.append(_step("extract", "completed", f"{len(text)} text characters"))
            return {
                "status": "ok",
                "toolName": "browser",
                "statusCode": status_code,
                "url": url,
                "action": action,
                "title": title,
                "contentType": content_type,
                "content": text,
                "truncated": truncated,
                "bytesRead": len(raw),
                "contentSource": "web",
                "contentTrust": "untrusted",
                "steps": steps,
            }

        if action == "raw":
            steps.append(_step("extract", "completed", f"{len(html)} raw HTML characters"))
            return {
                "status": "ok",
                "toolName": "browser",
                "statusCode": status_code,
                "url": url,
                "action": action,
                "contentType": content_type,
                "content": html,
                "truncated": truncated,
                "bytesRead": len(raw),
                "contentSource": "web",
                "contentTrust": "untrusted",
                "steps": steps,
            }

        raise ValueError(f"Unknown action: {action}. Use read or raw")

    return {"handler": browser}
