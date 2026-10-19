"""Loopback-only Ollama transport. No SDK, retries, redirects or model management."""

from collections.abc import Callable
import http.client
import io
import ipaddress
import json
import re
from time import monotonic
from urllib.parse import urlsplit

from .contracts import finite_number
from .llm_planner import GenerationSettings, ModelReply, ModelRequest, ModelUsage


class OllamaTransportError(RuntimeError):
    """Fixed transport diagnostic; never include provider bodies or observations."""


class _DeadlineReader(io.RawIOBase):
    """Reset the timeout for EACH socket read, including HTTP header parsing."""

    def __init__(self, raw, sock, remaining):
        super().__init__()
        self._raw, self._sock, self._remaining = raw, sock, remaining

    def readable(self):
        return True

    def readinto(self, buffer):
        self._sock.settimeout(self._remaining())
        count = self._raw.readinto(buffer)
        self._remaining()
        return count

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, remaining):
        self._sock, self._remaining = sock, remaining

    def __getattr__(self, name):
        return getattr(self._sock, name)

    def sendall(self, data):
        self._sock.settimeout(self._remaining())
        self._sock.sendall(data)
        self._remaining()

    def makefile(self, mode="rb", buffering=None):
        if mode != "rb" or buffering not in (None, -1):
            raise OllamaTransportError("unsupported_socket_mode")
        # A genuine socket.makefile reference keeps the descriptor alive when
        # HTTPConnection closes a Connection: close socket before body reading.
        raw = self._sock.makefile("rb", buffering=0)
        return io.BufferedReader(_DeadlineReader(raw, self._sock, self._remaining))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OllamaTransportError("duplicate_response_key")
        result[key] = value
    return result


def _constant(value):
    raise OllamaTransportError("nonstandard_response_json")


