from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.dependencies import get_current_context, require_role, AuthContext
from app.models.tenancy import Workspace
from app.schemas.auth import WorkspaceCreate, WorkspaceResponse

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])

@router.get("", response_model=List[WorkspaceResponse])
def list_workspaces(
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    """Lists workspaces strictly scoped to user's current organization."""
    workspaces = db.execute(
        select(Workspace).where(
            Workspace.organization_id == ctx.organization_id,
            Workspace.is_active == True
        )
    ).scalars().all()
    return workspaces

@router.post("", response_model=WorkspaceResponse)
def create_workspace(
    req: WorkspaceCreate,
    ctx: AuthContext = Depends(require_role(["SuperAdmin", "OrgAdmin"])),
    db: Session = Depends(get_db)
):
    """Creates a new workspace in user's current organization."""
    existing = db.execute(
        select(Workspace).where(
            Workspace.organization_id == ctx.organization_id,
            Workspace.slug == req.slug
        )
    ).scalars().first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workspace with slug '{req.slug}' already exists in this organization."
        )

    ws = Workspace(
        organization_id=ctx.organization_id,
        name=req.name,
        slug=req.slug
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws
