from typing import List, Dict, Any

class ReviewNLPDetector:
    """
    Analyzes customer reviews and sentiment signals to detect operational bottlenecks.
    Detects complaint patterns:
    - Communication bottlenecks (unanswered phones, delayed replies)
    - Scheduling friction (hard to book, long hold times)
    - After-hours inquiry loss
    """

    FRICTION_PATTERNS = {
        "communication_delay": [
            "never answered", "no one picks up", "hard to reach", "didn't call back",
            "slow response", "waited on hold", "impossible to contact"
        ],
        "booking_friction": [
            "couldn't book", "no online booking", "had to call multiple times", "appointment cancelled without notice"
        ],
        "after_hours_leakage": [
            "called after 5", "closed on weekend", "no after hours support", "no emergency booking"
        ]
    }

    def analyze_reviews(self, reviews: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        evidence_items: List[Dict[str, Any]] = []
        if not reviews:
            return evidence_items

        for review in reviews:
            text = review.get("text", "").lower()
            rating = review.get("rating", 5)

            for bottleneck_type, keywords in self.FRICTION_PATTERNS.items():
                matched = [kw for kw in keywords if kw in text]
                if matched and rating <= 3:
                    evidence_items.append({
                        "signal_type": "REVIEW_TEXT",
                        "feature": bottleneck_type,
                        "presence_state": "PRESENT",
                        "confidence": 0.85,
                        "raw_observation": f"Customer review complaint (Rating: {rating}/5): '{review.get('text', '')[:200]}'",
                        "classification_reason": f"Operational bottleneck detected via review keywords: {matched}"
                    })

        return evidence_items
