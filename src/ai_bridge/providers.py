"""Provider 预设：内置常用 OpenAI-Compatible 服务的 base url 和默认模型。"""

PROVIDERS = {
    "glm": {
        "name": "智谱 GLM",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.1",
        "doc_url": "https://open.bigmodel.cn",
    },
    "deepseek": {
        "name": "DeepSeek",
        "api_base": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "doc_url": "https://platform.deepseek.com",
    },
    "qwen": {
        "name": "通义千问",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "doc_url": "https://dashscope.console.aliyun.com",
    },
    "moonshot": {
        "name": "Moonshot",
        "api_base": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "doc_url": "https://platform.moonshot.cn",
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


def list_providers():
    return [{"key": k, **v} for k, v in PROVIDERS.items()]
