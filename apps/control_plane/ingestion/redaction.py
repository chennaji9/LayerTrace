"""入队前脱敏（FR-029、宪章 IV）。

在事件进入队列与持久化之前：
- 按允许列表过滤属性键；禁止键直接排除（提示词、完整回答、工具参数原文、
  Authorization、Cookie、API key、完整环境变量等）；
- 对字符串值执行令牌、手机号、邮箱过滤；
- 记录被排除的键名（不含值），保证脱敏动作可审计。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 禁止入队的属性键（规范化后小写匹配；子串命中即排除）
_BLOCKED_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "private_key",
    "environment",
    "env_vars",
    "environ",
    "prompt",  # 提示词（system_prompt/user_prompt/prompt 等）
    "completion",  # 模型完整回答
    "full_answer",
    "raw_answer",
    "model_response",
    "model_output_text",
    "tool_args",
    "tool_params",
    "tool_raw_params",
    "raw_params",
    "raw_arguments",
    "session_id",
)

# 明确允许的属性键前缀/键名（可读事实字段）
ALLOWED_KEY_PREFIXES = (
    "exit_code",
    "signal",
    "oom",
    "memory",
    "cpu",
    "duration",
    "limit",
    "quota",
    "throttling",
    "state",
    "reason",
    "error",
    "status",
    "container",
    "image",
    "restart",
    "pid",
    "vm",
    "host",
    "task",
    "trace",
    "scenario",
    "count",
    "bytes",
    "latency",
    "http",
    "tool_name",
    "model_name",
    "tokens_in",
    "tokens_out",
    "snapshot",
    "cpu_percent",
    "mem_bytes",
    "working_set",
    "gpu",
    "cuda",
    "device",
    "steal",
    "baseline",
    "clock",
    "meta",
)

_TOKEN_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),  # 典型 API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
)

_PHONE_PATTERN = re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

_REDACTED_TOKEN = "[REDACTED:credential]"
_REDACTED_PHONE = "[REDACTED:phone]"
_REDACTED_EMAIL = "[REDACTED:email]"
_OPAQUE_ID_KEYS = {
    "container_id",
    "instance_id",
    "service_instance_id",
    "model_instance_id",
    "tool_instance_id",
}
_OPAQUE_ID_PATTERN = re.compile(
    r"^[0-9a-f]{64}(?:@[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2}))?$"
)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def is_blocked_key(key: str) -> bool:
    norm = _normalize_key(key)
    return any(frag in norm for frag in _BLOCKED_KEY_FRAGMENTS)


def is_allowed_key(key: str) -> bool:
    norm = _normalize_key(key)
    return norm.startswith(ALLOWED_KEY_PREFIXES) or norm in {
        "type",
        "name",
        "kind",
        "message",
        "detail",
        "summary",
        "code",
        "attempt",
        "timeout_ms",
        "retries",
        "url_path",
        "method",
        "instance",
        "generation",
        "source_alarm_uuid",
    }


def scrub_value(value: str) -> str:
    """字符串值脱敏：令牌、手机号、邮箱。"""
    out = value
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(_REDACTED_TOKEN, out)
    if len(out) >= 11:
        out = _PHONE_PATTERN.sub(_REDACTED_PHONE, out)
        out = _EMAIL_PATTERN.sub(_REDACTED_EMAIL, out)
    return out


def _scrub_any(value, *, key: str | None = None):
    if isinstance(value, str):
        if key in _OPAQUE_ID_KEYS and _OPAQUE_ID_PATTERN.fullmatch(value):
            return value
        return scrub_value(value)
    if isinstance(value, dict):
        return {k: _scrub_any(v, key=k) for k, v in value.items() if not is_blocked_key(k)}
    if isinstance(value, list):
        return [_scrub_any(v, key=key) for v in value]
    return value


@dataclass
class RedactionResult:
    attributes: dict
    removed_keys: list[str]
    redacted_value_keys: list[str]


def redact_attributes(attributes: dict, *, strict_allowlist: bool = False) -> RedactionResult:
    """字段允许列表 + 值过滤。

    strict_allowlist=True 时只保留显式允许的键（用于更严格的外部入口）；
    默认模式排除禁止键后放行其余键，并对值脱敏。
    """
    cleaned: dict = {}
    removed: list[str] = []
    redacted_values: list[str] = []

    for key, value in attributes.items():
        if is_blocked_key(key):
            removed.append(key)
            continue
        if strict_allowlist and not is_allowed_key(key):
            removed.append(key)
            continue
        new_value = _scrub_any(value, key=key)
        if isinstance(value, str) and new_value != value:
            redacted_values.append(key)
        cleaned[key] = new_value

    return RedactionResult(
        attributes=cleaned, removed_keys=removed, redacted_value_keys=redacted_values
    )
