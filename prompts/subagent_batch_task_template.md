Use this template when assigning a worker to classify batch files.

---

You are not alone in the codebase. Do not revert others' edits. Only edit assigned files.

Task:
- Classify these source files:
  - `data/batches/batch_XXX.json`
  - `data/batches/batch_YYY.json`
- Write outputs with same filenames under `data/extracted/`.

Prompt to follow:
- `prompts/listing_classification_prompt.md`

Output contract:
- Preserve row order and row count exactly.
- Preserve `index` and `url` exactly.
- Use only these keys:
  - `index`, `url`, `price`, `neighborhood`, `commute_minutes`,
    `move_in`, `move_out`, `duration_months`, `is_offering`,
    `has_laundry`, `gender_restriction`, `is_furnished`, `poster_gender`
- `commute_minutes` must be `null` for all rows.
- Unknown fields must be `null` (except constrained enums with default values in prompt).

Validation before completion:
- JSON parses.
- Keys match schema exactly for each row.
- Row count matches source.
- `commute_minutes` is `null` for every row.

Return:
- List of changed file paths.

