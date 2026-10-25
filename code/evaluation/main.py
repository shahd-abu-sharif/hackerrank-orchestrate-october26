from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

import main as solution


TEAM_CASES = (
    (
        "explicit English deletion",
        "Please permanently delete the account and all personal data associated with it.",
        True,
    ),
    (
        "access request",
        "Please send me a copy of the information held on my account.",
        False,
    ),
    (
        "marketing-only opt-out",
        "Remove me from marketing lists only; keep my account active.",
        False,
    ),
    (
        "profile correction",
        "Please correct the phone number on my profile and leave the account active.",
        False,
    ),
    (
        "Portuguese deletion",
        "Solicito a exclusão permanente da conta associada a este e-mail.",
        True,
    ),
    (
        "negated deletion",
        "This is not a request for deletion; please leave the account as it is.",
        False,
    ),
)

INDEPENDENT_STEP_RULES = {
    "FINDING": {
        "REPORT_REMAINING_RECORD": "PRESENT",
        "REPORT_EXPOSED_PII": "PRESENT",
        "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
        "REPORT_RESTORE_RISK": "ACTIVE",
    },
    "REMEDIATION": {
        "DISABLE_DATA_FLOW": "DISABLED",
        "BLOCK_RESTORE": "BLOCKED",
        "DELETE_RECORD": "ABSENT",
        "REDACT_RECORD": "REDACTED",
        "ANONYMIZE_RECORD": "ANONYMIZED",
    },
    "VERIFICATION": {
        "VERIFY_RECORD_ABSENT": "ABSENT",
        "VERIFY_RECORD_PRESENT": "PRESENT",
        "VERIFY_RECORD_REDACTED": "REDACTED",
        "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
        "VERIFY_RESTORE_BLOCKED": "BLOCKED",
    },
}


def independent_contract_issues(
    rows: list[dict[str, str]],
    requests: list[dict[str, str]],
    evidence_times: dict[str, datetime | None],
) -> list[str]:
    issues: list[str] = []
    request_by_id = {row["case_id"]: row for row in requests}
    expected_ids = sorted(request_by_id)
    actual_ids = [row.get("case_id", "") for row in rows]
    if len(rows) != len(expected_ids) or actual_ids != expected_ids:
        issues.append("Rows do not exactly match sorted request case IDs")
    fields = {
        "step_no",
        "phase",
        "system_id",
        "resource_id",
        "operation",
        "state",
        "evidence_id",
    }
    phase_rank = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
    for row in rows:
        case_id = row.get("case_id", "")
        status = row.get("deletion_status", "")
        request = request_by_id.get(case_id)
        try:
            steps = json.loads(row.get("steps_json", ""))
        except (TypeError, json.JSONDecodeError):
            issues.append(f"{case_id}: steps_json is not valid JSON")
            continue
        if not isinstance(steps, list):
            issues.append(f"{case_id}: steps_json is not an array")
            continue
        if status not in {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}:
            issues.append(f"{case_id}: unsupported deletion status")
        if status == "NOT_APPLICABLE" and steps:
            issues.append(f"{case_id}: NOT_APPLICABLE must have no steps")
        if request and not solution.is_deletion_request(request["request_text"]):
            if status != "NOT_APPLICABLE" or steps:
                issues.append(f"{case_id}: non-deletion request must be NOT_APPLICABLE with []")
        if status == "COMPLETE" and (
            not steps or any(not isinstance(step, dict) or step.get("phase") != "VERIFICATION" for step in steps)
        ):
            issues.append(f"{case_id}: COMPLETE requires verification-only steps")
        if status == "INCOMPLETE":
            phases = [step.get("phase") for step in steps if isinstance(step, dict)]
            if not {"FINDING", "REMEDIATION", "VERIFICATION"}.issubset(phases):
                issues.append(f"{case_id}: INCOMPLETE needs all three phases")
        phase_keys: list[tuple[int, int, str, str]] = []
        for number, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or set(step) != fields:
                issues.append(f"{case_id}: step {number} has an invalid field set")
                continue
            phase = step["phase"]
            operation = step["operation"]
            expected_state = INDEPENDENT_STEP_RULES.get(phase, {}).get(operation)
            if expected_state is None or step["state"] != expected_state:
                issues.append(f"{case_id}: step {number} has an invalid operation/state pair")
            if step["step_no"] != number:
                issues.append(f"{case_id}: step numbers are not consecutive")
            if not isinstance(step["system_id"], str) or not step["system_id"].strip():
                issues.append(f"{case_id}: step {number} has a blank system_id")
            if not isinstance(step["resource_id"], str) or not step["resource_id"].strip():
                issues.append(f"{case_id}: step {number} has a blank resource_id")
            rank = phase_rank.get(phase, 9)
            operation_rank = list(INDEPENDENT_STEP_RULES.get(phase, {})).index(operation) if expected_state else 99
            phase_keys.append((rank, operation_rank, step["system_id"], step["resource_id"]))
            evidence_id = step["evidence_id"]
            if not isinstance(evidence_id, str):
                issues.append(f"{case_id}: step {number} evidence_id is not a string")
            elif evidence_id:
                if evidence_id not in evidence_times:
                    issues.append(f"{case_id}: unknown evidence reference {evidence_id}")
                elif request:
                    cutoff = solution.parse_time(request.get("audit_at"))
                    observed = evidence_times[evidence_id]
                    if cutoff and observed and observed > cutoff:
                        issues.append(f"{case_id}: evidence {evidence_id} is after audit_at")
            if phase == "FINDING" and not evidence_id:
                issues.append(f"{case_id}: finding has no evidence reference")
            if status == "COMPLETE" and not evidence_id:
                issues.append(f"{case_id}: COMPLETE verification has no evidence reference")
            if status == "INCOMPLETE" and phase != "FINDING" and evidence_id:
                issues.append(f"{case_id}: future action cites evidence")
        if phase_keys != sorted(phase_keys):
            issues.append(f"{case_id}: steps violate phase/operation/system/resource ordering")
    return issues


