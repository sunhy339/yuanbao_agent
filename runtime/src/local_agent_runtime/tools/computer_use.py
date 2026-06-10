"""computer_use tool - permission-gated desktop/browser actions."""

from __future__ import annotations

import base64
import asyncio
import inspect
import io
import json
import os
import platform
import socket
import time
from typing import Any

from ._shared import approval_by_id_or_none
from ..policy.permission_engine import PermissionRequest as PermRequest


_ALLOWED_ACTIONS = {"inspect", "screenshot", "click", "type", "key", "scroll"}
_EXECUTION_READY_ACTIONS = {"inspect", "screenshot", "click", "type", "key", "scroll"}
_DESKTOP_EXECUTOR_CAPABILITIES = frozenset({
    "click.coordinates",
    "type.text",
    "key.text",
    "scroll.direction",
})


def _request_payload(params: dict[str, Any]) -> dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported computer_use action: {action or '<empty>'}")
    target = str(params.get("target") or params.get("app") or params.get("application") or "desktop").strip() or "desktop"
    payload: dict[str, Any] = {
        "taskId": str(params.get("taskId") or params.get("task_id") or "").strip(),
        "app": target,
        "target": target,
        "action": action,
        "permission": str(params.get("permission") or params.get("summary") or f"{action} {target}").strip(),
    }
    for key in ("details", "selector", "text", "x", "y", "direction", "amount", "url", "pageId", "browserContextId"):
        value = params.get(key)
        if value not in (None, ""):
            payload[key] = value
    return payload


def _approval_for_request(store: Any, task_id: str, request: dict[str, Any], approval_id: str | None) -> dict[str, Any] | None:
    approval = approval_by_id_or_none(store, approval_id)
    if approval is not None:
        if approval["taskId"] != task_id:
            raise ValueError("Approval does not belong to the active task")
        if approval["kind"] != "computer_use":
            raise ValueError("Approval kind mismatch")
        stored_request = json.loads(approval["requestJson"] or "{}")
        if stored_request != request:
            raise ValueError("Approval request does not match the computer_use request")
        return approval
    if hasattr(store, "find_approval"):
        return store.find_approval(
            task_id=task_id,
            kind="computer_use",
            request=request,
        )
    return None


def _numeric_request_value(request: dict[str, Any], key: str) -> float | None:
    value = request.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_request_value(request: dict[str, Any], key: str) -> int | None:
    value = _numeric_request_value(request, key)
    if value is None:
        return None
    return int(value)


def _blocked_executor_result(
    request: dict[str, Any],
    approval_id: str | None,
    *,
    error: str,
    failure_kind: str = "desktop_control_unavailable",
    recovery_hint: str = "Attach a screenshot or enable a desktop control backend.",
    step_summary: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "action": request["action"],
        "target": request["target"],
        "error": error,
        "request": request,
        "approvalId": approval_id,
        "failureKind": failure_kind,
        "recoveryHint": recovery_hint,
        "toolName": "computer_use",
        "contentSource": "computer",
        "contentTrust": "untrusted",
        "steps": [
            {
                "label": request["action"],
                "status": "blocked",
                "summary": step_summary or error,
            },
        ],
    }


def _request_selector(request: dict[str, Any]) -> str:
    return str(request.get("selector") or "").strip()


def _request_browser_target(request: dict[str, Any]) -> str:
    parts: list[str] = []
    for label, key in (("url", "url"), ("pageId", "pageId"), ("browserContextId", "browserContextId")):
        value = str(request.get(key) or "").strip()
        if value:
            parts.append(f"{label}={value}")
    return "; ".join(parts)


def _request_is_browser_target(request: dict[str, Any]) -> bool:
    if any(str(request.get(key) or "").strip() for key in ("url", "pageId", "browserContextId")):
        return True
    target = str(request.get("target") or request.get("app") or "").strip().lower()
    return any(token in target for token in ("browser", "tab", "web", "chrome", "edge", "firefox", "safari"))


