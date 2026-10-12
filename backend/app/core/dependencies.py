from typing import List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.tenancy import User, Membership, Workspace, Organization

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

class AuthContext:
    def __init__(self, user: User, organization_id: str, active_workspace_id: str, role: str):
        self.user = user
        self.user_id = user.id
        self.organization_id = organization_id
        self.active_workspace_id = active_workspace_id
        self.role = role

def get_current_context(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> AuthContext:
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("user_id")
        organization_id: str = payload.get("organization_id")
        active_workspace_id: str = payload.get("active_workspace_id")
        role: str = payload.get("role")
        if not user_id or not organization_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload: missing identity or tenancy context."
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials or token expired."
        )

    # Validate user exists and is active
    user = db.execute(select(User).where(User.id == user_id, User.is_active == True)).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive."
        )

    # Superuser bypass for global administration
    if user.is_superuser:
        return AuthContext(user, organization_id, active_workspace_id or "", "SuperAdmin")

    # Resolve Role with Active Workspace Scoping
    resolved_role = None

    if active_workspace_id:
        ws = db.execute(
            select(Workspace).where(
                Workspace.id == active_workspace_id,
                Workspace.organization_id == organization_id,
                Workspace.is_active == True
            )
        ).scalars().first()
        if not ws:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active workspace does not belong to your organization or is inactive."
            )

        # 1. Check workspace-specific membership
        ws_membership = db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == organization_id,
                Membership.workspace_id == active_workspace_id
            )
        ).scalars().first()

        if ws_membership:
            resolved_role = ws_membership.role
        else:
            # 2. Check for Org-wide Admin role (workspace_id is NULL)
            org_membership = db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.organization_id == organization_id,
                    Membership.workspace_id == None
                )
            ).scalars().first()
            if org_membership and org_membership.role in ["OrgAdmin", "SuperAdmin"]:
                resolved_role = org_membership.role

        if not resolved_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User does not have authorized access to workspace '{active_workspace_id}'."
            )
    else:
        # Org-level context (no workspace specified)
        org_membership = db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == organization_id,
                Membership.workspace_id == None
            )
        ).scalars().first()

        if not org_membership:
            any_membership = db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.organization_id == organization_id
                )
            ).scalars().first()
            if any_membership:
                resolved_role = any_membership.role
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User does not have active membership in this organization."
                )
        else:
            resolved_role = org_membership.role

    return AuthContext(user, organization_id, active_workspace_id, resolved_role)


def require_role(allowed_roles: List[str]):
    """
    RBAC dependency factory.
    Roles: SuperAdmin, OrgAdmin, WorkspaceAdmin, Operator, Auditor
    """
    def role_checker(ctx: AuthContext = Depends(get_current_context)) -> AuthContext:
        if ctx.user.is_superuser or ctx.role == "SuperAdmin":
            return ctx
        if ctx.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of roles {allowed_roles}. Current role: {ctx.role}"
            )
        return ctx
    return role_checker
