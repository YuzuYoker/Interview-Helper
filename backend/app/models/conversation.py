"""会话相关 pydantic 模型。"""
from typing import Literal, Optional

from pydantic import BaseModel


class ConversationCreate(BaseModel):
    title: Optional[str] = None  # 省略时用"新对话"，首条消息后自动替换


class ConversationInfo(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    msg_count: int


class MessageInfo(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    sources: list[dict] = []  # assistant 消息的引用溯源（用户消息为空）
    tool_trace: list[dict] = []  # assistant 消息的 Agent 工具调用轨迹（tool_call/tool_result）
    ts: str


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    summary: Optional[str] = None  # 会话总结（summarize_conversation 落库）
    messages: list[MessageInfo]
