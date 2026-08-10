"""对话接口：RAG 问答（非流式 + SSE 流式，带 Redis 缓存 + 会话持久化）。

会话持久化（第5周）：请求带 conversation_id 时，
- 历史取服务端权威（读库），不依赖前端回传 —— 刷新/重开后上下文仍在；
- 用户消息先落库（生成失败也不丢），回答完成后 assistant 消息带引用落库；
- 不带 conversation_id 时保持旧行为（用请求内 history，不落库），兼容测试脚本。

联网搜索（第6周）：req.web_search 开关 OR 问题含时效敏感词自动触发
（build_answer_cached / stream_answer_cached 的 web_search 参数）。
"""
import json
import re

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from app.models.chat import ChatRequest, ChatResponse
from app.rag.generator import (
    build_answer_cached,
    stream_answer_cached,
)
from app.utils import conversations
from app.utils.config import settings

router = APIRouter()

# 时效敏感词：命中即自动联网（宁可少触发，不误伤"谈薪/简历"等知识库就能答的问题）
_WEB_AUTO_RE = re.compile(
    r"最新|今年|最近|近两年|202[4-9]|趋势|行情|新闻|热点|政策|榜单|排行|现状|薪资行情|工资水平|天气"
)


def _should_web_search(question: str, flag: bool) -> bool:
    """触发条件：前端开关 ON，或（自动触发开启 && 含时效敏感词/问题带链接）。"""
    if not settings.web_search_enabled:
        return False
    if flag:
        return True
    has_url = bool(re.search(r"https?://", question))
    return settings.web_search_auto and (
        has_url or bool(_WEB_AUTO_RE.search(question))
    )


def _check_request(req: ChatRequest) -> None:
    if not settings.deepseek_api_key:
        raise HTTPException(
            503, "DEEPSEEK_API_KEY 未配置，请先在根目录 .env 中填写"
        )
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")


def _resolve(req: ChatRequest) -> tuple[str | None, list[dict]]:
    """返回 (conversation_id, 服务端权威历史)。

    有 conversation_id：读库取历史（不含当前问题，最新一条是上轮 assistant）。
    无 conversation_id：回退请求内 history（兼容 smoke_test 等旧调用）。
    """
    if req.conversation_id:
        conv = conversations.get_conversation(req.conversation_id)
        if conv is None:
            raise HTTPException(404, f"会话不存在: {req.conversation_id}")
        return req.conversation_id, [
            {"role": m["role"], "content": m["content"]} for m in conv["messages"]
        ]
    return None, req.history or []


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, response: Response):
    _check_request(req)
    conv_id, history = _resolve(req)
    question = req.question.strip()

    if conv_id:
        conversations.append_message(conv_id, "user", question)  # 先落库，防生成失败丢失
    web_flag = _should_web_search(question, req.web_search)
    resp, hit = build_answer_cached(question, history, req.top_k, web_search=web_flag)
    response.headers["X-Cache"] = "HIT" if hit else "MISS"

    if conv_id:
        conversations.append_message(
            conv_id,
            "assistant",
            resp.answer,
            [s.model_dump() for s in resp.sources],
        )
    return resp


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式：sources → delta* → done/error（缓存命中回放打字机）。

    注意：不设 response_model（FastAPI 会尝试校验流式响应）。
    """
    _check_request(req)
    conv_id, history = _resolve(req)
    question = req.question.strip()

    async def gen():
        sources_data: list[dict] = []
        if conv_id:
            conversations.append_message(conv_id, "user", question)
        web_flag = _should_web_search(question, req.web_search)
        async for event, payload in stream_answer_cached(
            question, history, req.top_k, web_search=web_flag
        ):
            yield (
                f"event: {event}\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )
            if event == "sources":
                sources_data = payload.get("sources", [])
            if conv_id and event == "done" and payload.get("answer"):
                conversations.append_message(
                    conv_id, "assistant", payload["answer"], sources_data
                )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 反代防缓冲
        },
    )
