"""FastAPI 入口：lifespan 初始化（HF_ENDPOINT → 后台预热模型）+ 中间件 + 路由注册。"""
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, conversations, documents, health
from app.api.agent_router import agent_router
from app.core.context import request_id_ctx_var
from app.utils.config import settings


def warmup() -> None:
    """后台预热：BGE 嵌入 + BM25 索引 + Reranker 模型。失败不影响启动，首次请求重试。"""
    try:
        from app.rag.bm25_index import bm25_index
        from app.rag.vectorstore import get_store
        from app.utils.embedding import get_embedding_model

        get_store(get_embedding_model())
        if settings.enable_bm25:
            bm25_index.search("预热", k=1)  # 触发索引重建
        if settings.enable_reranker:
            from app.rag.reranker import reranker

            reranker.warmup()  # 触发模型加载
    except Exception as e:  # 预热失败不影响服务启动，首次请求时重试
        print(f"[warmup] 模型预热失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.init_env()  # 1. HF_ENDPOINT 注入，先于任何 transformers 导入
    if settings.warmup_on_startup:
        threading.Thread(target=warmup, daemon=True).start()  # 2. 后台预热
    yield


app = FastAPI(title="RAG Knowledge Base", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """为每个请求注入 request_id（ContextVar），日志按请求追踪 agent 链路。"""
    request_id = str(uuid.uuid4())[:8]
    request_id_ctx_var.set(request_id)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 限流（CORS 之后、路由之前）
from app.utils.rate_limit import RateLimitMiddleware

app.add_middleware(RateLimitMiddleware)

for router in (health.router, documents.router, conversations.router, chat.router):
    app.include_router(router, prefix="/api")
app.include_router(agent_router)  # agent_router 自带 /api/agent 前缀

# 生产形态：前端构建产物存在时单端口托管（dev 阶段 dist 不存在则跳过）
from app.utils.config import ROOT_DIR

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/", StaticFiles(directory=str(FRONTEND_DIST), html=True),
        name="frontend",
    )
