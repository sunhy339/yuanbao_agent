"""Tool Execution Pipeline — extracted from ReactRunnerMixin.

Handles tool validation, approval gating, execution, and observation.
Design target:

    before_tool_call hooks
      -> schema validation
      -> policy guard
      -> approval gate
      -> worktree path enforcement (initially disabled)
      -> execute tool
      -> after_tool_call hooks
      -> trace/store observation
"""
from __future__ import annotations

import json
import logging
import re as _re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..services.worker_budget import WorkerBudget
from ..tools.task import normalize_agent_tool_params

logger = logging.getLogger(__name__)

_WORKTREE_BOUND_TOOLS = {
    "list_dir",
    "search_files",
    "read_file",
    "run_command",
    "apply_patch",
    "git_status",
    "git_diff",
    "write_file",
    "code_search",
    "notebook",
}
_LAZY_WORKTREE_BIND_TOOLS = {"apply_patch", "write_file"}
_ACTIVE_WORKTREE_STATUSES = {"creating", "active", "paused", "ready_for_review"}
_TOOL_VISIBLE_RESULT_MAX_CHARS = 8000
_TOOL_VISIBLE_TEXT_HEAD_CHARS = 1200
_TOOL_VISIBLE_TEXT_TAIL_CHARS = 800
_TOOL_VISIBLE_COLLECTION_LIMIT = 12
_TOOL_VISIBLE_SNIPPET_LIMIT = 600
SUBAGENT_TOOL_NAMES = {"agent", "task"}
_MIN_MODEL_SUPPLIED_CHILD_TOKEN_BUDGET = 16000
_DEFAULT_CHILD_TOOL_CALL_BUDGET = 12
_MIN_MODEL_SUPPLIED_CHILD_TOOL_CALL_BUDGET = 12
_VERIFY_COMMAND_RE = _re.compile(
    r"\b("
    r"npm\s+(?:run\s+)?(?:test|typecheck|lint|build)|"
    r"pnpm\s+(?:run\s+)?(?:test|typecheck|lint|build)|"
    r"yarn\s+(?:test|typecheck|lint|build)|"
    r"pytest|vitest|jest|playwright|tsc|ruff|eslint|mypy|"
    r"cargo\s+(?:test|check|build)|go\s+test|dotnet\s+test|"
    r"test|typecheck|lint|build|verify|check"
    r")\b",
    _re.IGNORECASE,
)


def is_verification_command(command: Any) -> bool:
    return bool(_VERIFY_COMMAND_RE.search(str(command or "")))


