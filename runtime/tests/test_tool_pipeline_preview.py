from __future__ import annotations

from local_agent_runtime.execution.tool_pipeline import _tool_category, _tool_display_metadata, _tool_result_preview, _tool_runtime_progress_message


def test_run_command_python_file_probe_is_context_read() -> None:
    arguments = {
        "command": "python -c \"from pathlib import Path; print(Path('library.py').read_text(encoding='utf-8'))\"",
    }

    metadata = _tool_display_metadata("run_command", arguments, target=arguments["command"])

    assert _tool_category("run_command", arguments) == "context_read"
    assert metadata["displayKind"] == "context_read"
    assert metadata["displayTitle"] == "读取 library.py"
    assert "运行命令" not in metadata["displayTitle"]


def test_run_command_powershell_python_file_probe_is_context_read() -> None:
    arguments = {
        "command": "& \"C:\\Python314\\python.exe\" -c \"from pathlib import Path; print(Path('app.py').read_text())\"",
    }

    metadata = _tool_display_metadata("run_command", arguments, target=arguments["command"])

    assert _tool_category("run_command", arguments) == "context_read"
    assert metadata["displayKind"] == "context_read"
    assert metadata["displayTitle"] == "读取 app.py"


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


def test_run_command_display_metadata_uses_haha_style_comment_label() -> None:
    arguments = {"command": "# 读取 library.py\nGet-Content -Raw library.py"}

    metadata = _tool_display_metadata("run_command", arguments, target=arguments["command"])

    assert metadata["displayTitle"] == "读取 library.py"
    assert metadata["displaySummary"] == "读取 library.py"
    assert metadata["displayTitle"] != "运行命令"


def test_run_command_progress_uses_semantic_command_label() -> None:
    message = _tool_runtime_progress_message(
        "run_command",
        {"command": "Get-Content -Raw library.py"},
        "Get-Content -Raw library.py",
    )

    assert message == "正在读取 library.py"
    assert "正在运行命令" not in message


def test_tool_display_metadata_covers_haha_style_frontend_contract() -> None:
    read = _tool_display_metadata("read_file", {"path": "library.py"}, target="library.py")
    search = _tool_display_metadata("search_files", {"query": "class Library", "path": "."}, target="class Library")
    write = _tool_display_metadata("write_file", {"path": "library.py"}, target="library.py")
    pytest = _tool_display_metadata("run_command", {"command": "pytest -q"}, target="pytest -q")

    assert read["displayTitle"] == "读取 library.py"
    assert read["displaySummary"] == "library.py"
    assert read["displayTarget"] == "library.py"
    assert read["displayKind"] == "context_read"

    assert search["displayTitle"] == "搜索 class Library"
    assert search["displaySummary"] == "class Library"
    assert search["displayTarget"] == "class Library"
    assert search["displayKind"] == "search"

    assert write["displayTitle"] == "写入 library.py"
    assert write["displaySummary"] == "library.py"
    assert write["displayTarget"] == "library.py"
    assert write["displayKind"] == "file_change"

    assert pytest["displayTitle"] == "运行测试"
    assert pytest["displaySummary"] == "运行测试"
    assert pytest["displayTarget"] == "pytest -q"
    assert pytest["displayKind"] == "verification"