def public_sample_metrics() -> dict[str, object]:
    aliases, users_by_email = solution.build_identity_index()
    observations, warnings = solution.load_evidence()
    solution.load_identity_link_events(observations)
    rules = {
        row["data_category"]: row
        for row in solution.read_csv(solution.DATASET / "deletion_rules.csv")
    }
    expected_rows = {
        row["case_id"]: row
        for row in solution.read_csv(solution.DATASET / "sample_output.csv")
    }
    statuses = Counter()
    exact_sequences = 0
    resolved = 0
    expected_step_count = 0
    actual_step_count = 0
    exact_steps = 0
    field_matches = 0
    field_comparisons = 0
    mismatches: list[dict[str, object]] = []

    for request in solution.read_csv(solution.DATASET / "sample_deletion_requests.csv"):
        expected = expected_rows[request["case_id"]]
        expected_steps = json.loads(expected["steps_json"])
        expected_step_count += len(expected_steps)
        try:
            actual_status, actual_steps = solution.audit_case(
                request, aliases, users_by_email, observations, rules
            )
        except (ValueError, KeyError) as error:
            statuses["unresolved"] += 1
            mismatches.append(
                {"case_id": request["case_id"], "error": str(error)}
            )
            field_comparisons += len(expected_steps) * len(solution.STEP_FIELDS)
            continue

        resolved += 1
        actual_step_count += len(actual_steps)
        status_matches = actual_status == expected["deletion_status"]
        statuses["status_match" if status_matches else "status_mismatch"] += 1
        sequence_matches = actual_status == expected["deletion_status"] and actual_steps == expected_steps
        exact_sequences += int(sequence_matches)

        compared_steps = max(len(expected_steps), len(actual_steps))
        field_comparisons += compared_steps * len(solution.STEP_FIELDS)
        for index in range(compared_steps):
            if index >= len(expected_steps) or index >= len(actual_steps):
                continue
            expected_step = expected_steps[index]
            actual_step = actual_steps[index]
            field_matches += sum(
                expected_step.get(field) == actual_step.get(field)
                for field in solution.STEP_FIELDS
            )
            exact_steps += int(expected_step == actual_step)
        if not status_matches or not sequence_matches:
            mismatches.append(
                {
                    "case_id": request["case_id"],
                    "expected_status": expected["deletion_status"],
                    "actual_status": actual_status,
                    "expected_steps": len(expected_steps),
                    "actual_steps": len(actual_steps),
                    "exact_sequence": sequence_matches,
                }
            )

    sample_total = len(expected_rows)
    return {
        "sample_cases": sample_total,
        "resolved_cases": resolved,
        "unresolved_cases": statuses["unresolved"],
        "status_correct": statuses["status_match"],
        "status_accuracy": statuses["status_match"] / sample_total,
        "exact_sequence_cases": exact_sequences,
        "sequence_accuracy": exact_sequences / sample_total,
        "expected_steps": expected_step_count,
        "predicted_steps": actual_step_count,
        "exact_steps_at_same_position": exact_steps,
        "step_accuracy": exact_steps / max(expected_step_count, actual_step_count, 1),
        "field_matches_at_same_position": field_matches,
        "field_comparisons": field_comparisons,
        "field_accuracy": field_matches / max(field_comparisons, 1),
        "mismatches": mismatches,
        "evidence_warnings": warnings,
    }