def _compact_text(value: Any, limit: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _head_tail_text(value: Any, *, head: int = _TOOL_VISIBLE_TEXT_HEAD_CHARS, tail: int = _TOOL_VISIBLE_TEXT_TAIL_CHARS) -> dict[str, Any]:
    text = str(value or "")
    chars = len(text)
    if chars <= head + tail:
        return {"text": text, "chars": chars, "truncated": False}
    omitted = chars - head - tail
    return {
        "head": text[:head],
        "tail": text[-tail:] if tail > 0 else "",
        "chars": chars,
        "omittedChars": omitted,
        "truncated": True,
    }


def _compact_snippet(value: Any, limit: int = _TOOL_VISIBLE_SNIPPET_LIMIT) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    return {
        "head": value[: max(0, limit // 2)],
        "tail": value[-max(0, limit // 2) :],
        "chars": len(value),
        "omittedChars": max(0, len(value) - limit),
        "truncated": True,
    }


def _compact_list_items(items: Any, *, limit: int = _TOOL_VISIBLE_COLLECTION_LIMIT) -> list[Any]:
    if not isinstance(items, list):
        return []
    compacted: list[Any] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            compacted.append({
                str(key): _compact_snippet(value)
                for key, value in item.items()
                if key not in {"content", "body", "html", "markdown", "data", "base64", "imageData"}
            })
        else:
            compacted.append(_compact_snippet(item))
    return compacted


def _tool_result_full_ref(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "source": "trace",
        "rawResultStored": True,
        "rawResultSizeChars": _json_size(result),
    }
    command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
    if command_log.get("id"):
        refs["commandLogId"] = command_log.get("id")
    for key in ("stdoutPath", "stderrPath"):
        value = command_log.get(key) or result.get(key)
        if value:
            refs[key] = value
    if tool_name in {"git_diff", "apply_patch", "write_file"}:
        if result.get("patchId") or result.get("patch_id"):
            refs["patchId"] = result.get("patchId") or result.get("patch_id")
        if result.get("artifactId") or result.get("artifactIds"):
            refs["artifactId"] = result.get("artifactId")
            refs["artifactIds"] = result.get("artifactIds")
    return {key: value for key, value in refs.items() if value not in (None, "", [])}


def _visible_tool_result(
    tool_name: str,
    result: Any,
    target: str = "",
    *,
    summary: str = "",
    preview: list[dict[str, str]] | None = None,
    max_chars: int = _TOOL_VISIBLE_RESULT_MAX_CHARS,
) -> Any:
    """Return a model/frontend visible tool result.

    Small results stay byte-for-byte compatible. Oversized results keep the
    useful routing fields plus compact previews and full-result references.
    """
    if not isinstance(result, dict):
        return result
    force_compact = (
        str(result.get("status") or "").strip().lower() == "approval_required"
        or isinstance(result.get("approval"), dict)
    )
    if not force_compact and _json_size(result) <= max_chars:
        return result

    summary_text = summary or _tool_result_summary(tool_name, result, target)
    preview_rows = preview if preview is not None else _tool_result_preview(tool_name, result, target)
    compacted: dict[str, Any] = {
        "status": result.get("status"),
        "summary": summary_text,
        "preview": preview_rows,
        "target": target or result.get("path") or result.get("url") or result.get("command") or result.get("query"),
        "truncated": True,
        "fullResultRef": _tool_result_full_ref(tool_name, result),
    }
    approval = result.get("approval")
    if isinstance(approval, dict):
        compacted["approval"] = {
            key: value
            for key, value in {
                "id": approval.get("id"),
                "kind": approval.get("kind"),
                "decision": approval.get("decision"),
                "createdAt": approval.get("createdAt"),
            }.items()
            if value not in (None, "", [])
        }

    if tool_name == "run_command":
        command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
        compacted.update({
            "exitCode": result.get("exitCode"),
            "durationMs": result.get("durationMs"),
            "cwd": result.get("cwd") or command_log.get("cwd"),
            "shell": result.get("shell") or command_log.get("shell"),
            "command": command_log.get("command") or result.get("command") or target,
            "stdout": _head_tail_text(result.get("stdout") or ""),
            "stderr": _head_tail_text(result.get("stderr") or ""),
        })
    elif tool_name == "read_file":
        compacted.update({
            "path": result.get("path") or target,
            "bytesRead": result.get("bytesRead"),
            "content": _head_tail_text(result.get("content") or ""),
        })
    elif tool_name in {"search_files", "code_search"}:
        result_key = "matches" if tool_name == "search_files" else "results"
        matches = result.get(result_key)
        total = result.get("total") if tool_name == "search_files" else result.get("totalMatches")
        compacted.update({
            "query": result.get("query") or target,
            "total": total if isinstance(total, int) else len(matches) if isinstance(matches, list) else 0,
            result_key: _compact_list_items(matches),
        })
    elif tool_name in {"web_fetch", "browser"}:
        compacted.update({
            "url": result.get("url") or target,
            "statusCode": result.get("statusCode"),
            "contentType": result.get("contentType"),
            "bytesRead": result.get("bytesRead"),
            "title": result.get("title"),
            "content": _head_tail_text(result.get("content") or result.get("body") or ""),
        })
    elif tool_name == "git_diff":
        files = result.get("files")
        compacted.update({
            "staged": result.get("staged"),
            "files": _compact_list_items(files),
            "fileCount": len(files) if isinstance(files, list) else None,
            "diffText": _head_tail_text(result.get("diffText") or result.get("diff") or ""),
        })
    elif tool_name in {"apply_patch", "write_file"}:
        paths = result.get("changedPaths")
        compacted.update({
            "filesChanged": result.get("filesChanged"),
            "changedPaths": paths[:_TOOL_VISIBLE_COLLECTION_LIMIT] if isinstance(paths, list) else paths,
            "diffText": _head_tail_text(result.get("diffText") or ""),
        })
    else:
        for key in ("error", "message", "summary", "path", "url", "id", "count", "total"):
            if result.get(key) is not None:
                compacted[key] = _compact_snippet(result.get(key))
        for key in ("items", "entries", "results", "matches", "files"):
            if isinstance(result.get(key), list):
                compacted[key] = _compact_list_items(result.get(key))
                break

    return {key: value for key, value in compacted.items() if value not in (None, "", [])}


def _model_visible_tool_result(tool_name: str, result: Any, target: str = "", *, summary: str = "", preview: list[dict[str, str]] | None = None) -> Any:
    return _visible_tool_result(tool_name, result, target, summary=summary, preview=preview)


def _frontend_visible_tool_result(tool_name: str, result: Any, target: str = "", *, summary: str = "", preview: list[dict[str, str]] | None = None) -> Any:
    return _visible_tool_result(tool_name, result, target, summary=summary, preview=preview)


def _visible_command_output_chunk(chunk: Any) -> str:
    if not isinstance(chunk, str) or len(chunk) <= _TOOL_VISIBLE_RESULT_MAX_CHARS:
        return chunk if isinstance(chunk, str) else ""
    head = chunk[:_TOOL_VISIBLE_TEXT_HEAD_CHARS]
    tail = chunk[-_TOOL_VISIBLE_TEXT_TAIL_CHARS:]
    omitted = len(chunk) - _TOOL_VISIBLE_TEXT_HEAD_CHARS - _TOOL_VISIBLE_TEXT_TAIL_CHARS
    return f"{head}\n...[truncated {omitted} chars; full output is stored in the command log]...\n{tail}"


def _compact_operation_key(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip().replace("\\", "/").lower()
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def _preview_paths(items: Any, *, limit: int = 3) -> str:
    if not isinstance(items, list):
        return ""
    paths: list[str] = []
    for item in items:
        if isinstance(item, str):
            path = item.strip()
        elif isinstance(item, dict):
            path = str(item.get("path") or item.get("name") or "").strip()
        else:
            path = ""
        if path:
            paths.append(path)
        if len(paths) >= limit:
            break
    return ", ".join(paths)


def _paths_from_patch_text(patch_text: Any, *, limit: int = 3) -> list[str]:
    text = str(patch_text or "")
    if not text:
        return []
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                candidate = parts[3]
            else:
                candidate = ""
        elif line.startswith("+++ "):
            candidate = line[4:].strip()
        else:
            continue
        if candidate.startswith("b/"):
            candidate = candidate[2:]
        if candidate and candidate != "/dev/null" and candidate not in paths:
            paths.append(candidate)
        if len(paths) >= limit:
            break
    return paths


def _preview_memory_items(items: Any, *, limit: int = 3) -> str:
    if not isinstance(items, list):
        return ""
    previews: list[str] = []
    for item in items:
        if isinstance(item, dict):
            content = str(item.get("content") or "").strip()
            kind = str(item.get("kind") or "").strip()
            ident = str(item.get("id") or "").strip()
            if kind and content:
                preview = f"{kind}: {content}"
            else:
                preview = content or (f"{kind}: {ident}" if kind and ident else ident)
        else:
            preview = str(item or "").strip()
        if preview:
            previews.append(_compact_text(preview, 80))
        if len(previews) >= limit:
            break
    return ", ".join(previews)


def _preview_generic_items(items: Any, *, limit: int = 3) -> str:
    if not isinstance(items, list):
        return ""
    previews: list[str] = []
    for item in items:
        if isinstance(item, str):
            preview = item.strip()
        elif isinstance(item, dict):
            preview = str(
                item.get("path")
                or item.get("file")
                or item.get("name")
                or item.get("title")
                or item.get("id")
                or item.get("url")
                or ""
            ).strip()
            if not preview:
                preview = str(
                    item.get("summary")
                    or item.get("message")
                    or item.get("preview")
                    or item.get("text")
                    or ""
                ).strip()
        else:
            preview = str(item or "").strip()
        if preview:
            previews.append(_compact_text(preview, 80))
        if len(previews) >= limit:
            break
    return ", ".join(previews)


def _preview_notebook_cells(cells: Any, *, limit: int = 3) -> str:
    if not isinstance(cells, list):
        return ""
    previews: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        index = cell.get("index")
        cell_type = str(cell.get("cellType") or cell.get("cell_type") or "cell").strip()
        source_preview = _compact_text(cell.get("sourcePreview") or cell.get("source") or "", 60)
        if isinstance(index, int):
            label = f"#{index} {cell_type}"
        else:
            label = cell_type
        previews.append(f"{label}: {source_preview}" if source_preview else label)
        if len(previews) >= limit:
            break
    return ", ".join(previews)


def _preview_row(label: str, value: Any, *, limit: int = 160) -> dict[str, str] | None:
    text = _compact_text(value, limit)
    if not label or not text:
        return None
    return {"label": label, "value": text}


def _approval_request_preview(kind: str, request: dict[str, Any]) -> list[dict[str, str]]:
    if kind == "run_command":
        rows = [
            _preview_row("命令", request.get("command"), limit=220),
        ]
        if request.get("toolName") == "notebook" or request.get("notebookAction") == "execute_cell":
            rows.extend(
                [
                    _preview_row("Notebook", request.get("path")),
                    _preview_row("Cell", request.get("cellIndex")),
                ]
            )
        rows.extend(
            [
                _preview_row("目录", request.get("cwd") or request.get("workspaceRoot")),
                _preview_row("Shell", request.get("shell")),
                _preview_row("原因", request.get("policyReason") or request.get("risk") or request.get("reason")),
            ]
        )
    elif kind == "network_access":
        rows = (
            _preview_row("方法", request.get("method") or "GET"),
            _preview_row("URL", request.get("url"), limit=220),
            _preview_row("原因", request.get("reason") or request.get("risk")),
        )
    elif kind == "computer_use":
        coordinates = (
            f"{request.get('x')}, {request.get('y')}"
            if request.get("x") not in (None, "") and request.get("y") not in (None, "")
            else ""
        )
        scroll = (
            f"{request.get('direction') or 'down'} {request.get('amount')}"
            if request.get("direction") or request.get("amount")
            else ""
        )
        rows = (
            _preview_row("应用", request.get("app") or request.get("target") or request.get("application")),
            _preview_row("动作", request.get("action")),
            _preview_row("目标", request.get("selector") or request.get("target")),
            _preview_row("坐标", coordinates),
            _preview_row("文本", request.get("text"), limit=180),
            _preview_row("滚动", scroll),
            _preview_row("URL", request.get("url"), limit=220),
            _preview_row("Page", request.get("pageId") or request.get("browserContextId")),
            _preview_row("权限", request.get("permission") or request.get("summary"), limit=220),
            _preview_row("详情", request.get("details"), limit=220),
        )
    elif kind == "subagent_dispatch":
        rows = (
            _preview_row("子任务", request.get("prompt"), limit=240),
            _preview_row("原因", request.get("reason") or request.get("risk")),
        )
    else:
        rows = (
            _preview_row("摘要", request.get("summary") or request.get("description")),
            _preview_row("目标", request.get("target") or request.get("path") or request.get("url")),
            _preview_row("原因", request.get("reason") or request.get("risk")),
        )
    return [row for row in rows if row is not None][:5]


def _tool_result_is_failure_like(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "").strip().lower()
    return status in {"failed", "error", "timeout", "killed", "partial", "blocked", "validation_failed"} or result.get("ok") is False


def _tool_result_preview(tool_name: str, result: dict[str, Any] | None, target: str = "") -> list[dict[str, str]]:
    if not isinstance(result, dict):
        return []

    failure_like = _tool_result_is_failure_like(result)
    approval_required = str(result.get("status") or "").strip().lower() == "approval_required"
    rows: list[dict[str, str]] = []
    if tool_name == "run_command":
        exit_code = result.get("exitCode")
        command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
        status = f"exit {exit_code}" if isinstance(exit_code, int) else result.get("status")
        output = result.get("stdout") or result.get("stderr") or result.get("summary")
        rows.extend(
            row for row in (
                _preview_row("命令", target or result.get("command") or "command"),
                _preview_row("状态", status or ""),
                _preview_row("目录", result.get("cwd") or command_log.get("cwd") or ""),
                _preview_row("Shell", result.get("shell") or command_log.get("shell") or ""),
                _preview_row("输出", output or "", limit=220),
            ) if row
        )
    elif tool_name == "read_file":
        rows.extend(
            row for row in (
                _preview_row("文件", target or result.get("path") or "file"),
                _preview_row("大小", f"{result.get('bytesRead')} bytes" if isinstance(result.get("bytesRead"), int) else ""),
                _preview_row("行数", f"{len(str(result.get('content') or '').splitlines())} 行" if isinstance(result.get("content"), str) else ""),
            ) if row
        )
    elif tool_name == "write_file":
        if approval_required:
            rows.extend(
                row for row in (
                    _preview_row("Status", "waiting for approval"),
                    _preview_row("File", target or result.get("path") or "file"),
                    _preview_row("Changes", f"{result.get('filesChanged')} file(s)" if result.get("filesChanged") is not None else ""),
                ) if row
            )
            return rows
        rows.extend(
            row for row in (
                _preview_row("文件", target or result.get("path") or "file"),
                _preview_row("写入", f"{result.get('bytesWritten')} bytes" if isinstance(result.get("bytesWritten"), int) else ""),
            ) if row
        )
    elif tool_name in {"list_dir", "list_directory"}:
        items = result.get("items")
        count = len(items) if isinstance(items, list) else 0
        rows.extend(
            row for row in (
                _preview_row("目录", target or result.get("path") or "."),
                _preview_row("项目", f"{count} 项"),
                _preview_row("样例", _preview_paths(items, limit=5)),
            ) if row
        )
    elif tool_name in {"search_files", "code_search"}:
        total = result.get("total") if tool_name == "search_files" else result.get("totalMatches")
        matches = result.get("matches") if tool_name == "search_files" else result.get("results")
        count = total if isinstance(total, int) else len(matches) if isinstance(matches, list) else 0
        rows.extend(
            row for row in (
                _preview_row("查询", result.get("query") or target or "query"),
                _preview_row("命中", f"{count} 项"),
                _preview_row("样例", _preview_paths(matches, limit=5)),
            ) if row
        )
    elif tool_name == "git_status":
        if result.get("isGitRepository") is False:
            return [{"label": "Git", "value": "不是 Git 仓库"}]
        changes = result.get("changes")
        count = len(changes) if isinstance(changes, list) else 0
        sync = []
        if result.get("ahead"):
            sync.append(f"ahead {result.get('ahead')}")
        if result.get("behind"):
            sync.append(f"behind {result.get('behind')}")
        rows.extend(
            row for row in (
                _preview_row("分支", result.get("branch") or "detached"),
                _preview_row("同步", ", ".join(sync) or "up to date"),
                _preview_row("改动", f"{count} 个文件"),
                _preview_row("样例", _preview_paths(changes, limit=5)),
            ) if row
        )
    elif tool_name == "git_diff":
        if result.get("isGitRepository") is False:
            return [{"label": "Git", "value": "不是 Git 仓库"}]
        files = result.get("files")
        count = len(files) if isinstance(files, list) else 0
        rows.extend(
            row for row in (
                _preview_row("范围", "staged" if result.get("staged") else "worktree"),
                _preview_row("文件", f"{count} 个"),
                _preview_row("样例", _preview_paths(files, limit=5)),
            ) if row
        )
    elif tool_name == "apply_patch":
        paths = result.get("changedPaths") or []
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or "patch"),
                _preview_row("文件", f"{result.get('filesChanged')} 个" if result.get("filesChanged") is not None else ""),
                _preview_row("样例", _preview_paths(paths, limit=5)),
            ) if row
        )
    elif tool_name in {"agent", "task"}:
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or "completed"),
                _preview_row("摘要", result.get("summary") or result.get("resultSummary") or ""),
                _preview_row("子任务", result.get("childTaskId") or result.get("taskId") or ""),
            ) if row
        )
    elif tool_name == "computer_use":
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        request = result.get("request") if isinstance(result.get("request"), dict) else {}
        coordinates = detail.get("coordinates") if isinstance(detail.get("coordinates"), dict) else {}
        coordinate_text = (
            f"{coordinates.get('x')}, {coordinates.get('y')}"
            if coordinates.get("x") is not None and coordinates.get("y") is not None
            else (
                f"{request.get('x')}, {request.get('y')}"
                if request.get("x") not in (None, "") and request.get("y") not in (None, "")
                else ""
            )
        )
        screenshot_size = (
            f"{result.get('width')}x{result.get('height')}"
            if isinstance(result.get("width"), int) and isinstance(result.get("height"), int)
            else ""
        )
        preview_size = (
            f"{result.get('previewWidth')}x{result.get('previewHeight')}"
            if isinstance(result.get("previewWidth"), int) and isinstance(result.get("previewHeight"), int)
            else ""
        )
        elements = result.get("elements") if isinstance(result.get("elements"), list) else []
        element_samples = []
        for element in elements[:3]:
            if not isinstance(element, dict):
                continue
            label = element.get("label") or element.get("text") or element.get("testId") or element.get("id") or element.get("name")
            tag = element.get("tag") or element.get("role") or "element"
            if label:
                element_samples.append(f"{tag}: {label}")
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or "completed"),
                _preview_row("动作", result.get("action") or request.get("action")),
                _preview_row("目标", target or result.get("target") or result.get("app") or ""),
                _preview_row("选择器", request.get("selector") or detail.get("selector"), limit=180),
                _preview_row("URL", result.get("url") or request.get("url"), limit=220),
                _preview_row("Page", result.get("pageId") or request.get("pageId") or result.get("browserContextId") or request.get("browserContextId")),
                _preview_row("标题", result.get("title") or detail.get("title"), limit=180),
                _preview_row("元素", "; ".join(element_samples), limit=220),
                _preview_row("类型", result.get("failureKind") or result.get("code") or ""),
                _preview_row("执行器", result.get("executor") or ""),
                _preview_row("坐标", coordinate_text),
                _preview_row("截图", screenshot_size),
                _preview_row("预览", preview_size),
                _preview_row("输入", request.get("text"), limit=180),
                _preview_row("修复", result.get("recoveryHint") or "", limit=220),
                _preview_row("摘要", result.get("summary") or result.get("message") or result.get("error") or "", limit=220),
            ) if row
        )
    elif result.get("error"):
        rows = [
            row for row in (
                _preview_row("状态", result.get("status") or "failed"),
                _preview_row("错误", result.get("error"), limit=220),
                _preview_row("类型", result.get("failureKind") or result.get("code") or ""),
                _preview_row("修复", result.get("recoveryHint") or "", limit=220),
            ) if row
        ]
    elif tool_name == "web_fetch":
        status_code = result.get("statusCode")
        status_label = f"HTTP {status_code}" if isinstance(status_code, int) else result.get("status")
        rows.extend(
            row for row in (
                _preview_row("URL", target or result.get("url") or ""),
                _preview_row("状态", status_label or ""),
                _preview_row("类型", result.get("contentType") or ""),
                _preview_row("大小", f"{result.get('bytesRead')} bytes" if isinstance(result.get("bytesRead"), int) else ""),
                _preview_row("摘要", result.get("content") or result.get("body") or "", limit=220),
            ) if row
        )
    elif tool_name == "browser":
        status_code = result.get("statusCode")
        status_label = f"HTTP {status_code}" if isinstance(status_code, int) else result.get("status")
        rows.extend(
            row for row in (
                _preview_row("URL", target or result.get("url") or ""),
                _preview_row("动作", result.get("action") or ""),
                _preview_row("状态", status_label or ""),
                _preview_row("标题", result.get("title") or ""),
                _preview_row("摘要", result.get("content") or result.get("body") or "", limit=220),
            ) if row
        )
    elif tool_name == "notebook":
        execution_result = result.get("executionResult")
        if isinstance(execution_result, dict):
            exit_code = execution_result.get("exitCode")
            status_label = f"exit {exit_code}" if isinstance(exit_code, int) else result.get("status")
            output = execution_result.get("stdout") or execution_result.get("stderr") or ""
            rows.extend(
                row for row in (
                    _preview_row("Notebook", target or result.get("path") or ""),
                    _preview_row("动作", "execute_cell"),
                    _preview_row("状态", status_label or ""),
                    _preview_row("输出", output, limit=220),
                    _preview_row("Cell", result.get("index")),
                ) if row
            )
        elif "source" in result or "outputs" in result:
            outputs = result.get("outputs")
            output_count = len(outputs) if isinstance(outputs, list) else 0
            rows.extend(
                row for row in (
                    _preview_row("Notebook", target or result.get("path") or ""),
                    _preview_row("Cell", result.get("index")),
                    _preview_row("类型", result.get("cellType") or ""),
                    _preview_row("输出", f"{output_count} 项"),
                    _preview_row("源码", result.get("source") or "", limit=220),
                ) if row
            )
        else:
            rows.extend(
                row for row in (
                    _preview_row("Notebook", target or result.get("path") or ""),
                    _preview_row("动作", "list_cells"),
                    _preview_row("Kernel", result.get("kernel") or ""),
                    _preview_row("Cells", f"{result.get('totalCells')} 个" if isinstance(result.get("totalCells"), int) else ""),
                    _preview_row("样例", _preview_notebook_cells(result.get("cells"), limit=3), limit=240),
                ) if row
            )
    elif tool_name == "memory.remember":
        keywords = result.get("keywords")
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or "ok"),
                _preview_row("类型", result.get("kind") or ""),
                _preview_row("ID", result.get("id") or ""),
                _preview_row("关键词", ", ".join(str(item) for item in keywords[:5]) if isinstance(keywords, list) else ""),
            ) if row
        )
    elif tool_name == "memory.recall":
        memories = result.get("memories")
        count = result.get("count") if isinstance(result.get("count"), int) else len(memories) if isinstance(memories, list) else 0
        rows.extend(
            row for row in (
                _preview_row("查询", target or "memory"),
                _preview_row("结果", f"{count} 项"),
                _preview_row("样例", _preview_memory_items(memories, limit=3), limit=240),
            ) if row
        )
    elif tool_name == "scratchpad.write":
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or "ok"),
                _preview_row("键", target or result.get("key") or ""),
                _preview_row("ID", result.get("id") or ""),
            ) if row
        )
    elif tool_name == "scratchpad.read":
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or "ok"),
                _preview_row("键", target or result.get("key") or ""),
                _preview_row("值", result.get("value") or "", limit=220),
                _preview_row("ID", result.get("id") or ""),
            ) if row
        )
    else:
        sample_items = (
            result.get("items")
            or result.get("entries")
            or result.get("results")
            or result.get("matches")
            or result.get("files")
        )
        rows.extend(
            row for row in (
                _preview_row("状态", result.get("status") or result.get("state") or ""),
                _preview_row(
                    "目标",
                    target
                    or result.get("target")
                    or result.get("path")
                    or result.get("url")
                    or result.get("id")
                    or "",
                ),
                _preview_row("摘要", result.get("summary") or result.get("message") or result.get("resultSummary") or ""),
                _preview_row("结果", result.get("result") or result.get("output") or result.get("text") or "", limit=220),
                _preview_row("样例", _preview_generic_items(sample_items, limit=3), limit=240),
            ) if row
        )
    if failure_like and tool_name != "computer_use":
        failure_rows = [
            row for row in (
                _preview_row("状态", result.get("status") or "failed"),
                _preview_row("类型", result.get("failureKind") or result.get("code") or ""),
                _preview_row("修复", result.get("recoveryHint") or "", limit=220),
            ) if row
        ]
        if failure_rows:
            merged: list[dict[str, str]] = []
            seen_labels: set[str] = set()
            for row in [*failure_rows, *rows]:
                label = row.get("label") or ""
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                merged.append(row)
            rows = merged
    if tool_name == "computer_use":
        return rows[:8]
    return rows[:5]


