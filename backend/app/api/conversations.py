"""会话接口：对话记录本地持久化（列表 / 新建 / 详情 / 删除）。"""
from fastapi import APIRouter, HTTPException

from app.models.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationInfo,
)
from app.utils import conversations

router = APIRouter()


@router.get("/conversations", response_model=list[ConversationInfo])
def list_conversations():
    """侧边栏对话列表（按最近活跃排序）。"""
    return conversations.list_conversations()


@router.post("/conversations", response_model=ConversationInfo, status_code=201)
def create_conversation(body: ConversationCreate):
    return conversations.create_conversation(body.title)


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: str):
    conv = conversations.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(404, f"会话不存在: {conv_id}")
    return conv


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    if not conversations.delete_conversation(conv_id):
        raise HTTPException(404, f"会话不存在: {conv_id}")
    return {"deleted": conv_id}
