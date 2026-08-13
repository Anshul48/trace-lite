"""LLM provider adapters."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import logging
import re
from typing import Protocol, Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field


class OllamaAvailabilityError(RuntimeError):
    """A user-actionable failure discovered before calling LiteLLM."""


class OllamaServiceUnavailableError(OllamaAvailabilityError):
    """Ollama could not be reached at its configured endpoint."""


class OllamaModelNotFoundError(OllamaAvailabilityError):
    """The configured Ollama model tag is not installed locally."""


class LLMPreflightError(RuntimeError):
    """A provider rejected the minimal preflight request without leaking it."""


class UnsupportedModelError(LLMPreflightError):
    """LiteLLM could not route the selected model or the provider rejected it."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "Unsupported model. Choose a provider-qualified model such as 'openai/<model>'."
        )


class RejectedCredentialError(LLMPreflightError):
    """The provider rejected the supplied credential."""

    def __init__(self) -> None:
        super().__init__("Provider rejected the credential. Check the API key and try again.")


class EndpointUnavailableError(LLMPreflightError):
    """The configured endpoint could not be reached."""

    def __init__(self) -> None:
        super().__init__(
            "Provider endpoint is unavailable. Check the API Base URL and network connection."
        )


class TemporaryProviderError(LLMPreflightError):
    """The provider is rate-limited or temporarily unavailable."""

    def __init__(self) -> None:
        super().__init__(
            "Provider is rate-limited or temporarily unavailable. Wait and try again."
        )


class EmptyResponseError(LLMPreflightError):
    """The provider completed the request without returning usable text."""

    def __init__(self, *, finish_reason: str | None = None) -> None:
        # Keep the adapter's public completion contract unchanged while making
        # the provider's safe, non-content diagnostic available to callers
        # that can make a retry decision from it.
        self.finish_reason = finish_reason
        suffix = ""
        if finish_reason:
            suffix = f" (finish_reason={finish_reason!r})"
        super().__init__(
            "Provider returned no visible response"
            f"{suffix}. Verify the selected model and output mode, then try again."
        )


class UnsafeCompletionError(LLMPreflightError):
    """The visible completion contains reasoning/control text, not an answer."""

    def __init__(self) -> None:
        super().__init__(
            "Provider returned reasoning or control text instead of visible answer content."
        )


class ProviderResponseError(LLMPreflightError):
    """A provider returned an error that does not fit a narrower category."""

    def __init__(self) -> None:
        super().__init__(
            "Provider verification failed. Check the selected model, endpoint, and API key."
        )


# Descriptive aliases keep integrations free to use either the category name or
# the failure-oriented name without exposing LiteLLM's exception hierarchy.
AuthenticationRejectedError = RejectedCredentialError
CredentialRejectedError = RejectedCredentialError
ProviderUnavailableError = EndpointUnavailableError
ProviderEndpointUnavailableError = EndpointUnavailableError
RateLimitedProviderError = TemporaryProviderError
RateLimitedOrTemporaryError = TemporaryProviderError
EmptyProviderResponseError = EmptyResponseError


class NormalizedCompletionEnvelope(BaseModel):
    """Provider-neutral completion fields at the LLM trust boundary.

    ``visible_content`` is the only field allowed to leave the adapter through
    normal ``complete`` calls.  ``reasoning_content`` exists solely so the
    preflight path can verify that a provider/model route responded at all.
    """

    visible_content: str = ""
    reasoning_content: str = ""
    finish_reason: str | None = None
    choice_count: int = Field(default=0, ge=0)

    def __init__(self, **data: Any) -> None:
        # Accept the provider-neutral ``content`` spelling when integrations
        # construct an envelope directly, while keeping the explicit
        # visible/reasoning names in the serialized model.
        if "visible_content" not in data and "content" in data:
            data["visible_content"] = data["content"]
        if "reasoning_content" not in data and "reasoning" in data:
            data["reasoning_content"] = data["reasoning"]
        super().__init__(**data)

    @property
    def content(self) -> str:
        """Compatibility alias for integrations that call it ``content``."""
        return self.visible_content

    @property
    def reasoning(self) -> str:
        return self.reasoning_content


