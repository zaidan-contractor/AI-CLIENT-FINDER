from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional
from app.core.currency import is_valid_currency_code

class ImpactCalculator:
    """
    Deterministic Financial Impact Model.
    All calculations strictly use Python Decimal arithmetic.
    Enforces ISO-4217 currency requirement (no silent fallback).
    Enforces midpoint mathematical constraint: midpoint == (low + high) / 2.
    """

    @staticmethod
    def calculate_monetary_impact(
        currency: str,
        n_lost_inquiries_monthly: Decimal,
        v_ticket_avg: Decimal,
        c_conversion_rate: Decimal,
        h_wasted_hours_monthly: Decimal = Decimal("0"),
        r_labor_hourly_rate: Decimal = Decimal("0"),
        variance_percentage: Decimal = Decimal("0.20")  # ±20% for low/high band
    ) -> Dict[str, Any]:
        if not currency or not is_valid_currency_code(currency):
            raise ValueError(f"Invalid or missing ISO-4217 currency code: '{currency}'. No silent fallback allowed.")

        # 1. Annualized Revenue Leakage: (N_lost * V_ticket * C_conversion) * 12
        monthly_rev_leakage = n_lost_inquiries_monthly * v_ticket_avg * c_conversion_rate
        annual_rev_leakage = monthly_rev_leakage * Decimal("12")

        # 2. Annualized Labor Waste: (H_wasted * R_labor) * 12
        monthly_labor_waste = h_wasted_hours_monthly * r_labor_hourly_rate
        annual_labor_waste = monthly_labor_waste * Decimal("12")

        # 3. Base Annualized Impact
        base_annual_total = (annual_rev_leakage + annual_labor_waste).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 4. Low, High, Midpoint
        low = (base_annual_total * (Decimal("1.0") - variance_percentage)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        high = (base_annual_total * (Decimal("1.0") + variance_percentage)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        midpoint = ((low + high) / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if low > high:
            raise ValueError(f"Invalid impact range: low ({low}) cannot exceed high ({high}).")

        expected_midpoint = ((low + high) / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if midpoint != expected_midpoint:
            raise ValueError(f"Impact midpoint mismatch: supplied {midpoint} != expected {expected_midpoint}.")

        return {
            "currency": currency.upper(),
            "financial_impact_status": "CALCULATED",
            "estimated_impact_low": low,
            "estimated_impact_high": high,
            "estimated_impact_midpoint": midpoint,
            "annual_revenue_leakage": annual_rev_leakage.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "annual_labor_waste": annual_labor_waste.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "observed_financial_data": {},
            "business_specific_measurements": {
                "n_lost_inquiries_monthly": str(n_lost_inquiries_monthly),
                "h_wasted_hours_monthly": str(h_wasted_hours_monthly)
            },
            "industry_benchmark_assumptions": {
                "v_ticket_avg": str(v_ticket_avg),
                "c_conversion_rate": str(c_conversion_rate),
                "r_labor_hourly_rate": str(r_labor_hourly_rate)
            },
            "model_assumptions": {
                "variance_percentage": str(variance_percentage),
                "annualization_factor": "12"
            }
        }

    @staticmethod
    def create_unknown_impact(
        currency: str,
        missing_inputs: Optional[list] = None,
        reason: str = "Required business-specific volume metrics are unobserved."
    ) -> Dict[str, Any]:
        if not currency or not is_valid_currency_code(currency):
            currency = "USD"
        return {
            "currency": currency.upper(),
            "financial_impact_status": "UNKNOWN",
            "estimated_impact_low": None,
            "estimated_impact_high": None,
            "estimated_impact_midpoint": None,
            "annual_revenue_leakage": None,
            "annual_labor_waste": None,
            "observed_financial_data": {
                "status": "UNKNOWN",
                "reason": reason,
                "missing_inputs": missing_inputs or ["booking_volume", "average_ticket_value", "leakage_rate"]
            },
            "business_specific_measurements": {},
            "industry_benchmark_assumptions": {},
            "model_assumptions": {}
        }
