from __future__ import annotations

import codecs
import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
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


class HttpStream(Protocol):
    def __call__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> tuple[int, Iterable[bytes]]: ...


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


def default_http_stream(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, Iterable[bytes]]:
    request = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    except urllib.error.HTTPError as exc:
        return exc.code, [exc.read()]
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderAdapterError(f"Provider request failed: {reason}") from exc
    except TimeoutError as exc:
        raise ProviderAdapterError(f"Provider request timed out after {timeout:g}s") from exc

    def iter_lines() -> Iterator[bytes]:
        try:
            while True:
                line = response.readline()
                if not line:
                    break
                yield line
        finally:
            response.close()

    return response.status, iter_lines()


class OpenAICompatibleChatClient:
    def __init__(self, http_post: HttpPost | None = None, http_stream: HttpStream | None = None) -> None:
        self._http_post = http_post or default_http_post
        self._http_stream = http_stream or default_http_stream

    def chat(
        self,
        *,
        settings: OpenAICompatibleSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = self._build_payload(settings=settings, messages=messages, tools=tools, stream=False)
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

    def stream(
        self,
        *,
        settings: OpenAICompatibleSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        payload = self._build_payload(settings=settings, messages=messages, tools=tools, stream=True)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, chunks = self._stream_request(settings=settings, body=body)
        if status >= 400:
            response_body = b"".join(chunks)
            raise ProviderAdapterError(
                f"Provider request failed with HTTP {status}: {self._error_response_message(response_body)}"
            )
        yield from self._normalize_stream(chunks)

    def _build_payload(
        self,
        *,
        settings: OpenAICompatibleSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
        }
        if settings.temperature is not None:
            payload["temperature"] = settings.temperature
        if settings.max_tokens is not None:
            payload["max_tokens"] = settings.max_tokens
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

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

    def _stream_request(self, *, settings: OpenAICompatibleSettings, body: bytes) -> tuple[int, Iterable[bytes]]:
        url = self._chat_completions_url(settings.base_url)
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        try:
            return self._http_stream(url=url, headers=headers, body=body, timeout=settings.timeout)
        except ProviderAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderAdapterError(f"Provider streaming request failed: {exc}") from exc

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

    def _error_response_message(self, response_body: bytes) -> str:
        try:
            return self._error_message(self._decode_response(response_body))
        except ProviderAdapterError:
            try:
                message = response_body.decode("utf-8").strip()
            except UnicodeDecodeError:
                return "unreadable provider error"
            return message or "unknown provider error"

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

    def _normalize_stream(self, chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
        content_parts: list[str] = []
        role = "assistant"
        tool_call_parts: dict[int, dict[str, Any]] = {}
        finish_reason: Any = None
        response_id: Any = None
        model: Any = None
        usage: Any = None

        for data in self._iter_sse_data(chunks):
            if data == "[DONE]":
                break
            chunk = self._decode_sse_json(data)
            if self._contains_error(chunk):
                raise ProviderAdapterError(f"Provider returned error: {self._error_message(chunk)}")
            response_id = chunk.get("id", response_id)
            model = chunk.get("model", model)
            usage = chunk.get("usage", usage)

            choices = chunk.get("choices")
            if not choices:
                continue
            if not isinstance(choices, list):
                raise ProviderAdapterError("Provider returned invalid SSE chunk: choices must be a list")

            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                raise ProviderAdapterError("Provider returned invalid SSE chunk: choice must be an object")

            delta = first_choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ProviderAdapterError("Provider returned invalid SSE chunk: delta must be an object")

            delta_role = delta.get("role")
            if isinstance(delta_role, str) and delta_role:
                role = delta_role

            content_delta = delta.get("content")
            if isinstance(content_delta, str) and content_delta:
                content_parts.append(content_delta)
                yield {"type": "content_delta", "delta": content_delta}

            for event in self._apply_tool_call_deltas(delta.get("tool_calls"), tool_call_parts):
                yield event

            if first_choice.get("finish_reason") is not None:
                finish_reason = first_choice.get("finish_reason")
                yield {"type": "finish_reason", "finish_reason": finish_reason}

        response = self._final_stream_response(
            role=role,
            content="".join(content_parts),
            tool_call_parts=tool_call_parts,
            finish_reason=finish_reason,
            response_id=response_id,
            model=model,
            usage=usage,
        )
        yield {"type": "final", "response": response}

    def _iter_sse_data(self, chunks: Iterable[bytes]) -> Iterator[str]:
        buffer = ""
        data_lines: list[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")()

        for chunk in chunks:
            try:
                buffer += decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise ProviderAdapterError(f"Provider returned non-UTF-8 SSE stream: {exc}") from exc

            lines = buffer.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                buffer = lines.pop()
            else:
                buffer = ""

            for raw_line in lines:
                line = raw_line.rstrip("\r\n")
                if line == "":
                    if data_lines:
                        yield "\n".join(data_lines)
                        data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    value = line[5:]
                    if value.startswith(" "):
                        value = value[1:]
                    data_lines.append(value)

        try:
            buffer += decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise ProviderAdapterError(f"Provider returned non-UTF-8 SSE stream: {exc}") from exc
        if buffer:
            line = buffer.rstrip("\r\n")
            if line.startswith("data:"):
                value = line[5:]
                if value.startswith(" "):
                    value = value[1:]
                data_lines.append(value)
        if data_lines:
            yield "\n".join(data_lines)

    def _decode_sse_json(self, data: str) -> dict[str, Any]:
        try:
            value = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ProviderAdapterError(f"Provider returned invalid SSE JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ProviderAdapterError("Provider returned invalid SSE JSON: expected an object")
        return value

    def _apply_tool_call_deltas(
        self,
        tool_calls: Any,
        tool_call_parts: dict[int, dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        if tool_calls is None:
            return
        if not isinstance(tool_calls, list):
            raise ProviderAdapterError("Provider returned invalid SSE chunk: tool_calls must be a list")

        for fallback_index, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                raise ProviderAdapterError("Provider returned invalid SSE chunk: tool call must be an object")
            index = item.get("index", fallback_index)
            if not isinstance(index, int):
                raise ProviderAdapterError("Provider returned invalid SSE chunk: tool call index must be an integer")

            part = tool_call_parts.setdefault(
                index,
                {"id": None, "type": "function", "name": None, "arguments": ""},
            )
            tool_id = item.get("id")
            if isinstance(tool_id, str) and tool_id:
                part["id"] = tool_id
            tool_type = item.get("type")
            if isinstance(tool_type, str) and tool_type:
                part["type"] = tool_type

            function = item.get("function") or {}
            if not isinstance(function, dict):
                raise ProviderAdapterError("Provider returned invalid SSE chunk: tool function must be an object")
            name = function.get("name")
            if isinstance(name, str) and name:
                part["name"] = name
            arguments_delta = function.get("arguments")
            if isinstance(arguments_delta, str):
                part["arguments"] += arguments_delta

            yield {
                "type": "tool_call_delta",
                "index": index,
                "id": tool_id if isinstance(tool_id, str) else None,
                "tool_type": tool_type if isinstance(tool_type, str) else None,
                "name": name if isinstance(name, str) else None,
                "arguments_delta": arguments_delta if isinstance(arguments_delta, str) else "",
            }

    def _final_stream_response(
        self,
        *,
        role: str,
        content: str,
        tool_call_parts: dict[int, dict[str, Any]],
        finish_reason: Any,
        response_id: Any,
        model: Any,
        usage: Any,
    ) -> dict[str, Any]:
        tool_calls = []
        for index in sorted(tool_call_parts):
            part = tool_call_parts[index]
            tool_calls.append(
                {
                    "id": part["id"],
                    "type": part["type"],
                    "function": {
                        "name": part["name"],
                        "arguments": part["arguments"],
                    },
                }
            )
        return {
            "message": {
                "role": role,
                "content": content,
                "tool_calls": self._normalize_tool_calls(tool_calls),
            },
            "finish_reason": finish_reason,
            "raw": {"id": response_id, "model": model, "usage": usage},
        }

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