# Short aliases are useful to integrations that use either naming convention.
CompletionEnvelope = NormalizedCompletionEnvelope
NormalizedCompletion = NormalizedCompletionEnvelope


class _LiteLLMDebugHandler(logging.Handler):
    """Keep a bounded copy of LiteLLM diagnostics for the interactive chat."""

    def __init__(self) -> None:
        # UI mode enables debug-level provider logging.  Capture every emitted
        # record, but redact source-bearing request/response bodies before it
        # can reach a terminal or retained diagnostic snapshot.
        super().__init__(level=logging.NOTSET)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        if message:
            self.messages.append(_redact_diagnostic(message)[-1000:])


_CURL_PAYLOAD_FLAG_RE = re.compile(
    r"""(?is)
    (?P<flag>--(?:data(?:-(?:raw|binary|ascii|urlencode))?|json)|-d)
    (?P<separator>\s+|=)
    (?P<value>"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|\S+)
    """
)
_PAYLOAD_FIELD_PREFIX_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        (?:
            [\"']?(?:request|response|payload|messages|prompt|input|output|content|reasoning[_ -]?content)[\"']?
            |
            (?:request|response)\s+(?:body|headers|data|payload|messages|content)
        )
        \s*(?::|=|\b(?:contains|is|was)\b\s+)
    )
    """
)


def _payload_value_end(value: str, start: int) -> int:
    """Return the end of one quoted or structured payload value.

    Provider diagnostics commonly include nested JSON/Python values and may
    span several lines.  A line-only regular expression can redact the first
    line while exposing the remaining prompt text, so balance brackets and
    quotes before replacing the value.
    """
    length = len(value)
    index = start
    while index < length and value[index].isspace():
        index += 1
    if index >= length:
        return index

    opener = value[index]
    if opener in {"'", '"'}:
        quote = opener
        index += 1
        while index < length:
            if value[index] == "\\":
                index += 2
                continue
            if value[index] == quote:
                return index + 1
            index += 1
        return length

    if opener not in "[{(":
        line_end = re.search(r"[\r\n]", value[index:])
        return index + line_end.start() if line_end else length

    matching = {"{": "}", "[": "]", "(": ")"}
    stack = [matching[opener]]
    index += 1
    quote: str | None = None
    while index < length and stack:
        character = value[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character in matching:
            stack.append(matching[character])
        elif character == stack[-1]:
            stack.pop()
        index += 1
    return index


def _redact_payload_fields(value: str) -> str:
    """Redact structured request/response fields without leaking nested text."""
    result: list[str] = []
    position = 0
    while match := _PAYLOAD_FIELD_PREFIX_RE.search(value, position):
        result.append(value[position:match.end()])
        end = _payload_value_end(value, match.end())
        result.append("[REDACTED]")
        position = end
    result.append(value[position:])
    return "".join(result)


def _redact_diagnostic(message: str) -> str:
    """Remove common secrets and request bodies from provider diagnostics."""
    value = str(message)
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    # Curl commands are especially likely to contain a complete serialized
    # prompt. Redact every data-bearing flag before handling generic records.
    if re.search(r"\bcurl(?:\.exe)?\b", value, flags=re.IGNORECASE):
        value = _CURL_PAYLOAD_FLAG_RE.sub(
            lambda match: f"{match.group('flag')}{match.group('separator')}[REDACTED]",
            value,
        )
    value = re.sub(r"(?i)(api[_ -]?key\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(authorization\s*[=:]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
    value = re.sub(r"\b(?:sk|rk|gsk|AIza)[-_A-Za-z0-9]{8,}\b", "[REDACTED]", value)
    return _redact_payload_fields(value)


def redact_diagnostic(message: str) -> str:
    """Return an operational diagnostic with secrets and payloads removed."""
    return _redact_diagnostic(message)


@contextmanager
def _quiet_litellm_diagnostics():
    """Keep LiteLLM setup diagnostics out of the terminal.

    LiteLLM can write ANSI-formatted provider lists directly to stdout/stderr
    and can emit warnings while it imports its model map. Preflight needs only
    a safe category, so discard those side channels for the duration of the
    routing and completion checks.
    """
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous_disable)


def _exception_status(error: BaseException) -> int | None:
    for attribute in ("status_code", "status", "http_status", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _exception_text(error: BaseException) -> str:
    """Return text only for internal classification; never display it."""
    try:
        return str(error).casefold()
    except Exception:
        return ""


def _classify_provider_error(error: BaseException) -> LLMPreflightError:
    """Map LiteLLM/provider failures to safe, actionable categories."""
    if isinstance(error, LLMPreflightError):
        return error
    status = _exception_status(error)
    name = type(error).__name__.casefold()
    detail = _exception_text(error)

    if status in {401, 403} or any(
        marker in name or marker in detail
        for marker in (
            "authentication",
            "unauthorized",
            "invalid api key",
            "invalid token",
            "credential",
        )
    ):
        return RejectedCredentialError()

    if status == 429 or any(
        marker in name or marker in detail
        for marker in ("ratelimit", "rate limit", "too many requests")
    ):
        return TemporaryProviderError()

    if status in {408, 502, 503, 504} or any(
        marker in name for marker in ("serviceunavailable", "temporarilyunavailable")
    ):
        return TemporaryProviderError()

    if isinstance(error, (URLError, OSError, TimeoutError, ConnectionError)) or any(
        marker in name or marker in detail
        for marker in ("apiconnection", "connection", "timeout", "network", "unreachable", "dns")
    ):
        return EndpointUnavailableError()

    if status in {400, 404} or any(
        marker in name or marker in detail
        for marker in (
            "badrequest",
            "unsupported model",
            "model not found",
            "model_not_found",
            "does not exist",
            "unknown model",
            "invalid model",
        )
    ):
        return UnsupportedModelError()

    return ProviderResponseError()


def _safe_endpoint_label(endpoint: str) -> str:
    """Keep credentials/query material out of endpoint error messages."""
    try:
        parsed = urlsplit(endpoint)
        if parsed.scheme and parsed.hostname:
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{host}{port}{parsed.path}".rstrip("/")
    except (ValueError, TypeError):
        pass
    return "[configured endpoint]"


def _response_field(value: Any, name: str) -> Any:
    """Read a LiteLLM response field from either an object or a mapping."""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _text_from_response_value(value: Any) -> str:
    """Extract visible text from string or multimodal message content."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""

    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        text = _response_field(item, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


_TRANSPORT_WRAPPER_RE = re.compile(
    r"(?:<\|(?:assistant|end|eot_id|eom_id|endoftext)\|>|"
    r"<\|start_header_id\|>assistant<\|end_header_id\|>)",
    re.IGNORECASE,
)
_REASONING_OUTPUT_RE = re.compile(
    r"<\s*/?\s*(?:think|analysis|reasoning|thought|cot)\b|"
    r"\b(?:analysis|reasoning|thought(?:\s+process)?|chain\s+of\s+thought)\s*[:\-]",
    re.IGNORECASE,
)


def _strip_transport_wrappers(value: str) -> str:
    """Strip known protocol delimiters, never reasoning tags or labels."""
    return " ".join(_TRANSPORT_WRAPPER_RE.sub("", value.replace("\ufeff", "")).split())


def _completion_envelope(response: Any) -> NormalizedCompletionEnvelope:
    """Normalize a LiteLLM response without selecting hidden reasoning text."""
    choices = _response_field(response, "choices")
    if not isinstance(choices, (list, tuple)) or not choices:
        return NormalizedCompletionEnvelope()
    choice = choices[0]
    message = _response_field(choice, "message")
    visible = ""
    reasoning = ""
    if message is not None:
        visible = _strip_transport_wrappers(
            _text_from_response_value(_response_field(message, "content"))
        )
        reasoning = _strip_transport_wrappers(
            _text_from_response_value(
            _response_field(message, "reasoning_content")
            )
        )
    if not visible:
        # A few OpenAI-compatible providers expose a legacy choice.text field.
        # It is visible completion content, not a reasoning fallback.
        visible = _strip_transport_wrappers(
            _text_from_response_value(_response_field(choice, "text"))
        )
    return NormalizedCompletionEnvelope(
        visible_content=visible,
        reasoning_content=reasoning,
        finish_reason=_response_field(choice, "finish_reason"),
        choice_count=len(choices),
    )


def _completion_text(response: Any) -> str:
    """Return visible answer content only.

    This compatibility helper intentionally no longer falls back to
    ``reasoning_content``.  Preflight uses ``_preflight_completion_text``
    below, which is the only path allowed to accept reasoning-only output.
    """
    return _completion_envelope(response).visible_content.strip()


def _preflight_completion_text(response: Any) -> str:
    """Return visible content, or reasoning-only evidence for preflight."""
    envelope = _completion_envelope(response)
    return (envelope.visible_content or envelope.reasoning_content).strip()


class LLMAdapter(Protocol):
    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        ...


class LiteLLMAdapter:
    """LiteLLM wrapper supporting Ollama, OpenAI, Anthropic, Gemini, etc."""

    _DEFAULT_OLLAMA_API_BASE = "http://localhost:11434"
    _OLLAMA_PREFLIGHT_TIMEOUT_SECONDS = 2.0
    _PREFLIGHT_MAX_TOKENS = 128

    def __init__(self, model: str = "ollama/llama3.1:8b", api_base: str | None = None, api_version: str | None = None, **kwargs):
        self.model = model
        self.api_base = api_base
        self.api_version = api_version
        self.kwargs = kwargs
        self.last_error: str | None = None
        self.last_debug: list[str] = []

    @staticmethod
    def _load_litellm():
        """Load LiteLLM without globally enabling prompt-bearing debug logs."""
        import litellm
        return litellm

    def debug_snapshot(self, error: BaseException | None = None) -> str:
        """Return a safe, bounded diagnostic block suitable for chat output."""
        lines = [f"LiteLLM model: {self.model}"]
        if error is not None:
            lines.append(f"Error: {type(error).__name__}: {_redact_diagnostic(str(error))}")
        elif self.last_error:
            lines.append(f"Error: {self.last_error}")
        if self.last_debug:
            lines.append("LiteLLM debug:")
            lines.extend(self.last_debug[-40:])
        else:
            lines.append("LiteLLM debug: no log records were captured.")
        return "\n".join(lines)[-4000:]

    @property
    def is_ollama_model(self) -> bool:
        """Whether this adapter uses Ollama's native local-model endpoint."""
        return self.model.strip().lower().startswith("ollama/")

    def preflight_ollama(self, timeout: float | None = None) -> None:
        """Verify that the selected Ollama tag is available before a build.

        LiteLLM only discovers a missing local tag after it starts a completion,
        which both obscures the configuration mistake and can print a diagnostic
        for every attempted summary. Ollama exposes its installed
        tags through a small native endpoint, so use that direct check first.
        Non-Ollama models deliberately remain untouched.
        """
        if not self.is_ollama_model:
            return

        tag = self.model.split("/", 1)[1].strip()
        if not tag:
            raise OllamaModelNotFoundError(
                "The configured Ollama model name is empty. Choose an installed "
                "model in Settings, for example 'ollama/<model>:<tag>'."
            )

        endpoint = (self.api_base or self._DEFAULT_OLLAMA_API_BASE).rstrip("/")
        safe_endpoint = _safe_endpoint_label(endpoint)
        tags_url = f"{endpoint}/api/tags"
        request = Request(tags_url, headers={"Accept": "application/json"})
        try:
            with urlopen(
                request,
                timeout=(
                    self._OLLAMA_PREFLIGHT_TIMEOUT_SECONDS
                    if timeout is None
                    else timeout
                ),
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise OllamaServiceUnavailableError(
                f"Ollama is unavailable at {safe_endpoint}. Start Ollama or update the API Base in Settings."
            ) from exc

        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise OllamaServiceUnavailableError(
                f"Ollama at {safe_endpoint} returned an invalid model list. "
                "Check that the API Base points to an Ollama server."
            )

        installed_tags = {
            str(item.get(field)).strip()
            for item in models
            if isinstance(item, dict)
            for field in ("name", "model")
            if item.get(field)
        }
        expected_tags = {tag, f"{tag}:latest" if ":" not in tag else tag}
        if not installed_tags.intersection(expected_tags):
            raise OllamaModelNotFoundError(
                f"Ollama model '{tag}' is not installed at {safe_endpoint}. "
                f"Run `ollama pull {tag}` or choose an installed model in Settings."
            )

    def validate_model_routing(self) -> None:
        """Validate LiteLLM's provider route without making a network request."""
        model = self.model.strip()
        if not model:
            raise UnsupportedModelError()

        try:
            with _quiet_litellm_diagnostics():
                litellm = self._load_litellm()
                resolver = getattr(litellm, "get_llm_provider", None)
                if not callable(resolver):
                    # Older LiteLLM versions do not expose the resolver. The
                    # provider-qualified shape still prevents the known bare
                    # model failure and completion remains the final check.
                    if "/" not in model:
                        raise UnsupportedModelError()
                    return
                route = resolver(model=model)
        except LLMPreflightError:
            raise
        except Exception as exc:
            raise _classify_provider_error(exc) from exc

        if "/" not in model or not isinstance(route, (tuple, list)) or len(route) < 2 or not route[1]:
            raise UnsupportedModelError()

    def _clear_preflight_diagnostics(self) -> None:
        """Do not retain provider/request diagnostics from setup verification."""
        self.last_error = None
        self.last_debug = []

    def preflight(self) -> None:
        """Perform a categorized, quiet routing and authentication check."""
        self.preflight_ollama()
        # A real completion catches expired/revoked credentials for every
        # provider, not only the local Ollama model registry.
        try:
            with _quiet_litellm_diagnostics():
                self.validate_model_routing()
                response = self.complete_preflight(
                    "Reply with exactly OK and no explanation.",
                    max_tokens=self._PREFLIGHT_MAX_TOKENS,
                )
        except OllamaAvailabilityError:
            raise
        except LLMPreflightError:
            raise
        except Exception as exc:
            raise _classify_provider_error(exc) from exc
        finally:
            self._clear_preflight_diagnostics()
        if not isinstance(response, str) or not response.strip():
            raise EmptyResponseError()

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        """Return visible answer content and never hidden reasoning text."""
        return self._complete(prompt, max_tokens=max_tokens, preflight=False)

    def complete_preflight(self, prompt: str, max_tokens: int = 500) -> str:
        """Run the provider-only completion check.

        Some reasoning-capable models return only ``reasoning_content`` for a
        tiny verification prompt.  That output proves the route is alive but
        must never be reused as a stored summary or title.
        """
        return self._complete(prompt, max_tokens=max_tokens, preflight=True)

    def _complete(self, prompt: str, max_tokens: int, *, preflight: bool) -> str:
        capture = _LiteLLMDebugHandler()
        capture.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        loggers: list[logging.Logger] = []
        self.last_error = None
        self.last_debug = []
        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                litellm = self._load_litellm()
            loggers = [
                logging.getLogger("LiteLLM"),
                logging.getLogger("LiteLLM Router"),
                logging.getLogger("LiteLLM Proxy"),
            ]
            for logger in loggers:
                logger.addHandler(capture)

            params: dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                **self.kwargs,
            }
            # DeepSeek V4 enables thinking by default and includes its
            # reasoning tokens in max_tokens. Short structural outputs such as
            # tree titles otherwise commonly finish with only
            # ``reasoning_content`` and no visible ``content``. Keep the
            # normal trust boundary (hidden reasoning is never returned) while
            # selecting non-thinking mode for these bounded calls. An explicit
            # ``extra_body.thinking`` setting always wins.
            if (
                not preflight
                and self.model.strip().casefold().startswith("deepseek/")
                and max_tokens <= 128
                and isinstance(params.get("extra_body"), (dict, type(None)))
            ):
                extra_body = dict(params.get("extra_body") or {})
                thinking = extra_body.get("thinking")
                if thinking is None:
                    extra_body["thinking"] = {"type": "disabled"}
                elif isinstance(thinking, dict) and "type" not in thinking:
                    extra_body["thinking"] = {**thinking, "type": "disabled"}
                params["extra_body"] = extra_body
            if self.api_base:
                params["api_base"] = self.api_base
            if self.api_version:
                params["api_version"] = self.api_version

            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                response = litellm.completion(**params)
            envelope = _completion_envelope(response)
            value = (
                (envelope.visible_content or envelope.reasoning_content).strip()
                if preflight
                else envelope.visible_content.strip()
            )
            if not value:
                raise EmptyResponseError(finish_reason=envelope.finish_reason)
            if not preflight and _REASONING_OUTPUT_RE.search(value):
                raise UnsafeCompletionError()
            return value
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {_redact_diagnostic(str(exc))}"
            raise
        finally:
            captured_messages = list(capture.messages)
            for label, stream in (("stdout", stdout_capture), ("stderr", stderr_capture)):
                captured = stream.getvalue().strip()
                if captured:
                    captured_messages.extend(
                        f"LiteLLM {label}: {redact_diagnostic(line)[-1000:]}"
                        for line in captured.splitlines()
                        if line.strip()
                    )
            self.last_debug = captured_messages[-40:]
            for logger in loggers:
                logger.removeHandler(capture)


class MockLLMAdapter:
    """Mock LLM adapter for offline unit testing."""

    def __init__(self, default_response: str = "This is a mock LLM summary of the text."):
        self.default_response = default_response
        self.last_prompt = ""

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        self.last_prompt = prompt
        # If prompt requests JSON array of scores (for LATTICE traversal)
        if "JSON array of scores" in prompt:
            # Count how many sections/items in prompt
            num_items = prompt.count("[") - 1
            if num_items <= 0:
                num_items = 3
            scores = [8, 5, 2][:num_items]
            while len(scores) < num_items:
                scores.append(1)
            return str(scores)
        # If prompt requests topic label (for Router)
        if "short 2-5 word topic label" in prompt:
            return "General Knowledge Topic"
        # Keep the offline adapter useful under the same source-overlap gate as
        # real providers.  This is still a mock response; it merely includes a
        # deterministic source term from the prompt so tests exercise the real
        # publication contract instead of bypassing it.
        if "Child passages:" in prompt:
            source = prompt.split("Child passages:", 1)[1].split("\n\nSummary:", 1)[0]
            candidates = re.findall(r"[A-Za-z][A-Za-z0-9'_-]{3,}", source)
            ignored = {
                "child", "passages", "preserve", "important", "entities",
                "facts", "relationships", "heading", "summary",
            }
            term = next(
                (candidate for candidate in candidates if candidate.casefold() not in ignored),
                "source",
            )
            return f"{self.default_response} The source discusses {term}."
        return self.default_response
