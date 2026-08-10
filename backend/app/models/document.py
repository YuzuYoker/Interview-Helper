"""文档相关 pydantic 模型。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    file_size: int
    chunk_count: int  # 异步上传时为 0，完成后查 status
    status: Literal["processing"]


class DocumentInfo(BaseModel):
    document_id: str
    filename: str
    created_at: datetime
    chunk_count: int
    status: str = "indexed"  # indexed | processing | error


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentInfo]


class TaskStatusResponse(BaseModel):
    document_id: str
    status: Literal["processing", "done", "error"]
    chunk_count: int
    error: Optional[str] = None
