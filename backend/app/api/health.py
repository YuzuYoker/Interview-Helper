"""健康检查接口：collect_health() 探针与 /health 端点共用（Agent diagnose_system 工具复用）。"""
import torch
from fastapi import APIRouter

from app.rag.vectorstore import qdrant_ok
from app.utils.config import settings
from app.utils.embedding import get_embedding_model

router = APIRouter()


def collect_health() -> dict:
    """聚合各组件状态（端点与 Agent 工具共用，避免逻辑重复）。"""
    from app.utils.cache import redis_ok

    embedding = get_embedding_model()  # 轻量对象，不触发模型加载
    return {
        "status": "ok",
        "embedding_model": settings.embedding_model,
        "embedding_loaded": embedding.is_loaded(),
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "qdrant_mode": settings.qdrant_mode,
        "qdrant_collection": settings.qdrant_collection,
        "qdrant_reference_collection": settings.qdrant_reference_collection,
        "qdrant_connected": qdrant_ok(),
        "redis_connected": redis_ok(),
        "llm_configured": bool(settings.deepseek_api_key),
        "llm_model": settings.model,
    }


@router.get("/health")
def health():
    return collect_health()