def _selector_executor_required_result(
    request: dict[str, Any],
    approval_id: str | None,
    *,
    reason: str = "",
) -> dict[str, Any]:
    action = request["action"]
    selector = _request_selector(request)
    target_detail = _request_browser_target(request)
    context_text = f" ({target_detail})" if target_detail else ""
    reason_text = f" ({reason})" if reason else ""
    return _blocked_executor_result(
        request,
        approval_id,
        error=(
            f"computer_use {action} selector requires a browser DOM or accessibility executor"
            f"{context_text}{reason_text}."
        ),
        failure_kind="selector_executor_required",
        recovery_hint=(
            "Provide x/y coordinates from a screenshot, or enable a browser DOM/accessibility "
            f"executor that supports {action}.selector for selector {selector!r}."
        ),
    )


def _inspect_environment(request: dict[str, Any], approval_id: str | None) -> dict[str, Any]:
    target = request["target"]
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("SESSIONNAME")
    platform_label = platform.platform()
    summary = f"Computer Use permission approved for {request['permission']}."
    return {
        "status": "completed",
        "action": "inspect",
        "target": target,
        "summary": summary,
        "request": request,
        "approvalId": approval_id,
        "toolName": "computer_use",
        "contentSource": "computer",
        "contentTrust": "untrusted",
        "environment": {
            "platform": platform_label,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "hostname": socket.gethostname(),
            "display": display,
        },
        "steps": [
            {"label": "permission", "status": "completed", "summary": "Computer Use permission approved."},
            {"label": "environment", "status": "completed", "summary": f"{platform_label}; display={display or 'unavailable'}"},
        ],
    }


class _PyAutoGuiComputerUseExecutor:
    def __init__(self) -> None:
        try:
            import pyautogui  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"pyautogui is unavailable: {exc}") from exc
        self._pyautogui = pyautogui

    @staticmethod
    def name() -> str:
        return "pyautogui"

    @staticmethod
    def capabilities() -> set[str]:
        return set(_DESKTOP_EXECUTOR_CAPABILITIES)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request["action"]
        if action == "click":
            x = _numeric_request_value(request, "x")
            y = _numeric_request_value(request, "y")
            amount = _int_request_value(request, "amount") or 1
            if x is None or y is None:
                raise ValueError("click requires x and y coordinates")
            self._pyautogui.click(x=x, y=y, clicks=max(1, amount))
            return {
                "coordinates": {"x": x, "y": y},
                "clicks": max(1, amount),
            }
        if action == "type":
            text = str(request.get("text") or "")
            if not text:
                raise ValueError("type requires text")
            self._pyautogui.write(text, interval=0)
            return {
                "characters": len(text),
            }
        if action == "key":
            key = str(request.get("text") or request.get("selector") or "").strip()
            if not key:
                raise ValueError("key requires text or selector naming the key")
            self._pyautogui.press(key)
            return {
                "key": key,
            }
        if action == "scroll":
            direction = str(request.get("direction") or "down").strip().lower()
            amount = _int_request_value(request, "amount") or 5
            clicks = max(1, abs(amount))
            if direction in {"down", "right"}:
                clicks = -clicks
            self._pyautogui.scroll(clicks)
            return {
                "direction": direction,
                "amount": abs(amount),
            }
        raise ValueError(f"Unsupported computer_use action for pyautogui: {action}")


class _WindowsCtypesComputerUseExecutor:
    def __init__(self) -> None:
        if platform.system().lower() != "windows":
            raise RuntimeError("ctypes fallback is only available on Windows")
        import ctypes

        self._ctypes = ctypes
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    @staticmethod
    def name() -> str:
        return "windows_ctypes"

    @staticmethod
    def capabilities() -> set[str]:
        return {"click.coordinates", "scroll.direction"}

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request["action"]
        if action == "click":
            x = _int_request_value(request, "x")
            y = _int_request_value(request, "y")
            amount = _int_request_value(request, "amount") or 1
            if x is None or y is None:
                raise ValueError("click requires x and y coordinates")
            self._user32.SetCursorPos(x, y)
            for _ in range(max(1, amount)):
                self._user32.mouse_event(0x0002, 0, 0, 0, 0)
                self._user32.mouse_event(0x0004, 0, 0, 0, 0)
            return {
                "coordinates": {"x": x, "y": y},
                "clicks": max(1, amount),
            }
        if action == "scroll":
            direction = str(request.get("direction") or "down").strip().lower()
            amount = _int_request_value(request, "amount") or 5
            wheel_delta = 120 * max(1, abs(amount))
            if direction in {"down", "right"}:
                wheel_delta = -wheel_delta
            self._user32.mouse_event(0x0800, 0, 0, wheel_delta, 0)
            return {
                "direction": direction,
                "amount": abs(amount),
            }
        raise RuntimeError(f"ctypes fallback does not support {action!r}; install pyautogui or provide a custom executor")


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    raise RuntimeError("async computer-use executor methods cannot run while an event loop is already active")