def _tool_target(tool_name: str, arguments: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    result = result or {}
    if tool_name == "run_command":
        return _compact_text(arguments.get("command") or result.get("command") or "", 140)
    if tool_name in {"read_file", "write_file", "list_dir", "list_directory"}:
        return _compact_text(result.get("path") or arguments.get("path") or ".", 180)
    if tool_name == "notebook":
        path = result.get("path") or arguments.get("path") or ""
        cell_index = result.get("index") if "index" in result else arguments.get("cell_index")
        suffix = f"#cell {cell_index}" if isinstance(cell_index, int) else ""
        return _compact_text(f"{path}{f' {suffix}' if suffix else ''}", 180)
    if tool_name in {"search_files", "code_search"}:
        query = arguments.get("query") or result.get("query") or ""
        mode = arguments.get("mode") or result.get("mode") or ""
        return _compact_text(f"{mode}: {query}" if mode and query else query, 180)
    if tool_name == "apply_patch":
        paths = result.get("changedPaths") or arguments.get("changedPaths") or []
        if isinstance(paths, list) and paths:
            visible = ", ".join(str(path) for path in paths[:3])
            return _compact_text(visible + (f", +{len(paths) - 3}" if len(paths) > 3 else ""), 180)
        patch_paths = _paths_from_patch_text(arguments.get("patchText") or arguments.get("patch_text"), limit=3)
        if patch_paths:
            return _compact_text(", ".join(patch_paths) + (f", +{len(patch_paths) - 3}" if len(patch_paths) > 3 else ""), 180)
        files = arguments.get("files")
        if isinstance(files, list):
            paths = [str(item.get("path")) for item in files if isinstance(item, dict) and item.get("path")]
            if paths:
                return _compact_text(", ".join(paths[:3]) + (f", +{len(paths) - 3}" if len(paths) > 3 else ""), 180)
        return "patch"
    if tool_name == "computer_use":
        return _compact_text(arguments.get("target") or arguments.get("app") or result.get("target") or "desktop", 180)
    if tool_name in {"web_fetch", "browser"}:
        return _compact_text(result.get("url") or arguments.get("url") or "", 180)
    if tool_name == "memory.remember":
        return _compact_text(arguments.get("content") or "", 180)
    if tool_name == "memory.recall":
        return _compact_text(arguments.get("query") or "", 180)
    if tool_name.startswith("scratchpad."):
        return _compact_text(result.get("key") or arguments.get("key") or "", 180)
    return _compact_text(
        arguments.get("path")
        or arguments.get("file")
        or arguments.get("url")
        or arguments.get("uri")
        or arguments.get("query")
        or arguments.get("pattern")
        or arguments.get("content")
        or arguments.get("target")
        or arguments.get("id")
        or arguments.get("key")
        or arguments.get("action")
        or "",
        180,
    )


def _tool_input_summary(tool_name: str, arguments: dict[str, Any], target: str = "") -> str:
    if tool_name == "run_command":
        return _compact_text(arguments.get("command") or target or "run command", 180)
    if tool_name == "read_file":
        return _compact_text(f"read {target or arguments.get('path') or 'file'}", 180)
    if tool_name == "write_file":
        content = str(arguments.get("content") or "")
        return _compact_text(f"write {target or arguments.get('path') or 'file'} ({len(content.encode('utf-8', errors='replace'))} bytes)", 180)
    if tool_name in {"list_dir", "list_directory"}:
        return _compact_text(f"list {target or arguments.get('path') or '.'}", 180)
    if tool_name in {"search_files", "code_search"}:
        return _compact_text(f"search {arguments.get('query') or ''}".strip(), 180)
    if tool_name == "notebook":
        action = str(arguments.get("action") or "list_cells")
        cell_index = arguments.get("cell_index")
        target_text = target or arguments.get("path") or ""
        if isinstance(cell_index, int) and f"cell {cell_index}" not in str(target_text):
            target_text = f"{target_text} cell {cell_index}".strip()
        return _compact_text(f"notebook {action} {target_text}".strip(), 180)
    if tool_name == "apply_patch":
        return _compact_text(f"modify {target}" if target else "apply patch", 180)
    if tool_name == "computer_use":
        action = arguments.get("action") or "request"
        permission = arguments.get("permission") or target or "computer use"
        return _compact_text(f"{action} {permission}", 180)
    if tool_name == "web_fetch":
        method = str(arguments.get("method") or "GET").upper()
        return _compact_text(f"fetch {method} {target or arguments.get('url') or ''}".strip(), 180)
    if tool_name == "browser":
        action = str(arguments.get("action") or "read")
        return _compact_text(f"browser {action} {target or arguments.get('url') or ''}".strip(), 180)
    if tool_name == "memory.remember":
        kind = str(arguments.get("kind") or "working")
        return _compact_text(f"remember {kind}: {target or arguments.get('content') or ''}", 180)
    if tool_name == "memory.recall":
        limit = arguments.get("limit")
        suffix = f" (limit {limit})" if isinstance(limit, int) else ""
        return _compact_text(f"recall {target or arguments.get('query') or ''}{suffix}", 180)
    if tool_name == "scratchpad.write":
        return _compact_text(f"scratchpad write {target or arguments.get('key') or 'key'}", 180)
    if tool_name == "scratchpad.read":
        return _compact_text(f"scratchpad read {target or arguments.get('key') or 'key'}", 180)
    return _compact_text(target or tool_name, 180)


def _tool_write_execution_is_unblocked(tool_name: str, arguments: dict[str, Any], approval_required: bool) -> bool:
    if tool_name not in {"apply_patch", "write_file"}:
        return True
    approval_id = str(arguments.get("approvalId") or arguments.get("approval_id") or "").strip()
    return bool(approval_id) or not approval_required


def _write_tool_requires_approval_from_mode(tool_name: str, approval_mode: Any) -> bool:
    if tool_name not in {"apply_patch", "write_file"}:
        return False
    mode = str(approval_mode or "").strip().lower()
    if mode in {"off", "never", "none", "no", "false"}:
        return False
    return True


def _tool_runtime_progress_message(
    tool_name: str,
    arguments: dict[str, Any],
    target: str = "",
    *,
    approval_required: bool = False,
) -> str:
    if tool_name == "run_command":
        command = target or arguments.get("command") or "command"
        return _compact_text(f"正在运行命令：{command}", 220)
    if tool_name == "read_file":
        return _compact_text(f"正在读取文件：{target or arguments.get('path') or 'file'}", 220)
    if tool_name in {"list_dir", "list_directory"}:
        return _compact_text(f"正在列出目录：{target or arguments.get('path') or '.'}", 220)
    if tool_name in {"search_files", "code_search"}:
        query = arguments.get("query") or arguments.get("pattern") or ""
        return _compact_text(f"正在搜索：{query}" if query else "正在搜索", 220)
    if tool_name == "git_status":
        return "正在检查 Git 状态"
    if tool_name == "git_diff":
        return "正在读取 Git 差异"
    if tool_name in {"web_fetch", "browser"}:
        method = str(arguments.get("method") or "GET").upper()
        if tool_name == "browser":
            method = str(arguments.get("action") or "read")
        destination = target or arguments.get("url") or ""
        return _compact_text(f"正在发送网页请求：{method} {destination}".strip(), 220)
    if tool_name == "notebook":
        action = str(arguments.get("action") or "list_cells")
        return _compact_text(f"正在处理 Notebook：{action} {target or arguments.get('path') or ''}".strip(), 220)
    if tool_name == "apply_patch":
        if not _tool_write_execution_is_unblocked(tool_name, arguments, approval_required):
            return ""
        return _compact_text(f"正在应用文件改动：{target or 'patch'}", 220)
    if tool_name == "write_file":
        if not _tool_write_execution_is_unblocked(tool_name, arguments, approval_required):
            return ""
        return _compact_text(f"正在写入文件：{target or arguments.get('path') or 'file'}", 220)
    if tool_name == "computer_use":
        approval_id = str(arguments.get("approvalId") or arguments.get("approval_id") or "").strip()
        if not approval_id:
            return ""
        return _compact_text(f"正在执行桌面操作：{target or arguments.get('target') or arguments.get('app') or 'desktop'}", 220)
    if tool_name == "memory.remember":
        kind = str(arguments.get("kind") or "working")
        return _compact_text(f"正在整理 {kind} 记忆内容", 220)
    if tool_name == "memory.recall":
        query = target or arguments.get("query") or "memory"
        return _compact_text(f"正在查询记忆索引：{query}", 220)
    if tool_name == "scratchpad.write":
        key = target or arguments.get("key") or "key"
        return _compact_text(f"正在写入 scratchpad：{key}", 220)
    if tool_name == "scratchpad.read":
        key = target or arguments.get("key") or "key"
        return _compact_text(f"正在读取 scratchpad：{key}", 220)
    if tool_name in {"agent", "task"}:
        title = (
            arguments.get("description")
            or arguments.get("title")
            or arguments.get("subagent_type")
            or arguments.get("agent_type")
            or arguments.get("agentType")
            or arguments.get("prompt")
            or "subtask"
        )
        return _compact_text(f"正在启动子任务：{title}", 220)
    if tool_name.startswith("mcp__"):
        destination = _tool_progress_destination(tool_name, arguments, target)
        return _compact_text(f"正在调用 MCP 工具：{destination}", 220)
    destination = _tool_progress_destination(tool_name, arguments, target)
    if destination:
        return _compact_text(f"正在调用工具：{destination}", 220)
    return ""


def _tool_progress_destination(tool_name: str, arguments: dict[str, Any], target: str = "") -> Any:
    for value in (
        target,
        arguments.get("query"),
        arguments.get("pattern"),
        arguments.get("content"),
        arguments.get("url"),
        arguments.get("uri"),
        arguments.get("target"),
        arguments.get("path"),
        arguments.get("file"),
        arguments.get("id"),
        arguments.get("key"),
        arguments.get("action"),
        tool_name,
    ):
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return ""


def _tool_runtime_activity_messages(result: dict[str, Any] | None, *, limit: int = 8) -> list[str]:
    if not isinstance(result, dict):
        return []
    activity_items: list[Any] = []
    for key in ("steps", "logs", "events", "progress", "timeline", "messages", "diagnostics", "stages", "tasks"):
        value = result.get(key)
        if isinstance(value, list) and value:
            activity_items = value
            break
    if not activity_items:
        return []

    messages: list[str] = []
    seen: set[str] = set()
    for item in activity_items[:limit]:
        text = _tool_runtime_activity_item_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        messages.append(text)
    return messages


def _task_dispatch_steps(arguments: dict[str, Any], result: dict[str, Any]) -> list[dict[str, str]]:
    title = _compact_text(
        arguments.get("description")
        or arguments.get("title")
        or arguments.get("subagent_type")
        or arguments.get("agentType")
        or arguments.get("agent_type")
        or arguments.get("prompt")
        or "subtask",
        120,
    )
    status = str(result.get("status") or "completed")
    child_id = str(result.get("childTaskId") or result.get("taskId") or "").strip()
    summary = _compact_text(result.get("summary") or result.get("resultSummary") or child_id or status, 180)
    steps = [
        {"label": "prepare", "status": "completed", "summary": title},
        {"label": "dispatch", "status": "completed" if status == "completed" else status, "summary": summary},
    ]
    if child_id:
        steps.append({"label": "child_task", "status": "completed", "summary": "Child task recorded."})
    return steps


def _normalize_subagent_budget(arguments: dict[str, Any]) -> dict[str, Any]:
    budget = dict(arguments.get("budget")) if isinstance(arguments.get("budget"), dict) else {}
    if "maxTokens" in budget or "max_tokens" in budget or "remainingTokens" in budget or "remaining_tokens" in budget:
        raw_limit = budget.get("maxTokens", budget.get("max_tokens"))
        raw_remaining = budget.get("remainingTokens", budget.get("remaining_tokens"))
        try:
            limit = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError):
            limit = None
        try:
            remaining = int(raw_remaining) if raw_remaining is not None else None
        except (TypeError, ValueError):
            remaining = None
        effective = max(
            _MIN_MODEL_SUPPLIED_CHILD_TOKEN_BUDGET,
            *(value for value in (limit, remaining) if value is not None),
        )
        budget["maxTokens"] = effective
        budget["remainingTokens"] = max(effective, remaining or 0)
        budget["normalizedByRuntime"] = True
    if (
        "maxToolCalls" in budget
        or "max_tool_calls" in budget
        or "remainingToolCalls" in budget
        or "remaining_tool_calls" in budget
    ):
        raw_limit = budget.get("maxToolCalls", budget.get("max_tool_calls"))
        raw_remaining = budget.get("remainingToolCalls", budget.get("remaining_tool_calls"))
        try:
            limit = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError):
            limit = None
        try:
            remaining = int(raw_remaining) if raw_remaining is not None else None
        except (TypeError, ValueError):
            remaining = None
        effective_calls = max(
            _MIN_MODEL_SUPPLIED_CHILD_TOOL_CALL_BUDGET,
            *(value for value in (limit, remaining) if value is not None),
        )
        budget["maxToolCalls"] = effective_calls
        budget["remainingToolCalls"] = max(effective_calls, remaining or 0)
        budget["normalizedByRuntime"] = True
    else:
        budget.setdefault("maxToolCalls", _DEFAULT_CHILD_TOOL_CALL_BUDGET)
        budget.setdefault("remainingToolCalls", _DEFAULT_CHILD_TOOL_CALL_BUDGET)
    if budget:
        arguments["budget"] = budget
    return arguments


