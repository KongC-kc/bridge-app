"""GLM Plan 额度查询：调用 /api/monitor/usage/* 端点获取套餐剩余额度。"""
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
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    start_time = yesterday.strftime("%Y-%m-%d %H:00:00")
    end_time = now.strftime("%Y-%m-%d %H:59:59")

    result: dict = {"ok": True, "limits": [], "model_usage": None, "tool_usage": None}

    try:
        cli = httpx.Client(timeout=15.0)

        # quota/limit — 额度百分比
        try:
            r = cli.get(f"{domain}/api/monitor/usage/quota/limit", headers=headers)
            if r.status_code == 200:
                data = r.json()
                limits = []
                for item in data.get("limits") or []:
                    ltype = item.get("type", "")
                    pct = item.get("percentage", 0)
                    if ltype == "TOKENS_LIMIT":
                        limits.append({
                            "type": "Token 用量",
                            "window": "5 小时滚动窗口",
                            "percentage": pct,
                            "remaining": round(100 - pct, 1),
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
                        })
                    else:
                        limits.append({"type": ltype, "percentage": pct})
                result["limits"] = limits
        except Exception as e:
            logger.warning("quota/limit query failed: %s", e)

        # model-usage — 模型调用量
        try:
            r = cli.get(
                f"{domain}/api/monitor/usage/model-usage",
                params={"startTime": start_time, "endTime": end_time},
                headers=headers,
            )
            if r.status_code == 200:
                result["model_usage"] = r.json()
        except Exception as e:
            logger.warning("model-usage query failed: %s", e)

        # tool-usage — 工具调用量
        try:
            r = cli.get(
                f"{domain}/api/monitor/usage/tool-usage",
                params={"startTime": start_time, "endTime": end_time},
                headers=headers,
            )
            if r.status_code == 200:
                result["tool_usage"] = r.json()
        except Exception as e:
            logger.warning("tool-usage query failed: %s", e)

        cli.close()
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)

    if not result["limits"] and not result.get("error"):
        result["ok"] = False
        result["error"] = "未获取到额度数据"

    return result
