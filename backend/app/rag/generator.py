"""RAG 生成链路：检索 → DeepSeek 生成带引用标注的回答（Interview Helper 场景）。

- build_answer：非流式（原有路径，smoke_test 依赖，签名向后兼容）
- stream_answer：SSE 流式（sources 事件先行 → delta 逐 token → done/error）
- 检索等同步 CPU/GPU 调用在 _prepare 中完成，由 API 层 run_in_threadpool 执行
- 联网搜索（web_items）：由缓存包装层提前执行，注入 _prepare 追加为 [n] 引用
"""
from typing import AsyncIterator

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.state import InterviewAgentState
from app.agent.streaming import run_agent, stream_agent
from app.models.chat import ChatResponse, Source
from app.rag.retriever import retrieve
from app.utils.config import settings

def _record_metric(**kwargs) -> None:
    """性能指标落点（best-effort，metrics 模块缺失/异常不影响主链路）。"""
    try:
        from app.utils.metrics import record

        record(**kwargs)
    except Exception:
        pass


SYSTEM_PROMPT = """你是资深面试顾问，服务求职者。请严格基于下面的面试资料库回答问题，禁止编造。
回答风格：直接、有立场、给出可操作的建议，不用客套话。
引用规则：
1. 回答中凡是依据某条资料得出的内容，在句末标注 [n]（n 为资料编号）；
2. 多条资料共同支撑时用 [1][2] 形式；
3. 参考资料里可能同时包含【资料库内容】与【联网搜索到的网页内容】（后者来源行标注了网页链接）。
   引用网页内容时同样在句末标注 [n]，最好顺带给出网址（如 [3] https://…）；
4. 资料库中没有相关内容时，不要直接说"暂无"——若联网搜索提供了相关内容，应结合联网内容回答，
   并注明"（信息来源：联网搜索）"；两者都没有时才回答"资料库中暂无相关信息"。

重要：参考资料里【可能包含用户已上传的简历/面经等个人文档】，当用户提到"我的简历/我的资料/我上传的文件"时，直接基于这些附件内容回答——它们已经在参考资料里了，不要再说"请把你的简历发给我"；如需确认，应描述已看到的内容（如"我看到了你的简历（文件名），其中包含……"）。

参考资料：
{context}"""


def _make_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0.1,
        max_tokens=settings.max_tokens,
        max_retries=3,
        timeout=60,
    )


def _build_messages(
    refs: str, question: str, history: list[dict] | None = None
) -> list:
    """构造 LLM 消息：检索资料(SYSTEM) + 对话历史 + 当前问题。

    历史必须传给 LLM——改写只补全了检索用的 query，回答生成若看不到历史，
    多轮对话就无法承接（"刚才聊过什么"完全丢失），表现为"对话没有记忆"。
    """
    msgs = [SystemMessage(SYSTEM_PROMPT.format(context=refs))]
    if history:
        hist = list(history)
        # 前端 buildHistory 会把当前问题也带进来（push 后构建），去重避免重复
        if (
            hist
            and hist[-1].get("role") == "user"
            and str(hist[-1].get("content", "")).strip() == question.strip()
        ):
            hist = hist[:-1]
        for m in hist[-8:]:  # 与改写保持一致：最近 8 条
            content = str(m.get("content", ""))
            msgs.append(
                AIMessage(content) if m.get("role") == "assistant"
                else HumanMessage(content)
            )
    msgs.append(HumanMessage(question))
    return msgs


