"""HTTP tool client. Failure facts come from the real tool HTTP response."""

from __future__ import annotations

import os

import httpx

TOOL_URL = os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8090").rstrip("/")


def call_tool(
    tool_name: str, arguments: dict, *, timeout: float = 10.0, task_id: str | None = None
) -> dict:
    response = httpx.post(
        f"{TOOL_URL}/tools/{tool_name}",
        json=arguments,
        headers={"X-Task-ID": task_id or ""},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()
