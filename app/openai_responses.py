"""Small, budget-aware OpenAI Responses API client.

The client intentionally exposes one operation: produce JSON that conforms to
an application-owned schema. It does not expose model tools, browsing, or code
execution. Callers must still validate the returned object with their domain
models before using it.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_OPENAI_MODEL = "gpt-4o"
MAX_RESPONSE_BYTES = 5_000_000
GPT_4O_INPUT_USD_PER_MILLION = 2.50
GPT_4O_CACHED_INPUT_USD_PER_MILLION = 1.25
GPT_4O_OUTPUT_USD_PER_MILLION = 10.00


class OpenAIUnavailable(RuntimeError):
    """The optional model layer could not return a usable result."""

    code = "openai_unavailable"


class OpenAIBudgetExceeded(OpenAIUnavailable):
    code = "openai_budget_exceeded"


class _TransientOpenAIError(OpenAIUnavailable):
    pass


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema compatible with strict structured output.

    Strict output requires every object to reject unknown keys and list all of
    its properties as required. Nullable properties remain nullable; the model
    must emit them explicitly rather than silently omitting them.
    """

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: visit(item) for key, item in value.items() if key != "default"}
        if result.get("type") == "object" or "properties" in result:
            properties = result.get("properties", {})
            result["additionalProperties"] = False
            result["required"] = list(properties)
        return result

    return visit(schema)


@dataclass(frozen=True)
class OpenAIUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: OpenAIUsage) -> OpenAIUsage:
        return OpenAIUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class ProcessBudget:
    """A process-local safety brake; provider project limits remain authoritative."""

    def __init__(self, daily_limit_usd: float, total_limit_usd: float) -> None:
        self.daily_limit_usd = daily_limit_usd
        self.total_limit_usd = total_limit_usd
        self._day = datetime.now(UTC).date()
        self._daily_spend = 0.0
        self._total_spend = 0.0
        self._reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimated_cost_usd: float) -> float:
        with self._lock:
            today = datetime.now(UTC).date()
            if today != self._day:
                self._day = today
                self._daily_spend = 0.0
            if self._daily_spend + self._reserved + estimated_cost_usd > self.daily_limit_usd:
                raise OpenAIBudgetExceeded("daily model budget would be exceeded")
            if self._total_spend + self._reserved + estimated_cost_usd > self.total_limit_usd:
                raise OpenAIBudgetExceeded("total model budget would be exceeded")
            self._reserved += estimated_cost_usd
            return estimated_cost_usd

    def settle(self, reservation: float, actual_cost_usd: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - reservation)
            self._daily_spend += actual_cost_usd
            self._total_spend += actual_cost_usd

    def release(self, reservation: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - reservation)


_default_budget_instance: ProcessBudget | None = None
_default_budget_lock = threading.Lock()


def _default_budget() -> ProcessBudget:
    global _default_budget_instance
    with _default_budget_lock:
        if _default_budget_instance is None:
            _default_budget_instance = ProcessBudget(
                daily_limit_usd=_float_env(
                    "LLM_DAILY_BUDGET_USD", 1.00, minimum=0.01, maximum=10_000
                ),
                total_limit_usd=_float_env(
                    "LLM_TOTAL_BUDGET_USD", 9.00, minimum=0.01, maximum=100_000
                ),
            )
        return _default_budget_instance


def _usage_from_response(payload: dict[str, Any]) -> OpenAIUsage:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = max(0, int(usage.get("input_tokens", 0)))
    output_tokens = max(0, int(usage.get("output_tokens", 0)))
    details = usage.get("input_tokens_details")
    cached_tokens = (
        max(0, int(details.get("cached_tokens", 0))) if isinstance(details, dict) else 0
    )
    cached_tokens = min(cached_tokens, input_tokens)
    uncached_tokens = input_tokens - cached_tokens
    cost = (
        uncached_tokens * GPT_4O_INPUT_USD_PER_MILLION
        + cached_tokens * GPT_4O_CACHED_INPUT_USD_PER_MILLION
        + output_tokens * GPT_4O_OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    return OpenAIUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )


def _output_text(payload: dict[str, Any]) -> str:
    if payload.get("status") == "incomplete":
        raise OpenAIUnavailable("OpenAI response was incomplete")
    texts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "refusal":
                raise OpenAIUnavailable("OpenAI refused the structured request")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if not texts:
        raise OpenAIUnavailable("OpenAI response contained no structured output")
    return "".join(texts)


