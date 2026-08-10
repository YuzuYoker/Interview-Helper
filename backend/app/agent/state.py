"""Interview Agent 状态（TypedDict）——一次问答链路的输入与共享数据。

逐步写入意图、检索结果、合并上下文、最终回答与工具轨迹。
"""
from typing import TypedDict


class InterviewAgentState(TypedDict, total=False):
    """一次面试问答链路的图状态。"""

    # 输入
    question: str  # 用户问题
    conversation_id: str | None  # 会话记忆槽
    history: list[dict]  # 对话历史 [{role, content}]
    top_k: int
    web_hint: bool  # 前端联网开关 → 提示

    # 意图抽取
    intent: dict  # {needs_knowledge, needs_web, search_query, memory_query}

    # 检索 / 联网
    sources: list[dict]  # 知识库引用 Source dump（merge 统一全局编号 [n]）
    web_sources: list[dict]  # 网页引用 Source dump（merge 统一全局编号）

    # 上下文
    memory_context: str  # 长期记忆注入文本
    context_text: str  # 合并后的参考资料文本（[n] 编号，喂给生成节点）
    history_text: str  # 格式化后的对话历史文本（生成节点用）

    # 工具轨迹 / 输出
    tool_trace: list[dict]  # tool_call/tool_result 序列（SSE + 持久化）
    answer: str
    error: str | None
