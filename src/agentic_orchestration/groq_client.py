"""Fixed-origin Groq HTTPS transport; one request, no SDK, retries or redirects."""

from collections.abc import Callable
import http.client
import io
import json
import os
import re
from time import monotonic

from .contracts import finite_number
from .llm_planner import GenerationSettings, ModelReply, ModelRequest, ModelUsage


def _http_error_category(status):
    """Closed host classification, based solely on an actual HTTP status."""
    if type(status) is not int or not 100 <= status <= 599 or status == 200:
        raise ValueError("Invalid HTTP error status")
    if status in (401, 403):
        return "authentication_authorization"
    if status == 429:
        return "rate_limited"
    if status in (400, 404, 405, 413, 415, 422):
        return "request_rejected"
    if 500 <= status <= 599:
        return "provider_server_failure"
    return "other_http_failure"


def _http_error_code(status):
    if status in (401, 403):
        return "groq_authentication_failed"
    return "groq_rate_limited" if status == 429 else "groq_http_error"


class GroqTransportError(RuntimeError):
    """Legacy message plus optional status/category; no response body or headers.

    Non-HTTP failures retain absent metadata. These are transport assertions,
    not model instructions or proof of the provider's underlying failure cause.
    """

    def __init__(self, code, *, http_status=None):
        category = None if http_status is None else _http_error_category(http_status)
        if type(code) is not str or http_status is not None and code != _http_error_code(http_status):
            raise ValueError("Invalid Groq transport diagnostic")
        super().__init__(code)
        self.http_status = http_status
        self.error_category = category

    def http_diagnostics(self):
        """Revalidate optional exception attributes before projecting into audit."""
        status = getattr(self, "http_status", None)
        if type(status) is not int or not 100 <= status <= 599 or status == 200:
            return None, None
        category = _http_error_category(status)
        supplied = getattr(self, "error_category", None)
        if (type(supplied) is not str or supplied != category
                or self.args != (_http_error_code(status),)):
            return None, None
        return status, category


GROQ_MODEL_ID = "openai/gpt-oss-20b"


