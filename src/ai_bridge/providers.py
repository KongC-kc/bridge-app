"""Provider 预设：内置常用 OpenAI-Compatible 服务的 base url 和默认模型。"""

# 默认能力（OpenAI 级别全支持）
_DEFAULT_CAPABILITIES = {
    "parallel_tool_calls": True,
    "strict_tool_definition": True,
    "tool_choice_values": ["auto", "required", "none"],
    "simplify_schema": False,
}

PROVIDERS = {
    "glm": {
        "name": "智谱 GLM",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.1",
        "doc_url": "https://open.bigmodel.cn",
        "capabilities": {
            "parallel_tool_calls": False,
            "strict_tool_definition": False,
            "tool_choice_values": ["auto", "none"],
            "simplify_schema": True,
        },
    },
    "glm_plan": {
        "name": "GLM Plan",
        "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",
        "default_model": "glm-5.1",
        "doc_url": "https://open.bigmodel.cn",
        "has_quota": True,
        "capabilities": {
            "parallel_tool_calls": False,
            "strict_tool_definition": False,
            "tool_choice_values": ["auto", "none"],
            "simplify_schema": True,
        },
    },
    "deepseek": {
        "name": "DeepSeek",
        "api_base": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "doc_url": "https://platform.deepseek.com",
        "capabilities": {
            "parallel_tool_calls": False,
            "strict_tool_definition": True,
            "tool_choice_values": ["auto", "required", "none"],
            "simplify_schema": True,
        },
    },
    "qwen": {
        "name": "通义千问",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "doc_url": "https://dashscope.console.aliyun.com",
        "capabilities": {
            "parallel_tool_calls": False,
            "strict_tool_definition": False,
            "tool_choice_values": ["auto", "required", "none"],
            "simplify_schema": True,
        },
    },
    "moonshot": {
        "name": "Moonshot",
        "api_base": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "doc_url": "https://platform.moonshot.cn",
        "capabilities": {
            "parallel_tool_calls": False,
            "strict_tool_definition": False,
            "tool_choice_values": ["auto", "none"],
            "simplify_schema": True,
        },
    },
    "mimo": {
        "name": "小米 MiMo",
        "api_base": "https://api.xiaomimimo.com/v1",
        "default_model": "mimo-v2.5-pro",
        "doc_url": "https://platform.xiaomimimo.com",
        "capabilities": {
            "parallel_tool_calls": False,
            "strict_tool_definition": False,
            "tool_choice_values": ["auto", "none"],
            "simplify_schema": True,
        },
    },
    "openai": {
        "name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "doc_url": "https://platform.openai.com",
    },
    "custom": {
        "name": "自定义",
        "api_base": "",
        "default_model": "",
        "doc_url": "",
    },
}


def get_capabilities(provider_key: str | None) -> dict:
    """获取指定 provider 的能力集，未配置的回退到默认值。"""
    if provider_key and provider_key in PROVIDERS:
        caps = PROVIDERS[provider_key].get("capabilities")
        if caps:
            return {**_DEFAULT_CAPABILITIES, **caps}
    return dict(_DEFAULT_CAPABILITIES)


def list_providers():
    return [{"key": k, **v} for k, v in PROVIDERS.items()]
