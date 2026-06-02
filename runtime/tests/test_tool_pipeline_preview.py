from __future__ import annotations

from local_agent_runtime.execution.tool_pipeline import _tool_result_preview


def test_computer_use_browser_inspect_preview_includes_page_and_elements() -> None:
    preview = _tool_result_preview(
        "computer_use",
        {
            "status": "completed",
            "action": "inspect",
            "target": "browser",
            "executor": "playwright_browser_session",
            "url": "http://localhost:5173",
            "pageId": "page_1",
            "title": "Demo Page",
            "elements": [
                {"tag": "button", "text": "Save"},
                {"tag": "input", "label": "Search"},
            ],
            "summary": "Inspected browser page",
        },
        "browser",
    )

    assert {"label": "URL", "value": "http://localhost:5173"} in preview
    assert {"label": "Page", "value": "page_1"} in preview
    assert {"label": "标题", "value": "Demo Page"} in preview
    assert {"label": "元素", "value": "button: Save; input: Search"} in preview
