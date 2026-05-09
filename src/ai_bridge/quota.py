"""GLM Plan 额度查询：调用 /api/monitor/usage/* 端点获取套餐剩余额度。"""
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("quota")


def _base_domain(api_base: str) -> str:
    parsed = urlparse(api_base)
    return f"{parsed.scheme}://{parsed.hostname}"


def fetch_quota(api_base: str, api_key: str) -> dict:
    """查询 GLM Plan 账号的额度信息，返回结构化结果。"""
    domain = _base_domain(api_base)
    key = api_key.strip()

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    start_time = yesterday.strftime("%Y-%m-%d %H:00:00")
    end_time = now.strftime("%Y-%m-%d %H:59:59")

    result: dict = {"ok": True, "limits": [], "model_usage": None, "tool_usage": None}
    debug_info: list[str] = []

    try:
        cli = httpx.Client(timeout=15.0)

        url = f"{domain}/api/monitor/usage/quota/limit"

        # 尝试三种认证格式：Bearer token / 直接 token / x-api-key
        auth_attempts = [
            ("Bearer", {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}),
            ("Raw", {"Authorization": key, "Content-Type": "application/json"}),
            ("x-api-key", {"x-api-key": key, "Content-Type": "application/json"}),
        ]

        for label, headers in auth_attempts:
            try:
                r = cli.get(url, headers=headers)
                debug_info.append(f"[{label}] HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except json.JSONDecodeError:
                        debug_info.append(f"[{label}] 响应非 JSON")
                        continue

                    limits = []
                    # API 返回 {"code":200, "data":{"limits":[...]}}
                    payload = data.get("data") or data
                    for item in payload.get("limits") or []:
                        ltype = item.get("type", "")
                        pct = item.get("percentage", 0)
                        if ltype == "TOKENS_LIMIT":
                            limits.append({
                                "type": "Token 用量",
                                "window": "5 小时滚动窗口",
                                "percentage": pct,
                                "remaining": round(100 - pct, 1),
                                "nextResetTime": item.get("nextResetTime"),
                            })
                        elif ltype == "TIME_LIMIT":
                            limits.append({
                                "type": "MCP 工具用量",
                                "window": "1 个月",
                                "percentage": pct,
                                "remaining": round(100 - pct, 1),
                                "currentUsage": item.get("currentValue", 0),
                                "total": item.get("usage", 0),
                                "usageDetails": item.get("usageDetails", []),
                                "nextResetTime": item.get("nextResetTime"),
                            })
                        else:
                            limits.append({"type": ltype, "percentage": pct})
                    result["limits"] = limits
                    if limits:
                        break  # 成功获取，不再尝试其他认证
                elif r.status_code == 401 or r.status_code == 403:
                    continue  # 认证失败，尝试下一种
                else:
                    break  # 其他错误（如404、500），不用再试
            except Exception as e:
                debug_info.append(f"[{label}] 请求异常: {e}")

        # model-usage — 模型调用量
        try:
            r = cli.get(
                f"{domain}/api/monitor/usage/model-usage",
                params={"startTime": start_time, "endTime": end_time},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                result["model_usage"] = r.json()
        except Exception:
            pass

        # tool-usage — 工具调用量
        try:
            r = cli.get(
                f"{domain}/api/monitor/usage/tool-usage",
                params={"startTime": start_time, "endTime": end_time},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                result["tool_usage"] = r.json()
        except Exception:
            pass

        cli.close()
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        return result

    if not result["limits"]:
        result["ok"] = False
        result["error"] = "未获取到额度数据"
        result["debug"] = " | ".join(debug_info) if debug_info else "无响应"

    return result
