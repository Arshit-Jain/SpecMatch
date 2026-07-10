# SpecMatch

Material matching service and review console. A service that matches messy
construction-material records to a canonical catalog, assigns confidence
tiers, and exposes the results through an API and a server-rendered review
console.

`backend/app/models/schemas.py` is **frozen** — CI re-hashes it against
`.github/schema.sha256`, so any change fails the build. The matching-engine
design is documented below; see `APPROACH.md` for the full design narrative
and `PLAN.md` for the build order.

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
```

API and console: http://localhost:8000 (console at `/`, API docs at `/docs`).

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests

```bash
cd backend
pytest
```

## Matching engine — retrieval & scoring design

SpecMatch maps messy source records (`CONC RM 30MPa w/ 25% FA`) to clean catalog
prose (`Ready-mix concrete, 30 MPa, 25% fly ash`). A raw character-level ratio fails
because the two vocabularies barely overlap, so the engine is a **hybrid**:
normalization + stdlib token-set similarity + structured category/unit/attribute
signals. It uses **no third-party or ML dependency** — pure stdlib (`difflib`, `re`),
which keeps the clean-clone Docker/CI build unbreakable. Everything sits behind the ABCs
in `services/matching/interfaces.py`, so retrieval and scoring stay swappable.

### 1. Normalization (`normalize.py`) — highest leverage

Most of the accuracy is won here, before anything is compared. `normalize_text()` runs a
fixed pipeline: expand shorthand (`W/` → `with`), space glued units (`30MPa` → `30 mpa`),
normalize grades, collapse whitespace, lowercase, then expand a curated
construction-abbreviation vocabulary (`CONC RM` → `ready-mix concrete`; multi-token
entries applied longest-first). The abbreviation maps are module-level constants,
deliberately **not** in `settings.yaml` — they are domain knowledge, not tunable
parameters.

### 2. Retrieval (`retrieval.py`)

`LexicalRetriever` filters to same-category catalog entries when the record carries a
category, and falls back to the full catalog otherwise. At fixture scale (~800 entries)
brute-force scoring is trivially fast, so retrieval stays simple and the interface exists
mainly for swappability.

### 3. Scoring (`scoring.py`) — four signals, weighted from config

Each candidate gets a composite confidence score in `[0, 1]`: a weighted average of four
signals whose weights come **only** from `config/settings.yaml` (never hardcoded).

| Signal | Default weight | What it measures |
|---|---|---|
| `string_similarity`  | 0.45 | Token-set ratio (`difflib.SequenceMatcher`, max-of-three) over normalized text — robust to word reordering and extra tokens |
| `attribute_match`    | 0.25 | Jaccard overlap of structured specs (MPa, R-value, AWG, HSS/W-beam/rebar dimensions, %, grade) pulled by conservative regexes |
| `category_agreement` | 0.20 | 1.0 if categories match, else 0.0 |
| `unit_compatibility` | 0.10 | 1.0 if units match, else 0.0 |

**Neutral signals.** When a record has no category/unit, or no specs are extractable, that
signal returns `None` and is dropped from *both* the numerator and denominator of the
weighted average — missing data neither rewards nor punishes a candidate. The attribute
signal is what keeps a `W460x60` beam from scoring high against `W150x22` just because the
surrounding prose matches.

### 4. Tiering (`tiering.py`)

The top candidate's score maps to a tier using **inclusive lower bounds** from
`settings.yaml`: `score >= accept_min (0.85)` → 🟢 **green** (auto-selects the catalog id),
`>= review_min (0.60)` → 🟡 **yellow** (human review), otherwise 🔴 **red**.

### 5. Persistence

The top `top_k` (5) candidates per record are stored as a `MatchResult` (JSON in the
`matches` table). Each candidate carries its catalog id + description (*what* matched),
composite `score`, and per-signal `signals` breakdown — enough for the console to explain
*why* a record landed in its tier.

### Tier distribution (full fixture, default config)

Reproduce:

```bash
cd backend && python scripts/show_matches.py
```

| Tier | Count | Share |
|---|---|---|
| 🟢 green (auto-accept) | 114 | 76% |
| 🟡 yellow (needs review) | 25 | 17% |
| 🔴 red (no acceptable match) | 11 | 7% |
| **Total** | **150** | |

The spread confirms the tiers are meaningful — the engine neither greens nor yellows
everything:

- `CONC RM 30MPa w/ 25% FA` → 🟢 **0.961** → `CAT-0015 Ready-mix concrete, 30 MPa, 25% fly ash`
- `MISC MTL ALLOW` → 🔴 **0.346** (no catalog entry is a genuine match)

Results are deterministic for a given `settings.yaml`; retune the weights or thresholds
there and re-run the script to reproduce a new distribution.

### Design trade-off (honest note)

Because retrieval hard-filters to the record's category, every scored candidate for a
categorized record has `category_agreement = 1.0` — the signal lifts the absolute score
but does not discriminate between those candidates, and a record whose true match sits in
a different category would be filtered out. This trades a little recall for precision and
speed, which is acceptable when source categories are trustworthy; records without a
category fall back to full-catalog scoring, where the signal goes neutral.

## Layout

```
backend/app/            FastAPI application
  models/schemas.py     API contracts — FROZEN, do not modify
  routers/              health & records implemented; matches stubbed
  services/ingest.py    CSV ingest (runs at startup)
  services/matching/    hybrid matching engine (normalize → retrieve → score → tier)
  templates/            record table implemented; review panel stubbed
  core/                 logging, errors, storage
backend/tests/          existing tests — pass on a clean clone
config/settings.yaml    tier thresholds & scoring weights
data/                   fixture CSVs (~150 source records, ~800 catalog entries)
```

See `CONTRIBUTING.md` for the commit, logging, and error-handling
conventions, and `CLAUDE.md` for AI-assistant context.
