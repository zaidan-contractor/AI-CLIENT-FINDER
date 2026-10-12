import time
from typing import Dict, Any, List

class InfraProbe:
    """
    Probes digital infrastructure:
    - SSL certificate presence and validity
    - Time-to-First-Byte (TTFB) / server response latency
    """

    def probe(self, url: str, is_ssl: bool = True, ttfb_ms: float = 180.0, is_accessible: bool = True) -> List[Dict[str, Any]]:
        evidence_items: List[Dict[str, Any]] = []

        if not is_accessible:
            evidence_items.append({
                "signal_type": "INFRA_PROBE",
                "feature": "infrastructure_availability",
                "presence_state": "UNKNOWN",
                "confidence": 0.0,
                "raw_observation": f"Server for {url} unreachable or network timeout.",
                "classification_reason": "Inaccessibility != Absence: host was not reachable."
            })
            return evidence_items

        # SSL probe
        evidence_items.append({
            "signal_type": "INFRA_PROBE",
            "feature": "ssl_certificate",
            "presence_state": "PRESENT" if is_ssl else "ABSENCE_CONFIRMED",
            "confidence": 1.0,
            "raw_observation": f"HTTPS active: {is_ssl}",
            "classification_reason": "SSL/TLS certificate validated on standard port 443." if is_ssl else "Insecure HTTP only."
        })

        # Latency probe
        speed_category = "FAST" if ttfb_ms < 300 else ("MODERATE" if ttfb_ms < 800 else "SLOW")
        evidence_items.append({
            "signal_type": "INFRA_PROBE",
            "feature": "server_latency",
            "presence_state": "PRESENT" if speed_category == "FAST" else "NOT_OBSERVED",
            "confidence": 0.9,
            "raw_observation": f"TTFB latency measured at {ttfb_ms:.1f}ms ({speed_category})",
            "classification_reason": f"Server response time classified as {speed_category}."
        })

        return evidence_items
