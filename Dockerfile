# 流域水文建模平台 —— 多阶段构建
# 构建： docker build -t hydro-platform .
# 运行： docker run -d -p 8013:8013 -v hydro-data:/data hydro-platform
#   数据（项目/示例）持久化在 hydro-data 卷（容器内 /data，可用 -v 本机目录:/data 替代）

# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：Python 运行时 ----------
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./
COPY --from=frontend-build /build/dist ./frontend/dist

# 数据目录（项目 / 示例）挂载点
ENV HYDRO_DATA_DIR=/data
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8013
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8013"]
