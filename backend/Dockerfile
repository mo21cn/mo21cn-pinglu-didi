FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

WORKDIR /app

# 先复制依赖清单以利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app ./app

# ⚠️ 迁移执行器与迁移脚本**必须**进镜像：线上建库/升级要 `python migrate.py`
#    （迁移是手写 SQL + 执行历史表，见 docs/06-database-migration.md）。
#    缺了它就只能靠容器外临时拷贝，等于把"迁移"变成一次性的手工操作。
COPY migrate.py .
COPY migrations ./migrations

# 容器内不含任何 .env 文件（本 Dockerfile 只 COPY 上面三项）
# ⇒ 配置**只能**来自环境变量，符合 WP-4「密钥在仓库外」；
#   pydantic-settings 里环境变量优先级高于 .env 文件，注入即生效。

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
