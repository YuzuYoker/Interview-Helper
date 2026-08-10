"""Qdrant 向量存储封装：local/server 双模式 + 双集合（用户库/参考库）分离。

- kb_references：面试 skill 参考（只读种子，检索时提供方法论支持）；
- kb_documents：用户上传的简历/面经/资料（读写）。
两者分离，检索时各自独立召回再合并，互不污染。

local 模式（QDRANT_MODE=local）：数据落盘 backend/data/qdrant/，开发零 Docker 依赖；
部署时切 QDRANT_MODE=server + Docker 起 qdrant 容器，业务代码不变。
"""
from pathlib import Path
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    VectorParams,
)
from langchain_qdrant import QdrantVectorStore

from app.utils.config import settings
from app.utils.embedding import BGEZhEmbeddings

_client: Optional[QdrantClient] = None
_stores: dict[str, QdrantVectorStore] = {}  # collection -> store


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_mode == "local":
            _client = QdrantClient(path=str(settings.qdrant_path_abs))
        else:
            _client = QdrantClient(url=settings.qdrant_url)
    return _client


def ensure_collection(name: str, embeddings: BGEZhEmbeddings) -> None:
    """collection 不存在时按 embedding 维度创建（COSINE）。"""
    client = get_client()
    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config=VectorParams(
                size=embeddings.dim, distance=Distance.COSINE
            ),
        )


def get_store(
    embeddings: BGEZhEmbeddings,
    collection: Optional[str] = None,
) -> QdrantVectorStore:
    """按 collection 缓存 store。默认用户库（kb_documents）。"""
    name = collection or settings.qdrant_collection
    if name not in _stores:
        ensure_collection(name, embeddings)
        _stores[name] = QdrantVectorStore(
            client=get_client(),
            collection_name=name,
            embedding=embeddings,
        )
    return _stores[name]


def delete_document(doc_id: str, collection: Optional[str] = None) -> bool:
    """按 doc_id payload 过滤删除该文档的所有 chunk（默认用户库）。

    langchain-qdrant 1.x 存嵌套 payload（metadata.doc_id），过滤键需带点号路径。
    """
    name = collection or settings.qdrant_collection
    deleted = get_client().delete(
        name,
        points_selector=FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="metadata.doc_id", match=MatchValue(value=doc_id)
                    )
                ]
            )
        ),
    )
    return bool(deleted)


def collection_count(name: Optional[str] = None) -> int:
    try:
        return get_client().count(name or settings.qdrant_collection).count
    except Exception:
        return 0


def qdrant_ok() -> bool:
    """真实连通性检查（健康接口用）：能列出 collections 即连通。"""
    try:
        get_client().get_collections()
        return True
    except Exception:
        return False


def scroll_all_chunks(
    collection: Optional[str] = None,
    qdrant_filter: Optional[Filter] = None,
) -> list[dict]:
    """scroll 指定 collection 全量 chunk 的原始 payload（BM25 索引重建 / 标签过滤用）。

    不传 collection 时扫两个库合并（BM25 需要全局词频分布）。
    qdrant_filter：Qdrant Filter，如 metadata.tags 命中过滤（auto_tag 按标签检索）。
    """
    client = get_client()
    if collection:
        names = [collection]
    else:
        names = [settings.qdrant_collection, settings.qdrant_reference_collection]
    out: list[dict] = []
    for name in names:
        try:
            if not client.collection_exists(name):
                continue
            points, _ = client.scroll(
                name,
                limit=10000,
                with_payload=True,
                with_vectors=False,
                scroll_filter=qdrant_filter,
            )
            out.extend(p.payload or {} for p in points)
        except Exception:
            continue
    return out


def update_document_payload(
    doc_id: str, extra_meta: dict, collection: Optional[str] = None
) -> None:
    """给某文档的所有 chunk 追加 metadata（自动标签/关键词/摘要）。

    Qdrant set_payload 是**顶层浅合并**，嵌套的 metadata 对象必须整体重建：
    scroll 定位 → 拷贝旧 metadata → 合并 extra_meta → set_payload。
    """
    name = collection or settings.qdrant_collection
    client = get_client()
    try:
        if not client.collection_exists(name):
            return
        points, _ = client.scroll(
            name,
            limit=10000,
            with_payload=True,
            with_vectors=False,
            scroll_filter=Filter(
                must=[FieldCondition(key="metadata.doc_id", match=MatchValue(value=doc_id))]
            ),
        )
        if not points:
            return
        ids = [p.id for p in points]
        for p in points:
            payload = dict(p.payload or {})
            meta = dict(payload.get("metadata") or {})
            meta.update(extra_meta)
            payload["metadata"] = meta
            client.set_payload(name, payload=payload, points=[p.id])
    except Exception:
        pass  # best-effort，标签失败不影响主链路
