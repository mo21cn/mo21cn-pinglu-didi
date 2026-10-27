"""旧名防回流静态守卫（Q-03 / 评审单 §3.2）。

## 为什么要有这条

「智慧关务」是唯一对外名称，两个旧名（字面量见 `RETIRED_NAMES`）已于 PRD rev.2 停用。
一次性改字符串很便宜，但**回流**很贵：设计稿回填、文案复制粘贴、老分支合并，
都会把旧名带回 `miniapp/` 或 `backend/` —— 那时代码里同时存在两个名字，
对外口径就又开始分裂，而且没有任何东西会报错。

⇒ 把「不许出现」变成一条**能失败**的判据，比事后 grep 便宜。

## 范围

只扫 `miniapp/` 与 `backend/`（**代码与页面**）。`docs/` 不扫：评审单、决策记录
与迁移说明**必须**能写出旧名（形如"××（停用）"），扫进去只会逼人删掉这份说明。

## 名字怎么拼

两个词都由**片段拼接**得到，不在本文件里出现完整字面量 —— 否则本测试会命中自己，
只能靠"跳过自己"这种例外来通过，而例外一旦加了就再也说不清它是否还在生效。
"""

from __future__ import annotations

from pathlib import Path

#: 停用的旧名（拼接构造，见模块头）
RETIRED_NAMES = ("智慧" + "通关", "委托" + "关务")

#: 只扫这些后缀（代码 / 页面 / 配置 / 脚本）
SUFFIXES = {".py", ".js", ".json", ".wxml", ".wxss", ".ts", ".yml", ".yaml"}

#: 跳过：依赖、构建产物、本机数据库、附件
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", "var", ".mypy_cache", ".pytest_cache"}

#: 仓库根（`backend/tests/xxx.py` → 上三级）
REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("miniapp", "backend")


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for root_name in SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() not in SUFFIXES or not p.is_file():
                continue
            if SKIP_DIRS & set(p.parts):
                continue
            out.append(p)
    return out


def test_no_retired_names_in_code_or_pages():
    """旧名一旦回流到 `miniapp/` 或 `backend/`，这条断言就红。"""
    files = _iter_files()
    assert files, "扫描范围为空 —— 说明 SCAN_ROOTS 指错了，这条判据实际上没在检查任何东西"

    hits: list[str] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name in RETIRED_NAMES:
            if name in text:
                hits.append(f"{p.relative_to(REPO_ROOT).as_posix()} → {name}")

    assert not hits, "以下文件出现了停用的旧名（对外统一用「智慧关务」）：\n  " + "\n  ".join(
        sorted(hits)
    )


def test_guard_can_fail():
    """（正控）判据必须能失败：把旧名放进一份内存文本里，检查逻辑必须命中。

    没有这一条，`RETIRED_NAMES` 被改成空 tuple、或 `hits` 永远为空，
    上面那条依然全绿 —— 那是假绿。
    """
    sample = "标题：" + RETIRED_NAMES[0]
    assert any(name in sample for name in RETIRED_NAMES)
    # 同时确认两个名字都真的在检查范围内（不是只查了一个）
    assert len(RETIRED_NAMES) == 2
