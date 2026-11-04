"""云端演示就绪自检（E-0.11）—— 判据必须能失败，且放行那一侧要真跑一遍。

用本机 `ThreadingHTTPServer` 当假云端：不依赖外网、不碰真实云托管，
但每个判据都能在「条件不满足」时变红。⛔ 尤其要守住最后一条：
HTTP 探测**永远不能**宣称关务种子已铺（那只能看容器日志）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_demo_readiness_under_test",
        _BACKEND / "scripts" / "check_demo_readiness.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def readiness():
    return _load()


class _Handler(BaseHTTPRequestHandler):
    responses: dict[str, tuple[int, str]] = {}

    def do_GET(self):  # noqa: N802 -- http.server 的固定方法名
        status, body = self.responses.get(self.path, (404, "{}"))
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静音，免得测试输出被访问日志淹没
        pass


@pytest.fixture()
def serve():
    """返回「起一台假云端」的工厂；用例结束后统一收摊。"""
    running: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

    def _start(responses: dict[str, tuple[int, str]]) -> str:
        handler = type("HandlerUnderTest", (_Handler,), {"responses": responses})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        running.append((srv, thread))
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield _start

    for srv, thread in running:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def _repo(tmp_path: Path, profile: str) -> Path:
    cfg = tmp_path / "miniapp" / "config"
    cfg.mkdir(parents=True)
    (cfg / "env.js").write_text(
        "// demo profile\nconst RUNTIME_PROFILE = '" + profile + "'\n", encoding="utf-8"
    )
    return tmp_path


def _ok_responses(readiness, *, health=200, paths=None, auth=401):
    return {
        "/healthz": (
            health,
            '{"status":"ok","env":"production"}' if health == 200 else '{"status":"bad"}',
        ),
        "/openapi.json": (
            200,
            json.dumps(
                {
                    "paths": {
                        p: {}
                        for p in (readiness.EXPECTED_CUSTOMS_PATHS if paths is None else paths)
                    }
                }
            ),
        ),
        "/api/v1/customs/sources": (auth, '{"detail":"未提供访问令牌"}'),
    }


def _names(readiness_result):
    return {c["name"]: c["ok"] for c in readiness_result["checks"]}


# ─────────────────────────────────────────────── 正控：四项全绿


def test_all_checks_pass_on_healthy_demo_deployment(readiness, serve, tmp_path):
    url = serve(_ok_responses(readiness))
    result = readiness.probe(url, _repo(tmp_path, "demo"))
    assert all(_names(result).values()), _names(result)
    assert result["code_layer_ready"] is True


def test_expected_route_set_is_not_silently_loosened(readiness):
    """判据本身要能失败：路径集合被删掉一条，这里必须红。"""
    assert len(readiness.EXPECTED_CUSTOMS_PATHS) == 7
    assert "/api/v1/customs/classifications/{classification_id}/manual" in (
        readiness.EXPECTED_CUSTOMS_PATHS
    )


# ─────────────────────────────────────────────── 负控：逐项能红


def test_unhealthy_container_is_detected(readiness, serve, tmp_path):
    url = serve(_ok_responses(readiness, health=500))
    result = readiness.probe(url, _repo(tmp_path, "demo"))
    assert _names(result)["healthz"] is False
    assert result["code_layer_ready"] is False


def test_partially_deployed_customs_routes_is_detected(readiness, serve, tmp_path):
    partial = set(readiness.EXPECTED_CUSTOMS_PATHS)
    partial.remove("/api/v1/customs/classifications/{classification_id}/manual")
    url = serve(_ok_responses(readiness, paths=partial))
    result = readiness.probe(url, _repo(tmp_path, "demo"))
    assert _names(result)["customs_routes"] is False
    assert "manual" in next(
        c["evidence"] for c in result["checks"] if c["name"] == "customs_routes"
    )
    assert result["code_layer_ready"] is False


def test_missing_auth_layer_is_detected(readiness, serve, tmp_path):
    url = serve(_ok_responses(readiness, auth=200))
    result = readiness.probe(url, _repo(tmp_path, "demo"))
    assert _names(result)["customs_auth"] is False
    assert result["code_layer_ready"] is False


def test_dev_profile_is_detected(readiness, serve, tmp_path):
    url = serve(_ok_responses(readiness))
    result = readiness.probe(url, _repo(tmp_path, "dev"))
    assert _names(result)["runtime_profile"] is False
    assert result["code_layer_ready"] is False


def test_unreachable_host_is_reported_not_swallowed(readiness, tmp_path):
    """连不上必须落成 FAIL + 原因，⛔ 不能静默当成通过。"""
    result = readiness.probe("http://127.0.0.1:1", _repo(tmp_path, "demo"), timeout=1.0)
    assert result["code_layer_ready"] is False
    assert all(not c["ok"] for c in result["checks"][:3])
    assert any("HTTP -1" in c["evidence"] for c in result["checks"][:3])


# ─────────────────────────────────────────────── 不能跨层宣称


def test_http_probe_never_claims_seed_or_device_layer(readiness, serve, tmp_path):
    url = serve(_ok_responses(readiness))
    result = readiness.probe(url, _repo(tmp_path, "demo"))
    assert result["code_layer_ready"] is True
    assert result["seed_layer_proven"] is False
    assert result["device_layer_proven"] is False
    assert "容器日志" in result["seed_layer_note"]


def test_main_exit_code_matches_code_layer(readiness, serve, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_demo_readiness.py"])
    good = serve(_ok_responses(readiness))
    assert (
        readiness.main(["--base-url", good, "--repo", str(_repo(tmp_path, "demo")), "--json"]) == 0
    )
    bad = serve(_ok_responses(readiness, health=500))
    assert readiness.main(["--base-url", bad, "--repo", str(tmp_path), "--json"]) == 1
