# 项目：Interview Helper（ProgramRAG）

基于 **RAG + Agent（ReAct 工具循环）** 的 **Interview Helper 系统**：上传面试资料（JD、简历、面经、参考答案）→ 向量化 → 面试问答（DeepSeek 生成，带引用溯源）。配套 interview-master skill 提供全流程面试辅导（岗位分析/简历优化/模拟面试/复盘/谈薪）。

## 面试辅导 Skill（必须使用）

项目根目录 `.claude/skills/interview-master/` 内置了 [interview-master](https://github.com/chen3tu/interview-master-skill) Skill，**专门用于本项目**。用户提到任何面试相关需求（面试准备、岗位分析、简历优化、模拟面试、面试复盘、薪资谈判、offer 选择、AI 面试等）时，必须先调用 `interview-master` skill，按其 SKILL.md 流程执行。它的 19 个 references 文件同时是 RAG 种子知识库来源（灌入脚本：`backend/scripts/seed_knowledge.py`）。

## 后端

- 技术栈：FastAPI + LangChain 1.x + Qdrant（local/server 双模式，**双集合分离**）+ BGE 嵌入（GPU）+ DeepSeek LLM + Qwen-VL 图片 OCR + Redis 缓存 + 自写限流中间件 + **联网搜索（Fetch MCP Server）**
- **Qdrant 双集合架构**：`kb_references`（面试 skill 参考种子，只读，170 chunks）与 `kb_documents`（用户上传，读写）分离——种子是"方法论/答案"，用户简历是"素材"，混存会互相干扰（简历被种子淹没），独立成库后用户文档独立召回并前置，缓存失效粒度也更准（种子不变不动版本号）。检索：用户库**按文档聚合召回**（相关文档 chunk 全量进上下文，每文档 ≤8，学 DeepSeek——简历等小文档完整引用不丢中间内容；hint 命中/简历特征词命中时优先全量）+ 参考库双路(RRF+Reranker) top_k，合并时用户在前。`GET /api/documents` 只列用户上传（种子不出现在列表）
- **Agent 架构（`AGENT_ENABLED=true`，默认）**：`backend/app/agent/` 用 **`langchain.agents.create_agent`** 工厂装配（engine.py，自带 invoke/astream 等输出方式），配 17 个自定义官方 `StructuredTool`（tools.py，content_and_artifact；args_schema 用 pydantic.create_model 从 fn 签名生成，否则 LLM 传错参数名）。**中间件**：官方 `langchain.agents.middleware`（middleware.py：`dynamic_prompt` 每请求注入长期记忆+系统规则、`before_model` 日志）。**记忆**：短期=会话历史注入消息上下文（conversations.py）；长期=`memories` SQLite 事实表（utils/memories.py，结构化抽取 extract_memories + dynamic_prompt 注入）。**提示词模板**：`backend/prompts/*.prompt` 外部文件 + `app/prompt/prompt_loader.py`。**结构化输出**：`with_structured_output(method="json_mode")`（DeepSeek v4-flash 不支持 response_format/tool_choice，json_mode 实测可用）。SSE 映射（streaming.py）：`create_agent.astream(stream_mode=[messages,updates])` → thought/tool_call/tool_result/sources/delta/done，`done` 携带 `tool_trace`。**打字机效果**：`create_agent` 对 OpenAI 兼容模型（DeepSeek）model 节点硬编码 `ainvoke`（`astream(messages)` 只产整段、无 token 增量）→ streaming.py 收尾把完整回答按 **6 字/块、25ms 间隔**逐块 yield delta 模拟打字机（缓存回放 generator.py 同节奏；前端 ChatView `pushDelta` + rAF 消费）。**完全 LLM 驱动**：联网/改写由 LLM 通过工具自主决定（已移除 `_should_web_search`/`_WEB_AUTO_RE`/`optimize_search_query`）。前端 MessageBubble 展示工具卡片+思考过程（可折叠）+重试；新增「🛠 工作台」tab（AgentWorkbench.vue：记忆/管道/提示词/工具/指标，数据来自 `/api/agent/*`）。Agent 回答缓存：`rag:agent:{kv}:sha16(question+history+top_k+web_hint)`，命中回放 tool_trace。传统 RAG 路径保留在 `AGENT_ENABLED=false` 回归对照。request_id 中间件（core/context.py ContextVar + core/log.py 日志注入）。**坑**：DeepSeek v4-flash 结构化输出必须 json_mode；Qdrant local 单实例——强杀进程后要删 `backend/data/qdrant/.lock` 否则新进程打不开；**宿主机 8000 端口被 Docker backend 容器占用时，前端 Vite 代理 `localhost` 会命中旧容器（无打字机）**——vite.config.js 代理已固定 `http://127.0.0.1:8000` 指向本地实例
- **会话本地持久化**：SQLite（`backend/data/conversations.db`，Docker 落在 `backend-data` 卷）存对话记录——`conversations`（含可空 `summary`）+ `messages`（含 `tool_trace` JSON 列，幂等 `ALTER` 迁移）两表，assistant 消息连 sources + tool_trace 一起存 JSON。**服务端权威历史**：`/api/chat`/`/chat/stream` 带 `conversation_id` 时读库取历史（不依赖前端回传，刷新/重开上下文仍在），用户消息先落库（生成失败不丢），回答完成后 assistant 落库。多轮记忆 = 检索侧 `rewrite_query` 改写 + 生成侧最近 8 条历史注入（缺一不可）。前端左侧会话栏（参考 DeepSeek 官网）切换/新建/删除会话，标题=首条消息后 **LLM 自动生成**（`agent_title_auto`，后台非阻塞，替换原截断逻辑）
- **联网搜索（`backend/app/rag/web_search.py`）**：后端作为 **MCP 客户端**（`mcp` SDK，stdio）连接 fetch MCP server 子进程（`mcp-server-fetch`，只有 `fetch` 工具）。搜索=用 **curl_cffi（impersonate="chrome"，模拟 Chrome TLS/JA3 指纹）** 抓 Bing 结果页解析 b_algo 直链，失败降级 httpx → 正文用 fetch 工具并行抓取（readability）。触发：**完全 LLM 驱动**——LLM 通过 `web_search`/`fetch_url` 工具自主决定（系统提示引导时效/天气/链接场景；前端「🌐 联网搜索」开关仅作系统提示 hint）。工具返回的网页结果合并进 sources（`is_web=True` + url）。缓存：页级 `rag:web:{sha}`（TTL 1800s）+ Agent 回答缓存 key 并入工具轨迹（联网/不联网互不污染）。**坑**：`mcp-server-fetch` 2026.7.10 用旧命名 `McpError`，必须钉 `mcp<2`（uvx 会装 mcp 2.x 导致 ImportError）；stdio enter/exit 须同 task（每次搜索内开→用→关，不做常驻会话）；`--ignore-robots-txt` + 浏览器 UA 才不会被站点拒；**Docker 容器里裸 httpx 抓 Bing 会被判 bot 返回 CAPTCHA 页（TLS 指纹问题，宿主正常）→ 必须 curl_cffi 模拟 Chrome**（requirements 已加 `curl_cffi`）；直接抓 URL 冷启动偶发 `ConnectError('')`，`fetch_url` 已重试一次
- conda 环境：`rag`（Python 3.10.8），运行前先 `conda activate rag`，用环境的 `python`
- 启动（本地，推荐：GPU 快 + 最新代码 + 打字机）：终端① `docker compose up -d redis`（只起 Redis；Qdrant 用本地 local 模式，**不要起 Docker backend**）→ `cd backend && conda activate rag && python -m uvicorn app.main:app --port 8000`；终端② `cd frontend && npm run dev`（Vite 5173，代理固定 127.0.0.1:8000）→ 浏览器 `http://localhost:5173`。**务必确认 `docker compose ps` 里 backend 为 stopped**，否则它霸占 `0.0.0.0:8000`、`localhost` 解析优先命中旧容器（无打字机、Qdrant 报被占用）
- 启动（Docker，全容器）：`docker compose up -d`（qdrant/redis/backend 三服务，backend 自动 seed 空 `kb_references`；embedding 用 CPU 慢；改代码后需 `docker compose build backend` 重建容器否则跑旧代码）
- 测试脚本：`smoke_test.py`（全链路）、`test_stream.py`（SSE，兼容 Agent 事件）、`test_agent_stream.py`（Agent SSE 协议）、`perf_check.py`（缓存/限流）、`eval_retrieval.py`（检索评估，需停服务）
- 配置：根目录 `.env`（DEEPSEEK_API_KEY/REDIS_URL/限流阈值/WEB_SEARCH_*/AGENT_* 等），详见 `backend/.env.example`
- 关键接口：`GET /api/health`、`POST /api/documents/upload`（**202 异步**，后台向量化 + 自动标签）、`GET /api/documents/{id}/status`、`GET/DELETE /api/documents`、`POST /api/chat`（带 X-Cache 头；`web_search` 字段在 Agent 下仅作联网 hint）、`POST /api/chat/stream`（SSE，Agent 下含 thought/tool_call/tool_result 事件）、`GET/POST /api/conversations`、`GET/DELETE /api/conversations/{id}`（会话本地持久化，assistant 消息含 tool_trace）
- 坑：Qdrant local 单实例（跑脚本前先停服务，uvicorn 必须 workers=1）；Qdrant local(SQLite) 与 server(RocksDB) 数据不兼容（compose 用全新卷 + 自动 seed）；Windows 控制台 GBK（脚本需 `sys.stdout.reconfigure(encoding="utf-8")`）；限流 chat 10 次/分钟/上传 5 次/分钟（RATE_LIMIT_* 可调）；**Docker backend 容器（0.0.0.0:8000）与本地 uvicorn（127.0.0.1:8000）同端口并存**——`localhost:8000` 优先命中 Docker 旧代码，前端必须走 `localhost:5173`（Vite 代理 127.0.0.1）；本地调试用 `127.0.0.1:8000` 而非 `localhost:8000`

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
