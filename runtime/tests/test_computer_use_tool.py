from __future__ import annotations

import json
from typing import Any

from local_agent_runtime.tools.computer_use import (
    _PlaywrightBrowserSessionComputerUseExecutor,
    build_computer_use_tool,
    build_env_computer_use_executor,
)


class _Store:
    def __init__(self) -> None:
        self._approvals: dict[str, dict[str, Any]] = {}
        self._next = 0

    def create_approval(self, task_id: str, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        self._next += 1
        approval = {
            "id": f"appr_{self._next}",
            "taskId": task_id,
            "kind": kind,
            "requestJson": json.dumps(request, ensure_ascii=False, sort_keys=True),
            "decision": None,
        }
        self._approvals[approval["id"]] = approval
        return dict(approval)

    def get_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        approval = self._approvals.get(params["approvalId"])
        if approval is None:
            raise ValueError(f"Approval not found: {params['approvalId']}")
        return {"approval": dict(approval)}

    def find_approval(self, *, task_id: str, kind: str, request: dict[str, Any]) -> dict[str, Any] | None:
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
        for approval in reversed(list(self._approvals.values())):
            if approval["taskId"] == task_id and approval["kind"] == kind and approval["requestJson"] == request_json:
                return dict(approval)
        return None

    def approve(self, approval_id: str) -> None:
        self._approvals[approval_id]["decision"] = "approved"


class _Executor:
    capabilities = {"click.coordinates", "type.text", "key.text", "scroll.direction"}

    @staticmethod
    def name() -> str:
        return "fake_desktop"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(request))
        return {"ok": True, "action": request["action"]}


class _SelectorExecutor(_Executor):
    capabilities = {"click.selector", "type.selector", "scroll.selector"}

    @staticmethod
    def name() -> str:
        return "fake_dom"


class _FakeLocator:
    def __init__(self, selector: str, page: "_FakePage") -> None:
        self.selector = selector
        self.page = page

    def click(self) -> None:
        self.page.calls.append(("click", self.selector))

    def fill(self, text: str) -> None:
        self.page.calls.append(("fill", self.selector, text))

    def evaluate(self, expression: str, argument: dict[str, Any]) -> None:
        self.page.calls.append(("evaluate", self.selector, expression, argument))

    def inner_text(self, timeout: int | None = None) -> str:
        self.page.calls.append(("inner_text", self.selector, timeout))
        return self.page.body_text


class _FakeKeyboard:
    def __init__(self, page: "_FakePage") -> None:
        self.page = page

    def press(self, key: str) -> None:
        self.page.calls.append(("press", key))


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.keyboard = _FakeKeyboard(self)
        self.url = ""
        self.title_text = "Demo Page"
        self.body_text = "Save Search yuanbao"
        self.elements = [
            {"tag": "button", "text": "Save", "testId": "save"},
            {"tag": "input", "label": "Search", "name": "q"},
        ]

    def locator(self, selector: str) -> _FakeLocator:
        self.calls.append(("locator", selector))
        return _FakeLocator(selector, self)

    def evaluate(self, expression: str, argument: dict[str, Any] | None = None) -> Any:
        self.calls.append(("page.evaluate", expression, argument))
        if "querySelectorAll" in expression:
            return self.elements
        if "document.body" in expression:
            return self.body_text
        return None

    def goto(self, url: str) -> None:
        self.url = url
        self.calls.append(("goto", url))

    def title(self) -> str:
        self.calls.append(("title",))
        return self.title_text

    def screenshot(self, **kwargs: Any) -> bytes:
        self.calls.append(("screenshot", kwargs))
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
            b"\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    def close(self) -> None:
        self.calls.append(("close",))


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[_FakePage] = []
        self.closed = False

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[_FakeContext] = []
        self.closed = False

    def new_context(self) -> _FakeContext:
        context = _FakeContext()
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.launch_args: list[dict[str, Any]] = []

    def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launch_args.append(kwargs)
        return self.browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()
        self.chromium = _FakeChromium(self.browser)
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakePlaywrightStarter:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self.playwright = playwright

    def start(self) -> _FakePlaywright:
        return self.playwright


def _tool(store: _Store, executor: Any | None = None):
    return build_computer_use_tool(
        policy_guard=None,
        store=store,
        computer_use_executor=executor,
    )["handler"]


