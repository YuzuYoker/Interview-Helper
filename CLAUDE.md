# 项目：Interview Helper（ProgramRAG）

基于 RAG 的 **Interview Helper 系统**：上传面试资料（JD、简历、面经、参考答案）→ 向量化 → 面试问答（DeepSeek 生成，带引用溯源）。配套 interview-master skill 提供全流程面试辅导（岗位分析/简历优化/模拟面试/复盘/谈薪）。

## 面试辅导 Skill（必须使用）

项目根目录 `.claude/skills/interview-master/` 内置了 [interview-master](https://github.com/chen3tu/interview-master-skill) Skill，**专门用于本项目**。用户提到任何面试相关需求（面试准备、岗位分析、简历优化、模拟面试、面试复盘、薪资谈判、offer 选择、AI 面试等）时，必须先调用 `interview-master` skill，按其 SKILL.md 流程执行。它的 19 个 references 文件同时是 RAG 种子知识库来源（灌入脚本：`backend/scripts/seed_knowledge.py`）。

## 后端

- 技术栈：FastAPI + LangChain 1.x + Qdrant（local/server 双模式，**双集合分离**）+ BGE 嵌入（GPU）+ DeepSeek LLM + Qwen-VL 图片 OCR + Redis 缓存 + 自写限流中间件 + **联网搜索（Fetch MCP Server）**
- **Qdrant 双集合架构**：`kb_references`（面试 skill 参考种子，只读，170 chunks）与 `kb_documents`（用户上传，读写）分离——种子是"方法论/答案"，用户简历是"素材"，混存会互相干扰（简历被种子淹没），独立成库后用户文档独立召回并前置，缓存失效粒度也更准（种子不变不动版本号）。检索：用户库**按文档聚合召回**（相关文档 chunk 全量进上下文，每文档 ≤8，学 DeepSeek——简历等小文档完整引用不丢中间内容；hint 命中/简历特征词命中时优先全量）+ 参考库双路(RRF+Reranker) top_k，合并时用户在前。`GET /api/documents` 只列用户上传（种子不出现在列表）
- **会话本地持久化**：SQLite（`backend/data/conversations.db`，Docker 落在 `backend-data` 卷）存对话记录——`conversations` + `messages` 两表，assistant 消息连引用 sources 一起存 JSON。**服务端权威历史**：`/api/chat`/`/chat/stream` 带 `conversation_id` 时读库取历史（不依赖前端回传，刷新/重开上下文仍在），用户消息先落库（生成失败不丢），回答完成后 assistant 落库。多轮记忆 = 检索侧 `rewrite_query` 改写 + 生成侧 `_build_messages` 注入最近 8 条历史（缺一不可）。前端左侧会话栏（参考 DeepSeek 官网）切换/新建/删除会话，标题=首条问题摘要自动生成
- **联网搜索（`backend/app/rag/web_search.py`）**：后端作为 **MCP 客户端**（`mcp` SDK，stdio）连接 fetch MCP server 子进程（`mcp-server-fetch`，只有 `fetch` 工具）。搜索=用 **curl_cffi（impersonate="chrome"，模拟 Chrome TLS/JA3 指纹）** 抓 Bing 结果页解析 b_algo 直链，失败降级 httpx → 正文用 fetch 工具并行抓取（readability）。触发：前端「🌐 联网搜索」开关 OR `chat.py:_should_web_search` 时效敏感词正则（最新/今年/趋势/行情/天气…）+ **问题含 http(s):// URL 自动触发**。**特殊路径**：①问题带链接 → `generator._resolve_web` 直接 `fetch_url` 抓该页（不走 Bing）；②天气问句 → `optimize_search_query` 规范为"<城市>天气预报"（Bing 对"X天气如何"这类口语问句返回百科/攻略而非天气站，裸词才稳定命中天气站）。注入：`generator._prepare` 追加 web refs 与 sources（`is_web=True` + url），无命中时 web 结果兜底。缓存：页级 `rag:web:{sha}`（TTL 1800s）+ 回答缓存 key 并入 `web_search`+`web_hash`（联网/不联网互不污染）。**坑**：`mcp-server-fetch` 2026.7.10 用旧命名 `McpError`，必须钉 `mcp<2`（uvx 会装 mcp 2.x 导致 ImportError）；stdio enter/exit 须同 task（每次搜索内开→用→关，不做常驻会话）；`--ignore-robots-txt` + 浏览器 UA 才不会被站点拒；**Docker 容器里裸 httpx 抓 Bing 会被判 bot 返回 CAPTCHA 页（TLS 指纹问题，宿主正常）→ 必须 curl_cffi 模拟 Chrome**（requirements 已加 `curl_cffi`）；直接抓 URL 冷启动偶发 `ConnectError('')`，`fetch_url` 已重试一次
- conda 环境：`rag`（Python 3.10.8），运行前先 `conda activate rag`，用环境的 `python`
- 启动（宿主）：`cd backend && python -m uvicorn app.main:app --port 8000`（Redis 需先起：`docker run -d --name rag-redis -p 6379:6379 redis`）
- 启动（Docker）：`docker compose up -d`（qdrant/redis/backend 三服务，backend 自动 seed 空 `kb_references`）
- 测试脚本：`smoke_test.py`（全链路）、`test_stream.py`（SSE）、`perf_check.py`（缓存/限流）、`eval_retrieval.py`（检索评估，需停服务）
- 配置：根目录 `.env`（DEEPSEEK_API_KEY/REDIS_URL/限流阈值/WEB_SEARCH_* 等），详见 `backend/.env.example`
- 关键接口：`GET /api/health`、`POST /api/documents/upload`（**202 异步**，后台向量化）、`GET /api/documents/{id}/status`、`GET/DELETE /api/documents`、`POST /api/chat`（带 X-Cache 头，`web_search` 字段触发联网）、`POST /api/chat/stream`（SSE）、`GET/POST /api/conversations`、`GET/DELETE /api/conversations/{id}`（会话本地持久化）
- 坑：Qdrant local 单实例（跑脚本前先停服务，uvicorn 必须 workers=1）；Qdrant local(SQLite) 与 server(RocksDB) 数据不兼容（compose 用全新卷 + 自动 seed）；Windows 控制台 GBK（脚本需 `sys.stdout.reconfigure(encoding="utf-8")`）；限流 chat 10 次/分钟/上传 5 次/分钟（RATE_LIMIT_* 可调）

## 识图能力

你的底层模型不具备原生识图能力。遇到图片时，**不要用 Read 工具**，改用 vision.js：

```
node vision.js "<图片路径>" "用中文描述这张图片"
```

## 触发场景

- 用户分享图片路径（本地或网络 URL）
- 消息中出现 "Saved attachments:" 并列出图片
- 用户要求分析、描述、识别图片内容

## 配置好之后

用户直接发图片，自动识图，无需手动打命令。
