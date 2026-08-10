"""RAG 生成链路：检索 → DeepSeek 生成带引用标注的回答（Interview Helper 场景）。

- build_answer：非流式（原有路径，smoke_test 依赖，签名向后兼容）
- stream_answer：SSE 流式（sources 事件先行 → delta 逐 token → done/error）
- 检索等同步 CPU/GPU 调用在 _prepare 中完成，由 API 层 run_in_threadpool 执行
- 联网搜索（web_items）：由缓存包装层提前执行，注入 _prepare 追加为 [n] 引用
"""
import re
from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.models.chat import ChatResponse, Source
from app.rag.retriever import retrieve
from app.utils.config import settings

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

    if not sources:
        return None
    return sources, "\n".join(refs_parts)


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


def _resolve_web(
    question: str, history: list[dict] | None, web_search: bool
) -> tuple[list[dict] | None, str]:
    """联网搜索编排：web_search=True 时执行搜索+抓取，返回 (web_items, web_hash)。

    - 搜索 query 复用多轮改写结果（补全指代，如"那公司压价怎么办"→"谈薪压价"）；
    - 返回空 → (None, "")：走纯知识库路径，缓存 key 不带 web 维度（可复用普通缓存）；
    - web_hash 并入回答缓存 key：搜索结果变化 → 视为不同回答重新生成。
    """
    if not web_search or not settings.web_search_enabled:
        return None, ""
    from app.rag import web_search as ws
    from app.rag.query_optimizer import rewrite_query

    # 1) 问题里带 http(s):// 链接 → 直接抓取该网页（不走 Bing 搜索）。
    #    只匹配合法 URL 字符集，避免吞掉紧跟的中文句尾（"…github.com/吗"→只取到 /）
    url_m = re.search(
        r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", question
    )
    if url_m:
        items = ws.fetch_url(url_m.group(0))
        return (items, ws.web_hash(items)) if items else (None, "")

    # 2) 常规搜索：多轮改写 → 天气类搜索词规范化（"X天气如何"→"X天气预报"）
    query = ws.optimize_search_query(rewrite_query(question, history or []))
    items = ws.web_search(query)
    if not items:
        return None, ""
    return items, ws.web_hash(items)


def build_answer_cached(
    question: str,
    history: list[dict] | None = None,
    top_k: int | None = None,
    web_search: bool = False,
) -> tuple[ChatResponse, bool]:
    """带缓存：命中直接返回 (response, True)。

    web_search=True 时先执行联网搜索（结果进上下文 + 缓存 key 带 web 维度）。
    """
    from app.utils.cache import get_cached_answer, set_cached_answer

    k = top_k or settings.top_k
    web_items, web_hash = _resolve_web(question, history, web_search)
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


async def stream_answer_cached(
    question: str,
    history: list[dict] | None,
    top_k: int | None,
    web_search: bool = False,
) -> AsyncIterator[tuple[str, dict]]:
    """带缓存的流式：SSE 事件协议不变（前端零改动）。

    缓存命中时回放：sources → 按 2 字切 delta（3ms 间隔，保留打字机效果）→ done。
    """
    import asyncio

    from app.utils.cache import get_cached_answer

    k = top_k or settings.top_k
    web_items, web_hash = _resolve_web(question, history, web_search)
    cached = get_cached_answer(question, history, k, bool(web_items), web_hash)
    if cached is not None:
        yield ("sources", {"sources": cached.get("sources", [])})
        answer = cached["answer"]
        for i in range(0, len(answer), 2):  # 切小块模拟打字机
            yield ("delta", {"content": answer[i : i + 2]})
            await asyncio.sleep(0.003)
        yield ("done", {"answer": answer, "source_count": len(cached.get("sources", []))})
        return

    sources_data: list[dict] = []
    async for ev, payload in stream_answer(question, history, top_k, web_items):
        yield ev, payload
        if ev == "sources":
            sources_data = payload.get("sources", [])
        if ev == "done" and payload.get("answer"):
            from app.utils.cache import set_cached_answer

            set_cached_answer(
                question,
                history,
                k,
                {"answer": payload["answer"], "sources": sources_data},
                bool(web_items),
                web_hash,
            )
