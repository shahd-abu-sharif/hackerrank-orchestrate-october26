# HackerRank Orchestrate — October 2026

## Ghost Data Still Exists

A Python-based solution to the HackerRank Orchestrate October 2026 challenge, focused on auditing whether a user's personal data was actually deleted across multiple systems.

A deletion workflow may report completion while personal data remains in databases, logs, support attachments, exports, search indexes, archives, backups, or connected data flows. Historical identity changes, replication drift, and restoration processes can also affect the result.

This project analyzes deletion requests and available evidence to determine the appropriate status, identify remaining data or recreation risks, and produce ordered remediation and verification steps.

## Solution Architecture

The solution follows an evidence-driven pipeline:

1. **Request classification** — Determine whether the request actually asks for data deletion.
2. **Identity resolution** — Use available identity records and historical changes to identify the relevant user.
3. **Evidence extraction** — Inspect supported evidence formats, system activity, audit records, and data snapshots.
4. **Policy evaluation** — Apply the challenge's deletion rules, retention requirements, and relevant exceptions.
5. **State assessment** — Evaluate evidence available at the request's `audit_at` cutoff instead of relying solely on reported workflow status.
6. **Remediation planning** — Identify remaining data and propose required actions when deletion is incomplete.
7. **Verification planning** — Produce verification steps in the required order.
8. **Output validation** — Check the generated CSV structure, identifiers, step ordering, and evidence references.

The implementation distinguishes evidence of a current state from a claim that an operation succeeded. Proposed actions are not treated as completed actions.

## Output Format

The solution generates a root-level `output.csv` with the following columns:

```csv
case_id,deletion_status,steps_json
```

Each row corresponds to one deletion request.

Supported deletion statuses:

- `COMPLETE`
- `INCOMPLETE`
- `NOT_APPLICABLE`

The `steps_json` column contains an ordered JSON array. Each step includes:

- `step_no`
- `phase`
- `system_id`
- `resource_id`
- `operation`
- `state`
- `evidence_id`

Steps follow the required phase order:

1. `FINDING`
2. `REMEDIATION`
3. `VERIFICATION`

Within each phase, steps follow the operation order defined by the challenge specification, followed by ascending `system_id` and `resource_id`.

Non-deletion requests use `NOT_APPLICABLE` with an empty steps array.

## Project Structure

```text
.
├── README.md
├── output.csv
├── log.txt
└── code/
    ├── main.py
    └── evaluation/
        ├── main.py
        ├── evaluation_report.md
        └── usage_report.md
```

The challenge dataset is intentionally excluded from the packaged solution. The code expects the authorized dataset files to be available in the repository's `dataset/` directory when running the full pipeline.

## Requirements

- Python 3.10 or later
- The challenge dataset in the expected directory structure
- `pdftotext` for optional PDF evidence extraction
- Tesseract OCR for screenshot text extraction, if available

If screenshot OCR is unavailable, the implementation reports the limitation rather than claiming those images were analyzed.

## Run the Solution

From the repository root, compile the Python files:

```bash
python3 -m py_compile code/main.py code/evaluation/main.py
```

Generate the predictions:

```bash
python3 code/main.py
```

Run evaluation and output checks:

```bash
python3 code/evaluation/main.py
```

The evaluator compares the generated predictions with the available public sample and checks the output contract. Public sample mismatches are reported for review; sample answers are not hardcoded into the production solution.

## Evaluation Results

The latest recorded public-sample evaluation reported:

| Metric | Result |
|---|---:|
| Status agreement | 29/32 (90.6%) |
| Exact sequence agreement | 6/32 (18.8%) |
| Step accuracy | 51.8% |
| Field accuracy | 66.8% |

The latest run generated 518 prediction rows:

| Status | Count |
|---|---:|
| `COMPLETE` | 128 |
| `INCOMPLETE` | 380 |
| `NOT_APPLICABLE` | 10 |

The independent output-contract checks reported zero issues in the recorded run, and the evidence audit checked 942 evidence citations without reporting support issues.

**These are local evaluation results, not the official HackerRank score.** Hidden-test accuracy and the official score are unknown.

## Known Limitations

- Three public sample cases remained unresolved: `case_01`, `case_02`, and `case_04`. The available evidence did not establish the expected resource-to-user relationships.
- Eleven screenshots were not parsed because an OCR engine was unavailable in the execution environment.
- Exact sequence agreement remains an area for improvement.
- Public sample metrics do not establish performance on hidden cases.

Detailed evaluation results, regression tests, failures, and improvements are documented in `code/evaluation/evaluation_report.md`.

Usage and model-token information are documented in `code/evaluation/usage_report.md`. No model calls were recorded for the latest run.

## Reproducibility and Submission Artifacts

The project includes:

- `code/main.py` — Prediction generation
- `code/evaluation/main.py` — Evaluation and validation
- `code/evaluation/evaluation_report.md` — Evaluation results and known limitations
- `code/evaluation/usage_report.md` — Usage and cost reporting
- `output.csv` — Generated predictions
- `log.txt` — Agent interaction transcript

The packaged `code.zip` is intended to contain the runnable solution, README, and required evaluation reports without the dataset, virtual environments, build caches, or nested archives.

## Design Principles

- Evidence-backed decisions
- Historical identity awareness
- Audit-time cutoff enforcement
- Policy-aware deletion and retention
- Explicit separation of completed and proposed actions
- Ordered remediation and verification
- Independent output validation
- Transparent reporting of limitations

## Disclaimer

This repository documents an independent solution to a programming challenge. Reported metrics describe the available public sample and local validation only. They do not represent an official HackerRank score or guarantee hidden-test correctness.