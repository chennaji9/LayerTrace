"""US3 E2E：同名节点、重建前后及历史时刻切换。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN, run_async

pytestmark = [pytest.mark.e2e]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


@pytest.fixture()
def e2e_env(test_database_url, clean_db, live_server):
    return live_server


def _api(base: str, method: str, path: str, *, params: dict | None = None, **kwargs) -> httpx.Response:
    res = httpx.request(method, f"{base}{path}", headers=AUTH, params=params, timeout=60, **kwargs)
    assert res.status_code == 200, f"{path} -> {res.status_code}: {res.text[:300]}"
    return res


def _seed(test_database_url: str) -> dict:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.models import Relationship
    from tests.seed_utils import seed_relationship, seed_resource

    t0 = datetime.now(UTC) - timedelta(minutes=10)
    t1 = datetime.now(UTC) - timedelta(minutes=5)
    tag = uuid.uuid4().hex[:6]

    async def _do() -> dict:
        engine = create_async_engine(test_database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            vm1 = await seed_resource(
                session, kind="vm", provider="zsvirt", source_id=f"{tag}-vm1",
                namespace="zsvirt.vm", external_id=f"{tag}-vm1", display_name="vm", now=t0,
            )
            vm2 = await seed_resource(
                session, kind="vm", provider="zsvirt", source_id=f"{tag}-vm2",
                namespace="zsvirt.vm", external_id=f"{tag}-vm2", display_name="vm", now=t0,
            )
            c1 = await seed_resource(
                session, kind="container", provider="docker", source_id=f"{tag}-c1",
                namespace="docker.container", external_id=f"{tag}-c1", display_name="web", now=t0,
            )
            c2 = await seed_resource(
                session, kind="container", provider="docker", source_id=f"{tag}-c2",
                namespace="docker.container", external_id=f"{tag}-c2", display_name="web", now=t0,
            )
            c3 = await seed_resource(
                session, kind="container", provider="docker", source_id=f"{tag}-c3",
                namespace="docker.container", external_id=f"{tag}-c3", display_name="web", now=t1,
            )
            await seed_relationship(session, from_resource=vm1, to_resource=c1, type_="contains", valid_from=t0)
            await seed_relationship(session, from_resource=vm2, to_resource=c2, type_="contains", valid_from=t0)
            await seed_relationship(session, from_resource=vm1, to_resource=c3, type_="contains", valid_from=t1)
            await session.execute(
                update(Relationship)
                .where(
                    Relationship.from_id == vm1.id,
                    Relationship.to_id == c1.id,
                    Relationship.type == "contains",
                )
                .values(valid_to=t1)
            )
            await session.commit()
            await engine.dispose()
            return {"vm1": str(vm1.id), "c1": str(c1.id), "c3": str(c3.id), "t0": t0, "t1": t1}

    return run_async(_do())


def test_us3_same_name_and_rebuild(desktop_page, e2e_env, test_database_url) -> None:
    base = e2e_env
    refs = _seed(test_database_url)

    # API：同名容器在当前拓扑中是两个节点
    topology = _api(base, "GET", "/api/v1/topology").json()
    containers = [n for n in topology["nodes"] if n["kind"] == "container"]
    names = [c["display_name"] for c in containers]
    assert names.count("web") >= 2

    # 历史时刻：t0+5min 时 vm1 下仍是旧容器 c1
    past = (refs["t0"] + timedelta(minutes=5)).isoformat()
    hist = _api(base, "GET", "/api/v1/topology", params={"at": past, "root_id": refs["vm1"]}).json()
    ids = {n["id"] for n in hist["nodes"] if n["kind"] == "container"}
    assert refs["c1"] in ids
    assert refs["c3"] not in ids

    # 当前：vm1 下是新容器 c3
    now_topo = _api(base, "GET", "/api/v1/topology", params={"root_id": refs["vm1"]}).json()
    ids_now = {n["id"] for n in now_topo["nodes"] if n["kind"] == "container"}
    assert refs["c3"] in ids_now
    assert refs["c1"] not in ids_now

    # 页面：拓扑页加载并提供时刻切换
    desktop_page.goto(f"{base}/pages/topology")
    desktop_page.wait_for_selector("#topo-meta", timeout=20000)
    assert desktop_page.is_visible("#topo-at")