class OpenAIResponsesClient:
    """Synchronous Responses API client for worker jobs."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        budget: ProcessBudget | None = None,
        max_input_bytes: int | None = None,
        max_retries: int | None = None,
        max_request_cost_usd: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        if self.model != "gpt-4o" and not self.model.startswith("gpt-4o-"):
            raise ValueError("OPENAI_MODEL must be gpt-4o or a pinned gpt-4o snapshot")
        configured_base_url = base_url or os.environ.get(
            "OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL
        )
        self.base_url = configured_base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("OPENAI_BASE_URL must be credential-free HTTPS")
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(120, connect=5))
        self.budget = budget or _default_budget()
        self.max_input_bytes = max_input_bytes or _int_env(
            "LLM_MAX_INPUT_BYTES", 120_000, minimum=10_000, maximum=1_000_000
        )
        self.max_retries = max_retries if max_retries is not None else _int_env(
            "LLM_MAX_RETRIES", 1, minimum=0, maximum=2
        )
        self.max_request_cost_usd = max_request_cost_usd or _float_env(
            "LLM_MAX_REQUEST_COST_USD", 0.20, minimum=0.01, maximum=100
        )
        self._usage = OpenAIUsage()
        self._usage_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def usage(self) -> OpenAIUsage:
        with self._usage_lock:
            return self._usage

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _post_once(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            with self.client.stream(
                "POST",
                f"{self.base_url}/v1/responses",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request,
            ) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise OpenAIUnavailable("OpenAI response exceeded the size limit")
                if response.status_code in {408, 409, 429} or response.status_code >= 500:
                    raise _TransientOpenAIError(
                        f"OpenAI returned transient status {response.status_code}"
                    )
                if response.status_code >= 400:
                    raise OpenAIUnavailable(f"OpenAI returned status {response.status_code}")
        except httpx.TransportError as exc:
            raise _TransientOpenAIError("OpenAI transport failed") from exc
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIUnavailable("OpenAI returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise OpenAIUnavailable("OpenAI returned an invalid response contract")
        return value

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        if not self.available:
            raise OpenAIUnavailable("OPENAI_API_KEY is not configured")
        if not 1 <= max_output_tokens <= 16_384:
            raise ValueError("max_output_tokens must fit the GPT-4o output limit")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        input_bytes = len(json.dumps(messages, separators=(",", ":")).encode())
        if input_bytes > self.max_input_bytes:
            raise OpenAIBudgetExceeded("model input exceeds the configured byte limit")
        request = {
            "model": self.model,
            "input": messages,
            "max_output_tokens": max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": strict_json_schema(schema),
                }
            },
        }
        request_bytes = len(json.dumps(request, separators=(",", ":")).encode())
        estimated_input_tokens = (request_bytes + 2) // 3
        estimated_cost = (
            estimated_input_tokens * GPT_4O_INPUT_USD_PER_MILLION
            + max_output_tokens * GPT_4O_OUTPUT_USD_PER_MILLION
        ) / 1_000_000
        if estimated_cost > self.max_request_cost_usd:
            raise OpenAIBudgetExceeded("model request exceeds the per-request cost limit")
        reservation = self.budget.reserve(estimated_cost)
        settled = False
        try:
            response: dict[str, Any] | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    response = self._post_once(request)
                    break
                except _TransientOpenAIError:
                    if attempt == self.max_retries:
                        raise
            if response is None:  # pragma: no cover - defensive loop guard
                raise OpenAIUnavailable("OpenAI returned no response")
            usage = _usage_from_response(response)
            actual_cost = usage.cost_usd if response.get("usage") else estimated_cost
            self.budget.settle(reservation, actual_cost)
            settled = True
            with self._usage_lock:
                self._usage = self._usage + OpenAIUsage(
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=actual_cost,
                )
            try:
                result = json.loads(_output_text(response))
            except json.JSONDecodeError as exc:
                raise OpenAIUnavailable("OpenAI structured output was invalid JSON") from exc
            if not isinstance(result, dict):
                raise OpenAIUnavailable("OpenAI structured output was not an object")
            return result
        finally:
            if not settled:
                self.budget.release(reservation)
