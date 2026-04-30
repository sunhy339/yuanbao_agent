"""web_fetch tool — fetch content from a URL."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any


_DEFAULT_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB


def build_web_fetch_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None) -> dict[str, Any]:
    def web_fetch(params: dict[str, Any]) -> dict[str, Any]:
        url = str(params.get("url", "")).strip()
        if not url:
            raise ValueError("url is required")

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Only http/https URLs are supported: {url}")

        method = str(params.get("method", "GET")).upper()
        headers = params.get("headers") or {}
        body = params.get("body")
        timeout = int(params.get("timeout", _DEFAULT_TIMEOUT))
        timeout = max(5, min(timeout, 120))
        max_bytes = int(params.get("max_bytes", _MAX_RESPONSE_BYTES))
        max_bytes = max(1024, min(max_bytes, 2 * 1024 * 1024))

        req_data = None
        if body is not None:
            if isinstance(body, dict):
                req_data = json.dumps(body).encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"
            elif isinstance(body, str):
                req_data = body.encode("utf-8")
            else:
                req_data = str(body).encode("utf-8")

        request = urllib.request.Request(url, data=req_data, headers=headers, method=method)

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
                "statusCode": exc.code,
                "url": url,
                "error": f"HTTP {exc.code}: {exc.reason}",
                "body": error_body,
            }
        except urllib.error.URLError as exc:
            return {
                "status": "network_error",
                "url": url,
                "error": str(exc.reason),
            }

        status_code = response.status if hasattr(response, "status") else 200
        content_type = response.headers.get("Content-Type", "")
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]

        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()

        try:
            text = raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="replace")

        return {
            "status": "ok",
            "statusCode": status_code,
            "url": url,
            "contentType": content_type,
            "content": text,
            "truncated": truncated,
            "bytesRead": len(raw),
        }

    return {"handler": web_fetch}
