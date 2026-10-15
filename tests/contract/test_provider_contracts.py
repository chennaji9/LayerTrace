"""US4 契约测试：ReplayProvider / DockerRuntimeProvider / ZSvirtProvider 同一契约套件。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = [pytest.mark.contract]

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


@pytest.fixture()
def replay_provider():
    from integrations.replay.provider import replay_provider_for

    return replay_provider_for("story-oom", base_time=NOW)


@pytest.fixture()
def docker_provider():
    from agents.vm_probe.runtime import discovery_batch

    return discovery_batch([], "vm-1")


@pytest.fixture()
def zsvirt_provider():
    from integrations.zsvirt.provider import ZSvirtProvider

    return ZSvirtProvider(base_url=None, token=None)


async def test_infrastructure_contract_replay(replay_provider) -> None:
    # capabilities / discover / read_events / read_metrics / health
    caps = replay_provider.capabilities()
    assert isinstance(caps.resource_kinds, list)
    batch = await replay_provider.discover()
    assert hasattr(batch, "resources") and hasattr(batch, "relationships")
    events = await replay_provider.read_events()
    assert all(e.provenance == "replayed" for e in events.events)
    metrics = await replay_provider.read_metrics(
        ["story-oom-vm-1"], (NOW - timedelta(minutes=1), NOW + timedelta(minutes=1))
    )
    assert hasattr(metrics, "series")
    health = replay_provider.health()
    assert health.state in ("healthy", "degraded", "stale", "unavailable", "permission_denied", "not_configured")


async def test_infrastructure_contract_zsvirt(zsvirt_provider) -> None:
    assert zsvirt_provider.capabilities().notes["state"] == "not_configured"
    health = zsvirt_provider.health()
    # 未配置时诚实报告 not_configured，禁止伪装健康
    assert health.state == "not_configured"
    assert zsvirt_provider.enabled is False


async def test_runtime_contract_docker(docker_provider) -> None:
    assert hasattr(docker_provider, "instances")
    assert docker_provider.vm_namespace == "zsvirt.vm"
