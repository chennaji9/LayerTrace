"""Causal task scope follows runtime/dependency edges, never VM siblings."""


def get(context, name, default=None):
    return (
        context.get(name, default) if isinstance(context, dict) else getattr(context, name, default)
    )


def affected_instances(context, root=None):
    root = root or get(context, "symptom_resource_id")
    if not root:
        return set()
    nodes = {node["id"]: node for node in get(context, "topology_nodes", [])}
    edges = get(context, "topology_edges", [])
    roots = {root}
    if nodes.get(root, {}).get("kind") in {"vm", "host"}:
        # A VM/host fault can affect its descendants, but a container fault never
        # expands through its VM to sibling containers.
        changed = True
        while changed:
            previous = len(roots)
            roots.update(
                edge["to_id"]
                for edge in edges
                if edge.get("from_id") in roots and edge.get("type") in {"hosts", "contains"}
            )
            changed = previous != len(roots)
    instances = {root} if nodes.get(root, {}).get("kind") == "service_instance" else set()
    instances.update(
        edge["to_id"]
        for edge in edges
        if edge.get("from_id") in roots and edge.get("type") == "runs"
    )
    # A direct resource-attached task is acceptable, but no temporal fallback is used.
    instances.update(roots)
    services = {
        edge["to_id"]
        for edge in edges
        if edge.get("from_id") in instances and edge.get("type") == "instance_of"
    }
    changed = True
    while changed:
        before = len(instances) + len(services)
        dependent_services = set()
        for edge in edges:
            if edge.get("type") == "depends_on":
                if edge.get("to_id") in instances:
                    instances.add(edge["from_id"])
                if edge.get("to_id") in services:
                    dependent_services.add(edge["from_id"])
        services.update(dependent_services)
        instances.update(
            edge["from_id"]
            for edge in edges
            if edge.get("type") == "instance_of" and edge.get("to_id") in dependent_services
        )
        services.update(
            edge["to_id"]
            for edge in edges
            if edge.get("type") == "instance_of" and edge.get("from_id") in instances
        )
        changed = before != len(instances) + len(services)
    return instances


def baseline_instance_starts(context, root=None):
    """The causal path must exist when a baseline task starts.

    Each bound is the latest generation/dependency start along a real path
    from the symptom. Alternative paths use the earliest eligible bound.
    """
    from datetime import datetime

    nodes = {node["id"]: node for node in get(context, "topology_nodes", [])}
    edges = get(context, "topology_edges", [])
    root = root or get(context, "symptom_resource_id")
    if not root:
        return {}

    def timestamp(value):
        if not value:
            return None
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    bounds = {root: timestamp(nodes.get(root, {}).get("first_seen"))}
    allowed = affected_instances(context)
    changed = True
    while changed:
        changed = False
        for edge in edges:
            source, target, kind = edge.get("from_id"), edge.get("to_id"), edge.get("type")
            candidates = []
            if kind in {"hosts", "contains", "runs"} and source in bounds:
                candidates.append((target, bounds[source]))
            if kind == "instance_of" and source in bounds:
                candidates.append((target, bounds[source]))
            if kind == "depends_on" and target in bounds:
                candidates.append((source, bounds[target]))
            if kind == "instance_of" and target in bounds and source in allowed:
                # Only instances already reached through causal traversal qualify.
                candidates.append((source, bounds[target]))
            for key, parent_start in candidates:
                starts = [
                    start
                    for start in (
                        parent_start,
                        timestamp(edge.get("valid_from")),
                        timestamp(nodes.get(key, {}).get("first_seen")),
                    )
                    if start is not None
                ]
                start = max(starts) if starts else None
                if key not in bounds or (
                    bounds[key] is not None and (start is None or start < bounds[key])
                ):
                    bounds[key] = start
                    changed = True
    return {key: bounds.get(key) for key in allowed if bounds.get(key) is not None}


def affected_tasks(context, root=None):
    ids = affected_instances(context, root)
    bounds = baseline_instance_starts(context, root)
    return [
        task
        for task in get(context, "tasks", [])
        if task.get("service_instance_id") in ids
        and _eligible_at(task.get("started_at"), bounds.get(task.get("service_instance_id")))
    ]


def _eligible_at(value, bound):
    from datetime import datetime

    if bound is None:
        return True
    if value is None:
        return False
    at = datetime.fromisoformat(value) if isinstance(value, str) else value
    return at >= bound


def related_event(event, context, root=None):
    from datetime import datetime, timedelta

    resource_id = event.get("resource_id")
    if resource_id not in affected_instances(context, root):
        return False
    bound = baseline_instance_starts(context, root).get(resource_id)
    if bound is None:
        return True
    started = event.get("occurred_at")
    task_id = (event.get("correlation") or {}).get("task_id")
    task = next(
        (
            task
            for task in get(context, "tasks", [])
            if task_id
            and task.get("task_id") == task_id
            and task.get("service_instance_id") == resource_id
        ),
        None,
    )
    if task is not None:
        started = task.get("started_at")
    elif event.get("type") in {
        "task.succeeded",
        "task.failed",
        "task.timed_out",
        "model.completed",
        "model.succeeded",
        "model.failed",
    }:
        # Terminal-only receipts retain a factual duration even when a start
        # receipt has not arrived. Reject requests crossing a dependency change.
        payload = event.get("payload") or {}
        duration = payload.get("duration_ms", payload.get("model_duration_ms"))
        if started and isinstance(duration, int | float) and duration >= 0:
            at = datetime.fromisoformat(started) if isinstance(started, str) else started
            started = at - timedelta(milliseconds=duration)
    return _eligible_at(started, bound)


def owning_containers(context, instance_ids):
    nodes = get(context, "topology_nodes", [])
    own = {n["id"] for n in nodes if n.get("kind") == "container" and n["id"] in instance_ids}
    own.update(
        edge["from_id"]
        for edge in get(context, "topology_edges", [])
        if edge.get("type") == "runs" and edge.get("to_id") in instance_ids
    )
    return own
