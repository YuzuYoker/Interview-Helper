"""健康检查接口。"""
import torch
from fastapi import APIRouter

from app.rag.vectorstore import qdrant_ok
from app.utils.config import settings
from app.utils.embedding import get_embedding_model

router = APIRouter()


@router.get("/health")
def health():
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
