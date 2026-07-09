from app.config import TierThresholds
from app.models.schemas import Tier
from app.services.matching.tiering import assign_tier

THRESHOLDS = TierThresholds(accept_min=0.85, review_min=0.60)


def test_boundary_scores():
    # Issue #2: With accept_min = 0.85, a score of exactly 0.85 should be Tier.green (inclusive lower bound)
    assert assign_tier(0.85, THRESHOLDS) is Tier.green

    # Exactly review_min = 0.60 should be Tier.yellow (inclusive lower bound)
    assert assign_tier(0.60, THRESHOLDS) is Tier.yellow
