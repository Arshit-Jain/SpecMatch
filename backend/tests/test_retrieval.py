"""Tests for the candidate retrieval module."""

from app.models.schemas import CatalogEntry, RecordOut
from app.services.matching.retrieval import LexicalRetriever
from datetime import datetime, timezone


def _make_record(record_id: str = "SRC-TEST", raw_text: str = "test",
                 category: str | None = None, unit: str | None = None) -> RecordOut:
    return RecordOut(
        record_id=record_id,
        raw_text=raw_text,
        category=category,
        unit=unit,
        ingested_at=datetime.now(timezone.utc),
    )


def _make_catalog() -> list[CatalogEntry]:
    return [
        CatalogEntry(catalog_id="CAT-001", description="Concrete item", category="Concrete", unit="m3"),
        CatalogEntry(catalog_id="CAT-002", description="Steel item", category="Structural Steel", unit="kg"),
        CatalogEntry(catalog_id="CAT-003", description="Another concrete", category="Concrete", unit="m3"),
        CatalogEntry(catalog_id="CAT-004", description="Wood item", category="Wood", unit="m"),
        CatalogEntry(catalog_id="CAT-005", description="Insulation item", category="Insulation", unit="m2"),
    ]


class TestCategoryFiltering:
    """Records with a category should prefer same-category catalog entries."""

    def test_same_category_returned(self):
        retriever = LexicalRetriever()
        record = _make_record(category="Concrete")
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=10)
        assert len(results) == 2
        assert all(e.category == "Concrete" for e in results)

    def test_category_case_insensitive(self):
        retriever = LexicalRetriever()
        record = _make_record(category="concrete")
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=10)
        assert len(results) == 2

    def test_category_with_no_matches_returns_full_catalog(self):
        retriever = LexicalRetriever()
        record = _make_record(category="NonexistentCategory")
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=10)
        assert len(results) == 5  # full catalog


class TestNoCategory:
    """Records without a category should get the full catalog."""

    def test_no_category_returns_all(self):
        retriever = LexicalRetriever()
        record = _make_record(category=None)
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=10)
        assert len(results) == 5

    def test_empty_category_returns_all(self):
        retriever = LexicalRetriever()
        record = _make_record(category="")
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=10)
        assert len(results) == 5


class TestLimit:
    """Verify the limit parameter caps results."""

    def test_limit_applied_to_filtered(self):
        retriever = LexicalRetriever()
        record = _make_record(category="Concrete")
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=1)
        assert len(results) == 1

    def test_limit_applied_to_full(self):
        retriever = LexicalRetriever()
        record = _make_record(category=None)
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=2)
        assert len(results) == 2

    def test_zero_limit_returns_all(self):
        """A limit of 0 means no cap (return everything)."""
        retriever = LexicalRetriever()
        record = _make_record(category=None)
        catalog = _make_catalog()
        results = retriever.retrieve(record, catalog, limit=0)
        assert len(results) == 5


class TestEmptyCatalog:
    """Edge case: empty catalog."""

    def test_empty_catalog(self):
        retriever = LexicalRetriever()
        record = _make_record(category="Concrete")
        results = retriever.retrieve(record, [], limit=10)
        assert results == []