def regression_cases() -> list[tuple[str, bool]]:
    cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rules = {
        row["data_category"]: row
        for row in solution.read_csv(solution.DATASET / "deletion_rules.csv")
    }
    cases: list[tuple[str, bool]] = []

    backup = solution.Observation(
        evidence_id="team_backup",
        file_path="synthetic",
        captured_at=cutoff,
        observed_at=cutoff,
        system_id="backup_service",
        resource_id="backup_7",
        category="BACKUP_ARCHIVE",
        values={"restore_allowed": False},
    )
    cases.append(
        (
            "restore_allowed=false means restore is blocked",
            solution.explicit_state(backup) == "BLOCKED"
            and not solution.observation_violations(backup, rules, cutoff)
            and solution.compliant_verification(backup, rules, cutoff)
            == ("VERIFY_RESTORE_BLOCKED", "BLOCKED"),
        )
    )

    parsed_support: list[solution.Observation] = []
    solution.text_observations(
        "- 2026-10-01T12:00:00Z | support_platform | support_7 | "
        "SUPPORT_MESSAGE | IDENTIFIABLE | person@example.test",
        evidence_id="team_support",
        file_path="synthetic.log",
        captured_at=cutoff,
        observations=parsed_support,
    )
    support = next(item for item in parsed_support if item.resource_id == "support_7")
    support_violation = solution.observation_violations(support, rules, cutoff)
    cases.append(
        (
            "subject-only support message is deleted without shared context",
            support.values.get("subject_reference") == "person@example.test"
            and solution.policy_action(
                support.category, support.values, rules, cutoff, support.observed_at
            )
            == "DELETE"
            and support_violation
            == [("REPORT_REMAINING_RECORD", "PRESENT", "DELETE_RECORD")],
        )
    )

    shared_support_values = {
        "subject_reference": "person@example.test",
        "order_reference": "order_7",
    }
    cases.append(
        (
            "support message with shared order context is redacted",
            solution.policy_action(
                "SUPPORT_MESSAGE", shared_support_values, rules, cutoff, cutoff
            )
            == "REDACT",
        )
    )

    customer_conversation = solution.Observation(
        evidence_id="team_shared_support",
        file_path="synthetic",
        captured_at=cutoff,
        observed_at=cutoff,
        system_id="support_platform",
        resource_id="support_8",
        category="SUPPORT_MESSAGE",
        values={
            "customer_reference": "user_8",
            "message_excerpt": "Customer contacted support about account access.",
        },
    )
    cases.append(
        (
            "support conversation with retained customer context is kept",
            solution.compliant_verification(
                customer_conversation,
                rules,
                cutoff,
                shared_context_hint=True,
            )
            == ("VERIFY_RECORD_REDACTED", "REDACTED"),
        )
    )

    historical_hash = "historical-email-hash"
    analytics = solution.Observation(
        evidence_id="team_analytics",
        file_path="synthetic",
        captured_at=cutoff,
        observed_at=cutoff,
        system_id="analytics_platform",
        resource_id="analytics_7",
        category="ANALYTICS_PROFILE",
        values={"correlation_key": historical_hash, "rows": "1"},
    )
    analytics_violations = solution.observation_violations(
        analytics, rules, cutoff, {historical_hash}
    )
    cases.append(
        (
            "analytics profile linked by historical hash is not anonymized",
            analytics_violations
            == [("REPORT_EXPOSED_PII", "PRESENT", "ANONYMIZE_RECORD")]
            and solution.compliant_verification(
                analytics, rules, cutoff, {historical_hash}
            )
            is None,
        )
    )

    identity_link = solution.Observation(
        evidence_id="activity_team_link",
        file_path="dataset/system_activity.csv",
        captured_at=cutoff,
        observed_at=cutoff,
        system_id="identity_service",
        resource_id="tombstone_7",
        category="ACCOUNT_TOMBSTONE",
        values={
            "identity_link": True,
            "details": "linked tombstone_7 to person@example.test and user_7",
            "state": "PRESENT",
        },
    )
    cases.append(
        (
            "identity-link metadata does not prove tombstone PII",
            not solution.observation_violations(identity_link, rules, cutoff)
            and solution.compliant_verification(identity_link, rules, cutoff) is None,
        )
    )

    text_observations: list[solution.Observation] = []
    solution.text_observations(
        "Customer user_7 person@example.test\nReference support_7",
        evidence_id="team_pdf",
        file_path="synthetic.pdf",
        captured_at=cutoff,
        observations=text_observations,
        document_identifiers={"person@example.test", "user_7"},
    )
    pdf_link_ok = any(
        item.resource_id == "support_7"
        and {"person@example.test", "user_7"}.issubset(
            solution.observation_identifiers(item)
        )
        for item in text_observations
    )
    cases.append(("single-subject PDF links identifiers across lines", pdf_link_ok))

    incidental_state = solution.infer_state(
        {"message": "The customer is active and was found in the audit."}
    )
    cases.append(
        (
            "incidental prose does not establish a record state",
            incidental_state == "",
        )
    )
    cases.append(
        (
            "data-flow resources map to integration service",
            solution.system_for("data_flow_7", "DATA_FLOW") == "integration_service",
        )
    )

    bad_contract = [
        {
            "case_id": "team_contract",
            "deletion_status": "INCOMPLETE",
            "steps_json": json.dumps(
                [
                    {
                        "step_no": 1,
                        "phase": "FINDING",
                        "system_id": "",
                        "resource_id": "account_7",
                        "operation": "REPORT_REMAINING_RECORD",
                        "state": "PRESENT",
                        "evidence_id": "team_evidence",
                    }
                ]
            ),
        }
    ]
    bad_contract_issues = independent_contract_issues(
        bad_contract,
        [
            {
                "case_id": "team_contract",
                "audit_at": "2026-10-01T00:00:00Z",
                "request_text": "Please delete my account.",
            }
        ],
        {"team_evidence": cutoff},
    )
    cases.append(
        (
            "independent validator rejects incomplete phases and blank identifiers",
            any("blank system_id" in issue for issue in bad_contract_issues)
            and any("INCOMPLETE needs all three phases" in issue for issue in bad_contract_issues),
        )
    )
    return cases


