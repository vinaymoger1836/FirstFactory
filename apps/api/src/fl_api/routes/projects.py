from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fl_api.db import Project, session
from fl_api.settings import get_settings
from fl_api.tokens import hash_token, looks_like_token, new_token

router = APIRouter(prefix="/projects", tags=["projects"])

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


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, db: DbSession) -> ProjectCreated:
    # Turnstile and per-IP limits arrive in P9 (docs/11 §2).
    token = new_token()
    expires = datetime.now(UTC) + timedelta(days=get_settings().project_retention_days)
    project = Project(name=body.name, token_hash=hash_token(token), expires_at=expires)
    db.add(project)
    await db.commit()
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
    return ProjectOut(
        id=project.id, name=project.name, created_at=project.created_at, expires_at=expires
    )
