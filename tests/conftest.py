"""全局测试夹具。

- PostgreSQL 测试库：优先使用 LAYERTRACE_TEST_DATABASE_URL；否则通过 Docker 启动
  独立 postgres:16.4-alpine（端口 55432），会话结束即销毁，不触碰开发库。
- 每个测试之间清空全部表（TRUNCATE ... CASCADE），保证隔离。
- 迁移通过 Alembic API 执行，与生产路径一致。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_PG_PORT = 55432
TEST_PG_CONTAINER = "layertrace-test-pg"
TEST_URL_EXPLICIT = bool(
    os.environ.get("LAYERTRACE_TEST_DATABASE_URL")
    or os.environ.get("LAYERTRACE_TEST_DATABASE_URL_EXPLICIT")
)

# 与 apps.control_plane.config 中的异步驱动保持一致；psycopg3 使用原生 postgresql:// URL
ASYNC_TEST_URL = os.environ.get(
    "LAYERTRACE_TEST_DATABASE_URL",
    "postgresql+asyncpg://layertrace:layertrace@127.0.0.1:55432/layertrace_test",
)
# Native psycopg is used only by synchronous test fixtures.
SYNC_TEST_URL = ASYNC_TEST_URL.replace("+asyncpg", "")  # 原生 psycopg3
ALEMBIC_TEST_URL = ASYNC_TEST_URL


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=20,
            check=True,
        )
        return True
    except Exception:
        return False


def _start_test_postgres() -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            TEST_PG_CONTAINER,
            "-e",
            "POSTGRES_USER=layertrace",
            "-e",
            "POSTGRES_PASSWORD=layertrace",
            "-e",
            "POSTGRES_DB=layertrace_test",
            "-p",
            f"127.0.0.1:{TEST_PG_PORT}:5432",
            "postgres:16.4-alpine3.20",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _stop_test_postgres() -> None:
    subprocess.run(
        ["docker", "rm", "-f", TEST_PG_CONTAINER],
        capture_output=True,
        timeout=60,
    )


def _wait_postgres(url: str, timeout: float = 60.0) -> bool:
    """同步探测（psycopg）：不依赖事件循环，任何 fixture 上下文下都可用。"""
    import psycopg

    first_error: Exception | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                url.replace("+asyncpg", "").replace("+psycopg", ""),
                connect_timeout=3,
                autocommit=True,
            ):
                pass
            return True
        except Exception as exc:
            if first_error is None:
                first_error = exc
                print(f"[conftest] PG 探测失败：{type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.5)
    return False


def _run_migrations(revision: str = "head") -> None:
    # Playwright's sync fixture may already own this process' event loop.
    # Run the production migration CLI in its own process and async loop.
    os.environ["DATABASE_URL"] = ALEMBIC_TEST_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", revision],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        diagnostic = result.stderr.replace(ALEMBIC_TEST_URL, "[redacted database URL]")
        raise RuntimeError("Test database migration failed: " + diagnostic)


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """返回异步测试库 URL；必要时拉起一次性 PostgreSQL 容器并执行迁移。"""
    os.environ["LAYERTRACE_TEST_DATABASE_URL"] = ASYNC_TEST_URL
    if TEST_URL_EXPLICIT:
        assert _wait_postgres(ASYNC_TEST_URL), "显式测试 PostgreSQL 未在超时内就绪"
        _run_migrations()
        yield ASYNC_TEST_URL
    elif _docker_available():
        running = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", TEST_PG_CONTAINER],
            capture_output=True,
            text=True,
        )
        if running.stdout.strip() != "true":
            _stop_test_postgres()
            _start_test_postgres()
        assert _wait_postgres(ASYNC_TEST_URL), "测试 PostgreSQL 未在超时内就绪"
        _run_migrations()
        yield ASYNC_TEST_URL
        _stop_test_postgres()
    else:
        pytest.fail("需要 Docker 或显式设置 LAYERTRACE_TEST_DATABASE_URL")


@pytest.fixture
def clean_db(test_database_url: str) -> Iterator[None]:
    """清空全部业务表，保持隔离（保留 alembic_version）。需要库的测试显式请求本夹具。"""
    import psycopg

    with psycopg.connect(SYNC_TEST_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename != 'alembic_version'
                """
            )
            tables = [row[0] for row in cur.fetchall()]
            if tables:
                cur.execute(f"TRUNCATE {', '.join(sorted(tables))} CASCADE")
        conn.commit()
    yield


@pytest.fixture
async def db_session(test_database_url: str) -> AsyncIterator:
    """提供绑定到当前事件循环的异步 SQLAlchemy 会话（测试前清表）。"""
    import psycopg
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    with psycopg.connect(SYNC_TEST_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename != 'alembic_version'
                """
            )
            tables = [row[0] for row in cur.fetchall()]
            if tables:
                cur.execute(f"TRUNCATE {', '.join(sorted(tables))} CASCADE")
        conn.commit()

    engine = create_async_engine(test_database_url, echo=False)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