def _tool_runtime_activity_item_text(item: Any) -> str:
    if isinstance(item, str):
        return _compact_text(item, 220)
    if not isinstance(item, dict):
        return _compact_text(item, 220)

    status_value = item.get("status") or item.get("state")
    if status_value is None and isinstance(item.get("ok"), bool):
        status_value = "completed" if item.get("ok") is True else "failed"
    label = _compact_text(
        item.get("label")
        or item.get("title")
        or item.get("name")
        or item.get("phase")
        or item.get("step")
        or item.get("action")
        or item.get("type")
        or item.get("event")
        or item.get("level")
        or item.get("code")
        or "",
        80,
    )
    status = _compact_text(status_value or "", 40)
    detail = _compact_text(
        item.get("summary")
        or item.get("message")
        or item.get("error")
        or item.get("reason")
        or item.get("detail")
        or item.get("description")
        or item.get("output")
        or item.get("text")
        or "",
        180,
    )
    target = _compact_text(item.get("target") or item.get("path") or item.get("file") or item.get("url") or "", 120)
    metrics: list[str] = []
    for key, suffix in (("count", ""), ("total", ""), ("durationMs", "ms"), ("elapsedMs", "ms")):
        value = item.get(key)
        if isinstance(value, (int, float)):
            metrics.append(f"{key}={value}{suffix}")
    metric_text = ", ".join(metrics)

    prefix = label
    if prefix and status:
        prefix = f"{prefix} ({status})"
    elif status:
        prefix = status
    if prefix and detail:
        return f"{prefix}: {detail}{f' [{metric_text}]' if metric_text else ''}"
    if prefix and target:
        return f"{prefix}: {target}{f' [{metric_text}]' if metric_text else ''}"
    return (prefix or detail or target) + (f" [{metric_text}]" if metric_text and (prefix or detail or target) else "")


