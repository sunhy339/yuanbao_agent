from __future__ import annotations

import codecs
import concurrent.futures
import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNSAFE_TOOL_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")
_UNSUPPORTED_REQUEST_SCHEMA_KEYS = frozenset({
    "$defs",
    "$id",
    "$schema",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "default",
    "definitions",
    "dependencies",
    "dependentSchemas",
    "else",
    "examples",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "if",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "not",
    "nullable",
    "oneOf",
    "pattern",
    "patternProperties",
    "propertyNames",
    "then",
    "title",
    "uniqueItems",
})


def _sanitize_request_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_request_schema(child)
            for key, child in value.items()
            if key not in _UNSUPPORTED_REQUEST_SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_request_schema(item) for item in value]
    return value


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
    timeout: float = 10.0
    stream_timeout: float = 180.0
    max_tool_argument_chars: int = 120_000


_shared_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Per-host connection pool for HTTP keep-alive.
# Keyed by (scheme, host, port); each entry is an open http.client connection.
_connection_pool: dict[tuple[str, str, int], http.client.HTTPConnection | http.client.HTTPSConnection] = {}
_pool_lock = threading.Lock()


def _pool_key(url: str) -> tuple[str, str, int]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    scheme = parsed.scheme
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    return (scheme, host, port)


def _make_connection(key: tuple[str, str, int], timeout: float) -> http.client.HTTPConnection:
    scheme, host, port = key
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _discard_connection(key: tuple[str, str, int]) -> None:
    """Remove and close the pooled connection for *key* if present."""
    with _pool_lock:
        old = _connection_pool.pop(key, None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


def _is_conn_clean(conn: http.client.HTTPConnection) -> bool:
    """Return True if *conn* is safe to reuse."""
    try:
        if conn.sock is None:
            return False
        # Access the internal __state via mangled name.
        state = conn._HTTPConnection__state  # type: ignore[attr-defined]
        return state == "idle"
    except Exception:
        return False


def _get_connection(url: str, timeout: float) -> http.client.HTTPConnection:
    """Return an HTTP(S) connection for *url*.

    Tries to reuse an idle pooled connection under a lock so that concurrent
    threads never share the same connection object.
    """
    key = _pool_key(url)

    with _pool_lock:
        conn = _connection_pool.pop(key, None)

    # Validate outside the lock (socket checks can block briefly).
    if conn is not None and not _is_conn_clean(conn):
        try:
            conn.close()
        except Exception:
            pass
        conn = None

    if conn is None:
        conn = _make_connection(key, timeout)

    return conn


def _return_connection(key: tuple[str, str, int], conn: http.client.HTTPConnection) -> None:
    """Return a connection to the pool if it's still clean."""
    if not _is_conn_clean(conn):
        try:
            conn.close()
        except Exception:
            pass
        return
    with _pool_lock:
        # If someone else already placed a new connection, close the old one.
        existing = _connection_pool.get(key)
        if existing is not None and existing is not conn:
            try:
                existing.close()
            except Exception:
                pass
        _connection_pool[key] = conn


def _request_via_pool(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    """Send a POST request using a pooled connection for keep-alive."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    key = _pool_key(url)
    conn = _get_connection(url, timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp_body = resp.read()
        _return_connection(key, conn)
        return status, resp_body
    except (http.client.HTTPException, OSError):
        _discard_connection(key)
        raise


def _stream_via_pool(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, Iterable[bytes]]:
    """Send a streaming POST request using a pooled connection for keep-alive."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    key = _pool_key(url)
    conn = _get_connection(url, timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
    except (http.client.HTTPException, OSError):
        _discard_connection(key)
        raise

    status = resp.status

    # Raise socket timeout for the streaming read phase — LLM may take a long
    # time between tokens.  The *timeout* param covers the connection phase only.
    # Some models (especially on complex reasoning tasks) can pause for minutes
    # between chunks, so we use a generous default of 300s (5 minutes).
    _stream_read_timeout = max(timeout * 12, 300.0)
    try:
        sock = conn.sock
        if sock is not None:
            sock.settimeout(_stream_read_timeout)
    except Exception:
        pass

    def iter_chunks() -> Iterator[bytes]:
        try:
            while True:
                try:
                    chunk = resp.readline()
                except (TimeoutError, OSError) as exc:
                    raise ProviderAdapterError(
                        f"Provider streaming read timed out: {exc}"
                    ) from exc
                if not chunk:
                    break
                yield chunk
        except ProviderAdapterError:
            raise
        finally:
            # After streaming, the connection is consumed — discard it.
            _discard_connection(key)

    return status, iter_chunks()


def _run_with_hard_timeout(fn, timeout):
    """Run *fn* in a worker thread and enforce a hard wall-clock deadline.

    On Windows, ``urllib``'s socket-level ``timeout`` only covers read/write
    phases — DNS resolution and TCP SYN retransmission can block for 20-75 s.
    This wrapper guarantees the caller never waits longer than *timeout* seconds.

    Reuses a shared ``ThreadPoolExecutor`` to avoid per-request thread creation
    overhead (~1-2 ms per ``ThreadPoolExecutor`` on CPython).
    """
    future = _shared_pool.submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise ProviderAdapterError(
            f"Provider request timed out after {timeout:g}s "
            "(DNS or TCP connection may be unreachable)"
        )


def default_http_post(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    """POST with keep-alive connection pool and hard timeout."""
    def _do_request() -> tuple[int, bytes]:
        try:
            return _request_via_pool(url=url, headers=headers, body=body, timeout=timeout)
        except (http.client.HTTPException, OSError) as exc:
            raise ProviderAdapterError(f"Provider request failed: {exc}") from exc
        except TimeoutError as exc:
            raise ProviderAdapterError(f"Provider request timed out after {timeout:g}s") from exc

    return _run_with_hard_timeout(_do_request, timeout)


def default_http_stream(
    *,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, Iterable[bytes]]:
    """Streaming POST with keep-alive connection pool and hard timeout."""
    def _do_connect() -> tuple[int, Iterable[bytes]]:
        try:
            return _stream_via_pool(url=url, headers=headers, body=body, timeout=timeout)
        except (http.client.HTTPException, OSError) as exc:
            raise ProviderAdapterError(f"Provider streaming request failed: {exc}") from exc
        except TimeoutError as exc:
            raise ProviderAdapterError(f"Provider stream timed out after {timeout:g}s") from exc

    return _run_with_hard_timeout(_do_connect, timeout)


class OpenAICompatibleChatClient:
    def __init__(self, http_post: HttpPost | None = None, http_stream: HttpStream | None = None) -> None:
        self._http_post = http_post or default_http_post
        self._http_stream = http_stream or default_http_stream

    def _prepare_tools_for_request(self, tools: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]] | None, dict[str, str]]:
        if not tools:
            return None, {}
        prepared: list[dict[str, Any]] = []
        name_map: dict[str, str] = {}
        used_names: set[str] = set()
        for index, tool in enumerate(tools, start=1):
            if not isinstance(tool, dict):
                continue
            outbound = deepcopy(tool)
            function = outbound.get("function")
            if not isinstance(function, dict):
                continue
            raw_name = function.get("name")
            if not isinstance(raw_name, str) or not raw_name:
                continue
            safe_name = self._safe_tool_name(raw_name, used_names, index)
            function["name"] = safe_name
            parameters = function.get("parameters")
            if isinstance(parameters, dict):
                function["parameters"] = _sanitize_request_schema(parameters)
            used_names.add(safe_name)
            name_map[safe_name] = raw_name
            prepared.append(outbound)
        return prepared or None, name_map

    def _safe_tool_name(self, name: str, used_names: set[str], index: int) -> str:
        if _SAFE_TOOL_NAME_RE.match(name) and name not in used_names:
            return name
        base = _UNSAFE_TOOL_NAME_CHARS_RE.sub("_", name).strip("_") or f"tool_{index}"
        base = base[:64].strip("_") or f"tool_{index}"
        candidate = base
        suffix = 2
        while candidate in used_names or not _SAFE_TOOL_NAME_RE.match(candidate):
            suffix_text = f"_{suffix}"
            candidate = f"{base[:64 - len(suffix_text)]}{suffix_text}".strip("_") or f"tool_{index}_{suffix}"
            suffix += 1
        return candidate

    def _original_tool_name(self, name: str, tool_name_map: dict[str, str] | None) -> str:
        return tool_name_map.get(name, name) if tool_name_map else name

    def chat(
        self,
        *,
        settings: OpenAICompatibleSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_tools, tool_name_map = self._prepare_tools_for_request(tools)
        payload = self._build_payload(settings=settings, messages=messages, tools=request_tools, stream=False)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, response_body = self._request(settings=settings, body=body)
        response_json = self._decode_response(response_body)
        if status >= 400:
            raise ProviderAdapterError(
                f"Provider request failed with HTTP {status}: {self._error_message(response_json)}"
            )
        if self._contains_error(response_json):
            raise ProviderAdapterError(f"Provider returned error: {self._error_message(response_json)}")
        return self._normalize_response(response_json, tool_name_map=tool_name_map)

    def stream(
        self,
        *,
        settings: OpenAICompatibleSettings,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        request_tools, tool_name_map = self._prepare_tools_for_request(tools)
        payload = self._build_payload(settings=settings, messages=messages, tools=request_tools, stream=True)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, chunks = self._stream_request(settings=settings, body=body)
        if status >= 400:
            response_body = b"".join(chunks)
            raise ProviderAdapterError(
                f"Provider request failed with HTTP {status}: {self._error_response_message(response_body)}"
            )
        yield from self._normalize_stream(chunks, settings=settings, tool_name_map=tool_name_map)

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
            "messages": self._serialize_messages_for_request(messages),
        }
        if settings.temperature is not None:
            payload["temperature"] = settings.temperature
        if settings.max_tokens is not None:
            payload["max_tokens"] = settings.max_tokens
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
        return payload

    def _serialize_messages_for_request(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "assistant":
                serialized_message = {
                    "role": "assistant",
                    "content": message.get("content") if isinstance(message.get("content"), str) else "",
                }
                tool_calls = self._serialize_tool_calls_for_request(message.get("tool_calls"))
                if tool_calls:
                    serialized_message["tool_calls"] = tool_calls
                serialized.append(serialized_message)
                continue
            if role == "tool":
                tool_message = {
                    "role": "tool",
                    "content": message.get("content") if isinstance(message.get("content"), str) else "",
                    "tool_call_id": str(message.get("tool_call_id") or ""),
                }
                serialized.append(tool_message)
                continue
            serialized.append(dict(message))
        return serialized

    def _serialize_tool_calls_for_request(self, tool_calls: Any) -> list[dict[str, Any]]:
        if not isinstance(tool_calls, list):
            return []
        serialized: list[dict[str, Any]] = []
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                raw_arguments = function.get("arguments")
            else:
                name = item.get("name")
                raw_arguments = item.get("arguments")
            if not isinstance(name, str) or not name:
                continue
            if isinstance(raw_arguments, str):
                arguments = raw_arguments
            else:
                arguments = json.dumps(raw_arguments or {}, ensure_ascii=False)
            serialized.append(
                {
                    "id": str(item.get("id") or ""),
                    "type": item.get("type") if isinstance(item.get("type"), str) else "function",
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    },
                }
            )
        return serialized

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
        from urllib.parse import urlsplit, urlunsplit

        trimmed = base_url.rstrip("/")
        if trimmed.endswith("/chat/completions"):
            return trimmed
        parsed = urlsplit(trimmed)
        if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
            return urlunsplit((parsed.scheme, parsed.netloc, "/v1/chat/completions", "", ""))
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

    def _normalize_response(self, response_json: dict[str, Any], *, tool_name_map: dict[str, str] | None = None) -> dict[str, Any]:
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
            "tool_calls": self._normalize_tool_calls(message.get("tool_calls") or [], tool_name_map=tool_name_map),
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

    def _normalize_tool_calls(self, tool_calls: Any, *, tool_name_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
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
            original_name = self._original_tool_name(name, tool_name_map)
            raw_id = item.get("id")
            tool_call_id = raw_id if isinstance(raw_id, str) and raw_id else f"call_{len(normalized)}"
            normalized.append(
                {
                    "id": tool_call_id,
                    "type": item.get("type") or "function",
                    "name": original_name,
                    "arguments": self._parse_tool_arguments(original_name, function.get("arguments")),
                }
            )
        return normalized

    def _normalize_stream(
        self,
        chunks: Iterable[bytes],
        *,
        settings: OpenAICompatibleSettings,
        tool_name_map: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        started_at = time.monotonic()
        content_parts: list[str] = []
        role = "assistant"
        tool_call_parts: dict[int, dict[str, Any]] = {}
        finish_reason: Any = None
        response_id: Any = None
        model: Any = None
        usage: Any = None

        for data in self._iter_sse_data(chunks):
            if time.monotonic() - started_at > settings.stream_timeout:
                raise ProviderAdapterError(
                    f"Provider streaming response exceeded {settings.stream_timeout:g}s before completion."
                )
            if data == "[DONE]":
                break
            chunk = self._decode_sse_json(data)
            if chunk is None:
                continue
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

            for event in self._apply_tool_call_deltas(
                delta.get("tool_calls"),
                tool_call_parts,
                tool_name_map=tool_name_map,
                max_tool_argument_chars=settings.max_tool_argument_chars,
            ):
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
            tool_name_map=tool_name_map,
        )
        yield {"type": "final", "response": response}

    def _iter_sse_data(self, chunks: Iterable[bytes]) -> Iterator[str]:
        buffer_parts: list[str] = []
        data_lines: list[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")()

        for chunk in chunks:
            try:
                buffer_parts.append(decoder.decode(chunk))
            except UnicodeDecodeError as exc:
                raise ProviderAdapterError(f"Provider returned non-UTF-8 SSE stream: {exc}") from exc

            buffer = "".join(buffer_parts)
            buffer_parts.clear()
            lines = buffer.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                buffer_parts.append(lines.pop())

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
            tail = decoder.decode(b"", final=True)
            if tail:
                buffer_parts.append(tail)
        except UnicodeDecodeError as exc:
            raise ProviderAdapterError(f"Provider returned non-UTF-8 SSE stream: {exc}") from exc
        if buffer_parts:
            buffer = "".join(buffer_parts)
            buffer_parts.clear()
            line = buffer.rstrip("\r\n")
            if line.startswith("data:"):
                value = line[5:]
                if value.startswith(" "):
                    value = value[1:]
                data_lines.append(value)
        if data_lines:
            yield "\n".join(data_lines)

    def _decode_sse_json(self, data: str) -> dict[str, Any] | None:
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            # Malformed SSE data — likely a truncated chunk from network
            # instability.  Skip it rather than killing the entire stream.
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _apply_tool_call_deltas(
        self,
        tool_calls: Any,
        tool_call_parts: dict[int, dict[str, Any]],
        *,
        tool_name_map: dict[str, str] | None = None,
        max_tool_argument_chars: int,
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
                part["name"] = self._original_tool_name(name, tool_name_map)
            arguments_delta = function.get("arguments")
            if isinstance(arguments_delta, str):
                part["arguments"] += arguments_delta
                if len(part["arguments"]) > max_tool_argument_chars:
                    raise ProviderAdapterError(
                        "Provider tool call arguments exceeded "
                        f"{max_tool_argument_chars} characters before completion."
                    )

            yield {
                "type": "tool_call_delta",
                "index": index,
                "id": tool_id if isinstance(tool_id, str) else None,
                "tool_type": tool_type if isinstance(tool_type, str) else None,
                "name": self._original_tool_name(name, tool_name_map) if isinstance(name, str) else None,
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
        tool_name_map: dict[str, str] | None = None,
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
                "tool_calls": self._normalize_tool_calls(tool_calls, tool_name_map=tool_name_map),
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
        except json.JSONDecodeError:
            # Try lightweight JSON repair for common LLM mistakes (truncated,
            # missing delimiters, trailing commas) before giving up entirely.
            repaired = self._try_repair_json(arguments)
            if repaired is not None:
                return repaired
            logger.warning(
                "Provider returned invalid tool call arguments JSON for %s, "
                "using empty dict as fallback: %.200s",
                name, arguments,
            )
            return {}
        if not isinstance(parsed, dict):
            raise ProviderAdapterError(
                f"Provider returned invalid tool call arguments for {name}: expected JSON object"
            )
        return parsed

    @staticmethod
    def _try_repair_json(raw: str) -> dict[str, Any] | None:
        """Attempt to repair common JSON mistakes from LLM tool calls."""
        import re
        s = raw.strip()
        if not s:
            return None

        # Ensure it looks like an object
        if not s.startswith("{"):
            brace = s.find("{")
            if brace >= 0:
                s = s[brace:]
        if not s.endswith("}"):
            brace = s.rfind("}")
            if brace >= 0:
                s = s[:brace + 1]
            else:
                # Likely truncated — try closing it
                s = s + "}"

        # Remove trailing commas before } or ]
        s = re.sub(r",\s*([}\]])", r"\1", s)

        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        return None
