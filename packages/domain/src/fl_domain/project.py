"""Project, stage runs and change sets (docs/02 §2, docs/04 §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from fl_domain.base import DomainModel, LocalId
from fl_domain.common import Assumption, StageKey

__all__ = [
    "ChangeOp",
    "ChangeSet",
    "Project",
    "Question",
    "StageRun",
    "StageStatus",
]

StageStatus = Literal[
    "pending", "running", "awaiting_approval", "approved", "rejected", "stale", "failed"
]


class Project(DomainModel):
    """A project reached by capability link. Only the token hash is stored server-side."""

    id: UUID
    name: str = Field(min_length=1, max_length=200)
    created_at: datetime
    expires_at: datetime
    catalog_version: str
    pack_versions: dict[str, str] = Field(description='e.g. {"KA": "2026.11.1"}')
    template_version: int = 1


class Question(DomainModel):
    """A typed question raised by a stage for the user (shown on the approval screen)."""

    id: LocalId
    stage: StageKey
    text: str
    path: str | None = None
    options: list[str] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(
        default_factory=list, description="Conflicting values with their sources."
    )
    answer: str | None = None


class StageRun(DomainModel):
    """One immutable version of a stage's artifact (ADR-005)."""

    id: UUID
    project_id: UUID
    stage: StageKey
    version: int = Field(ge=1)
    status: StageStatus
    inputs_hash: str
    upstream: dict[StageKey, int] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    artifact: dict[str, Any] = Field(
        default_factory=dict, description="Validated against the stage's artifact model."
    )
    summary: str = ""
    assumptions: list[Assumption] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    created_at: datetime


class ChangeOp(DomainModel):
    """One edit inside a change set: JSON-pointer path, old and new value."""

    path: str
    old: Any = None
    new: Any = None


class ChangeSet(DomainModel):
    """Feedback and recommendations are applied as change sets (CLAUDE.md rule 5)."""

    id: LocalId
    stage: StageKey
    base_version: int = Field(ge=1)
    ops: list[ChangeOp] = Field(min_length=1)
    reason: str
    origin: Literal["user", "simulation", "agent"]
    status: Literal["proposed", "applied", "rejected"] = "proposed"
