"""检索封装：双库分离（用户库 + 参考库）各自召回后合并。

架构：
- kb_documents（用户上传的简历/面经/资料）：**按文档聚合召回**——最相关文档的
  chunk 全量进上下文（学 DeepSeek：上传文件整篇可引用，简历等小文档完整，
  避免"top N 截断"导致中间内容缺失）。量小不参与 rerank。
- kb_references（面试 skill 参考，种子）：双路检索（向量+BM25）→ RRF 融合 →
  Reranker 精排 → top_k。
- 合并顺序：用户文档在前（用户上传的资料是用户最关心的内容，LLM 优先参考），
  参考资料在后（方法论支持）。

流水线：
    question + history
      → query 优化（多轮改写，失败降级）
      → 用户库：向量打分 → 按 doc 聚合 → 相关文档 chunk 全量（每文档 ≤8）
      → 参考库 向量(k=cand)+BM25(k=top) → RRF → Reranker → top_k
      → 合并返回 user + ref
"""
from collections import defaultdict
from typing import Optional

from langchain_core.documents import Document

from app.rag.bm25_index import bm25_index, uuid5_id
from app.rag.vectorstore import get_store, scroll_all_chunks
from app.utils.config import settings
from app.utils.embedding import get_embedding_model

# 用户文档指代词：问题指向用户上传资料时，优先整个相关文档进上下文
USER_DOC_HINTS = ("简历", "我上传", "这份文档", "这个文件", "我的资料", "我的文件", "上传的")


def rrf_fuse(ranked_ids: list[list[str]], k: int | None = None) -> list[str]:
    """Reciprocal Rank Fusion：只依赖排名、免调参融合多路结果。"""
    k = k or settings.rrf_k
    scores: dict[str, float] = {}
    for lst in ranked_ids:
        for rank, did in enumerate(lst, start=1):
            scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank)
    return [did for did, _ in sorted(scores.items(), key=lambda x: -x[1])]


def _tag_filter(tags: list[str] | None):
    """Qdrant Filter：metadata.tags 命中任一标签。Agent 按标签过滤检索用。"""
    if not tags:
        return None
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    return Filter(must=[FieldCondition(key="metadata.tags", match=MatchAny(any=list(tags)))])


def _optimize_query(
    question: str,
    history: list[dict] | None,
    hyde_enabled: bool | None = None,
    query_multiview: bool | None = None,
    query_rewrite: bool | None = None,
) -> list[str]:
    """返回用于检索的 query 列表（可能多个视角）。全部失败降级原问题。

    显式参数优先；None 时读 settings（传统路径用配置开关，Agent 路径传显式值绕过）。
    """
    hyde = settings.hyde_enabled if hyde_enabled is None else hyde_enabled
    mv = settings.query_multiview if query_multiview is None else query_multiview
    rw = settings.query_rewrite if query_rewrite is None else query_rewrite

    if hyde:  # 实验项：HyDE 用假设文档检索
        from app.rag.query_optimizer import build_hyde

        h = build_hyde(question)
        return [h] if h else [question]

    if history:
        if mv:  # 实验项：多视角扩展
            from app.rag.query_optimizer import rewrite_multiview

            return rewrite_multiview(question, history) or [question]
        if rw:  # 多轮改写（默认路径）
            from app.rag.query_optimizer import rewrite_query

            return [rewrite_query(question, history) or question]
    return [question]


def _retrieve_user_docs(
    queries: list[str], k: int, tags: list[str] | None = None
) -> list[tuple[Document, float]]:
    """用户库（kb_documents）：按文档聚合召回，相关文档 chunk 全量进上下文。

    学 DeepSeek 官网：上传的文件整篇可被引用，简历等小文档完整覆盖，
    不因"top N 截断"丢中间内容。做法：
    1. 向量打分找出最相关 chunk → 按 doc_id 取**最佳 chunk 分**作为文档得分
       （取 max 而非 sum：避免"多块弱相关文档"以数量碾压"单块强相关文档"——
       如 4 块各 0.28 的简历总和 1.1，反而压过 1 块 0.55 的精准命中文档）；
       hint 指代词命中文件名额外加权。
    2. 取最相关 1-2 个文档，其 chunk **全量**返回（每文档上限 8，防大文档爆上下文）。
    """
    store = get_store(get_embedding_model(), collection=settings.qdrant_collection)
    qfilter = _tag_filter(tags)

    # 1. 向量打分 → 按 doc_id 聚合文档得分（取每个文档的最佳 chunk 分）
    doc_score: dict[str, float] = defaultdict(float)
    for q in queries:
        for _d, s in store.similarity_search_with_score(q, k=6, filter=qfilter):
            did = (_d.metadata.get("doc_id") or "")
            if did:
                doc_score[did] = max(doc_score.get(did, 0.0), s)

    # 2. scroll 用户库全部 chunk → 按 doc_id 分组（保持 chunk_index 顺序）
    docs_by_id: dict[str, list[Document]] = defaultdict(list)
    for payload in scroll_all_chunks(settings.qdrant_collection):
        meta = payload.get("metadata") or {}
        content = payload.get("page_content") or ""
        did = meta.get("doc_id") or ""
        if content and did:
            docs_by_id[did].append(Document(page_content=content, metadata=meta))
    for chunks in docs_by_id.values():
        chunks.sort(key=lambda d: d.metadata.get("chunk_index", 0))

    if not docs_by_id:
        return []

    # 3. 排序：hint 指代词命中文件名/简历特征词 → 排最前；否则按文档得分
    hint_hits = [h for h in USER_DOC_HINTS if h in queries[0]]
    # hint 含"简历"时按内容识别，保证简历文档被优先全量引用
    RESUME_MARKERS = (
        "教育背景", "实习经历", "项目经历", "求职意向",
        "自我评价", "专业技能", "个人总结", "联系方式", "主修课程",
    )

    def doc_rank(did: str) -> float:
        chunks = docs_by_id[did]
        fname = (chunks[0].metadata.get("filename") or "").lower()
        bonus = 0.0
        if hint_hits:
            if any(h.lower() in fname for h in hint_hits):
                bonus += 1000.0
            if "简历" in hint_hits and any(
                m in "".join(c.page_content for c in chunks) for m in RESUME_MARKERS
            ):
                bonus += 500.0
        return doc_score.get(did, 0.0) + bonus

    ranked_docs = sorted(docs_by_id, key=doc_rank, reverse=True)

    # 4. 取最相关 1-2 个文档，chunk 全量返回（每文档 ≤8）
    max_docs = 2 if hint_hits else 1
    max_per_doc = 8
    out: list[tuple[Document, float]] = []
    for did in ranked_docs[:max_docs]:
        for c in docs_by_id[did][:max_per_doc]:
            out.append((c, 0.02))  # 附件卡不显示分数，占位即可
    return out


