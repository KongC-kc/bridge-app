"""HTTP 服务：动态根据 config 中的 active account 路由请求。"""
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config

logger = logging.getLogger("bridge")

app = FastAPI(title="AI Bridge")


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))


def _check_auth(authorization: str | None):
    cfg = config.load()
    expected = cfg.get("proxy_api_key", "")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    if authorization[7:].strip() != expected:
        raise HTTPException(401, "Invalid API key")


def _resolve_account() -> dict:
    cfg = config.load()
    acc = config.get_active_account(cfg)
    if not acc:
        raise HTTPException(503, "No active account configured")
    if not acc.get("enabled", True):
        raise HTTPException(503, "Active account is disabled")
    if not acc.get("api_key") or not acc.get("api_base"):
        raise HTTPException(503, "Active account missing api_key or api_base")
    return acc


def _resolve_model(acc: dict, requested_model: str | None) -> str:
    cfg = config.load()
    if cfg.get("force_model", True) and acc.get("default_model"):
        return acc["default_model"]
    models = acc.get("models") or []
    if requested_model and models and requested_model in models:
        return requested_model
    return requested_model or acc.get("default_model") or ""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


# ---------- request: Responses -> Chat ----------
def responses_to_chat(body: dict, model: str) -> dict:
    chat: dict[str, Any] = {"model": model, "stream": body.get("stream", False)}
    messages: list[dict] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": body["instructions"]})

    inp = body.get("input")
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        pending: list[dict] = []

        def flush():
            if pending:
                messages.append({"role": "assistant", "content": None,
                                 "tool_calls": pending.copy()})
                pending.clear()

        for item in inp:
            if not isinstance(item, dict):
                if isinstance(item, str):
                    flush()
                    messages.append({"role": "user", "content": item})
                continue
            t = item.get("type")
            if t == "function_call":
                pending.append({
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                })
            elif t == "function_call_output":
                flush()
                output = item.get("output", "")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("id"),
                    "content": output,
                })
            elif t == "reasoning":
                continue
            else:
                flush()
                role = item.get("role", "user")
                content = item.get("content")
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict):
                            parts.append(c.get("text") or c.get("input_text")
                                         or c.get("output_text") or "")
                        elif isinstance(c, str):
                            parts.append(c)
                    content = "".join(parts)
                messages.append({"role": role, "content": content or ""})
        flush()

    chat["messages"] = messages

    if body.get("tools"):
        chat_tools = []
        for tool in body["tools"]:
            if tool.get("type") != "function":
                continue
            if "function" in tool:
                fn = dict(tool["function"])
                fn.pop("strict", None)
                chat_tools.append({"type": "function", "function": fn})
            else:
                chat_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    },
                })
        if chat_tools:
            chat["tools"] = chat_tools

    for k in ("temperature", "top_p", "tool_choice",
              "user", "stop", "presence_penalty", "frequency_penalty"):
        if k in body:
            val = body[k]
            if k == "tool_choice" and isinstance(val, dict):
                tc_type = val.get("type")
                if tc_type == "function":
                    fn_name = val.get("name", "")
                    val = {"type": "function", "function": {"name": fn_name}}
                elif tc_type in ("auto", "required", "none"):
                    val = tc_type
            chat[k] = val

    if body.get("parallel_tool_calls") is True:
        chat["parallel_tool_calls"] = True
    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]
    elif "max_tokens" in body:
        chat["max_tokens"] = body["max_tokens"]

    return chat


def _resp_base(req_body: dict | None, model: str) -> dict:
    b = req_body or {}
    return {
        "metadata": b.get("metadata", {}),
        "temperature": b.get("temperature", 1.0),
        "top_p": b.get("top_p", 1.0),
        "instructions": b.get("instructions", ""),
        "max_output_tokens": b.get("max_output_tokens"),
        "tools": b.get("tools", []),
        "tool_choice": b.get("tool_choice", "auto"),
        "parallel_tool_calls": b.get("parallel_tool_calls", True),
        "truncated": False,
        "incomplete_details": None,
        "model": model,
    }


