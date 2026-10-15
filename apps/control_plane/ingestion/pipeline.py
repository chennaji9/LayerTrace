"""One durable consumer for real and replay facts. Facts and receipts commit together."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from apps.control_plane.diagnosis.service import sweep_pending_diagnoses
from apps.control_plane.diagnosis.task_interruption import infer_task_interruptions
from apps.control_plane.incidents.event_alerts import process_event_alerts, process_event_recoveries
from apps.control_plane.incidents.recovery import sweep_recoveries
from apps.control_plane.models import Event, EventReceipt
from apps.control_plane.tasks.projection import apply_task_events


async def consume_events(session, *, limit=500):
    # Replay requests and the worker share one transaction-scoped consumer lock.
    # Receipts are committed in the same transaction as projections/diagnoses.
    await session.execute(select(func.pg_advisory_xact_lock(1279358036)))
    events = (
        (
            await session.execute(
                select(Event)
                .outerjoin(EventReceipt, EventReceipt.event_id == Event.id)
                .where(EventReceipt.event_id.is_(None), Event.resource_id.isnot(None))
                .order_by(Event.occurred_at, Event.observed_at, Event.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if events:
        for event in events:
            await apply_task_events(session, [event])
            await infer_task_interruptions(session, [event])
        await process_event_alerts(session, events)
        await process_event_recoveries(session, events)
        for event in events:
            session.add(EventReceipt(event_id=event.id, processed_at=datetime.now(UTC)))
        await session.flush()
    await sweep_pending_diagnoses(session)
    await sweep_recoveries(session)
    return len(events)