def hits_to_sources(
    hits: list[tuple[Document, float]],
    web_items: list[dict] | None = None,
) -> tuple[list[Source], str]:
    """把检索命中 (Document, score) 与联网结果组装为 (sources, refs)。

    与 Agent 的 retrieve_knowledge/web_search 工具共用，保证引用编号 [n] 一致。
    - 联网结果追加在知识库条目之后，编号顺延（[n+1]…），sources 带 is_web=True；
    - 为空时返回 ([], "")，由调用方决定是否降级。
    """
    refs_parts: list[str] = []
    sources: list[Source] = []
    for i, (d, score) in enumerate(hits):
        refs_parts.append(
            f"[{i + 1}] 来源：{d.metadata.get('filename', '未知')}"
            + (f"，第{d.metadata.get('page')}页" if d.metadata.get("page") else "")
            + f"\n内容：{d.page_content}"
        )
        sources.append(
            Source(
                index=i + 1,
                content=d.page_content,
                filename=d.metadata.get("filename", "未知"),
                page=d.metadata.get("page"),
                score=score,
                is_attachment=(d.metadata.get("type") or "") != "interview-reference",
            )
        )

    n = len(hits)
    for item in web_items or []:
        content = (item.get("content") or item.get("snippet") or "").strip()
        if not content:
            continue
        n += 1
        title = item.get("title") or "网页"
        url = item.get("url") or ""
        refs_parts.append(f"[{n}] 来源：{title}（{url}）\n内容：{content}")
        sources.append(
            Source(
                index=n,
                content=content,
                filename=title,
                url=url,
                score=1.0,
                is_web=True,
            )
        )

    return sources, "\n".join(refs_parts)


def _prepare(
    question: str,
    history: list[dict] | None,
    top_k: int | None,
    web_items: list[dict] | None = None,
) -> tuple[list[Source], str] | None:
    """共享检索步骤：返回 (sources, refs)。

    联网搜索结果（web_items）追加在知识库条目之后，编号顺延（[n+1]…），
    sources 带 is_web=True 供前端 🌐 展示。两者都无内容才返回 None。
    """
    # 用户文档保送已并入 retrieve（4.5 步），此处无需二次附加
    hits = retrieve(question, k=top_k, history=history)  # [(Document, score), ...]
    sources, refs = hits_to_sources(hits, web_items)

    if not sources:
        return None
    return sources, refs


def build_answer(
    question: str,
    history: list[dict] | None = None,
    top_k: int | None = None,
    web_items: list[dict] | None = None,
) -> ChatResponse:
    prep = _prepare(question, history, top_k, web_items)
    if prep is None:
        return ChatResponse(answer="知识库中暂无相关信息。", sources=[])
    sources, refs = prep

    resp = _make_llm().invoke(_build_messages(refs, question, history))
    return ChatResponse(answer=resp.content, sources=sources)


