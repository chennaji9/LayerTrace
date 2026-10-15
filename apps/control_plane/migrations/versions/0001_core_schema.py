"""core schema: T015-T022 全部表、索引、唯一约束与检查约束

Revision ID: 0001
Revises:
Create Date: 2026-09-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "resources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_instance", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("attributes", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "provider", "provider_instance", "kind", "source_id",
            name="uq_resource_identity",
        ),
        sa.CheckConstraint(
            "kind IN ('project','host','vm','container','service','service_instance','gpu','vgpu')",
            name="ck_resource_kind",
        ),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_resource_provenance",
        ),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_resource_seen_order"),
    )
    op.create_index("ix_resource_project_kind", "resources", ["project_id", "kind"])

    op.create_table(
        "resource_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resource_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("confirmed_by", sa.String(256), nullable=True),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_alias_validity"),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_alias_provenance",
        ),
    )
    op.create_index(
        "uq_alias_active", "resource_aliases", ["namespace", "external_id"],
        unique=True, postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index("ix_alias_resource", "resource_aliases", ["resource_id"])

    op.create_table(
        "relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "from_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column(
            "to_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("confidence_note", sa.Text(), nullable=True),
        sa.CheckConstraint("from_id <> to_id", name="ck_relationship_no_self"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_relationship_validity"),
        sa.CheckConstraint(
            "type IN ('hosts','contains','runs','instance_of','depends_on','attached_to','backed_by','belongs_to')",
            name="ck_relationship_type",
        ),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_relationship_provenance",
        ),
    )
    op.create_index("ix_relationship_to", "relationships", ["to_id"])
    op.create_index("ix_relationship_from", "relationships", ["from_id"])
    op.create_index("ix_relationship_valid", "relationships", ["from_id", "type", "to_id"])
    # 同一关系三元组有效区间不得重叠
    op.execute(
        "ALTER TABLE relationships ADD CONSTRAINT ex_relationship_no_overlap "
        "EXCLUDE USING gist ("
        " from_id WITH =, to_id WITH =, type WITH =, "
        " tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz)) WITH &&)"
    )

    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.String(256), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "resource_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("resource_ref", JSONB(), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("correlation", JSONB(), nullable=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("raw_ref", sa.String(1024), nullable=True),
        sa.Column("resolution_state", sa.String(16), nullable=False),
        sa.Column("scenario_name", sa.String(128), nullable=True),
        sa.Column("scenario_offset", sa.String(16), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_event_event_id"),
        sa.CheckConstraint("severity IN ('info','warning','error','critical')", name="ck_event_severity"),
        sa.CheckConstraint(
            "resolution_state IN ('resolved','unresolved','rejected')",
            name="ck_event_resolution_state",
        ),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_event_provenance",
        ),
        sa.CheckConstraint(
            "provenance <> 'replayed' OR (scenario_name IS NOT NULL AND scenario_offset IS NOT NULL)",
            name="ck_event_replayed_scenario",
        ),
    )
    op.create_index(
        "ix_event_resource_time", "events", ["tenant_id", "resource_id", "occurred_at"]
    )
    op.create_index(
        "ix_event_resolution", "events", ["project_id", "resolution_state", "observed_at"]
    )
    op.create_index("ix_event_type_time", "events", ["project_id", "type", "occurred_at"])

    op.create_table(
        "evidence",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column(
            "resource_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("summary", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("query_parameters", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_expired", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_evidence_provenance",
        ),
        sa.CheckConstraint("range_start <= range_end", name="ck_evidence_range"),
    )
    op.create_index("ix_evidence_resource", "evidence", ["resource_id", "range_start", "range_end"])
    op.create_index("ix_evidence_kind", "evidence", ["project_id", "kind"])

    op.create_table(
        "task_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(256), nullable=False),
        sa.Column(
            "service_instance_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_class", sa.String(128), nullable=True),
        sa.Column("status_provenance", sa.String(16), nullable=False),
        sa.Column(
            "trigger_event_id", UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("model_duration_ms", sa.Integer(), nullable=True),
        sa.Column("tool_duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("project_id", "task_id", name="uq_task_run_project_task"),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','timed_out','interrupted')",
            name="ck_task_run_status",
        ),
        sa.CheckConstraint(
            "status_provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_task_run_provenance",
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_task_run_duration"),
        sa.CheckConstraint(
            "model_duration_ms IS NULL OR model_duration_ms >= 0", name="ck_task_run_model_duration"
        ),
        sa.CheckConstraint(
            "tool_duration_ms IS NULL OR tool_duration_ms >= 0", name="ck_task_run_tool_duration"
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_task_run_time_order"
        ),
    )
    op.create_index("ix_task_run_instance_time", "task_runs", ["service_instance_id", "started_at"])
    op.create_index("ix_task_run_status", "task_runs", ["project_id", "status", "started_at"])

    op.create_table(
        "resource_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resource_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("limits", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("image_digest", sa.String(256), nullable=True),
        sa.Column("config_hash", sa.String(128), nullable=False),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("resource_id", "captured_at", "config_hash", name="uq_snapshot_dedup"),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_snapshot_provenance",
        ),
    )
    op.create_index("ix_snapshot_resource", "resource_snapshots", ["resource_id", "captured_at"])

    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(256), nullable=False),
        sa.Column(
            "resource_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_alert_id", sa.String(256), nullable=True),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column(
            "parent_alert_id", UUID(as_uuid=True),
            sa.ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("annotations", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("state IN ('pending','firing','resolved')", name="ck_alert_state"),
        sa.CheckConstraint(
            "severity IN ('info','warning','error','critical')", name="ck_alert_severity"
        ),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_alert_provenance",
        ),
        sa.UniqueConstraint("source", "source_alert_id", name="uq_alert_source"),
    )
    op.create_index("ix_alert_fingerprint", "alerts", ["project_id", "fingerprint", "state"])
    op.create_index("ix_alert_resource", "alerts", ["resource_id", "starts_at"])

    op.create_table(
        "incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(256), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(4), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("acknowledged_by", sa.String(256), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notification_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("title", sa.Text(), nullable=True),
        sa.UniqueConstraint("project_id", "fingerprint", name="uq_incident_fingerprint"),
        sa.CheckConstraint("state IN ('open','acknowledged','resolved')", name="ck_incident_state"),
        sa.CheckConstraint("severity IN ('P1','P2','P3')", name="ck_incident_severity"),
        sa.CheckConstraint("alert_count >= 1", name="ck_incident_alert_count"),
        sa.CheckConstraint("reopen_count >= 0", name="ck_incident_reopen_count"),
        sa.CheckConstraint("first_seen <= last_seen", name="ck_incident_seen_order"),
    )
    op.create_index(
        "ix_incident_state", "incidents", ["project_id", "state", "severity", "last_seen"]
    )

    op.create_table(
        "silences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("matcher", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_silence_window"),
    )
    op.create_index("ix_silence_active", "silences", ["project_id", "ends_at"])

    op.create_table(
        "diagnoses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "incident_id", UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "symptom_resource_id", UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidates", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("supporting_evidence_ids", postgresql.ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("contradicting_evidence_ids", postgresql.ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("missing_evidence", postgresql.ARRAY(sa.String(256)), nullable=False, server_default="{}"),
        sa.Column("observed_impact", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("potential_impact", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rule_versions", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("time_confidence", sa.String(16), nullable=False),
        sa.Column("context_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("incident_id", "revision", name="uq_diagnosis_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_diagnosis_revision"),
        sa.CheckConstraint(
            "time_confidence IN ('trusted','degraded','unknown')",
            name="ck_diagnosis_time_confidence",
        ),
        sa.CheckConstraint("window_start <= window_end", name="ck_diagnosis_window"),
    )
    op.create_index("ix_diagnosis_incident", "diagnoses", ["incident_id", "revision"])
    op.create_index("ix_diagnosis_symptom", "diagnoses", ["symptom_resource_id", "created_at"])

    op.create_table(
        "source_statuses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_instance", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("missing_permissions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("capabilities", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("unresolved_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("queue_lag_seconds", sa.Float(), nullable=True),
        sa.Column("dropped_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("data_gaps", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("source_type", "source_instance", name="uq_source_status_identity"),
        sa.CheckConstraint(
            "state IN ('not_configured','healthy','degraded','stale','unavailable','permission_denied')",
            name="ck_source_status_state",
        ),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_source_status_latency"),
        sa.CheckConstraint("unresolved_count >= 0", name="ck_source_status_unresolved"),
        sa.CheckConstraint("dropped_count >= 0", name="ck_source_status_dropped"),
    )

    op.create_table(
        "ingestion_cursors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_instance", sa.String(128), nullable=False),
        sa.Column("stream", sa.String(128), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("provider_instance", "stream", name="uq_cursor_stream"),
    )
    op.create_index("ix_cursor_committed", "ingestion_cursors", ["provider_instance", "committed_at"])

    op.create_table(
        "scenario_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scenario_name", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("target_refs", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_by", sa.String(256), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_configuration", JSONB(), nullable=True),
        sa.Column("verification_result", JSONB(), nullable=True),
        sa.Column("restore_result", JSONB(), nullable=True),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('replay','lab')", name="ck_scenario_mode"),
        sa.CheckConstraint(
            "state IN ('pending','running','verifying','succeeded','failed','restoring','restored','restore_failed')",
            name="ck_scenario_state",
        ),
        sa.CheckConstraint(
            "provenance IN ('observed','simulated','replayed','inferred','manual')",
            name="ck_scenario_provenance",
        ),
        sa.CheckConstraint(
            "deadline_at IS NULL OR deadline_at > started_at", name="ck_scenario_deadline"
        ),
    )
    op.create_index(
        "ix_scenario_name_state", "scenario_runs", ["scenario_name", "state", "started_at"]
    )


def downgrade() -> None:
    op.drop_table("scenario_runs")
    op.drop_table("ingestion_cursors")
    op.drop_table("source_statuses")
    op.drop_index("ix_diagnosis_symptom", table_name="diagnoses")
    op.drop_index("ix_diagnosis_incident", table_name="diagnoses")
    op.drop_table("diagnoses")
    op.drop_index("ix_silence_active", table_name="silences")
    op.drop_table("silences")
    op.drop_table("incidents")
    op.drop_index("ix_alert_resource", table_name="alerts")
    op.drop_index("ix_alert_fingerprint", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_snapshot_resource", table_name="resource_snapshots")
    op.drop_table("resource_snapshots")
    op.drop_index("ix_task_run_status", table_name="task_runs")
    op.drop_index("ix_task_run_instance_time", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index("ix_evidence_kind", table_name="evidence")
    op.drop_index("ix_evidence_resource", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_event_type_time", table_name="events")
    op.drop_index("ix_event_resolution", table_name="events")
    op.drop_index("ix_event_resource_time", table_name="events")
    op.drop_table("events")
    op.execute("DROP TABLE IF EXISTS relationships CASCADE")
    op.drop_index("ix_alias_resource", table_name="resource_aliases")
    op.drop_index("uq_alias_active", table_name="resource_aliases")
    op.drop_table("resource_aliases")
    op.drop_index("ix_resource_project_kind", table_name="resources")
    op.drop_table("resources")
