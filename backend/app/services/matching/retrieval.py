"""Candidate retrieval for the matching engine.

At fixture scale (800 catalog entries), brute-force scoring of every entry
is trivially fast, so the retriever is kept simple.  The interface exists
for swappability — documented, not gold-plated.
"""

from __future__ import annotations

from app.models.schemas import CatalogEntry, RecordOut
from app.services.matching.interfaces import CandidateRetriever


class LexicalRetriever(CandidateRetriever):
    """Return catalog candidates for one source record.

    Strategy: if the record carries a category, prefer same-category entries
    first.  If that yields fewer than *limit* results (or the record has no
    category), fall back to the full catalog.  At 800 rows this is fast
    enough; the limit is applied *after* scoring in the engine to keep the
    retriever simple.
    """

    def retrieve(
        self, record: RecordOut, catalog: list[CatalogEntry], limit: int
    ) -> list[CatalogEntry]:
        """Return up to ``limit`` catalog entries worth scoring for ``record``."""
        if record.category:
            same_cat = [
                e for e in catalog
                if e.category.lower() == record.category.lower()
            ]
            if same_cat:
                return same_cat[:limit] if limit else same_cat
        # No category on record, or no same-category hits — return the full
        # catalog (capped at limit).
        return catalog[:limit] if limit else catalog
