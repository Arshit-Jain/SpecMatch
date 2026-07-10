# SpecMatch — AI assistant context

SpecMatch matches messy construction-material records to a canonical
catalog, assigns confidence tiers, and exposes the results through a
FastAPI API and a server-rendered (Jinja2) review console.

## Layout

- `backend/app/` — FastAPI application; `routers/` stay thin, logic lives
  in `services/`.
- `backend/app/models/schemas.py` — API contracts. **FROZEN: never modify.**
- `backend/app/services/matching/` — matching interfaces; the engine
  implementation goes here.
- `config/settings.yaml` — scoring weights and tier thresholds. Read them
  via `app.config.get_settings()`; never hardcode.
- `data/` — fixture CSVs ingested at startup.

## Commands

- Run locally: `cd backend && uvicorn app.main:app --reload`
- Tests: `cd backend && pytest`
- Full stack: `docker compose up --build` (API + console on :8000)

## Conventions

See CONTRIBUTING.md for the commit, logging, and error-handling rules.
Follow them in generated code.

---

# Project-specific rules (Section 08)

Rules below are specific to this codebase. They expand on CONTRIBUTING.md;
where they overlap, both apply. Prefer editing an existing module over adding
a new one, and match the surrounding style.

## Frozen surfaces & CI gates

- `backend/app/models/schemas.py` is **frozen**. CI's first step re-hashes it
  against `.github/schema.sha256` (`sha256sum --check`); any byte change —
  even whitespace — fails the build, which is an automatic disqualifier. Never
  edit the file and never regenerate the hash to work around it. If a contract
  looks wrong, argue it in the README; do not change it.
- The API contracts in Tasks 3–4 are frozen too. `/health`, `/matches`, and
  `/matches/{record_id}/review` must return exactly `HealthResponse`,
  `MatchesResponse`, and `MatchResult` from `schemas.py` — no added, renamed,
  or dropped fields.
- Runtime deps live in `backend/requirements.txt`; CI installs from it and
  nowhere else. The matching engine is intentionally **pure-stdlib** (`difflib`,
  `re`). Do not pull in a fuzzy-match/ML library (rapidfuzz, scikit-learn,
  embeddings) without adding it to `requirements.txt` and defending it in the
  README — a forgotten dep means red CI, another automatic disqualifier.
- Never commit credentials. `.env.example` is the only env file in the repo;
  `.env` is git-ignored and stays that way.

## Configuration — weights & thresholds

- Every tunable comes from `config/settings.yaml`, read through
  `app.config.get_settings()`. Never hardcode a weight, tier threshold, or
  `top_k` in Python.
- `get_settings()` returns frozen dataclasses: `settings.matching.weights`
  (`dict[str, float]`), `settings.matching.top_k`, and
  `settings.tiers.accept_min` / `.review_min`.
- `get_settings()` is `@lru_cache(maxsize=1)`. Any test or script that swaps
  the config (via the `SPECMATCH_CONFIG` env var) **must call
  `get_settings.cache_clear()`** or it reads stale values — see
  `backend/scripts/show_matches.py`.
- A scoring signal contributes to the composite only if its name is a key in
  `matching.weights`. The four names — `string_similarity`,
  `category_agreement`, `unit_compatibility`, `attribute_match` — are a
  contract shared between `settings.yaml` and `scoring.py`; change them in
  lockstep or not at all.

## Error handling — external dependencies

Every call to an external dependency (SQLite, filesystem, subprocess, network)
uses this exact shape (see `services/ingest.py`, `config.py`,
`matching/engine.py`):

```python
try:
    ...  # the risky call
except sqlite3.Error as exc:            # the dependency's *specific* type
    log_event(logger, logging.ERROR, "dependency_failure",
              dependency="sqlite", operation="load_catalog", error=str(exc))
    raise DependencyError("could not load catalog from database") from exc
```

Catch the specific exception (`sqlite3.Error`, `OSError`, …) at the call site —
never a bare `except`. Log a `dependency_failure` event carrying `dependency=`
and enough context to reproduce. Re-raise as `app.core.errors.DependencyError`
with `from exc`. Never let a raw `OSError`/`sqlite3.Error` escape a service
function, and never silently swallow one.

