"""对话接口：Agent 问答（非流式 + SSE 流式，带 Redis 缓存 + 会话持久化）。

Agent 模式（AGENT_ENABLED=true）：LLM 通过工具自主决定检索/联网/改写，
SSE 事件含 thought/tool_call/tool_result（sources/delta/done 兼容旧前端）；
req.web_search 字段降级为**系统提示 hint**（不再做代码路径触发——完全 LLM 驱动）。
传统路径（AGENT_ENABLED=false）：回退 build_answer_cached / stream_answer_cached。

会话持久化（第5周）：带 conversation_id 时服务端读库取权威历史，
用户消息先落库（生成失败也不丢），回答完成后 assistant 消息带 sources + tool_trace 落库。
"""
import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from app.core.log import logger
from app.models.chat import ChatRequest, ChatResponse
from app.rag.generator import (
    build_answer_cached,
    stream_answer_cached,
)
from app.utils import conversations
from app.utils.config import settings

router = APIRouter()


def _maybe_generate_title(conv_id: str, content: str) -> None:
    """首条消息后后台生成会话标题（agent_title_auto），非阻塞。"""
    if not (settings.agent_enabled and settings.agent_title_auto):
        return
    try:
        from app.agent.tools import _generate_title

        threading.Thread(
            target=_generate_title,
            kwargs={"conversation_id": conv_id, "content": content},
            daemon=True,
        ).start()
    except Exception:
        pass


def _maybe_extract_memories(question: str, answer: str, conv_id: str | None) -> None:
    """回答完成后后台抽取长期记忆（结构化抽取 → SQLite memories），非阻塞。"""
    if not (settings.agent_enabled and conv_id):
        return
    try:
        from langchain_core.messages import AIMessage, HumanMessage
        from app.agent.memory.long_term import extract_memories

        messages = [HumanMessage(content=question), AIMessage(content=answer)]
        threading.Thread(
            target=extract_memories,
            args=(messages, conv_id),
            daemon=True,
        ).start()
    except Exception:
        pass


def _check_request(req: ChatRequest) -> None:
    if not settings.deepseek_api_key:
        raise HTTPException(
            503, "DEEPSEEK_API_KEY 未配置，请先在根目录 .env 中填写"
        )
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")


def _resolve(req: ChatRequest) -> tuple[str | None, list[dict], str, bool]:
    """返回 (conversation_id, 服务端权威历史, 本轮问题, 是否需要新落库 user 消息)。

    - 普通一轮：history = 库里已有消息（不含当前问题），persist_user=True；
    - regenerate（重新生成 / 重试）：服务端在同一事务里删除最后一条 user 之后的
      assistant 消息，复用已落库的 user 内容，persist_user=False，避免重复问答轮次；
    - 无 conversation_id：回退请求内 history（兼容 smoke_test 等旧调用）。
    """
    question = req.question.strip()
    if not req.conversation_id:
        return None, req.history or [], question, True

    conv = conversations.get_conversation(req.conversation_id)
    if conv is None:
        raise HTTPException(404, f"会话不存在: {req.conversation_id}")

    if req.regenerate:
        rewound = conversations.rewind_last_turn(
            req.conversation_id, expected_question=question
        )
        if rewound is not None:
            stored_question, history = rewound
            return req.conversation_id, history, stored_question or question, False
        # 库里没有匹配的 user 消息（旧异常会话 / 请求在落库前失败）：退回普通一轮

    return (
        req.conversation_id,
        [{"role": m["role"], "content": m["content"]} for m in conv["messages"]],
        question,
        True,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, response: Response):
    _check_request(req)
    conv_id, history, question, persist_user = _resolve(req)

    if conv_id and persist_user:
        conversations.append_message(conv_id, "user", question)  # 先落库，防生成失败丢失
    web_flag = req.web_search  # Agent：仅 hint；传统：联网开关
    resp, hit = build_answer_cached(
        question, history, req.top_k, web_search=web_flag, bypass_cache=req.regenerate
    )
    response.headers["X-Cache"] = "HIT" if hit else "MISS"

    if conv_id:
        conversations.append_message(
            conv_id,
            "assistant",
            resp.answer,
            [s.model_dump() for s in resp.sources],
            resp.tool_trace,
        )
        _maybe_extract_memories(question, resp.answer, conv_id)
        if not history and persist_user:  # 首条消息 → 后台生成标题
            _maybe_generate_title(conv_id, question)
    return resp


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式：thought/tool_call/tool_result/sources → delta* → done/error。

    注意：不设 response_model（FastAPI 会尝试校验流式响应）。
    """
    _check_request(req)
    conv_id, history, question, persist_user = _resolve(req)

    async def gen():
        sources_data: list[dict] = []
        trace_data: list[dict] = []
        thought_data: list[str] = []
        partial: list[str] = []
        persisted = False  # done 已落库，防止取消路径重复写 assistant
        if conv_id and persist_user:
            conversations.append_message(conv_id, "user", question)
        web_flag = req.web_search
        try:
            async for event, payload in stream_answer_cached(
                question,
                history,
                req.top_k,
                web_search=web_flag,
                bypass_cache=req.regenerate,
            ):
                # 先记账、done 先落库、最后 yield：
                # 客户端断开时生成器会在 yield 处被取消，yield 之后的代码不会执行。
                if event == "sources":
                    sources_data = payload.get("sources", [])
                elif event == "delta":
                    partial.append(payload.get("content", ""))
                elif event == "thought":
                    thought_data.append(payload.get("message", ""))
                elif event == "tool_call":
                    trace_data.append(
                        {"type": "tool_call", "name": payload.get("name"),
                         "args": payload.get("args", {}),
                         "tool_call_id": payload.get("tool_call_id")}
                    )
                elif event == "tool_result":
                    trace_data.append(
                        {"type": "tool_result", "name": payload.get("name"),
                         "ok": payload.get("ok", True),
                         "summary": payload.get("summary", ""),
                         "tool_call_id": payload.get("tool_call_id")}
                    )
                if conv_id and event == "done" and payload.get("answer"):
                    try:
                        conversations.append_message(
                            conv_id,
                            "assistant",
                            payload["answer"],
                            sources_data,
                            payload.get("tool_trace") or trace_data,
                            thought_data,
                        )
                        persisted = True
                    except Exception:
                        logger.exception("assistant 消息落库失败")
                    _maybe_extract_memories(question, payload["answer"], conv_id)
                    if not history and persist_user:  # 首条消息 → 后台生成标题
                        _maybe_generate_title(conv_id, question)
                yield (
                    f"event: {event}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
        except asyncio.CancelledError:
            # 用户中断：已落库则不重复；否则保留已生成的部分（有内容才落库，保守）
            if conv_id and not persisted and "".join(partial).strip():
                conversations.append_message(
                    conv_id,
                    "assistant",
                    "".join(partial),
                    sources_data,
                    trace_data,
                    thought_data,
                )
            raise

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 反代防缓冲
        },
    )