def chat_to_responses(chat_resp: dict, model: str, req_body: dict | None = None) -> dict:
    choice = chat_resp["choices"][0]
    msg = choice.get("message", {})
    text = msg.get("content") or ""
    output: list[dict] = []

    reasoning = msg.get("reasoning_content")
    if reasoning:
        output.append({
            "type": "reasoning",
            "id": _new_id("rs"),
            "summary": [{"type": "summary_text", "text": reasoning}],
        })

    if text:
        output.append({
            "type": "message",
            "id": _new_id("msg"),
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        })

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        output.append({
            "type": "function_call",
            "id": _new_id("fc"),
            "call_id": tc.get("id") or _new_id("call"),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "") or "",
            "status": "completed",
        })

    usage = chat_resp.get("usage") or {}
    resp: dict[str, Any] = {
        "id": _new_id("resp"),
        "object": "response",
        "created_at": chat_resp.get("created", int(time.time())),
        "status": "completed",
        "output": output,
        "output_text": text,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }
    resp.update(_resp_base(req_body, model))
    return resp


async def _iter_sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    buf = b""
    async for raw_chunk in response.aiter_bytes():
        buf += raw_chunk
        while b"\n" in buf:
            line_bytes, buf = buf.split(b"\n", 1)
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
            if line:
                yield line
    if buf.strip():
        yield buf.decode("utf-8", errors="replace").rstrip("\r")


