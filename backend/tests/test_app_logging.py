"""应用日志接线的用例（2026-09-24 甲方空队列故障的排查副产品）。

## 为什么值得一条用例

云托管容器里，应用 logger 的日志**此前完全不可见**（只有 WARNING+ 经 Python 的
`lastResort` 冒出来，另有 uvicorn 自己的访问日志）—— 因为全仓没有任何
`basicConfig` / root handler，`LOG_LEVEL=INFO` 这个环境变量**写了却没人读**。

后果不是"少几条日志"，而是**关键事实缺失**：`auth/service.py` 里那条
「云托管通道采信平台注入身份: openid=…」是 `logger.info`，云端看不见 ⇒
排查"这次登录的是哪个账号"时**没有任何日志证据**，只能靠排除法绕远路。

这条用例钉住两件事：
1. 级别字符串 → logging 常量的解析（非法值不静默变成 NOTSET）；
2. `configure_logging()` 真的让应用 logger 的 INFO 生效（即上面那条身份行可见）。
"""

from __future__ import annotations

import logging


def test_resolve_level_understands_configured_names() -> None:
    from app.main import _resolve_level

    assert _resolve_level("INFO") == logging.INFO
    assert _resolve_level("info") == logging.INFO
    assert _resolve_level(" warning ") == logging.WARNING
    assert _resolve_level("DEBUG") == logging.DEBUG


def test_resolve_level_falls_back_to_info_for_garbage() -> None:
    """非法值 ⇒ INFO。**不能**退化成 NOTSET（那等于把日志全关掉，且是静默的）。"""
    from app.main import _resolve_level

    assert _resolve_level("") == logging.INFO
    assert _resolve_level("verbose") == logging.INFO
    assert _resolve_level("INFO2") == logging.INFO


def test_configure_logging_makes_app_info_visible() -> None:
    """核心契约：配置 INFO 后，`auth.service` 的 INFO 身份行必须是**可输出**的。

    这正是 2026-09-24 那次故障里缺失的证据类型 —— 没有它，
    "请求全 200 但数据全空"只能靠反推。
    """
    from app.main import configure_logging

    auth_logger = logging.getLogger("app.modules.auth.service")
    root = logging.getLogger()
    original = root.level
    try:
        configure_logging("INFO")
        assert auth_logger.isEnabledFor(logging.INFO) is True

        configure_logging("ERROR")
        assert auth_logger.isEnabledFor(logging.INFO) is False
        assert auth_logger.isEnabledFor(logging.ERROR) is True
    finally:
        root.setLevel(original)
