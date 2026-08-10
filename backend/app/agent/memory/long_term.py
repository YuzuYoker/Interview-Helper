"""长期记忆：SQLite 事实表 + 官方结构化抽取（with_structured_output）。

- 存取层复用 app.utils.memories（save_memory/search_memories/...）；
- extract_memories：从一轮对话抽取值得记住的用户事实（官方 with_structured_output
  返回 MemoryExtraction），供 after_agent 中间件自动落库。
"""
from app.utils.memories import (  # noqa: F401
    delete_memory,
    get_memory,
    list_memories,
    memory_stats,
    save_memory,
    search_memories,
)


def extract_memories(messages: list, source: str = "") -> list[dict]:
    """从对话消息中抽取长期记忆（官方 with_structured_output）。失败返回 []（不阻塞）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agent.schemas import MemoryExtraction
    from app.agent.llm import get_structured_model

    text = "\n".join(f"{type(m).__name__}: {str(getattr(m, 'content', ''))[:300]}" for m in messages[-8:])
    if not text.strip():
        return []
    try:
        resp = get_structured_model(MemoryExtraction).invoke(
            [SystemMessage(
                content=(
                    "你是记忆抽取器。从对话中抽取【用户明确陈述、且跨会话仍有用】的事实，如：目标岗位、期望薪资、城市、"
                    "技能栈、求职意向、个人偏好、关键决策、面试进展。寒暄、检索到的资料内容、通用建议不抽取。"
                    "key 用简短语义名（如 目标岗位/期望薪资/期望城市/技能栈）。只输出 json。"
                )
            ),
             HumanMessage(content=text)]
        )
        out = []
        for m in resp.memories:
            saved = save_memory(m.key, m.content, m.category, source=source)
            out.append({"key": m.key, "content": m.content, "category": m.category, "id": saved.get("id")})
        return out
    except Exception:
        return []