async def stream_responses(chat_body: dict, model: str, api_base: str, api_key: str,
                           req_body: dict | None = None) -> AsyncIterator[bytes]:
    response_id = _new_id("resp")
    created = int(time.time())
    base_resp = {"id": response_id, "object": "response", "created_at": created,
                 "status": "in_progress", "model": model, "output": []}
    yield _sse("response.created", {"type": "response.created", "response": base_resp})
    yield _sse("response.in_progress", {"type": "response.in_progress", "response": base_resp})

    output_index = 0
    text_msg_id: str | None = None
    text_part_added = False
    full_text = ""
    final_output: list[dict] = []
    tool_calls: dict[int, dict] = {}
    fc_added: dict[int, bool] = {}
    fc_item_ids: dict[int, str] = {}
    fc_output_indices: dict[int, int] = {}
    usage: dict = {}
    upstream_errored = False

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    cli = _make_client()

    try:
        async with cli.stream("POST", f"{api_base.rstrip('/')}/chat/completions",
                              json=chat_body, headers=headers) as r:
            if r.status_code != 200:
                err_text = await r.aread()
                yield _sse("response.failed", {
                    "type": "response.failed",
                    "response": {**base_resp, "status": "failed"},
                    "error": {"message": err_text.decode("utf-8", "ignore")},
                })
                return

            async for line in _iter_sse_lines(r):
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("SSE JSON parse error, skipping: %s", data[:200])
                    continue

                if chunk.get("usage"):
                    usage = chunk["usage"]

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                content = delta.get("content")
                if content:
                    if text_msg_id is None:
                        text_msg_id = _new_id("msg")
                        yield _sse("response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": output_index,
                            "item": {"id": text_msg_id, "type": "message",
                                     "status": "in_progress", "role": "assistant", "content": []},
                        })
                    if not text_part_added:
                        text_part_added = True
                        yield _sse("response.content_part.added", {
                            "type": "response.content_part.added",
                            "item_id": text_msg_id, "output_index": output_index,
                            "content_index": 0,
                            "part": {"type": "output_text", "text": "", "annotations": []},
                        })
                    full_text += content
                    yield _sse("response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": text_msg_id, "output_index": output_index,
                        "content_index": 0, "delta": content,
                    })

                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls:
                        tool_calls[idx] = {"id": tc_delta.get("id") or _new_id("call"),
                                           "name": "", "args": ""}
                    if tc_delta.get("id"):
                        tool_calls[idx]["id"] = tc_delta["id"]
                    fn = tc_delta.get("function") or {}
                    if fn.get("name"):
                        tool_calls[idx]["name"] += fn["name"]
                    args_delta = fn.get("arguments")

                    if not fc_added.get(idx):
                        if text_msg_id is not None and text_part_added:
                            yield _sse("response.output_text.done", {
                                "type": "response.output_text.done",
                                "item_id": text_msg_id, "output_index": output_index,
                                "content_index": 0, "text": full_text,
                            })
                            yield _sse("response.content_part.done", {
                                "type": "response.content_part.done",
                                "item_id": text_msg_id, "output_index": output_index,
                                "content_index": 0,
                                "part": {"type": "output_text", "text": full_text, "annotations": []},
                            })
                            yield _sse("response.output_item.done", {
                                "type": "response.output_item.done",
                                "output_index": output_index,
                                "item": {"id": text_msg_id, "type": "message", "status": "completed",
                                         "role": "assistant",
                                         "content": [{"type": "output_text", "text": full_text,
                                                      "annotations": []}]},
                            })
                            final_output.append({
                                "id": text_msg_id, "type": "message", "status": "completed",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": full_text, "annotations": []}],
                            })
                            text_msg_id = None
                            text_part_added = False
                            output_index += 1

                        fc_item_id = _new_id("fc")
                        fc_item_ids[idx] = fc_item_id
                        fc_output_indices[idx] = output_index
                        yield _sse("response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": output_index,
                            "item": {"id": fc_item_id, "type": "function_call",
                                     "status": "in_progress",
                                     "call_id": tool_calls[idx]["id"],
                                     "name": tool_calls[idx]["name"],
                                     "arguments": ""},
                        })
                        fc_added[idx] = True
                        output_index += 1

                    if args_delta:
                        tool_calls[idx]["args"] += args_delta
                        yield _sse("response.function_call_arguments.delta", {
                            "type": "response.function_call_arguments.delta",
                            "item_id": fc_item_ids[idx],
                            "output_index": fc_output_indices[idx],
                            "delta": args_delta,
                        })

    except (httpx.ReadTimeout, httpx.ReadError, httpx.ConnectError,
            httpx.PoolTimeout, httpx.RemoteProtocolError,
            ConnectionResetError, BrokenPipeError, OSError) as exc:
        logger.error("Upstream stream error: %s", exc)
        upstream_errored = True
        if text_msg_id is not None and text_part_added:
            yield _sse("response.output_text.done", {
                "type": "response.output_text.done",
                "item_id": text_msg_id, "output_index": output_index,
                "content_index": 0, "text": full_text,
            })
    except Exception as exc:
        logger.error("Unexpected stream error: %s", exc)
        upstream_errored = True
    finally:
        await cli.aclose()

    if text_msg_id is not None:
        if text_part_added:
            yield _sse("response.output_text.done", {
                "type": "response.output_text.done",
                "item_id": text_msg_id, "output_index": output_index,
                "content_index": 0, "text": full_text,
            })
            yield _sse("response.content_part.done", {
                "type": "response.content_part.done",
                "item_id": text_msg_id, "output_index": output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": full_text, "annotations": []},
            })
        yield _sse("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": {"id": text_msg_id, "type": "message",
                     "status": "completed" if not upstream_errored else "incomplete",
                     "role": "assistant",
                     "content": [{"type": "output_text", "text": full_text, "annotations": []}]},
        })
        final_output.append({
            "id": text_msg_id, "type": "message",
            "status": "completed" if not upstream_errored else "incomplete",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text, "annotations": []}],
        })

    for idx, tc in tool_calls.items():
        yield _sse("response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "item_id": fc_item_ids[idx],
            "output_index": fc_output_indices[idx],
            "arguments": tc["args"],
        })
        item = {"id": fc_item_ids[idx], "type": "function_call", "status": "completed",
                "call_id": tc["id"], "name": tc["name"], "arguments": tc["args"]}
        yield _sse("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": fc_output_indices[idx], "item": item,
        })
        final_output.append(item)

    completed = {
        "id": response_id, "object": "response", "created_at": created,
        "status": "completed" if not upstream_errored else "incomplete",
        "output": final_output,
        "output_text": full_text,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }
    completed.update(_resp_base(req_body, model))
    yield _sse("response.completed", {"type": "response.completed", "response": completed})


