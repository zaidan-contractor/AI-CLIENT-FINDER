import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, ForeignKey, Text, JSON, Enum
)
from sqlalchemy.orm import relationship
from app.core.database import Base

def generate_uuid() -> str:
    return str(uuid.uuid4())

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

# --- Global Reference Tables ---

class ISOCurrencyRegistry(Base):
    __tablename__ = "iso_currency_registry"

    currency_code = Column(String(3), primary_key=True, index=True)  # e.g. "USD", "INR"
    currency_name = Column(String(100), nullable=False)
    minor_units = Column(Integer, nullable=False, default=2)  # e.g. 2 for cents, 0 for JPY
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


# --- Organization-Scoped Tables ---

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    workspaces = relationship("Workspace", back_populates="organization", cascade="all, delete-orphan")
    org_suppressions = relationship("OrgSuppressionRecord", back_populates="organization", cascade="all, delete-orphan")
    compliance_ledger = relationship("ComplianceSuppressionLedger", back_populates="organization", cascade="all, delete-orphan")


class OrgSuppressionRecord(Base):
    __tablename__ = "org_suppression_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    email_canonical = Column(String(255), nullable=False, index=True)
    reason = Column(String(255), nullable=False)
    source_channel = Column(String(50), nullable=False)  # "UNSUBSCRIBE_PAGE", "INBOUND_REPLY", "OPERATOR"
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    organization = relationship("Organization", back_populates="org_suppressions")


class ComplianceSuppressionLedger(Base):
    __tablename__ = "compliance_suppression_ledger"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    suppression_hash = Column(String(64), nullable=False, index=True)  # HMAC-SHA256 hex digest
    hash_key_version = Column(Integer, nullable=False)
    algorithm = Column(String(32), default="HMAC-SHA256", nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    organization = relationship("Organization", back_populates="compliance_ledger")


# --- Workspace-Scoped Tables ---

class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    organization = relationship("Organization", back_populates="workspaces")
    suppression_records = relationship("WorkspaceSuppressionRecord", back_populates="workspace", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class Membership(Base):
    __tablename__ = "memberships"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    role = Column(String(50), nullable=False)  # "SuperAdmin", "OrgAdmin", "WorkspaceAdmin", "Operator", "Auditor"
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class WorkspaceSuppressionRecord(Base):
    __tablename__ = "workspace_suppression_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    email_canonical = Column(String(255), nullable=False, index=True)
    reason = Column(String(255), nullable=False)
    source_channel = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    workspace = relationship("Workspace", back_populates="suppression_records")


class UnsubscribeToken(Base):
    __tablename__ = "unsubscribe_tokens"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    token = Column(String(128), unique=True, nullable=False, index=True)
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(String(36), nullable=True, index=True)
    email_canonical_hash = Column(String(64), nullable=False, index=True)
    raw_email_for_delivery = Column(String(255), nullable=False)  # needed strictly to execute write upon confirmation
    scope = Column(String(50), default="UNSUBSCRIBE_ONLY", nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class SystemAuditEvent(Base):
    __tablename__ = "system_audit_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    organization_id = Column(String(36), nullable=True, index=True)
    workspace_id = Column(String(36), nullable=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    actor_id = Column(String(255), nullable=False)
    actor_type = Column(String(50), nullable=False)  # "USER", "SYSTEM", "AI", "EXTERNAL_MUA"
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
