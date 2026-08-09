import json

import httpx
import pytest

from app.openai_responses import (
    OpenAIBudgetExceeded,
    OpenAIResponsesClient,
    ProcessBudget,
    strict_json_schema,
)


def _response(output: dict, *, input_tokens: int = 100, output_tokens: int = 20):
    return {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(output)}],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tokens_details": {"cached_tokens": 40},
        },
    }


def _client(handler, **kwargs):
    return OpenAIResponsesClient(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        budget=ProcessBudget(10, 10),
        max_input_bytes=10_000,
        max_retries=0,
        max_request_cost_usd=1,
        **kwargs,
    )


def test_responses_client_uses_gpt4o_strict_schema_without_storage():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_response({"answer": "ok"}))

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", "default": ""}},
    }
    client = _client(handler)

    result = client.generate_json(
        system_prompt="Return the answer.",
        user_input="test",
        schema_name="answer",
        schema=schema,
        max_output_tokens=100,
    )

    assert result == {"answer": "ok"}
    assert captured["model"] == "gpt-4o"
    assert captured["store"] is False
    assert captured["text"]["format"]["strict"] is True
    assert captured["text"]["format"]["schema"] == {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
        "required": ["answer"],
    }
    assert client.usage.input_tokens == 100
    assert client.usage.cached_input_tokens == 40
    assert client.usage.output_tokens == 20
    assert client.usage.cost_usd == pytest.approx(0.0004)


def test_input_limit_blocks_request_before_transport():
    calls = []
    client = OpenAIResponsesClient(
        api_key="test-key",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(500)
            )
        ),
        budget=ProcessBudget(10, 10),
        max_input_bytes=10,
        max_retries=0,
        max_request_cost_usd=1,
    )

    with pytest.raises(OpenAIBudgetExceeded, match="input"):
        client.generate_json(
            system_prompt="system",
            user_input="this is larger than ten bytes",
            schema_name="answer",
            schema={"type": "object", "properties": {}},
            max_output_tokens=10,
        )

    assert calls == []


def test_strict_schema_recurses_through_definitions():
    schema = {
        "$defs": {
            "item": {
                "type": "object",
                "properties": {"value": {"type": "string", "default": "x"}},
            }
        },
        "type": "object",
        "properties": {"item": {"$ref": "#/$defs/item"}},
    }

    strict = strict_json_schema(schema)

    assert strict["required"] == ["item"]
    assert strict["$defs"]["item"]["required"] == ["value"]
    assert "default" not in strict["$defs"]["item"]["properties"]["value"]
