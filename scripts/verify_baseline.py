"""Run the frozen backend checks without touching local databases or remote APIs.

Usage from the repository root:
    .venv/Scripts/python.exe scripts/verify_baseline.py

Evidence is written to tmp/baseline-a. Mypy is recorded separately, matching
the existing CI's non-blocking type-check policy. No application code is patched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUTPUT = ROOT / "tmp" / "baseline-a"


def isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite://",
        "WECHAT_MOCK": "true",
        "LLM_MOCK": "true",
        "LLM_API_KEY": "baseline-unused",
        "LLM_BASE_URL": "http://127.0.0.1:1/disabled",
        "LLM_GATEWAY_URL": "http://127.0.0.1:1/disabled",
        "VECTOR_DB_URL": "http://127.0.0.1:1/disabled",
        "WX_APP_ID": "baseline-unused",
        "WX_APP_SECRET": "baseline-unused",
        "WX_MCH_ID": "baseline-unused",
        "WX_PAY_KEY": "baseline-unused",
        "JWT_SECRET_KEY": "baseline-test-only-not-a-production-secret",
        "DB_PASSWORD": "baseline-unused",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(BACKEND),
        "COVERAGE_FILE": str(OUTPUT / ".coverage"),
        "PYTEST_ADDOPTS": "",
        "PYTEST_PLUGINS": "",
    })
    return env


def pytest_worker() -> int:
    # Only this subprocess gets the test configuration and HTTP transport guard.
    os.environ.update(isolated_env())
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    import httpx
    import pytest
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.APP_ENV == "test"
    assert settings.database_url == "sqlite://"
    assert settings.WECHAT_MOCK and settings.LLM_MOCK
    attempts: list[str] = []

    def deny_http(*args, **kwargs):
        attempts.append("sync HTTP blocked")
        raise AssertionError("External HTTP is disabled in baseline verification")

    async def deny_async_http(*args, **kwargs):
        attempts.append("async HTTP blocked")
        raise AssertionError("External HTTP is disabled in baseline verification")

    # TestClient uses its own in-process transport; real httpx requests are blocked.
    httpx.HTTPTransport.handle_request = deny_http
    httpx.AsyncHTTPTransport.handle_async_request = deny_async_http
    result = pytest.main([
        "--tb=short", "--cov=app", "--cov-report=term-missing",
        f"--junitxml={OUTPUT / 'pytest.xml'}",
        "-o", f"cache_dir={OUTPUT / 'pytest-cache'}",
    ])
    isolation = {
        "app_env": settings.APP_ENV,
        "database_url": settings.database_url,
        "wechat_mock": settings.WECHAT_MOCK,
        "llm_mock": settings.LLM_MOCK,
        "external_http_attempts": len(attempts),
    }
    (OUTPUT / "isolation.json").write_text(
        json.dumps(isolation, indent=2), encoding="utf-8"
    )
    print("BASELINE_ISOLATION " + json.dumps(isolation))
    return int(result) if not attempts else 1


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = isolated_env()
    checks = [
        ("pytest", [sys.executable, str(Path(__file__).resolve()), "--pytest-worker"]),
        ("ruff", [sys.executable, "-m", "ruff", "check", "app", "tests", "--no-cache"]),
        ("mypy", [sys.executable, "-m", "mypy", "app", "--cache-dir", str(OUTPUT / "mypy-cache")]),
    ]
    results = {}
    for name, command in checks:
        print(f"Running {name}...", flush=True)
        completed = subprocess.run(
            command, cwd=BACKEND, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        log = completed.stdout + completed.stderr
        (OUTPUT / f"{name}.log").write_text(log, encoding="utf-8")
        results[name] = {"exit_code": completed.returncode, "log": f"{name}.log"}
        print(f"{name}: exit={completed.returncode}", flush=True)
        print("\n".join(log.splitlines()[-6:]), flush=True)
    packages = sorted(
        f"{d.metadata['Name']}=={d.version}" for d in distributions()
    )
    (OUTPUT / "installed-versions.txt").write_text(
        "\n".join(packages) + "\n", encoding="utf-8"
    )
    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "executable": sys.executable,
        "checks": results,
        "mypy_policy": "recorded_nonblocking_as_in_existing_ci",
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return int(any(results[name]["exit_code"] for name in ("pytest", "ruff")))


if __name__ == "__main__":
    raise SystemExit(pytest_worker() if "--pytest-worker" in sys.argv else main())
