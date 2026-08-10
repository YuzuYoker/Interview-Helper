"""中文分块配置：RecursiveCharacterTextSplitter。

chunk_size 450 / overlap 100 是 BGE 512 token 上限内的保险值（中文约 1 字 ≈ 1 token）。
separators 加入中文标点，避免长句被硬切。
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.utils.config import settings


def get_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        add_start_index=True,
    )
