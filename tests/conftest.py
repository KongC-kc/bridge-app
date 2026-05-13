"""Shared fixtures for bridge tests."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

# Ensure src is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TEST_API_KEY = "sk-test-bridge-key-1234567890"
TEST_UPSTREAM_KEY = "sk-upstream-fake-key"
TEST_UPSTREAM_BASE = "https://mock-upstream.test/v1"
TEST_MODEL = "glm-5.1"


def _mock_config():
    return {
        "port": 4000,
        "proxy_api_key": TEST_API_KEY,
        "active_account_id": "acc-test",
        "force_model": True,
        "accounts": [{
            "id": "acc-test",
            "name": "Test Account",
            "provider": "glm",
            "api_key": TEST_UPSTREAM_KEY,
            "api_base": TEST_UPSTREAM_BASE,
            "default_model": TEST_MODEL,
            "models": [TEST_MODEL],
            "enabled": True,
        }],
    }


@pytest.fixture(autouse=True)
def mock_config():
    """Override config.load() for every test."""
    with patch("ai_bridge.config.load", return_value=_mock_config()):
        yield


@pytest.fixture()
def client():
    """FastAPI test client with mocked config."""
    from ai_bridge.bridge import app
    return TestClient(app)


@pytest.fixture()
def auth_headers():
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


def make_upstream_response(
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
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": usage or {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        },
    }


def make_sse_chunks(
    content: str = "Hello!",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
) -> bytes:
    """Build a fake SSE stream (Chat Completions format) as raw bytes."""
    lines = []

    # First chunk: role
    chunk0 = {"choices": [{"delta": {"role": "assistant"}, "index": 0}]}
    lines.append(f"data: {json.dumps(chunk0)}")

    # Content chunks
    if content:
        chunk1 = {"choices": [{"delta": {"content": content}, "index": 0}]}
        lines.append(f"data: {json.dumps(chunk1)}")

    # Tool call chunks
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            tc_delta = {
                "index": i,
                "id": tc["id"],
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            chunk_tc = {"choices": [{"delta": {"tool_calls": [tc_delta]}, "index": 0}]}
            lines.append(f"data: {json.dumps(chunk_tc)}")

    # Final chunk with finish_reason + usage
    usage = {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55}
    chunk_final = {
        "choices": [{"delta": {}, "finish_reason": finish_reason, "index": 0}],
        "usage": usage,
    }
    lines.append(f"data: {json.dumps(chunk_final)}")

    lines.append("data: [DONE]")

    return "\n\n".join(lines).encode("utf-8") + b"\n\n"