def _result_preview_output_text(rows: list[dict[str, str]], *, limit: int = 4) -> str:
    lines: list[str] = []
    for row in rows[:limit]:
        label = str(row.get("label") or "").strip()
        value = str(row.get("value") or "").strip()
        if label and value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _tool_runtime_output_message(tool_name: str, result: dict[str, Any] | None, target: str = "") -> str:
    if tool_name not in {
        "web_fetch",
        "read_file",
        "write_file",
        "list_dir",
        "list_directory",
        "search_files",
        "code_search",
        "git_status",
        "git_diff",
        "apply_patch",
        "browser",
        "notebook",
        "agent",
        "task",
        "computer_use",
        "memory.remember",
        "memory.recall",
        "scratchpad.write",
        "scratchpad.read",
    } or not isinstance(result, dict):
        return ""
    if result.get("status") in {"approval_required", "blocked", "failed"}:
        return ""
    preview = _tool_result_preview(tool_name, result, target)
    return _compact_text(_result_preview_output_text(preview), 320)


def _tool_result_summary(tool_name: str, result: dict[str, Any] | None, target: str = "") -> str:
    if not isinstance(result, dict):
        return ""
    status = result.get("status")
    error = result.get("error")
    status_text = str(status or "").strip().lower()
    if status_text == "approval_required":
        target_text = target or result.get("path") or result.get("command") or result.get("url") or tool_name
        if tool_name == "write_file":
            return _compact_text(f"approval required before writing {target_text}", 180)
        if tool_name == "apply_patch":
            return _compact_text(f"approval required before applying changes {target_text}".strip(), 180)
        if tool_name == "run_command":
            return _compact_text(f"approval required before running {target_text}", 180)
        return _compact_text(f"approval required before {tool_name} {target_text}".strip(), 180)
    if tool_name == "computer_use":
        action = result.get("action")
        request = result.get("request") if isinstance(result.get("request"), dict) else {}
        if not action and isinstance(request, dict):
            action = request.get("action")
        target_text = target or result.get("target") or request.get("target") or request.get("app") or "desktop"
        if str(status or "").lower() == "blocked":
            failure = result.get("failureKind") or "blocked"
            error_text = result.get("error") or result.get("recoveryHint") or ""
            return _compact_text(
                f"blocked {action or 'computer_use'} {target_text}: {failure}{f' - {error_text}' if error_text else ''}",
                220,
            )
        if result.get("executor"):
            return _compact_text(f"desktop {action or 'action'} {target_text} via {result.get('executor')}", 180)
        if result.get("width") and result.get("height"):
            return _compact_text(f"screenshot {target_text} ({result.get('width')}x{result.get('height')})", 180)
        return _compact_text(result.get("summary") or result.get("error") or status or "", 180)
    if error:
        return _compact_text(f"failed: {error}", 180)
    if tool_name == "run_command":
        exit_code = result.get("exitCode")
        stdout = _compact_text(result.get("stdout") or result.get("stderr") or "", 120)
        prefix = f"exit {exit_code}" if exit_code is not None else str(status or "completed")
        return _compact_text(f"{prefix}: {stdout}" if stdout else prefix, 180)
    if tool_name == "read_file":
        bytes_read = result.get("bytesRead")
        suffix = f" ({bytes_read} bytes)" if isinstance(bytes_read, int) else ""
        return _compact_text(f"read {target or result.get('path') or 'file'}{suffix}", 180)
    if tool_name == "write_file":
        bytes_written = result.get("bytesWritten")
        suffix = f" ({bytes_written} bytes)" if isinstance(bytes_written, int) else ""
        return _compact_text(f"wrote {target or result.get('path') or 'file'}{suffix}", 180)
    if tool_name in {"list_dir", "list_directory"}:
        items = result.get("items")
        count = len(items) if isinstance(items, list) else 0
        preview = _preview_paths(items)
        return _compact_text(
            f"listed {count} item(s) in {target or result.get('path') or '.'}{f': {preview}' if preview else ''}",
            180,
        )
    if tool_name in {"search_files", "code_search"}:
        total = result.get("total") if tool_name == "search_files" else result.get("totalMatches")
        matches = result.get("matches") if tool_name == "search_files" else result.get("results")
        count = total if isinstance(total, int) else len(matches) if isinstance(matches, list) else 0
        preview = _preview_paths(matches)
        query = result.get("query") or target or "query"
        return _compact_text(
            f"found {count} match(es) for {query}{f': {preview}' if preview else ''}",
            180,
        )
    if tool_name == "git_status":
        if result.get("isGitRepository") is False:
            return "not a git repository"
        changes = result.get("changes")
        count = len(changes) if isinstance(changes, list) else 0
        branch = result.get("branch") or "detached"
        sync = []
        if result.get("ahead"):
            sync.append(f"ahead {result.get('ahead')}")
        if result.get("behind"):
            sync.append(f"behind {result.get('behind')}")
        preview = _preview_paths(changes)
        return _compact_text(
            f"{branch}: {count} changed file(s){f' ({', '.join(sync)})' if sync else ''}{f': {preview}' if preview else ''}",
            180,
        )
    if tool_name == "git_diff":
        if result.get("isGitRepository") is False:
            return "not a git repository"
        files = result.get("files")
        count = len(files) if isinstance(files, list) else 0
        preview = _preview_paths(files)
        scope = "staged" if result.get("staged") else "worktree"
        return _compact_text(f"{scope} diff: {count} file(s){f': {preview}' if preview else ''}", 180)
    if tool_name == "apply_patch":
        files_changed = result.get("filesChanged")
        return _compact_text(f"{status or 'patch'} {files_changed or ''} file(s) {target}".strip(), 180)
    if tool_name in {"web_fetch", "browser"}:
        status_code = result.get("statusCode")
        status_label = f"HTTP {status_code}" if isinstance(status_code, int) else str(status or "ok")
        details = []
        if isinstance(result.get("bytesRead"), int):
            details.append(f"{result.get('bytesRead')} bytes")
        if result.get("contentType"):
            details.append(_compact_text(result.get("contentType"), 60))
        if tool_name == "browser" and result.get("title"):
            details.append(_compact_text(result.get("title"), 60))
        excerpt = _compact_text(result.get("content") or result.get("body") or "", 100)
        detail_text = f" ({', '.join(details)})" if details else ""
        return _compact_text(
            f"{status_label} {target or result.get('url') or ''}{detail_text}{f': {excerpt}' if excerpt else ''}",
            180,
        )
    if tool_name == "notebook":
        path = target or result.get("path") or "notebook"
        execution_result = result.get("executionResult")
        if isinstance(execution_result, dict):
            exit_code = execution_result.get("exitCode")
            output = _compact_text(execution_result.get("stdout") or execution_result.get("stderr") or "", 100)
            return _compact_text(
                f"executed {path}: exit {exit_code}{f': {output}' if output else ''}",
                180,
            )
        if "source" in result or "outputs" in result:
            outputs = result.get("outputs")
            output_count = len(outputs) if isinstance(outputs, list) else 0
            cell_index = result.get("index")
            cell_type = result.get("cellType") or "cell"
            source = _compact_text(result.get("source") or "", 100)
            cell_label = f" cell {cell_index}" if f"cell {cell_index}" not in str(path) else ""
            return _compact_text(
                f"read {path}{cell_label} ({cell_type}, {output_count} output(s)){f': {source}' if source else ''}",
                180,
            )
        total = result.get("totalCells")
        kernel = result.get("kernel")
        sample = _preview_notebook_cells(result.get("cells"), limit=2)
        return _compact_text(
            f"listed {total if isinstance(total, int) else 0} notebook cell(s) in {path}{f' ({kernel})' if kernel else ''}{f': {sample}' if sample else ''}",
            180,
        )
    if tool_name == "memory.remember":
        keywords = result.get("keywords")
        keyword_text = ", ".join(str(item) for item in keywords[:5]) if isinstance(keywords, list) else ""
        ident = result.get("id")
        kind = result.get("kind") or "memory"
        return _compact_text(
            f"remembered {kind} memory{f' {ident}' if ident else ''}{f': {keyword_text}' if keyword_text else ''}",
            180,
        )
    if tool_name == "memory.recall":
        memories = result.get("memories")
        count = result.get("count") if isinstance(result.get("count"), int) else len(memories) if isinstance(memories, list) else 0
        sample = _preview_memory_items(memories, limit=2)
        return _compact_text(
            f"recalled {count} memory item(s) for {target or 'memory'}{f': {sample}' if sample else ''}",
            180,
        )
    if tool_name == "scratchpad.write":
        key = result.get("key") or target or "key"
        ident = result.get("id")
        return _compact_text(f"stored scratchpad {key}{f' ({ident})' if ident else ''}", 180)
    if tool_name == "scratchpad.read":
        key = result.get("key") or target or "key"
        if str(status or "").strip().lower() == "not_found":
            return _compact_text(f"scratchpad {key} not found", 180)
        value = _compact_text(result.get("value") or "", 100)
        return _compact_text(f"read scratchpad {key}{f': {value}' if value else ''}", 180)
    summary = result.get("summary")
    return _compact_text(summary or status or "", 180)


