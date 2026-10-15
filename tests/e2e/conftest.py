"""E2E 浏览器验收夹具（Playwright）。

- 桌面（1280x800）与移动（375x812）视口夹具。
- live_server：在测试库上启动真实 uvicorn 服务（loopback HTTP 仅用于同机验证）。
- 来源断言夹具：页面产生的数据请求必须且只能指向 /api/v1/*（FR-009）。
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
MOBILE_VIEWPORT = {"width": 375, "height": 812}

# e2e 测试服务固定令牌（仅测试进程，不用于生产）
E2E_TOKEN = "e2e-token-3f9a1c7d55b84e26"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(test_database_url: str) -> Iterator[str]:
    """以子进程方式启动真实 HTTP 服务并返回基址。

    使用 subprocess（python -m uvicorn）而非 multiprocessing.spawn：
    spawn 在 pytest 下会重导入 __main__ 造成子进程递归执行测试。
    """
    import subprocess
    import sys

    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": test_database_url,
            "API_TOKENS": E2E_TOKEN,
            "WEB_HOST": "127.0.0.1",
            "WEB_PORT": str(port),
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.control_plane.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    import urllib.request

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as res:
                if res.status == 200:
                    break
        except Exception:
            if process.poll() is not None:
                pytest.fail(f"live_server 进程提前退出（code={process.returncode}）")
            time.sleep(0.3)
    else:
        process.terminate()
        pytest.fail("live_server 未在超时内就绪")
    yield base
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


BASE_URL = os.environ.get("LAYERTRACE_E2E_BASE_URL", "")


def run_async(coro):
    """在独立线程中执行协程。

    Playwright 同步 API 会在主线程留下运行中的事件循环，因此 e2e 测试内的
    异步 DB 操作必须放到独立线程（线程内无循环，asyncio.run 可用）。
    """
    import asyncio
    import threading

    result: dict = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - 需跨线程传回
            result["error"] = exc

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join(timeout=120)
    if "error" in result:
        raise result["error"]
    if "value" not in result:
        raise RuntimeError("run_async 超时")
    return result["value"]


class OriginRecorder:
    """记录页面发出的数据请求并校验来源边界。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.data_requests: list[str] = []
        self.violations: list[str] = []

    def on_request(self, request: Any) -> None:
        url = request.url
        path = url[len(self.base_url) :] if url.startswith(self.base_url) else url
        # 静态资源允许；任何数据读取必须走 /api/v1/*
        if any(path.endswith(suffix) for suffix in (".js", ".css", ".png", ".svg", ".ico", ".woff2")):
            return
        if path.startswith("/api/v1/") or path in ("/", "/healthz") or path.startswith("/pages/"):
            if path.startswith("/api/v1/"):
                self.data_requests.append(path)
            return
        self.violations.append(path)


@pytest.fixture(scope="session")
def base_url(live_server: str) -> str:
    return live_server


@pytest.fixture(scope="session")
def playwright_instance():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright_instance):
    browser = playwright_instance.chromium.launch()
    yield browser
    browser.close()


@pytest.fixture
def desktop_page(browser, base_url):
    context = browser.new_context(
        viewport=DESKTOP_VIEWPORT,
        extra_http_headers={"Authorization": f"Bearer {E2E_TOKEN}"},
    )
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def mobile_page(browser, base_url):
    context = browser.new_context(
        viewport=MOBILE_VIEWPORT,
        extra_http_headers={"Authorization": f"Bearer {E2E_TOKEN}"},
    )
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture
def origin_assert(desktop_page, base_url):
    recorder = OriginRecorder(base_url)
    desktop_page.on("request", recorder.on_request)
    yield recorder
    assert not recorder.violations, (
        f"页面产生了契约外数据请求（必须仅经 /api/v1/*）：{recorder.violations}"
    )
