# Implementation Plan — SpecMatch

*Committed before any implementation code, on purpose: it's the evidence of structured
thinking a reviewer can diff against the result.*

## Why I'm following this plan

Four principles set the order:

1. **Fix the foundation before building on it.** Two of the three filed bugs sit in the
   exact primitives the engine stands on — `tiering.assign_tier` (the boundary bug) and
   `ingest`/`db` (the records it reads, the idempotency of the matches it writes). Clearing
   them first (Task 2) means the engine (Task 3) is written *once* against correct semantics,
   not against "intended" behaviour I'm waiting on a later task to deliver. This is the brief's
   own order, and it's the right one.

2. **Core before surface.** The engine produces the data every downstream deliverable reads
   (API, console, `/health` counts), so it comes straight after the fixes and gets my freshest hours.

3. **Test-first for bugs, config-driven for numbers.** A failing test committed before each
   fix (the brief grades this); weights/thresholds read via `get_settings()`, never hardcoded;
   `schemas.py` never touched (CI sha256 freeze — modifying it is an automatic disqualifier).

4. **Docs are a living artifact.** README + CLAUDE.md (Task 8) grow *as I build* and are
   finalized at submission, so design reasoning, deviations, and AI-usage are captured while
   fresh — the brief weights "reflective and honest" AI documentation.


## Order of work

| # | Task | Files | Why in this slot |
|---|---|---|---|
| 1 | **Orientation** | `ARCHITECTURE_NOTES.md`, `PLAN.md` | Map the contracts before touching them; brief requires these committed first |
| 2 | **Fix the 3 filed issues** | `db.py`, `ingest.py`, `tiering.py`, `console.py`, `records.html` | The foundation the engine stands on — clean it first, test-first |
| 3 | **Matching engine** | `normalize.py`, `retrieval.py`, `scoring.py`, `engine.py` + startup wiring | The core; everything downstream consumes it |
| 4 | **API** | `routers/matches.py`, `routers/health.py` | Expose engine output + the auditable review trail |
| 5 | **Review console** | `console.py /review`, `review.html` | Human yellow/red queues, scores, accept/override/reject |
| 6 | **Docker & CI** | `.github/workflows/ci.yml`, compose | Clean-clone boot + data persistence; tests/lint/build/freeze gates |
| 7 | **Tests** | `tests/` | Consolidate to the brief's required matrix; the engine tests are the proof |
| 8 | **AI setup + README (ongoing)** | `CLAUDE.md`, `README.md` | Project rules + living design write-up; finalized at submission |


## Task 3 — The matching engine (the core, most heavily graded)

**Approach: hybrid — normalization + stdlib fuzzy (token-set) similarity + category/unit/attribute scoring.**

The core problem: source records are abbreviated, reordered shop shorthand
(`CONC RM 30MPa w/ 25% FA`) while the catalog is clean prose (`Ready-mix concrete, 30 MPa,
25% fly ash`). A raw character-level ratio fails because the surface vocabularies barely overlap.
No single metric solves that, so the design layers complementary techniques:

1. **Normalization (highest leverage).** Clean and expand a curated construction-abbreviation
   vocabulary so shorthand converges on catalog prose *before* anything is compared. Most of the
   accuracy is won here, not in the similarity metric.

2. **Fuzzy string matching (stdlib `difflib`).** Token-set similarity built on
   `difflib.SequenceMatcher` over the normalized text as the primary signal — robust to word
   reordering and extra tokens, unlike a plain sequence ratio. No third-party dependency, so it
   never touches `requirements.txt` and can't break the clean-clone Docker/CI build.

3. **Structured signals — category, unit, attribute.** String overlap alone over-trusts prose, so
   it is combined with three structured signals: **category agreement**, **unit compatibility**,
   and an **attribute/spec match** on extracted specifications (grade `30 MPa`, `R-22`, size
   `6x6x1/4`, `5/8in`) — the detail that separates near-identical descriptions.

   The starter configuration contains weights for three signals. Task 3 extends
   `config/settings.yaml` with an `attribute_match` weight alongside the existing scoring weights.
   All four signal weights remain configuration-driven through `get_settings()`; no scoring
   constants are embedded in code.

   Missing category/unit/spec data receives a **neutral** contribution, so absent information
   neither rewards nor punishes a candidate.

