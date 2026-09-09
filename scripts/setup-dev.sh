#!/usr/bin/env bash
# pinglu-didi 一键初始化开发环境
# 用法：bash scripts/setup-dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/4] 安装工程工具链（husky + commitlint）"
npm install

echo "==> [2/4] 创建后端虚拟环境"
cd backend
python -m venv .venv

# 激活（Windows 用 Scripts，macOS/Linux 用 bin）
if [ -f ".venv/Scripts/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/Scripts/activate"
else
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

echo "==> [3/4] 安装后端依赖"
pip install -r requirements.txt

echo "==> [4/4] 准备环境配置"
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "    已从 .env.example 生成 .env，请按需修改"
fi

echo ""
echo "✅ 初始化完成！"
echo "   启动后端：cd backend && uvicorn app.main:app --reload"
echo "   启动基础设施：docker compose -f deploy/docker-compose.yml up"
