"""BGE 中文嵌入客户端：懒加载单例 + GPU 自动检测 + 查询指令前缀。

bge-large-zh-v1.5 官方推荐检索时在查询侧加指令前缀
"为这个句子生成表示以用于检索相关文章："，HuggingFaceEmbeddings 不支持该参数，
故自写（langchain-huggingface 保留备用）。
"""
import threading
from typing import Optional

import torch
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, PrivateAttr
from sentence_transformers import SentenceTransformer

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class BGEZhEmbeddings(BaseModel, Embeddings):
    """BGE 中文嵌入客户端。模型在第一次 encode 时才真正加载（懒加载）。"""

    model_name: str = "BAAI/bge-large-zh-v1.5"
    device: Optional[str] = None  # None => 自动检测

    _model: Optional[SentenceTransformer] = PrivateAttr(default=None)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            with self._lock:  # 双检锁，防并发重复加载
                if self._model is None:
                    dev = self.device or (
                        "cuda" if torch.cuda.is_available() else "cpu"
                    )
                    self._model = SentenceTransformer(self.model_name, device=dev)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        return model.encode(
            texts, normalize_embeddings=True, batch_size=16
        ).tolist()

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        return model.encode(
            QUERY_INSTRUCTION + text, normalize_embeddings=True
        ).tolist()

    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def dim(self) -> int:
        return len(self.embed_query("测试"))  # bge-large-zh-v1.5 => 1024


_embedding: Optional[BGEZhEmbeddings] = None


def get_embedding_model() -> BGEZhEmbeddings:
    global _embedding
    if _embedding is None:
        _embedding = BGEZhEmbeddings()  # device=None，_load 时自动检测
    return _embedding
