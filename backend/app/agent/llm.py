"""集中初始化 Chat Model（init_chat_model），复用同一个实例。

配置三件套（model/base_url/api_key）统一读取。
"""
from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI

from app.utils.config import settings


def get_chat_model(temperature: float = 0.1, max_tokens: int | None = None) -> ChatOpenAI:
    """DeepSeek（OpenAI 兼容）聊天模型。max_tokens 默认取 settings.max_tokens。"""
    return init_chat_model(
        model=settings.model,
        model_provider="openai",
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        temperature=temperature,
        max_tokens=max_tokens or settings.max_tokens,
        timeout=settings.agent_timeout,
    )


def get_small_model() -> ChatOpenAI:
    """小任务模型：低温度、低 token（意图抽取/标题/标签/评估等结构化子任务）。"""
    return ChatOpenAI(
        model=settings.rewrite_model or settings.model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0,
        max_tokens=200,
        timeout=15,
    )


def get_structured_model(schema):
    """返回绑定结构化输出的模型（官方 with_structured_output）。

    DeepSeek v4-flash 不支持 response_format(json_schema) 与强制 tool_choice
    （思考模式），实测 method="json_mode" 可用 → 统一用它。
    """
    return get_small_model().with_structured_output(schema, method="json_mode")
