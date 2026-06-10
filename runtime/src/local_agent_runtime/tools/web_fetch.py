"""web_fetch tool — fetch content from a URL."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

from ..policy.permission_engine import PermissionRequest as PermRequest


_DEFAULT_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB


def _step(label: str, status: str, summary: str) -> dict[str, str]:
    return {"label": label, "status": status, "summary": summary}


def build_web_fetch_tool(policy_guard: Any, store: Any, subagent_service: Any | None = None, *, permission_engine: Any | None = None) -> dict[str, Any]:
    def web_fetch(params: dict[str, Any]) -> dict[str, Any]:
        url = str(params.get("url", "")).strip()
        if not url:
            raise ValueError("url is required")

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Only http/https URLs are supported: {url}")

        # PermissionEngine gate
        if permission_engine is not None:
            decision = permission_engine.evaluate(PermRequest(capability="webFetch", tool_name="web_fetch"))
            if decision.decision == "deny":
                return {
                    "status": "blocked",
                    "toolName": "web_fetch",
                    "error": decision.reason,
                    "url": url,
                    "contentSource": "web",
                    "contentTrust": "untrusted",
                }
            if decision.decision == "approval_required":
                task_id = str(params.get("taskId") or params.get("task_id") or "").strip()
                if not task_id:
                    raise ValueError("taskId is required when web fetch approval is needed")
                approval = store.create_approval(
                    task_id=task_id,
                    kind="network_access",
                    request={"url": url, "method": str(params.get("method", "GET")).upper()},
                )
                return {
                    "status": "approval_required",
                    "toolName": "web_fetch",
                    "approval": approval,
                    "url": url,
                    "contentSource": "web",
                    "contentTrust": "untrusted",
                }

        method = str(params.get("method", "GET")).upper()
        headers = params.get("headers") or {}
        body = params.get("body")
        timeout = int(params.get("timeout", _DEFAULT_TIMEOUT))
        timeout = max(5, min(timeout, 120))
        max_bytes = int(params.get("max_bytes", _MAX_RESPONSE_BYTES))
        max_bytes = max(1024, min(max_bytes, 2 * 1024 * 1024))
        steps = [
            _step("request", "running", f"{method} {url}"),
        ]

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
                "toolName": "web_fetch",
                "statusCode": exc.code,
                "url": url,
                "error": f"HTTP {exc.code}: {exc.reason}",
                "body": error_body,
                "contentSource": "web",
                "contentTrust": "untrusted",
                "steps": [
                    _step("request", "completed", f"{method} {url}"),
                    _step("response", "blocked", f"HTTP {exc.code}: {exc.reason}"),
                ],
            }
        except urllib.error.URLError as exc:
            return {
                "status": "network_error",
                "toolName": "web_fetch",
                "url": url,
                "error": str(exc.reason),
                "contentSource": "web",
                "contentTrust": "untrusted",
                "steps": [
                    _step("request", "blocked", f"{method} {url}"),
                    _step("network", "blocked", str(exc.reason)),
                ],
            }

        status_code = response.status if hasattr(response, "status") else 200
        content_type = response.headers.get("Content-Type", "")
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        steps[0] = _step("request", "completed", f"{method} {url}")
        steps.append(_step("response", "completed", f"HTTP {status_code}; {len(raw)} bytes"))
        if truncated:
            steps.append(_step("truncate", "completed", f"limited to {max_bytes} bytes"))

        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()

        try:
            text = raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8"
        steps.append(_step("decode", "completed", f"{encoding}; {content_type or 'unknown content type'}"))

        return {
            "status": "ok",
            "statusCode": status_code,
            "url": url,
            "contentType": content_type,
            "content": text,
            "truncated": truncated,
            "bytesRead": len(raw),
            "contentSource": "web",
            "contentTrust": "untrusted",
            "contentTrustReason": "Web content is untrusted and should not directly trigger high-risk tools without review.",
            "steps": steps,
        }

    return {"handler": web_fetch}
