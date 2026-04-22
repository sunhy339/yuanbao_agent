from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class ProviderAdapterError(RuntimeError):
    """Readable provider failure surfaced to the orchestrator/UI boundary."""


class HttpPost(Protocol):
    def __call__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> tuple[int, bytes]: ...


@dataclass(slots=True)
class OpenAICompatibleSettings:
    base_url: str
    api_key: str
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float = 30.0


def default_http_post(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderAdapterError(f"Provider request failed: {reason}") from exc
    except TimeoutError as exc:
        raise ProviderAdapterError(f"Provider request timed out after {timeout:g}s") from exc


class OpenAICompatibleChatClient:
    def __init__(self, http_post: HttpPost | None = None) -> None:
        self._http_post = http_post or default_http_post

    def chat(
        self,
        *,
        settings: OpenAICompatibleSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
        }
        if settings.temperature is not None:
            payload["temperature"] = settings.temperature
        if settings.max_tokens is not None:
            payload["max_tokens"] = settings.max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, response_body = self._request(settings=settings, body=body)
        response_json = self._decode_response(response_body)
        if status >= 400:
            raise ProviderAdapterError(
                f"Provider request failed with HTTP {status}: {self._error_message(response_json)}"
            )
        if self._contains_error(response_json):
            raise ProviderAdapterError(f"Provider returned error: {self._error_message(response_json)}")
        return self._normalize_response(response_json)

    def _request(self, *, settings: OpenAICompatibleSettings, body: bytes) -> tuple[int, bytes]:
        url = self._chat_completions_url(settings.base_url)
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            return self._http_post(url=url, headers=headers, body=body, timeout=settings.timeout)
        except ProviderAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderAdapterError(f"Provider request failed: {exc}") from exc

    def _chat_completions_url(self, base_url: str) -> str:
        trimmed = base_url.rstrip("/")
        if trimmed.endswith("/chat/completions"):
            return trimmed
        return f"{trimmed}/chat/completions"

    def _decode_response(self, response_body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(response_body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ProviderAdapterError(f"Provider returned non-UTF-8 response: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderAdapterError(f"Provider returned invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ProviderAdapterError("Provider returned invalid JSON: expected an object")
        return value

    def _contains_error(self, response_json: dict[str, Any]) -> bool:
        return "error" in response_json and response_json["error"] not in (None, "")

    def _error_message(self, response_json: dict[str, Any]) -> str:
        error = response_json.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error
            return str(message)
        if error:
            return str(error)
        return "unknown provider error"

    def _normalize_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderAdapterError("Provider returned invalid response: missing choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ProviderAdapterError("Provider returned invalid response: choice must be an object")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ProviderAdapterError("Provider returned invalid response: missing assistant message")

        content = message.get("content")
        normalized_message = {
            "role": message.get("role") or "assistant",
            "content": content if isinstance(content, str) else "",
            "tool_calls": self._normalize_tool_calls(message.get("tool_calls") or []),
        }
        return {
            "message": normalized_message,
            "finish_reason": first_choice.get("finish_reason"),
            "raw": {
                "id": response_json.get("id"),
                "model": response_json.get("model"),
                "usage": response_json.get("usage"),
            },
        }

    def _normalize_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        if not isinstance(tool_calls, list):
            raise ProviderAdapterError("Provider returned invalid response: tool_calls must be a list")

        normalized: list[dict[str, Any]] = []
        for item in tool_calls:
            if not isinstance(item, dict):
                raise ProviderAdapterError("Provider returned invalid response: tool call must be an object")
            function = item.get("function") or {}
            if not isinstance(function, dict):
                raise ProviderAdapterError("Provider returned invalid response: tool function must be an object")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ProviderAdapterError("Provider returned invalid response: tool call is missing function name")
            normalized.append(
                {
                    "id": item.get("id"),
                    "type": item.get("type") or "function",
                    "name": name,
                    "arguments": self._parse_tool_arguments(name, function.get("arguments")),
                }
            )
        return normalized

    def _parse_tool_arguments(self, name: str, arguments: Any) -> dict[str, Any]:
        if arguments in (None, ""):
            return {}
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str):
            raise ProviderAdapterError(
                f"Provider returned invalid tool call arguments for {name}: expected JSON object string"
            )
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ProviderAdapterError(
                f"Provider returned invalid tool call arguments JSON for {name}: {exc.msg}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderAdapterError(
                f"Provider returned invalid tool call arguments for {name}: expected JSON object"
            )
        return parsed
