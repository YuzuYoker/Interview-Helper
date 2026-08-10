"""Query 优化：多轮对话改写（核心）/ 多视角扩展 / HyDE（实验）。

- 改写仅在 history 非空时由 retriever 调用（单轮对话零额外延迟）；
- 调用失败/超时静默降级为原问题，检索链路永不被改写拖垮；
- history 内容不可信（提示词注入面）：仅作上下文文本，限制轮数与长度。
"""
import json
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.utils.config import settings

REWRITE_SYSTEM = """你是搜索查询改写助手。根据对话历史把用户最新问题改写为一个独立的、
可直接用于资料库检索的完整问题（补全指代与省略）。只输出改写后的问题本身，不要解释。
若最新问题已完整清晰，原样输出。"""

REWRITE_USER = "对话历史：\n{history}\n\n用户最新问题：{question}"

MULTIVIEW_SYSTEM = """你是搜索查询改写助手。根据对话历史把用户最新问题改写为
3 个不同视角的独立检索问题（补全指代、扩展同义表达、覆盖不同信息面）。
只输出 JSON 数组，如 ["问题1", "问题2", "问题3"]，不要输出其他内容。"""

HYDE_SYSTEM = """你是知识库检索助手。根据问题写一段假设性的、详细的、可直接回答该问题的
短文（约 100-200 字，就像资料库中真的存在这段内容一样），用于向量检索。只输出短文本身。"""


def _history_text(history: list[dict]) -> str:
    msgs = history[-settings.history_turns :]  # 只取最近 N 条
    return "\n".join(
        f"{m.get('role', 'user')}: {str(m.get('content', ''))[:200]}"
        for m in msgs
    )


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.rewrite_model or settings.model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0,
        max_tokens=100,
        timeout=10,
    )


def rewrite_query(question: str, history: list[dict]) -> str:
    """多轮改写：补全指代/省略。失败返回原问题（降级）。"""
    if not history:
        return question
    try:
        resp = _llm().invoke(
            [
                SystemMessage(REWRITE_SYSTEM),
                HumanMessage(
                    REWRITE_USER.format(
                        history=_history_text(history), question=question
                    )
                ),
            ]
        )
        return (resp.content or "").strip() or question
    except Exception:
        return question


def rewrite_multiview(question: str, history: list[dict]) -> list[str]:
    """多视角扩展：一次调用返回 3 个变体（实验项）。失败降级单问题。"""
    try:
        resp = _llm().invoke(
            [
                SystemMessage(MULTIVIEW_SYSTEM),
                HumanMessage(
                    REWRITE_USER.format(
                        history=_history_text(history), question=question
                    )
                ),
            ]
        )
        variants = json.loads((resp.content or "").strip())
        if isinstance(variants, list) and variants:
            return [str(v).strip() for v in variants][:3]
    except Exception:
        pass
    return [question]


def build_hyde(question: str) -> Optional[str]:
    """HyDE：生成假设文档用于检索（实验项，默认关）。失败返回 None。"""
    try:
        resp = _llm().invoke(
            [
                SystemMessage(HYDE_SYSTEM),
                HumanMessage(question),
            ]
        )
        return (resp.content or "").strip() or None
    except Exception:
        return None
