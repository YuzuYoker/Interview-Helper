# ---- Stage 1: 前端构建 ----
FROM node:24-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: 后端运行 ----
FROM python:3.10-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/hf-cache \
    TRANSFORMERS_OFFLINE=1 \
    PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

WORKDIR /app

# 先装 CPU 版 torch（本地 wheel 规避容器网络波动；torch<2.6 会被 transformers 拒绝，CVE-2025-32434）
COPY torch-2.9.1+cpu-cp310-cp310-manylinux_2_28_x86_64.whl /tmp/torch-2.9.1+cpu-cp310-cp310-manylinux_2_28_x86_64.whl
RUN pip install --no-cache-dir /tmp/torch-2.9.1+cpu-cp310-cp310-manylinux_2_28_x86_64.whl && rm /tmp/torch-2.9.1+cpu-cp310-cp310-manylinux_2_28_x86_64.whl

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 业务代码（backend/ 布局保持 parents[3] = /app，前端 dist 同源可被静态托管）
COPY backend/ ./backend/
# interview-master skill 的 references —— seed 知识库的资料来源
COPY .claude/ ./.claude/
COPY --from=frontend-build /build/dist ./frontend/dist/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
CMD ["sh", "./docker-entrypoint.sh"]
