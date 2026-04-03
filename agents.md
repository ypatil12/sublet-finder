# Project Overview

This repository ingests raw Facebook housing exports, classifies listings into a normalized schema, enriches them with commute data to `36 Cooper Square`, and stores the final result in DuckDB for filtering and analysis.

The current workflow is:

1. Raw export lives in `data/large-4-02.json`.
2. `ingest.py --split-only` creates `data/batches/batch_*.json`.
3. Prompt-driven batch classification produces `data/extracted/batch_*.json`.
4. `ingest.py` loads extracted rows into `output/sublets.duckdb`.
5. `query.py` and custom DuckDB SQL are used to rank and inspect listings.

# Core Files

`ingest.py`
- Splits raw exports into batches.
- Rebuilds DuckDB from extracted batch outputs.
- Creates both `posts` and `neighborhood_commutes`.
- Backfills `posts.commute_minutes` from the commute lookup table.

`neighborhood_lookup.py`
- Builds the `neighborhood_commutes` lookup table.
- Uses the official NYC NTA CSV in `data/nyc_nta_2020.csv`.
- Seeds known commute values and falls back to centroid-based modeled transit minutes.

`query.py`
- Main CLI for querying `output/sublets.duckdb`.
- Supports canned filters, dedupe mode, stats mode, and raw SQL execution.

# Prompt Artifacts

`prompts/listing_classification_prompt.md`
- Canonical prompt contract for LLM classification.
- Defines output schema, date parsing rules, gender rules, and null-handling.

`prompts/subagent_batch_task_template.md`
- Template for assigning batch-classification work to subagents.
- Enforces output shape, validation, and file ownership boundaries.

# Data Layout

`data/large-4-02.json`
- Main raw export used for the current database build.

`data/batches/`
- Source batches used for classification.
- Each file contains a small slice of raw posts with `index`, `text`, `url`, and metadata.

`data/extracted/`
- Normalized per-batch classification outputs used by `ingest.py`.
- This is the immediate input to the final DuckDB load.

`data/nyc_nta_2020.csv`
- Official NYC neighborhood dataset cached locally for commute lookup generation.

# Output Layout

`output/sublets.duckdb`
- Primary source of truth for final querying.
- `posts` holds listing rows.
- `neighborhood_commutes` holds commute lookup rows.

# UI And Tests

`ui/`
- Lightweight frontend assets for browsing results.

# Current Caveats

- The repository now treats the prompt-driven LLM/subagent classification flow as the only supported extraction path.
- Classification logic lives in prompt files and operator workflow rather than a single local Python runner.
- Final filtering should be done against `output/sublets.duckdb`, not directly against `data/extracted`.
