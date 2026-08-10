# 项目1：Interview Helper 知识库系统（RAG）

## 项目概述

**一句话介绍**：支持多格式面试资料上传、智能检索、引用溯源的企业级RAG **Interview Helper** 系统——上传 JD/简历/面经/参考答案，问答式获取面试准备支持

> **定位说明（2026-08-07 更新）**：本项目由"智能客服知识库"改造为"Interview Helper"。
> 改造内容：① 后端生成角色改为资深面试顾问（[generator.py](backend/app/rag/generator.py) 系统提示词）；
> ② 内置 [interview-master](https://github.com/chen3tu/interview-master-skill) Skill（`.claude/skills/interview-master/`）提供全流程面试辅导；
> ③ 种子知识库 = skill 的 19 篇 references（面试题库/岗位分析/谈薪策略等），灌入脚本 [seed_knowledge.py](backend/scripts/seed_knowledge.py)；
> ④ **双库分离**（kb_references 种子方法论 / kb_documents 用户素材，检索各自召回再合并，用户文档按文档聚合、按最佳 chunk 相似度排序置前）+ **联网搜索**（Fetch MCP Server：curl_cffi 模拟 Chrome 指纹抓 Bing、天气问句规范化、问题带链接直接抓取）。
> RAG 技术栈与 4 周开发计划保持不变（混合检索/重排序/前端/部署等仍按计划推进）。

**技术栈**：
- 后端：FastAPI + LangChain
- 向量库：Qdrant
- LLM：DeepSeek API（高性价比）
- 嵌入模型：BAAI/bge-large-zh-v1.5（本地）
- 重排序：BAAI/bge-reranker-v2-m3（本地）
- 图片识别：Qwen-VL-Max API（多模态）
- 前端：Vue3
- 部署：Docker + 阿里云/腾讯云

---

## 开发计划（4周）

### 第1周：基础RAG功能

#### Day 1-2：项目搭建
- [ ] 创建FastAPI项目结构
- [ ] 配置依赖（langchain, qdrant, openai/deepseek）
- [ ] 实现基础API路由（健康检查、测试接口）
- [ ] 配置LLM连接（DeepSeek API）
- [ ] 配置Embedding模型（BGE本地模型）

#### Day 3-4：文档处理
- [ ] 实现PDF文档加载与解析
- [ ] 实现Word/Excel文档加载
- [ ] 实现图片OCR处理（Qwen-VL-Max）
- [ ] 实现文本分块（RecursiveCharacterTextSplitter）
- [ ] 添加分块元数据（来源、页码、章节）

#### Day 5-7：向量化与检索
- [ ] 配置Embedding模型（BAAI/bge-large-zh-v1.5）
- [ ] 搭建Qdrant向量数据库
- [ ] 实现文档向量化存储
- [ ] 实现基础向量检索
- [ ] 测试检索效果

**本周产出**：
- 可以上传PDF → 自动向量化 → 提问返回答案
- 基础API接口完成

---

### 第2周：RAG高级功能

#### Day 1-2：混合检索
- [x] 实现BM25关键词检索（rank_bm25 + jieba，从 Qdrant 懒重建）
- [x] 向量检索 + BM25结果融合
- [x] 实现RRF（Reciprocal Rank Fusion）排序算法
- [x] 对比单一检索 vs 混合检索效果

#### Day 3-4：Reranker重排序
- [x] 接入Reranker模型（BAAI/bge-reranker-v2-m3，CrossEncoder + Sigmoid，fp16）
- [x] 实现检索结果重排序
- [x] 优化Top-K参数（候选 8 最优：Hit@1 95.3%）
- [x] 评估准确率提升

#### Day 5-7：Query优化
- [x] 实现Query改写（多视角扩展，实验项默认关）
- [x] 实现HyDE（实验：实测无显著提升，默认关——面试可讲）
- [x] 添加对话历史管理
- [x] 实现多轮对话上下文感知（改写命中率 80%）

**本周产出**：
- [x] 检索准确率：Hit@1 **94%**、Hit@3 **98%**（50 题评估集，远超 85% 目标）
- [x] 支持多轮对话（仅 history 非空时改写，单轮零额外延迟）
- [x] 对比实验数据（backend/data/eval/results_*.md，四配置对比表）

---

### 第3周：产品化功能

#### Day 1-2：引用溯源
- [x] 回答中插入引用标记 [1][2][3]（第1周已实现）
- [x] 返回引用原文片段（第1周已实现）
- [x] 前端展示引用来源（可点击跳转：[[n] 徽标 → 展开原文卡片）
- [x] 添加置信度评分（Source.score，前端色阶 绿≥0.7/黄0.4-0.7/灰<0.4）

#### Day 3-4：流式输出
- [x] 实现SSE（Server-Sent Events）流式响应（POST /api/chat/stream，sources→delta*→done 事件协议）
- [x] 前端流式渲染（打字机效果：rAF 节流）
- [x] 优化首字响应时间（诚实口径：sources 首内容 <0.5s，LLM 首字 1-3s，实测 178-580 delta 事件中文完整）

#### Day 5-7：前端界面
- [x] 搭建Vue3前端（Vite + Vue3，手写 9 文件，无 UI 框架）
- [x] 文档上传界面（拖拽上传，串行，图片 OCR 提示）
- [x] 对话界面（类似ChatGPT：流式/停止/多轮/清空）
- [x] 知识库管理界面（查看/删除文档）

**本周产出**：
- [x] 完整的前后端交互（dev 5173 代理 + 生产单端口 8000 静态托管）
- [x] 流式输出体验（打字机 + 停止按钮 + 断流容错）
- [x] 引用溯源功能（徽标点击展开原文 + 置信度）

---

### 第4周：部署与优化

#### Day 1-2：性能优化
- [x] 添加Redis缓存（完整回答缓存 + 知识版本号失效 + TTL 兜底，命中 7ms/加速 2100x）
- [x] 实现异步处理（上传 202 立即返回，后台线程池向量化，前端轮询状态）
- [x] 并发限流（自写纯 ASGI 滑动窗口中间件：对话 10 次/分、上传 5 次/分，429+Retry-After）
- [x] 优化响应时间（诚实口径：缓存命中 <10ms、流式首内容 <0.5s、完整生成 10s 内）

#### Day 3-4：Docker部署
- [x] 编写Dockerfile（多阶段：node 构建前端 → python:3.10-slim + CPU torch 2.9.1）
- [x] 编写docker-compose.yml（backend + qdrant + redis 三服务，健康检查，自动 seed 空库）
- [x] 本地Docker测试（✅ 三服务 healthy → 自动 seed 170 chunks → 单端口流式问答 + 缓存命中全通过）
- [x] 部署到云服务器（步骤写入 docs/部署与优化.md，本次仅本地验证）

#### Day 5-7：文档与面试准备
- [x] 编写README（功能介绍、快速开始、配置表、API 一览、性能指标）
- [x] 写技术博客（docs/技术博客.md：缓存版本化/异步状态机/限流 + Docker 六坑）
- [x] 准备面试话术（docs/项目吃透指南.md 7.5 节 + docs/demo_脚本.md 分镜讲稿）
- [x] Demo（文本分镜脚本替代视频）

**本周产出**：
- [x] 线上可访问的 Demo（docker compose up 一条命令，本地验证通过）
- [x] 技术博客 1 篇（踩坑记录、性能优化）
- [x] 面试准备材料（话术/指标/演示脚本）

---

## 项目结构

```
rag-knowledge-base/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── chat.py          # 对话接口
│   │   │   ├── documents.py     # 文档管理接口
│   │   │   └── health.py        # 健康检查
│   │   ├── rag/
│   │   │   ├── loader.py        # 文档加载器
│   │   │   ├── splitter.py      # 分块器
│   │   │   ├── retriever.py     # 检索器（混合检索）
│   │   │   ├── reranker.py      # 重排序器
│   │   │   └── generator.py     # 生成器
│   │   ├── models/
│   │   │   ├── document.py      # 文档模型
│   │   │   └── chat.py          # 对话模型
│   │   ├── utils/
│   │   │   ├── embedding.py     # Embedding工具
│   │   │   └── config.py        # 配置
│   │   └── main.py              # FastAPI入口
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   └── package.json
├── docker-compose.yml
└── README.md
```

---

## 面试亮点

### 能讲的技术点
1. **为什么选Qdrant不选Milvus**：轻量、易部署、适合中小项目
2. **混合检索原理**：向量检索（语义）+ BM25（关键词）互补
3. **RRF算法**：无需调参的结果融合方法
4. **Reranker作用**：粗排→精排，准确率提升10-15%
5. **Query改写**：解决用户提问模糊的问题

### 能展示的数据
- 检索准确率：85%+
- 首字响应时间：< 1秒
- 完整响应时间：< 3秒
- 支持并发用户：50+

### 常见问题准备
- Q: 文档太长怎么处理？
  A: 按语义分块 + 滑动窗口 + 元数据保留上下文
- Q: 检索不准怎么办？
  A: 混合检索 + Reranker + Query改写三重保障
- Q: Token成本如何优化？
  A: 缓存常见问题 + 精简Prompt + 选用性价比高的模型

---

## 关键依赖

```txt
# 后端框架
fastapi==0.109.0
uvicorn==0.27.0
python-multipart==0.0.6

# LangChain
langchain==0.1.0
langchain-community==0.0.10
langchain-openai==0.0.5  # DeepSeek兼容OpenAI API

# 向量数据库
qdrant-client==1.7.0

# 本地模型
sentence-transformers==2.2.2  # BGE Embedding
FlagEmbedding==1.2.0  # BGE Reranker
torch==2.1.0

# 文档处理
pypdf==3.17.0
python-docx==1.1.0
openpyxl==3.1.2
paddleocr==2.7.0  # 图片OCR（可选）

# 多模态API
dashscope==1.14.0  # Qwen-VL图片识别

# 缓存与工具
redis==5.0.0
```

---

## 部署清单

### 云服务器配置
- [ ] **CPU方案**：4核8G（本地跑BGE模型需要较大内存）
- [ ] **GPU方案**（可选）：T4 16G显存（加速本地模型推理）
- [ ] **系统**：Ubuntu 22.04 LTS
- [ ] **存储**：50GB+ SSD（向量库+模型文件）
- [ ] **带宽**：5Mbps+（前端资源+API响应）

### API密钥准备
- [ ] **DeepSeek API Key**（LLM生成回答）
  - 注册地址：https://platform.deepseek.com
  - 费用：约 ¥0.01-0.05/千token
- [ ] **Qwen-VL-Max API Key**（图片识别）
  - 注册地址：https://dashscope.console.aliyun.com
  - 模型：qwen-vl-max（最强多模态理解）
  - 费用：约 ¥0.01-0.03/次

### 域名与网络
- [ ] 域名备案（国内服务器必须）
- [ ] Nginx反向代理配置
- [ ] HTTPS证书（Let's Encrypt免费）
- [ ] 防火墙规则（开放80/443端口）

### Docker组件
- [ ] 后端服务（FastAPI）
- [ ] 前端服务（Vue3构建后的静态文件）
- [ ] Qdrant向量数据库
- [ ] Redis缓存（可选）
- [ ] Nginx网关

### 监控与运维
- [ ] 日志收集（ELK或阿里云SLS）
- [ ] 性能监控（Prometheus + Grafana）
- [ ] 健康检查接口
- [ ] 自动重启策略（docker-compose restart policy）
- [ ] 数据备份（Qdrant数据定期备份）

### 成本估算（月度）
| 项目 | 费用 |
|------|------|
| 云服务器（4核8G） | ¥200-400 |
| DeepSeek API | ¥50-200（按量） |
| Qwen-VL-Max API | ¥20-100（按量） |
| 域名+SSL | ¥10-50 |
| **合计** | **¥280-750/月** |