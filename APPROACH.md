# SpecMatch Matching Engine: Implementation Approach

This document outlines the architectural and design decisions behind the core Matching Engine (Task 3) in SpecMatch.

## 1. Problem Overview

The core challenge of SpecMatch is mapping messy, inconsistent, and often highly abbreviated construction materials (the "Source Records") to a clean, standardized master list (the "Catalog"). 

Source records suffer from:
*   **Severe abbreviations** (`CONC RM 30MPA w/ 25% FA` vs `Ready-mix concrete, 30 MPa, 25% fly ash`)
*   **Missing context** (Lacking explicit categories or units)
*   **Subtle but critical variations** (e.g., `W460x60` vs `W410x54`, or `Grade B` vs `Grade C`)

## 2. The Hybrid Approach

Instead of relying purely on an LLM or purely on simple string distance, we implemented a **Hybrid Lexical + Heuristic Engine**. 

*   **Why not pure LLM?** While an LLM could understand the context, running 150 records against 800 catalog entries via LLM is slow, expensive, and prone to hallucinations or non-deterministic tiering.
*   **Why not pure string distance?** Levenshtein distance fails completely on `CONC RM` vs `Ready-mix concrete`.
*   **Our Solution:** We built an engine that normalizes domain-specific abbreviations, explicitly extracts critical engineering attributes (like dimensions and grades), and calculates a composite confidence score across four distinct signals.

---

## 3. Engine Architecture

The engine is orchestrated by `LexicalMatchingEngine` in `engine.py`, which guides a record through four distinct phases: **Normalization**, **Retrieval**, **Scoring**, and **Tiering**.

### Phase 1: Normalization (`normalize.py`)
Before any matching occurs, text is run through a normalization pipeline:
1.  **Abbreviation Expansion:** A predefined dictionary maps common industry shorthand to their full catalog equivalents (e.g., `STL HSS` → `steel hss`, `BATT INSUL MW` → `batt insulation, mineral wool,`).
2.  **Standardization:** All text is converted to lowercase, multi-spaces are collapsed, and edge cases like `W/` are converted to `with`.
3.  **Unit Spacing:** Glued units are separated (e.g., `30MPA` → `30 mpa`) so that tokenizers treat them consistently.

### Phase 2: Candidate Retrieval (`retrieval.py`)
At the current scale (800 catalog entries), brute-force scoring is trivially fast. 
*   **Category-First Strategy:** The `LexicalRetriever` attempts to filter the catalog down to entries matching the source record's category. 
*   **Fallback:** If the source record has no category, or no matches are found in that category, it falls back to scoring the entire catalog.

### Phase 3: Scoring & Attribute Extraction (`scoring.py`)
This is the heart of the engine. A candidate is evaluated against a source record using a **4-signal composite score**. Weights are configurable via `settings.yaml`.

1.  **String Similarity (Token-Set):** Uses a max-of-three-ratios token-set approach. This makes the metric robust to word reordering and extra tokens. If a catalog entry is a perfect subset of the source record, it scores highly.
2.  **Category Agreement:** 1.0 if categories match exactly, 0.0 if they differ.
3.  **Unit Compatibility:** 1.0 if units match exactly, 0.0 if they differ.
4.  **Attribute Match (Jaccard Similarity):** A suite of 15+ conservative Regular Expressions explicitly extracts critical engineering specs from the *normalized* text. 
    *   *Examples:* Rebar sizes (`15m`), HSS dimensions (`10x6x1/2`), Lumber grades (`no1/btr`), Facing (`unfaced`).
    *   The extracted attributes from both strings are compared using Jaccard Similarity (Intersection / Union). This prevents a W-beam `W460x60` from falsely scoring high against a `W150x22` simply because the rest of the text matches.

#### The "Neutral Signal" Philosophy
A critical design decision was how to handle missing data. If a source record simply says `STL HSS 10x6x1/2` and lacks a category, we **do not penalize it**. 
Instead, the `category_agreement` signal evaluates to `None` and is mathematically excluded from both the numerator and denominator of the weighted average. Missing data neither rewards nor punishes the candidate; the engine simply relies heavier on the data it *does* have.

### Phase 4: Tiering & Auto-Selection (`tiering.py`)
Once scored, the candidates are sorted. The top candidate's score determines the tier of the match based on inclusive lower bounds defined in `settings.yaml`:
*   🟢 **Green (Auto-Accept):** High confidence (e.g., `>= 0.85`). The engine automatically selects the top catalog ID.
*   🟡 **Yellow (Review Required):** Moderate confidence. The system provides top suggestions, but a human must make the final call.
*   🔴 **Red (No Match):** Low confidence. Usually indicates junk data (e.g., `MISC MTL ALLOW`).