async def stream_answer(
    question: str,
    history: list[dict] | None,
    top_k: int | None,
    web_items: list[dict] | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """SSE 事件生成器：产出 (event_name, payload) 序列，格式化由 API 层负责。

    事件：sources（检索完即发）→ delta*（LLM 逐 chunk）→ done / error
    """
    from starlette.concurrency import run_in_threadpool

    prep = await run_in_threadpool(_prepare, question, history, top_k, web_items)
    if prep is None:
        yield ("done", {"answer": "知识库中暂无相关信息。", "source_count": 0})
        return
    sources, refs = prep
    yield ("sources", {"sources": [s.model_dump() for s in sources]})

    chunks: list[str] = []
    try:
        async for chunk in _make_llm().astream(_build_messages(refs, question, history)):
            text = chunk.content or ""
            if not text:  # langchain-openai 1.x 末块常为空（仅含 usage）
                continue
            chunks.append(text)
            yield ("delta", {"content": text})
    except Exception as e:
        yield ("error", {"message": f"生成失败: {e}"})
        return
    yield ("done", {"answer": "".join(chunks), "source_count": len(sources)})


# ---- 缓存包装 ----


def _resolve_web_legacy(
    question: str, web_search: bool
) -> tuple[list[dict] | None, str]:
    """传统 RAG 路径（agent_enabled=False）的联网编排：直接以原问题为搜索词。

    Agent 模式下联网完全由 LLM 通过 web_search/fetch_url 工具决定，不走此函数；
    此函数只服务旧路径回退（保持 build_answer_cached/stream_answer_cached 兼容）。
    """
    if not web_search or not settings.web_search_enabled:
        return None, ""
    from app.rag import web_search as ws

    items = ws.web_search(question)
    return (items, ws.web_hash(items)) if items else (None, "")


def build_agent_answer_cached(
    question: str,
    history: list[dict] | None = None,
    top_k: int | None = None,
    web_hint: bool = False,
) -> tuple[ChatResponse, bool]:
    """Agent 非流式 + 缓存：命中返回 (response, True)。

    web_hint 仅作系统提示 hint（完全由 LLM 决定是否联网）；缓存 key 并入 web_hint
    与工具轨迹，联网/不联网回答互不污染。
    """
    import time as _time

    from app.utils.cache import get_agent_cached, set_agent_cached

    k = top_k or settings.top_k
    start = _time.monotonic()
    cached = get_agent_cached(question, history, k, web_hint)
    if cached is not None:
        _record_metric(cache_hit=True, total_ms=round((_time.monotonic() - start) * 1000, 1))
        sources = [Source(**s) for s in cached.get("sources", [])]
        return ChatResponse(
            answer=cached["answer"],
            sources=sources,
            tool_trace=cached.get("tool_trace", []),
        ), True

    state = InterviewAgentState(question=question, history=history or [], top_k=k, web_hint=web_hint)
    resp = run_agent(state)
    _record_metric(cache_hit=False, total_ms=round((_time.monotonic() - start) * 1000, 1),
                   tool_calls=len(resp.tool_trace))
    set_agent_cached(
        question,
        history,
        k,
        {
            "answer": resp.answer,
            "sources": [s.model_dump() for s in resp.sources],
            "tool_trace": resp.tool_trace,
        },
        web_hint,
    )
    return resp, False


def build_answer_cached(
    question: str,
    history: list[dict] | None = None,
    top_k: int | None = None,
    web_search: bool = False,
) -> tuple[ChatResponse, bool]:
    """带缓存：agent_enabled 走 Agent 循环，否则回退传统 RAG（web_search 作为开关）。

    返回值与旧签名兼容：(ChatResponse, is_hit)。
    """
    if settings.agent_enabled:
        return build_agent_answer_cached(question, history, top_k, web_hint=web_search)

    from app.utils.cache import get_cached_answer, set_cached_answer

    k = top_k or settings.top_k
    web_items, web_hash = _resolve_web_legacy(question, web_search)
    cached = get_cached_answer(question, history, k, bool(web_items), web_hash)
    if cached is not None:
        sources = [Source(**s) for s in cached.get("sources", [])]
        return ChatResponse(answer=cached["answer"], sources=sources), True

    resp = build_answer(question, history, top_k, web_items)
    set_cached_answer(
        question,
        history,
        k,
        {
            "answer": resp.answer,
            "sources": [s.model_dump() for s in resp.sources],
        },
        bool(web_items),
        web_hash,
    )
    return resp, False


def _replay_trace_event(entry: dict) -> tuple[str, dict]:
    """把持久化的 tool_trace 条目转成 SSE 事件（缓存命中回放用）。"""
    t = entry.get("type")
    if t == "tool_call":
        return (
            "tool_call",
            {
                "tool_call_id": entry.get("tool_call_id", ""),
                "name": entry.get("name", ""),
                "args": entry.get("args", {}),
            },
        )
    if t == "tool_result":
        return (
            "tool_result",
            {
                "tool_call_id": entry.get("tool_call_id", ""),
                "name": entry.get("name", ""),
                "ok": entry.get("ok", True),
                "summary": entry.get("summary", ""),
            },
        )
    return ("thought", {"type": "info", "message": entry.get("summary", "")})


async def stream_agent_cached(
    question: str,
    history: list[dict] | None,
    top_k: int | None,
    web_hint: bool = False,
    conv_id: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """Agent 流式 + 缓存：SSE 事件协议含 thought/tool_call/tool_result。

    命中回放：thought(命中) → tool_trace 逐条 → sources → 打字机 delta → done；
    未命中：跑 stream_agent，done 时写入 agent 缓存。
    """
    import asyncio
    import time as _time

    from app.utils.cache import get_agent_cached, set_agent_cached

    k = top_k or settings.top_k
    start = _time.monotonic()
    cached = get_agent_cached(question, history, k, web_hint)
    if cached is not None:
        _record_metric(cache_hit=True, total_ms=round((_time.monotonic() - start) * 1000, 1))
        yield ("thought", {"type": "info", "message": "命中缓存，回放上一次处理过程"})
        for entry in cached.get("tool_trace", []):
            yield _replay_trace_event(entry)
        yield ("sources", {"sources": cached.get("sources", [])})
        answer = cached["answer"]
        for i in range(0, len(answer), 6):  # 切小块模拟打字机（与 streaming.py 同节奏）
            yield ("delta", {"content": answer[i : i + 6]})
            await asyncio.sleep(0.025)
        yield ("done", {
            "answer": answer,
            "source_count": len(cached.get("sources", [])),
            "tool_trace": cached.get("tool_trace", []),
        })
        return

    sources_data: list[dict] = []
    trace_data: list[dict] = []
    state = InterviewAgentState(
        question=question, history=history or [], top_k=k,
        web_hint=web_hint, conversation_id=conv_id,
    )
    async for event, payload in stream_agent(state):
        if event == "sources":
            sources_data = payload.get("sources", [])
        elif event == "tool_call":
            trace_data.append({"type": "tool_call", "name": payload.get("name"),
                               "args": payload.get("args", {}),
                               "tool_call_id": payload.get("tool_call_id", "")})
        elif event == "tool_result":
            trace_data.append({"type": "tool_result", "name": payload.get("name"),
                               "ok": payload.get("ok", True),
                               "summary": payload.get("summary", ""),
                               "tool_call_id": payload.get("tool_call_id", "")})
        elif event == "done":
            payload = {**payload, "tool_trace": trace_data}  # 注入轨迹，供 chat.py 持久化
            _record_metric(cache_hit=False, total_ms=round((_time.monotonic() - start) * 1000, 1),
                           tool_calls=len(trace_data))
            set_agent_cached(
                question,
                history,
                k,
                {
                    "answer": payload["answer"],
                    "sources": sources_data,
                    "tool_trace": trace_data,
                },
                web_hint,
            )
        yield event, payload


async def stream_answer_cached(
    question: str,
    history: list[dict] | None,
    top_k: int | None,
    web_search: bool = False,
) -> AsyncIterator[tuple[str, dict]]:
    """带缓存的流式：agent_enabled 走 Agent 循环（含 thought/tool_call 事件），
    否则回退传统 RAG（前端零改动）。

    缓存命中时回放：sources → 按 2 字切 delta（3ms 间隔，保留打字机效果）→ done。
    """
    if settings.agent_enabled:
        async for ev, payload in stream_agent_cached(
            question, history, top_k, web_hint=web_search
        ):
            yield ev, payload
        return

    import asyncio

    from app.utils.cache import get_cached_answer, set_cached_answer

    k = top_k or settings.top_k
    web_items, web_hash = _resolve_web_legacy(question, web_search)
    cached = get_cached_answer(question, history, k, bool(web_items), web_hash)
    if cached is not None:
        yield ("sources", {"sources": cached.get("sources", [])})
        answer = cached["answer"]
        for i in range(0, len(answer), 6):  # 切小块模拟打字机（与 streaming.py 同节奏）
            yield ("delta", {"content": answer[i : i + 6]})
            await asyncio.sleep(0.025)
        yield ("done", {"answer": answer, "source_count": len(cached.get("sources", []))})
        return

    sources_data: list[dict] = []
    async for ev, payload in stream_answer(question, history, top_k, web_items):
        yield ev, payload
        if ev == "sources":
            sources_data = payload.get("sources", [])
        if ev == "done" and payload.get("answer"):
            set_cached_answer(
                question,
                history,
                k,
                {"answer": payload["answer"], "sources": sources_data},
                bool(web_items),
                web_hash,
            )