4. **Composite + tiering.** The four signals combine into one confidence score in `[0,1]` using
   **weights read from config, never hardcoded**; the score maps to green/yellow/red via the config
   thresholds. Each record persists its top candidates **with the per-signal breakdown**, so every
   tier decision is explainable in the console.


**Why hybrid, not one metric:** normalization fixes vocabulary mismatch, the token-set metric
handles messy prose, and the structured signals catch cases where two descriptions read alike but differ on
category/unit/spec. `CONC RM 30MPa w/ 25% FA` should land confidently green; `MISC MTL ALLOW`
should honestly land red. An engine that dumps everything in yellow has dodged the problem; one
that greens everything hasn't understood it.

At the fixture's scale, scoring every record against the full catalog is trivially fast, so I keep
the retrieval interface only for *swappability* — documented, not gold-plated.

**Stretch (only if the core is solid):** an embedding-based retrieval layer behind the same
interface, compared against this lexical baseline's tier distribution. Explicitly optional — a
strong core beats a weak core plus extras.


## Risks I'm watching

Each maps to a layer of the hybrid approach — where it's most likely to break, and how I contain it.

- **Abbreviation coverage (highest).** Normalization is the highest-leverage layer, so an incomplete
  abbreviation vocabulary makes true matches under-score and collapse into yellow/red. Mitigation:
  build the map from the actual fixture and spot-check known record→catalog pairs (`SRC-0054`), while
  accepting it won't be exhaustive.

- **Attribute extraction is brittle.** The spec signal hinges on parsing messy, inconsistently
  formatted dimensions (`30MPa`, `R-22`, `6x6x1/4`, `5/8in`); a mis-parse either drops the signal or
  invents false agreement. Mitigation: keep extraction conservative — a missed spec scores neutral,
  never wrong — and test it against real records.

- **Fuzzy over-confidence on short/sparse text.** Token-set similarity can score short junk
  strings deceptively high against short catalog rows, risking false greens. Mitigation: verify
  deliberate junk (`MISC MTL ALLOW`) lands red, and never let string similarity alone clear the accept threshold.

- **Blank category/unit/spec fields (~a quarter of records).** A large share of the structured
  weight depends on fields that are frequently absent; **neutral** scoring is the deliberate middle
  between starving green and over-promoting junk — but sparse records require careful validation
  because string similarity carries more influence.

- **Weighting & tier calibration.** Task 3 introduces the fourth `attribute_match` signal weight into
  `settings.yaml`, while thresholds remain fixed in config. The balance between the four signals and
  similarity scale determines whether green is empty or excessive. I tune the *algorithm and weights*
  and sanity-check the tier distribution against concrete matches — never nudge thresholds to hit a target.

- **Scoring has no single right answer.** The most judgment-heavy and most heavily graded piece; I
  keep every decision explainable via the persisted per-signal breakdown so I can defend it live in the walkthrough.


## Time budget & final gate

Budget ~8–12 h. **Thursday AM start → build done by Friday noon → Friday PM QA** before the
11:59 PM ET deadline. Freshest hours to Task 3; if the build runs long, console polish (Task 5)
trims first — the engine and its tests are non-negotiable.

| Task | Allotted | When |
|---|---|---|
| 1 — Orientation docs | ~1.0 h | Thu AM |
| 2 — Fix 3 issues (test-first) | ~1.5 h | Thu AM |
| 3 — Matching engine | ~3.5 h | Thu PM |
| 4 — API | ~1.0 h | Fri AM |
| 5 — Review console | ~1.5 h | Fri AM |
| 6 — Docker & CI | ~1.0 h | Fri AM → noon |
| 7 — Tests | ~1.0 h | Fri PM |
| 8 — AI setup + README | ~1.0 h (concurrent) | throughout → Fri PM |
| QA + buffer | ~1.0 h | Fri PM |