"""应用配置：pydantic-settings 读取根目录 .env（与 vision.js 共用）。"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（本文件位于 backend/app/utils/config.py，parents[3] = 项目根）
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM（DeepSeek，OpenAI 兼容）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"

    # 多模态（Qwen-VL-Max，DashScope OpenAI 兼容端点）
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vision_model: str = "qwen-vl-max"

    # Qdrant 向量库（local 本地落盘 | server 远程容器）
    # 两个集合分离：kb_references=面试 skill 参考（只读种子），kb_documents=用户上传（读写）。
    # 分离原因：种子是"方法论/答案"，用户简历是"素材"，语义相似度天然不匹配，
    # 混存会互相干扰（简历被种子淹没）；独立后互不污染、缓存失效粒度也更准。
    qdrant_mode: str = "local"
    qdrant_path: str = "backend/data/qdrant"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "kb_documents"  # 用户上传的简历/面经/资料
    qdrant_reference_collection: str = "kb_references"  # 面试 skill 参考（种子）

    # 嵌入模型
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    embedding_device: str = "auto"  # auto | cuda | cpu
    hf_endpoint: str = "https://hf-mirror.com"

    # 分块与检索
    chunk_size: int = 450
    chunk_overlap: int = 100
    top_k: int = 4

    # 混合检索（BM25 + RRF）
    enable_bm25: bool = True
    bm25_top_k: int = 10
    rrf_k: int = 60

    # Reranker 重排序
    enable_reranker: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = "auto"  # auto | cuda | cpu
    reranker_top_k: int = 8  # 进入 reranker 的候选数（实测 8 最优）

    # Query 优化与多轮对话
    query_rewrite: bool = True
    rewrite_model: str = ""  # 默认复用 model
    query_multiview: bool = False  # 多视角扩展（实验）
    history_turns: int = 6  # 改写时使用的历史消息上限
    hyde_enabled: bool = False  # HyDE（实验，默认关）

    # 生成
    max_tokens: int = 1024  # 防长回答失控

    # ---- Agent（ReAct / tool-calling 循环）----
    agent_enabled: bool = True  # 主开关：True 走 Agent 循环；False 回退传统 RAG（回归对照）
    agent_max_rounds: int = 6  # ReAct 循环最大决策轮次（LLM 工具调用轮）
    agent_max_retries: int = 2  # 单工具瞬时错误重试次数
    agent_timeout: int = 120  # 单次 Agent 运行整体超时（秒）
    agent_thought_enabled: bool = True  # 是否发 thought SSE 事件（仅状态行，非原始 CoT）
    agent_title_auto: bool = True  # 首条消息后自动生成会话标题（LLM）
    agent_compress_threshold: int = 20  # 历史消息数超过此值 → 压缩早期对话为摘要
    agent_plan_enabled: bool = True  # 注册 plan_tasks 工具 + 启用 Plan-and-Execute
    agent_trace_enabled: bool = True  # 是否把 tool_trace JSON 持久化到 SQLite messages

    # 缓存（Redis）
    redis_url: str = ""  # 空 = 禁用缓存；如 redis://localhost:6379/0
    cache_ttl: int = 900  # 回答缓存 TTL（秒），与知识版本号双保险

    # 联网搜索（后端作为 MCP 客户端连接 fetch MCP server；搜索=抓 Bing 结果页解析）
    web_search_enabled: bool = True  # 总开关
    # fetch server 启动命令。"python -m mcp_server_fetch" 会解析为当前解释器
    # （sys.executable，宿主/容器一致）；不用 uvx——它装最新 mcp 2.x 与 fetch 2026.7 不兼容
    web_fetch_command: str = "python -m mcp_server_fetch"
    web_fetch_ignore_robots: bool = True  # 默认忽略 robots.txt（默认 UA 会被大量站点拒绝）
    web_search_auto: bool = True  # 问题含时效敏感词时自动触发
    web_search_max_pages: int = 3  # 抓取正文的网页数
    web_search_page_length: int = 4000  # 每页正文截断字符数
    web_search_timeout: int = 20  # 单次搜索+抓取整体超时（秒）
    web_cache_ttl: int = 1800  # 搜索页级缓存 TTL（秒），防重复抓取

    # 限流
    rate_limit_chat_per_min: int = 10  # /api/chat 与 /chat/stream 共享
    rate_limit_upload_per_min: int = 5
    rate_limit_trust_proxy: bool = False  # true 时取 X-Forwarded-For（反代场景）

    # 其他
    upload_dir: str = "backend/data/uploads"
    conversations_db: str = "backend/data/conversations.db"  # 会话持久化（本地 SQLite）
    warmup_on_startup: bool = True
    max_upload_size_mb: int = 10  # 单文件大小上限（超限返回 413）

    @property
    def qdrant_path_abs(self) -> Path:
        p = Path(self.qdrant_path)
        return p if p.is_absolute() else ROOT_DIR / p

    @property
    def upload_dir_abs(self) -> Path:
        p = Path(self.upload_dir)
        return p if p.is_absolute() else ROOT_DIR / p

    @property
    def conversations_db_abs(self) -> Path:
        p = Path(self.conversations_db)
        return p if p.is_absolute() else ROOT_DIR / p

    def init_env(self) -> None:
        """必须在任何 transformers/huggingface_hub 导入之前调用。"""
        os.environ.setdefault("HF_ENDPOINT", self.hf_endpoint)


settings = Settings()
