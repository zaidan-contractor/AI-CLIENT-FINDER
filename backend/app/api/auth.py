from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import verify_password, create_access_token, canonicalize_email
from app.core.dependencies import get_current_context, AuthContext
from app.models.tenancy import User, Membership, Workspace, Organization
from app.schemas.auth import LoginRequest, TokenResponse, SwitchWorkspaceRequest, UserResponse, WorkspaceResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    clean_email = canonicalize_email(req.email)
    user = db.execute(
        select(User).where(User.email == clean_email)
    ).scalars().first()

    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password."
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated."
        )

    # Find primary membership
    membership = db.execute(
        select(Membership).where(Membership.user_id == user.id)
    ).scalars().first()

    if not membership and not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no assigned organization memberships."
        )

    org_id = membership.organization_id if membership else ""
    role = membership.role if membership else "SuperAdmin"

    # Default to assigned workspace or first active workspace in this organization
    ws_id = ""
    if membership and membership.workspace_id:
        assigned_ws = db.execute(
            select(Workspace).where(Workspace.id == membership.workspace_id, Workspace.is_active == True)
        ).scalars().first()
        if assigned_ws:
            ws_id = assigned_ws.id

    if not ws_id and org_id:
        ws = db.execute(
            select(Workspace).where(Workspace.organization_id == org_id, Workspace.is_active == True)
        ).scalars().first()
        if ws:
            ws_id = ws.id

    token = create_access_token(
        user_id=user.id,
        organization_id=org_id,
        active_workspace_id=ws_id,
        role=role
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        organization_id=org_id,
        active_workspace_id=ws_id,
        role=role
    )

@router.get("/me", response_model=UserResponse)
def get_me(ctx: AuthContext = Depends(get_current_context)):
    return UserResponse(
        id=ctx.user.id,
        email=ctx.user.email,
        full_name=ctx.user.full_name,
        is_active=ctx.user.is_active,
        is_superuser=ctx.user.is_superuser,
        role=ctx.role,
        organization_id=ctx.organization_id,
        active_workspace_id=ctx.active_workspace_id
    )

@router.post("/switch-workspace", response_model=TokenResponse)
def switch_workspace(
    req: SwitchWorkspaceRequest,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    """
    Context Switch Authorization Gate:
    Validates that target workspace belongs to user's organization before issuing updated token.
    Cross-organization workspace switching is strictly blocked with 403 Forbidden.
    """
    target_ws = db.execute(
        select(Workspace).where(
            Workspace.id == req.workspace_id,
            Workspace.is_active == True
        )
    ).scalars().first()

    if not target_ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target workspace does not exist."
        )

    # Cross-Tenant Boundary Enforcement
    if target_ws.organization_id != ctx.organization_id and not ctx.user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Target workspace belongs to another organization."
        )

    new_token = create_access_token(
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        active_workspace_id=target_ws.id,
        role=ctx.role
    )

    return TokenResponse(
        access_token=new_token,
        token_type="bearer",
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        active_workspace_id=target_ws.id,
        role=ctx.role
    )

@router.get("/workspaces", response_model=List[WorkspaceResponse])
def get_my_workspaces(
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    """
    Returns list of active workspaces belonging to the authenticated user's organization.
    Supports multi-workspace switching for operators.
    """
    workspaces = db.execute(
        select(Workspace).where(
            Workspace.organization_id == ctx.organization_id,
            Workspace.is_active == True
        )
    ).scalars().all()

    return [
        WorkspaceResponse(
            id=w.id,
            organization_id=w.organization_id,
            name=w.name,
            slug=w.slug,
            is_active=w.is_active
        )
        for w in workspaces
    ]