# ---------- routes ----------
@app.post("/v1/responses")
async def responses_endpoint(request: Request, authorization: str = Header(None)):
    _check_auth(authorization)
    acc = _resolve_account()
    body = await request.json()
    model = _resolve_model(acc, body.get("model"))
    chat_body = responses_to_chat(body, model)

    if body.get("stream"):
        return StreamingResponse(
            stream_responses(chat_body, model, acc["api_base"], acc["api_key"],
                             req_body=body),
            media_type="text/event-stream",
        )

    headers = {"Authorization": f"Bearer {acc['api_key']}", "Content-Type": "application/json"}
    async with _make_client() as cli:
        r = await cli.post(f"{acc['api_base'].rstrip('/')}/chat/completions",
                           json=chat_body, headers=headers)
    if r.status_code != 200:
        return JSONResponse(status_code=r.status_code, content=_safe_json(r))
    return chat_to_responses(r.json(), model, req_body=body)


@app.post("/v1/chat/completions")
async def chat_endpoint(request: Request, authorization: str = Header(None)):
    _check_auth(authorization)
    acc = _resolve_account()
    body = await request.json()
    body["model"] = _resolve_model(acc, body.get("model"))
    headers = {"Authorization": f"Bearer {acc['api_key']}", "Content-Type": "application/json"}

    if body.get("stream"):
        async def proxy_stream():
            cli = _make_client()
            try:
                async with cli.stream("POST", f"{acc['api_base'].rstrip('/')}/chat/completions",
                                      json=body, headers=headers) as r:
                    async for chunk in r.aiter_bytes():
                        yield chunk
            except (httpx.ReadTimeout, httpx.ReadError, httpx.ConnectError,
                    httpx.PoolTimeout, httpx.RemoteProtocolError,
                    ConnectionResetError, BrokenPipeError, OSError) as exc:
                logger.error("Chat proxy stream error: %s", exc)
                err_data = json.dumps({"error": {"message": f"Stream interrupted: {exc}",
                                                 "type": "stream_error"}},
                                      ensure_ascii=False)
                yield f"data: {err_data}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
            except Exception as exc:
                logger.error("Unexpected chat proxy error: %s", exc)
                err_data = json.dumps({"error": {"message": f"Unexpected error: {exc}",
                                                 "type": "stream_error"}},
                                      ensure_ascii=False)
                yield f"data: {err_data}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
            finally:
                await cli.aclose()
        return StreamingResponse(proxy_stream(), media_type="text/event-stream")

    async with _make_client() as cli:
        r = await cli.post(f"{acc['api_base'].rstrip('/')}/chat/completions",
                           json=body, headers=headers)
    return JSONResponse(status_code=r.status_code, content=_safe_json(r))


@app.get("/v1/models")
async def list_models(authorization: str = Header(None)):
    _check_auth(authorization)
    cfg = config.load()
    data = []
    seen = set()
    for acc in cfg.get("accounts", []):
        if not acc.get("enabled", True):
            continue
        models = acc.get("models") or []
        if models:
            for m in models:
                if m and m not in seen:
                    seen.add(m)
                    data.append({"id": m, "object": "model",
                                 "owned_by": acc.get("provider", "custom")})
        elif acc.get("default_model") and acc["default_model"] not in seen:
            seen.add(acc["default_model"])
            data.append({"id": acc["default_model"], "object": "model",
                         "owned_by": acc.get("provider", "custom")})
    return {"object": "list", "data": data}


@app.get("/health")
@app.get("/health/liveliness")
async def health():
    return {"status": "alive"}


def _safe_json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except Exception:
        return {"error": {"message": r.text, "code": r.status_code}}
