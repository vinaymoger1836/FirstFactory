"""Projects: create (capability link), current project, template, submit (docs/03 §2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fl_agents.queue import defer_advance
from fl_agents.settings import DEFAULT_STAGE_BUDGETS
from fl_agents.store import Row
from fl_api.db import Project, session, store
from fl_api.settings import get_settings
from fl_api.tokens import hash_token, looks_like_token, new_token
from fl_domain.artifacts import ProductTemplate

router = APIRouter(prefix="/projects", tags=["projects"])
templates_router = APIRouter(prefix="/templates", tags=["projects"])

DbSession = Annotated[AsyncSession, Depends(session)]


class ProjectCreate(BaseModel):
    name: str = Field(default="Untitled project", min_length=1, max_length=200)


class ProjectCreated(BaseModel):
    id: UUID
    name: str
    token: str = Field(description="Shown once. Only its hash is stored.")
    link: str
    expires_at: datetime


class ProjectOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    expires_at: datetime
    status: str = "draft"
    catalog_version: str | None = None
    pack_versions: dict[str, str] = Field(default_factory=dict)
    template: dict[str, Any] | None = None
    thread_status: str | None = None


class IssueOut(BaseModel):
    path: str
    message: str


class ValidateOut(BaseModel):
    ok: bool
    issues: list[IssueOut]


class SubmitIn(BaseModel):
    stage_budgets: dict[str, int] = Field(
        default_factory=dict, description="Optional per-stage token budgets for this project."
    )


class SubmitOut(BaseModel):
    thread_id: str
    job_id: int


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, db: DbSession) -> ProjectCreated:
    # Turnstile and per-IP limits arrive in P9 (docs/11 §2).
    token = new_token()
    expires = datetime.now(UTC) + timedelta(days=get_settings().project_retention_days)
    project = Project(name=body.name, token_hash=hash_token(token), expires_at=expires)
    db.add(project)
    await db.commit()
    # Pin the library versions the project is built with (ADR-009).
    catalog, packs = await store().latest_library_versions()
    await store().update_project(project.id, catalog_version=catalog, pack_versions=packs)
    return ProjectCreated(
        id=project.id, name=project.name, token=token, link=f"/p/{token}", expires_at=expires
    )


async def project_from_token(db: DbSession, x_project_token: Annotated[str, Header()]) -> Project:
    """Resolve the capability token from the `X-Project-Token` header (never a cookie)."""
    if not looks_like_token(x_project_token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    project = await db.scalar(
        select(Project).where(Project.token_hash == hash_token(x_project_token))
    )
    if project is None or project.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def current_row(project: Annotated[Project, Depends(project_from_token)]) -> Row:
    row = await store().project(project.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return row


CurrentRow = Annotated[Row, Depends(current_row)]


def _issues(exc: ValidationError) -> list[IssueOut]:
    return [
        IssueOut(path="/".join(str(p) for p in e["loc"]), message=e["msg"]) for e in exc.errors()
    ]


@router.get("/current")
async def get_current_project(
    db: DbSession, project: Annotated[Project, Depends(project_from_token)]
) -> ProjectOut:
    # Touch activity and slide the 30-day expiry (docs/11 §2 retention).
    expires = datetime.now(UTC) + timedelta(days=get_settings().project_retention_days)
    await db.execute(
        update(Project)
        .where(Project.id == project.id)
        .values(last_activity_at=datetime.now(UTC), expires_at=expires)
    )
    await db.commit()
    row = await store().project(project.id)
    assert row is not None
    th = await store().active_thread(project.id)
    return ProjectOut(
        id=project.id,
        name=project.name,
        created_at=project.created_at,
        expires_at=expires,
        status=row["status"],
        catalog_version=row["catalog_version"],
        pack_versions=row["pack_versions"] or {},
        template=row["template"],
        thread_status=th["status"] if th else None,
    )


@templates_router.post("/validate")
async def validate_template(body: dict[str, Any]) -> ValidateOut:
    """Server-side check of a wizard template: exact paths for every problem."""
    try:
        ProductTemplate.model_validate(body)
    except ValidationError as exc:
        return ValidateOut(ok=False, issues=_issues(exc))
    return ValidateOut(ok=True, issues=[])


@router.put("/current/template")
async def put_template(body: dict[str, Any], row: CurrentRow) -> ValidateOut:
    try:
        t = ProductTemplate.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "message": "The template is not valid",
                "issues": [i.model_dump() for i in _issues(exc)],
            },
        ) from exc
    atts = {a["id"]: a for a in await store().attachments(row["id"])}
    unknown = [a.id for a in t.attachments if a.id not in atts]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": f"Unknown attachments: {', '.join(unknown)}", "issues": []},
        )
    for a in t.attachments:
        if a.kind != atts[a.id]["kind"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"message": f"{a.id} is a {atts[a.id]['kind']} file, not {a.kind}", "issues": []},
            )
    await store().update_project(
        row["id"], template=t.model_dump(mode="json"), name=t.product.name[:200]
    )
    return ValidateOut(ok=True, issues=[])


@router.post("/current/submit")
async def submit(body: SubmitIn, row: CurrentRow) -> SubmitOut:
    """Start the pipeline: S1 intake runs in the agents service (docs/03 §2)."""
    if row["template"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Save the template before submitting")
    bad = [
        k
        for k in body.stage_budgets
        if k not in DEFAULT_STAGE_BUDGETS
        and not (k.endswith("_tools") and k[: -len("_tools")] in DEFAULT_STAGE_BUDGETS)
    ]
    if bad or any(v < 0 for v in body.stage_budgets.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Bad stage budgets: {bad}")
    await store().update_project(row["id"], status="submitted", stage_budgets=body.stage_budgets)
    thread_id = await store().new_thread(row["id"], "intake")
    job = await defer_advance(str(row["id"]), thread_id, None)
    await store().emit(row["id"], "pipeline", {"status": "started", "thread_id": thread_id})
    return SubmitOut(thread_id=thread_id, job_id=job)
