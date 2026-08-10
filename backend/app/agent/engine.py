"""Agent 引擎：langchain.agents.create_agent 装配（官方 agent 工厂）。

- create_agent：官方 agent 工厂，自带 invoke/ainvoke/stream/astream 输出方式；
- tools：17 个自定义官方 StructuredTool；
- middleware：langchain.agents.middleware（记忆注入/日志）；
- 短期记忆 = 会话历史注入消息上下文；长期记忆 = SQLite 事实表（dynamic_prompt 注入）。
"""
from langchain.agents import create_agent

from app.agent.llm import get_chat_model
from app.agent.middleware import build_middleware
from app.agent.tools import build_tools

_agent = None


def get_agent():
    """全局 agent 单例（懒构建）。"""
    global _agent
    if _agent is None:
        _agent = create_agent(
            get_chat_model(),
            tools=build_tools(),
            middleware=build_middleware(),
        )
    return _agent