def _tool_category(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "run_command":
        return "verification" if is_verification_command(arguments.get("command")) else "command"
    if tool_name in {"apply_patch", "write_file"}:
        return "file_change"
    if tool_name in {"read_file", "list_dir", "list_directory"}:
        return "context_read"
    if tool_name in {"search_files", "code_search"}:
        return "search"
    if tool_name in {"git_status", "git_diff"}:
        return "git"
    if tool_name in {"agent", "task"}:
        return "subtask"
    if tool_name == "computer_use":
        return "computer_use"
    if tool_name in {"web_fetch", "browser"}:
        return "web"
    if tool_name.startswith("mcp__"):
        return "mcp"
    if tool_name == "notebook":
        return "notebook"
    if tool_name.startswith(("memory.", "scratchpad.")):
        return "memory"
    return "tool"


_TOOL_PHASES: dict[str, tuple[str, str]] = {
    "command": ("command", "命令"),
    "computer_use": ("computer_use", "桌面操作"),
    "context_read": ("context_read", "读取上下文"),
    "file_change": ("file_change", "文件改动"),
    "git": ("git", "Git 检查"),
    "memory": ("memory", "记忆"),
    "mcp": ("mcp", "MCP"),
    "notebook": ("notebook", "Notebook"),
    "search": ("search", "搜索"),
    "subtask": ("subtask", "子任务"),
    "tool": ("tool", "工具"),
    "verification": ("verification", "验证"),
    "web": ("web", "网页读取"),
}


def _tool_phase_metadata(tool_category: str) -> dict[str, str]:
    phase_id, phase_label = _TOOL_PHASES.get(tool_category, _TOOL_PHASES["tool"])
    return {"toolPhaseId": phase_id, "toolPhaseLabel": phase_label}


def _tool_semantic_parent_metadata(tool_phase_metadata: dict[str, str], tool_batch_metadata: dict[str, Any] | None = None) -> dict[str, str]:
    fallback_phase_id, fallback_phase_label = _TOOL_PHASES["tool"]
    phase_id = str(tool_phase_metadata.get("toolPhaseId") or fallback_phase_id).strip() or fallback_phase_id
    phase_label = str(tool_phase_metadata.get("toolPhaseLabel") or fallback_phase_label).strip() or fallback_phase_label
    tool_group_id = ""
    if isinstance(tool_batch_metadata, dict):
        tool_group_id = str(tool_batch_metadata.get("toolGroupId") or "").strip()
    semantic_parent_id = f"group:{tool_group_id}:phase:{phase_id}" if tool_group_id else f"phase:{phase_id}"
    return {
        "toolSemanticParentId": semantic_parent_id,
        "toolSemanticParentLabel": phase_label,
    }


def _tool_batch_metadata(tool_spec: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    tool_group_id = tool_spec.get("toolGroupId")
    if isinstance(tool_group_id, str) and tool_group_id.strip():
        metadata["toolGroupId"] = tool_group_id
    for key in ("toolOperationId", "toolOperationLabel"):
        value = tool_spec.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    for key in ("toolIndex", "toolTotal"):
        value = tool_spec.get(key)
        if isinstance(value, int):
            metadata[key] = value
    return metadata


def _normalize_tool_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text.rstrip("/").lower()
    if not parts.scheme and not parts.netloc:
        return text.rstrip("/").lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _normalize_tool_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/").lower()


def _first_tool_result_path(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return _normalize_tool_path(value)
    if isinstance(value, dict):
        for key in ("path", "file", "relativePath", "relative_path"):
            path = _first_tool_result_path(value.get(key))
            if path:
                return path
        for key in ("changedPaths", "paths", "files", "items", "results", "matches", "sources", "references"):
            path = _first_tool_result_path(value.get(key))
            if path:
                return path
    if isinstance(value, list):
        for item in value:
            path = _first_tool_result_path(item)
            if path:
                return path
    return ""


def _first_tool_result_url(value: Any) -> str:
    if isinstance(value, str):
        try:
            parts = urlsplit(value.strip())
        except ValueError:
            return ""
        return _normalize_tool_url(value) if parts.scheme and parts.netloc else ""
    if isinstance(value, dict):
        for key in ("url", "uri", "href", "link"):
            url = _first_tool_result_url(value.get(key))
            if url:
                return url
        for key in ("items", "results", "matches", "links", "sources", "references"):
            url = _first_tool_result_url(value.get(key))
            if url:
                return url
    if isinstance(value, list):
        for item in value:
            url = _first_tool_result_url(item)
            if url:
                return url
    return ""


def _result_operation_metadata(tool_name: str, result: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(result, dict):
        return {}
    if not tool_name.startswith("mcp__") and tool_name in {
        "read_file",
        "run_command",
        "agent",
        "task",
        "scratchpad.read",
        "scratchpad.write",
        "memory.remember",
        "memory.recall",
        "web_fetch",
        "browser",
        "notebook",
        "computer_use",
        "list_dir",
        "list_directory",
        "search_files",
        "code_search",
        "git_status",
        "git_diff",
        "apply_patch",
        "write_file",
    }:
        return {}
    url = _first_tool_result_url(result)
    if url:
        hint = f"url:{_compact_operation_key(url, 96)}"
    else:
        path = _first_tool_result_path(result)
        hint = f"path:{_compact_operation_key(path, 96)}" if path else ""
    if not hint:
        return {}
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        server = _compact_operation_key(parts[1] if len(parts) >= 2 else "mcp")
        tool = _compact_operation_key(parts[2] if len(parts) >= 3 else tool_name)
        operation_id = f"mcp:{server}:{tool}:{hint}"
        return {"toolOperationId": operation_id, "toolOperationLabel": "MCP"}
    operation_id = f"tool:{_compact_operation_key(tool_name or 'tool')}:{hint}"
    return {"toolOperationId": operation_id}


def _tool_metadata_with_result_operation(
    tool_spec: dict[str, Any],
    result: dict[str, Any] | None,
    tool_batch_metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(tool_batch_metadata)
    if not metadata.get("toolOperationId") and not metadata.get("toolOperationLabel"):
        metadata.update(_result_operation_metadata(str(tool_spec.get("name") or ""), result))
    return metadata


class ToolExecutionMixin:
    """Mixin providing the full tool execution pipeline."""

    # -- Child worker safety guards --------------------------------------------

    def _ensure_tool_allowed_for_child_worker(self, tool_name: str) -> None:
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return
        allowed_set = set(allowed)
        if tool_name in allowed_set or (tool_name.startswith("mcp__") and "mcp__*" in allowed_set):
            return
        raise ValueError(f"Tool is not allowed in child worker process: {tool_name}")

    def _ensure_command_safe_for_child_worker(self, command: str) -> None:
        """Block git commit/push in child workers — only root may commit."""
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return  # root task, no restriction
        blocked = (
            _re.compile(r"\bgit\s+commit\b", _re.IGNORECASE),
            _re.compile(r"\bgit\s+push\b", _re.IGNORECASE),
        )
        for pattern in blocked:
            if pattern.search(command):
                raise ValueError(
                    "Child workers cannot run git commit/push directly. "
                    "Only the root task may commit changes."
                )

    # -- Budget consumption ----------------------------------------------------

    def _consume_budget_for_tool_call(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        budget: WorkerBudget | None,
        tool_name: str,
    ) -> None:
        if budget is None:
            return
        consumed = budget.consume_tool_call()
        self._publish(
            session_id=session_id,
            task=task,
            event_type="collab.worker.budget.updated",
            payload={
                "dimension": "toolCalls",
                "consumed": consumed,
                "toolName": tool_name,
                "budget": budget.to_metadata(),
            },
        )

    # -- Main execution pipeline -----------------------------------------------

    def _apply_task_worktree_to_tool_arguments(
        self,
        *,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in _WORKTREE_BOUND_TOOLS:
            return arguments
        try:
            worktree = self._store.get_worktree_by_task({"taskId": task_id}).get("worktree")
        except Exception:  # noqa: BLE001
            return arguments
        if not isinstance(worktree, dict):
            return arguments
        if worktree.get("status") not in _ACTIVE_WORKTREE_STATUSES:
            return arguments
        worktree_path = worktree.get("worktreePath")
        if not isinstance(worktree_path, str) or not worktree_path.strip():
            return arguments

        original_root = arguments.get("workspaceRoot") or arguments.get("workspace_root")
        bound = dict(arguments)
        if original_root and original_root != worktree_path:
            bound["originalWorkspaceRoot"] = original_root
        bound["workspaceRoot"] = worktree_path
        bound["activeWorktreeId"] = worktree.get("id")
        if tool_name == "run_command":
            bound.setdefault("cwd", ".")
        return bound

    def _ensure_worktree_for_write_tool(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_name: str,
        context: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if tool_name not in _LAZY_WORKTREE_BIND_TOOLS:
            return context, None
        routing = task.get("routing")
        if not isinstance(routing, dict):
            routing = {}
        if routing.get("disableWorktreeBinding") is True:
            return context, None
        if routing.get("worktreeBindingRequired") is not True:
            return context, None
        if self._store.get_worktree_by_task({"taskId": task["id"]}).get("worktree") is not None:
            return context, None
        try:
            session = self._store.require_session(session_id)
        except Exception:  # noqa: BLE001
            logger.debug("Unable to load session before lazy worktree binding", exc_info=True)
            return context, None
        routing_for_bind = {**routing, "worktreeBindingRequired": True}
        worktree = self._maybe_bind_task_worktree(
            session=session,
            task=task,
            routing=routing_for_bind,
        )
        if worktree is None:
            return context, {
                "status": "failed",
                "ok": False,
                "error": "Write-oriented tool requires an active worktree, but worktree binding failed.",
                "summary": "Write-oriented tool requires an active worktree, but worktree binding failed.",
                "failureKind": "worktree_binding_failed",
            }
        updated_routing = {**routing_for_bind, "activeWorktree": worktree}
        task["routing"] = self._store.update_task(
            task_id=task["id"],
            routing=updated_routing,
        ).get("routing") or updated_routing
        return self._context_with_worktree_binding(context or {}, worktree), None

    def _execute_tool(
        self,
        session_id: str,
        task: dict[str, Any],
        tool_spec: dict[str, Any],
        budget: WorkerBudget | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_tool_allowed_for_child_worker(tool_spec["name"])
        # Child workers cannot run git commit/push via run_command
        if tool_spec["name"] == "run_command":
            command = tool_spec.get("arguments", {}).get("command", "")
            if isinstance(command, str):
                self._ensure_command_safe_for_child_worker(command)
        tool_call_id = tool_spec.get("id") or self._store.new_id("tc")
        parent_tool_use_id = tool_spec.get("parentToolUseId")
        tool_batch_metadata = _tool_batch_metadata(tool_spec)
        tool_arguments = {
            **tool_spec["arguments"],
            "taskId": task["id"],
            "sessionId": session_id,
        }
        context, worktree_binding_failure = self._ensure_worktree_for_write_tool(
            session_id=session_id,
            task=task,
            tool_name=tool_spec["name"],
            context=context,
        )
        for key, value in tool_batch_metadata.items():
            tool_arguments.setdefault(key, value)
        if tool_spec["name"] == "run_command":
            tool_arguments.setdefault("toolUseId", tool_call_id)
            if parent_tool_use_id:
                tool_arguments.setdefault("parentToolUseId", parent_tool_use_id)
        if isinstance(context, dict):
            untrusted_signals = context.get("untrustedContentSignals")
            if isinstance(untrusted_signals, list) and untrusted_signals:
                tool_arguments["untrustedContentSignals"] = [dict(item) for item in untrusted_signals if isinstance(item, dict)]
        tool_arguments = self._apply_task_worktree_to_tool_arguments(
            task_id=task["id"],
            tool_name=tool_spec["name"],
            arguments=tool_arguments,
        )
        if tool_spec["name"] == "agent":
            tool_arguments = normalize_agent_tool_params(tool_arguments)
            tool_spec = {
                **tool_spec,
                "arguments": {
                    **tool_spec.get("arguments", {}),
                    **tool_arguments,
                },
            }
        if tool_spec["name"] in SUBAGENT_TOOL_NAMES:
            tool_arguments = _normalize_subagent_budget(tool_arguments)
            tool_spec = {
                **tool_spec,
                "arguments": {
                    **tool_spec.get("arguments", {}),
                    **tool_arguments,
                },
            }
        self._consume_budget_for_tool_call(
            session_id=session_id,
            task=task,
            budget=budget,
            tool_name=tool_spec["name"],
        )
        tool_target = _tool_target(tool_spec["name"], tool_arguments)
        tool_input_summary = _tool_input_summary(tool_spec["name"], tool_arguments, tool_target)
        tool_category = _tool_category(tool_spec["name"], tool_arguments)
        tool_phase_metadata = _tool_phase_metadata(tool_category)
        tool_semantic_parent_metadata = _tool_semantic_parent_metadata(tool_phase_metadata, tool_batch_metadata)
        if tool_spec["name"] == "run_command":
            tool_arguments.setdefault("target", tool_target)
            tool_arguments.setdefault("inputSummary", tool_input_summary)
            tool_arguments.setdefault("toolCategory", tool_category)
            for key, value in {**tool_phase_metadata, **tool_semantic_parent_metadata}.items():
                tool_arguments.setdefault(key, value)
        approval_required = False
        if tool_spec["name"] in {"apply_patch", "write_file"}:
            try:
                config = self._store.get_config({})["config"]
                policy = config.get("policy", {}) if isinstance(config, dict) else {}
                approval_mode = policy.get("approvalMode") if isinstance(policy, dict) else None
                approval_required = _write_tool_requires_approval_from_mode(tool_spec["name"], approval_mode)
            except Exception:  # noqa: BLE001
                approval_required = True
        tool_started_at = time.perf_counter()
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": tool_call_id,
                **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                **tool_batch_metadata,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
                "target": tool_target,
                "inputSummary": tool_input_summary,
                "toolCategory": tool_category,
                **tool_phase_metadata,
                **tool_semantic_parent_metadata,
            },
        )
        runtime_progress_message = _tool_runtime_progress_message(
            tool_spec["name"],
            tool_arguments,
            tool_target,
            approval_required=approval_required,
        )
        if runtime_progress_message:
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.progress",
                payload={
                    "toolCallId": tool_call_id,
                    "toolUseId": tool_call_id,
                    **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                    **tool_batch_metadata,
                    "toolName": tool_spec["name"],
                    "target": tool_target,
                    "inputSummary": tool_input_summary,
                    "toolCategory": tool_category,
                    **tool_phase_metadata,
                    **tool_semantic_parent_metadata,
                    "message": f"{runtime_progress_message}\n",
                    "outputStream": "activity",
                },
            )
        self._fire_hooks("before_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"]})
        if tool_spec["name"] == "apply_patch":
            self._fire_hooks("before_patch_apply", session_id, task, extra_context={"toolCallId": tool_call_id, "patchArguments": tool_spec.get("arguments", {})})
        # MCP-specific lifecycle event
        is_mcp_tool = tool_spec["name"].startswith("mcp__")
        if is_mcp_tool:
            parts = tool_spec["name"].split("__", 2)
            mcp_server_id = parts[1] if len(parts) >= 2 else ""
            self._publish_mcp_event("mcp.tool.started", {
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "serverId": mcp_server_id,
            })
        tool_span = self._tracer.start_span(
            "tool_call",
            trace_id=getattr(self, "_active_trace_id", None),
            parent_span_id=getattr(self, "_active_parent_span_id", None),
            attributes={"toolName": tool_spec["name"]},
        )
        streamed_command_output: dict[str, bool] = {"stdout": False, "stderr": False}
        command_log_id_holder: dict[str, str] = {"value": ""}

        def command_metadata_payload(command_log: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "commandId": command_log_id_holder["value"] or str((command_log or {}).get("id") or ""),
                "toolUseId": tool_call_id,
                "toolName": tool_spec["name"],
                **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                **tool_batch_metadata,
                "toolCategory": tool_category,
                **tool_phase_metadata,
                **tool_semantic_parent_metadata,
                "target": tool_target,
                "inputSummary": tool_input_summary,
            }

        def publish_command_started(command_log: dict[str, Any]) -> None:
            if tool_spec["name"] != "run_command":
                return
            self._publish(
                session_id=session_id,
                task=task,
                event_type="command.started",
                payload={
                    **command_metadata_payload(command_log),
                    "command": command_log.get("command") or tool_arguments.get("command"),
                    "cwd": command_log.get("cwd") or tool_arguments.get("cwd"),
                    "shell": tool_arguments.get("shell"),
                    "status": "running",
                    "background": False,
                },
            )

        def publish_command_output_chunk(stream_name: str, chunk: str) -> None:
            if tool_spec["name"] != "run_command" or not chunk:
                return
            streamed_command_output[stream_name] = True
            self._publish(
                session_id=session_id,
                task=task,
                event_type="command.output",
                payload={
                    **command_metadata_payload(),
                    "stream": stream_name,
                    "chunk": chunk,
                },
            )

        try:
            if worktree_binding_failure is not None:
                result = worktree_binding_failure
            elif tool_spec["name"] in SUBAGENT_TOOL_NAMES:
                self._fire_hooks("before_subagent_start", session_id, task, extra_context={"toolArguments": tool_arguments})
                try:
                    result = self._subagent_service.dispatch(tool_arguments)
                    if isinstance(result, dict):
                        task_steps = _task_dispatch_steps(tool_arguments, result)
                        existing_steps = result.get("steps")
                        result = {
                            **result,
                            "steps": [*task_steps, *existing_steps] if isinstance(existing_steps, list) else task_steps,
                        }
                    sub_status = result.get("status", "")
                    if sub_status == "failed":
                        self._fire_hooks("on_subagent_failed", session_id, task, extra_context={"subagentResult": result})
                    else:
                        self._fire_hooks("after_subagent_complete", session_id, task, extra_context={"subagentResult": result})
                except Exception as sub_exc:
                    self._fire_hooks("on_subagent_failed", session_id, task, extra_context={"subagentError": str(sub_exc)})
                    raise
            elif tool_spec["name"] == "run_command":
                execution_arguments = dict(tool_arguments)
                execution_arguments["_commandLogIdSink"] = command_log_id_holder
                execution_arguments["_commandStartedCallback"] = publish_command_started
                execution_arguments["_stdoutCallback"] = lambda chunk: publish_command_output_chunk("stdout", chunk)
                execution_arguments["_stderrCallback"] = lambda chunk: publish_command_output_chunk("stderr", chunk)
                result = self._tool_registry.execute(tool_spec["name"], execution_arguments, session_id=session_id)
            else:
                result = self._tool_registry.execute(tool_spec["name"], tool_arguments, session_id=session_id)
            self._tracer.end_span(tool_span.span_id, status="ok")
        except Exception as exc:  # noqa: BLE001
            import asyncio as _asyncio
            is_timeout = isinstance(exc, _asyncio.TimeoutError)
            result = {
                "status": "failed",
                "ok": False,
                "error": str(exc),
                "summary": f"Tool {tool_spec['name']} raised an exception: {exc}",
            }
            if is_timeout:
                result["timeout"] = True
            self._tracer.end_span(tool_span.span_id, status="error")
            if is_mcp_tool:
                event_name = "mcp.tool.timeout" if is_timeout else "mcp.tool.failed"
                self._publish_mcp_event(event_name, {
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "serverId": mcp_server_id,
                    "error": str(exc),
                    "timeout": is_timeout,
                    "durationMs": max(0, int((time.perf_counter() - tool_started_at) * 1000)),
                })
        tool_duration_ms = max(0, int((time.perf_counter() - tool_started_at) * 1000))
        result_preview_streamed = False
        if isinstance(result, dict):
            for activity_message in _tool_runtime_activity_messages(result):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="tool.progress",
                    payload={
                        "toolCallId": tool_call_id,
                        "toolUseId": tool_call_id,
                        **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                        **tool_batch_metadata,
                        "toolName": tool_spec["name"],
                        "target": tool_target,
                        "inputSummary": tool_input_summary,
                        "toolCategory": tool_category,
                        **tool_phase_metadata,
                        **tool_semantic_parent_metadata,
                        "message": f"{activity_message}\n",
                        "outputStream": "activity",
                    },
                )
            output_target = _tool_target(tool_spec["name"], tool_arguments, result) or tool_target
            runtime_output_message = _tool_runtime_output_message(tool_spec["name"], result, output_target)
            if runtime_output_message:
                result_preview_streamed = True
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="tool.output",
                    payload={
                        "toolCallId": tool_call_id,
                        "toolUseId": tool_call_id,
                        **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                        **tool_batch_metadata,
                        "toolName": tool_spec["name"],
                        "target": output_target,
                        "inputSummary": tool_input_summary,
                        "toolCategory": tool_category,
                        **tool_phase_metadata,
                        **tool_semantic_parent_metadata,
                        "chunk": f"{runtime_output_message}\n",
                        "outputStream": "result_preview",
                    },
                )
        def tool_event_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
            target = _tool_target(tool_spec["name"], tool_arguments, result if isinstance(result, dict) else None) or tool_target
            result_preview = _tool_result_preview(tool_spec["name"], result if isinstance(result, dict) else None, target)
            operation_metadata = _tool_metadata_with_result_operation(
                tool_spec,
                result if isinstance(result, dict) else None,
                tool_batch_metadata,
            )
            payload = {
                "toolCallId": tool_call_id,
                **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                **operation_metadata,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
                "target": target,
                "inputSummary": tool_input_summary,
                "toolCategory": tool_category,
                **tool_phase_metadata,
                **tool_semantic_parent_metadata,
                "durationMs": tool_duration_ms,
                "resultSummary": _tool_result_summary(tool_spec["name"], result if isinstance(result, dict) else None, target),
                **({"resultPreview": result_preview} if result_preview else {}),
                **({"resultPreviewStreamed": True} if result_preview_streamed else {}),
            }
            if extra:
                payload.update(extra)
            return payload

        def provider_tool_result() -> dict[str, Any]:
            target = _tool_target(tool_spec["name"], tool_arguments, result if isinstance(result, dict) else None) or tool_target
            result_summary = _tool_result_summary(tool_spec["name"], result if isinstance(result, dict) else None, target)
            result_preview = _tool_result_preview(tool_spec["name"], result if isinstance(result, dict) else None, target)
            model_visible_result = _model_visible_tool_result(
                tool_spec["name"],
                result,
                target,
                summary=result_summary,
                preview=result_preview,
            )
            operation_metadata = _tool_metadata_with_result_operation(
                tool_spec,
                result if isinstance(result, dict) else None,
                tool_batch_metadata,
            )
            return {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                **operation_metadata,
                "target": target,
                "inputSummary": tool_input_summary,
                **({"resultSummary": result_summary} if result_summary else {}),
                **({"resultPreview": result_preview} if result_preview else {}),
                "toolCategory": tool_category,
                **tool_phase_metadata,
                **tool_semantic_parent_metadata,
                "durationMs": tool_duration_ms,
                "result": result,
                **({"modelVisibleResult": model_visible_result} if model_visible_result is not result else {}),
            }

        if tool_spec["name"] == "run_command":
            command_log = result.get("commandLog") or {}
            command_id = command_log.get("id")
            if command_id and result.get("stdout") and not streamed_command_output["stdout"]:
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="command.output",
                    payload={
                        "commandId": command_id,
                        "toolUseId": tool_call_id,
                        "toolName": tool_spec["name"],
                        **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                        **tool_batch_metadata,
                        "toolCategory": tool_category,
                        **tool_phase_metadata,
                        **tool_semantic_parent_metadata,
                        "target": tool_target,
                        "inputSummary": tool_input_summary,
                        "stream": "stdout",
                        "chunk": result["stdout"],
                    },
                )
            if command_id and result.get("stderr") and not streamed_command_output["stderr"]:
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="command.output",
                    payload={
                        "commandId": command_id,
                        "toolUseId": tool_call_id,
                        "toolName": tool_spec["name"],
                        **({"parentToolUseId": parent_tool_use_id} if parent_tool_use_id else {}),
                        **tool_batch_metadata,
                        "toolCategory": tool_category,
                        **tool_phase_metadata,
                        **tool_semantic_parent_metadata,
                        "target": tool_target,
                        "inputSummary": tool_input_summary,
                        "stream": "stderr",
                        "chunk": result["stderr"],
                    },
                )
            if command_id and not result.get("background"):
                command_status = str(result.get("status") or "").strip().lower()
                command_event_type = "command.completed" if command_status == "completed" else "command.failed"
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type=command_event_type,
                    payload={
                        **command_metadata_payload(command_log if isinstance(command_log, dict) else None),
                        "command": command_log.get("command") or tool_arguments.get("command"),
                        "cwd": command_log.get("cwd") or tool_arguments.get("cwd"),
                        "shell": result.get("shell") or tool_arguments.get("shell"),
                        "status": result.get("status"),
                        "exitCode": result.get("exitCode"),
                        "durationMs": result.get("durationMs"),
                        "stdoutPath": command_log.get("stdoutPath"),
                        "stderrPath": command_log.get("stderrPath"),
                        "background": False,
                    },
                )

        if tool_spec["name"] in SUBAGENT_TOOL_NAMES:
            tool_result = provider_tool_result()
            if result.get("status") == "approval_required":
                event_type = "tool.blocked"
                extra = {"result": result, "reason": "approval_required"}
                tool_status = "blocked"
            elif result.get("status") == "blocked":
                event_type = "tool.blocked"
                extra = {"result": result, "reason": result.get("error", "Blocked by permission policy.")}
                tool_status = "blocked"
            elif self._tool_failed(tool_spec["name"], result):
                event_type = "tool.failed"
                extra = {"result": result}
                tool_status = "failed"
            else:
                event_type = "tool.completed"
                extra = {"result": result}
                tool_status = "completed"
            self._publish(
                session_id=session_id,
                task=task,
                event_type=event_type,
                payload=tool_event_payload(extra),
            )
            self._fire_hooks(
                "after_tool_call",
                session_id,
                task,
                extra_context={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "toolStatus": tool_status,
                },
            )
            return tool_result

        if result.get("status") == "blocked":
            failure = self._annotate_failed_tool_recovery(
                session_id=session_id,
                task=task,
                tool_call_id=tool_call_id,
                tool_name=tool_spec["name"],
                arguments=tool_arguments,
                result=result,
            )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.blocked",
                payload=tool_event_payload({
                    "reason": result.get("error", "Blocked by permission policy."),
                    "failureKind": failure.get("failureKind"),
                    "recoveryHint": failure.get("recoveryHint"),
                    "recoveryDecision": result.get("recoveryDecision"),
                }),
            )
            return provider_tool_result()

        if result.get("status") == "approval_required":
            approval = result.get("approval", {})
            approval_kind = str(approval.get("kind") or tool_spec["name"])
            approval_request_payload = json.loads(approval.get("requestJson", "{}"))
            approval_preview = _approval_request_preview(approval_kind, approval_request_payload)
            self._validate_task_transition(task["status"], "waiting_approval", task["id"])
            task["status"] = "waiting_approval"
            self._store.update_task(task_id=task["id"], status="waiting_approval", plan=task["plan"])
            if tool_spec["name"] == "apply_patch":
                patch = result.get("patch", {})
                changed_paths = patch.get("changedPaths") or result.get("changedPaths") or []
                diff_text = patch.get("diffText") or result.get("diffText") or ""
                files_changed = patch.get("filesChanged", result.get("filesChanged", 0))
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="patch.proposed",
                    payload={
                        "patchId": patch.get("id"),
                        "summary": patch.get("summary", ""),
                        "filesChanged": files_changed,
                        "changedPaths": changed_paths,
                        "diffText": diff_text,
                    },
                )
            else:
                changed_paths = result.get("changedPaths") or []
                diff_text = result.get("diffText") or ""
                files_changed = result.get("filesChanged")
            self._publish(
                session_id=session_id,
                task=task,
                event_type="approval.requested",
                payload={
                    "approvalId": approval.get("id"),
                    "taskId": task["id"],
                    "kind": approval_kind,
                    "request": approval_request_payload,
                    "preview": approval_preview,
                    "patchId": result.get("patch", {}).get("id"),
                    "filesChanged": files_changed,
                    "changedPaths": changed_paths,
                    "diffText": diff_text,
                },
            )
            self._fire_hooks("on_approval_required", session_id, task, extra_context={"approvalId": approval.get("id"), "kind": approval.get("kind", tool_spec["name"])})
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.waiting_approval",
                payload={
                    "status": "waiting_approval",
                    "detail": "执行前需要先审批补丁。"
                    if tool_spec["name"] == "apply_patch"
                    else "执行前需要先审批命令。",
                },
            )
            tool_result = provider_tool_result()
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.blocked",
                payload=tool_event_payload({
                    "result": result,
                    "reason": "approval_required",
                }),
            )
            return tool_result

        if self._is_patch_validation_failure(tool_spec["name"], result):
            self._annotate_failed_tool_recovery(
                session_id=session_id,
                task=task,
                tool_call_id=tool_call_id,
                tool_name=tool_spec["name"],
                arguments=tool_arguments,
                result=result,
            )
            tool_result = provider_tool_result()
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.failed",
                payload=tool_event_payload({"result": result}),
            )
            return tool_result

        if self._tool_failed(tool_spec["name"], result):
            failure = self._annotate_failed_tool_recovery(
                session_id=session_id,
                task=task,
                tool_call_id=tool_call_id,
                tool_name=tool_spec["name"],
                arguments=tool_arguments,
                result=result,
            )
            tool_result = provider_tool_result()
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.failed",
                payload=tool_event_payload({"result": result}),
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "failed"})
            if is_mcp_tool:
                is_timeout = bool(result.get("timeout"))
                event_name = "mcp.tool.timeout" if is_timeout else "mcp.tool.failed"
                self._publish_mcp_event(event_name, {
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "serverId": mcp_server_id,
                    "error": result.get("error", f"Tool returned status: {result.get('status')}"),
                    "timeout": is_timeout,
                    "durationMs": tool_duration_ms,
                    "failureKind": failure.get("failureKind"),
                    "recoveryHint": failure.get("recoveryHint"),
                    "recoveryDecision": result.get("recoveryDecision"),
                })
            return tool_result

        if tool_spec["name"] == "run_command":
            tool_result = provider_tool_result()
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload=tool_event_payload({"result": result}),
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
            return tool_result

        if tool_spec["name"] in {"apply_patch", "write_file"}:
            tool_result = provider_tool_result()
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload=tool_event_payload({"result": result}),
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
            if tool_spec["name"] == "apply_patch":
                self._fire_hooks("after_patch_apply", session_id, task, extra_context={"toolCallId": tool_call_id, "patchResult": result})
            return tool_result

        tool_result = provider_tool_result()
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.completed",
            payload=tool_event_payload({"result": result}),
        )
        if is_mcp_tool:
            self._publish_mcp_event("mcp.tool.completed", {
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "serverId": mcp_server_id,
                "ok": result.get("ok", True),
                "durationMs": tool_duration_ms,
            })
        self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
        if tool_spec["name"] == "memory.remember" and result.get("ok", True):
            self._fire_hooks("on_memory_write", session_id, task, extra_context={"toolCallId": tool_call_id, "memoryResult": result})
        return tool_result