def citation_issues(
    output_rows: list[dict[str, str]],
    requests: list[dict[str, str]],
    aliases_by_user: dict[str, set[str]],
    users_by_email: dict[str, set[str]],
    observations: list[solution.Observation],
    rules: dict[str, dict[str, str]],
) -> tuple[int, list[str]]:
    requests_by_id = {request["case_id"]: request for request in requests}
    cited_count = 0
    issues: list[str] = []
    for row in output_rows:
        request = requests_by_id.get(row["case_id"])
        if request is None:
            continue
        cutoff = solution.parse_time(request["audit_at"])
        if cutoff is None:
            continue
        case_aliases, case_users_by_email = solution.build_identity_index(cutoff)
        user_id = solution.get_request_user(
            request["email"], case_users_by_email, case_aliases
        )
        if user_id is None:
            continue
        aliases_by_user = case_aliases
        users_by_email = case_users_by_email
        aliases = aliases_by_user[user_id]
        shared_context_hint = solution.request_has_shared_support_context(
            request["request_text"]
        )
        relevant = solution.user_observations(
            user_id, aliases, observations, cutoff
        )
        relevant_resources = {
            (item.system_id, item.resource_id) for item in relevant
        }
        try:
            steps = json.loads(row["steps_json"])
        except json.JSONDecodeError:
            continue
        for step in steps:
            evidence_id = step.get("evidence_id", "")
            if not evidence_id:
                continue
            cited_count += 1
            matching = [
                item
                for item in observations
                if item.evidence_id == evidence_id
                and item.system_id == step["system_id"]
                and item.resource_id == step["resource_id"]
                and item.captured_at <= cutoff
                and item.observed_at <= cutoff
            ]
            if not matching:
                issues.append(
                    f"{row['case_id']}: {evidence_id} has no cutoff-eligible "
                    f"observation for {step['system_id']}/{step['resource_id']}"
                )
                continue
            if (step["system_id"], step["resource_id"]) not in relevant_resources:
                issues.append(
                    f"{row['case_id']}: {evidence_id} resource "
                    f"{step['system_id']}/{step['resource_id']} is not linked to the subject"
                )
                continue
            if step["phase"] == "FINDING":
                remediation_by_operation = {
                    "REPORT_REMAINING_RECORD": {"DELETE_RECORD"},
                    "REPORT_EXPOSED_PII": {
                        "REDACT_RECORD",
                        "ANONYMIZE_RECORD",
                    },
                    "REPORT_ACTIVE_DATA_FLOW": {"DISABLE_DATA_FLOW"},
                    "REPORT_RESTORE_RISK": {"BLOCK_RESTORE"},
                }
                supported = any(
                    any(
                        operation == step["operation"]
                        and state == step["state"]
                        and remediation
                        in remediation_by_operation[step["operation"]]
                        for operation, state, remediation
                        in solution.observation_violations(
                            item,
                            rules,
                            cutoff,
                            aliases,
                            shared_context_hint
                            and item.category == "SUPPORT_MESSAGE",
                        )
                    )
                    for item in matching
                )
                if not supported:
                    issues.append(
                        f"{row['case_id']}: {evidence_id} does not support "
                        f"{step['operation']} for {step['resource_id']}"
                    )
            elif row["deletion_status"] == "COMPLETE":
                supported = any(
                    solution.compliant_verification(
                        item,
                        rules,
                        cutoff,
                        aliases,
                        shared_context_hint
                        and item.category == "SUPPORT_MESSAGE",
                    )
                    == (step["operation"], step["state"])
                    for item in matching
                )
                if not supported:
                    issues.append(
                        f"{row['case_id']}: {evidence_id} does not support "
                        f"{step['operation']} for {step['resource_id']}"
                    )
    return cited_count, issues


