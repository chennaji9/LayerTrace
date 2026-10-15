"""控制面配置。

所有配置通过环境变量（或 .env）注入；`MODE` 默认 `prod`，禁止默认启用 lab。
数值上限与规格 FR-030、data-model.md 第 1/2.4 节一致，运行期不允许放宽。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 事件契约硬上限（与 contracts/events.schema.json 保持一致）
MAX_EVENT_BYTES = 256 * 1024  # 单条事件 UTF-8 JSON 序列化后最多 256 KiB
MAX_BATCH_BYTES = 8 * 1024 * 1024  # 单批请求最多 8 MiB
MAX_BATCH_EVENTS = 500  # 单批最多 500 条
MAX_ATTRIBUTES = 64  # 单条事件顶层属性最多 64 项
MAX_ATTRIBUTE_DEPTH = 8  # 属性嵌套深度最多 8 层


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 运行模式 ---
    mode: str = Field(default="prod", pattern="^(prod|lab|replay)$")
    # lab 写通道必须显式开启且仅在 MODE=lab 下生效
    lab_enabled: bool = False

    # --- 基础标识 ---
    tenant_id: str = "00000000-0000-0000-0000-000000000001"
    project_id: str = "00000000-0000-0000-0000-000000000001"
    instance_name: str = "layertrace-control-plane"

    # --- 数据库 / 指标 / 链路 ---
    database_url: str = "postgresql+asyncpg://layertrace:layertrace@localhost:5432/layertrace"
    prometheus_url: str = "http://localhost:9090"
    grafana_url: str | None = None
    tempo_url: str | None = None

    # --- ZSvirt 平台（只读） ---
    zsvirt_enabled: bool = False
    zsvirt_base_url: str | None = None
    zsvirt_token: str | None = Field(default=None, repr=False)
    zsvirt_account_name: str | None = None
    zsvirt_password_hash: str | None = Field(default=None, repr=False)
    zsvirt_ca_path: str | None = None
    zsvirt_verify_hostname: bool = True
    zsvirt_provider_instance: str = "zsvirt-lab"

    # --- 传输安全 ---
    # 只信任来自这些地址的 X-Forwarded-* 头（Caddy 所在网段）
    trusted_proxies: str = "127.0.0.1/32,::1/128"
    # 是否强制跨主机 HTTPS（loopback 始终允许 HTTP）
    require_tls_off_loopback: bool = True
    # 容器部署中，宿主机 loopback 经 DNAT 后呈现为固定网桥网关；默认不放行。
    # 仅应与只绑定 127.0.0.1 的端口映射配套使用。
    plaintext_client_cidrs: str = ""

    # --- 保留期（天） ---
    retention_metrics_days: int = Field(default=7, gt=0)
    retention_span_days: int = Field(default=3, gt=0)
    retention_event_days: int = Field(default=30, gt=0)
    retention_diagnosis_days: int = Field(default=30, gt=0)

    # --- 契约上限（禁止调高超规格值） ---
    max_event_bytes: int = MAX_EVENT_BYTES
    max_batch_bytes: int = MAX_BATCH_BYTES
    max_batch_events: int = MAX_BATCH_EVENTS
    max_attributes: int = MAX_ATTRIBUTES
    max_attribute_depth: int = MAX_ATTRIBUTE_DEPTH

    # --- 探针 spool 上限（先到为准） ---
    probe_spool_max_bytes: int = 100 * 1024 * 1024  # 100 MiB
    probe_spool_max_seconds: int = 600  # 10 分钟

    # --- 采集状态 ---
    heartbeat_interval_seconds: int = 15
    stale_after_missed_heartbeats: int = 3

    # --- 告警 / 事件单 ---
    alert_poll_seconds: int = 15
    incident_recovery_stable_seconds: int = 60

    # --- 延迟基线（R2/R4/Agent 共享） ---
    baseline_window_seconds: int = 900  # 15 分钟
    baseline_min_samples: int = 30
    baseline_relative_multiplier: float = 2.0
    baseline_absolute_increase_ms: float = 500.0
    agent_timeout_multiplier: int = 3

    # --- 诊断上下文 ---
    diagnosis_window_before_seconds: int = 120
    diagnosis_window_after_seconds: int = 30
    topology_max_depth: int = 8
    topology_max_nodes: int = 5000
    clock_skew_degrade_seconds: float = 5.0

    # --- 认证 ---
    # 主 API bearer 令牌（生产必须替换；通过环境注入，不入库不入仓）
    api_tokens: str = ""  # 逗号分隔 token 列表
    probe_token_ttl_seconds: int = 600

    # --- 可选组件 ---
    notification_sink_enabled: bool = False
    notification_sink_url: str | None = None
    replay_provider_enabled: bool = True

    # --- Web ---
    web_host: str = "127.0.0.1"  # HTTP 仅绑定 loopback；跨主机走 Caddy TLS
    web_port: int = 8000

    @property
    def api_token_list(self) -> list[str]:
        return [t.strip() for t in self.api_tokens.split(",") if t.strip()]

    @property
    def trusted_proxy_list(self) -> list[str]:
        return [t.strip() for t in self.trusted_proxies.split(",") if t.strip()]

    @property
    def plaintext_client_cidr_list(self) -> list[str]:
        return [t.strip() for t in self.plaintext_client_cidrs.split(",") if t.strip()]

    @property
    def allowed_event_types_layer(self) -> int:
        return MAX_ATTRIBUTE_DEPTH


@lru_cache
def get_settings() -> Settings:
    return Settings()
