"""Shared Pydantic models for incidents, actions, and audit records."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AutonomyLevel(str, Enum):
    AUTO = "auto"
    APPROVE = "approve"
    NEVER = "never"


class AgentState(str, Enum):
    TRIAGE = "triage"
    EVIDENCE = "evidence"
    ROOT_CAUSE = "root_cause"
    AUTONOMY_GATE = "autonomy_gate"
    ACTING = "acting"
    ESCALATED = "escalated"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    FAILED = "failed"


class IncidentSignal(BaseModel):
    service: str
    signal_type: str        # health_check_failed | high_error_rate | oom | hang | ...
    details: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=_now)


class Incident(BaseModel):
    id: str = Field(default_factory=_uuid)
    signal: IncidentSignal
    severity: Severity | None = None
    blast_radius: str | None = None         # low | medium | high
    evidence: dict[str, Any] = Field(default_factory=dict)
    root_cause: str | None = None
    proposed_action: str | None = None
    proposed_action_params: dict[str, Any] = Field(default_factory=dict)
    autonomy_level: AutonomyLevel | None = None
    state: AgentState = AgentState.TRIAGE
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=_uuid)
    incident_id: str
    proposed_action: str
    proposed_action_params: dict[str, Any]
    root_cause: str
    runbook: str
    created_at: datetime = Field(default_factory=_now)
    approved: bool | None = None           # None = pending
    reviewed_at: datetime | None = None
    reviewer_note: str | None = None


class AuditRecord(BaseModel):
    id: str = Field(default_factory=_uuid)
    incident_id: str
    event: str
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)
    auto: bool = True
