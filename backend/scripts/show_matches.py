"""Quick diagnostic: show all match results with tier, top candidate, and score."""

import os
import sys
from pathlib import Path

# Ensure the app modules are importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import get_conn, init_schema
from app.services.matching.engine import run_matching


def main():
    # Use a temp dir so we don't pollute the real DB.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATA_DIR"] = tmp

        # Ingest + match.
        conn = get_conn()
        init_schema(conn)

        from app.services.ingest import ingest_catalog, ingest_records

        ingest_catalog(conn)
        ingest_records(conn)

        # Clear lru_cache so config reloads cleanly.
        from app.config import get_settings

        get_settings.cache_clear()

        results = run_matching(conn)

        # Split by tier.
        greens = [r for r in results if r.tier.value == "green"]
        yellows = [r for r in results if r.tier.value == "yellow"]
        reds = [r for r in results if r.tier.value == "red"]

        print(f"\n{'=' * 90}")
        print(
            f"  MATCHING RESULTS: "
            f"{len(greens)} green | {len(yellows)} yellow | {len(reds)} red"
        )
        print(f"{'=' * 90}\n")

        def print_match(r):
            top = r.candidates[0]

            print(
                f"  {r.record_id:10s} │ "
                f"{r.source_text:50s} │ "
                f"score={top.score:.3f}"
            )
            print(
                f"  {'':10s} │ "
                f"→ {top.catalog_id}: {top.description}"
            )

            sigs = " | ".join(
                f"{k}={v:.2f}"
                for k, v in top.signals.items()
            )

            print(
                f"  {'':10s} │ "
                f"signals: {sigs}"
            )
            print()

        # Green matches.
        print(f"{'─' * 90}")
        print(f"  🟢 GREEN (auto-accept) — {len(greens)} records")
        print(f"{'─' * 90}")

        for r in sorted(
            greens,
            key=lambda x: x.candidates[0].score,
            reverse=True,
        ):
            print_match(r)

        # Yellow matches.
        print(f"{'─' * 90}")
        print(f"  🟡 YELLOW (needs review) — {len(yellows)} records")
        print(f"{'─' * 90}")

        for r in sorted(
            yellows,
            key=lambda x: x.candidates[0].score,
            reverse=True,
        ):
            print_match(r)

        # Red matches.
        print(f"{'─' * 90}")
        print(f"  🔴 RED (no match) — {len(reds)} records")
        print(f"{'─' * 90}")

        for r in sorted(
            reds,
            key=lambda x: x.candidates[0].score if x.candidates else -1,
            reverse=True,
        ):
            if r.candidates:
                print_match(r)
            else:
                print(
                    f"  {r.record_id:10s} │ "
                    f"{r.source_text:50s} │ "
                    f"no candidates"
                )
                print()

        conn.close()


if __name__ == "__main__":
    main()