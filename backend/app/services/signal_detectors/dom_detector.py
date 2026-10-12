import re
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup

class DOMDetector:
    """
    Evaluates website HTML DOM to discover digital capabilities and bottlenecks.
    Strictly follows Epistemological Invariants:
    - PRESENT: Widget or script definitively observed in DOM
    - ABSENCE_CONFIRMED: Explicit textual denial found in page copy
    - NOT_OBSERVED: Feature not detected, but no explicit denial exists
    - UNKNOWN: Page was inaccessible, blocked, or errored
    """

    BOOKING_SIGNATURES = [
        "calendly.com", "acuityscheduling.com", "fresha.com", "mindbodyonline.com",
        "setmore.com", "appointlet.com", "square.site/book", "booksy.com", "schedulista.com"
    ]

    CHAT_SIGNATURES = [
        "intercom.io", "drift.com", "tidio.co", "zendesk.com", "crisp.chat", "hubspot.com",
        "livechatinc.com", "tawk.to"
    ]

    EXPLICIT_ABSENCE_PATTERNS = [
        r"we do not (?:offer|have|support|accept) online (?:booking|appointments?|reservations?)",
        r"no online (?:booking|appointments?|reservations?)(?: (?:available|accepted|widget|system|form))?",
        r"online (?:booking|scheduling|appointments?) (?:is|are) (?:not available|unavailable|disabled)"
    ]

    OFFLINE_COORDINATION_PATTERNS = [
        r"call (?:us )?(?:to|for) (?:book|schedule|appointments?|reservations?)",
        r"walk-?ins? (?:only|welcome)",
        r"phone (?:appointments?|bookings?|reservations?) only"
    ]

    def analyze_html(self, html_content: Optional[str], status_code: int = 200, is_accessible: bool = True) -> List[Dict[str, Any]]:
        evidence_items: List[Dict[str, Any]] = []

        # Inaccessibility Invariant: If blocked, timed out, or HTTP error, presence is strictly UNKNOWN
        if not is_accessible or status_code >= 400 or not html_content:
            evidence_items.append({
                "signal_type": "DOM_SELECTOR",
                "feature": "online_booking",
                "presence_state": "UNKNOWN",
                "confidence": 0.0,
                "raw_observation": f"Website inaccessible or returned HTTP {status_code}",
                "classification_reason": "Inaccessibility != Absence: crawler could not access target page."
            })
            return evidence_items

        soup = BeautifulSoup(html_content, "html.parser")
        html_str = html_content.lower()
        text_str = soup.get_text(" ", strip=True).lower()

        # 1. Online Booking Engine Analysis
        booking_found = False
        matched_sig = ""
        for sig in self.BOOKING_SIGNATURES:
            if sig in html_str:
                booking_found = True
                matched_sig = sig
                break

        if booking_found:
            evidence_items.append({
                "signal_type": "DOM_SELECTOR",
                "feature": "online_booking",
                "presence_state": "PRESENT",
                "confidence": 1.0,
                "raw_observation": f"Booking engine detected via script/iframe signature: {matched_sig}",
                "classification_reason": "Active booking integration confirmed in DOM."
            })
        else:
            # Check for explicit textual denial
            explicit_denial_match = None
            for pattern in self.EXPLICIT_ABSENCE_PATTERNS:
                m = re.search(pattern, text_str)
                if m:
                    explicit_denial_match = m.group(0)
                    break

            if explicit_denial_match:
                evidence_items.append({
                    "signal_type": "DOM_SELECTOR",
                    "feature": "online_booking",
                    "presence_state": "ABSENCE_CONFIRMED",
                    "confidence": 0.95,
                    "raw_observation": f"Explicit denial text observed: '{explicit_denial_match}'",
                    "classification_reason": "Business explicitly stated absence of online booking in copy."
                })
            else:
                # Check for offline signals
                offline_match = None
                for pattern in self.OFFLINE_COORDINATION_PATTERNS:
                    m = re.search(pattern, text_str)
                    if m:
                        offline_match = m.group(0)
                        break

                reason = "No booking widget observed in page DOM."
                if offline_match:
                    reason += f" Observed offline coordination signal: '{offline_match}'. Offline signal does NOT prove online absence."

                evidence_items.append({
                    "signal_type": "DOM_SELECTOR",
                    "feature": "online_booking",
                    "presence_state": "NOT_OBSERVED",
                    "confidence": 0.75,
                    "raw_observation": f"No booking widget in DOM. Text: '{offline_match or 'none'}'",
                    "classification_reason": reason
                })

        # 2. Live Chat / AI Agent Widget Analysis
        chat_found = False
        matched_chat = ""
        for sig in self.CHAT_SIGNATURES:
            if sig in html_str:
                chat_found = True
                matched_chat = sig
                break

        if chat_found:
            evidence_items.append({
                "signal_type": "DOM_SELECTOR",
                "feature": "live_chat_support",
                "presence_state": "PRESENT",
                "confidence": 1.0,
                "raw_observation": f"Live chat widget detected: {matched_chat}",
                "classification_reason": "Active chat integration confirmed in DOM."
            })
        else:
            evidence_items.append({
                "signal_type": "DOM_SELECTOR",
                "feature": "live_chat_support",
                "presence_state": "NOT_OBSERVED",
                "confidence": 0.85,
                "raw_observation": "No live chat widget scripts observed in DOM.",
                "classification_reason": "Live chat integration not observed."
            })

        # 3. Mobile Viewport Analysis
        viewport_tag = soup.find("meta", attrs={"name": "viewport"})
        if viewport_tag:
            evidence_items.append({
                "signal_type": "META_TAG",
                "feature": "mobile_responsive_viewport",
                "presence_state": "PRESENT",
                "confidence": 1.0,
                "raw_observation": f"Meta viewport present: {viewport_tag.get('content', '')}",
                "classification_reason": "Mobile viewport meta tag configured."
            })
        else:
            evidence_items.append({
                "signal_type": "META_TAG",
                "feature": "mobile_responsive_viewport",
                "presence_state": "NOT_OBSERVED",
                "confidence": 0.95,
                "raw_observation": "Missing <meta name='viewport'> tag in page head.",
                "classification_reason": "Mobile responsive viewport not configured."
            })

        return evidence_items
