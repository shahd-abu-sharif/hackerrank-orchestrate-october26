# Ghost data analysis overview
_Exported on 10/25/2026 at 11:04:04 GMT+2 from Cursor (3.24.12)_

---

**User**

We are participating in the HackerRank Orchestrate challenge "Ghost Data Still Exists".

Read `AGENTS.md`, `agent_trace_instructions.md`, `problem_statement.md`, and `README.md` first.

For now, do not modify any files and do not write the solution code.

Analyze the dataset and explain:
1. The deletion rules and their conditional requirements.
2. All event types in `dataset/system_activity.csv`, including counts and source-to-target system relationships.
3. The evidence file formats and what each evidence source may establish.
4. How the 32 sample cases and `dataset/sample_output.csv` can help validate our understanding.
5. Potential pitfalls involving historical identifiers, audit timestamps, retained records, tombstones, active data flows, and backup restoration.

Use only evidence found in the repository. Clearly distinguish verified facts from hypotheses. Do not trust `reported_status` as proof of deletion. Return a concise, structured analysis with file paths and concrete examples. Do not commit, push, or change any files.

---

**Cursor**

Welcome to HackerRank Orchestrate. Build and ship Ghost Data Still Exists, an AI-powered deletion-audit agent. Let's get started.

I'll read the required docs and inspect participant-facing dataset files only, then log this session without changing solution code.

I'll inspect the dataset CSVs and evidence layout next, then write the required session log only.

The October challenge end time is **not configured** in this repository.

This is a read-only dataset analysis. No solution code was written. The only file created was root `log.txt`, required by `AGENTS.md`.

**Sources used:** `dataset/deletion_rules.csv`, `dataset/system_activity.csv` (4,011 rows), `dataset/evidence_files.csv` (94 files), sample CSVs, `dataset/user_identities.csv`, `dataset/user_audit_log.csv`, and representative files under `dataset/evidence/`. Organizer-only files were not used.

---

## Verified contract (not hypotheses)

- Evaluate **518** rows in `dataset/deletion_requests.csv` (`case_33`–`case_550`). Samples `case_01`–`case_32` are public goldens, not the scored set.
- `reported_status` and `reported_result` are **claims**. State is proven at `audit_at` from evidence.
- `deletion_status` is `COMPLETE` | `INCOMPLETE` | `NOT_APPLICABLE`. Non-deletion → `NOT_APPLICABLE` with `steps_json=[]`.
- Findings cite `file_id` / `activity_id` / `audit_id`. Incomplete remediation and future verification use `evidence_id=""`.
- Phase order: FINDING → REMEDIATION → VERIFICATION. Within a phase: operation table order, then `system_id`, then `resource_id`.

---

## 1. Deletion rules (`dataset/deletion_rules.csv`)

15 rules. Action is **not** “always delete.” Match **data_category + condition**, not `system_id` alone.

| Rule | Category | Condition | Action |
|---|---|---|---|
| rule_01 | `USER_PROFILE` | Always | DELETE |
| rule_02 | `SECONDARY_IDENTIFIER` | Always | DELETE |
| rule_03 | `SEARCH_INDEX_ENTRY` | Always | DELETE |
| rule_04 | `CACHE_ENTRY` | Always | DELETE |
| rule_05 | `SUPPORT_MESSAGE` | Shared customer/agent/order context must be retained | REDACT |
| rule_06 | `SUPPORT_ATTACHMENT` | Attachment belongs **only** to the subject | DELETE |
| rule_07 | `ANALYTICS_PROFILE` | Still attributable to a user | ANONYMIZE |
| rule_08 | `PRODUCT_TELEMETRY` | Still attributable to a user | ANONYMIZE |
| rule_09 | `USER_EXPORT` | Always | DELETE |
| rule_10 | `MARKETING_PROFILE` | Always | DELETE |
| rule_11 | `EMAIL_DELIVERY_LOG` | Log contains a user email | REDACT |
| rule_12 | `SECURITY_AUDIT_LOG` | Within supplied retention period | RETAIN |
| rule_13 | `LEGAL_HOLD_RECORD` | A supplied legal hold is active | RETAIN |
| rule_14 | `BACKUP_ARCHIVE` | Restore access can be blocked | RETAIN (do not destroy; block restore) |
| rule_15 | `ACCOUNT_TOMBSTONE` | Account deleted; only stable `user_id` needed | REDACT (keep tombstone, strip direct IDs) |

