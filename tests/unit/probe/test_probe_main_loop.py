"""Exercise probe waiting and renewal in the real main loop without external services."""

import signal
from collections import deque
from types import SimpleNamespace

import httpx
import pytest

from agents.vm_probe import __main__ as probe

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self, readings=()):
        self.now = 0.0
        self.readings = deque(readings)
        self.sleeps = []
        self.on_sleep = None

    def monotonic(self):
        if self.readings:
            self.now = self.readings.popleft()
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        assert seconds <= 0.25
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


@pytest.fixture
def prepare(monkeypatch, tmp_path):
    def setup(clock, *, cycles=2, renew=False, registration_failures=0):
        calls = SimpleNamespace(
            collected=[], updates=[], registrations=0, identity_loads=0, closed=[], uploads=[]
        )
        handlers = {}
        config = SimpleNamespace(
            validate=lambda: [],
            spool_max_bytes=1024,
            spool_max_seconds=60,
            state_dir=str(tmp_path),
            docker_proxy_url="http://unused",
            container_label="unused",
            metrics_host="127.0.0.1",
            metrics_port=0,
            sample_interval=1,
            inventory_interval=10,
            heartbeat_interval=1,
            api_base="http://unused",
            tls_ca=True,
            identity_path=str(tmp_path / "identity.json"),
            vm_external_id="vm-test",
        )
        identity = SimpleNamespace(
            credential=None if renew else "test-credential",
            probe_id="test-probe",
            vm_id="vm-test",
            renew_due=renew,
        )

        def load():
            calls.identity_loads += 1

        def expired(*, margin_seconds=0):
            return identity.renew_due if margin_seconds else not identity.credential

        def register():
            calls.registrations += 1
            if calls.registrations <= registration_failures:
                raise httpx.ConnectError("registration temporarily unavailable")
            identity.credential = "renewed-test-credential"
            identity.renew_due = False

        identity.load = load
        identity.expired = expired
        identity.register = register
        identity.auth_headers = lambda: {}

        def discover(*_args):
            calls.collected.append(clock.now)
            if len(calls.collected) == cycles:
                handlers[signal.SIGTERM]()
            return []

        class Api:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, path, **_kwargs):
                calls.uploads.append(path)
                return SimpleNamespace(raise_for_status=lambda: None)

        spool = SimpleNamespace(
            peek_batch=lambda **_kwargs: [],
            close=lambda: calls.closed.append("spool"),
            depth=0,
            dropped_count=0,
            oldest_queued_at=None,
            gaps=[],
        )
        monkeypatch.setattr(probe, "ProbeConfig", lambda: config)
        monkeypatch.setattr(probe, "ProbeIdentity", lambda _config: identity)
        monkeypatch.setattr(probe, "BoundedSpool", lambda **_kwargs: spool)
        monkeypatch.setattr(
            probe, "DockerProxyClient",
            lambda *_args, **_kwargs: SimpleNamespace(close=lambda: calls.closed.append("docker")),
        )
        monkeypatch.setattr(
            probe, "EventBackfill",
            lambda _spool: SimpleNamespace(poll=lambda _client: [], commit_cursor=lambda: None),
        )
        monkeypatch.setattr(
            probe, "MetricsExporter",
            lambda *_args, **_kwargs: SimpleNamespace(
                update=lambda samples: calls.updates.append(samples),
                close=lambda: calls.closed.append("exporter"),
            ),
        )
        monkeypatch.setattr(probe, "discover", discover)
        monkeypatch.setattr(probe, "write_identity", lambda *_args: None)
        monkeypatch.setattr(
            probe, "signal",
            SimpleNamespace(
                SIGINT=signal.SIGINT,
                SIGTERM=signal.SIGTERM,
                signal=lambda number, handler: handlers.__setitem__(number, handler),
            ),
        )
        monkeypatch.setattr(probe, "time", clock)
        monkeypatch.setattr(
            probe, "httpx", SimpleNamespace(Client=lambda **_kwargs: Api(),
                                           HTTPStatusError=httpx.HTTPStatusError)
        )
        return calls, handlers

    return setup


@pytest.mark.parametrize(
    ("readings", "expected_sleeps"),
    [
        pytest.param([0.0, 0.99, 1.01], [0.01], id="clock-crosses-deadline"),
        pytest.param([0.0, 1.0], [], id="exact-deadline"),
        pytest.param([0.0, 1.01], [], id="already-past-deadline"),
    ],
)
def test_probe_deadline_boundary_never_passes_negative_sleep(prepare, readings, expected_sleeps):
    clock = Clock(readings)
    calls, _ = prepare(clock)
    probe.main()
    assert len(calls.collected) == len(calls.updates) == 2
    assert clock.sleeps == pytest.approx(expected_sleeps)
    assert calls.closed == ["docker", "spool", "exporter"]


def test_probe_collects_on_normal_interval_until_stopped(prepare):
    clock = Clock()
    calls, _ = prepare(clock, cycles=4)
    probe.main()
    assert calls.collected == [0.0, 1.0, 2.0, 3.0]
    assert len(calls.updates) == 4
    assert clock.sleeps == [0.25] * 12
    assert calls.closed == ["docker", "spool", "exporter"]


def test_probe_stop_signal_leaves_wait_after_one_slice(prepare):
    clock = Clock()
    calls, handlers = prepare(clock, cycles=10)
    clock.on_sleep = lambda: handlers[signal.SIGTERM]()
    probe.main()
    assert calls.collected == [0.0]
    assert clock.sleeps == [0.25]
    assert calls.closed == ["docker", "spool", "exporter"]


@pytest.mark.parametrize("registration_failures", [0, 1])
def test_probe_renewal_continues_collection_in_same_main_loop(prepare, registration_failures):
    clock = Clock()
    calls, _ = prepare(
        clock, cycles=3, renew=True, registration_failures=registration_failures
    )
    probe.main()
    assert calls.identity_loads == 1
    assert calls.registrations == registration_failures + 1
    assert calls.collected == [0.0, 1.0, 2.0]
    assert len(calls.updates) == 3
    assert calls.uploads == ["/api/v1/probes/test-probe/heartbeat"] * 2
    assert calls.closed == ["docker", "spool", "exporter"]