def _compact_page_text(value: Any, *, limit: int = 4000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _playwright_page_id(request: dict[str, Any], page: Any) -> dict[str, Any]:
    return {
        "url": str(getattr(page, "url", "") or ""),
        "pageId": str(request.get("pageId") or request.get("url") or "default").strip() or "default",
        "browserContextId": str(request.get("browserContextId") or "default").strip() or "default",
    }


def _playwright_page_title(page: Any) -> str:
    title = getattr(page, "title", None)
    if not callable(title):
        return ""
    try:
        return str(_resolve_awaitable(title()) or "")
    except Exception:
        return ""


def _playwright_page_text(page: Any) -> str:
    try:
        locator = _resolve_awaitable(page.locator("body"))
        inner_text = getattr(locator, "inner_text", None)
        if callable(inner_text):
            return _compact_page_text(_resolve_awaitable(inner_text(timeout=1000)))
    except Exception:
        pass
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return ""
    try:
        return _compact_page_text(_resolve_awaitable(evaluate("() => document.body ? document.body.innerText : ''")))
    except Exception:
        return ""


def _normalize_browser_elements(elements: Any) -> list[dict[str, str]]:
    if not isinstance(elements, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in elements[:40]:
        if not isinstance(item, dict):
            continue
        element: dict[str, str] = {}
        for key in ("tag", "role", "label", "text", "type", "id", "testId", "name", "href"):
            value = _compact_page_text(item.get(key), limit=160)
            if value:
                element[key] = value
        if element:
            normalized.append(element)
    return normalized


def _playwright_page_elements(page: Any) -> list[dict[str, str]]:
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        return []
    script = r"""
() => Array.from(document.querySelectorAll('button,a,input,textarea,select,[role],[aria-label],[data-testid]')).slice(0, 40).map((el) => ({
  tag: el.tagName.toLowerCase(),
  role: el.getAttribute('role') || '',
  label: el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || '',
  text: ((el.innerText || el.value || '') + '').trim().replace(/\s+/g, ' ').slice(0, 160),
  type: el.getAttribute('type') || '',
  id: el.id || '',
  testId: el.getAttribute('data-testid') || '',
  name: el.getAttribute('name') || '',
  href: el.href || '',
}))
"""
    try:
        return _normalize_browser_elements(_resolve_awaitable(evaluate(script)))
    except Exception:
        return []


def _playwright_page_inspect(page: Any, request: dict[str, Any]) -> dict[str, Any]:
    identity = _playwright_page_id(request, page)
    title = _playwright_page_title(page)
    text = _playwright_page_text(page)
    elements = _playwright_page_elements(page)
    summary = f"Inspected browser page {identity['url'] or identity['pageId']}"
    if title:
        summary = f"{summary}: {title}"
    return {
        **identity,
        "title": title,
        "text": text,
        "elements": elements,
        "summary": summary,
    }


def _png_preview_from_bytes(png: bytes) -> dict[str, Any]:
    preview_png = png
    width: int | None = None
    height: int | None = None
    preview_width: int | None = None
    preview_height: int | None = None
    try:
        from PIL import Image  # type: ignore[import-not-found]

        image = Image.open(io.BytesIO(png))
        width, height = image.size
        preview = image.copy()
        preview.thumbnail((1280, 1280))
        preview_width, preview_height = preview.size
        buffer = io.BytesIO()
        preview.save(buffer, format="PNG")
        preview_png = buffer.getvalue()
    except Exception:
        pass
    result: dict[str, Any] = {
        "mimeType": "image/png",
        "bytes": len(preview_png),
        "imageDataUrl": "data:image/png;base64," + base64.b64encode(preview_png).decode("ascii"),
    }
    if width is not None and height is not None:
        result["width"] = width
        result["height"] = height
    if preview_width is not None and preview_height is not None:
        result["previewWidth"] = preview_width
        result["previewHeight"] = preview_height
    return result


def _playwright_page_screenshot(page: Any, request: dict[str, Any]) -> dict[str, Any]:
    screenshot = getattr(page, "screenshot", None)
    if not callable(screenshot):
        raise ValueError("playwright page screenshot is unavailable")
    png = _resolve_awaitable(screenshot(type="png"))
    if not isinstance(png, (bytes, bytearray)):
        raise ValueError("playwright page screenshot did not return PNG bytes")
    identity = _playwright_page_id(request, page)
    image = _png_preview_from_bytes(bytes(png))
    summary = f"Captured browser screenshot for {identity['url'] or identity['pageId']}."
    return {
        **identity,
        **image,
        "capturedAt": int(time.time() * 1000),
        "summary": summary,
    }


class _PlaywrightPageComputerUseExecutor:
    def __init__(self, page: Any) -> None:
        self._page = page

    @staticmethod
    def name() -> str:
        return "playwright_page"

    def capabilities(self) -> set[str]:
        capabilities = {
            "click.selector",
            "type.selector",
            "key.text",
            "scroll.direction",
            "scroll.selector",
        }
        if callable(getattr(self._page, "locator", None)) or callable(getattr(self._page, "evaluate", None)):
            capabilities.add("inspect.browser")
        if callable(getattr(self._page, "screenshot", None)):
            capabilities.add("screenshot.browser")
        return capabilities

    def _locator(self, selector: str) -> Any:
        locator = self._page.locator(selector)
        return _resolve_awaitable(locator)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request["action"]
        selector = str(request.get("selector") or "").strip()
        if action == "inspect":
            return _playwright_page_inspect(self._page, request)
        if action == "screenshot":
            return _playwright_page_screenshot(self._page, request)
        if action == "click":
            if not selector:
                raise ValueError("playwright_page click requires selector")
            _resolve_awaitable(self._locator(selector).click())
            return {
                "selector": selector,
            }
        if action == "type":
            if not selector:
                raise ValueError("playwright_page type requires selector")
            text = str(request.get("text") or "")
            if not text:
                raise ValueError("playwright_page type requires text")
            locator = self._locator(selector)
            fill = getattr(locator, "fill", None)
            if callable(fill):
                _resolve_awaitable(fill(text))
            else:
                type_text = getattr(locator, "type", None)
                if not callable(type_text):
                    raise ValueError("playwright_page locator does not support fill/type")
                _resolve_awaitable(type_text(text))
            return {
                "selector": selector,
                "characters": len(text),
            }
        if action == "key":
            key = str(request.get("text") or request.get("selector") or "").strip()
            if not key:
                raise ValueError("playwright_page key requires text or selector naming the key")
            keyboard = getattr(self._page, "keyboard", None)
            press = getattr(keyboard, "press", None)
            if not callable(press):
                raise ValueError("playwright_page keyboard.press is unavailable")
            _resolve_awaitable(press(key))
            return {
                "key": key,
            }
        if action == "scroll":
            direction = str(request.get("direction") or "down").strip().lower()
            amount = _int_request_value(request, "amount") or 5
            x_delta = 0
            y_delta = 0
            pixels = max(1, abs(amount)) * 120
            if direction == "up":
                y_delta = -pixels
            elif direction == "down":
                y_delta = pixels
            elif direction == "left":
                x_delta = -pixels
            elif direction == "right":
                x_delta = pixels
            if selector:
                expression = "(el, delta) => el.scrollBy(delta.x, delta.y)"
                _resolve_awaitable(self._locator(selector).evaluate(expression, {"x": x_delta, "y": y_delta}))
            else:
                evaluate = getattr(self._page, "evaluate", None)
                if not callable(evaluate):
                    raise ValueError("playwright_page evaluate is unavailable")
                _resolve_awaitable(evaluate("delta => window.scrollBy(delta.x, delta.y)", {"x": x_delta, "y": y_delta}))
            return {
                "selector": selector or None,
                "direction": direction,
                "amount": abs(amount),
                "pixels": {"x": x_delta, "y": y_delta},
            }
        raise ValueError(f"Unsupported computer_use action for playwright_page: {action}")


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _PlaywrightBrowserSessionComputerUseExecutor:
    def __init__(self, *, playwright_factory: Any | None = None, headless: bool | None = None) -> None:
        self._playwright_factory = playwright_factory
        self._headless = _env_flag("LOCAL_AGENT_COMPUTER_USE_BROWSER_HEADLESS", default=True) if headless is None else headless
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._contexts: dict[str, Any] = {}
        self._pages: dict[tuple[str, str], Any] = {}

    @staticmethod
    def name() -> str:
        return "playwright_browser_session"

    @staticmethod
    def capabilities() -> set[str]:
        return {
            "click.selector",
            "browser.click.selector",
            "inspect.browser",
            "browser.inspect",
            "screenshot.browser",
            "browser.screenshot",
            "type.selector",
            "browser.type.selector",
            "key.text",
            "scroll.direction",
            "scroll.selector",
            "browser.scroll.selector",
        }

    def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        if self._playwright_factory is not None:
            starter = self._playwright_factory
        else:
            try:
                from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"playwright is unavailable: {exc}") from exc
            starter = sync_playwright
        self._playwright = starter().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        return self._browser

    def _context(self, context_id: str) -> Any:
        context_key = context_id or "default"
        existing = self._contexts.get(context_key)
        if existing is not None:
            return existing
        context = self._ensure_browser().new_context()
        self._contexts[context_key] = context
        return context

    def _page(self, request: dict[str, Any]) -> Any:
        context_id = str(request.get("browserContextId") or "default").strip() or "default"
        page_id = str(request.get("pageId") or request.get("url") or "default").strip() or "default"
        page_key = (context_id, page_id)
        page = self._pages.get(page_key)
        if page is None:
            page = self._context(context_id).new_page()
            self._pages[page_key] = page
        url = str(request.get("url") or "").strip()
        if url:
            current_url = str(getattr(page, "url", "") or "")
            if current_url != url:
                _resolve_awaitable(page.goto(url))
        return page

    def _locator(self, request: dict[str, Any]) -> Any:
        selector = _request_selector(request)
        if not selector:
            raise ValueError("playwright browser session requires selector")
        return _resolve_awaitable(self._page(request).locator(selector))

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        page = self._page(request)
        page_url = str(getattr(page, "url", "") or "")
        page_id = str(request.get("pageId") or request.get("url") or "default").strip() or "default"
        context_id = str(request.get("browserContextId") or "default").strip() or "default"
        page_executor = _PlaywrightPageComputerUseExecutor(page)
        detail = page_executor.execute(request)
        if isinstance(detail, dict):
            return {
                **detail,
                "url": page_url,
                "pageId": page_id,
                "browserContextId": context_id,
                "headless": self._headless,
            }
        return {
            "detail": detail,
            "url": page_url,
            "pageId": page_id,
            "browserContextId": context_id,
            "headless": self._headless,
        }

    def close(self) -> None:
        pages = list(self._pages.values())
        contexts = list(self._contexts.values())
        browser = self._browser
        playwright = self._playwright
        self._pages.clear()
        self._contexts.clear()
        self._browser = None
        self._playwright = None

        for resource, method_name in [
            *[(page, "close") for page in pages],
            *[(context, "close") for context in contexts],
            (browser, "close"),
            (playwright, "stop"),
        ]:
            if resource is None:
                continue
            method = getattr(resource, method_name, None)
            if not callable(method):
                continue
            try:
                _resolve_awaitable(method())
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def build_env_computer_use_executor() -> Any | None:
    if not _env_flag("LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT", default=False):
        return None
    return _PlaywrightBrowserSessionComputerUseExecutor()


def _normalize_computer_use_executor(executor: Any | None) -> Any | None:
    if executor is None:
        return None
    if hasattr(executor, "execute"):
        return executor
    if hasattr(executor, "locator") and (hasattr(executor, "keyboard") or hasattr(executor, "evaluate")):
        return _PlaywrightPageComputerUseExecutor(executor)
    return executor


def _default_computer_use_executor() -> Any | None:
    try:
        return _PyAutoGuiComputerUseExecutor()
    except Exception:
        pass
    try:
        return _WindowsCtypesComputerUseExecutor()
    except Exception:
        return None


def _executor_name(executor: Any) -> str:
    name = getattr(executor, "name", None)
    if callable(name):
        try:
            value = name()
            if isinstance(value, str) and value.strip():
                return value.strip()
        except Exception:  # noqa: BLE001
            pass
    return executor.__class__.__name__


def _executor_capabilities(executor: Any) -> set[str]:
    capabilities = getattr(executor, "capabilities", None)
    if callable(capabilities):
        try:
            value = capabilities()
        except Exception:  # noqa: BLE001
            return set()
    else:
        value = capabilities
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _executor_can_execute(executor: Any, request: dict[str, Any]) -> tuple[bool, str]:
    can_execute = getattr(executor, "can_execute", None)
    if callable(can_execute):
        try:
            decision = can_execute(request)
        except Exception as exc:  # noqa: BLE001
            return False, f"executor capability check failed: {exc}"
        if isinstance(decision, tuple):
            allowed = bool(decision[0]) if decision else False
            reason = str(decision[1]).strip() if len(decision) > 1 else ""
            return allowed, reason
        return bool(decision), "" if decision else "executor rejected this request"

    capabilities = _executor_capabilities(executor)
    if not capabilities:
        return True, ""
    action = request["action"]
    if action == "inspect":
        return (
            "inspect.browser" in capabilities or "browser.inspect" in capabilities,
            "executor does not support browser inspect",
        )
    if action == "screenshot":
        return (
            "screenshot.browser" in capabilities or "browser.screenshot" in capabilities,
            "executor does not support browser screenshot",
        )
    if action == "click":
        if _numeric_request_value(request, "x") is not None and _numeric_request_value(request, "y") is not None:
            return "click.coordinates" in capabilities, "executor does not support coordinate click"
        if str(request.get("selector") or "").strip():
            return "click.selector" in capabilities or "browser.click.selector" in capabilities, "executor does not support selector click"
    if action == "type":
        if str(request.get("selector") or "").strip():
            return (
                "type.selector" in capabilities or "browser.type.selector" in capabilities,
                "executor does not support selector typing",
            )
        return "type.text" in capabilities, "executor does not support text typing"
    if action == "key":
        return "key.text" in capabilities, "executor does not support key presses"
    if action == "scroll":
        if str(request.get("selector") or "").strip():
            return (
                "scroll.selector" in capabilities or "browser.scroll.selector" in capabilities,
                "executor does not support selector scrolling",
            )
        return "scroll.direction" in capabilities, "executor does not support scrolling"
    return True, ""


def _execute_browser_observation_if_available(
    request: dict[str, Any],
    approval_id: str | None,
    executor: Any | None,
) -> dict[str, Any] | None:
    if not _request_is_browser_target(request):
        return None
    active_executor = _normalize_computer_use_executor(executor)
    if active_executor is None:
        return None
    can_execute, _reason = _executor_can_execute(active_executor, request)
    if not can_execute:
        return None
    return _execute_with_executor(request, approval_id, active_executor)


def _execute_desktop_action(request: dict[str, Any], approval_id: str | None, executor: Any | None) -> dict[str, Any]:
    action = request["action"]
    selector = _request_selector(request)
    if selector and action in {"click", "type", "scroll"}:
        active_executor = _normalize_computer_use_executor(executor)
        if active_executor is not None:
            can_execute, reason = _executor_can_execute(active_executor, request)
            if can_execute:
                return _execute_with_executor(request, approval_id, active_executor)
            return _selector_executor_required_result(request, approval_id, reason=reason)
        if action == "click" and (_numeric_request_value(request, "x") is not None and _numeric_request_value(request, "y") is not None):
            active_executor = _default_computer_use_executor()
            if active_executor is not None:
                can_execute, reason = _executor_can_execute(active_executor, request)
                if can_execute:
                    return _execute_with_executor(request, approval_id, active_executor)
        return _selector_executor_required_result(request, approval_id)
    if action == "click" and (_numeric_request_value(request, "x") is None or _numeric_request_value(request, "y") is None):
        return _blocked_executor_result(
            request,
            approval_id,
            error="computer_use click requires x and y coordinates until an accessibility-selector backend is available.",
            failure_kind="coordinates_required",
            recovery_hint="Provide x/y coordinates from a screenshot, or enable an accessibility selector backend.",
        )
    if action == "type" and not str(request.get("text") or ""):
        return _blocked_executor_result(
            request,
            approval_id,
            error="computer_use type requires non-empty text.",
            failure_kind="text_required",
            recovery_hint="Provide the text to type.",
        )
    if action == "key" and not str(request.get("text") or request.get("selector") or "").strip():
        return _blocked_executor_result(
            request,
            approval_id,
            error="computer_use key requires text or selector naming the key.",
            failure_kind="key_required",
            recovery_hint="Provide a key name such as enter, escape, tab, or ctrl+c.",
        )
    active_executor = _normalize_computer_use_executor(executor) or _default_computer_use_executor()
    if active_executor is None:
        return _blocked_executor_result(
            request,
            approval_id,
            error="No desktop control backend is available in this runtime.",
            recovery_hint="Install pyautogui, run in an interactive desktop session, or provide a custom computer-use executor.",
        )
    can_execute, reason = _executor_can_execute(active_executor, request)
    if not can_execute:
        return _blocked_executor_result(
            request,
            approval_id,
            error=f"Computer Use executor {_executor_name(active_executor)} cannot run {action}: {reason or 'unsupported action'}.",
            failure_kind="executor_capability_unsupported",
            recovery_hint="Provide a compatible computer-use executor or adjust the request parameters.",
        )
    return _execute_with_executor(request, approval_id, active_executor)


def _execute_with_executor(request: dict[str, Any], approval_id: str | None, active_executor: Any) -> dict[str, Any]:
    action = request["action"]
    try:
        detail = active_executor.execute(request)
    except Exception as exc:  # noqa: BLE001
        return _blocked_executor_result(
            request,
            approval_id,
            error=f"Computer Use action failed: {exc}",
            failure_kind="desktop_control_failed",
            recovery_hint="Check the requested coordinates/action and make sure the desktop session accepts automation.",
            step_summary=str(exc),
        )
    if not isinstance(detail, dict):
        detail = {"detail": detail}
    summary = str(detail.get("summary") or f"Executed computer_use {action} on {request['target']} via {_executor_name(active_executor)}.")
    result = {
        "status": "completed",
        "action": action,
        "target": request["target"],
        "summary": summary,
        "request": request,
        "approvalId": approval_id,
        "executor": _executor_name(active_executor),
        "detail": detail,
        "toolName": "computer_use",
        "contentSource": "computer",
        "contentTrust": "untrusted",
        "steps": [
            {"label": "permission", "status": "completed", "summary": "Computer Use permission approved."},
            {"label": action, "status": "completed", "summary": summary},
        ],
    }
    for key in (
        "url",
        "pageId",
        "browserContextId",
        "title",
        "text",
        "elements",
        "width",
        "height",
        "previewWidth",
        "previewHeight",
        "mimeType",
        "bytes",
        "capturedAt",
        "imageDataUrl",
    ):
        if key in detail:
            result[key] = detail[key]
    return result


def _capture_screenshot(request: dict[str, Any], approval_id: str | None) -> dict[str, Any]:
    target = request["target"]
    try:
        from PIL import ImageGrab  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "blocked",
            "error": f"Screenshot capture is unavailable: Pillow ImageGrab could not be imported ({exc}).",
            "request": request,
            "approvalId": approval_id,
            "failureKind": "screenshot_unavailable",
            "recoveryHint": "Attach a screenshot manually or enable a desktop screenshot backend.",
            "toolName": "computer_use",
            "contentSource": "computer",
            "contentTrust": "untrusted",
            "steps": [
                {"label": "screenshot", "status": "blocked", "summary": "Pillow ImageGrab is unavailable."},
            ],
        }
    try:
        image = ImageGrab.grab()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "blocked",
            "error": f"Screenshot capture is unavailable in this runtime: {exc}",
            "request": request,
            "approvalId": approval_id,
            "failureKind": "screenshot_unavailable",
            "recoveryHint": "Attach a screenshot manually or enable a desktop screenshot backend.",
            "toolName": "computer_use",
            "contentSource": "computer",
            "contentTrust": "untrusted",
            "steps": [
                {"label": "screenshot", "status": "blocked", "summary": str(exc)},
            ],
        }

    width, height = image.size
    preview = image.copy()
    preview.thumbnail((1280, 1280))
    preview_width, preview_height = preview.size
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    png = buffer.getvalue()
    summary = f"Captured screenshot for {target} ({width}x{height})."
    return {
        "status": "completed",
        "action": "screenshot",
        "target": target,
        "summary": summary,
        "request": request,
        "approvalId": approval_id,
        "width": width,
        "height": height,
        "previewWidth": preview_width,
        "previewHeight": preview_height,
        "mimeType": "image/png",
        "bytes": len(png),
        "capturedAt": int(time.time() * 1000),
        "imageDataUrl": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        "toolName": "computer_use",
        "contentSource": "computer",
        "contentTrust": "untrusted",
        "steps": [
            {"label": "screenshot", "status": "completed", "summary": summary},
        ],
    }


