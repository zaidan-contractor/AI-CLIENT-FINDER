from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.research import EvidenceRecord
from app.models.opportunity import ProblemHypothesis, ImpactAssessment, SolutionMapping
from app.services.impact_calculator import ImpactCalculator

class HypothesisEngine:
    VALIDATION_THRESHOLD = 0.70

    CATEGORY_TEMPLATES = {
        "BOOKING_FRICTION_AFTER_HOURS_LEAKAGE": {
            "title": "After-Hours Booking & Scheduling Friction",
            "desc": "Lack of 24/7 automated online scheduling leads to prospective client leakage during off-hours.",
            "solution_catalogue": "24/7 Automated Web & WhatsApp Appointment Engine",
            "benchmark": {
                "n_lost": Decimal("15"),
                "v_ticket": Decimal("120.00"),
                "c_rate": Decimal("0.30"),
                "h_waste": Decimal("8"),
                "r_labor": Decimal("25.00")
            }
        },
        "MISSED_CALLS_UNANSWERED_INQUIRIES": {
            "title": "Unanswered Inbound Inquiries & Phone Friction",
            "desc": "Repeated customer reports indicate missed calls and delayed response times for new patient inquiries.",
            "solution_catalogue": "AI Conversational Receptionist & Instant Lead Call-Back",
            "benchmark": {
                "n_lost": Decimal("20"),
                "v_ticket": Decimal("100.00"),
                "c_rate": Decimal("0.25"),
                "h_waste": Decimal("12"),
                "r_labor": Decimal("22.00")
            }
        },
        "WEBSITE_PERFORMANCE_CONVERSION_DEFICIT": {
            "title": "Website Performance & Mobile Viewport Deficit",
            "desc": "Missing mobile responsive viewport or excessive latency degrades search rank and mobile visitor conversion.",
            "solution_catalogue": "Ultra-Fast Mobile-First Headless Booking Landing Page",
            "benchmark": {
                "n_lost": Decimal("10"),
                "v_ticket": Decimal("90.00"),
                "c_rate": Decimal("0.20"),
                "h_waste": Decimal("4"),
                "r_labor": Decimal("25.00")
            }
        },
        "DIGITAL_SOLUTION_ADOPTION_GAP": {
            "title": "Digital Operational Infrastructure Gap",
            "desc": "Business operates without a registered modern digital presence, limiting client discovery and acquisition.",
            "solution_catalogue": "Complete Turnkey Modern Digital Practice Suite",
            "benchmark": {
                "n_lost": Decimal("25"),
                "v_ticket": Decimal("150.00"),
                "c_rate": Decimal("0.25"),
                "h_waste": Decimal("15"),
                "r_labor": Decimal("30.00")
            }
        }
    }

    def generate_and_evaluate_hypotheses(
        self,
        db: Session,
        workspace_id: str,
        business_id: str,
        currency: str = "USD"
    ) -> List[ProblemHypothesis]:
        """
        Synthesizes validated problem hypotheses from collected evidence records.
        Applies strict refutation and validation logic (threshold >= 0.70).
        """
        evidence_list = db.execute(
            select(EvidenceRecord).where(
                EvidenceRecord.business_id == business_id,
                EvidenceRecord.workspace_id == workspace_id
            )
        ).scalars().all()

        if not evidence_list:
            return []

        # Clear existing hypotheses for clean re-evaluation
        db.query(ProblemHypothesis).filter(
            ProblemHypothesis.business_id == business_id,
            ProblemHypothesis.workspace_id == workspace_id
        ).delete()
        db.commit()

        generated: List[ProblemHypothesis] = []

        from app.models.business import MasterBusinessRecord
        biz = db.execute(
            select(MasterBusinessRecord).where(
                MasterBusinessRecord.id == business_id,
                MasterBusinessRecord.workspace_id == workspace_id
            )
        ).scalars().first()

        is_sales_eligible = True
        if biz:
            if biz.sales_eligibility_state in ["RESEARCH_ONLY", "INSTITUTIONAL"] or biz.entity_type in [
                "GOVERNMENT_PUBLIC_FACILITY", "MUNICIPAL_SPORTS_COMPLEX", "SCHOOL_COLLEGE_FACILITY"
            ]:
                is_sales_eligible = False

        # 1. Booking Friction Evaluation
        booking_ev = [
            e for e in evidence_list 
            if ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower() or e.signal_type == "DOM_SELECTOR")
        ]
        is_booking_present = any(
            e.feature_presence_state == "PRESENT" and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
            for e in evidence_list
        )
        has_explicit_absence = any(
            e.feature_presence_state == "ABSENCE_CONFIRMED" and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
            for e in evidence_list
        )
        has_corroborated_absence = any(
            getattr(e, "corroboration_count", 1) >= 2 and e.feature_presence_state == "NOT_OBSERVED"
            and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
            for e in evidence_list
        )
        is_single_surface_unobserved = any(
            e.feature_presence_state == "NOT_OBSERVED" and ("booking" in e.raw_observation.lower() or "appointment" in e.raw_observation.lower())
            for e in evidence_list
        )

        tmpl = self.CATEGORY_TEMPLATES["BOOKING_FRICTION_AFTER_HOURS_LEAKAGE"]
        if is_booking_present:
            # Explicitly refuted
            hyp = ProblemHypothesis(
                workspace_id=workspace_id,
                business_id=business_id,
                category="BOOKING_FRICTION_AFTER_HOURS_LEAKAGE",
                title=tmpl["title"],
                description=tmpl["desc"],
                status="REFUTED",
                opportunity_state="NO_CONFIDENT_OPPORTUNITY",
                evidence_strength="STRONG",
                unknowns=[],
                confidence=0.1,
                refutation_reason="Direct counter-evidence observed: Active online booking engine confirmed present in DOM."
            )
            db.add(hyp)
            generated.append(hyp)
        elif not is_sales_eligible:
            # Entity is institutional or municipal -> RESEARCH_ONLY
            hyp = ProblemHypothesis(
                workspace_id=workspace_id,
                business_id=business_id,
                category="BOOKING_FRICTION_AFTER_HOURS_LEAKAGE",
                title=tmpl["title"],
                description=tmpl["desc"],
                status="PROPOSED",
                opportunity_state="RESEARCH_ONLY",
                evidence_strength="WEAK",
                unknowns=["Entity is non-commercial / institutional facility; commercial sales proposal ineligible"],
                confidence=0.40,
                supporting_evidence_ids=[e.id for e in booking_ev]
            )
            db.add(hyp)
            db.flush()

            impact_calc = ImpactCalculator.create_unknown_impact(
                currency=currency,
                reason="Non-commercial / institutional entities are restricted to RESEARCH_ONLY. Commercial financial models are withheld."
            )
            impact_rec = ImpactAssessment(
                workspace_id=workspace_id,
                hypothesis_id=hyp.id,
                impact_currency=impact_calc["currency"],
                financial_impact_status=impact_calc["financial_impact_status"],
                estimated_impact_low=impact_calc["estimated_impact_low"],
                estimated_impact_high=impact_calc["estimated_impact_high"],
                estimated_impact_midpoint=impact_calc["estimated_impact_midpoint"],
                observed_financial_data=impact_calc["observed_financial_data"],
                business_specific_measurements=impact_calc["business_specific_measurements"],
                industry_benchmark_assumptions=impact_calc["industry_benchmark_assumptions"],
                model_assumptions=impact_calc["model_assumptions"]
            )
            db.add(impact_rec)

            sol = SolutionMapping(
                workspace_id=workspace_id,
                hypothesis_id=hyp.id,
                solution_type="NEEDS_DISCOVERY",
                title="Public Institutional Digital Assessment",
                capability_summary="Institutional review only. Requires administrative approval and government procurement discovery.",
                feasibility_score=0.50,
                operator_approved=False
            )
            db.add(sol)
            generated.append(hyp)
        elif has_explicit_absence or has_corroborated_absence:
            # Validated with strong/moderate evidence
            conf = 0.85 if has_explicit_absence else 0.75
            strength = "STRONG" if has_explicit_absence else "MODERATE"
            hyp = ProblemHypothesis(
                workspace_id=workspace_id,
                business_id=business_id,
                category="BOOKING_FRICTION_AFTER_HOURS_LEAKAGE",
                title=tmpl["title"],
                description=tmpl["desc"],
                status="VALIDATED",
                opportunity_state="EVIDENCE_BACKED",
                evidence_strength=strength,
                unknowns=[],
                confidence=conf,
                supporting_evidence_ids=[e.id for e in booking_ev]
            )
            db.add(hyp)
            db.flush()

            # Attach Impact Assessment
            meta = biz.raw_metadata if biz else {}
            has_empirical_volume = (
                "booking_volume" in meta or "monthly_inquiries" in meta or "hourly_rate" in meta or "ticket_price" in meta
            )
            allow_benchmark = (
                meta.get("allow_industry_benchmark") is True
                or meta.get("benchmark_mode") is True
                or meta.get("use_benchmark") is True
            )

            if has_empirical_volume or allow_benchmark:
                bm = tmpl["benchmark"]
                n_lost = Decimal(str(meta.get("booking_volume", meta.get("monthly_inquiries", bm["n_lost"]))))
                v_ticket = Decimal(str(meta.get("ticket_price", meta.get("hourly_rate", bm["v_ticket"]))))
                c_rate = Decimal(str(meta.get("conversion_rate", bm["c_rate"])))
                impact_calc = ImpactCalculator.calculate_monetary_impact(
                    currency=currency,
                    n_lost_inquiries_monthly=n_lost,
                    v_ticket_avg=v_ticket,
                    c_conversion_rate=c_rate,
                    h_wasted_hours_monthly=bm["h_waste"],
                    r_labor_hourly_rate=bm["r_labor"]
                )
                if has_empirical_volume:
                    impact_calc["observed_financial_data"] = {
                        "source": "BUSINESS_FACT",
                        "observed_metrics": {k: v for k, v in meta.items() if k in ["booking_volume", "ticket_price", "hourly_rate", "monthly_inquiries"]}
                    }
                else:
                    impact_calc["observed_financial_data"] = {
                        "source": "EXTERNAL_MARKET_BENCHMARK",
                        "note": "Documented regional turf benchmarks utilized under explicit benchmark policy."
                    }
            else:
                impact_calc = ImpactCalculator.create_unknown_impact(
                    currency=currency,
                    missing_inputs=["booking_volume", "average_hourly_rate", "conversion_rate"],
                    reason="Required empirical business volume metrics (monthly slot demand, booking rates) are unobserved."
                )

            impact_rec = ImpactAssessment(
                workspace_id=workspace_id,
                hypothesis_id=hyp.id,
                impact_currency=impact_calc["currency"],
                financial_impact_status=impact_calc["financial_impact_status"],
                estimated_impact_low=impact_calc["estimated_impact_low"],
                estimated_impact_high=impact_calc["estimated_impact_high"],
                estimated_impact_midpoint=impact_calc["estimated_impact_midpoint"],
                observed_financial_data=impact_calc["observed_financial_data"],
                business_specific_measurements=impact_calc["business_specific_measurements"],
                industry_benchmark_assumptions=impact_calc["industry_benchmark_assumptions"],
                model_assumptions=impact_calc["model_assumptions"]
            )
            db.add(impact_rec)

            # Attach Solution Mapping
            sol = SolutionMapping(
                workspace_id=workspace_id,
                hypothesis_id=hyp.id,
                solution_type="CATALOGUE_SOLUTION",
                title=tmpl["solution_catalogue"],
                capability_summary="Deploy automated 24/7 calendar booking widget and after-hours reservation engine.",
                feasibility_score=0.95,
                operator_approved=True
            )
            db.add(sol)
            generated.append(hyp)
        elif is_single_surface_unobserved:
            # Single surface unobserved -> INSUFFICIENT_EVIDENCE (Status: PROPOSED)
            hyp = ProblemHypothesis(
                workspace_id=workspace_id,
                business_id=business_id,
                category="BOOKING_FRICTION_AFTER_HOURS_LEAKAGE",
                title=tmpl["title"],
                description=tmpl["desc"],
                status="PROPOSED",
                opportunity_state="INSUFFICIENT_EVIDENCE",
                evidence_strength="WEAK",
                unknowns=["Booking capabilities on alternative public surfaces remain unverified."],
                confidence=0.50,
                supporting_evidence_ids=[e.id for e in booking_ev]
            )
            db.add(hyp)
            db.flush()

            impact_calc = ImpactCalculator.create_unknown_impact(
                currency=currency,
                reason="Opportunity is in INSUFFICIENT_EVIDENCE state. Financial projection withheld."
            )
            impact_rec = ImpactAssessment(
                workspace_id=workspace_id,
                hypothesis_id=hyp.id,
                impact_currency=impact_calc["currency"],
                financial_impact_status=impact_calc["financial_impact_status"],
                estimated_impact_low=impact_calc["estimated_impact_low"],
                estimated_impact_high=impact_calc["estimated_impact_high"],
                estimated_impact_midpoint=impact_calc["estimated_impact_midpoint"],
                observed_financial_data=impact_calc["observed_financial_data"],
                business_specific_measurements=impact_calc["business_specific_measurements"],
                industry_benchmark_assumptions=impact_calc["industry_benchmark_assumptions"],
                model_assumptions=impact_calc["model_assumptions"]
            )
            db.add(impact_rec)

            sol = SolutionMapping(
                workspace_id=workspace_id,
                hypothesis_id=hyp.id,
                solution_type="NEEDS_DISCOVERY",
                title=tmpl["solution_catalogue"],
                capability_summary="Requires exploratory multi-surface booking verification before commercial proposal.",
                feasibility_score=0.60,
                operator_approved=False
            )
            db.add(sol)
            generated.append(hyp)

        # 2. Review Complaint Hypotheses
        review_ev = [e for e in evidence_list if e.signal_type == "REVIEW_TEXT"]
        if review_ev:
            tmpl_call = self.CATEGORY_TEMPLATES["MISSED_CALLS_UNANSWERED_INQUIRIES"]
            hyp_call = ProblemHypothesis(
                workspace_id=workspace_id,
                business_id=business_id,
                category="MISSED_CALLS_UNANSWERED_INQUIRIES",
                title=tmpl_call["title"],
                description=tmpl_call["desc"],
                status="VALIDATED",
                confidence=0.85,
                supporting_evidence_ids=[e.id for e in review_ev]
            )
            db.add(hyp_call)
            db.flush()

            bm = tmpl_call["benchmark"]
            impact_calc = ImpactCalculator.calculate_monetary_impact(
                currency=currency,
                n_lost_inquiries_monthly=bm["n_lost"],
                v_ticket_avg=bm["v_ticket"],
                c_conversion_rate=bm["c_rate"],
                h_wasted_hours_monthly=bm["h_waste"],
                r_labor_hourly_rate=bm["r_labor"]
            )

            impact_rec = ImpactAssessment(
                workspace_id=workspace_id,
                hypothesis_id=hyp_call.id,
                impact_currency=impact_calc["currency"],
                estimated_impact_low=impact_calc["estimated_impact_low"],
                estimated_impact_high=impact_calc["estimated_impact_high"],
                estimated_impact_midpoint=impact_calc["estimated_impact_midpoint"],
                business_specific_measurements=impact_calc["business_specific_measurements"],
                industry_benchmark_assumptions=impact_calc["industry_benchmark_assumptions"],
                model_assumptions=impact_calc["model_assumptions"]
            )
            db.add(impact_rec)

            sol_call = SolutionMapping(
                workspace_id=workspace_id,
                hypothesis_id=hyp_call.id,
                solution_type="CATALOGUE_SOLUTION",
                title=tmpl_call["solution_catalogue"],
                capability_summary="Automated AI Voice & SMS receptionist to handle missed inquiries instantaneously.",
                feasibility_score=0.90,
                operator_approved=True
            )
            db.add(sol_call)
            generated.append(hyp_call)

        db.commit()
        return generated
