"""Read-only Docker API client. Events are finite NDJSON windows."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

ALLOWED_OPERATIONS = {"inspect", "events", "stats"}
DENIED_OPERATIONS = {"create", "exec", "delete", "kill", "restart", "update", "pause", "unpause"}


class DockerProxyClient:
    def __init__(self, base_url: str, *, container_label: str = "layertrace.observe=true") -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=15)
        self.container_label = container_label

    def _check(self, operation: str) -> None:
        if operation not in ALLOWED_OPERATIONS:
            raise PermissionError(f"Docker write/unknown operation denied: {operation}")

    def _filters(self, *, events: bool = False) -> str:
        filters = {"label": [self.container_label]} if self.container_label else {}
        if events:
            filters["type"] = ["container"]
        return json.dumps(filters)

    def list_containers(self) -> list[dict[str, Any]]:
        self._check("inspect")
        res = self._client.get("/containers/json", params={"all": "1", "filters": self._filters()})
        res.raise_for_status()
        return res.json()

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        self._check("inspect")
        res = self._client.get(f"/containers/{container_id}/json")
        res.raise_for_status()
        return res.json()

    def container_events(
        self, since: int | None = None, *, until: int | None = None
    ) -> list[dict[str, Any]]:
        self._check("events")
        until = int(time.time()) - 1 if until is None else until
        params = {
            "since": since if since is not None else until - 30,
            "until": until,
            "filters": self._filters(events=True),
        }
        res = self._client.get("/events", params=params)
        res.raise_for_status()
        return [json.loads(line) for line in res.iter_lines() if line.strip()]

    def container_stats(self, container_id: str, stream: bool = False) -> dict[str, Any]:
        self._check("stats")
        if stream:
            raise ValueError("Only finite stats snapshots are supported")
        res = self._client.get(
            f"/containers/{container_id}/stats", params={"stream": "false", "one-shot": "true"}
        )
        res.raise_for_status()
        return res.json()

    def close(self) -> None:
        self._client.close()
