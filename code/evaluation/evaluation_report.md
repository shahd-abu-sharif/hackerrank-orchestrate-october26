# Evaluation Report

## Scope and method

The solution was run locally against participant-facing files only. The public comparison uses the 32 rows in `dataset/sample_deletion_requests.csv` and `dataset/sample_output.csv`. It is a diagnostic reference, not a source of labels for scored requests. No organizer-only truth or hidden answer set was used.

## Full-dataset output validation

The final run generated 518 rows, sorted by case ID:

| Status | Rows |
| --- | ---: |
| `COMPLETE` | 128 |
| `INCOMPLETE` | 380 |
| `NOT_APPLICABLE` | 10 |

The independent output validator reported **0 structural, ordering, citation-reference, and cutoff issues**. A case-aware evidence audit checked **942 cited steps** for a cutoff-eligible observation of the cited resource linked to that subject; it found **0 issues**. These checks validate the output contract and citations, not the truth of the hidden answers. Hidden-label status, step, field, and sequence accuracy are unavailable and are not claimed.

## Public sample results

| Metric | Result |
| --- | ---: |
| Status agreement | 29/32 (90.6%) |
| Exact sequence agreement | 6/32 (18.8%) |
| Step accuracy at matching array positions | 51.8% |
| Field accuracy at matching array positions | 66.8% |
| Unresolved cases | 3/32 |

Unresolved cases count as nonmatches in status agreement. Step accuracy counts exact step objects at the same array position divided by the larger of expected and predicted step counts. Field accuracy compares the seven required fields at matching positions and counts unmatched positions as incorrect.

Compared with the immediate pre-audit run (29/32 status agreement, 5/32 exact sequences, 48.7% step accuracy, 65.8% field accuracy, and three unresolved cases), status agreement and unresolved count are unchanged. Exact sequence agreement increased by one case; step accuracy increased by 3.1 percentage points and field accuracy by 1.0 point. The three remaining status-unresolved cases are below. `case_12` has the expected status, although its expected operation conflicts with the supplied deletion rule.

## Verified defects fixed in this audit

- Remediation ordering previously inserted a system dependency order before the contract's within-phase operation/system/resource ordering. The comparator now follows the documented operation order, then `system_id`, then `resource_id`.
- `DATA_FLOW` resources had no fallback system mapping, risking blank `system_id` values when evidence did not include an explicit system context. They now map to `integration_service`, consistent with connector evidence in the participant dataset.
- State inference previously scanned entire free-text lines for words such as “active”, “found”, “blocked”, and “deleted”. Incidental prose could therefore be treated as a structured state. State inference now relies on explicit structured fields, status codes, and row counts; an added regression confirms prose alone produces no state.
- Identity aliases were previously aggregated without regard to `audit_at`, allowing a later identity change or snapshot to resolve an earlier case. Identity snapshots and audit events are now included only when their timestamps are at or before the case cutoff.
- The evaluation previously reused the solution's ordering validator. It now independently checks required columns, exact case rows, step fields, operation/state pairs, nonempty system/resource IDs, status/phase constraints, evidence references and timestamps, and required step ordering.

The final validation command passed 16 intent/regression checks, a historical-email resolution check, a future-evidence cutoff check, 518 independent row/contract checks, and 942 case-aware evidence citations. No hidden-label accuracy is claimed.

### Investigation of the five status discrepancies

| Case | Request identity / audit cutoff | Expected | Current result | Evidence and policy conclusion |
| --- | --- | --- | --- | --- |
| `case_01` | `user_01`; 2026-09-03 11:30Z | `COMPLETE` | Unresolved | The sample cites `evidence_54` for `account_001`, but the failover SQL contains no such resource or subject link. The capture is before the cutoff, but it does not provide an observation that proves this specific account absent. No verdict is fabricated. |
| `case_02` | `user_02`; 2026-09-04 15:00Z | `COMPLETE` | Unresolved | The Spanish text authorizes deletion and the email resolves to `user_02`. The cited `evidence_54` does not contain `account_002` or a link to this user. Its pre-cutoff time alone does not prove the expected resource absent. |
| `case_04` | `user_04`; 2026-09-07 10:20Z | `COMPLETE` | Unresolved | The cited `account_004` is absent from `evidence_54` as a named observation. `evidence_56` is timely and contains `analytics_901`, but all user identifiers are null and no supplied evidence links it to `user_04`. The analytics rule applies only to the subject’s attributable record, so the sample’s verification is not grounded. |
| `case_10` | Historical email `nadia.ionescu@yahoo.com` resolves to `user_10`; 2026-09-18 12:25Z | `INCOMPLETE` | `INCOMPLETE` | Identity history confirms the old email. In `evidence_61`, `support_002` returned `rows=1` at 22:00:05Z and the deletion-marker check reported `marker_found=false` at 22:00:06Z, both before cutoff. The evidence exposes no shared conversation context; the conditional support rule therefore calls for deleting this subject-only record. Output reports it, proposes deletion, and verifies its absence. The sample additionally plans verification of `account_010`, but no participant evidence identifies that resource for `user_10`. |
| `case_12` | `user_12`; 2026-09-15 12:00Z | `INCOMPLETE` | `INCOMPLETE` | `evidence_01` reports `rows=1` for `analytics_9914` at 11:58:26Z. Its `correlation_key` matches the prior email hash in `audit_283`, so the analytics row remains attributable at cutoff. Rule 07 requires anonymization of attributable analytics data, not deletion of the whole resource. The corrected output reports exposed linkage and plans `ANONYMIZE_RECORD`; the sample’s `DELETE_RECORD` expectation conflicts with `dataset/deletion_rules.csv`. |