class OllamaModelClient:
    """Exactly one synchronous POST /api/chat to a host-configured loopback IP.

    Deadline checks cover socket reads/writes, not OS scheduling or server-side
    cancellation. The outer shared budget remains the final release boundary.
    """

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", *,
                 max_http_response_bytes: int = 65536,
                 clock: Callable[[], float] = monotonic):
        if type(endpoint) is not str or any(c.isspace() or ord(c) < 32 for c in endpoint):
            raise ValueError("Invalid Ollama endpoint")
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or "?" in endpoint or "#" in endpoint
                or not parsed.hostname or "%" in parsed.hostname):
            raise ValueError("Use an HTTP loopback origin without credentials, path or query")
        # Never resolve hostnames, including localhost, through DNS.
        host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        address = ipaddress.ip_address(host)
        if not address.is_loopback:
            raise ValueError("Ollama endpoint must be a loopback IP or localhost")
        port = parsed.port if parsed.port is not None else 11434
        if not 1 <= port <= 65535:
            raise ValueError("Invalid Ollama port")
        if type(max_http_response_bytes) is not int or max_http_response_bytes < 1:
            raise ValueError("HTTP response cap must be a positive integer")
        self._host, self._port = str(address), port
        self._max_body, self._clock = max_http_response_bytes, clock

    def complete(self, request: ModelRequest) -> ModelReply:
        if type(request) is not ModelRequest or type(request.settings) is not GenerationSettings:
            raise ValueError("Expected ModelRequest with GenerationSettings")
        for value in (request.timeout_sec, request.reserved_elapsed_sec, request.deadline_elapsed_sec):
            if type(value) not in (int, float) or not finite_number(value):
                raise ValueError("Invalid model deadline")
        if (request.reserved_elapsed_sec < 0 or request.timeout_sec <= 0
                or request.timeout_sec > request.deadline_elapsed_sec - request.reserved_elapsed_sec):
            raise ValueError("Invalid remaining model budget")
        if (type(request.max_output_bytes) is not int or request.max_output_bytes < 1
                or request.attempt != 1 or request.max_attempts != 1):
            raise ValueError("Invalid model-call limits")
        start = self._clock()
        deadline = start + request.timeout_sec if finite_number(start) else None
        if not finite_number(deadline):
            raise ValueError("Invalid transport clock")
        last = start

        def remaining():
            nonlocal last
            now = self._clock()
            if not finite_number(now) or now < last:
                raise OllamaTransportError("invalid_transport_clock")
            last = now
            if now >= deadline:
                raise TimeoutError("ollama_deadline_exceeded")
            return deadline - now

        if type(request.model_id) is not str or not request.model_id.strip() or len(request.model_id) > 128:
            raise ValueError("Invalid model identifier")
        for text in (request.instructions, request.observation_json):
            if type(text) is not str or len(text.encode("utf-8")) > 16384:
                raise ValueError("Invalid model prompt data")
        # Revalidate settings; do not silently drop settings unsupported by a server.
        settings = GenerationSettings(request.settings.temperature, request.settings.max_output_tokens,
                                      request.settings.seed)
        options = {"temperature": settings.temperature, "num_predict": settings.max_output_tokens}
        if settings.seed is not None:
            options["seed"] = settings.seed
        payload = json.dumps({
            "model": request.model_id, "stream": False, "format": "json", "think": False,
            "messages": [{"role": "system", "content": request.instructions},
                         {"role": "user", "content": request.observation_json}],
            "options": options,
        }, ensure_ascii=True, allow_nan=False).encode("utf-8")
        connection = response = None
        try:
            connection = http.client.HTTPConnection(self._host, self._port, timeout=remaining())
            connection.connect()
            remaining()
            connection.sock = _DeadlineSocket(connection.sock, remaining)
            connection.request("POST", "/api/chat", body=payload, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "Accept-Encoding": "identity", "Connection": "close",
            })
            remaining()
            response = connection.getresponse()
            remaining()
            if response.status != 200:
                raise OllamaTransportError("ollama_http_error")
            headers = {}
            for key, value in response.getheaders():
                name = key.lower()
                if name in ("content-type", "content-length", "content-encoding", "transfer-encoding"):
                    if name in headers:
                        raise OllamaTransportError("ambiguous_http_headers")
                    headers[name] = value.strip()
            content_type = headers.get("content-type", "").lower()
            if not re.fullmatch(r'application/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?', content_type):
                raise OllamaTransportError("unsupported_content_type")
            if headers.get("content-encoding", "identity").lower() != "identity":
                raise OllamaTransportError("unsupported_content_encoding")
            transfer = headers.get("transfer-encoding", "").lower()
            if transfer not in ("", "chunked") or (transfer and "content-length" in headers):
                raise OllamaTransportError("unsupported_http_framing")
            length = None
            if "content-length" in headers:
                value = headers["content-length"]
                if not re.fullmatch(r"[0-9]+", value):
                    raise OllamaTransportError("invalid_content_length")
                length = int(value)
                if length > self._max_body:
                    raise OllamaTransportError("http_response_oversized")
            body = bytearray()
            while True:
                remaining()
                chunk = response.read1(min(8192, self._max_body + 1 - len(body)))
                remaining()
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self._max_body:
                    raise OllamaTransportError("http_response_oversized")
            if length is not None and len(body) != length:
                raise OllamaTransportError("incomplete_http_body")
            try:
                value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise OllamaTransportError("malformed_http_json") from None
            reply = self._reply(value, request)
            remaining()
            return reply
        except (TimeoutError, OllamaTransportError):
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise OllamaTransportError("ollama_transport_failed") from None
        finally:
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    @staticmethod
    def _reply(value, request):
        allowed = {"model", "created_at", "message", "done", "done_reason", "total_duration",
                   "load_duration", "prompt_eval_count", "prompt_eval_cached_count",
                   "prompt_eval_duration", "eval_count", "eval_duration", "logprobs"}
        if type(value) is not dict or set(value) - allowed:
            raise OllamaTransportError("unsupported_response_envelope")
        if type(value.get("model")) is not str or value["model"] != request.model_id:
            raise OllamaTransportError("model_identity_mismatch")
        if value.get("done") is not True or value.get("done_reason") not in ("stop", "length"):
            raise OllamaTransportError("unsupported_completion_status")
        message = value.get("message")
        if (type(message) is not dict or set(message) - {"role", "content", "thinking", "tool_calls", "images"}
                or message.get("role") != "assistant" or type(message.get("content")) is not str
                or ("thinking" in message and message["thinking"] != "")):
            raise OllamaTransportError("unsupported_message")
        for name in ("tool_calls", "images"):
            if message.get(name) is not None and (type(message[name]) is not list or message[name]):
                raise OllamaTransportError("unsupported_message")
        text = message["content"]
        if len(text.encode("utf-8")) > request.max_output_bytes:
            raise OllamaTransportError("action_text_oversized")
        counts = []
        for name in ("prompt_eval_count", "eval_count"):
            count = value.get(name)
            if name in value and (type(count) is not int or count < 0):
                raise OllamaTransportError("invalid_usage")
            counts.append(count)
        usage = ModelUsage(*counts) if all(count is not None for count in counts) else None
        # Native chat does not supply a verified revision or a dedicated refusal
        # flag. Natural-language refusals remain unaltered and fail action decoding.
        # Explicit unsupported refusal fields/reasons above fail closed as well.
        return ModelReply(text, value["model"], model_revision=None, usage=usage,
                          refused=False, truncated=value["done_reason"] == "length")
