"""VM-scoped runtime discovery, stable generations and explicit dependencies."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import select

from apps.control_plane.api.errors import ApiError
from apps.control_plane.ingestion.redaction import redact_attributes
from apps.control_plane.inventory.service import bind_alias, upsert_relationship, upsert_resource
from apps.control_plane.models import Relationship, Resource, ResourceSnapshot


def _started_at(value, observed):
    try:
        started = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        if started.tzinfo is not None and 2000 <= started.year and started <= observed:
            return started
    except (ValueError, TypeError):
        pass
    return observed


async def apply_probe_inventory(session, *, principal, inventory):
    vm = await session.get(Resource, principal.vm_id)
    if vm is None:
        raise ApiError(403, "forbidden", "绑定 VM 不存在")
    observed = inventory.observed_at
    vm_key = str(vm.id)
    vm.last_seen = max(vm.last_seen, observed)
    await bind_alias(
        session,
        resource_id=vm.id,
        namespace="zsvirt.vm",
        external_id=vm.source_id,
        valid_from=vm.first_seen,
        provenance=vm.provenance,
    )
    instances = {}
    containers = set()
    allowed_labels = {
        "layertrace-service",
        "layertrace.service",
        "layertrace-test",
        "layertrace.instance_id",
        "layertrace.depends_on",
    }
    for item in inventory.containers:
        started = _started_at(item.started_at, observed)
        created = _started_at(item.created_at, started)
        attributes = redact_attributes(
            {
                "state": item.state,
                "vm_id": vm_key,
                "vm_source_id": vm.source_id,
                "probe_id": str(principal.probe_id),
                "started_at": item.started_at,
                "limits": item.limits,
                "metrics": item.metrics,
                "observed_at": observed.isoformat(),
                "labels": {k: v for k, v in item.labels.items() if k in allowed_labels},
            }
        ).attributes
        container = await upsert_resource(
            session,
            tenant_id=vm.tenant_id,
            project_id=vm.project_id,
            kind="container",
            provider="docker",
            provider_instance=vm_key,
            source_id=item.container_id,
            display_name=item.name,
            attributes=attributes,
            provenance="observed",
            now=created,
            namespace="docker.container",
            external_id=item.container_id,
        )
        container.last_seen = max(container.last_seen, observed)
        container.deleted_at = None
        containers.add(container.id)
        await upsert_relationship(
            session,
            tenant_id=vm.tenant_id,
            project_id=vm.project_id,
            from_id=vm.id,
            type_="contains",
            to_id=container.id,
            valid_from=started,
            valid_to=None,
            source=f"probe:{principal.probe_id}",
            provenance="observed",
            from_kind="vm",
            to_kind="container",
        )
        limits = redact_attributes(item.limits).attributes
        config_hash = hashlib.sha256(json.dumps(limits, sort_keys=True).encode()).hexdigest()
        latest = (
            await session.execute(
                select(ResourceSnapshot)
                .where(ResourceSnapshot.resource_id == container.id)
                .order_by(ResourceSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (
            latest is None
            or latest.config_hash != config_hash
            or latest.image_digest != item.image_digest
        ):
            session.add(
                ResourceSnapshot(
                    resource_id=container.id,
                    captured_at=observed,
                    limits=limits,
                    image_digest=item.image_digest,
                    config_hash=config_hash,
                    provenance="observed",
                )
            )
        if not item.service_name or not item.instance_id:
            continue
        expected = f"{item.container_id}@{item.started_at}"
        if item.instance_id != expected:
            raise ApiError(
                422, "invalid_instance_id", "实例身份必须由完整容器 ID 与原始 StartedAt 构成"
            )
        service = await upsert_resource(
            session,
            tenant_id=vm.tenant_id,
            project_id=vm.project_id,
            kind="service",
            provider="application",
            provider_instance=vm_key,
            source_id=item.service_name,
            display_name=item.service_name,
            provenance="observed",
            now=started,
        )
        instance = await upsert_resource(
            session,
            tenant_id=vm.tenant_id,
            project_id=vm.project_id,
            kind="service_instance",
            provider="application",
            provider_instance=vm_key,
            source_id=item.instance_id,
            display_name=f"{item.service_name} · {item.container_id[:12]}",
            attributes={
                "vm_id": vm_key,
                "container_id": item.container_id,
                "service_name": item.service_name,
                "state": item.state,
                "started_at": item.started_at,
                "probe_id": str(principal.probe_id),
            },
            provenance="observed",
            now=started,
            namespace="otel.service_instance",
            external_id=item.instance_id,
        )
        instance.last_seen = max(instance.last_seen, observed)
        instances[item.instance_id] = instance
        old = (
            (
                await session.execute(
                    select(Relationship).where(
                        Relationship.from_id == container.id,
                        Relationship.type == "runs",
                        Relationship.valid_to.is_(None),
                        Relationship.to_id != instance.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for relation in old:
            relation.valid_to = max(started, relation.valid_from + timedelta(microseconds=1))
        for source, type_, target, source_kind, target_kind in [
            (container, "runs", instance, "container", "service_instance"),
            (instance, "instance_of", service, "service_instance", "service"),
        ]:
            await upsert_relationship(
                session,
                tenant_id=vm.tenant_id,
                project_id=vm.project_id,
                from_id=source.id,
                type_=type_,
                to_id=target.id,
                valid_from=started,
                valid_to=None,
                source=f"probe:{principal.probe_id}",
                provenance="observed",
                from_kind=source_kind,
                to_kind=target_kind,
            )
    for dependency in inventory.dependencies:
        source = instances.get(dependency.from_instance_id)
        target = instances.get(dependency.to_instance_id)
        if source is None or target is None:
            raise ApiError(422, "unknown_dependency", "依赖端点必须属于本次绑定 VM 的库存")
        await upsert_relationship(
            session,
            tenant_id=vm.tenant_id,
            project_id=vm.project_id,
            from_id=source.id,
            type_="depends_on",
            to_id=target.id,
            valid_from=max(source.first_seen, target.first_seen),
            valid_to=None,
            source=f"probe:{principal.probe_id}",
            provenance="observed",
            from_kind="service_instance",
            to_kind="service_instance",
        )
    if inventory.complete:
        old = (
            (
                await session.execute(
                    select(Relationship).where(
                        Relationship.from_id == vm.id,
                        Relationship.type == "contains",
                        Relationship.valid_to.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for relation in old:
            if relation.to_id not in containers:
                relation.valid_to = max(observed, relation.valid_from + timedelta(microseconds=1))
                resource = await session.get(Resource, relation.to_id)
                if resource is not None:
                    resource.deleted_at = observed
                    outgoing = (
                        (
                            await session.execute(
                                select(Relationship).where(
                                    Relationship.from_id == resource.id,
                                    Relationship.type == "runs",
                                    Relationship.valid_to.is_(None),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for run in outgoing:
                        run.valid_to = max(observed, run.valid_from + timedelta(microseconds=1))
    # Resolve delayed facts using the identity valid at the original occurrence time.
    from apps.control_plane.ingestion.repository import resolve_pending_events

    resolved = await resolve_pending_events(session, vm.project_id)
    await session.flush()
    return {"containers": len(containers), "instances": len(instances), "resolved_events": resolved}