**Grounded in samples**

- Support **attachment** vs **message**: `case_07` deletes `support_001`; `case_19` redacts `support_9553`. Handover `dataset/evidence/records/customer_account_handover_20261001.txt` labels `support_4218` as `SUPPORT_ATTACHMENT | FOUND` vs `support_864` as `SUPPORT_MESSAGE | IDENTIFIABLE`.
- Analytics that still have email/user_id are `REPORT_EXPOSED_PII` + `ANONYMIZE_RECORD` (`case_11`, `case_22`). Null identifiers are treated as already anonymized (`VERIFY_RECORD_ANONYMIZED` on COMPLETE cases).
- Backups are retained; incomplete cases `BLOCK_RESTORE` (`case_09`, `case_14`, `case_17`).
- Tombstones live on `identity_service` (`tombstone_440` redacted in `case_31`; `tombstone_9178` still has name/email/phone in `evidence_51`).

**Hypothesis (do not code as a hard map):** `case_12` deletes `analytics_9914` instead of anonymizing it. The log line in `evidence_01` is `contact_lookup_completed` with `email_opt_in=true` keyed by a **historical email hash**. That record may be a marketing/contact copy, not `ANALYTICS_PROFILE`. Use the evidence category, not “analytics_platform ⇒ anonymize.”

Conditional rules that are **undersampled** in the 32 goldens: `CACHE_ENTRY`, `SECONDARY_IDENTIFIER`, `PRODUCT_TELEMETRY`, `EMAIL_DELIVERY_LOG`, `LEGAL_HOLD_RECORD`. Evidence for some of these exists (`REFRESH_CACHE` activities, `evidence_41` notification log, `evidence_27` legal PDF, `evidence_60` marketing screenshot).

---

## 2. `system_activity.csv` event types

4,011 unique `activity_id`s. Columns: `activity_id,event_time,event_type,source_system,target_system,resource_id,reported_result,details`.

`reported_result`: SUCCESS 2914, FAILED 721, PARTIAL 376. **DELETE_RECORD is often FAILED** (368 failed / 302 success / 220 partial). Failure is not proof the record remains.

| Event | Count | Dominant source → target |
|---|---:|---|
| `DELETE_RECORD` | 890 | `deletion_worker` → `account_service` (539), `search_service` (289), `export_service` (32), `support_platform` (29), `analytics_platform` (1) |
| `UPDATE_RECORD` | 672 | `profile_service` → `account_service` (440); `support_platform` → `search_service` (232) |
| `SYNC_RECORD` | 447 | `account_service` → `analytics_platform` (360), `account_replication_cluster` (55); `integration_service` → `search_service` (31); 1 to `account_failover_replica` |
| `INDEX_RECORD` | 400 | `account_service` → `search_service` |
| `SEND_EMAIL` | 368 | `notification_service` → `email_delivery_service` |
| `REFRESH_CACHE` | 284 | `account_service` → `cache_service` |
| `RETAIN_RECORD` | 206 | `security_service` → `archive_service` (196); `deletion_worker` → `legal_service` (10) |
| `ANONYMIZE_RECORD` | 204 | `deletion_worker` → `analytics_platform` |
| `REDACT_RECORD` | 173 | `deletion_worker` → `support_platform` (163), `identity_service` (10) |
| `CREATE_EXPORT` | 155 | `export_service` → `object_store` |
| `CHECK_DATA_FLOW` | 89 | `deletion_worker` → `integration_service` (all SUCCESS; details say flow **remains enabled**) |
| `DISABLE_DATA_FLOW` | 36 | `deletion_worker` → `integration_service` (all SUCCESS) |
| `BLOCK_RESTORE` | 36 | `deletion_worker` → `backup_service` (all SUCCESS) |
| `CHECK_RESTORE_ACCESS` | 28 | `deletion_worker` → `backup_service` (details: archive **can still be restored**) |
| `LINK_RESOURCE_IDENTITY` | 12 | `data_catalog` → many systems (identity join keys) |
| `CLASSIFY_REQUEST` | 10 | `privacy_intake` → `privacy_case_management` (claims “non-deletion”) |
| `RESTORE_BACKUP` | 1 | `backup_service` → `search_service`: `activity_3387` “repopulated downstream resource `search_4882`” from `backup_9287` |

