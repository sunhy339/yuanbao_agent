"""ask_user_question tool - pause the ReAct loop for structured user input."""

from __future__ import annotations

from typing import Any


def _text(value: Any, *, limit: int, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    return text[:limit]


def _normalize_option(option: Any, index: int) -> dict[str, Any]:
    if isinstance(option, dict):
        label = _text(option.get("label") or option.get("title") or option.get("value"), limit=80, default=f"Option {index + 1}")
        value = _text(option.get("value") or label, limit=120, default=label)
        description = _text(option.get("description") or option.get("detail") or option.get("hint"), limit=240)
        recommended = bool(option.get("recommended") or option.get("isRecommended"))
    else:
        label = _text(option, limit=80, default=f"Option {index + 1}")
        value = label
        description = ""
        recommended = False
    result: dict[str, Any] = {
        "label": label,
        "value": value,
        "description": description,
    }
    if recommended:
        result["recommended"] = True
    return result


def _normalize_question(question: Any, index: int) -> dict[str, Any]:
    if isinstance(question, dict):
        qid = _text(question.get("id") or question.get("name"), limit=64, default=f"question_{index + 1}")
        prompt = _text(
            question.get("question") or question.get("prompt") or question.get("text"),
            limit=1000,
            default="Please provide the missing information.",
        )
        header = _text(question.get("header") or question.get("title") or qid.replace("_", " ").title(), limit=80)
        raw_options = question.get("options")
    else:
        qid = f"question_{index + 1}"
        prompt = _text(question, limit=1000, default="Please provide the missing information.")
        header = f"Question {index + 1}"
        raw_options = None
    options = [_normalize_option(option, i) for i, option in enumerate(raw_options[:5])] if isinstance(raw_options, list) else []
    normalized: dict[str, Any] = {
        "id": qid,
        "header": header,
        "question": prompt,
    }
    if options:
        normalized["options"] = options
    return normalized


def _normalize_questions(params: dict[str, Any]) -> list[dict[str, Any]]:
    raw_questions = params.get("questions")
    if isinstance(raw_questions, list) and raw_questions:
        return [_normalize_question(question, index) for index, question in enumerate(raw_questions[:3])]

    question = params.get("question") or params.get("prompt") or params.get("text")
    if question:
        legacy = {
            "id": params.get("id") or "question_1",
            "header": params.get("header") or params.get("title") or "Question",
            "question": question,
            "options": params.get("options") if isinstance(params.get("options"), list) else [],
        }
        return [_normalize_question(legacy, 0)]

    raise ValueError("ask_user_question requires questions or question")


def build_ask_user_question_tool(*_: Any, **__: Any) -> dict[str, Any]:
    """Build the structured user-question tool handler."""

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        questions = _normalize_questions(params)
        primary = questions[0]
        request_id = _text(params.get("requestId") or params.get("request_id"), limit=80, default="")
        result: dict[str, Any] = {
            "status": "waiting_user",
            "summary": _text(params.get("summary") or primary.get("question"), limit=1000),
            "reason": _text(params.get("reason"), limit=240, default="needs_user_input"),
            "resumePolicy": _text(params.get("resumePolicy") or params.get("resume_policy"), limit=120, default="requires_user_follow_up"),
            "questions": questions,
            "question": primary.get("question"),
            "options": primary.get("options") or [],
        }
        if request_id:
            result["requestId"] = request_id
        return result

    return {"handler": handler}
