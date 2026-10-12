import re
import logging
from typing import Dict, Any, List, Tuple, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Canonical Entity Types
COMMERCIAL_VENUE = "COMMERCIAL_VENUE"
SPORTS_CLUB = "SPORTS_CLUB"
ACADEMY = "ACADEMY"
SCHOOL_COLLEGE_FACILITY = "SCHOOL_COLLEGE_FACILITY"
GOVERNMENT_PUBLIC_FACILITY = "GOVERNMENT_PUBLIC_FACILITY"
MUNICIPAL_SPORTS_COMPLEX = "MUNICIPAL_SPORTS_COMPLEX"
NONPROFIT_OR_COMMUNITY_FACILITY = "NONPROFIT_OR_COMMUNITY_FACILITY"
UNKNOWN = "UNKNOWN"

# Canonical Sales Eligibility States
SALES_ELIGIBLE = "SALES_ELIGIBLE"
RESEARCH_ONLY = "RESEARCH_ONLY"
INSTITUTIONAL = "INSTITUTIONAL"
UNKNOWN_ENTITY_TYPE = "UNKNOWN_ENTITY_TYPE"
NOT_ENOUGH_EVIDENCE = "NOT_ENOUGH_EVIDENCE"


class EntityClassificationResult(BaseModel):
    entity_type: str
    confidence: float
    sales_eligibility_state: str
    reasons: List[str]
    supporting_signals: Dict[str, Any]


