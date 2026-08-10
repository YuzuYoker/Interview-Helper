"""langchain.agents.middleware 中间件：记忆注入 + 请求日志。

- dynamic_prompt：每请求把长期记忆（SQLite 事实表）+ 系统规则拼成动态系统提示；
- before_model：记录模型调用（request_id 日志，可观测）。
"""
from langchain.agents.middleware import before_model, dynamic_prompt
from langchain_core.messages import SystemMessage

from app.core.log import logger
from app.prompt.prompt_loader import load_prompt
from app.utils import memories as memories_db


@dynamic_prompt
def memory_and_rules_prompt(request) -> SystemMessage:
    """动态系统提示：面试规则 + 命中长期记忆。"""
    question = ""
    for m in reversed(request.state.get("messages", []) or []):
        if getattr(m, "type", "") == "human":
            question = str(getattr(m, "content", ""))
            break
    memory_text = ""
    try:
        items = memories_db.search_memories(question, "", 5)
        if items:
            parts = [f"- [{m['category']}] {m['key']}：{m['content']}" for m in items]
            memory_text = "\n\n【长期记忆】用户画像/偏好/历史结论：\n" + "\n".join(parts)
    except Exception as e:  # 记忆失败不阻塞
        logger.warning("记忆注入失败: %s", e)
    return SystemMessage(content=load_prompt("agent_system") + memory_text)


@before_model
async def log_model_call(state, runtime) -> None:
    """模型调用前日志（request_id 已由 core.log 注入）。"""
    msgs = state.get("messages", []) or []
    last = msgs[-1] if msgs else None
    logger.info(
        "模型调用：%d 条消息，最后 %s",
        len(msgs),
        type(last).__name__ if last else "none",
    )


def build_middleware() -> list:
    """create_agent 使用的中间件列表（官方 AgentMiddleware）。"""
    return [memory_and_rules_prompt, log_model_call]
