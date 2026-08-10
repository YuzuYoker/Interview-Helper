"""Agent 工作台路由：记忆/提示词/管道(中间件)/工具/指标 的只读+管理接口。

供前端「Agent 工作台」面板展示 agent 内部结构（可观测性）。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils import memories as memories_db

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])


# ---- 长期记忆管理 ----


class MemoryIn(BaseModel):
    category: str = "fact"
    key: str
    content: str
    source: str = ""


@agent_router.get("/memories")
def list_memories(category: str = ""):
    return memories_db.list_memories(category)


@agent_router.get("/memories/stats")
def memory_stats():
    return memories_db.memory_stats()


@agent_router.post("/memories")
def create_memory(body: MemoryIn):
    return memories_db.save_memory(body.key, body.content, body.category, body.source)


@agent_router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int):
    if not memories_db.delete_memory(memory_id):
        raise HTTPException(404, f"记忆不存在: {memory_id}")
    return {"deleted": memory_id}


# ---- 工具注册表 / 提示词模板 / 管道 / 指标 ----


@agent_router.get("/tools")
def list_tools():
    """17 个自定义工具（StructuredTool）的名称/描述/参数 schema。"""
    from app.agent.tools import TOOL_FRIENDLY, build_tools

    return [
        {
            "name": t.name,
            "label": TOOL_FRIENDLY.get(t.name, t.name),
            "description": t.description,
            "args": t.args,
        }
        for t in build_tools()
    ]


@agent_router.get("/prompts")
def list_prompts():
    """backend/prompts/*.prompt 模板。"""
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
    out = []
    if prompts_dir.exists():
        for p in sorted(prompts_dir.glob("*.prompt")):
            out.append({"name": p.stem, "content": p.read_text(encoding="utf-8")})
    return out


@agent_router.get("/middleware")
def list_pipeline():
    """Agent 中间件清单（langchain.agents.middleware）+ 应用中间件。"""
    return {
        "agent_framework": "langchain.agents.create_agent",
        "middleware": ["dynamic_prompt(记忆注入)", "before_model(日志)"],
        "tools": ["retrieve_knowledge", "web_search", "fetch_url", "rewrite_query",
                  "hyde_retrieve", "multiview_search", "evaluate_search_result",
                  "generate_title", "compress_history", "summarize_conversation",
                  "parse_document", "auto_tag", "store_memory", "search_memory",
                  "diagnose_system", "analyze_performance", "plan_tasks"],
        "app_middleware": ["CORSMiddleware", "RateLimitMiddleware", "request_id"],
    }


@agent_router.get("/metrics")
def agent_metrics():
    from app.utils.metrics import get_summary

    return {"performance": get_summary(), "memories": memories_db.memory_stats()}
