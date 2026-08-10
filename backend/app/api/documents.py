"""文档管理接口：异步上传（后台向量化）/ 列表 / 状态查询 / 删除。"""
import uuid
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.document import (
    DocumentInfo,
    DocumentListResponse,
    TaskStatusResponse,
    UploadResponse,
)
from app.rag.loader import LOADERS
from app.rag.vectorstore import delete_document, get_client
from app.utils.cache import incr_kv
from app.utils.config import settings
from app.utils.task_manager import get_task, submit_upload

router = APIRouter()


@router.post("/documents/upload", response_model=UploadResponse, status_code=202)
def upload_document(file: UploadFile = File(...)):
    """立即返回 202，向量化在后台线程执行（POST 后轮询 status）。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in LOADERS:
        raise HTTPException(
            400, f"不支持的文件类型: {ext}（支持 {' '.join(LOADERS)}）"
        )
    if ext == ".doc":
        raise HTTPException(400, "不支持旧版 .doc 格式，请先转换为 .docx")

    # 大小限制：一次读完校验，超限 413 并清理已建目录
    content = file.file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            413,
            f"文件过大（{len(content) / 1048576:.1f}MB），最大支持 {settings.max_upload_size_mb}MB",
        )

    doc_id = str(uuid4())
    save_dir = settings.upload_dir_abs / doc_id
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / (file.filename or f"upload{ext}")
    save_path.write_bytes(content)

    submit_upload(doc_id, save_path, file.filename or "")
    return UploadResponse(
        document_id=doc_id,
        filename=file.filename or "",
        file_size=save_path.stat().st_size,
        chunk_count=0,
        status="processing",
    )


@router.get("/documents/{document_id}/status", response_model=TaskStatusResponse)
def document_status(document_id: str):
    task = get_task(document_id)
    if task is None:
        raise HTTPException(404, f"任务不存在: {document_id}")
    return TaskStatusResponse(
        document_id=task["document_id"],
        status=task["status"],
        chunk_count=task["chunk_count"],
        error=task.get("error") or None,
    )


@router.get("/documents", response_model=DocumentListResponse)
def list_documents():
    client = get_client()
    # langchain-qdrant 1.x 将 payload 存为嵌套结构 {page_content, metadata:{...}}
    points, _ = client.scroll(
        settings.qdrant_collection,
        limit=10000,
        with_payload=True,
        with_vectors=False,
    )
    by_doc: dict[str, dict] = {}
    for p in points:
        meta = (p.payload or {}).get("metadata") or {}
        pid = meta.get("doc_id")
        if not pid:
            continue
        item = by_doc.setdefault(pid, {"chunk_count": 0, "created_at": None, "filename": ""})
        item["chunk_count"] += 1
        item["filename"] = meta.get("filename") or item["filename"]
        item["created_at"] = meta.get("created_at") or item["created_at"]

    docs = [
        DocumentInfo(
            document_id=doc_id,
            filename=info["filename"],
            created_at=datetime.fromisoformat(info["created_at"])
            if info["created_at"]
            else datetime.now(),
            chunk_count=info["chunk_count"],
            status="indexed",
        )
        for doc_id, info in by_doc.items()
    ]
    docs.sort(key=lambda d: d.created_at, reverse=True)

    # 合并后台任务（processing/error 尚未入库，需从任务表补全展示）
    from app.utils.task_manager import list_tasks

    for task in list_tasks():
        if task["status"] == "processing" or (
            task["status"] == "error"
            and all(d.document_id != task["document_id"] for d in docs)
        ):
            docs.append(
                DocumentInfo(
                    document_id=task["document_id"],
                    filename=task["filename"],
                    created_at=datetime.fromisoformat(task["created_at"]),
                    chunk_count=task["chunk_count"],
                    status=task["status"],
                )
            )
    return DocumentListResponse(total=len(docs), documents=docs)


@router.delete("/documents/{document_id}")
def delete_document_api(document_id: str):
    task = get_task(document_id)
    if task is not None and task["status"] == "processing":
        raise HTTPException(409, "文档正在处理中，请稍后再试")

    deleted = delete_document(document_id)
    if not deleted:
        raise HTTPException(404, f"文档不存在或已删除: {document_id}")

    from app.rag.bm25_index import bm25_index

    bm25_index.invalidate()  # 删除后重建 BM25 索引
    incr_kv()  # 知识版本号 +1 → 缓存全部失效
    return {"deleted": document_id}
