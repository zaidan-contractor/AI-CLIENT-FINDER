import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, JSON, Float
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.tenancy import generate_uuid, get_utc_now

class PatternInsight(Base):
    __tablename__ = "pattern_insights"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    insight_data = Column(JSON, default=dict, nullable=False)
    confidence = Column(Float, default=0.85, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class RecommendationRecord(Base):
    __tablename__ = "recommendations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id = Column(String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True, index=True)
    recommendation_type = Column(String(100), nullable=False, index=True)
    headline = Column(String(255), nullable=False)
    suggested_action = Column(Text, nullable=False)
    expected_benefit = Column(Text, nullable=False)
    status = Column(String(50), default="PROPOSED", nullable=False, index=True)
    # Statuses: "PROPOSED", "ACCEPTED", "DISMISSED"
    applied_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
