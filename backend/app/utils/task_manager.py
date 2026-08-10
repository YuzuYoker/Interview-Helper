"""异步上传任务管理器：单 worker 线程池 + 内存任务状态。

- max_workers=1：上传串行化（torch encode 并发不安全、BM25 重建互斥）；
- 任务状态存内存 dict（单机够用；多实例扩展时换 Redis 是面试话术）；
- 完成超过 1 小时的任务自动清理（内存有界）。
"""
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.utils.config import settings

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")

# document_id -> TaskState
_tasks: dict[str, dict] = {}
_lock = threading.Lock()
_TASK_TTL = 3600  # 完成状态保留 1 小时


def _new_state(document_id: str, filename: str) -> dict:
    return {
        "document_id": document_id,
        "filename": filename,
        "status": "processing",  # processing | done | error
        "chunk_count": 0,
        "error": "",
        "created_at": datetime.now().isoformat(),
        "finished_at": None,
    }


def get_task(document_id: str) -> Optional[dict]:
    with _lock:
        return _tasks.get(document_id)


def list_tasks() -> list[dict]:
    """返回全部任务副本（含 processing/error，供列表接口合并展示）。"""
    with _lock:
        return [dict(t) for t in _tasks.values()]


def _cleanup() -> None:
    """剔除完成超过 TTL 的任务。"""
    now = time.time()
    stale = [
        did
        for did, t in _tasks.items()
        if t["status"] != "processing" and t.get("_finished_ts")
        and now - t["_finished_ts"] > _TASK_TTL
    ]
    for did in stale:
        _tasks.pop(did, None)


def submit_upload(document_id: str, save_path: Path, filename: str) -> dict:
    """提交上传任务：任务体 = 原同步流水线（加载→分块→向量化→入库）。"""
    with _lock:
        state = _new_state(document_id, filename)
        _tasks[document_id] = state

    def run() -> None:
        try:
            from app.rag.loader import LOADERS
            from app.rag.splitter import get_splitter
            from app.rag.vectorstore import get_store
            from app.utils.cache import incr_kv
            from app.utils.embedding import get_embedding_model

            ext = save_path.suffix.lower()
            docs = LOADERS[ext](str(save_path), filename)
            if not docs:
                raise ValueError("未能从文件中提取到任何文本内容")

            chunks = get_splitter().split_documents(docs)
            now = datetime.now().isoformat()
            for i, c in enumerate(chunks):
                c.metadata.update(
                    {
                        "doc_id": document_id,
                        "filename": filename,
                        "chunk_index": i,
                        "created_at": now,
                    }
                )

            import uuid

            ids = [
                str(uuid.uuid5(uuid.NAMESPACE_DNS, c.page_content))
                for c in chunks
            ]
            get_store(get_embedding_model()).add_documents(chunks, ids=ids)

            from app.rag.bm25_index import bm25_index

            bm25_index.invalidate()  # 新文档入库后重建 BM25 索引
            incr_kv()  # 知识版本号 +1 → 缓存全部失效

            with _lock:
                state.update(
                    status="done",
                    chunk_count=len(chunks),
                    finished_at=datetime.now().isoformat(),
                    _finished_ts=time.time(),
                )
        except Exception as e:
            shutil.rmtree(save_path.parent, ignore_errors=True)  # 清理落盘文件
            with _lock:
                state.update(
                    status="error",
                    error=str(e),
                    finished_at=datetime.now().isoformat(),
                    _finished_ts=time.time(),
                )

    _pool.submit(run)
    _cleanup()
    return state
