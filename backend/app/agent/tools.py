"""Agent 工具层：17 个自定义工具，包装为 `StructuredTool`（content_and_artifact）。

- 工具函数（_xxx）内部包装现有 RAG 能力（retriever / web_search / query_optimizer /
  loader / conversations / memories）；
- 对外暴露 `StructuredTool`：LLM 拿 content 文本，SSE 映射器从 artifact 取
  sources/summary/plan 等结构化数据（content_and_artifact 机制）。
"""
import json

from langchain_core.tools import StructuredTool

from app.agent.llm import get_structured_model
from app.utils.config import settings

__all__ = ["TOOL_FRIENDLY", "build_tools"]


# 工具名 → 前端友好中文（thought/卡片展示）
TOOL_FRIENDLY = {
    "retrieve_knowledge": "知识库检索",
    "web_search": "联网搜索",
    "fetch_url": "抓取网页",
    "rewrite_query": "查询改写",
    "hyde_retrieve": "HyDE 检索",
    "multiview_search": "多视角检索",
    "evaluate_search_result": "结果质量评估",
    "generate_title": "生成标题",
    "compress_history": "压缩历史",
    "summarize_conversation": "总结对话",
    "parse_document": "文档解析",
    "auto_tag": "自动标签",
    "store_memory": "写入记忆",
    "search_memory": "查询记忆",
    "diagnose_system": "系统诊断",
    "analyze_performance": "性能分析",
    "plan_tasks": "任务拆解",
}


# ---- 工具实现（内部包装现有 RAG 能力；懒 import 防启动拉重依赖）----


def _retrieve_knowledge(
    question: str,
    top_k: int = 4,
    collection: str = "auto",
    enable_reranker: bool = True,
    tags: list[str] | None = None,
) -> dict:
    """知识库检索。collection: auto 用户文档+参考资料；user 仅用户上传；reference 仅面试参考库。"""
    from app.rag import retriever
    from app.rag.generator import hits_to_sources

    hits = retriever.retrieve(
        question, k=top_k, collection=collection,
        enable_reranker=enable_reranker, tags=tags,
    )
    sources, refs = hits_to_sources(hits)
    if not sources:
        return {"content": "知识库中未检索到相关内容。", "summary": "知识库未检索到相关内容", "data": None}
    return {
        "content": refs,
        "summary": f"检索到 {len(sources)} 条知识库结果",
        "data": {"sources": [s.model_dump() for s in sources]},
    }


def _web_search(query: str) -> dict:
    """联网搜索（Bing）。query 由模型给出，建议简洁（天气类写成"<城市>天气预报"）。"""
    from app.models.chat import Source
    from app.rag import web_search as ws

    items = ws.web_search(query)
    if not items:
        return {"content": "联网搜索无结果。", "summary": "联网搜索无结果", "data": None}
    parts: list[str] = []
    sources: list[Source] = []
    for i, it in enumerate(items, 1):
        title = it.get("title") or "网页"
        url = it.get("url") or ""
        content = (it.get("content") or it.get("snippet") or "").strip()
        if not content:
            continue
        parts.append(f"[{i}] 来源：{title}（{url}）\n内容：{content}")
        sources.append(Source(index=i, content=content, filename=title, url=url, score=1.0, is_web=True))
    if not sources:
        return {"content": "联网搜索无结果。", "summary": "联网搜索无结果", "data": None}
    return {
        "content": "\n".join(parts),
        "summary": f"联网搜索到 {len(sources)} 条结果",
        "data": {"sources": [s.model_dump() for s in sources]},
    }


def _fetch_url(url: str) -> dict:
    """抓取指定网页正文（readability 提取）。"""
    from app.models.chat import Source
    from app.rag import web_search as ws

    items = ws.fetch_url(url)
    if not items:
        return {"content": "网页抓取失败或无内容。", "summary": "网页抓取失败", "data": None}
    it = items[0]
    content = (it.get("content") or it.get("snippet") or "").strip()
    title = it.get("title") or url
    if not content:
        return {"content": "网页抓取失败或无内容。", "summary": "网页抓取失败", "data": None}
    source = Source(index=1, content=content, filename=title, url=url, score=1.0, is_web=True)
    return {
        "content": f"[1] 来源：{title}（{url}）\n内容：{content}",
        "summary": "已抓取网页内容",
        "data": {"sources": [source.model_dump()]},
    }


def _rewrite_query(question: str, history: list[dict] | None = None) -> dict:
    """查询改写：补全多轮对话中指代。"""
    from app.rag.query_optimizer import rewrite_query

    r = rewrite_query(question, history or [])
    return {"content": r, "summary": "查询改写完成", "data": None}


