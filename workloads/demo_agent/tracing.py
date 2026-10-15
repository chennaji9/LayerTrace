"""标准档三段 Span（T092）：模型调用 / 工具调用 / 汇总。

轻量档不依赖 Collector：未配置 OTLP 端点时为空操作。
"""

from __future__ import annotations

import contextlib
import os
import uuid

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "demo-agent")


@contextlib.contextmanager
def span(name: str, attributes: dict | None = None):
    """三段 Span：mod_ / tool_ / agg_ 前缀。

    - 标准档（配置了 OTLP_ENDPOINT）：经 OTLP 上报到 Collector；
    - 轻量档：空操作上下文，不引入依赖。
    """
    if not OTLP_ENDPOINT:
        yield {"span_id": uuid.uuid4().hex[:16], "exported": False}
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        yield {"span_id": uuid.uuid4().hex[:16], "exported": False}
        return

    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT)))
    tracer = (
        trace.TracerProvider().get_tracer(SERVICE_NAME) if False else trace.get_tracer(SERVICE_NAME)
    )
    with tracer.start_as_current_span(name) as otel_span:
        for key, value in (attributes or {}).items():
            # 仅受控属性；trace_id/task_id 不作为属性
            if key not in ("task_id", "trace_id", "prompt"):
                otel_span.set_attribute(key, value)
        yield {"span_id": format(otel_span.get_span_context().span_id, "016x"), "exported": True}
    provider.shutdown()
