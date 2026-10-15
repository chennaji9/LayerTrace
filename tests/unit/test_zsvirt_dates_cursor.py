"""Platform lifecycle timestamps and independent stream failures retain evidence."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from integrations.zsvirt import provider as module
from integrations.zsvirt.provider import ZSvirtProvider, _platform_created_at

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
CURSOR = str(int(NOW.timestamp() * 1000) - 60_000)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Oct 14, 2026 10:18:58 PM", "2026-10-14T14:18:58+00:00"),
        ("2026-10-14T22:18:58", "2026-10-14T14:18:58+00:00"),
        ("2026-10-14T14:18:58Z", "2026-10-14T14:18:58+00:00"),
        ("2026-10-14T22:18:58+08:00", "2026-10-14T14:18:58+00:00"),
        ("bad timestamp", None),
        (None, None),
    ],
)
def test_platform_lifecycle_time_is_explicitly_shanghai(value, expected):
    assert _platform_created_at(value) == expected


@pytest.fixture
def frozen_clock(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedDatetime)


@pytest.mark.asyncio
async def test_platform_creation_does_not_backdate_an_observed_host_relationship(frozen_clock):
    def handle(request):
        if request.url.path.endswith("/hosts"):
            records = [{"uuid": "host-1", "name": "host", "createDate": "Oct 14, 2026 09:00:00 AM"}]
        else:
            records = [{"uuid": "vm-1", "hostUuid": "host-1", "name": "vm", "createDate": "Oct 14, 2026 10:18:58 PM"}]
        return httpx.Response(200, json={"inventories": records})

    provider = ZSvirtProvider(base_url="http://127.0.0.1:8080", token="test", transport=httpx.MockTransport(handle))
    batch = await provider.discover()
    vm = next(resource for resource in batch.resources if resource.kind == "vm")
    assert vm.attributes["platform_created_at"] == "2026-10-14T14:18:58+00:00"
    assert batch.relationships[0].valid_from == NOW
    assert batch.relationships[0].provenance == "observed"
    assert provider.health().state == "healthy"


def event_transport(*, alarm_status=200, event_status=200):
    def handle(request):
        is_alarm = request.url.path.endswith("/alarm-records")
        assert request.url.params["q"] == f"createTime>={int(CURSOR) - 300_000}"
        status = alarm_status if is_alarm else event_status
        return httpx.Response(
            status,
            json={"inventories": [{
                "id": "record-1", "dataUuid": "native-record", "alarmUuid": "native-alarm",
                "resourceUuid": "host-1", "namespace": "ZStack/Host", "alarmStatus": "Alarm",
                "metricName": "MemoryAvailableCapacity", "metricValue": 1234,
                "createTime": int(CURSOR) + 1_000,
            }] if is_alarm else []},
        )

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_partial_event_failure_emits_good_stream_and_retains_exact_cursor(frozen_clock):
    provider = ZSvirtProvider(base_url="http://127.0.0.1:8080", token="test", transport=event_transport(event_status=403))
    batch = await provider.read_events(CURSOR)
    assert batch.next_cursor == CURSOR
    assert len(batch.events) == 1
    event = batch.events[0]
    assert event.attributes["source_alarm_uuid"] == "native-alarm"
    assert event.attributes["platform_event_id"] == "native-record"
    assert event.namespace == "zsvirt.host" and event.provenance == "observed"
    assert provider.health().state == "degraded"
    assert provider.health().missing_permissions == ["/zwatch/event-records"]


@pytest.mark.asyncio
async def test_successful_streams_advance_cursor_only_after_all_streams_complete(frozen_clock):
    provider = ZSvirtProvider(base_url="http://127.0.0.1:8080", token="test", transport=event_transport())
    batch = await provider.read_events(CURSOR)
    assert batch.next_cursor == str(int(NOW.timestamp() * 1000))
    assert provider.health().state == "healthy"
    assert provider.health().missing_permissions == []


@pytest.mark.asyncio
async def test_all_streams_failed_cannot_report_an_advanced_cursor(frozen_clock):
    provider = ZSvirtProvider(base_url="http://127.0.0.1:8080", token="test", transport=event_transport(alarm_status=403, event_status=503))
    with pytest.raises(RuntimeError, match="cursor retained"):
        await provider.read_events(CURSOR)
    assert set(provider.capabilities().missing_permissions) == {
        "/zwatch/alarm-records", "/zwatch/event-records",
    }