The current unresolved samples are `case_01`, `case_02`, and `case_04`. Their sample answer citations refer to resources not linked to the subjects by the cited evidence. Treating the absence of a resource from a partial set of observations as a positive lookup result would overstate the available proof.

## Exact-sequence differences

The previous run's five exact matches increased to six after correcting within-phase ordering. Sequence differences are not solely a serializer problem:

- A citation audit of the 75 evidence-backed steps in the public sample answers found **19 relevance/support issues**. Sixteen cite a resource not linked to that case in the evidence, or cite a resource for which the referenced file contains no observation (including several `account_00x` / `account_4xxx` rows and unlinked records in the September resource export). Three are operation/policy-support mismatches: support-message redaction in cases 11 and 18 is not established by the cited PII-scan evidence alone, and case 12’s expected full deletion conflicts with the analytics anonymization rule.
- Some sample `INCOMPLETE` plans include future verification resources not established in participant evidence. Future actions correctly use empty evidence IDs, but the resource still needs a grounded identity link. The solution does not infer resource IDs from case numbering.
- The solution can find relevant records omitted by a sample answer. For example, case 07’s `activity_4007` explicitly links `object_19` to `user_07` and says the attachment belongs only to that subject. Deleting it is supported by the attachment rule, even though that record is absent from the sample’s expected sequence.
- Conversely, resources grouped in a global export but carrying no subject identifier are not assigned to a case merely because the sample answer does so. For example, several account and analytics rows in `evidence_92` have no user reference or direct identifier; other records in the same export do explicitly reference `user_26`, `user_27`, or `user_28`.
- Retained audit records, backups, and inactive flows are included when a cutoff-eligible observation links them to the user. Older observations can result in additional planned verification steps when the case is incomplete; these future checks use empty evidence IDs.

Exact sequence agreement remains a public-sample diagnostic, not a claim that the sample answers are fully evidence-consistent or that hidden-case predictions are accurate.

## Team-created tests and additional checks

All 16 intent and focused regression cases passed. The separate historical-email resolution and future-evidence cutoff checks also passed:

| Case | Expected behavior | Result |
| --- | --- | --- |
| Explicit English deletion | Recognize an authorized deletion request | Pass |
| Access/copy request | Classify as non-deletion | Pass |
| Marketing-only opt-out | Classify as non-deletion | Pass |
| Profile correction | Classify as non-deletion | Pass |
| Portuguese deletion wording | Recognize deletion intent | Pass |
| Negated deletion request | Respect the negation | Pass |
| Backup with `restore_allowed=false` | Treat restore access as blocked | Pass |
| Subject-only support message | Delete when shared-context condition is absent | Pass |
| Support message with shared order context | Redact rather than delete | Pass |
| Retained support conversation/customer context | Verify retained, redacted record | Pass |
| Analytics row linked by a historical hash | Do not treat it as anonymized | Pass |
| Tombstone identity-link catalog metadata | Do not treat link metadata alone as proof of PII | Pass |
| Single-subject PDF with identity and resource on separate lines | Link the document resource to its subject | Pass |
| Incidental prose mentioning a state word | Do not infer a structured record state | Pass |
| Data-flow resource without explicit source context | Use the integration-service system mapping | Pass |
| Independent validator with blank identifier and missing phases | Reject invalid contract output | Pass |
| Historical-only request email | Resolve through identity audit history | Pass |
| Evidence captured after `audit_at` | Exclude from the case audit | Pass |

These are participant-created synthetic regression cases with asserted expected behavior. They test individual decision rules and do not estimate hidden-label accuracy.

## Image evidence and remaining limitations

The evidence manifest lists 11 PNGs (`evidence_04`, `09`, `14`, `19`, `23`, `28`, `32`, `37`, `42`, `46`, and `60`). The local environment has `pdftoppm` but no OCR executable (`tesseract`, `gocr`, `cuneiform`, `ocrmypdf`, or `ocrad`) and no installed Python OCR package (`pytesseract`, `easyocr`, `paddleocr`, `keras_ocr`, `rapidocr_onnxruntime`, `doctr`, or `mmocr`). No packages or system tools were installed, and no external service was used. Rasterization is available, but OCR extraction is not; image contents remain unparsed and may contain relevant facts.

No hidden canonical answers are available in the participant-facing repository, so accuracy over the 518 scored requests cannot be measured. The implementation is a deterministic parser and rules engine, not a model-backed agent. Ambiguous evidence, especially conditional support-message context and unlinked resource rows, remains a limitation.