## Logging

- One structured event per log line via
  `log_event(logger, level, event, **fields)` (`app.core.logging`). `event` is
  a snake_case identifier; all context goes in keyword fields.
- No `print()`, and never interpolate values into the event string — write
  `log_event(..., "ingest_completed", record_rows=n)`, not
  `logger.info(f"ingested {n}")`.
- Reuse/extend the existing event vocabulary: `ingest_completed`,
  `matching_completed`, `dependency_failure`, `app_started`. New events (e.g.
  `review_persisted`) follow the same naming.

## Persistence & the SQLite layer

- Storage is SQLite at `DATA_DIR/specmatch.db` (`app.core.db`). In Docker
  `DATA_DIR=/data` is a named volume — that is what makes data survive
  restarts. `get_conn()` sets `row_factory = sqlite3.Row` (index rows by column
  name) and `PRAGMA foreign_keys = ON`.
- Three tables (`db.py::SCHEMA`): `records` (surrogate autoincrement `id` plus
  a **non-unique** `record_id`), `catalog` (`catalog_id` PK), and `matches`
  (`record_id` PK, `payload` = JSON-serialized `MatchResult`, with `tier` and
  `matched_at` denormalized for cheap filtering).
- Idempotency is table-specific and deliberate: catalog and matches use
  `INSERT OR REPLACE`; **records use `INSERT ... WHERE NOT EXISTS`** because
  `record_id` has no unique constraint (the Issue #1 fix — re-ingest must not
  duplicate). Preserve that asymmetry.
- Connection lifecycle: routers open a connection and close it in `try/finally`
  (`routers/records.py`, `health.py`). Service entrypoints take an optional
  `conn` and use the `owned = conn is None` pattern to close only what they
  opened (`run_ingest`, `run_matching`). Match the pattern of the layer you are
  editing.
- When building Pydantic models from rows, coerce empty strings to `None`:
  `category=row["category"] or None`.

## Matching engine

The engine lives entirely under `services/matching/` and must stay behind the
ABCs in `interfaces.py` (`CandidateRetriever`, `CandidateScorer`,
`MatchingEngine`) so retrieval/scoring strategies remain swappable.

- `normalize.py` — `normalize_text()` is the highest-leverage layer: it expands
  a curated construction-abbreviation vocabulary to catalog prose *before*
  anything is compared. **The abbreviation maps are module-level constants, on
  purpose not in `settings.yaml`** — they are domain knowledge, not tunable
  parameters. Step order matters (shortcuts → unit spacing → grades → collapse
  whitespace → lowercase → expand abbreviations, multi-token longest-first).
- `retrieval.py` — `LexicalRetriever` does a category-first filter with a
  full-catalog fallback; brute-force is fine at fixture scale (`limit=0` means
  "score the whole catalog").
- `scoring.py` — `LexicalScorer` combines four signals; attribute extraction is
  a suite of conservative regexes. Two hard-won rules: **(1)** every attribute
  regex runs on the already-lowercased normalized text, so it must carry
  `re.IGNORECASE` (the `_WBEAM_RE`/`_CHANNEL_RE` misses came from forgetting
  this); **(2)** a missed spec must score *neutral*, never a false match.
- **Neutral-signal math:** a signal that cannot be evaluated (missing
  category/unit, no extractable attributes) returns `None` and is excluded from
  *both* the numerator and the denominator of the weighted average — missing
  data neither rewards nor punishes. Preserve this when adding a signal.
- `tiering.py` — `assign_tier()` thresholds are **inclusive lower bounds**
  (`score >= accept_min` → green). This is the Issue #2 fix; do not reintroduce
  a strict `>`.
- Design intent: tiers must be *meaningfully distributed*. Junk like
  `MISC MTL ALLOW` should land red; a clean `CONC RM 30MPa` should land green.
  An engine that dumps everything into yellow (or green) has not solved the
  problem. Only green matches auto-select a `selected_catalog_id`. Persist
  `top_k` candidates with their full per-signal breakdown so the console can
  explain *why* a record landed where it did.