def _hyde_retrieve(question: str, top_k: int = 4) -> dict:
    """HyDE 检索：先生成假设文档再用其检索。"""
    from app.rag import retriever
    from app.rag.generator import hits_to_sources
    from app.rag.query_optimizer import build_hyde

    hyde = build_hyde(question)
    hits = retriever.retrieve(hyde or question, k=top_k, collection="auto", hyde_enabled=False)
    sources, refs = hits_to_sources(hits)
    if not sources:
        return {"content": "知识库中未检索到相关内容。", "summary": "HyDE 检索无结果", "data": None}
    return {
        "content": refs,
        "summary": f"HyDE 检索到 {len(sources)} 条结果",
        "data": {"sources": [s.model_dump() for s in sources]},
    }


def _multiview_search(question: str, history: list[dict] | None = None, top_k: int = 4) -> dict:
    """多视角检索：改写为多个视角分别检索后合并（去重）。"""
    from app.rag import retriever
    from app.rag.generator import hits_to_sources
    from app.rag.query_optimizer import rewrite_multiview

    views = rewrite_multiview(question, history or []) or [question]
    seen: set[str] = set()
    all_hits: list = []
    for v in views:
        for d, s in retriever.retrieve(v, k=top_k, collection="auto", query_multiview=False):
            key = d.page_content[:120]
            if key in seen:
                continue
            seen.add(key)
            all_hits.append((d, s))
    sources, refs = hits_to_sources(all_hits)
    if not sources:
        return {"content": "知识库中未检索到相关内容。", "summary": "多视角检索无结果", "data": None}
    return {
        "content": refs,
        "summary": f"多视角检索到 {len(sources)} 条结果",
        "data": {"sources": [s.model_dump() for s in sources]},
    }