def build_computer_use_tool(
    policy_guard: Any,
    store: Any,
    subagent_service: Any | None = None,
    *,
    permission_engine: Any | None = None,
    computer_use_executor: Any | None = None,
) -> dict[str, Any]:
    def computer_use(params: dict[str, Any]) -> dict[str, Any]:
        request = _request_payload(params)
        task_id = request["taskId"]
        if not task_id:
            raise ValueError("taskId is required")
        approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip() or None
        approval = _approval_for_request(store, task_id, request, approval_id)
        if approval is not None:
            if approval.get("decision") != "approved":
                return {
                    "status": "approval_required",
                    "approval": approval,
                    "request": request,
                    "summary": request["permission"],
                    "toolName": "computer_use",
                    "contentSource": "computer",
                    "contentTrust": "untrusted",
                }
        else:
            decision = permission_engine.evaluate(
                PermRequest(
                    capability="computerUse",
                    tool_name="computer_use",
                    context=request,
                )
            ) if permission_engine is not None else None
            if decision is not None and decision.decision == "deny":
                return {
                    "status": "blocked",
                    "error": decision.reason,
                    "request": request,
                    "toolName": "computer_use",
                    "contentSource": "computer",
                    "contentTrust": "untrusted",
                }
            if decision is None or decision.decision == "approval_required":
                approval = store.create_approval(
                    task_id=task_id,
                    kind="computer_use",
                    request=request,
                )
                return {
                    "status": "approval_required",
                    "approval": approval,
                    "request": request,
                    "summary": request["permission"],
                    "toolName": "computer_use",
                    "contentSource": "computer",
                    "contentTrust": "untrusted",
                }

        action = request["action"]
        if action not in _EXECUTION_READY_ACTIONS:
            return {
                "status": "blocked",
                "error": (
                    "computer_use approval was granted, but this runtime does not yet have a "
                    f"desktop/browser executor for action {action!r}."
                ),
                "request": request,
                "approvalId": approval_id,
                "toolName": "computer_use",
                "contentSource": "computer",
                "contentTrust": "untrusted",
            }
        if action == "inspect":
            browser_result = _execute_browser_observation_if_available(request, approval_id, computer_use_executor)
            if browser_result is not None:
                return browser_result
            return _inspect_environment(request, approval_id)
        if action == "screenshot":
            browser_result = _execute_browser_observation_if_available(request, approval_id, computer_use_executor)
            if browser_result is not None:
                return browser_result
            return _capture_screenshot(request, approval_id)
        if action in {"click", "type", "key", "scroll"}:
            return _execute_desktop_action(request, approval_id, computer_use_executor)
        raise ValueError(f"Unsupported computer_use action: {action}")

    return {"handler": computer_use}
