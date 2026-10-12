import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Float, ForeignKey, Text, JSON, Numeric
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now

class MasterBusinessRecord(Base):
    __tablename__ = "master_business_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    normalized_name = Column(String(255), nullable=False, index=True)
    website_url = Column(String(512), nullable=True)
    canonical_domain = Column(String(255), nullable=True, index=True)
    phone = Column(String(50), nullable=True)
    normalized_phone = Column(String(50), nullable=True, index=True)
    primary_email = Column(String(255), nullable=True, index=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True, index=True)
    state = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True, index=True)
    postal_code = Column(String(20), nullable=True)
    category = Column(String(100), nullable=False, index=True)
    source_provider = Column(String(50), nullable=False)  # "OPENSTREETMAP", "LOCAL_DIRECTORY", "API_PLACES"
    source_provider_id = Column(String(255), nullable=True)
    possible_duplicate_of_id = Column(String(36), nullable=True, index=True)
    identity_confidence = Column(Float, default=1.0, nullable=False)
    entity_type = Column(String(50), default="UNKNOWN", nullable=False, index=True)
    entity_type_confidence = Column(Float, default=0.5, nullable=False)
    sales_eligibility_state = Column(String(50), default="UNKNOWN_ENTITY_TYPE", nullable=False, index=True)
    raw_metadata = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    contacts = relationship("ContactRecord", back_populates="business", cascade="all, delete-orphan")


class ContactRecord(Base):
    __tablename__ = "contact_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id = Column(String(36), ForeignKey("master_business_records.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    canonical_email = Column(String(255), nullable=False, index=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    role_title = Column(String(100), nullable=True)
    verification_tier = Column(String(50), default="UNVERIFIED", nullable=False)
    # Tiers: "CONFIRMED_VERIFIED", "PROVIDER_VERIFIED", "UNVERIFIED", "FAILED_VERIFICATION"
    freshness_timestamp = Column(DateTime(timezone=True), nullable=True)
    verification_metadata = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    business = relationship("MasterBusinessRecord", back_populates="contacts")


class MarketTarget(Base):
    __tablename__ = "market_targets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    query_text = Column(String(255), nullable=False)
    location = Column(String(255), nullable=False)
    target_category = Column(String(100), nullable=False)
    status = Column(String(50), default="PENDING", nullable=False)  # "PENDING", "RUNNING", "COMPLETED", "FAILED"
    discovered_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
