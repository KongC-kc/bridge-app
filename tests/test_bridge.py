"""Tests for ai_bridge.bridge — helper functions, conversion, endpoints, streaming."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ai_bridge.bridge import (
    DEFAULT_CONTEXT_WINDOW,
    _error_responses_resp,
    _estimate_tokens,
    _resp_base,
    _truncate_messages,
    chat_to_responses,
    responses_to_chat,
)

TEST_MODEL = "glm-5.1"


# ---------- Test helpers ----------

def _upstream_response(
    status_code: int = 200,
    content: str = "Hello!",
    tool_calls: list | None = None,
    reasoning: str | None = None,
    usage: dict | None = None,
    finish_reason: str = "stop",
) -> dict:
    """Build a fake Chat Completions API response."""
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    else:
        message["tool_calls"] = None
    if reasoning:
        message["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": TEST_MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


def _sse_chunks(
    content: str | None = "Hello!",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
) -> bytes:
    """Build a fake SSE stream (Chat Completions format) as raw bytes."""
    lines = []

    # First chunk: role
    chunk0 = {"choices": [{"delta": {"role": "assistant"}, "index": 0}]}
    lines.append(f"data: {json.dumps(chunk0)}")

    if content:
        chunk1 = {"choices": [{"delta": {"content": content}, "index": 0}]}
        lines.append(f"data: {json.dumps(chunk1)}")

    if tool_calls:
        for i, tc in enumerate(tool_calls):
            tc_delta = {
                "index": i, "id": tc["id"],
                "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]},
            }
            chunk_tc = {"choices": [{"delta": {"tool_calls": [tc_delta]}, "index": 0}]}
            lines.append(f"data: {json.dumps(chunk_tc)}")

    usage = {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55}
    chunk_final = {"choices": [{"delta": {}, "finish_reason": finish_reason, "index": 0}], "usage": usage}
    lines.append(f"data: {json.dumps(chunk_final)}")
    lines.append("data: [DONE]")
    return "\n\n".join(lines).encode("utf-8") + b"\n\n"


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    """Parse raw SSE bytes into (event_type, data_dict) pairs."""
    events = []
    ev = None
    for line in raw.decode("utf-8").split("\n"):
        line = line.strip()
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:") and ev:
            events.append((ev, json.loads(line[5:].strip())))
            ev = None
    return events


def _mock_client(post_return=None):
    """Create a mock httpx.AsyncClient for non-streaming _make_client() usage.

    The bridge does `async with _make_client() as cli:` so __aenter__ must
    return the same mock so that cli.post is the configured one.
    """
    mock_cli = AsyncMock()
    mock_cli.__aenter__.return_value = mock_cli
    mock_cli.aclose = AsyncMock()

    if post_return is not None:
        mock_cli.post = AsyncMock(return_value=post_return)

    return mock_cli


# ====================================================================
# A. Unit tests — Helper functions
# ====================================================================

class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens([]) == 0

    def test_text_only(self):
        msgs = [{"role": "user", "content": "A" * 350}]
        assert _estimate_tokens(msgs) == 100  # 350 / 3.5

    def test_tool_calls(self):
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"function": {"arguments": '{"cmd":"ls"}' * 10}}]}]
        assert _estimate_tokens(msgs) > 0

    def test_mixed(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello " * 50},
            {"role": "assistant", "content": None,
             "tool_calls": [{"function": {"arguments": '{"a": 1}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        assert _estimate_tokens(msgs) > 0


class TestTruncateMessages:
    def test_no_truncation_needed(self):
        msgs = [{"role": "system", "content": "Hello"}, {"role": "user", "content": "Hi"}]
        assert _truncate_messages(msgs, 100000) == msgs

    def test_keeps_system_messages(self):
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "A" * 50000},
            {"role": "assistant", "content": "B" * 50000},
            {"role": "user", "content": "C" * 50000},
        ]
        result = _truncate_messages(msgs, 10000)
        assert result[0]["role"] == "system"

    def test_keeps_recent_messages(self):
        msgs = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "A" * 10000},
            {"role": "assistant", "content": "B" * 10000},
            {"role": "user", "content": "final message"},
        ]
        result = _truncate_messages(msgs, 1000)
        assert result[-1]["content"] == "final message"

    def test_returns_same_object_when_under_limit(self):
        msgs = [{"role": "user", "content": "short"}]
        assert _truncate_messages(msgs, 999999) is msgs


class TestErrorResponsesResp:
    def test_format(self):
        resp = _error_responses_resp("glm-5.1", None, 502, "Upstream error")
        assert resp.status_code == 502
        body = json.loads(resp.body.decode())
        assert body["status"] == "failed"
        assert body["error"]["message"] == "Upstream error"
        assert body["output"] == []
        assert body["object"] == "response"
        assert body["usage"]["input_tokens"] == 0

    def test_includes_model_and_base_fields(self):
        resp = _error_responses_resp("my-model", {"tools": []}, 400, "bad request")
        body = json.loads(resp.body.decode())
        assert body["model"] == "my-model"
        assert body["status"] == "failed"


# ====================================================================
# B. Unit tests — Conversion functions
# ====================================================================

class TestResponsesToChat:
    def test_basic_string_input(self):
        chat = responses_to_chat({"input": "Hello", "model": "gpt-4", "stream": False}, "glm-5.1")
        assert chat["model"] == "glm-5.1"
        assert chat["messages"] == [{"role": "user", "content": "Hello"}]

    def test_instructions_become_system(self):
        chat = responses_to_chat({"input": "Hi", "instructions": "Be helpful."}, "m")
        assert chat["messages"][0] == {"role": "system", "content": "Be helpful."}
        assert chat["messages"][1] == {"role": "user", "content": "Hi"}

    def test_with_tools_filters_non_function(self):
        body = {
            "input": "Go",
            "tools": [
                {"type": "function", "function": {"name": "shell", "description": "Run", "parameters": {}}},
                {"type": "web_search"},  # should be dropped
            ],
        }
        chat = responses_to_chat(body, "m")
        assert len(chat["tools"]) == 1
        assert chat["tools"][0]["function"]["name"] == "shell"

    def test_tool_roundtrip(self):
        body = {
            "input": [
                {"type": "message", "role": "user", "content": "Read foo.py"},
                {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"cmd":"cat"}'},
                {"type": "function_call_output", "call_id": "c1", "output": "file content"},
                {"type": "message", "role": "user", "content": "Good"},
            ],
        }
        msgs = responses_to_chat(body, "m")["messages"]
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant" and msgs[1]["tool_calls"][0]["function"]["name"] == "shell"
        assert msgs[2]["role"] == "tool" and msgs[2]["content"] == "file content"
        assert msgs[3]["role"] == "user" and msgs[3]["content"] == "Good"

    def test_parallel_tool_calls(self):
        body = {
            "input": [
                {"type": "function_call", "call_id": "c1", "name": "a", "arguments": "{}"},
                {"type": "function_call", "call_id": "c2", "name": "b", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": "r1"},
                {"type": "function_call_output", "call_id": "c2", "output": "r2"},
            ],
        }
        msgs = responses_to_chat(body, "m")["messages"]
        assert len(msgs[0]["tool_calls"]) == 2  # one assistant with 2 tool_calls
        assert msgs[1]["content"] == "r1"
        assert msgs[2]["content"] == "r2"

    def test_skips_reasoning(self):
        body = {"input": [
            {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "thinking..."}]},
            {"type": "message", "role": "user", "content": "Hi"},
        ]}
        msgs = responses_to_chat(body, "m")["messages"]
        assert len(msgs) == 1 and msgs[0]["content"] == "Hi"

    def test_function_call_output_non_string(self):
        body = {"input": [
            {"type": "function_call", "call_id": "c1", "name": "view", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": [{"type": "input_image", "data": "b64"}]},
        ]}
        tool_msg = responses_to_chat(body, "m")["messages"][1]
        assert json.loads(tool_msg["content"])[0]["type"] == "input_image"


class TestChatToResponses:
    def test_text_response(self):
        resp = chat_to_responses(_upstream_response(content="Hello!"), "m")
        assert resp["status"] == "completed"
        assert resp["output_text"] == "Hello!"
        assert resp["output"][0]["type"] == "message"

    def test_tool_calls_response(self):
        upstream = _upstream_response(
            content=None,
            tool_calls=[{"id": "call_abc", "type": "function",
                         "function": {"name": "shell", "arguments": '{"cmd":"ls"}'}}],
        )
        resp = chat_to_responses(upstream, "m")
        fc = resp["output"][0]
        assert fc["type"] == "function_call"
        assert fc["name"] == "shell"
        assert fc["call_id"] == "call_abc"

    def test_reasoning_response(self):
        resp = chat_to_responses(_upstream_response(content="Done", reasoning="Let me think..."), "m")
        types = [o["type"] for o in resp["output"]]
        assert "reasoning" in types
        assert "message" in types
        assert types.index("reasoning") < types.index("message")

    def test_empty_content_no_message(self):
        resp = chat_to_responses(_upstream_response(content=""), "m")
        assert all(o["type"] != "message" for o in resp["output"])


# ====================================================================
# C. Integration tests — /v1/responses endpoint
# ====================================================================

class TestResponsesEndpoint:
    def test_success(self, client, auth_headers):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _upstream_response(content="Hi there!")

        mock_cli = _mock_client(post_return=mock_resp)
        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Hello", "stream": False},
                               headers=auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["output_text"] == "Hi there!"

    def test_upstream_error_returns_responses_format(self, client, auth_headers):
        """Upstream errors must return Responses API format, not raw Chat Completions errors."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": {"message": "Context length exceeded"}}
        mock_resp.text = '{"error":{"message":"Context length exceeded"}}'

        mock_cli = _mock_client(post_return=mock_resp)
        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Hello", "stream": False},
                               headers=auth_headers)

        body = resp.json()
        assert resp.status_code == 400
        assert body["status"] == "failed"
        assert body["object"] == "response"
        assert body["output"] == []
        assert "Context length exceeded" in body["error"]["message"]

    def test_upstream_timeout_returns_502(self, client, auth_headers):
        mock_cli = AsyncMock()
        mock_cli.__aenter__.return_value = mock_cli
        mock_cli.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        mock_cli.aclose = AsyncMock()

        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Hello", "stream": False},
                               headers=auth_headers)

        body = resp.json()
        assert resp.status_code == 502
        assert body["status"] == "failed"
        assert "timeout" in body["error"]["message"].lower() or "Timeout" in body["error"]["message"]

    def test_truncation_applied(self, client, auth_headers):
        """Large conversation is truncated before sending to upstream."""
        big_input = [{"type": "message", "role": "user", "content": "A" * 10000}]
        for i in range(40):
            big_input.append({"type": "function_call", "call_id": f"c{i}", "name": "shell", "arguments": "{}"})
            big_input.append({"type": "function_call_output", "call_id": f"c{i}", "output": "B" * 5000})
            big_input.append({"type": "message", "role": "assistant", "content": "ok"})
            big_input.append({"type": "message", "role": "user", "content": "go"})
        big_input.append({"type": "message", "role": "user", "content": "final"})

        captured = {}

        def capture_post(url, json=None, **kwargs):
            captured["msgs"] = json["messages"]
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = _upstream_response(content="Done")
            return r

        mock_cli = AsyncMock()
        mock_cli.__aenter__.return_value = mock_cli
        mock_cli.post = AsyncMock(side_effect=capture_post)
        mock_cli.aclose = AsyncMock()

        # Use a small context window to ensure truncation triggers
        with patch("ai_bridge.bridge.DEFAULT_CONTEXT_WINDOW", 1000), \
             patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": big_input, "stream": False},
                               headers=auth_headers)

        assert resp.status_code == 200
        assert len(captured["msgs"]) < len(big_input)  # truncated
        assert captured["msgs"][-1]["content"] == "final"

    def test_unauthorized(self, client):
        resp = client.post("/v1/responses",
                           json={"input": "Hello"},
                           headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_no_auth(self, client):
        resp = client.post("/v1/responses", json={"input": "Hello"})
        assert resp.status_code == 401

    def test_conversion_error_returns_502(self, client, auth_headers):
        """If chat_to_responses fails on upstream data, return 502 Responses API error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"bad": "data"}  # missing 'choices'

        mock_cli = _mock_client(post_return=mock_resp)
        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Hello", "stream": False},
                               headers=auth_headers)

        body = resp.json()
        assert resp.status_code == 502
        assert body["status"] == "failed"


# ====================================================================
# D. Integration tests — Streaming
# ====================================================================

def _make_stream_mock(sse_bytes, status_code=200, aread_return=None):
    """Build a mock client for streaming: cli.stream() returns async ctx yielding response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    if sse_bytes is not None:
        # aiter_bytes() must return an async iterable
        async def _aiter():
            yield sse_bytes
        mock_response.aiter_bytes = MagicMock(return_value=_aiter())
    if aread_return is not None:
        async def fake_aread():
            return aread_return
        mock_response.aread = fake_aread

    # stream() returns an async context manager yielding mock_response
    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = mock_response
    stream_cm.__aexit__.return_value = False

    mock_cli = AsyncMock()
    mock_cli.stream = MagicMock(return_value=stream_cm)
    mock_cli.aclose = AsyncMock()
    return mock_cli


class TestStreaming:
    def test_streaming_success(self, client, auth_headers):
        sse = _sse_chunks(content="Hello!")
        mock_cli = _make_stream_mock(sse, status_code=200)

        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Hello", "stream": True},
                               headers=auth_headers)

        events = _parse_sse(resp.content)
        types = [e[0] for e in events]

        assert resp.status_code == 200
        assert "response.created" in types
        assert "response.in_progress" in types
        assert "response.output_text.delta" in types
        assert "response.completed" in types

        completed = [e[1] for e in events if e[0] == "response.completed"][0]
        assert completed["response"]["status"] == "completed"
        assert completed["response"]["output_text"] == "Hello!"

    def test_streaming_upstream_error(self, client, auth_headers):
        """Upstream error in stream: both response.failed AND response.completed must emit."""
        mock_cli = _make_stream_mock(
            sse_bytes=None, status_code=500,
            aread_return=b'{"error": "Internal server error"}',
        )

        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Hello", "stream": True},
                               headers=auth_headers)

        events = _parse_sse(resp.content)
        types = [e[0] for e in events]

        assert "response.failed" in types, f"Missing response.failed, got: {types}"
        assert "response.completed" in types, f"Missing response.completed, got: {types}"
        assert types.index("response.failed") < types.index("response.completed")

    def test_streaming_tool_calls(self, client, auth_headers):
        sse = _sse_chunks(
            content=None,
            tool_calls=[{"id": "call_1", "function": {"name": "shell", "arguments": '{"cmd":"ls"}'}}],
            finish_reason="tool_calls",
        )
        mock_cli = _make_stream_mock(sse, status_code=200)

        with patch("ai_bridge.bridge._make_client", return_value=mock_cli):
            resp = client.post("/v1/responses",
                               json={"input": "Run ls", "stream": True},
                               headers=auth_headers)

        events = _parse_sse(resp.content)
        types = [e[0] for e in events]

        assert "response.output_item.added" in types
        assert "response.function_call_arguments.delta" in types
        assert "response.function_call_arguments.done" in types
        assert "response.output_item.done" in types
        assert "response.completed" in types

        completed = [e[1] for e in events if e[0] == "response.completed"][0]
        fc_items = [o for o in completed["response"]["output"] if o["type"] == "function_call"]
        assert len(fc_items) == 1
        assert fc_items[0]["name"] == "shell"
