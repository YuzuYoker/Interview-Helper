# Interview Helper · RAG 知识库系统

基于 RAG（检索增强生成）的 **Interview Helper**：上传面试资料（JD/简历/面经/参考答案），问答式获取面试支持，回答**强制带引用溯源**，支持流式打字机、多轮对话、多会话本地持久化与**联网搜索**。

> 想直接上手？跳到 [四、从零开始部署（教程）](#四从零开始部署教程)，两条路任选：**Docker 一键**（推荐）或 **Conda 从零搭建**（本地开发）。

---

## 目录

- [一、功能特性](#一功能特性)
- [二、架构](#二架构)
- [三、项目结构](#三项目结构)
- [四、从零开始部署（教程）](#四从零开始部署教程)
  - [4.0 两条部署路线怎么选](#40-两条部署路线怎么选)
  - [4.1 准备清单（硬件 / 软件 / 密钥）](#41-准备清单硬件--软件--密钥)
  - [4.2 安装基础软件](#42-安装基础软件)
  - [4.3 申请 API 密钥](#43-申请-api-密钥)
  - [4.4 获取项目代码并配置 .env](#44-获取项目代码并配置-env)
  - [4.5 方式 A：Docker 一键部署（推荐）](#45-方式-adocker-一键部署推荐)
  - [4.6 方式 B：Conda 从零搭建（本地开发）](#46-方式-bconda-从零搭建本地开发)
- [五、验证部署](#五验证部署)
- [六、配置说明（根目录 .env）](#六配置说明根目录-env)
- [七、API 一览](#七api-一览)
- [八、性能指标（实测）](#八性能指标实测)
- [九、常见问题 / 排错](#九常见问题--排错)
- [十、更多文档](#十更多文档)

---

## 一、功能特性

- 📄 **多格式资料入库**：PDF / Word / Excel / txt / 图片（图片自动走 Qwen-VL-Max OCR 转文字）
- 🧩 **双库分离**：`kb_references`（面试 skill 参考，只读种子）+ `kb_documents`（你上传的资料，读写）——系统方法论与个人素材分库，检索互不污染，你的文档稳定命中并优先引用
- 🎯 **混合检索**：向量检索（BGE 语义）+ BM25 关键词，RRF 融合，Reranker 精排 —— 检索 Hit@1 **94%**（50 题评估集）
- 📚 **引用溯源**：回答标注 [1][2]，点击展开原文卡片 + 置信度评分（你上传的文档标注 📎 附件并排在最前）
- ⚡ **流式输出**：SSE 打字机效果
- 💬 **多轮对话**：Query 改写自动补全指代（"那公司压价怎么办？"→ 命中谈薪资料）；回答生成注入最近对话历史，衔接前文
- 💾 **会话本地持久化**：SQLite 落库（`backend/data/conversations.db`），左侧会话栏切换历史对话（参考 DeepSeek 官网），刷新/重开保留完整上下文与引用
- 🌐 **联网搜索（Fetch MCP Server）**：Agent 模式下由 LLM 通过 `web_search`/`fetch_url` 工具自主决定（系统提示引导时效/天气/链接场景；输入框「🌐 联网搜索」开关仅作提示）——Bing 搜最新网页 → fetch MCP server 抓正文 → 与知识库合并引用（🌐 卡片带原文链接），**无 API key**。天气问句自动规范为"<城市>天气预报"保证命中天气站；直接发链接则抓该网页内容
- 🚀 **性能优化**：Redis 回答缓存（命中 <50ms，加速 2000x）、异步向量化上传（202 立即返回）、滑动窗口限流
- 🐳 **一键部署**：docker compose 三服务（后端 + Qdrant + Redis），空库自动 seed

---

## 二、架构

```
浏览器 (Vue3) ──► FastAPI (8000)
                    ├─ Agent 层（backend/app/agent/，AGENT_ENABLED=true 默认）
                    │    ├─ engine.py      # langchain.agents.create_agent 官方装配（ReAct 循环）
                    │    ├─ tools.py       # 17 个官方 StructuredTool（检索/联网/记忆/文档/系统）
                    │    ├─ middleware.py  # langchain.agents.middleware（记忆注入/日志）
                    │    ├─ streaming.py   # astream → SSE（thought/tool_call/tool_result/sources/delta/done）
                    │    └─ memory/        # 长期记忆 SQLite 事实表；短期=会话历史注入
                    ├─ Redis 缓存层（回答缓存 + 知识版本号失效，只随用户库变化）
                    ├─ Qdrant 双集合
                    │    ├─ kb_references 面试 skill 参考（只读种子）
                    │    └─ kb_documents 你上传的资料（读写）
                    ├─ SQLite 会话存储（conversations.db —— 对话记录本地持久化）
                    ├─ Fetch MCP Server 子进程（联网搜索：Bing 结果页 + 网页正文提取）
                    ├─ BM25 关键词索引（内存，jieba 分词，覆盖两库）
                    ├─ Reranker 精排（bge-reranker-v2-m3，参考库双路用）
                    ├─ DeepSeek LLM（回答生成）
                    └─ Qwen-VL-Max（图片 OCR）

Agent 流程：LLM 通过工具自主决策（检索/联网/改写/记忆…），官方循环执行工具、结果回送，直至输出答案 → SSE 流式推送 + tool_trace
对话流程：服务端权威历史（读库）→ Agent → 用户/助手消息落库，多会话切换
```

---

## 三、项目结构

```
ProgramRAG/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── api/              # 路由：chat / documents / conversations / health / agent 工作台
│   │   ├── agent/            # Agent 层：engine(create_agent) / tools(17 工具) / middleware / streaming / memory
│   │   ├── rag/              # 检索/生成/联网搜索核心
│   │   ├── prompt/           # 提示词模板加载（backend/prompts/*.prompt）
│   │   ├── models/           # Pydantic 模型
│   │   ├── utils/            # 配置 / Redis 缓存 / 限流 / embedding / memories
│   │   └── main.py           # 入口（lifespan 预热模型 + 静态托管前端 dist）
│   ├── scripts/              # seed_knowledge.py / smoke_test.py / eval_retrieval.py…
│   ├── data/                 # qdrant / uploads / conversations.db（运行时生成）
│   ├── requirements.txt
│   └── .env.example          # 配置模板
├── frontend/                 # Vue3 前端（Vite）
├── .claude/skills/interview-master/   # 面试辅导 Skill，其 references 是种子知识库来源
├── docker-compose.yml        # backend + qdrant + redis
├── Dockerfile                # 多阶段：node 构建前端 → python 运行
└── docker-entrypoint.sh      # 等 qdrant → 自动 seed → 启动 uvicorn
```

---

## 四、从零开始部署（教程）

### 4.0 两条部署路线怎么选

| | 方式 A：Docker（推荐） | 方式 B：Conda 从零搭建 |
|---|---|---|
| 适合 | 想最快跑起来 / 部署到服务器 | 本地开发、调试代码、面试演示 |
| 前置 | 只要装 Docker | 要装 Miniconda + Node.js |
| 命令 | 一条 `docker compose up -d` | 一步步 pip 安装 |
| 模型缓存 | 卷内（首次需下载） | 宿主 HF 缓存 |
| 缺点 | 镜像构建较慢（torch CPU 版） | 环境配置步骤多 |

> 两条路都能得到同一套系统。教程按**先做公共准备（4.1~4.4），再选一条路线**组织。

### 4.1 准备清单（硬件 / 软件 / 密钥）

**硬件要求**

| 项 | 要求 | 说明 |
|---|---|---|
| 内存 | ≥ 8GB 推荐 16GB | 本地模型（embedding + reranker）加载约需 3-5GB |
| GPU | 可选（NVIDIA，有 CUDA 更好） | 无 GPU 则 CPU 运行，向量化/检索会慢一些（可用但体验一般） |
| 磁盘 | ≥ 10GB 空闲 | 模型 ~3GB + Docker 镜像 ~5GB |

**软件清单**（按你选的路线准备）

| 软件 | 方式 A | 方式 B | 去哪装 |
|---|---|---|---|
| Docker Desktop / Docker Engine | ✅ 必装 | —（Redis 可选时用到） | https://www.docker.com/products/docker-desktop/ |
| Miniconda（Python 3.10） | — | ✅ 必装 | https://docs.conda.io/en/latest/miniconda.html |
| Git | 可选 | 可选 | https://git-scm.com/downloads |
| Node.js ≥ 18 | 不用（镜像内自动构建） | 前端需构建/开发时才用 | https://nodejs.org/ |

**账号 / 密钥**

| 密钥 | 是否必填 | 用途 | 申请 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ 必填 | LLM 生成回答（无它问答接口返回 503） | https://platform.deepseek.com |
| `DASHSCOPE_API_KEY` | 可选 | 图片 OCR（Qwen-VL-Max），不传图片可跳过 | https://dashscope.console.aliyun.com |

### 4.2 安装基础软件

按 4.1 表格里的"去哪装"，下载对应安装包按提示装好即可：

- **Windows / macOS**：安装包均为图形界面，双击下一步；安装完成后**重开一个终端**让环境变量生效。
- **Linux（Ubuntu）**：Miniconda 用终端命令安装：
  ```bash
  wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
  bash Miniconda3-latest-Linux-x86_64.sh   # 一路回车 + yes
  # 重开终端后
  conda --version                          # 出现版本号即安装成功
  ```
- **Docker**：Windows/Mac 装 Docker Desktop（装完**重启一次**）；Linux 用发行版包管理器安装，然后：
  ```bash
  sudo systemctl enable --now docker
  docker --version
  ```

### 4.3 申请 API 密钥

1. 打开 https://platform.deepseek.com → 注册/登录 → 左侧「API Keys」→ 创建密钥（形如 `sk-...`），**复制保存**（只显示一次）。
2. （可选）打开 https://dashscope.console.aliyun.com → 开通 DashScope → 获取 API Key，用于图片 OCR。
3. 充值少量余额（DeepSeek 问答极便宜，几块钱能用很久）。

### 4.4 获取项目代码并配置 .env

```bash
# 1. 获取项目代码（两种方式任选）
git clone <你的仓库地址>            # 有仓库则 clone
# 或直接把项目文件夹复制/解压到你的磁盘，例如 D:\Projects\ProgramRAG

# 2. 进入项目根目录（后续所有命令都在这里执行）
cd ProgramRAG

# 3. 创建配置文件：复制模板为根目录 .env
cp backend/.env.example .env        # Windows cmd：copy backend\.env.example .env

# 4. 编辑 .env，至少填上 DEEPSEEK_API_KEY
#    （Visual Studio Code / 记事本打开均可）
```

`.env` 在**项目根目录**（不是 backend/ 下），后端启动时自动读取。完整变量见[六、配置说明](#六配置说明根目录-env)。

> 版本 A 继续走 [4.5 Docker](#45-方式-adocker-一键部署推荐)；想本地开发/调试代码走 [4.6 Conda](#46-方式-bconda-从零搭建本地开发)。

---

### 4.5 方式 A：Docker 一键部署（推荐）

#### 第 1 步：确认前置

```bash
docker --version       # Docker 已装
docker compose version # 新版自带 compose 插件
```

#### 第 2 步：设置模型缓存（重要！）

`docker-compose.yml` 里 backend 通过 `${HF_CACHE_DIR}` 把**宿主 HF 模型缓存**只读挂载进容器（容器内离线跑模型）。路径不写死在文件里——在根目录 `.env` 配置即可（`.env` 已被 gitignore，不含隐私、不随仓库提交）：

```bash
# 根目录 .env 加一行（Windows 示例，换成你自己的路径）：
HF_CACHE_DIR=C:/Users/<你的用户名>/.cache/huggingface
```

**两种方式任选：**

- **方式 A（宿主已下过模型，如本地先跑了 4.6 方式 B）**——在 `.env` 设好 `HF_CACHE_DIR` 指向你的缓存目录即可，其余不动。
- **方式 B（首次部署，模型进 Docker 卷）**——改 `docker-compose.yml`：
  1. backend 的 `volumes` 里删掉 bind 块，换成 `- hf-cache:/hf-cache`；
  2. `TRANSFORMERS_OFFLINE: "1"` 改为 `"0"`（容器内允许联网下载模型）；
  3. 文件末尾 `volumes:` 段补一行 `hf-cache:`；
  4. 按下方第 3 步预下载模型。

#### 第 3 步：预下载模型到卷（改法 1 才需要）

首次用 named volume 时，先在容器里把两个模型下好，避免 `up` 时启动卡很久：

```bash
docker compose run --rm -e TRANSFORMERS_OFFLINE=0 -e HF_ENDPOINT=https://hf-mirror.com \
  backend python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
  SentenceTransformer('BAAI/bge-large-zh-v1.5'); CrossEncoder('BAAI/bge-reranker-v2-m3', device='cpu')"
```

> 国内网络默认走 `hf-mirror.com` 镜像；海外可把 `HF_ENDPOINT` 换回 `https://huggingface.co`。这一步约需几分钟，下载完模型就缓存在 `hf-cache` 卷里。

#### 第 4 步：构建并启动

```bash
docker compose up -d --build
```

- 首次构建会下载 python/node 基础镜像 + 安装依赖（torch CPU 版在仓库根目录的 `torch-2.9.1+cpu-*.whl`，随仓库提供），约 5-15 分钟；
- 启动后 backend 的 entrypoint 会**自动把 19 篇面试参考资料 seed 进 `kb_references`**（CPU 向量化 1-5 分钟，日志可见）；
- 查看进度：`docker compose logs -f backend`。

#### 第 5 步：验证

```bash
docker compose ps            # 三个服务都 healthy
curl http://localhost:8000/api/health   # status: ok
```

浏览器打开 **http://localhost:8000** 即可使用。

> 局域网/服务器访问：把 compose 里 `8000:8000` 端口放开（云服务器记得配安全组），防火墙 `sudo ufw allow 8000`。

---

### 4.6 方式 B：Conda 从零搭建（本地开发）

#### 第 1 步：创建并激活 conda 环境

```bash
conda create -n rag python=3.10 -y    # 与 Docker 镜像同一 Python 大版本
conda activate rag
python --version                       # 确认是 3.10.x
```

> 如果你已有 `rag` 环境，直接 `conda activate rag`。所有依赖都装在这个环境里，不污染系统 Python。

#### 第 2 步：安装 PyTorch（必须先于后端依赖）

`requirements.txt` 故意**不含 torch**（为了各自控制版本），所以要单独先装：

```bash
# 有 NVIDIA 显卡（推荐，检索快很多）：
pip install torch

# 没有显卡 / CPU only：
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

> Windows 上 `pip install torch` 默认就是带 CUDA 的构建；装完验证：`python -c "import torch; print(torch.__version__)"`。这步体积较大（约 2.5GB），耐心等。

#### 第 3 步：安装后端依赖

```bash
cd backend
pip install -r requirements.txt
cd ..
```

#### 第 4 步：灌入种子知识库（顺便下载 embedding 模型）

```bash
# 确保此时没在运行 uvicorn（Qdrant local 模式单实例，会锁文件）
python backend/scripts/seed_knowledge.py
```

- 脚本把 `.claude/skills/interview-master/references/` 下的 **19 篇面试参考资料**向量化灌入 `kb_references`；
- 首次运行会**自动下载 embedding 模型** `BAAI/bge-large-zh-v1.5`（约 1.3GB，走 `.env` 里的 `HF_ENDPOINT=https://hf-mirror.com` 国内镜像）；
- 输出 `已入库 N 个分块` 即成功；再跑一次会因非空自动跳过（幂等）。

> reranker 模型 `BAAI/bge-reranker-v2-m3` 会在首次启动后端时后台自动下载，也可手动预下：
> ```bash
> export HF_ENDPOINT=https://hf-mirror.com   # Windows cmd: set HF_ENDPOINT=https://hf-mirror.com
> python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', device='cpu')"
> ```

#### 第 5 步：启动 Redis（可选，默认无缓存也能跑）

Redis 只用于**回答缓存**（命中 <50ms）。装了 Docker 就一行：

```bash
docker run -d --name rag-redis -p 6379:6379 redis
```

没 Docker 也可以跳过——`.env` 里 `REDIS_URL` 留空即自动禁用缓存，功能完全可用，只是每次回答都重新生成（约 10s）。

#### 第 6 步：启动后端

```bash
cd backend
python -m uvicorn app.main:app --port 8000
```

- 启动会后台**预热模型**（加载 embedding / BM25 索引 / reranker），首次多等一会儿（看日志），后续秒开；
- 日志出现 `Uvicorn running on http://127.0.0.1:8000` 即启动成功。

> 保持这个终端开着。**不要**加 `--workers 多`——Qdrant local 模式单实例，多 worker 会互抢文件锁。

#### 第 7 步：前端

前端用 Vite 构建，产物由后端单端口托管：

| 场景 | 操作 |
|---|---|
| 仓库里已有 `frontend/dist/`（本仓库自带） | 直接打开 http://localhost:8000 |
| 从 git clone 的（dist 被 gitignore，没有） | `cd frontend && npm install && npm run build && cd ..`，再打开 8000 |
| 开发模式（热更新，改前端代码） | 另开终端 `cd frontend && npm install && npm run dev` → http://localhost:5173（代理 /api 到 8000） |

#### 第 8 步：验证

见下一节 [五、验证部署](#五验证部署)。

---

## 五、验证部署

**1. 健康检查**

```bash
curl http://localhost:8000/api/health
# 期望 status: ok，并显示 redis / qdrant / 模型加载状态
```

**2. 全链路冒烟测试**（启动服务后，另开终端）

```bash
python backend/scripts/smoke_test.py
# [ok] health → 上传测试文档(202 异步) → 后台完成 → 提问 → 列表 → 删除
# 全部冒烟测试通过 ✅
```

- 未配 `DEEPSEEK_API_KEY` 时，提问一步会打印 `[skip]`（检索链路仍验证），配上 key 后重跑即全通过；
- Windows 控制台若报 GBK 编码错，先执行 `set PYTHONIOENCODING=utf-8` 再跑。

**3. 浏览器手动验证**

| 检查项 | 操作 | 预期 |
|---|---|---|
| 问答 | 输入"行为面试怎么准备？STAR法则是什么？" | SSE 打字机输出，回答带 [1][2] 引用 |
| 引用溯源 | 点击 [1] 徽标 | 展开原文卡片 + 置信度 |
| 上传 | 拖一个 PDF/简历进「知识库」 | 202 立即返回，处理完出现在列表，提问优先引用它 |
| 多轮 | 追问"那公司压价怎么办？" | 理解指代，命中谈薪资料 |
| 会话 | 刷新页面 | 左侧会话栏仍在，上下文保留 |
| 联网 | 问"2026 年 Java 薪资行情"或"杭州天气如何？" | Agent 下 LLM 自主调用 `web_search`/`fetch_url`，回答带 🌐 原文链接卡片（「🌐 联网搜索」开关仅作提示） |

---

## 六、配置说明（根目录 .env）

> 所有变量都有默认值，**只有 `DEEPSEEK_API_KEY` 必填**。完整模板见 [backend/.env.example](backend/.env.example)。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | DeepSeek LLM 密钥（必填） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | LLM 端点 |
| `MODEL` | `deepseek-v4-flash` | LLM 模型名 |
| `DASHSCOPE_API_KEY` | — | 图片 OCR 密钥（Qwen-VL-Max，可选） |
| `QDRANT_MODE` | `local` | `local`=本地落盘 / `server`=Docker 容器 |
| `QDRANT_PATH` | `backend/data/qdrant` | local 模式数据目录 |
| `EMBEDDING_MODEL` | `BAAI/bge-large-zh-v1.5` | 嵌入模型（首次启动自动下载） |
| `EMBEDDING_DEVICE` | `auto` | `auto`/`cuda`/`cpu` |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HF 下载镜像（海外可改 `https://huggingface.co`） |
| `REDIS_URL` | 空 | `redis://localhost:6379/0`；空 = 禁用缓存 |
| `CACHE_TTL` | 900 | 回答缓存秒数 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 450/100 | 分块参数 |
| `ENABLE_BM25` / `ENABLE_RERANKER` | true | 混合检索/精排开关 |
| `RATE_LIMIT_CHAT_PER_MIN` | 10 | /api/chat 与 stream 限额 |
| `RATE_LIMIT_UPLOAD_PER_MIN` | 5 | 上传限额 |
| `AGENT_ENABLED` | true | 主开关：true 走 Agent（LLM 工具决策）；false 回退传统 RAG（回归对照） |
| `WEB_SEARCH_ENABLED` | true | 联网搜索总开关 |
| `WEB_FETCH_COMMAND` | `python -m mcp_server_fetch` | fetch MCP server 启动命令（解析为当前解释器；不用 uvx——它装 mcp 2.x 与 fetch 不兼容） |
| `WEB_SEARCH_AUTO` | true | 仅作用于旧 RAG 路径（`AGENT_ENABLED=false`）：问题含时效敏感词时自动触发。Agent 模式下联网完全由 LLM 通过 `web_search`/`fetch_url` 工具自主决定 |
| `WEB_SEARCH_MAX_PAGES` | 3 | 抓取正文的网页数 |
| `WEB_SEARCH_TIMEOUT` | 20 | 单次搜索+抓取整体超时（秒） |

---

## 七、API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（含 redis/qdrant/模型状态） |
| POST | `/api/documents/upload` | 上传（**202 异步**，后台向量化） |
| GET | `/api/documents/{id}/status` | 后台任务状态轮询 |
| GET | `/api/documents` | 文档列表 |
| DELETE | `/api/documents/{id}` | 删除（处理中返回 409） |
| POST | `/api/chat` | 问答（响应头 `X-Cache: HIT/MISS`，带 `conversation_id` 则读库取历史并落库；Agent 下 `web_search` 仅作联网 hint） |
| POST | `/api/chat/stream` | SSE 流式问答（Agent 下：thought/tool_call/tool_result → sources → delta* → done，`done` 带 `tool_trace`；同样支持会话落库与联网搜索） |
| GET | `/api/conversations` | 会话列表（左侧栏） |
| POST | `/api/conversations` | 新建会话 |
| GET | `/api/conversations/{id}` | 会话详情（含全部消息与引用） |
| DELETE | `/api/conversations/{id}` | 删除会话 |
| GET/POST/DELETE | `/api/agent/memories` | 长期记忆（Agent 工作台）CRUD 与统计 |
| GET | `/api/agent/tools` | 17 个工具的名称/描述/参数 schema（工作台） |
| GET | `/api/agent/prompts` | 提示词模板列表（工作台） |
| GET | `/api/agent/middleware` | 中间件清单（工作台） |
| GET | `/api/agent/metrics` | 性能指标 + 记忆统计（工作台） |

---

## 八、性能指标（实测）

| 场景 | 数值 |
|---|---|
| 检索 Hit@1 / Hit@3 | **94% / 98%**（50 题评估集） |
| 缓存命中回答 | **<50ms**（MISS 约 14s，加速 2000x） |
| 流式首内容（sources 事件） | <0.5s（缓存命中 8ms） |
| LLM 首字 | 1-3s |
| 多轮改写命中率 | 80% |

---

## 九、常见问题 / 排错

**环境搭建**

- **pip 安装太慢 / 超时**：临时用国内源 `pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r requirements.txt`
- **torch 装不上或没有显卡**：用第 4.6 节的 CPU 索引命令 `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- **模型下载失败/卡住**：确认 `.env` 里 `HF_ENDPOINT=https://hf-mirror.com`（国内必须）；或手动预下载命令里带上 `HF_ENDPOINT`。实在不行删掉半成品缓存（`C:\Users\<你>\.cache\huggingface` 或 `hf-cache` 卷）重下
- **Windows 报 GBK/UnicodeEncodeError**：先 `set PYTHONIOENCODING=utf-8` 再跑命令
- **端口被占用**：`8000` 被占 → `--port 8001`（前端 dist 走 8001 需改 vite 代理）；`6379`/`6333` 被占 → 停掉占用进程或改 compose 端口映射

**运行问题**

- **Qdrant "already accessed"**：local 模式单实例——跑 `seed_knowledge.py` 等独立脚本前先停 uvicorn；uvicorn 必须 `--workers 1`
- **改 .env 不生效**：配置启动时读取，改完重启后端
- **首次启动很慢 / 没反应**：后台在预热/下载模型，看后端日志确认进度
- **问答返回 503 / "DeepSeek 未配置"**：`.env` 里 `DEEPSEEK_API_KEY` 没填或没重启
- **清空对话**：左侧会话栏悬停每条 → 点 × 删除；或删 `backend/data/conversations.db`
- **清空知识库**：删 `backend/data/qdrant` 目录（Docker 模式：`docker compose down -v`，重启自动重新 seed）
- **限流 429**：`RATE_LIMIT_CHAT_PER_MIN` 调大，或确认没有异常刷请求

**联网搜索**

- **联网搜索要 API key 吗？** 不需要。后端作为 MCP 客户端连接 fetch MCP server（`mcp-server-fetch`），搜索走 Bing 结果页解析（curl_cffi 模拟 Chrome 指纹绕过反爬——Docker 容器里裸 httpx 会被判 bot 返回验证码页），正文抓取走 fetch 工具（readability 提取）
- **怎么触发联网？** Agent 模式（默认）下由 LLM 自主决定——系统提示引导"时效/天气/链接"场景，LLM 判断需要时调用 `web_search`/`fetch_url` 工具；输入框「🌐 联网搜索」开关仅作系统提示 hint（不强制）。旧 RAG 路径（`AGENT_ENABLED=false`）仍按开关/时效敏感词自动触发（`WEB_SEARCH_AUTO`）。问题带链接 LLM 会直接抓该网页；天气问句自动规范为"<城市>天气预报"（Bing 对口语问句易返回百科/攻略而非天气站）。联网回答带 🌐 来源卡片（可点原文链接），缓存 key 含搜索结果哈希，联网/不联网互不污染
- **Docker 里模型从哪来**：本仓库 compose 默认绑定宿主 HF 缓存；首次部署按 4.5 改 named volume + 预下载（`hf-cache` 卷持久化，只需一次）

**其他**

- **种子为什么在 kb_references？**：面试 skill 参考是系统的静态方法论，与你上传的简历（kb_documents）分库存储，检索各自召回再合并——你的文档稳定命中且排最前
- **Docker 构建报 COPY torch 失败**：仓库根目录必须有 `torch-2.9.1+cpu-cp310-*.whl`（Dockerfile 依赖它做 CPU torch 安装），缺失则下载并放回根目录

---
