from typing import Optional, List
from pydantic import BaseModel, EmailStr

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    organization_id: str
    active_workspace_id: str
    role: str

class SwitchWorkspaceRequest(BaseModel):
    workspace_id: str

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    is_superuser: bool
    role: Optional[str] = None
    organization_id: Optional[str] = None
    active_workspace_id: Optional[str] = None

class WorkspaceCreate(BaseModel):
    name: str
    slug: str

class WorkspaceResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    slug: str
    is_active: bool

class OrganizationCreate(BaseModel):
    name: str
    slug: str

class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
