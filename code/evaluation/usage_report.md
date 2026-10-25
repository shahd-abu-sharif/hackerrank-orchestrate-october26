# Usage Report

## Final full-dataset run

The final local Python run processed all 518 requests and wrote the root-level `output.csv` (128 `COMPLETE`, 380 `INCOMPLETE`, 10 `NOT_APPLICABLE`). It uses deterministic parsing and policy logic; no external model provider or inference model was used. The run command was `python3 code/main.py`.

| Metric | Result |
| --- | --- |
| Model provider/model | None |
| Model calls | 0 |
| Input tokens | Not applicable; no model calls or tokenizer measurements |
| Output tokens | Not applicable; no model calls or tokenizer measurements |
| Total tokens | Not applicable |
| Average tokens per request | Not applicable |
| Estimated model-inference cost | $0.00; no provider calls were made |
| Per-request model-inference cost | $0.00; no provider calls were made |

Token usage is not measured because the solution made no model calls. The cost figures above refer only to model inference; local compute, development time, and other environment costs were not measured.
