from typing import List, Optional, Dict, Any, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from app.core.database import get_db
from app.core.dependencies import get_current_context, AuthContext
from app.models.business import MasterBusinessRecord, ContactRecord
from app.models.research import EvidenceRecord, ResearchRun
from app.services.research_service import ResearchService
from app.services.opportunity_intelligence_service import OpportunityIntelligenceService

router = APIRouter(prefix="/businesses", tags=["Businesses & Contacts"])


class ContactResponse(BaseModel):
    id: str
    business_id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role_title: Optional[str] = None
    verification_tier: str
    freshness_timestamp: Optional[Any] = None
    verification_metadata: Dict[str, Any] = {}


def _is_demo_fixture(biz: MasterBusinessRecord) -> Tuple[bool, str]:
    prov = (biz.source_provider or "").upper()
    domain = (biz.canonical_domain or "").lower()
    url = (biz.website_url or "").lower()
    prov_id = (biz.source_provider_id or "").lower()

    if (
        prov in ["MANUAL", "SEED", "SYNTHETIC", "DEMO", "TEST_FIXTURE"]
        or "example.com" in domain
        or "example.com" in url
        or "demo" in prov_id
    ):
        return True, "DEMO_FIXTURE"
    return False, "REAL_DISCOVERY"


class BusinessResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    website_url: Optional[str] = None
    canonical_domain: Optional[str] = None
    phone: Optional[str] = None
    normalized_phone: Optional[str] = None
    primary_email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    category: str
    source_provider: str
    identity_confidence: float = 1.0
    possible_duplicate_of_id: Optional[str] = None
    is_demo_fixture: bool = False
    data_origin: str = "REAL_DISCOVERY"
    contacts: List[ContactResponse] = []

    @classmethod
    def from_orm_record(cls, b: MasterBusinessRecord) -> "BusinessResponse":
        is_demo, origin = _is_demo_fixture(b)
        contacts = [
            ContactResponse(
                id=c.id,
                business_id=c.business_id,
                email=c.email,
                first_name=c.first_name,
                last_name=c.last_name,
                role_title=c.role_title,
                verification_tier=c.verification_tier,
                freshness_timestamp=c.freshness_timestamp.isoformat() if getattr(c, "freshness_timestamp", None) else None,
                verification_metadata=c.verification_metadata or {}
            )
            for c in (b.contacts or [])
        ]
        return cls(
            id=b.id,
            workspace_id=b.workspace_id,
            name=b.name,
            website_url=b.website_url,
            canonical_domain=b.canonical_domain,
            phone=b.phone,
            normalized_phone=b.normalized_phone,
            primary_email=b.primary_email,
            address=b.address,
            city=b.city,
            category=b.category,
            source_provider=b.source_provider,
            identity_confidence=b.identity_confidence,
            possible_duplicate_of_id=b.possible_duplicate_of_id,
            is_demo_fixture=is_demo,
            data_origin=origin,
            contacts=contacts
        )


class TriggerResearchRequest(BaseModel):
    trigger_type: str = "MANUAL"
    idempotency_key: Optional[str] = None
    mock_html: Optional[str] = None
    mock_status_code: int = 200
    mock_is_accessible: bool = True


class ResearchRunResponse(BaseModel):
    id: str
    business_id: str
    status: str
    trigger_type: str
    evidence_count: int
    error_message: Optional[str] = None
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None


class EvidenceDetailResponse(BaseModel):
    id: str
    business_id: str
    research_run_id: Optional[str] = None
    signal_type: str
    source_url: Optional[str] = None
    raw_observation: str
    feature_presence_state: str
    epistemic_tier: str
    content_hash: Optional[str] = None
    confidence: float
    classification_reason: str


@router.get("", response_model=List[BusinessResponse])
def list_businesses(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    query = select(MasterBusinessRecord).where(MasterBusinessRecord.workspace_id == ctx.active_workspace_id)
    if category:
        query = query.where(MasterBusinessRecord.category == category)
    if search:
        s_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                MasterBusinessRecord.name.ilike(s_term),
                MasterBusinessRecord.canonical_domain.ilike(s_term),
                MasterBusinessRecord.city.ilike(s_term)
            )
        )
    query = query.offset(skip).limit(limit)
    businesses = db.execute(query).scalars().all()
    return [BusinessResponse.from_orm_record(b) for b in businesses]


@router.get("/{business_id}", response_model=BusinessResponse)
def get_business_detail(
    business_id: str,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    biz = db.execute(
        select(MasterBusinessRecord).where(
            MasterBusinessRecord.id == business_id,
            MasterBusinessRecord.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not biz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found.")
    return BusinessResponse.from_orm_record(biz)


@router.post("/{business_id}/research", response_model=ResearchRunResponse)
def trigger_business_research(
    business_id: str,
    req: Optional[TriggerResearchRequest] = None,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    # Verify business ownership
    biz = db.execute(
        select(MasterBusinessRecord).where(
            MasterBusinessRecord.id == business_id,
            MasterBusinessRecord.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not biz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found.")

    r_req = req or TriggerResearchRequest()
    run, is_new = ResearchService.get_or_create_research_run(
        db=db,
        workspace_id=ctx.active_workspace_id,
        business_id=business_id,
        trigger_type=r_req.trigger_type,
        idempotency_key=r_req.idempotency_key
    )

    if is_new or run.status == "QUEUED":
        run = ResearchService.execute_research_run(
            db=db,
            research_run_id=run.id,
            mock_html=r_req.mock_html,
            mock_status_code=r_req.mock_status_code,
            mock_is_accessible=r_req.mock_is_accessible
        )

    return run


@router.get("/{business_id}/evidence", response_model=List[EvidenceDetailResponse])
def get_business_evidence_records(
    business_id: str,
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    # Verify business ownership
    biz = db.execute(
        select(MasterBusinessRecord).where(
            MasterBusinessRecord.id == business_id,
            MasterBusinessRecord.workspace_id == ctx.active_workspace_id
        )
    ).scalars().first()
    if not biz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found.")

    records = db.execute(
        select(EvidenceRecord).where(
            EvidenceRecord.business_id == business_id,
            EvidenceRecord.workspace_id == ctx.active_workspace_id
        ).order_by(EvidenceRecord.created_at.desc())
    ).scalars().all()
    return records


@router.get("/{business_id}/opportunities", response_model=Dict[str, Any])
def get_business_opportunities(
    business_id: str,
    currency: str = Query("USD", description="Currency for financial impact modeling"),
    ctx: AuthContext = Depends(get_current_context),
    db: Session = Depends(get_db)
):
    try:
        res = OpportunityIntelligenceService.analyze_business_opportunities(
            db=db,
            workspace_id=ctx.active_workspace_id,
            business_id=business_id,
            currency=currency
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