Example of a catalog join that is evidence-like, not a deletion proof: `activity_4007` links `object_19` **only** to `priya.raman@gmail.com` / `user_07` (supports rule_06 for `case_07`).

`CLASSIFY_REQUEST` is a workflow claim. The scored file still contains explicit non-deletion texts (e.g. `case_536` login help, `case_538` marketing opt-out only, `case_540`/`case_542` access/copy). Classify from `request_text`, not this event.

---

## 3. Evidence formats and what they can establish

`dataset/evidence_files.csv`: **94 files**, all present on disk. `captured_at` 2026-01-29 → 2026-10-01. Manifest does **not** name the case; join via identifiers.

| Location | Count / types | Origins (examples) | What a capture can establish |
|---|---|---|---|
| `evidence/records/` | 29: JSON + 1 TXT | snapshot bundles, service exports, connectors, analytics, export jobs, security ledger, handover | Point-in-time records: FOUND/NOT_FOUND, IDENTIFIABLE vs ANONYMIZED/REDACTED, `enabled`/`restore_allowed`, tombstone PII |
| `evidence/logs/` | 26 `.log` | observability, search diagnostics, support replica, notification delivery, resource-lookup traces | Lookups: `http_status=404`, `result=hit`, `enabled=true`, `restore_allowed`, PII scans, replica lag |
| `evidence/databases/` | 24 `.sql` (SQLite text) | inventory, read replica, regional/reporting/failover replicas, search primary, accounts failover | Table-level state **and replica drift**. Header states topology (`read_replica`, compare primary vs replica) |
| `evidence/images/` | 11 PNG | admin/search/connector/analytics/backup/support/marketing/identity consoles | UI facts (archive IDs, restore history, retained `user_17` reference) |
| `evidence/documents/` | 4 PDF | account history, legal retention register, security extract, support attachment | Business artifacts, not a deletion verdict |

**Concrete examples**

- Replica drift: `dataset/evidence/databases/customer_data_read_replica_20260921.sql` — `account_994` (Diya Kapoor) is in `customer_data_read_replica` and **absent** from `customer_data_primary`. That is why `case_16` reports remaining `account_994` + `search_1007`.
- Handover snapshot: `customer_account_handover_20261001.txt` encodes category + state on one line (`BACKUP_ARCHIVE | ACTIVE`, `DATA_FLOW | ACTIVE`, `ANALYTICS_PROFILE | IDENTIFIABLE`, `SECURITY_AUDIT_LOG | FOUND`).
- Tombstones: `dataset/evidence/records/customer_data_snapshot_bundle_20260915.json` (`evidence_51`) — `tombstone_440` identifiers are null; `tombstone_9178` still has name/email/phone with `account_status: DELETED`.
- Structured readback: `evidence_92` / `evidence_94` JSON/logs give `status_code: 404`, `enabled=true`, `restore_allowed=false`, analytics with leftover email.
- Screenshot `evidence_32`: backup console lists `backup_9287` for `areed1433@gmail.com` and completed restores — pairs with the single `RESTORE_BACKUP` activity.
- Screenshot `evidence_42`: identity registry keeps `user_17` as a retained reference linked to orders/support/finance.

**Hypothesis:** noisy log lines (`consumer_lag`, DNS cache) are decoys; only identifier-linked, in-time events should be cited.

---

## 4. How the 32 samples validate understanding

Files: `dataset/sample_deletion_requests.csv`, `dataset/sample_output.csv`.

Status mix: **10 COMPLETE, 22 INCOMPLETE, 0 NOT_APPLICABLE**. Intent classification is **not** covered by goldens.

`reported_status` is a poor predictor:

