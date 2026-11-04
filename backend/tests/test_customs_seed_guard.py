"""关务演示种子的**环境守卫**与云端归属参数（Q-06）。

为什么单独一个文件：这两件事都是"**默认必须拦住**"的那一类 —— 一旦被改松，
后果不是功能坏了，而是**真实客户库里长出 `PL-CUS-DEMO-*` 假数据**，
或演示数据挂到一个没人登录的账号下（界面表现＝"功能正常但列表全空"，极难排查）。

⛔ 判据必须能失败：每条都有正控（放行的一侧也要真跑一遍），
否则"守卫永远拒绝"这种实现也能把用例跑绿。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.core.config import get_settings

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load(path: str, name: str):
    """按文件路径加载脚本模块（`scripts/` 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location(name, _BACKEND / path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def seed():
    return _load("scripts/seed_customs_demo.py", "seed_customs_demo_under_test")


def _set_env(monkeypatch, *, app_env: str, demo_store: bool | None) -> None:
    """改 `get_settings()` 的**缓存实例**字段。

    ⛔ 不能用 `monkeypatch.setenv`：`get_settings()` 返回的是进程内单例，
    它在 conftest 设 `APP_ENV=test` 时就已构造完，之后改环境变量读不到
    （实测 `Settings()` 会变、`get_settings()` 不变）。与仓库既有用例
    （`force_wechat_mock` patch 实例属性）同一口径。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    monkeypatch.setattr(settings, "DEMO_DATA_STORE", bool(demo_store))


@pytest.fixture()
def boot():
    return _load("boot_seed.py", "boot_seed_under_test")


# ─────────────────────────────────────────────── 环境守卫真值表


def test_production_without_declaration_is_rejected(seed, monkeypatch):
    _set_env(monkeypatch, app_env="production", demo_store=False)
    with pytest.raises(SystemExit) as exc:
        seed._assert_demo_env()
    msg = str(exc.value)
    assert "DEMO_DATA_STORE" in msg, "拒绝时必须告诉对方正确的做法，而不是只说不行"
    assert "--force" not in msg, "⛔ 不得提示任何命令行绕过参数"


def test_production_with_declaration_is_allowed(seed, monkeypatch, capsys):
    """正控：显式声明为演示库 ⇒ 放行（云托管 `APP_ENV` 只能是 production）。"""
    _set_env(monkeypatch, app_env="production", demo_store=True)
    seed._assert_demo_env()  # 不抛即通过
    assert "演示库" in capsys.readouterr().err, "放行时必须留下醒目痕迹，便于事后审计"


def test_env_outside_whitelist_is_rejected(seed, monkeypatch):
    _set_env(monkeypatch, app_env="staging", demo_store=False)
    with pytest.raises(SystemExit) as exc:
        seed._assert_demo_env()
    assert "白名单" in str(exc.value)


def test_development_is_allowed(seed, monkeypatch):
    """正控：本机 dev 档不受影响（走查与日常开发依赖这条路径）。"""
    _set_env(monkeypatch, app_env="development", demo_store=False)
    seed._assert_demo_env()


# ─────────────────────────────────────────────── 云端归属参数


def test_owner_auto_is_rejected(boot, monkeypatch):
    monkeypatch.setenv("SEED_CUSTOMS_OWNER", "auto")
    extra, bad = boot._customs_owner_args()
    assert extra is None and bad == "auto", "⛔ auto 无法证明那条账号就是演示者本人"


def test_owner_user_id(boot, monkeypatch):
    monkeypatch.setenv("SEED_CUSTOMS_OWNER", "user:7")
    assert boot._customs_owner_args()[0] == ("--owner-user-id", "7")


def test_owner_user_id_must_be_numeric(boot, monkeypatch):
    monkeypatch.setenv("SEED_CUSTOMS_OWNER", "user:abc")
    extra, bad = boot._customs_owner_args()
    assert extra is None and bad == "user:abc"


def test_owner_openid(boot, monkeypatch):
    monkeypatch.setenv("SEED_CUSTOMS_OWNER", "oabcdef")
    assert boot._customs_owner_args()[0] == ("--owner-openid", "oabcdef")


def test_owner_unset_means_no_customs_seed(boot, monkeypatch):
    """不设 ⇒ 不铺（默认行为不变，⛔ 不得悄悄按 mock-openid 造一个账号）。"""
    monkeypatch.delenv("SEED_CUSTOMS_OWNER", raising=False)
    extra, bad = boot._customs_owner_args()
    assert extra is None and bad == ""