def _retrieve_references(
    queries: list[str],
    k: int,
    enable_reranker: bool | None = None,
    enable_bm25: bool | None = None,
    tags: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """参考库（kb_references）：双路检索 + RRF + Reranker 精排。"""
    cand = settings.reranker_top_k
    rerank = settings.enable_reranker if enable_reranker is None else enable_reranker
    bm25_on = settings.enable_bm25 if enable_bm25 is None else enable_bm25
    qfilter = _tag_filter(tags)
    store = get_store(
        get_embedding_model(), collection=settings.qdrant_reference_collection
    )

    # 1. 双路检索
    dense_map: dict[str, Document] = {}
    dense_scores: dict[str, float] = {}
    sparse_ranks: list[list[str]] = []
    for q in queries:
        for d, s in store.similarity_search_with_score(q, k=cand, filter=qfilter):
            did = uuid5_id(d.page_content)
            dense_map[did] = d
            dense_scores[did] = max(dense_scores.get(did, 0.0), s)
    if bm25_on:
        for q in queries:
            ref_ids = []
            for did in bm25_index.search(q, settings.bm25_top_k):
                doc = bm25_index.doc_by_id(did)
                if doc and (doc.metadata.get("type") or "") == "interview-reference":
                    ref_ids.append(did)
            if ref_ids:
                sparse_ranks.append(ref_ids)

    # 2. RRF 融合（向量按相似度降序 + BM25 按分数降序）
    dense_rank = [
        did
        for did, _ in sorted(dense_scores.items(), key=lambda x: -x[1])
    ]
    fused = rrf_fuse([dense_rank, *sparse_ranks])[:cand]

    # 3. 构造候选（向量路优先，BM25 独有结果补充）
    candidates: list[tuple[Document, float]] = []
    for did in fused:
        doc = dense_map.get(did) or bm25_index.doc_by_id(did)
        if doc is not None:
            candidates.append((doc, dense_scores.get(did, 0.0)))

    # 4. Reranker 精排（可选）
    if rerank and candidates:
        from app.rag.reranker import reranker

        return reranker.rerank(queries[0], [d for d, _ in candidates], k)

    return candidates[:k]


def retrieve(
    question: str,
    k: int | None = None,
    history: list[dict] | None = None,
    collection: str = "auto",  # auto 用户+参考 | user 仅用户 | reference 仅参考
    enable_reranker: bool | None = None,
    enable_bm25: bool | None = None,
    hyde_enabled: bool | None = None,
    query_multiview: bool | None = None,
    query_rewrite: bool | None = None,
    tags: list[str] | None = None,
) -> list[tuple[Document, float]]:
    """检索入口。Agent 路径传入显式参数（绕过配置开关）；传统路径向后兼容。"""
    k = k or settings.top_k
    queries = _optimize_query(
        question, history, hyde_enabled, query_multiview, query_rewrite
    )

    user_hits: list[tuple[Document, float]] = []
    ref_hits: list[tuple[Document, float]] = []
    if collection in ("auto", "user"):
        user_hits = _retrieve_user_docs(queries, k=2, tags=tags)
    if collection in ("auto", "reference"):
        ref_hits = _retrieve_references(queries, k, enable_reranker, enable_bm25, tags)

    # 用户文档在前（用户上传的资料是用户最关心的内容，LLM 优先参考）；
    # 参考资料在后（方法论支持）。
    return user_hits + ref_hits