---

## 4. Quality Assurance & Testing

The matching engine is backed by over **120+ unit and integration tests** ensuring extreme reliability:
*   **Spot Checks:** Tests explicitly verify that known problematic fixtures (like `SRC-0054`) correctly map to their intended catalog entries (`CAT-0015`).
*   **Bug Prevention:** Specific tests ensure that substring exploits are prevented. For instance, ensuring that uppercase regexes still work on normalized lowercase text, and ensuring that subset string matching doesn't artificially inflate scores when critical attributes (like lumber grades) are missing.
*   **Dependency Injection:** Database connections are strictly managed, and the `sqlite3` dependency failures gracefully throw custom `DependencyError` exceptions per the repository's `CONTRIBUTING.md` guidelines.

---

## 5. Optimizations & Edge Cases Encountered

During the implementation and QA phase, we identified and resolved several critical edge cases where the engine's attribute extraction was either too strict or missing context, leading to artificially inflated scores. Here is how we overcame them:

### Case 1: Steel Grading & Un-Normalized Extraction (`SRC-0080` & `SRC-0093`)
*   **The Issue:** The engine matched `STL HSS 10X6X1/2 GR B` to a `Grade C` catalog entry with a high score. The `_GRADE_RE` regex only looked for numeric grades (like `Grade 400W`) and missed letter grades (`Grade C`). Furthermore, `_attribute_match()` was mistakenly parsing the raw un-normalized text (`GR B`), which didn't match the word "Grade". As a result, the engine ignored the grade entirely, saw a 100% attribute match on the `10x6x1/2` dimensions, and falsely boosted the score to Green.
*   **The Solution:** We updated `_GRADE_RE` to capture alphanumeric grades (`\bgrade\s+([a-z0-9]+)\b`) and fixed `_attribute_match()` to parse the **normalized text**. This ensured `GR B` was expanded to `grade b` *before* extraction, allowing the engine to properly penalize the `Grade B` vs `Grade C` mismatch and drop the score to the Yellow tier for human review.

### Case 2: Lumber Grading (`SRC-0103`)
*   **The Issue:** `LBR 38X184MM SPF NO.1/BTR` falsely matched a `SPF No.2` catalog entry. The engine correctly extracted the dimensions (`38x184`) but didn't know how to extract lumber grades. Since the grade was missed on both sides, the engine thought they were identical based solely on dimensions.
*   **The Solution:** We introduced a new attribute extractor `_LUMBER_GRADE_RE` (`\bno\.?\s*(\d+(?:/btr)?)\b`) specifically for lumber. Now, `no1/btr` and `no2` are explicitly extracted, dropping the attribute overlap to 33% and properly heavily penalizing the mismatched lumber grade.

### Case 3: Insulation Facing & Subset Matches (`SRC-0003`)
*   **The Issue:** `BATT INSUL MW R-40 UNFACED` scored a false 1.000 against `Batt insulation, mineral wool, R-40`. Because the catalog entry was a perfect string subset of the source record, the token-set similarity scored 1.00. Since the engine didn't extract the "unfaced" attribute, both entries only had the `R-40` attribute, leading to a perfect score.
*   **The Solution:** We added a new attribute extractor `_FACING_RE` for `unfaced` and `faced`. This allowed the engine to extract `unfaced` from the source, dropping the attribute match to 50% when compared to the generic entry, and successfully surfacing the true `unfaced` catalog match.

### Case 4: Steel Dimensions Case Sensitivity (`SRC-0079`)
*   **The Issue:** `STL BM W460X60` falsely matched a `W150x22` catalog entry. The regexes for W-beams, channel steel, and rebar were written assuming uppercase letters (like `W360X57`). However, the text is completely normalized to lowercase (`w460x60`) *before* extraction. Lacking the `re.IGNORECASE` flag, they failed to match entirely, evaluating the `attribute_match` as `None` (missing data on both sides). The engine then relied entirely on string similarity, ignoring the completely different beam dimensions.
*   **The Solution:** We added the `re.IGNORECASE` flag to `_WBEAM_RE`, `_CHANNEL_RE`, and `_REBAR_SIZE_RE`. This ensured the lowercase dimensions were properly extracted and correctly tanked the score when compared against mismatched beam sizes.
