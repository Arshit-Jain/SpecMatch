from app.config import get_settings


def test_settings_load_from_yaml():
    settings = get_settings()
    assert settings.matching.top_k >= 1
    assert set(settings.matching.weights) == {
        "string_similarity",
        "category_agreement",
        "unit_compatibility",
        "attribute_match",
    }
    assert 0.0 < settings.tiers.review_min < settings.tiers.accept_min < 1.0
