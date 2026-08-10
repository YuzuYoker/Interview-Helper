"""BM25 中文关键词索引：rank_bm25 + jieba 分词，从 Qdrant 懒重建。

- 身份对齐：point id = uuid5(page_content)（与 vectorstore 入库同规则），
  向量路与 BM25 路的融合天然对齐；
- 覆盖两个 collection（用户库 + 参考库，scroll_all_chunks 合并）；
  检索时按 metadata.type 区分目标库；
- 索引规模（当前 ~260 chunks）全量 scroll 重建毫秒级，无需持久化；
- 上传/删除文档后调用 invalidate()，下次 search 自动重建。
"""
import threading
import uuid
from typing import Optional

from langchain_core.documents import Document

from app.rag.vectorstore import scroll_all_chunks


def uuid5_id(content: str) -> str:
    """与 vectorstore.py 入库 point id 相同的确定性 ID。"""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, content))


class BM25Index:
    def __init__(self) -> None:
        self._bm25: Optional[object] = None
        self._ids: list[str] = []  # 与 tokenized 语料对齐的 uuid5
        self._docs: dict[str, Document] = {}  # id -> Document
        self._lock = threading.Lock()

    def _rebuild(self) -> None:
        """从两个 collection scroll 合并重建（payload 为 langchain-qdrant 嵌套结构）。"""
        import jieba  # 懒加载：首次 import ~1s 且打日志
        from rank_bm25 import BM25Okapi

        with self._lock:
            ids, docs = [], {}
            for payload in scroll_all_chunks():
                content = payload.get("page_content", "")
                if not content:
                    continue
                did = uuid5_id(content)
                if did in docs:
                    continue  # 跨库去重（内容相同视为同一 chunk）
                ids.append(did)
                docs[did] = Document(
                    page_content=content,
                    metadata=(payload.get("metadata") or {}),
                )
            self._ids = ids
            self._docs = docs
            self._bm25 = BM25Okapi(
                [jieba.lcut_for_search(d.page_content) for d in docs.values()]
            )

    def _ensure(self) -> None:
        if self._bm25 is None:
            self._rebuild()

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """返回 [(uuid5_id, bm25_score), ...] 按分数降序。"""
        import jieba

        self._ensure()
        scores = self._bm25.get_scores(jieba.lcut_for_search(query))
        top = scores.argsort()[::-1][:k]
        return [(self._ids[i], float(scores[i])) for i in top]

    def scores(self, query: str) -> dict[str, float]:
        """全量打分：id -> bm25 分数（毫秒级，供用户文档预筛使用）。"""
        import jieba

        self._ensure()
        scores = self._bm25.get_scores(jieba.lcut_for_search(query))
        return {did: float(x) for did, x in zip(self._ids, scores)}

    def doc_by_id(self, doc_id: str) -> Optional[Document]:
        self._ensure()
        return self._docs.get(doc_id)

    def invalidate(self) -> None:
        self._bm25 = None


bm25_index = BM25Index()
