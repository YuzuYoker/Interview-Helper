"""Agent 模块：langchain.agents.create_agent 装配 + 中间件 + 自定义工具 + 记忆体系。

- engine.py：`langchain.agents.create_agent` 装配（官方 agent 工厂，自带 invoke/stream/astream）
- middleware.py：`langchain.agents.middleware`（dynamic_prompt 记忆注入 / before_model 日志）
- tools.py：17 个自定义工具（StructuredTool，content_and_artifact）
- schemas.py：with_structured_output(method="json_mode") 的 Pydantic 模型
- llm.py：init_chat_model 集中初始化（get_chat_model / get_small_model / get_structured_model）
- memory/：长期记忆（SQLite 事实表 + 结构化抽取）；短期记忆=会话历史注入
- streaming.py：create_agent.astream → SSE (event, payload) 映射
- prompt/ + backend/prompts/*.prompt：外部提示词模板 + prompt_loader
"""
from app.agent.engine import get_agent
from app.agent.schemas import (
    DocumentPlan,
    DocumentTags,
    Intent,
    MemoryExtraction,
    MemoryFact,
    SearchEvaluation,
    TaskPlan,
    Title,
)
from app.agent.state import InterviewAgentState
from app.agent.streaming import run_agent, stream_agent
from app.agent.tools import TOOL_FRIENDLY, build_tools

__all__ = [
    "DocumentPlan",
    "DocumentTags",
    "Intent",
    "InterviewAgentState",
    "MemoryExtraction",
    "MemoryFact",
    "SearchEvaluation",
    "TaskPlan",
    "Title",
    "TOOL_FRIENDLY",
    "build_tools",
    "get_agent",
    "run_agent",
    "stream_agent",
]
