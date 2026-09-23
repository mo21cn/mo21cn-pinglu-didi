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

# ⭐ 启动时**先跑迁移，再起服务**（2026-09-23 补）。
#
# 为什么必须自动化：容器内**没有 Shell**，「人工进容器执行 `python migrate.py`」这条路
# 实际走不通；而漏跑迁移的后果是 **服务显示正常、业务接口整组 500**
# （`app/main.py` 的 `create_all` 明确排除 `ent_` 前缀）—— 属最难排查的静默故障，
# 本项目实测就是这么整条委托支线"看不见"了，还被误判成"代码回退到旧版本"。
#
# 迁移本身按 `(module, migration_id)` 去重、可重复执行（见 `migrate.py`），启动开销很小。
# ⛔ 迁移失败**不吞异常**（不用 `|| true` 之类）：宁可让本次部署失败、立刻暴露，
#    也不要服务"活着但不工作"—— 这正是本次要修的故障模式。
CMD ["sh", "-c", "python migrate.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
