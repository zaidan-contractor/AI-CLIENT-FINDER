from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_context, AuthContext
from app.services.discovery_service import DiscoveryService

router = APIRouter(prefix="/discovery", tags=["Discovery"])


class DiscoverySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language search query")
    location: str = Field(..., min_length=1, description="Geographic scope, e.g. city or region")
    category: str = Field(..., min_length=1, description="Business vertical or category")
    limit: int = Field(20, ge=1, le=50, description="Maximum results per provider")


class DiscoveredBusinessItem(BaseModel):
    id: str
    name: str
    website_url: Optional[str] = None
    canonical_domain: Optional[str] = None
    phone: Optional[str] = None
    primary_email: Optional[str] = None
    city: Optional[str] = None
    category: str
    source_provider: str
    identity_confidence: float
    possible_duplicate_of_id: Optional[str] = None
    is_demo_fixture: bool = False
    data_origin: str = "REAL_DISCOVERY"


class DiscoverySearchResponse(BaseModel):
    query: str
    location: str
    category: str
    total_discovered_candidates: int
    newly_registered: int
    exact_duplicates_skipped: int
    possible_duplicates_flagged: int
    errors: List[str] = []
    businesses: List[DiscoveredBusinessItem] = []


@router.post("/search", response_model=DiscoverySearchResponse)
def execute_discovery_search(
    req: DiscoverySearchRequest,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    service = DiscoveryService()
    try:
        result = service.search_and_register(
            db=db,
            workspace_id=ctx.active_workspace_id,
            query=req.query,
            location=req.location,
            category=req.category,
            limit=req.limit
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discovery search failed: {str(e)}"
        )