def _windows_user_key():
    """Read only the named user variable when an old VS Code process lacks it."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry:
            value, kind = winreg.QueryValueEx(registry, "GROQ_API_KEY")
        return value if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else None
    except OSError:
        return None


def _api_key():
    value = os.environ.get("GROQ_API_KEY")
    if value is None and os.name == "nt":
        value = _windows_user_key()
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise GroqTransportError("groq_api_key_missing_or_invalid")
    return value


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
            raise GroqTransportError("unsupported_socket_mode")
        # A genuine socket.makefile reference keeps the descriptor alive when
        # HTTPConnection closes a Connection: close socket before body reading.
        raw = self._sock.makefile("rb", buffering=0)
        return io.BufferedReader(_DeadlineReader(raw, self._sock, self._remaining))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GroqTransportError("duplicate_response_key")
        result[key] = value
    return result


def _constant(value):
    raise GroqTransportError("nonstandard_response_json")


class GroqModelClient:
    """One POST to Groq, with verified TLS and no proxy or endpoint overrides.

    DNS resolution/OS scheduling cannot be forcibly interrupted by this synchronous
    client. The planner/controller shared budget is the final release boundary.
    """

    def __init__(self, *, max_http_response_bytes: int = 65536,
                 clock: Callable[[], float] = monotonic):
        if type(max_http_response_bytes) is not int or max_http_response_bytes < 1:
            raise ValueError("HTTP response cap must be a positive integer")
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
                raise GroqTransportError("invalid_transport_clock")
            last = now
            if now >= deadline:
                raise TimeoutError("groq_deadline_exceeded")
            return deadline - now

        if type(request.model_id) is not str or request.model_id != GROQ_MODEL_ID:
            raise ValueError("Invalid model identifier")
        for text in (request.instructions, request.observation_json):
            if type(text) is not str or len(text.encode("utf-8")) > 16384:
                raise ValueError("Invalid model prompt data")
        # Revalidate settings; do not silently drop settings unsupported by a server.
        settings = GenerationSettings(request.settings.temperature, request.settings.max_output_tokens,
                                      request.settings.seed)
        if settings.temperature > 2:
            raise ValueError("Groq temperature must be between 0 and 2")
        if settings.max_output_tokens > 65536:
            raise ValueError("Groq completion token limit exceeds model capacity")
        parameters = {
            "model": request.model_id, "stream": False,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "low", "include_reasoning": False,
            "temperature": settings.temperature,
            "max_completion_tokens": settings.max_output_tokens,
            "messages": [{"role": "system", "content": request.instructions},
                         {"role": "user", "content": request.observation_json}],
        }
        if settings.seed is not None:
            parameters["seed"] = settings.seed
        payload = json.dumps(parameters, ensure_ascii=True, allow_nan=False).encode("utf-8")
        connection = response = None
        try:
            api_key = _api_key()
            remaining()
            connection = http.client.HTTPSConnection("api.groq.com", 443, timeout=remaining())
            connection.connect()
            remaining()
            connection.sock = _DeadlineSocket(connection.sock, remaining)
            connection.request("POST", "/openai/v1/chat/completions", body=payload, headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json", "Accept": "application/json",
                "Accept-Encoding": "identity", "Connection": "close",
            })
            remaining()
            response = connection.getresponse()
            remaining()
            status = getattr(response, "status", None)
            if type(status) is not int or not 100 <= status <= 599:
                raise GroqTransportError("invalid_http_status")
            if status != 200:
                raise GroqTransportError(_http_error_code(status), http_status=status)
            headers = {}
            for key, value in response.getheaders():
                name = key.lower()
                if name in ("content-type", "content-length", "content-encoding", "transfer-encoding"):
                    if name in headers:
                        raise GroqTransportError("ambiguous_http_headers")
                    headers[name] = value.strip()
            content_type = headers.get("content-type", "").lower()
            if not re.fullmatch(r'application/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?', content_type):
                raise GroqTransportError("unsupported_content_type")
            if headers.get("content-encoding", "identity").lower() != "identity":
                raise GroqTransportError("unsupported_content_encoding")
            transfer = headers.get("transfer-encoding", "").lower()
            if transfer not in ("", "chunked") or (transfer and "content-length" in headers):
                raise GroqTransportError("unsupported_http_framing")
            length = None
            if "content-length" in headers:
                value = headers["content-length"]
                if not re.fullmatch(r"[0-9]+", value):
                    raise GroqTransportError("invalid_content_length")
                length = int(value)
                if length > self._max_body:
                    raise GroqTransportError("http_response_oversized")
            body = bytearray()
            while True:
                remaining()
                chunk = response.read1(min(8192, self._max_body + 1 - len(body)))
                remaining()
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self._max_body:
                    raise GroqTransportError("http_response_oversized")
            if length is not None and len(body) != length:
                raise GroqTransportError("incomplete_http_body")
            try:
                value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise GroqTransportError("malformed_http_json") from None
            reply = self._reply(value, request)
            remaining()
            return reply
        except TimeoutError:
            raise TimeoutError("groq_deadline_exceeded") from None
        except GroqTransportError:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise GroqTransportError("groq_transport_failed") from None
        finally:
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    @staticmethod
    def _reply(value, request):
        allowed = {"id", "object", "created", "model", "choices", "usage",
                   "system_fingerprint", "x_groq", "service_tier", "usage_breakdown"}
        if (type(value) is not dict or set(value) - allowed
                or value.get("object") != "chat.completion"):
            raise GroqTransportError("unsupported_response_envelope")
        if type(value.get("model")) is not str or value["model"] != request.model_id:
            raise GroqTransportError("model_identity_mismatch")
        choices = value.get("choices")
        if type(choices) is not list or len(choices) != 1:
            raise GroqTransportError("unsupported_choices")
        choice = choices[0]
        if (type(choice) is not dict or set(choice) - {"index", "message", "finish_reason", "logprobs"}
                or type(choice.get("index")) is not int or choice["index"] != 0
                or choice.get("finish_reason") not in ("stop", "length", "content_filter")):
            raise GroqTransportError("unsupported_completion_status")
        message = choice.get("message")
        if (type(message) is not dict
                or set(message) - {"role", "content", "refusal", "reasoning", "tool_calls", "function_call"}
                or message.get("role") != "assistant"):
            raise GroqTransportError("unsupported_message")
        if message.get("reasoning") not in (None, ""):
            raise GroqTransportError("unexpected_reasoning")
        if message.get("tool_calls") not in (None, []) or message.get("function_call") is not None:
            raise GroqTransportError("unsupported_tool_call")
        refusal = message.get("refusal")
        if refusal is not None and type(refusal) is not str:
            raise GroqTransportError("invalid_refusal")
        refused = bool(refusal) or choice["finish_reason"] == "content_filter"
        truncated = choice["finish_reason"] == "length"
        text = message.get("content")
        if text is None and (refused or truncated):
            text = ""
        if type(text) is not str:
            raise GroqTransportError("invalid_content")
        if len(text.encode("utf-8")) > request.max_output_bytes:
            raise GroqTransportError("action_text_oversized")
        usage = None
        counts = value.get("usage")
        if counts is not None:
            if type(counts) is not dict:
                raise GroqTransportError("invalid_usage")
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if name in counts and (type(counts[name]) is not int or counts[name] < 0):
                    raise GroqTransportError("invalid_usage")
            if "prompt_tokens" in counts and "completion_tokens" in counts:
                usage = ModelUsage(counts["prompt_tokens"], counts["completion_tokens"])
                if ("total_tokens" in counts
                        and counts["total_tokens"] != usage.input_tokens + usage.output_tokens):
                    raise GroqTransportError("invalid_usage")
        # system_fingerprint identifies backend configuration, NOT model weights.
        # The API does not supply a verified model revision; do not invent one.
        return ModelReply(text, value["model"], model_revision=None, usage=usage,
                          refused=refused, truncated=truncated)
