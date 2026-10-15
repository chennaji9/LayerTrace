"""稳定游标分页。

- limit 约束在 1-200（openapi Limit 参数一致）。
- 游标编码 (sort_value, id) 对，排序稳定（相同 sort_value 时按 id 决胜），
  插入/删除不影响未读页。
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import Query
from sqlalchemy import tuple_

from apps.control_plane.api.errors import ApiError


def parse_limit(
    limit: int = Query(default=50, ge=1, le=200, description="每页条数 1-200"),
) -> int:
    return limit


def encode_cursor(sort_value: Any, last_id: str) -> str:
    payload = json.dumps([str(sort_value), last_id], ensure_ascii=False)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError
        return str(payload[0]), str(payload[1])
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise ApiError(400, "invalid_cursor", "游标无效") from exc


def apply_descending_cursor(query, timestamp_column, id_column, cursor: str | None):
    """Use the last returned timestamp/UUID as an exclusive keyset boundary."""
    if not cursor:
        return query
    try:
        timestamp, key = decode_cursor(cursor)
        at = datetime.fromisoformat(timestamp)
        if at.tzinfo is None:
            raise ValueError
        resource_id = uuid.UUID(key)
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "invalid_cursor", "游标无效") from exc
    return query.where(tuple_(timestamp_column, id_column) < tuple_(at, resource_id))


def paginate(items: list[Any], *, cursor_factory, limit: int, sort_key, id_key: str = "id"):
    """对已按 (sort, id) 排序的行列表应用游标过滤并生成下一页游标。

    items 必须已按 sort_key 升序、id_key 决胜排序。
    cursor_factory(row) 返回该行的游标字符串。
    """
    page = items[:limit]
    next_cursor = None
    if len(items) > limit and page:
        last = page[-1]
        next_cursor = cursor_factory(last)
    return page, next_cursor
