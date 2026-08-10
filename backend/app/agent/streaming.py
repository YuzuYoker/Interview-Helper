"""图执行 → SSE 事件流：消费 create_agent 官方 astream(stream_mode=[messages, updates])。

- messages 模式 → 最终答案 token 流（delta）；
- updates 模式 → 模型工具决策（tool_call）与工具结果（tool_result，含 sources）；
- 收尾：全局编号 sources + done（answer / source_count / tool_trace）。
"""
import asyncio
from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.engine import get_agent
from app.agent.tools import TOOL_FRIENDLY
from app.models.chat import ChatResponse, Source

# 输入 state 兼容旧接口（question/history/top_k/web_hint/conversation_id）


def _friendly(name: str) -> str:
    return TOOL_FRIENDLY.get(name, name)


def _build_input(state: dict) -> dict:
    """组装 create_agent 输入消息：会话历史 + 当前问题。"""
    question = state["question"]
    history = state.get("history") or []
    messages: list = []
    for m in history[-8:]:
        content = str(m.get("content", ""))
        messages.append(
            AIMessage(content=content) if m.get("role") == "assistant"
            else HumanMessage(content=content)
        )
    messages.append(HumanMessage(content=question))
    return {"messages": messages}


async def stream_agent(
    state: dict, context=None
) -> AsyncIterator[tuple[str, dict]]:
    """执行 create_agent，产出 (event, payload) 序列。"""
    agent = get_agent()
    sources: list[dict] = []
    tool_trace: list[dict] = []
    deltas: list[str] = []

    async for chunk in agent.astream(
        _build_input(state), stream_mode=["messages", "updates"]
    ):
        if not isinstance(chunk, tuple) or len(chunk) < 2:
            continue
        mode, data = chunk[0], chunk[1]

        if mode == "messages":
            msg, _meta = data
            if getattr(msg, "type", "") == "ai":
                text = getattr(msg, "content", "") or ""
                if text:
                    deltas.append(text)
                    yield ("delta", {"content": text})

        elif mode == "updates":
            for node, update in (data or {}).items():
                if node == "model":
                    for m in update.get("messages", []) or []:
                        for tc in getattr(m, "tool_calls", None) or []:
                            name = tc.get("name", "")
                            args = tc.get("args", {})
                            tid = tc.get("id", "")
                            yield ("thought", {"type": "step", "message": f"调用 {_friendly(name)}…"})
                            yield ("tool_call", {"tool_call_id": tid, "name": name, "args": args})
                            tool_trace.append(
                                {"type": "tool_call", "name": name, "args": args, "tool_call_id": tid}
                            )
                elif node == "tools":
                    for tm in update.get("messages", []) or []:
                        artifact = getattr(tm, "artifact", None) or {}
                        name = getattr(tm, "name", "") or ""
                        summary = artifact.get("summary", "")
                        ok = bool(artifact.get("ok", True))
                        yield ("tool_result", {
                            "tool_call_id": getattr(tm, "tool_call_id", ""),
                            "name": name, "ok": ok, "summary": summary,
                        })
                        tool_trace.append(
                            {"type": "tool_result", "name": name, "ok": ok, "summary": summary}
                        )
                        if summary:
                            yield ("thought", {"type": "step", "message": summary})
                        data_sources = ((artifact.get("data") or {}).get("sources")) or []
                        sources.extend(data_sources)

    # 收尾：全局编号 + sources + done
    for i, s in enumerate(sources, 1):
        s["index"] = i
    answer = "".join(deltas)
    yield ("sources", {"sources": sources})
    yield ("done", {
        "answer": answer or "知识库中暂无相关信息。",
        "source_count": len(sources),
        "tool_trace": tool_trace,
    })


def run_agent(state: dict, context=None) -> ChatResponse:
    """同步变体（POST /api/chat）：收集事件，返回 ChatResponse。"""
    sources: list[dict] = []
    answer = ""
    tool_trace: list[dict] = []

    async def _collect():
        nonlocal sources, answer, tool_trace
        async for event, payload in stream_agent(state, context):
            if event == "sources":
                sources = payload.get("sources", [])
            elif event == "tool_call":
                tool_trace.append({"type": "tool_call", "name": payload.get("name"),
                                   "args": payload.get("args", {})})
            elif event == "tool_result":
                tool_trace.append({"type": "tool_result", "name": payload.get("name"),
                                   "ok": payload.get("ok", True),
                                   "summary": payload.get("summary", "")})
            elif event == "done":
                answer = payload.get("answer", "")
            elif event == "error":
                answer = answer or "生成失败，请重试。"

    asyncio.run(_collect())
    return ChatResponse(
        answer=answer or "知识库中暂无相关信息。",
        sources=[Source(**s) for s in sources],
        tool_trace=tool_trace,
    )