- Tier readout over the whole fixture set:
  `cd backend && python scripts/show_matches.py`.

## API & routers (Task 4)

- Routers stay thin: request/response handling and validation only; all logic
  in `services/`. Type-annotate signatures and use FastAPI `Query(...)`
  validators as `records.py` does.
- The read/review side of the matches table lives in `services/matches.py`,
  the complement to `services/matching/engine.py`: the engine **writes** each
  record's initial `MatchResult`; `matches.py` **reads** them (`list_matches`,
  `get_match`) and applies review decisions (`apply_review`). Keep that split —
  don't fold query/review logic into the matching engine.
- `/matches` orders by `record_id` (ids are zero-padded and unique, so this is
  ingestion order and deterministic *without* joining the non-unique
  `records.record_id`). `total` is the count under the same tier filter, not
  the page length. The `tier` query param is the `Tier` enum, so an unknown
  tier is a 422 for free.
- Review decisions are **persisted and auditable**: store the `Review`
  (`action`, resolved `catalog_id`, `note`, `reviewed_at`) on the record's
  `MatchResult`, log a `review_persisted` event (`record_id`, `action`,
  `catalog_id`, `tier`), and return the updated result. Only `payload` is
  rewritten — `tier` and `matched_at` stay as the engine recorded them, so
  tier-filtered queries keep reflecting the *match*, not the review.
- Review semantics (`_resolve_selection`): **accept** selects the top
  candidate; **override** requires `catalog_id` and it must be one of the
  record's own candidates; **reject** clears `selected_catalog_id`. In all
  three, `review.catalog_id` mirrors the resolved `selected_catalog_id`.
- Service→HTTP error mapping is the router's only branching: `NotFoundError`
  → 404, `InvalidReviewError` → 400 (both in `app.core.errors`); malformed
  bodies (unknown `action`) are pydantic 422s. Services never import FastAPI.
- Leave `/records` and `/health` behaviour intact (Issue #1 aside).

## Review console (Task 5)

- Server-rendered Jinja2 only — no JS framework, no client build. Templates
  live in `backend/app/templates/`; every page `{% extends "base.html" %}` and
  fills `{% block content %}`. Reuse the existing CSS variables and classes
  (`.toolbar`, `.muted`, the dark theme) instead of adding stylesheets.
- Filters are plain GET forms that auto-submit
  (`onchange="this.form.submit()"`) with a `<noscript>` fallback button —
  mirror `records.html`.
- **The "All" gotcha:** the category selector submits the literal string
  `"All"` for the no-filter option, and the handler maps `"All" → None` before
  querying (the Issue #3 fix — an unmapped sentinel returned an empty list).
  Any new filter needs the same sentinel handling and an empty-state
  (`{% else %}` in the loop).
- The review panel (`/review`, stubbed) must show yellow/red queues with
  counts, each record's source text + top candidates + per-signal breakdown,
  and accept/override/reject actions that persist through the API and reflect
  the persisted result.

## Commits & tests

- Imperative subject ≤72 chars, one logical change per commit, issues
  referenced with `#N`. Reproduce a bug with a **failing test committed before**
  the fix. The established issue-work format in this repo is a paired
  `Issue #N [test]: …` commit followed by `Issue #N [fix]: …`.
- Tests run from `backend/` with `pytest`. Back them with a throwaway
  `DATA_DIR` (tmp dir) as in `conftest.py` / `test_engine.py`, and call
  `get_settings.cache_clear()` in any test that mutates config. Matching tests
  should spot-check named fixtures to specific catalog IDs and assert at least
  one deliberate red — they are the proof the engine does what the README
  claims.

## Path-resolution gotcha

Repo-root is derived from `__file__` depth, and the count differs by module:
`config.py` uses `parents[2]`, `ingest.py` uses `parents[3]`. A new module that
needs a repo-relative path must count its own depth — never copy a literal
`parents[N]` from another file.
