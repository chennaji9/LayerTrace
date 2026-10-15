"""时态拓扑：按故障时刻读取有效关系（T040）。

- 有效关系：valid_from <= at < valid_to（或 valid_to 为空）；
- 从 root 出发限制深度与节点数，超限设置 truncated；
- 未指定 root 时返回全项目在 at 时刻的有效子图。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.models import Relationship, Resource


async def effective_relationships(
    session: AsyncSession, project_id: uuid.UUID, at: datetime
) -> list[Relationship]:
    return list(
        (
            await session.execute(
                select(Relationship)
                .where(
                    Relationship.project_id == project_id,
                    Relationship.valid_from <= at,
                )
                .order_by(Relationship.valid_from.asc())
            )
        )
        .scalars()
        .all()
    )


def _filter_valid(rels: list[Relationship], at: datetime) -> list[Relationship]:
    return [r for r in rels if r.valid_to is None or r.valid_to > at]


async def effective_topology(
    session: AsyncSession,
    project_id: uuid.UUID,
    at: datetime,
    *,
    root_id: uuid.UUID | None = None,
    max_depth: int | None = None,
    max_nodes: int | None = None,
) -> tuple[list[Resource], list[Relationship], bool]:
    """返回 (nodes, edges, truncated)。"""
    settings = get_settings()
    max_depth = max_depth or settings.topology_max_depth
    max_nodes = max_nodes or settings.topology_max_nodes

    history = await effective_relationships(session, project_id, at)
    rels = _filter_valid(history, at)
    # Some providers retain instance_of/depends_on facts after a generation
    # ends. A closed authoritative runs interval prevents those retained edges
    # from reviving that generation in the current graph. Historical reads
    # still include it while its runs interval was valid.
    known_instances = {rel.to_id for rel in history if rel.type == "runs"}
    active_instances = {rel.to_id for rel in rels if rel.type == "runs"}
    ended_instances = known_instances - active_instances
    rels = [
        rel
        for rel in rels
        if rel.from_id not in ended_instances and rel.to_id not in ended_instances
    ]
    truncated = False

    if root_id is None:
        keep_rels = rels
        node_ids = {r.from_id for r in rels} | {r.to_id for r in rels}
        if len(node_ids) > max_nodes:
            truncated = True
    else:
        # 双向 BFS 限制深度
        adjacency: dict[uuid.UUID, list[Relationship]] = {}
        for rel in rels:
            adjacency.setdefault(rel.from_id, []).append(rel)
            adjacency.setdefault(rel.to_id, []).append(rel)
        visited: set[uuid.UUID] = {root_id}
        frontier = {root_id}
        keep: list[Relationship] = []
        for _depth in range(max_depth):
            next_frontier: set[uuid.UUID] = set()
            for node in frontier:
                for rel in adjacency.get(node, []):
                    other = rel.to_id if rel.from_id == node else rel.from_id
                    if rel not in keep:
                        keep.append(rel)
                    if other not in visited:
                        visited.add(other)
                        next_frontier.add(other)
                        if len(visited) > max_nodes:
                            truncated = True
                            break
                if truncated:
                    break
            if truncated or not next_frontier:
                break
            frontier = next_frontier
        keep_rels = keep
        node_ids = visited

    nodes = (
        (
            await session.execute(
                select(Resource).where(
                    Resource.project_id == project_id,
                    Resource.id.in_(node_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    if root_id is not None and root_id not in {n.id for n in nodes}:
        root = (
            await session.execute(
                select(Resource).where(Resource.id == root_id, Resource.project_id == project_id)
            )
        ).scalar_one_or_none()
        if root is not None:
            nodes = [*nodes, root]

    return list(nodes), keep_rels, truncated