def test_computer_use_click_executes_after_approval_with_injected_executor() -> None:
    store = _Store()
    executor = _Executor()
    tool = _tool(store, executor)
    params = {
        "taskId": "task_1",
        "action": "click",
        "target": "desktop",
        "permission": "Click the selected button",
        "x": 120,
        "y": 240,
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["action"] == "click"
    assert result["executor"] == "fake_desktop"
    assert executor.requests[-1]["x"] == 120
    assert any(step["label"] == "click" and step["status"] == "completed" for step in result["steps"])


def test_computer_use_click_selector_requires_selector_executor() -> None:
    store = _Store()
    executor = _Executor()
    tool = _tool(store, executor)
    params = {
        "taskId": "task_1",
        "action": "click",
        "target": "desktop",
        "permission": "Click Save",
        "selector": "Save",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "blocked"
    assert result["failureKind"] == "selector_executor_required"
    assert "selector" in result["recoveryHint"]
    assert not executor.requests


def test_computer_use_click_selector_executes_with_selector_executor() -> None:
    store = _Store()
    executor = _SelectorExecutor()
    tool = _tool(store, executor)
    params = {
        "taskId": "task_1",
        "action": "click",
        "target": "browser",
        "permission": "Click Save",
        "selector": "button[data-testid='save']",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["executor"] == "fake_dom"
    assert executor.requests[-1]["selector"] == "button[data-testid='save']"


def test_computer_use_uses_playwright_page_like_executor_for_selector_actions() -> None:
    store = _Store()
    page = _FakePage()
    tool = _tool(store, page)
    params = {
        "taskId": "task_1",
        "action": "click",
        "target": "browser",
        "permission": "Click Save",
        "selector": "button.save",
        "url": "http://localhost:5173",
        "pageId": "page_1",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["executor"] == "playwright_page"
    assert result["detail"] == {"selector": "button.save"}
    assert ("locator", "button.save") in page.calls
    assert ("click", "button.save") in page.calls


def test_computer_use_playwright_page_like_executor_can_type_into_selector() -> None:
    store = _Store()
    page = _FakePage()
    tool = _tool(store, page)
    params = {
        "taskId": "task_1",
        "action": "type",
        "target": "browser",
        "permission": "Fill search input",
        "selector": "input[name='q']",
        "text": "yuanbao",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["executor"] == "playwright_page"
    assert result["detail"] == {"selector": "input[name='q']", "characters": 7}
    assert ("fill", "input[name='q']", "yuanbao") in page.calls


def test_computer_use_type_selector_requires_selector_executor() -> None:
    store = _Store()
    executor = _Executor()
    tool = _tool(store, executor)
    params = {
        "taskId": "task_1",
        "action": "type",
        "target": "browser",
        "permission": "Fill search input",
        "selector": "input[name='q']",
        "text": "yuanbao",
        "url": "http://localhost:5173",
        "pageId": "page_1",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "blocked"
    assert result["failureKind"] == "selector_executor_required"
    assert "url=http://localhost:5173" in result["error"]
    assert "pageId=page_1" in result["error"]
    assert "type.selector" in result["recoveryHint"]
    assert not executor.requests


def test_computer_use_scroll_selector_requires_selector_executor() -> None:
    store = _Store()
    executor = _Executor()
    tool = _tool(store, executor)
    params = {
        "taskId": "task_1",
        "action": "scroll",
        "target": "browser",
        "permission": "Scroll results panel",
        "selector": ".results",
        "direction": "down",
        "amount": 3,
        "browserContextId": "ctx_1",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "blocked"
    assert result["failureKind"] == "selector_executor_required"
    assert "browserContextId=ctx_1" in result["error"]
    assert "scroll.selector" in result["recoveryHint"]
    assert not executor.requests


def test_computer_use_playwright_page_like_executor_can_scroll_selector() -> None:
    store = _Store()
    page = _FakePage()
    tool = _tool(store, page)
    params = {
        "taskId": "task_1",
        "action": "scroll",
        "target": "browser",
        "permission": "Scroll results panel",
        "selector": ".results",
        "direction": "down",
        "amount": 2,
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["executor"] == "playwright_page"
    assert result["detail"]["selector"] == ".results"
    assert result["detail"]["pixels"] == {"x": 0, "y": 240}
    assert any(call[0] == "evaluate" and call[1] == ".results" for call in page.calls)


def test_computer_use_browser_inspect_uses_playwright_page_like_executor() -> None:
    store = _Store()
    page = _FakePage()
    tool = _tool(store, page)
    params = {
        "taskId": "task_1",
        "action": "inspect",
        "target": "browser",
        "permission": "Inspect current page",
        "url": "http://localhost:5173",
        "pageId": "page_1",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["executor"] == "playwright_page"
    assert result["url"] == ""
    assert result["pageId"] == "page_1"
    assert result["title"] == "Demo Page"
    assert result["text"] == "Save Search yuanbao"
    assert result["elements"][0]["text"] == "Save"
    assert ("inner_text", "body", 1000) in page.calls


def test_computer_use_browser_screenshot_uses_playwright_page_like_executor() -> None:
    store = _Store()
    page = _FakePage()
    tool = _tool(store, page)
    params = {
        "taskId": "task_1",
        "action": "screenshot",
        "target": "browser",
        "permission": "Capture current page",
        "pageId": "page_1",
    }

    pending = tool(params)
    approval_id = pending["approval"]["id"]
    store.approve(approval_id)
    result = tool({**params, "approvalId": approval_id})

    assert result["status"] == "completed"
    assert result["executor"] == "playwright_page"
    assert result["mimeType"] == "image/png"
    assert result["width"] == 1
    assert result["height"] == 1
    assert str(result["imageDataUrl"]).startswith("data:image/png;base64,")
    assert any(call[0] == "screenshot" for call in page.calls)


def test_env_computer_use_executor_is_disabled_by_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT", raising=False)

    assert build_env_computer_use_executor() is None


def test_env_computer_use_executor_uses_playwright_session_when_enabled(monkeypatch: Any) -> None:
    monkeypatch.setenv("LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT", "1")
    monkeypatch.setenv("LOCAL_AGENT_COMPUTER_USE_BROWSER_HEADLESS", "0")
    playwright = _FakePlaywright()
    assert build_env_computer_use_executor() is not None
    executor = _PlaywrightBrowserSessionComputerUseExecutor(
        playwright_factory=lambda: _FakePlaywrightStarter(playwright),
        headless=False,
    )

    first = executor.execute(
        {
            "action": "click",
            "target": "browser",
            "permission": "Click Save",
            "selector": "button.save",
            "url": "http://localhost:5173",
            "pageId": "page_1",
            "browserContextId": "ctx_1",
        }
    )
    second = executor.execute(
        {
            "action": "type",
            "target": "browser",
            "permission": "Fill search",
            "selector": "input[name='q']",
            "text": "yuanbao",
            "url": "http://localhost:5173",
            "pageId": "page_1",
            "browserContextId": "ctx_1",
        }
    )

    assert first["url"] == "http://localhost:5173"
    assert first["pageId"] == "page_1"
    assert first["browserContextId"] == "ctx_1"
    assert first["headless"] is False
    assert second["characters"] == 7
    assert len(playwright.browser.contexts) == 1
    assert len(playwright.browser.contexts[0].pages) == 1
    page = playwright.browser.contexts[0].pages[0]
    assert ("goto", "http://localhost:5173") in page.calls
    assert ("click", "button.save") in page.calls
    assert ("fill", "input[name='q']", "yuanbao") in page.calls


def test_playwright_session_executor_close_releases_browser_resources() -> None:
    playwright = _FakePlaywright()
    executor = _PlaywrightBrowserSessionComputerUseExecutor(
        playwright_factory=lambda: _FakePlaywrightStarter(playwright),
        headless=True,
    )

    executor.execute(
        {
            "action": "click",
            "target": "browser",
            "permission": "Click Save",
            "selector": "button.save",
            "url": "http://localhost:5173",
            "pageId": "page_1",
            "browserContextId": "ctx_1",
        }
    )
    page = playwright.browser.contexts[0].pages[0]

    executor.close()
    executor.close()

    assert ("close",) in page.calls
    assert playwright.browser.contexts[0].closed is True
    assert playwright.browser.closed is True
    assert playwright.stopped is True


def test_playwright_session_executor_can_inspect_and_screenshot_browser_page() -> None:
    playwright = _FakePlaywright()
    executor = _PlaywrightBrowserSessionComputerUseExecutor(
        playwright_factory=lambda: _FakePlaywrightStarter(playwright),
        headless=True,
    )

    inspect_result = executor.execute(
        {
            "action": "inspect",
            "target": "browser",
            "permission": "Inspect page",
            "url": "http://localhost:5173",
            "pageId": "page_1",
            "browserContextId": "ctx_1",
        }
    )
    screenshot_result = executor.execute(
        {
            "action": "screenshot",
            "target": "browser",
            "permission": "Screenshot page",
            "url": "http://localhost:5173",
            "pageId": "page_1",
            "browserContextId": "ctx_1",
        }
    )

    assert inspect_result["title"] == "Demo Page"
    assert inspect_result["elements"][0]["testId"] == "save"
    assert inspect_result["browserContextId"] == "ctx_1"
    assert screenshot_result["mimeType"] == "image/png"
    assert screenshot_result["width"] == 1
    assert len(playwright.browser.contexts) == 1
    assert len(playwright.browser.contexts[0].pages) == 1
