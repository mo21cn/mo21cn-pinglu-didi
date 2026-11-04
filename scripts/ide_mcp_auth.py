"""等 IDE 人工授权的一次性工具（走查前跑，批完即可关）。

为什么需要"等"而不是"发一次就走"
--------------------------------
`POST /mcp` 未授权时回 `403 -32002 Client authorization pending`，同时 IDE 会弹一个
420px 的授权小窗（日志里是 `winId='mcp_client_auth_<client>_<ts>'`）。**该窗口的生命周期
绑在请求方连接上**：脚本发一次就退出 ⇒ IDE 约 1.3 秒后 `forceClose` 掉窗口并记
`authResult=deny`，人根本来不及点（2026-10-26 实测，连着失败两轮）。

本脚本因此**复用同一条 HTTP 长连接**反复 initialize（不新开 socket、不堆窗口），
直到人工点「允许」或超时。授权按 clientName 持久保存：批过一次后同名客户端直接 200。

用法
----
    python scripts/ide_mcp_auth.py --client WorkBuddy --wait 180
"""

from __future__ import annotations

import argparse
import sys
import time

from wechatide_mcp import McpBackend, discover, service_port_state


def main() -> int:
    ap = argparse.ArgumentParser(description="等 IDE 授权 MCP 客户端")
    ap.add_argument("--client", default="WorkBuddy", help="MCP clientName（授权按此持久）")
    ap.add_argument("--wait", type=int, default=180, help="等待秒数")
    ap.add_argument("--interval", type=float, default=2.5)
    a = ap.parse_args()

    got = discover()
    if got is None:
        print("找不到 IDE 的 MCP 配置：确认 IDE 已启动、设置→安全→服务端口已开启", flush=True)
        return 2
    port, _token, uh = got
    print(f"User Data : {uh}", flush=True)
    print(f"IDE 端口  : {port}（服务端口 {service_port_state() or '未知'}）", flush=True)
    print(f"客户端名  : {a.client}", flush=True)

    m = McpBackend(project=".", client=a.client)
    deadline = time.time() + a.wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            j = m.status()
        except RuntimeError as exc:
            print(f"[{attempt}] {exc}", flush=True)
            time.sleep(a.interval)
            continue
        if j.get("ok"):
            user = (j.get("loginUser") or {}).get("nickName") or "?"
            print(f"[{attempt}] 已授权（IDE 登录用户 {user}）", flush=True)
            return 0
        print(f"[{attempt}] 未就绪：{str(j)[:200]}", flush=True)
        time.sleep(a.interval)
    print("超时未批准", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