def main() -> int:
    failures: list[str] = []
    for name, text, expected in TEAM_CASES:
        actual = solution.is_deletion_request(text)
        passed = actual is expected
        print(f"{'PASS' if passed else 'FAIL'} team case: {name} (expected={expected}, actual={actual})")
        if not passed:
            failures.append(f"intent test failed: {name}")

    for name, passed in regression_cases():
        print(f"{'PASS' if passed else 'FAIL'} team regression: {name}")
        if not passed:
            failures.append(f"regression test failed: {name}")

    aliases, users_by_email = solution.build_identity_index()
    observations, _ = solution.load_evidence()
    solution.load_identity_link_events(observations)
    rules = {
        row["data_category"]: row
        for row in solution.read_csv(solution.DATASET / "deletion_rules.csv")
    }
    current_emails = {
        row["email"].strip().casefold()
        for row in solution.read_csv(solution.DATASET / "user_identities.csv")
    }
    historical_request = None
    for request in solution.read_csv(solution.DATASET / "deletion_requests.csv"):
        email = request["email"].strip().casefold()
        if email not in current_emails and len(users_by_email.get(email, set())) == 1:
            historical_request = request
            break
    history_ok = False
    if historical_request:
        cutoff = solution.parse_time(historical_request["audit_at"])
        if cutoff:
            cutoff_aliases, cutoff_users = solution.build_identity_index(cutoff)
            history_ok = bool(
                solution.get_request_user(
                    historical_request["email"], cutoff_users, cutoff_aliases
                )
            )
    print(
        "PASS historical-email resolution"
        if history_ok
        else "FAIL historical-email resolution"
    )
    if not history_ok:
        failures.append("no historical-only request resolved")

    test_cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = solution.Observation(
        evidence_id="test_evidence",
        file_path="synthetic",
        captured_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        system_id="account_service",
        resource_id="account_test",
        category="USER_PROFILE",
        values={"email": "person@example.test"},
    )
    cutoff_ok = not solution.user_observations(
        "user_test", {"person@example.test"}, [later], test_cutoff
    )
    print("PASS future evidence cutoff" if cutoff_ok else "FAIL future evidence cutoff")
    if not cutoff_ok:
        failures.append("future evidence was included")

    output_path = ROOT / "output.csv"
    with output_path.open(newline="", encoding="utf-8") as handle:
        output_reader = csv.DictReader(handle)
        if output_reader.fieldnames != ["case_id", "deletion_status", "steps_json"]:
            failures.append("Output header does not match the exact required columns")
        output_rows = list(output_reader)
    requests = solution.read_csv(solution.DATASET / "deletion_requests.csv")
    evidence_times = {
        row["file_id"]: solution.parse_time(row["captured_at"])
        for row in solution.read_csv(solution.DATASET / "evidence_files.csv")
    }
    evidence_times.update(
        {
            row["activity_id"]: solution.parse_time(row["event_time"])
            for row in solution.read_csv(solution.DATASET / "system_activity.csv")
        }
    )
    evidence_times.update(
        {
            row["audit_id"]: solution.parse_time(row["event_time"])
            for row in solution.read_csv(solution.DATASET / "user_audit_log.csv")
        }
    )
    structural_issues = independent_contract_issues(
        output_rows, requests, evidence_times
    )
    print(
        f"Output contract: {len(output_rows)} rows, "
        f"{len(structural_issues)} validation issues"
    )
    if structural_issues:
        failures.extend(structural_issues[:20])

    citation_count, citation_errors = citation_issues(
        output_rows,
        requests,
        aliases,
        users_by_email,
        observations,
        rules,
    )
    print(
        f"Evidence citations: {citation_count} cited steps, "
        f"{len(citation_errors)} relevance/support issues"
    )
    if citation_errors:
        failures.extend(citation_errors[:20])

    sample = public_sample_metrics()
    sample_citations, sample_citation_errors = citation_issues(
        list(csv.DictReader((solution.DATASET / "sample_output.csv").open(
            newline="", encoding="utf-8"
        ))),
        solution.read_csv(solution.DATASET / "sample_deletion_requests.csv"),
        aliases,
        users_by_email,
        observations,
        rules,
    )
    print(
        "Public sample diagnostic: "
        f"status={sample['status_correct']}/{sample['sample_cases']} "
        f"({sample['status_accuracy']:.1%}), "
        f"exact sequences={sample['exact_sequence_cases']}/{sample['sample_cases']} "
        f"({sample['sequence_accuracy']:.1%}), "
        f"step={sample['step_accuracy']:.1%}, field={sample['field_accuracy']:.1%}, "
        f"unresolved={sample['unresolved_cases']}"
    )
    print(
        f"Public sample answer citations: {sample_citations} cited steps, "
        f"{len(sample_citation_errors)} relevance/support issues"
    )
    for issue in sample_citation_errors:
        print(f"  sample citation issue: {issue}")
    print("Sample mismatches:")
    for mismatch in sample["mismatches"]:
        print(json.dumps(mismatch, ensure_ascii=False, sort_keys=True))
    if failures:
        print(f"FAILURES: {len(failures)}")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("All team-created and output-contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