class EntityClassifier:
    """
    Evidence-grounded business and entity classifier.
    Distinguishes private commercial businesses from public, municipal,
    school, and government facilities without assuming a venue name alone dictates status.
    """

    MUNICIPAL_PATTERNS = [
        r"\bmunicipal\b", r"\bmunicipality\b", r"\bcouncil\b", r"\bnagarpalika\b"
    ]
    GOVERNMENT_PATTERNS = [
        r"\bgovernment\b", r"\bgovt\b", r"\bpanchayat\b", r"\bdsya\b",
        r"\bsports authority\b", r"\bsag\b", r"\bdirectorate\b", r"\bpublic ground\b"
    ]
    SCHOOL_COLLEGE_PATTERNS = [
        r"\bschool\b", r"\bcollege\b", r"\buniversity\b", r"\bcampus\b",
        r"\boratory\b", r"\bvidyalaya\b", r"\bvidyapeeth\b", r"\binstitute\b",
        r"\bseminary\b", r"\bdon bosco\b", r"\bst\.?\s+[a-z]+'?s?\s+ground\b"
    ]
    ACADEMY_PATTERNS = [
        r"\bacademy\b", r"\btraining centre\b", r"\bcoaching centre\b"
    ]
    SPORTS_CLUB_PATTERNS = [
        r"\bsports? club\b", r"\bfootball club\b", r"\bgymkhana\b", r"\byouth club\b", r"\bclub\b"
    ]
    COMMUNITY_PATTERNS = [
        r"\bchurch ground\b", r"\bvillage ground\b", r"\bparish\b", r"\bcommunity ground\b"
    ]
    COMMERCIAL_PATTERNS = [
        r"\bturf\b", r"\barena\b", r"\bfutsal\b", r"\bbox cricket\b",
        r"\bmultisports\b", r"\bsports hub\b", r"\bshooting hub\b", r"\bhub\b",
        r"\bkick-?off\b", r"\bpadel\b", r"\bgo-?kart\b", r"\bkarting\b",
        r"\bclinic\b", r"\bhealth\b", r"\bdental\b", r"\bmedical\b",
        r"\bsalon\b", r"\bbarber\b", r"\bspa\b", r"\bgym\b", r"\bfitness\b"
    ]

    @classmethod
    def classify_entity(
        cls,
        name: str,
        category: Optional[str] = None,
        raw_metadata: Optional[Dict[str, Any]] = None,
        source_provider: Optional[str] = None,
        has_commercial_pricing: bool = False,
        has_commercial_booking: bool = False
    ) -> EntityClassificationResult:
        meta = raw_metadata or {}
        n_lower = (name or "").lower()
        reasons = []
        signals = {
            "name": name,
            "category": category,
            "source_provider": source_provider
        }

        # 1. Check Municipal complex indicators
        if any(re.search(p, n_lower) for p in cls.MUNICIPAL_PATTERNS) or meta.get("ownership") == "municipal":
            reasons.append(f"Name or metadata indicates municipal ownership: '{name}'")
            return EntityClassificationResult(
                entity_type=MUNICIPAL_SPORTS_COMPLEX,
                confidence=0.90,
                sales_eligibility_state=INSTITUTIONAL,
                reasons=reasons,
                supporting_signals=signals
            )

        # 2. Check Government / DSYA / SAG indicators
        if any(re.search(p, n_lower) for p in cls.GOVERNMENT_PATTERNS) or meta.get("operator:type") == "government":
            reasons.append(f"Name or metadata indicates government or panchayat facility: '{name}'")
            return EntityClassificationResult(
                entity_type=GOVERNMENT_PUBLIC_FACILITY,
                confidence=0.90,
                sales_eligibility_state=INSTITUTIONAL,
                reasons=reasons,
                supporting_signals=signals
            )

        # 3. Check School / College / Educational facility
        is_school = any(re.search(p, n_lower) for p in cls.SCHOOL_COLLEGE_PATTERNS)
        if is_school:
            if has_commercial_pricing or has_commercial_booking or "futsal arena" in n_lower or "turf" in n_lower:
                reasons.append("Affiliated with educational trust/institution but operates public commercial sports venue/slots")
                return EntityClassificationResult(
                    entity_type=COMMERCIAL_VENUE,
                    confidence=0.85,
                    sales_eligibility_state=SALES_ELIGIBLE,
                    reasons=reasons,
                    supporting_signals=signals
                )
            reasons.append(f"Name indicates educational or school/college campus facility: '{name}'")
            return EntityClassificationResult(
                entity_type=SCHOOL_COLLEGE_FACILITY,
                confidence=0.85,
                sales_eligibility_state=RESEARCH_ONLY,
                reasons=reasons,
                supporting_signals=signals
            )

        # 4. Check Sports Academy
        if any(re.search(p, n_lower) for p in cls.ACADEMY_PATTERNS):
            reasons.append(f"Entity identifies as sports training academy: '{name}'")
            return EntityClassificationResult(
                entity_type=ACADEMY,
                confidence=0.85,
                sales_eligibility_state=SALES_ELIGIBLE,
                reasons=reasons,
                supporting_signals=signals
            )

        # 5. Check Sports Club / Gymkhana
        if any(re.search(p, n_lower) for p in cls.SPORTS_CLUB_PATTERNS):
            reasons.append(f"Entity identifies as sports club or association: '{name}'")
            return EntityClassificationResult(
                entity_type=SPORTS_CLUB,
                confidence=0.85,
                sales_eligibility_state=SALES_ELIGIBLE,
                reasons=reasons,
                supporting_signals=signals
            )

        # 5b. Check Community or Parish Ground
        if any(re.search(p, n_lower) for p in cls.COMMUNITY_PATTERNS):
            reasons.append(f"Entity identifies as church, village, or community ground: '{name}'")
            return EntityClassificationResult(
                entity_type=NONPROFIT_OR_COMMUNITY_FACILITY,
                confidence=0.85,
                sales_eligibility_state=RESEARCH_ONLY,
                reasons=reasons,
                supporting_signals=signals
            )

        # 6. Check Commercial Turf / Arena patterns or commercial listing
        cat_lower = (category or "").lower()
        is_commercial_cat = any(c in cat_lower for c in ["dental", "medical", "health", "clinic", "salon", "barber", "spa", "gym", "fitness", "commercial"])
        is_commercial_name = any(re.search(p, n_lower) for p in cls.COMMERCIAL_PATTERNS)
        if is_commercial_name or is_commercial_cat or has_commercial_pricing or has_commercial_booking:
            reasons.append(f"Entity exhibits commercial business/venue markers: '{name}'")
            return EntityClassificationResult(
                entity_type=COMMERCIAL_VENUE,
                confidence=0.90,
                sales_eligibility_state=SALES_ELIGIBLE,
                reasons=reasons,
                supporting_signals=signals
            )

        # 7. Generic "Sports Complex" without commercial keywords or private owner
        if "sports complex" in n_lower or "sports ground" in n_lower:
            # Regional Indian public sports complexes are overwhelmingly state or municipality owned
            if source_provider == "OPENSTREETMAP" and not has_commercial_booking:
                reasons.append("Generic sports complex/ground discovered from public map registry with no commercial owner verified")
                return EntityClassificationResult(
                    entity_type=MUNICIPAL_SPORTS_COMPLEX,
                    confidence=0.75,
                    sales_eligibility_state=RESEARCH_ONLY,
                    reasons=reasons,
                    supporting_signals=signals
                )

        # 8. Fallback to Unknown
        reasons.append("Insufficient explicit signals to verify commercial vs institutional entity type")
        return EntityClassificationResult(
            entity_type=UNKNOWN,
            confidence=0.40,
            sales_eligibility_state=UNKNOWN_ENTITY_TYPE,
            reasons=reasons,
            supporting_signals=signals
        )
