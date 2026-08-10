"""对话相关 pydantic 模型。"""
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = None  # 无 conversation_id 时的兜底历史
    conversation_id: Optional[str] = None  # 会话持久化：提供则服务端读库取权威历史并落库
    top_k: int = Field(default=4, ge=1, le=10)
    web_search: bool = False  # 联网搜索开关（前端「🌐 联网搜索」）


class Source(BaseModel):
    """引用溯源基础结构（前端直接渲染）。"""

    index: int  # [1][2][3] 编号
    content: str
    filename: str
    page: Optional[int] = None
    score: float
    is_attachment: bool = False  # 用户上传的资料（非内置种子库），前端显示"附件"标签
    url: Optional[str] = None  # 联网搜索结果：网页链接（点击可打开）
    is_web: bool = False  # 联网搜索结果（前端 🌐 展示，不显示置信度）


class ChatResponse(BaseModel):
    answer: str  # 正文含 [1][2] 标注
    sources: list[Source]
    tool_trace: list[dict] = []  # Agent 工具调用轨迹（非 agent 模式为空）
