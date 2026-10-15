"""Exercise the real client loop without network, processes, or wall-clock waits."""

import signal
import sys
from collections import deque
from types import SimpleNamespace

import httpx
import pytest

from workloads.demo_agent import load_generator as client

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
def prepare(monkeypatch):
    def setup(clock, *arguments):
        calls = []
        flushes = []
        monkeypatch.setattr(sys, "argv", ["load_generator", *arguments])
        monkeypatch.setattr(client, "_running", True)
        monkeypatch.setattr(client, "INTERVAL_SECONDS", 2.0)
        monkeypatch.setattr(client, "time", clock)
        monkeypatch.setattr(
            client,
            "signal",
            SimpleNamespace(SIGINT=signal.SIGINT, SIGTERM=signal.SIGTERM, signal=lambda *_: None),
        )
        monkeypatch.setattr(
            client,
            "httpx",
            SimpleNamespace(
                HTTPError=httpx.HTTPError,
                get=lambda *_args, **_kwargs: SimpleNamespace(status_code=200),
            ),
        )
        monkeypatch.setattr(
            client,
            "ev",
            SimpleNamespace(
                emitter=lambda: SimpleNamespace(flush=lambda: flushes.append(True) or 0)
            ),
        )
        monkeypatch.setattr(client, "paused", lambda: False)
        monkeypatch.setattr(
            client, "run_client_task", lambda *, topic: calls.append((clock.now, topic))
        )
        return calls, flushes

    return setup


@pytest.mark.parametrize(
    ("readings", "expected_sleeps"),
    [
        pytest.param([0.0, 0.0, 1.99, 2.01], [0.01], id="clock-crosses-deadline"),
        pytest.param([0.0, 0.0, 2.0], [], id="exact-deadline"),
        pytest.param([0.0, 0.0, 2.01], [], id="already-past-deadline"),
    ],
)
def test_deadline_boundary_never_passes_negative_sleep(prepare, readings, expected_sleeps):
    clock = Clock(readings)
    calls, flushes = prepare(clock, "--count", "1")
    client.main()
    assert calls == [(0.0, "memory")]
    assert clock.sleeps == pytest.approx(expected_sleeps)
    assert flushes == [True]


def test_default_count_runs_continuously_with_serial_two_second_spacing(prepare, monkeypatch):
    clock = Clock()
    calls, flushes = prepare(clock)
    active = False

    def task(*, topic):
        nonlocal active
        assert not active
        active = True
        calls.append((clock.now, topic))
        if len(calls) == 3:
            client._handle_stop()
        active = False

    monkeypatch.setattr(client, "run_client_task", task)
    client.main()
    assert calls == [(0.0, "memory"), (2.0, "memory"), (4.0, "memory")]
    assert clock.sleeps == [0.25] * 16
    assert flushes == [True]


def test_stop_signal_exits_current_wait_within_one_slice(prepare):
    clock = Clock()
    calls, flushes = prepare(clock, "--count", "10")
    clock.on_sleep = client._handle_stop
    client.main()
    assert calls == [(0.0, "memory")]
    assert clock.sleeps == [0.25]
    assert flushes == [True]


def test_pause_defers_task_and_custom_interval_is_retained(prepare, monkeypatch):
    clock = Clock()
    calls, flushes = prepare(clock, "--count", "1", "--interval", "1.3", "--topic", "cpu")
    pause_states = iter([True, False])
    monkeypatch.setattr(client, "paused", lambda: next(pause_states))
    client.main()
    assert len(calls) == 1
    assert calls[0][0] == pytest.approx(1.3)
    assert calls[0][1] == "cpu"
    assert sum(clock.sleeps) == pytest.approx(2.6)
    assert all(0 < wait <= 0.25 for wait in clock.sleeps)
    assert flushes == [True]