def _structured_llm() -> "BaseChatModel":
    """返回绑定结构化输出的 LLM（官方 with_structured_output）。"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.rewrite_model or settings.model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0,
        max_tokens=200,
        timeout=15,
    )


def _evaluate_search_result(question: str, items: list[dict]) -> dict:
    """评估搜索结果质量（官方 with_structured_output）。"""
    from app.agent.schemas import SearchEvaluation
    from langchain_core.messages import HumanMessage, SystemMessage

    text = "\n".join(
        f"- {it.get('title', '')} {it.get('url', '')}\n  {(it.get('snippet') or '')[:200]}"
        for it in (items or [])[:6]
    ) or "(空)"
    try:
        resp = get_structured_model(SearchEvaluation).invoke(
            [SystemMessage(content="你是搜索质量评估器。"),
             HumanMessage(content=f"问题：{question}\n搜索结果：\n{text}\n判断是否充分并给出建议，只输出 json。")]
        )
        out = resp.model_dump()
        return {"content": json.dumps(out, ensure_ascii=False), "summary": "搜索结果质量评估完成", "data": None}
    except Exception:
        return {"content": '{"quality":"good","reason":"评估失败，视为充分","suggest":"stop"}',
                "summary": "结果质量评估失败，按充分处理", "data": None}


def _generate_title(conversation_id: str, content: str) -> dict:
    """生成会话标题（官方 with_structured_output）。"""
    from app.agent.schemas import Title
    from app.utils import conversations
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        resp = get_structured_model(Title).invoke(
            [SystemMessage(content="你是对话标题生成器。"),
             HumanMessage(content=f"为以下用户消息生成 ≤20 字会话标题（只输出 json）：\n{content[:200]}")]
        )
        title = resp.title
    except Exception:
        title = ""
    if not title:
        title = content[:20]
    conversations.update_conversation_title(conversation_id, title)
    return {"content": title, "summary": f"已生成标题：{title}", "data": None}


def _compress_history(history: list[dict], target: str) -> dict:
    """压缩早期对话为摘要。"""
    from app.rag.query_optimizer import _llm
    from langchain_core.messages import HumanMessage, SystemMessage

    lines = "\n".join(f"{m.get('role', '')}: {str(m.get('content', ''))[:200]}" for m in (history or []))
    try:
        resp = _llm().invoke(
            [SystemMessage(content="你是对话压缩器，输出紧凑摘要。"),
             HumanMessage(content=f"把以下对话压缩成一段中文摘要（保留关键事实、用户背景、已答复结论）：\n{lines}")]
        )
        summary = (resp.content or "").strip()
    except Exception:
        summary = ""
    if not summary:
        return {"content": "", "summary": "历史压缩失败", "data": None}
    return {"content": summary, "summary": f"已压缩 {len(history or [])} 条历史消息", "data": None}


def _summarize_conversation(conversation_id: str) -> dict:
    """总结整个会话，写入 conversations.summary。"""
    from app.rag.query_optimizer import _llm
    from app.utils import conversations
    from langchain_core.messages import HumanMessage, SystemMessage

    conv = conversations.get_conversation(conversation_id)
    if not conv:
        return {"content": "会话不存在。", "summary": "会话不存在", "data": None}
    lines = "\n".join(f"{m['role']}: {str(m['content'])[:300]}" for m in conv["messages"][-40:])
    try:
        resp = _llm().invoke(
            [SystemMessage(content="你是对话总结器。"),
             HumanMessage(content=f"总结以下面试对话：双方要点、最终结论、遗留问题：\n{lines}")]
        )
        summary = (resp.content or "").strip()
    except Exception:
        summary = ""
    if summary:
        conversations.update_conversation_summary(conversation_id, summary)
    return {"content": summary or "总结失败。", "summary": "对话总结完成", "data": None}


def _parse_document(filename: str, content_preview: str = "") -> dict:
    """分析文档并推荐解析策略（官方 with_structured_output）。"""
    from app.agent.schemas import DocumentPlan
    from langchain_core.messages import HumanMessage, SystemMessage

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        resp = get_structured_model(DocumentPlan).invoke(
            [SystemMessage(content="你是文档解析顾问。"),
             HumanMessage(content=f"文档文件名：{filename}\n扩展名：{ext}\n内容预览：{content_preview[:300]}\n推荐解析策略。")]
        )
        return {"content": json.dumps(resp.model_dump(), ensure_ascii=False),
                "summary": "文档解析策略已生成", "data": None}
    except Exception:
        return {"content": f'{{"type":"{ext or "txt"}","strategy":"按扩展名默认解析","reason":"评估失败"}}',
                "summary": "文档解析策略（默认）", "data": None}


def _auto_tag(filename: str, content_preview: str = "", doc_id: str = "") -> dict:
    """为文档生成标签/关键词/摘要；doc_id 提供时写入 Qdrant payload。"""
    from app.agent.schemas import DocumentTags
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        resp = get_structured_model(DocumentTags).invoke(
            [SystemMessage(content="你是文档标签生成器。"),
             HumanMessage(content=f"为文档生成元数据。文件名：{filename}\n内容预览：{content_preview[:400]}")]
        )
        out = resp.model_dump()
        if doc_id and (out.get("tags") or out.get("keywords")):
            from app.rag.vectorstore import update_document_payload

            update_document_payload(doc_id, {"tags": out.get("tags", []), "keywords": out.get("keywords", []),
                                             "doc_summary": out.get("summary", "")})
        return {"content": json.dumps(out, ensure_ascii=False),
                "summary": f"已生成 {len(out.get('tags', []))} 个标签", "data": None}
    except Exception:
        return {"content": '{"tags":[],"keywords":[],"summary":""}', "summary": "自动标签失败", "data": None}


def _store_memory(key: str, content: str, category: str = "fact") -> dict:
    """写入长期记忆（SQLite 事实表）。key 语义化去重，新内容覆盖旧值。"""
    from app.agent.memory.long_term import save_memory

    save_memory(key=key, content=content, category=category)
    return {"content": f"已记住：{key} = {content}", "summary": f"已写入记忆：{key}", "data": None}


def _search_memory(query: str, category: str = "") -> dict:
    """检索长期记忆（按 key/内容模糊匹配）。"""
    from app.agent.memory.long_term import search_memories

    items = search_memories(query, category=category, limit=5)
    if not items:
        return {"content": "长期记忆中无相关内容。", "summary": "记忆无命中", "data": None}
    parts = [f"- [{m['category']}] {m['key']}：{m['content']}" for m in items]
    return {"content": "\n".join(parts), "summary": f"记忆命中 {len(items)} 条", "data": None}


def _diagnose_system() -> dict:
    """诊断系统组件状态（复用 /health 探针）。"""
    from app.api.health import collect_health

    data = collect_health()
    return {"content": json.dumps(data, ensure_ascii=False), "summary": "系统诊断完成", "data": None}


def _analyze_performance() -> dict:
    """分析性能指标。"""
    from app.utils.metrics import get_summary

    data = get_summary()
    return {"content": json.dumps(data, ensure_ascii=False), "summary": "性能分析完成", "data": None}


def _plan_tasks(question: str, history: list[dict] | None = None) -> dict:
    """把复杂问题拆解为子任务（官方 with_structured_output）。"""
    from app.agent.schemas import TaskPlan
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        resp = get_structured_model(TaskPlan).invoke(
            [SystemMessage(content="你是任务规划器。"),
             HumanMessage(content=f"问题：{question}\n请拆解为 ≤4 个子任务（若是简单问题则空列表）。")]
        )
        tasks = [t.model_dump() for t in resp.tasks]
        return {"content": json.dumps(tasks, ensure_ascii=False),
                "summary": f"已拆解为 {len(tasks)} 个子任务",
                "data": {"plan": tasks} if tasks else None}
    except Exception:
        return {"content": "[]", "summary": "任务拆解失败（按单轮处理）", "data": None}


# ---- 包装为官方 StructuredTool（content_and_artifact）----


def _to_structured(fn, name: str, description: str) -> StructuredTool:
    """把 dict 返回的工具函数包装为官方 StructuredTool。

    - 用 pydantic.create_model 从 fn 签名显式生成 args_schema（否则 **kwargs 会被
      推断成泛型 kwargs 参数，LLM 传错参数名导致工具失败）；
    - 官方 content_and_artifact：content=喂给 LLM 的文本；artifact=结构化数据
      （sources/summary/plan），SSE 映射器从 ToolMessage.artifact 读取。
    """
    import inspect
    from typing import Any

    from pydantic import create_model

    fields: dict[str, tuple] = {}
    for pname, p in inspect.signature(fn).parameters.items():
        ann = p.annotation if p.annotation is not inspect.Parameter.empty else Any
        default = p.default if p.default is not inspect.Parameter.empty else ...
        fields[pname] = (ann, default)
    schema = create_model(f"{name}_args", **fields) if fields else None

    def _run(**kwargs):
        try:
            out = fn(**kwargs) or {}
            return out.get("content", ""), {
                "ok": True,
                "summary": out.get("summary", ""),
                "data": out.get("data"),
            }
        except Exception as e:  # 工具异常 → 错误文本给 LLM，artifact 带 ok=False
            import logging
            logging.getLogger("interview.agent").warning("工具 %s 执行失败: %s", name, e)
            return f"[工具执行失败] {type(e).__name__}: {e}", {
                "ok": False, "summary": f"工具执行失败: {type(e).__name__}", "data": None,
            }

    return StructuredTool.from_function(
        func=_run,
        name=name,
        description=description,
        args_schema=schema,
        response_format="content_and_artifact",
    )


def build_tools() -> list[StructuredTool]:
    """构建全部官方 StructuredTool。"""
    return [
        _to_structured(_retrieve_knowledge, "retrieve_knowledge",
                       "从面试知识库检索与问题相关的资料（用户简历/面经 + 面试参考）。返回带 [n] 编号的引用内容。回答问题前通常先调用。"),
        _to_structured(_web_search, "web_search",
                       "联网搜索（Bing 中文）。适用于最新资讯/行情/趋势/天气等时效性问题。搜索词用简洁关键词，天气类写成\"<城市>天气预报\"。"),
        _to_structured(_fetch_url, "fetch_url", "抓取指定 URL 的网页正文。问题包含具体网页链接时使用。"),
        _to_structured(_rewrite_query, "rewrite_query", "多轮查询改写：补全历史对话中指代，生成独立检索词。"),
        _to_structured(_hyde_retrieve, "hyde_retrieve", "HyDE 检索：先假设理想答案再用其检索，提升低相似度召回。"),
        _to_structured(_multiview_search, "multiview_search", "多视角检索：把问题拆为多个角度分别检索后合并。"),
        _to_structured(_evaluate_search_result, "evaluate_search_result", "评估搜索结果质量，判断是否需补充搜索。"),
        _to_structured(_generate_title, "generate_title", "为会话生成标题。"),
        _to_structured(_compress_history, "compress_history", "压缩早期对话为摘要（长对话时节省上下文）。"),
        _to_structured(_summarize_conversation, "summarize_conversation", "总结整个对话（用户要求总结时调用）。"),
        _to_structured(_parse_document, "parse_document", "分析文档并推荐解析策略（扩展名不常见或解析异常时）。"),
        _to_structured(_auto_tag, "auto_tag", "为文档生成标签/关键词/摘要。"),
        _to_structured(_store_memory, "store_memory",
                       "把关于用户的重要事实写入长期记忆（如「我的目标岗位是后端」「我期望薪资 25k」）。新内容会覆盖同 key 旧值。"),
        _to_structured(_search_memory, "search_memory", "检索长期记忆（用户画像/偏好/跨会话关键结论）。"),
        _to_structured(_diagnose_system, "diagnose_system", "诊断系统组件状态并给出修复建议（用户询问系统健康时）。"),
        _to_structured(_analyze_performance, "analyze_performance", "分析缓存命中率与耗时等性能指标。"),
        _to_structured(_plan_tasks, "plan_tasks", "把复杂问题拆解为可执行的子任务列表（Plan-and-Execute）。"),
    ]
