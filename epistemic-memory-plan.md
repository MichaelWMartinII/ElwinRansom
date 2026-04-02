# Epistemic Control Integration Plan

## The Problem

Every fact ever extracted gets injected into every prompt with equal weight, forever. No decay, no confidence, no contradiction resolution. The system gets more wrong over time, not more right.

The root issue (from `ai-memory-epistemic-control.md`): memory without epistemic discipline produces hallucinations that compound and beliefs that lock in. The real bottleneck is not storage — it's knowing what to remember, what to discard, and when the system is wrong.

---

## Phase 1 — Schema (db.py)

Add four columns to the `facts` table:

```sql
confidence    REAL DEFAULT 0.7      -- 0.0 to 1.0, set at extraction
access_count  INTEGER DEFAULT 0     -- incremented on retrieval
last_accessed TEXT                  -- ISO8601, updated on retrieval
contradicts   TEXT REFERENCES facts(id)  -- explicit conflict pointer
```

Add one column to `embeddings` (message-level):

```sql
access_count  INTEGER DEFAULT 0
```

One new table:

```sql
CREATE TABLE fact_contradictions (
    id          TEXT PRIMARY KEY,
    fact_a      TEXT REFERENCES facts(id),
    fact_b      TEXT REFERENCES facts(id),
    detected_at TEXT
)
```

Migration is additive — no existing data touched.

---

## Phase 2 — Confidence at Extraction (extractor.py)

Modify `_EXTRACT_PROMPT` to ask the LLM to include a confidence score (0.0–1.0) per fact. Low hedging language from the user ("I think", "maybe") → lower score. Direct statements → higher. Pass that through to `db.save_fact()`.

---

## Phase 3 — Decay in Retrieval (embeddings.py)

In `search_similar()`, apply a decay multiplier before ranking:

```python
import math

age_days = (now - created_at).days
decay = math.exp(-0.01 * age_days)         # half-life ~70 days
frequency_boost = 1 + (0.1 * access_count) # retrieval reinforces salience
adjusted_score = cosine_score * decay * frequency_boost
```

Also add a minimum threshold — don't return results below `0.3` cosine similarity regardless of top-k.

Increment `access_count` and update `last_accessed` on every retrieval.

---

## Phase 4 — Filter Facts in Context (db.py + prompts.py)

Change `get_active_facts()` to accept a confidence floor and sort by confidence descending:

```sql
SELECT * FROM facts
WHERE superseded_by IS NULL
  AND confidence >= 0.4
ORDER BY confidence DESC, last_accessed DESC
```

In `build_system_prompt()`, annotate facts when confidence is borderline:

```
Things I remember:
- Michael: prefers dark mode
- Michael: allergic to shellfish [uncertain]   ← confidence < 0.6
```

This surfaces epistemic state to the model itself, which helps it hedge appropriately.

---

## Phase 5 — Contradiction Detection (extractor.py)

After extracting new facts, run a second pass: embed the new fact, find the top-3 semantically similar existing facts, and if cosine similarity is above `0.85` but the content conflicts (another quick LLM call at temperature 0), write to `fact_contradictions` and lower confidence on both facts until resolved.

Resolution:
- Manual: user corrects via chat
- Automatic: newer fact wins, older fact's confidence drops to 0.2 and gets filtered out

---

## Phase 6 — Periodic Pruning (pruner.py + launchd)

A lightweight cron job fitting the existing launchd setup:

- Facts with `confidence < 0.2` and `access_count == 0` older than 30 days → delete
- Facts with `access_count > 10` → confidence floor bumped to 0.8 (retrieval frequency is a signal of truth)
- Orphaned embeddings (source message deleted) → vacuum

---

## Execution Order

| Step | File(s) | Effort |
|---|---|---|
| 1. Schema migration | `companion/db.py` | Small |
| 2. Confidence at extraction | `companion/extractor.py`, `companion/db.py` | Small |
| 3. Decay in retrieval | `companion/embeddings.py` | Small |
| 4. Filter + annotate facts | `companion/db.py`, `companion/prompts.py` | Small |
| 5. Contradiction detection | `companion/extractor.py` | Medium |
| 6. Pruning job | new `companion/pruner.py` + launchd plist | Small |

Steps 1–4 are a coherent unit that delivers most of the value. Step 5 is the hard interesting part. Step 6 is the maintenance layer that makes it sustainable.

---

## Key Files

| Purpose | File | Key Functions |
|---|---|---|
| Database schema & CRUD | `companion/db.py` | `save_fact()`, `get_active_facts()`, `save_embedding()` |
| Embedding search | `companion/embeddings.py` | `search_similar()`, `embed_text()` |
| Fact extraction | `companion/extractor.py` | `extract_async()`, `_run_extraction()` |
| Context assembly | `companion/pipeline.py` | `prepare_context()`, `save_response()` |
| System prompt building | `companion/prompts.py` | `build_system_prompt()` |
| Config & budgets | `companion/config.py` | `TOP_K_MEMORIES`, `MEMORY_TOKEN_BUDGET` |