- `case_02` PARTIALLY_COMPLETE → **COMPLETE**
- `case_05` COMPLETE → **INCOMPLETE** (profile still in `account_service`)
- `case_25`/`case_26`/`case_28` NOT_STARTED → **COMPLETE**
- `case_09` NOT_STARTED → INCOMPLETE (restore still active)

**Patterns the goldens encode**

1. **COMPLETE = verification only**, with real evidence IDs (`evidence_54`, `evidence_92`, `evidence_94`, `evidence_51`, …).
2. **INCOMPLETE = findings (cited) + full remediation + verification of both remaining and already-compliant resources**, later steps `evidence_id=""`. Example: `case_06` finds only `search_001` but still verifies `account_006` absent.
3. **Retained audit logs** almost always get `VERIFY_RECORD_PRESENT` on `security_service` / `audit_record_*` (`case_03`, `case_09`, `case_31`).
4. **Operation/sort order** is visible in `case_11`: remaining search → exposed analytics → exposed support → active flow; remediate disable-flow **before** delete/redact/anonymize; verify absent (account, search) → present (audit) → redacted → anonymized → flow disabled.
5. Multilingual deletion still counts (`case_02` Spanish, `case_03` French, `case_14` Japanese).
6. Historical request emails: `case_10` `nadia.ionescu@yahoo.com` is **not** the current identity (`n.petrescu@yahoo.com` after two email changes in `user_audit_log.csv`). Support replica log still hits `support_002` on the old address; nearby `nadia.popescu@yahoo.com` is a decoy.
7. `case_28` text warns a similar search email belongs to someone else — do not over-match.

Use samples as a **format + policy oracle**, then hold out a few and re-derive steps from evidence captured at/before `audit_at`.

---

## 5. Pitfalls (fact vs hypothesis)

**Historical identifiers (verified)**  
Request email may be old (`case_10`, `case_29`, `case_30`). Audit log fields: `email` (107), `email_hash` (107), `phone` (92). `case_12` evidence matches **old hash** `44a65154bfd8e408…`, not the current email. Hashes, phones, and case-folded emails (`BCARVALHO6517@GMAIL.COM`) all appear in SQL/JSON.

**Audit timestamps (verified)**  
Ignore evidence with `captured_at` after `audit_at`. Example: `case_01` `audit_at=2026-09-03T11:30:00Z` cites `evidence_54` captured `2026-09-02T23:00:00Z`. Later handover `evidence_03` (`2026-10-01`) must not decide September cases. Activity `event_time` and SQL `updated_at` need the same cutoff.

**Retained records (verified)**  
COMPLETE is not “everything gone.” Security audits stay (`rule_12`). Deleting them would be a policy error. Legal holds (`rule_13`, PDF `evidence_27`) and backups (`rule_14`) are retain-with-controls.

**Tombstones (verified)**  
`identity_service` keeps `user_id` / `subject_reference`. Direct identifiers must be null (`case_31` vs `case_32`). Do not `DELETE_RECORD` a tombstone when the rule is REDACT.

**Active data flows (verified)**  
`CHECK_DATA_FLOW` SUCCESS means the connector is **still on**. Incomplete cases disable `integration_service` / `data_flow_*` **before** mutating downstream copies (`case_11`, `case_15`, `case_20`). `SYNC_RECORD` into analytics and replicas is the recreation path.

**Backup restoration (verified)**  
Retain archive + `BLOCK_RESTORE`. One restore already replayed search: `activity_3387` + screenshot `backup_9287`. `CHECK_RESTORE_ACCESS` SUCCESS means restore is still possible. `case_17` text allows keeping backups if they cannot restore the profile.

**Other traps**

- Replica-only ghosts (`account_994`).
- Decoy similar emails (`support_replica.log`).
- `LINK_RESOURCE_IDENTITY` helps join, it does not prove deletion.
- Samples never exercise `NOT_APPLICABLE`; the 518-file set does (`case_536`, `case_538`, `case_540`, `case_542`).
- Do not treat `deletion_worker` SUCCESS as state; cite the snapshot that still shows FOUND/IDENTIFIABLE/ACTIVE.

---

No commit, no push, no solution code. When you want implementation next, a reasonable first check is: reproduce `dataset/sample_output.csv` from participant evidence only, including the `reported_status` mismatches above.

