"""bge-reranker-v2-m3 重排序：sentence-transformers CrossEncoder（5.7.0 已含）。

- 必须用 CrossEncoder 加载（SentenceTransformer 加载会 mean-pooling 错误）；
- 原始输出是 logits（无界），activation_fn=Sigmoid() 映射到 0-1 供 Source.score 展示；
- fp16 仅在 cuda 时启用（568M → ~1.1GB 显存，与 embedding 共存 6GB 无压力）。
"""
import threading
from typing import Optional

import torch
from langchain_core.documents import Document
from sentence_transformers import CrossEncoder

from app.utils.config import settings


class Reranker:
    _model: Optional[CrossEncoder] = None
    _lock = threading.Lock()

    def _load(self) -> CrossEncoder:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    dev = (
                        settings.reranker_device
                        if settings.reranker_device != "auto"
                        else ("cuda" if torch.cuda.is_available() else "cpu")
                    )
                    kwargs = {}
                    if dev == "cuda":
                        kwargs = {"model_kwargs": {"torch_dtype": torch.float16}}
                    self._model = CrossEncoder(
                        settings.reranker_model,
                        device=dev,
                        max_length=1024,  # chunk 450 字，1024 足够
                        activation_fn=torch.nn.Sigmoid(),  # logits -> 0-1
                        **kwargs,
                    )
        return self._model

    def is_loaded(self) -> bool:
        return self._model is not None

    def warmup(self) -> None:
        """触发模型加载（预热用）。"""
        self._load()

    def rerank(
        self, query: str, docs: list[Document], k: int
    ) -> list[tuple[Document, float]]:
        if not docs:
            return []
        model = self._load()
        results = model.rank(
            query, [d.page_content for d in docs], top_k=k, batch_size=16
        )
        return [(docs[r["corpus_id"]], float(r["score"])) for r in results]


reranker = Reranker()
