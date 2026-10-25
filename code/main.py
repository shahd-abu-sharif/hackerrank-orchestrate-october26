from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"
OUTPUT = ROOT / "output.csv"
STEP_FIELDS = (
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
)

SYSTEM_BY_COLLECTION = {
    "customer_profiles": "account_service",
    "customer_profiles_primary": "account_service",
    "customer_profiles_failover_replica": "account_service",
    "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "customer_analytics_reports": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
    "deleted_customer_references": "identity_service",
    "marketing_profiles": "marketing_service",
    "email_delivery_logs": "email_delivery_service",
    "cache_entries": "cache_service",
}

CATEGORY_BY_PREFIX = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "analytics": "ANALYTICS_PROFILE",
    "support": "SUPPORT_MESSAGE",
    "attachment": "SUPPORT_ATTACHMENT",
    "export": "USER_EXPORT",
    "data_flow": "DATA_FLOW",
    "backup": "BACKUP_ARCHIVE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "marketing": "MARKETING_PROFILE",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "cache": "CACHE_ENTRY",
    "telemetry": "PRODUCT_TELEMETRY",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "identifier": "SECONDARY_IDENTIFIER",
    "secondary_identifier": "SECONDARY_IDENTIFIER",
}

SYSTEM_BY_CATEGORY = {
    "USER_PROFILE": "account_service",
    "SECONDARY_IDENTIFIER": "identity_service",
    "SEARCH_INDEX_ENTRY": "search_service",
    "CACHE_ENTRY": "cache_service",
    "SUPPORT_MESSAGE": "support_platform",
    "SUPPORT_ATTACHMENT": "support_platform",
    "ANALYTICS_PROFILE": "analytics_platform",
    "PRODUCT_TELEMETRY": "analytics_platform",
    "USER_EXPORT": "export_service",
    "MARKETING_PROFILE": "marketing_service",
    "EMAIL_DELIVERY_LOG": "email_delivery_service",
    "SECURITY_AUDIT_LOG": "security_service",
    "LEGAL_HOLD_RECORD": "legal_service",
    "BACKUP_ARCHIVE": "backup_service",
    "ACCOUNT_TOMBSTONE": "identity_service",
    "DATA_FLOW": "integration_service",
}

FINDING_ORDER = {
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
}
REMEDIATION_ORDER = {
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
}
VERIFICATION_ORDER = {
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}

RESOURCE_PATTERN = re.compile(
    r"\b(?:account|search|analytics|support|attachment|object|export|data_flow|"
    r"backup|audit_record|tombstone|marketing|email_delivery|cache|telemetry|"
    r"legal_hold|identifier|secondary_identifier)"
    r"_\d+\b",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
USER_ID_PATTERN = re.compile(r"\buser_\d+\b", re.I)
LEADING_TIME_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)")
KEY_VALUE_PATTERN = re.compile(
    r"(?:^|\s)([A-Za-z][A-Za-z0-9_-]*)=(\"[^\"]*\"|'[^']*'|\S+)"
)


@dataclass
class Observation:
    evidence_id: str
    file_path: str
    captured_at: datetime
    observed_at: datetime
    system_id: str
    resource_id: str
    category: str
    values: dict[str, Any] = field(default_factory=dict)
    scope: str = ""
    line: str = ""


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def canonical(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def normal_email(value: Any) -> str:
    return canonical(value).strip("<>.,;:")


def scalar_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from scalar_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from scalar_values(nested)
    elif value is not None and isinstance(value, (str, int, float, bool)):
        yield str(value)


def normalize_state(value: Any) -> str:
    text = canonical(value).replace("-", "_").replace(" ", "_")
    aliases = {
        "not_found": "ABSENT",
        "notfound": "ABSENT",
        "missing": "ABSENT",
        "absent": "ABSENT",
        "deleted": "ABSENT",
        "404": "ABSENT",
        "found": "PRESENT",
        "identifiable": "PRESENT",
        "present": "PRESENT",
        "active": "ACTIVE",
        "enabled": "ACTIVE",
        "running": "ACTIVE",
        "healthy": "ACTIVE",
        "disabled": "DISABLED",
        "inactive": "DISABLED",
        "blocked": "BLOCKED",
        "redacted": "REDACTED",
        "anonymized": "ANONYMIZED",
        "anonymised": "ANONYMIZED",
    }
    return aliases.get(text, "")


def infer_state(values: dict[str, Any], line: str = "") -> str:
    restore_allowed = values.get("restore_allowed")
    if restore_allowed is False or canonical(restore_allowed) in {"false", "0", "no"}:
        return "BLOCKED"
    if restore_allowed is True or canonical(restore_allowed) in {"true", "1", "yes"}:
        return "ACTIVE"
    for key in (
        "state",
        "status",
        "record_status",
        "account_status",
        "restore_status",
        "restore_state",
        "result",
        "outcome",
        "marker_status",
        "flow_status",
    ):
        state = normalize_state(values.get(key))
        if state:
            return state
    if values.get("enabled") is False or canonical(values.get("enabled")) == "false":
        return "DISABLED"
    if any(
        values.get(key) is True or canonical(values.get(key)) == "true"
        for key in ("restore_blocked", "restore_access_blocked", "block_restore")
    ):
        return "BLOCKED"
    code = values.get("status_code")
    if str(code) == "404" or str(values.get("http_status")) == "404":
        return "ABSENT"
    if str(values.get("rows")) == "0":
        return "ABSENT"
    return ""


def category_for(resource_id: str, values: dict[str, Any], context: str = "") -> str:
    for key in ("data_category", "record_type", "category", "collection_name", "collection"):
        value = values.get(key)
        if value:
            normalized = re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")
            known = {
                "USER_PROFILE",
                "SECONDARY_IDENTIFIER",
                "SEARCH_INDEX_ENTRY",
                "CACHE_ENTRY",
                "SUPPORT_MESSAGE",
                "SUPPORT_ATTACHMENT",
                "ANALYTICS_PROFILE",
                "PRODUCT_TELEMETRY",
                "USER_EXPORT",
                "MARKETING_PROFILE",
                "EMAIL_DELIVERY_LOG",
                "SECURITY_AUDIT_LOG",
                "LEGAL_HOLD_RECORD",
                "BACKUP_ARCHIVE",
                "ACCOUNT_TOMBSTONE",
            }
            if normalized in known:
                return normalized
    combined = f"{resource_id} {context}".casefold()
    if resource_id.casefold().startswith("object_") and "attachment" in combined:
        return "SUPPORT_ATTACHMENT"
    for prefix, category in CATEGORY_BY_PREFIX.items():
        if re.search(rf"\b{re.escape(prefix)}_\d+\b", combined):
            return category
    return ""


def system_for(resource_id: str, category: str, context: str = "") -> str:
    for system in (
        "account_service",
        "search_service",
        "support_platform",
        "analytics_platform",
        "export_service",
        "integration_service",
        "backup_service",
        "security_service",
        "identity_service",
        "marketing_service",
        "email_delivery_service",
        "cache_service",
        "legal_service",
        "object_storage",
    ):
        if system in context:
            return system
    return SYSTEM_BY_CATEGORY.get(category, "")


def add_observation(
    observations: list[Observation],
    *,
    evidence_id: str,
    file_path: str,
    captured_at: datetime,
    observed_at: datetime | None,
    system_id: str,
    resource_id: str,
    category: str,
    values: dict[str, Any],
    scope: str = "",
    line: str = "",
) -> None:
    if not resource_id or not category:
        return
    observations.append(
        Observation(
            evidence_id=evidence_id,
            file_path=file_path,
            captured_at=captured_at,
            observed_at=observed_at or captured_at,
            system_id=system_id or SYSTEM_BY_CATEGORY.get(category, ""),
            resource_id=resource_id,
            category=category,
            values=values,
            scope=scope,
            line=line,
        )
    )


def walk_json_records(
    data: Any,
    *,
    evidence_id: str,
    file_path: str,
    captured_at: datetime,
    observations: list[Observation],
    system_context: str = "",
    category_context: str = "",
    scope: str = "",
) -> None:
    if isinstance(data, list):
        for item in data:
            walk_json_records(
                item,
                evidence_id=evidence_id,
                file_path=file_path,
                captured_at=captured_at,
                observations=observations,
                system_context=system_context,
                category_context=category_context,
                scope=scope,
            )
        return
    if not isinstance(data, dict):
        return

    local_system = system_context
    local_category = category_context
    local_scope = scope
    for key, value in data.items():
        if key in {"data_store", "system_id", "system", "service"} and isinstance(value, str):
            local_system = value
        elif key in {"collection", "collection_name", "record_type", "data_category"} and isinstance(value, str):
            local_category = value
        elif key in {"table", "table_name", "topology"} and isinstance(value, str):
            local_scope = value

    resource_id = str(data.get("resource_id") or data.get("_id") or "")
    if resource_id:
        observed_at = None
        for key in ("observed_at", "captured_at", "updated_at", "recorded_at", "event_time"):
            observed_at = parse_time(data.get(key))
            if observed_at:
                break
        context = " ".join((local_system, local_category, local_scope))
        category = category_for(resource_id, data, context)
        system_id = system_for(resource_id, category, context)
        add_observation(
            observations,
            evidence_id=evidence_id,
            file_path=file_path,
            captured_at=captured_at,
            observed_at=observed_at,
            system_id=system_id,
            resource_id=resource_id,
            category=category,
            values=data,
            scope=local_scope,
        )
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                walk_json_records(
                    value,
                    evidence_id=evidence_id,
                    file_path=file_path,
                    captured_at=captured_at,
                    observations=observations,
                    system_context=local_system,
                    category_context=local_category,
                    scope=local_scope,
                )
        return

    for key, value in data.items():
        child_system = local_system
        child_category = local_category
        if key in SYSTEM_BY_CATEGORY or key.endswith("_service") or key.endswith("_platform"):
            child_system = key
        walk_json_records(
            value,
            evidence_id=evidence_id,
            file_path=file_path,
            captured_at=captured_at,
            observations=observations,
            system_context=child_system,
            category_context=child_category,
            scope=local_scope,
        )


def sql_observations(
    path: Path, evidence_id: str, captured_at: datetime, observations: list[Observation]
) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(path.read_text(encoding="utf-8", errors="replace"))
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table,) in tables:
            if table == "snapshot_scope":
                continue
            escaped = table.replace('"', '""')
            cursor = connection.execute(f'SELECT * FROM "{escaped}"')
            columns = [column[0] for column in cursor.description or ()]
            for row in cursor.fetchall():
                values = dict(zip(columns, row))
                record_json = values.get("record_json")
                if isinstance(record_json, str):
                    try:
                        embedded = json.loads(record_json)
                    except json.JSONDecodeError:
                        embedded = {}
                    if isinstance(embedded, dict):
                        values.update(embedded)
                resource_id = str(values.get("resource_id") or values.get("_id") or "")
                if not resource_id:
                    continue
                category = category_for(resource_id, values, table)
                context = " ".join(
                    (
                        table,
                        str(values.get("origin_system") or ""),
                        str(values.get("collection_name") or ""),
                    )
                )
                system_id = str(values.get("origin_system") or "") or system_for(
                    resource_id, category, context
                )
                observed_at = None
                for key in ("observed_at", "captured_at", "updated_at", "event_time"):
                    observed_at = parse_time(values.get(key))
                    if observed_at:
                        break
                add_observation(
                    observations,
                    evidence_id=evidence_id,
                    file_path=str(path.relative_to(ROOT)),
                    captured_at=captured_at,
                    observed_at=observed_at,
                    system_id=system_id,
                    resource_id=resource_id,
                    category=category,
                    values=values,
                    scope=table,
                )
    finally:
        connection.close()


def parse_key_values(line: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    normalized = line.replace('\\"', '"')
    for match in KEY_VALUE_PATTERN.finditer(normalized):
        value = match.group(2).strip("\"'")
        values[match.group(1).casefold()] = value
    return values


def text_observations(
    text: str,
    *,
    evidence_id: str,
    file_path: str,
    captured_at: datetime,
    observations: list[Observation],
    document_identifiers: set[str] | None = None,
) -> None:
    lines = text.splitlines()
    aliases_by_trace: dict[str, set[str]] = {}
    parsed_lines: list[tuple[str, dict[str, Any], set[str]]] = []
    trace_pattern = re.compile(r"\b(?:trace_id|traceId|requestId|request_id)\s*=?\s*[\"']?([A-Za-z0-9_-]+)", re.I)
    for line in lines:
        normalized_line = re.sub(r"^\s*[-*]\s+", "", line)
        values = parse_key_values(normalized_line)
        if "{" in normalized_line:
            json_start = normalized_line.find("{")
            try:
                parsed_json = json.loads(normalized_line[json_start:])
            except json.JSONDecodeError:
                parsed_json = None
            if isinstance(parsed_json, dict):
                values.update({str(key).casefold(): value for key, value in parsed_json.items()})
        aliases = {canonical(value) for value in EMAIL_PATTERN.findall(normalized_line)}
        aliases.update(canonical(value) for value in USER_ID_PATTERN.findall(normalized_line))
        traces = {match.group(1) for match in trace_pattern.finditer(normalized_line)}
        for key in ("trace_id", "traceid", "requestid", "request_id"):
            if values.get(key):
                traces.add(str(values[key]).strip("\"'"))
        traces.update(re.findall(r"\btr-[A-Za-z0-9_-]+\b", normalized_line))
        for trace in traces:
            aliases_by_trace.setdefault(trace, set()).update(aliases)
        parsed_lines.append((normalized_line, values, traces))

    for line, values, traces in parsed_lines:
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        linked_aliases = set().union(*(aliases_by_trace.get(trace, set()) for trace in traces))
        if document_identifiers:
            linked_aliases.update(document_identifiers)
        if linked_aliases:
            values["linked_identifiers"] = sorted(linked_aliases)
        leading_time = LEADING_TIME_PATTERN.match(line)
        if leading_time:
            values["observed_at"] = leading_time.group(1)
        resource_ids = set(RESOURCE_PATTERN.findall(line))
        if len(parts) >= 5 and parse_time(parts[0]):
            values.update(
                {
                    "observed_at": parts[0],
                    "system": parts[1],
                    "record_type": parts[3],
                    "status": parts[4],
                }
            )
            if len(parts) > 5:
                values["subject_reference"] = parts[5]
            resource_ids.update(RESOURCE_PATTERN.findall(parts[2]))
        for resource_id in sorted(resource_ids):
            context = line + " " + " ".join(str(value) for value in values.values())
            category = category_for(resource_id, values, context)
            system_id = system_for(resource_id, category, context)
            observed_at = None
            for key in ("observed_at", "timestamp", "event_time", "last_write"):
                observed_at = parse_time(values.get(key))
                if observed_at:
                    break
            add_observation(
                observations,
                evidence_id=evidence_id,
                file_path=file_path,
                captured_at=captured_at,
                observed_at=observed_at,
                system_id=system_id,
                resource_id=resource_id,
                category=category,
                values=values,
                scope=str(values.get("service") or values.get("source") or ""),
                line=line,
            )


def pdf_text(path: Path) -> str:
    executable = shutil.which("pdftotext")
    if not executable:
        return ""
    result = subprocess.run(
        [executable, "-layout", str(path), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"pdftotext failed for {path}: {result.stderr.strip()}")
    return result.stdout


def image_text(path: Path) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        return ""
    result = subprocess.run(
        [executable, str(path), "stdout"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"tesseract failed for {path}: {result.stderr.strip()}")
    return result.stdout


def load_evidence() -> tuple[list[Observation], list[str]]:
    observations: list[Observation] = []
    warnings: list[str] = []
    manifest = read_csv(DATASET / "evidence_files.csv")
    for item in manifest:
        evidence_id = item["file_id"]
        file_path = DATASET / item["file_path"]
        captured_at = parse_time(item["captured_at"])
        if captured_at is None:
            warnings.append(f"{evidence_id}: invalid manifest timestamp")
            continue
        if not file_path.is_file():
            raise FileNotFoundError(f"Evidence file listed in manifest is missing: {file_path}")
        suffix = file_path.suffix.casefold()
        relative_path = str(file_path.relative_to(ROOT))
        if suffix == ".json":
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON evidence {file_path}: {error}") from error
            walk_json_records(
                data,
                evidence_id=evidence_id,
                file_path=relative_path,
                captured_at=captured_at,
                observations=observations,
            )
        elif suffix == ".sql":
            sql_observations(file_path, evidence_id, captured_at, observations)
        elif suffix in {".log", ".txt"}:
            text_observations(
                file_path.read_text(encoding="utf-8", errors="replace"),
                evidence_id=evidence_id,
                file_path=relative_path,
                captured_at=captured_at,
                observations=observations,
            )
        elif suffix == ".pdf":
            text = pdf_text(file_path)
            if not text:
                warnings.append(f"{evidence_id}: PDF text not extracted (pdftotext unavailable)")
            document_emails = {canonical(value) for value in EMAIL_PATTERN.findall(text)}
            document_users = {canonical(value) for value in USER_ID_PATTERN.findall(text)}
            document_identifiers = (
                document_emails | document_users
                if len(document_emails) <= 1
                and len(document_users) <= 1
                and (document_emails or document_users)
                else None
            )
            text_observations(
                text,
                evidence_id=evidence_id,
                file_path=relative_path,
                captured_at=captured_at,
                observations=observations,
                document_identifiers=document_identifiers,
            )
        elif suffix == ".png":
            text = image_text(file_path)
            if not text:
                warnings.append(f"{evidence_id}: screenshot OCR unavailable (tesseract missing)")
            text_observations(
                text,
                evidence_id=evidence_id,
                file_path=relative_path,
                captured_at=captured_at,
                observations=observations,
            )
        else:
            warnings.append(f"{evidence_id}: unsupported evidence format {suffix or '(none)'}")
    return observations, warnings


def load_identity_link_events(observations: list[Observation]) -> None:
    for row in read_csv(DATASET / "system_activity.csv"):
        if row["event_type"] != "LINK_RESOURCE_IDENTITY":
            continue
        event_time = parse_time(row["event_time"])
        resource_id = row["resource_id"]
        category = category_for(
            resource_id, {}, f"{row['target_system']} {row['details']}"
        )
        if event_time is None or not category:
            continue
        values = {
            "event_type": row["event_type"],
            "target_system": row["target_system"],
            "details": row["details"],
            "message_text": row["details"],
            "resource_id": resource_id,
            "state": "PRESENT",
            "identity_link": True,
        }
        observations.append(
            Observation(
                evidence_id=row["activity_id"],
                file_path="dataset/system_activity.csv",
                captured_at=event_time,
                observed_at=event_time,
                system_id=row["target_system"],
                resource_id=resource_id,
                category=category,
                values=values,
                scope="identity_link",
                line=row["details"],
            )
        )


def build_identity_index(
    cutoff: datetime | None = None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    aliases: dict[str, set[str]] = {}
    users_by_email: dict[str, set[str]] = {}

    def add(user_id: str, value: Any) -> None:
        key = canonical(value)
        if key:
            aliases.setdefault(user_id, set()).add(key)
            if "@" in key:
                users_by_email.setdefault(normal_email(key), set()).add(user_id)

    for row in read_csv(DATASET / "user_identities.csv"):
        snapshot_time = parse_time(row.get("updated_at"))
        if cutoff is not None and (snapshot_time is None or snapshot_time > cutoff):
            continue
        user_id = row["user_id"]
        for field_name in ("name", "email", "phone", "email_hash"):
            add(user_id, row.get(field_name))
        email = row.get("email", "").strip().lower()
        if email:
            add(user_id, hashlib.sha256(email.encode("utf-8")).hexdigest())

    audit_rows = read_csv(DATASET / "user_audit_log.csv")
    for row in audit_rows:
        event_time = parse_time(row.get("event_time"))
        if cutoff is not None and (event_time is None or event_time > cutoff):
            continue
        user_id = row["user_id"]
        for field_name in ("old_value", "new_value"):
            value = row.get(field_name, "")
            add(user_id, value)
            if row.get("field_name", "").casefold() == "email" and value:
                add(user_id, hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest())
    return aliases, users_by_email


DELETION_TERMS = (
    "delete",
    "deletion",
    "delet",
    "erase",
    "erasure",
    "remove my personal data",
    "remove all my data",
    "remove my account",
    "close my account",
    "account closure",
    "purge my",
    "forget me",
    "right to be forgotten",
    "supprimer",
    "suppression",
    "effacer",
    "eliminar",
    "eliminación",
    "eliminacao",
    "exclusão",
    "exclusao",
    "apagar",
    "löschen",
    "loeschen",
    "cancellare",
    "cancellazione",
    "verwijder",
    "usunąć",
    "usunac",
    "удалить",
    "удаление",
    "削除",
    "删除",
    "刪除",
    "삭제",
    "حذف",
    "احذف",
    "हटाना",
    "मिटाना",
    "διαγραφή",
    "silme",
    "silmek",
)

NON_DELETION_TERMS = (
    "not a request to erase",
    "not asking for deletion",
    "not requesting deletion",
    "not a request for deletion",
    "not asking you to delete",
    "do not delete",
    "don't delete",
    "please don't delete",
    "please do not delete",
    "do not close the account",
    "don't close the account",
    "without removing any of my records",
    "leave the account active",
    "leave my profile",
    "keep the account as it is",
    "still need the account",
    "still use it",
    "change the email",
    "change my email",
    "correct my",
    "replace it with the new",
    "resetting the login",
    "stop the promotional emails",
    "remove me from marketing",
    "unsubscribe",
    "download my account information",
    "send me a copy",
    "copy of the information",
    "tell me what personal information",
    "access request",
)


def is_deletion_request(text: str) -> bool:
    normalized = canonical(text)
    if any(term in normalized for term in NON_DELETION_TERMS):
        return False
    if "marketing" in normalized and not any(
        term in normalized
        for term in ("delete my account", "delete my data", "erase my account", "delete the account")
    ):
        return False
    if any(term in normalized for term in DELETION_TERMS):
        return True
    contextual_patterns = (
        r"\b(?:close|closed|erase|remove|delete|purge)\b.{0,80}\b(?:account|profile|records?|data|search result|personal details)\b",
        r"\b(?:account|profile|records?|data)\b.{0,80}\b(?:closed|removed|erased|deleted|gone)\b",
        r"\bclean\b.{0,80}\b(?:personal details|personal data|profile|records?)\b",
        r"\brequest failed\b.{0,100}\b(?:account|profile|deletion|privacy)\b",
    )
    return any(re.search(pattern, normalized) for pattern in contextual_patterns)


def observation_identifiers(observation: Observation) -> set[str]:
    found = {canonical(value) for value in scalar_values(observation.values)}
    found.update(canonical(email) for email in EMAIL_PATTERN.findall(observation.line))
    found.update(canonical(user_id) for user_id in USER_ID_PATTERN.findall(observation.line))
    found.update(canonical(value) for value in observation.values.get("linked_identifiers", []))
    return {value for value in found if value}


def values_have_pii(values: dict[str, Any], line: str = "") -> bool:
    pii_keys = {
        "name",
        "full_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "email_hash",
        "display_name",
        "customer_name",
        "requester_name",
        "requester_email",
        "requester_phone",
    }
    for key, value in values.items():
        if key.casefold() in pii_keys and value not in (None, ""):
            if canonical(value) not in {"null", "none", "—", "-", "unknown", "n/a"}:
                return True
    all_text = " ".join(scalar_values(values)) + " " + line
    if EMAIL_PATTERN.search(all_text):
        return True
    if re.search(r"(?<!\w)\+\d[\d ()-]{7,}\d(?!\w)", all_text):
        return True
    return False


def request_has_shared_support_context(text: str) -> bool:
    normalized = canonical(text)
    return any(
        phrase in normalized
        for phrase in (
            "another customer",
            "shared order",
            "conversation itself may need to stay",
        )
    )


def policy_action(
    category: str,
    values: dict[str, Any],
    rules: dict[str, dict[str, str]],
    cutoff: datetime,
    observed_at: datetime,
    shared_context_hint: bool = False,
) -> str:
    rule = rules.get(category)
    if not rule:
        return ""
    action = rule["required_action"].upper()
    condition = rule["condition"].casefold()
    if category == "SUPPORT_ATTACHMENT":
        if "belongs only" in condition and values.get("shared_context") is True:
            return "REDACT"
        return action
    if category == "SUPPORT_MESSAGE":
        shared_context = shared_context_hint or values.get("shared_context") is True or canonical(
            values.get("shared_context")
        ) in {"true", "yes", "1", "shared"}
        if any(
            values.get(key) not in (None, "")
            for key in (
                "order_id",
                "order_reference",
                "customer_context",
                "agent_context",
            )
        ):
            shared_context = True
        message_text = canonical(values.get("message_text") or values.get("message_excerpt"))
        if values.get("customer_reference") not in (None, "") and message_text:
            shared_context = True
        shared_context = shared_context or any(
            token in message_text
            for token in (
                "another customer",
                "shared order",
                "order context",
                "order update",
                "delivery query",
            )
        )
        return "REDACT" if shared_context else "DELETE"
    if category in {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}:
        return "ANONYMIZE"
    if category == "SECURITY_AUDIT_LOG":
        retention_until = None
        for key in ("retention_until", "retain_until", "retention_expires_at"):
            retention_until = parse_time(values.get(key))
            if retention_until:
                break
        if retention_until:
            return "RETAIN" if retention_until >= cutoff else ""
        record_time = None
        for key in ("event_time", "recorded_at", "created_at", "updated_at"):
            record_time = parse_time(values.get(key))
            if record_time:
                break
        record_time = record_time or observed_at
        return "RETAIN" if (cutoff - record_time).days <= 365 else ""
    if category == "LEGAL_HOLD_RECORD":
        active = canonical(values.get("hold_status") or values.get("status"))
        return "RETAIN" if active in {"active", "true", "1", "present"} else ""
    if category == "BACKUP_ARCHIVE":
        return "RETAIN"
    return action


def explicit_state(observation: Observation) -> str:
    state = infer_state(observation.values, observation.line)
    if (
        observation.values.get("account_status") == "DELETED"
        and observation.category in {"USER_PROFILE", "ACCOUNT_TOMBSTONE"}
    ):
        return "PRESENT"
    if state:
        return state
    if observation.category in {"DATA_FLOW"}:
        return "ACTIVE"
    return "PRESENT"


def observation_violations(
    observation: Observation,
    rules: dict[str, dict[str, str]],
    cutoff: datetime,
    subject_aliases: set[str] | None = None,
    shared_context_hint: bool = False,
) -> list[tuple[str, str, str]]:
    if (
        observation.values.get("identity_link") is True
        and observation.category == "ACCOUNT_TOMBSTONE"
    ):
        return []
    category = observation.category
    action = policy_action(
        category,
        observation.values,
        rules,
        cutoff,
        observation.observed_at,
        shared_context_hint,
    )
    state = explicit_state(observation)
    values = observation.values
    violations: list[tuple[str, str, str]] = []

    if category == "DATA_FLOW":
        if state not in {"DISABLED", "ABSENT"}:
            violations.append(("REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "DISABLE_DATA_FLOW"))
        return violations
    if category == "BACKUP_ARCHIVE":
        if state not in {"BLOCKED", "ABSENT"}:
            violations.append(("REPORT_RESTORE_RISK", "ACTIVE", "BLOCK_RESTORE"))
        return violations
    if state == "ABSENT":
        return violations

    has_pii = values_have_pii(values, observation.line)
    if action == "DELETE":
        if state not in {"ABSENT", "ANONYMIZED"}:
            violations.append(("REPORT_REMAINING_RECORD", "PRESENT", "DELETE_RECORD"))
    elif action == "REDACT":
        if has_pii:
            violations.append(("REPORT_EXPOSED_PII", "PRESENT", "REDACT_RECORD"))
    elif action == "ANONYMIZE":
        linked = any(
            canonical(values.get(key))
            for key in ("user_id", "subject_reference", "customer_reference")
        ) or bool(
            subject_aliases
            and observation_identifiers(observation).intersection(subject_aliases)
        )
        if has_pii or linked:
            violations.append(("REPORT_EXPOSED_PII", "PRESENT", "ANONYMIZE_RECORD"))
    elif action == "RETAIN":
        if state not in {"PRESENT", "REDACTED", "ANONYMIZED"}:
            return violations
    return violations


def compliant_verification(
    observation: Observation,
    rules: dict[str, dict[str, str]],
    cutoff: datetime,
    subject_aliases: set[str] | None = None,
    shared_context_hint: bool = False,
) -> tuple[str, str] | None:
    if (
        observation.values.get("identity_link") is True
        and observation.category == "ACCOUNT_TOMBSTONE"
    ):
        return None
    state = explicit_state(observation)
    action = policy_action(
        observation.category,
        observation.values,
        rules,
        cutoff,
        observation.observed_at,
        shared_context_hint,
    )
    if observation.category == "DATA_FLOW":
        return ("VERIFY_DATA_FLOW_DISABLED", "DISABLED") if state == "DISABLED" else None
    if observation.category == "BACKUP_ARCHIVE":
        return ("VERIFY_RESTORE_BLOCKED", "BLOCKED") if state == "BLOCKED" else None
    if state == "ABSENT":
        return ("VERIFY_RECORD_ABSENT", "ABSENT")
    if action == "RETAIN":
        return ("VERIFY_RECORD_PRESENT", "PRESENT")
    if action == "REDACT" and not values_have_pii(observation.values, observation.line):
        return ("VERIFY_RECORD_REDACTED", "REDACTED")
    if action == "ANONYMIZE" and not values_have_pii(observation.values, observation.line):
        linked = any(
            canonical(observation.values.get(key))
            for key in ("user_id", "subject_reference", "customer_reference")
        ) or bool(
            subject_aliases
            and observation_identifiers(observation).intersection(subject_aliases)
        )
        if not linked:
            return ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED")
    return None


def user_observations(
    user_id: str,
    aliases: set[str],
    observations: list[Observation],
    cutoff: datetime,
) -> list[Observation]:
    available = [
        item
        for item in observations
        if item.captured_at <= cutoff and item.observed_at <= cutoff
    ]
    direct_matches = []
    for item in available:
        if observation_identifiers(item).intersection(aliases):
            direct_matches.append(item)

    linked_resources = {item.resource_id for item in direct_matches}
    linked_values = set(aliases)
    linked_values.add(canonical(user_id))
    selected = list(direct_matches)
    selected_keys = {(item.evidence_id, item.resource_id, item.scope, item.line) for item in selected}

    for _ in range(3):
        additions = []
        for item in available:
            key = (item.evidence_id, item.resource_id, item.scope, item.line)
            if key in selected_keys:
                continue
            identifiers = observation_identifiers(item)
            if item.resource_id in linked_resources or identifiers.intersection(linked_values):
                additions.append(item)
                linked_resources.add(item.resource_id)
                linked_values.update(identifiers.intersection(aliases))
        if not additions:
            break
        selected.extend(additions)
        selected_keys.update(
            (item.evidence_id, item.resource_id, item.scope, item.line) for item in additions
        )

    resource_latest: dict[tuple[str, str, str], Observation] = {}
    for item in selected:
        key = (item.system_id, item.resource_id, item.scope)
        previous = resource_latest.get(key)
        if previous is None or (item.observed_at, item.captured_at) >= (
            previous.observed_at,
            previous.captured_at,
        ):
            resource_latest[key] = item
    return sorted(
        resource_latest.values(),
        key=lambda item: (item.system_id, item.resource_id, item.scope, item.evidence_id),
    )


def get_request_user(
    email: str, users_by_email: dict[str, set[str]], aliases: dict[str, set[str]]
) -> str | None:
    matches = users_by_email.get(normal_email(email), set())
    if len(matches) == 1:
        return next(iter(matches))
    if len(matches) > 1:
        raise ValueError(f"Request email maps to multiple identities: {email}")
    return None


def make_step(
    phase: str,
    system_id: str,
    resource_id: str,
    operation: str,
    state: str,
    evidence_id: str,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "system_id": system_id,
        "resource_id": resource_id,
        "operation": operation,
        "state": state,
        "evidence_id": evidence_id,
    }


def ordered_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phase_order = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
    op_order = {
        "FINDING": FINDING_ORDER,
        "REMEDIATION": REMEDIATION_ORDER,
        "VERIFICATION": VERIFICATION_ORDER,
    }
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for step in steps:
        key = (step["phase"], step["system_id"], step["resource_id"], step["operation"])
        previous = unique.get(key)
        if previous is None:
            unique[key] = step
        elif previous["evidence_id"] == "" and step["evidence_id"]:
            unique[key] = step
    result = sorted(
        unique.values(),
        key=lambda step: (
            phase_order[step["phase"]],
            op_order[step["phase"]].get(step["operation"], 99),
            step["system_id"],
            step["resource_id"],
        ),
    )
    for number, step in enumerate(result, 1):
        step["step_no"] = number
    return result


def audit_case(
    request: dict[str, str],
    aliases_by_user: dict[str, set[str]],
    users_by_email: dict[str, set[str]],
    observations: list[Observation],
    rules: dict[str, dict[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    if not is_deletion_request(request["request_text"]):
        return "NOT_APPLICABLE", []

    cutoff = parse_time(request["audit_at"])
    if cutoff is None:
        raise ValueError(f"Invalid audit_at for {request['case_id']}")
    aliases_by_user, users_by_email = build_identity_index(cutoff)
    user_id = get_request_user(request["email"], users_by_email, aliases_by_user)
    if user_id is None:
        raise ValueError(f"No identity found for deletion request {request['case_id']}")
    relevant = user_observations(
        user_id, aliases_by_user.get(user_id, set()), observations, cutoff
    )
    shared_support_context = request_has_shared_support_context(request["request_text"])

    findings: list[dict[str, Any]] = []
    remediations: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    for observation in relevant:
        for operation, state, remediation in observation_violations(
            observation,
            rules,
            cutoff,
            aliases_by_user.get(user_id, set()),
            shared_support_context and observation.category == "SUPPORT_MESSAGE",
        ):
            findings.append(
                make_step(
                    "FINDING",
                    observation.system_id,
                    observation.resource_id,
                    operation,
                    state,
                    observation.evidence_id,
                )
            )
            remediations.append(
                make_step(
                    "REMEDIATION",
                    observation.system_id,
                    observation.resource_id,
                    remediation,
                    {
                        "DELETE_RECORD": "ABSENT",
                        "REDACT_RECORD": "REDACTED",
                        "ANONYMIZE_RECORD": "ANONYMIZED",
                        "DISABLE_DATA_FLOW": "DISABLED",
                        "BLOCK_RESTORE": "BLOCKED",
                    }[remediation],
                    "",
                )
            )
            verification_operation = {
                "DELETE_RECORD": "VERIFY_RECORD_ABSENT",
                "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
                "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED",
                "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
                "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
            }[remediation]
            verifications.append(
                make_step(
                    "VERIFICATION",
                    observation.system_id,
                    observation.resource_id,
                    verification_operation,
                    {
                        "VERIFY_RECORD_ABSENT": "ABSENT",
                        "VERIFY_RECORD_REDACTED": "REDACTED",
                        "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
                        "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
                        "VERIFY_RESTORE_BLOCKED": "BLOCKED",
                    }[verification_operation],
                    "",
                )
            )

    for observation in relevant:
        verification = compliant_verification(
            observation,
            rules,
            cutoff,
            aliases_by_user.get(user_id, set()),
            shared_support_context and observation.category == "SUPPORT_MESSAGE",
        )
        if verification:
            operation, state = verification
            evidence_id = observation.evidence_id if not findings else ""
            verifications.append(
                make_step(
                    "VERIFICATION",
                    observation.system_id,
                    observation.resource_id,
                    operation,
                    state,
                    evidence_id,
                )
            )

    if findings:
        return "INCOMPLETE", ordered_steps(findings + remediations + verifications)

    evidence_backed_checks = [step for step in verifications if step["evidence_id"]]
    if evidence_backed_checks:
        return "COMPLETE", ordered_steps(evidence_backed_checks)

    raise ValueError(
        f"No evidence-backed state or supported finding for {request['case_id']}; "
        "refusing to fabricate a deletion verdict"
    )


def validate_output(
    rows: list[dict[str, str]],
    requests: list[dict[str, str]],
    evidence_times: dict[str, datetime],
) -> list[str]:
    issues: list[str] = []
    expected_ids = sorted(row["case_id"] for row in requests)
    actual_ids = [row["case_id"] for row in rows]
    if actual_ids != expected_ids:
        issues.append("Output case IDs are not exactly the sorted input case IDs")
    if len(rows) != len(requests):
        issues.append(f"Expected {len(requests)} output rows, found {len(rows)}")

    allowed_phases = {"FINDING", "REMEDIATION", "VERIFICATION"}
    phase_operations = {
        "FINDING": set(FINDING_ORDER),
        "REMEDIATION": set(REMEDIATION_ORDER),
        "VERIFICATION": set(VERIFICATION_ORDER),
    }
    expected_fields = set(STEP_FIELDS)
    requests_by_id = {request["case_id"]: request for request in requests}
    for row in rows:
        request = requests_by_id.get(row["case_id"])
        if request and not is_deletion_request(request["request_text"]):
            if row["deletion_status"] != "NOT_APPLICABLE" or row["steps_json"] != "[]":
                issues.append(f"{row['case_id']}: non-deletion request must be NOT_APPLICABLE with []")
        if row["deletion_status"] not in {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}:
            issues.append(f"{row['case_id']}: invalid deletion status")
        try:
            steps = json.loads(row["steps_json"])
        except json.JSONDecodeError as error:
            issues.append(f"{row['case_id']}: invalid steps_json: {error}")
            continue
        if not isinstance(steps, list):
            issues.append(f"{row['case_id']}: steps_json is not an array")
            continue
        if row["deletion_status"] == "NOT_APPLICABLE" and steps:
            issues.append(f"{row['case_id']}: NOT_APPLICABLE must have []")
        if row["deletion_status"] == "COMPLETE" and any(
            step.get("phase") != "VERIFICATION" for step in steps if isinstance(step, dict)
        ):
            issues.append(f"{row['case_id']}: COMPLETE must contain verification only")
        if row["deletion_status"] == "INCOMPLETE" and not any(
            isinstance(step, dict) and step.get("phase") == "FINDING" for step in steps
        ):
            issues.append(f"{row['case_id']}: INCOMPLETE has no evidence-backed finding")
        if row["deletion_status"] == "INCOMPLETE" and (
            not any(isinstance(step, dict) and step.get("phase") == "REMEDIATION" for step in steps)
            or not any(isinstance(step, dict) and step.get("phase") == "VERIFICATION" for step in steps)
        ):
            issues.append(f"{row['case_id']}: INCOMPLETE lacks remediation or planned verification")
        phase_numbers = []
        for number, step in enumerate(steps, 1):
            if not isinstance(step, dict) or set(step) != expected_fields:
                issues.append(f"{row['case_id']}: step {number} has incorrect fields")
                continue
            if step["step_no"] != number:
                issues.append(f"{row['case_id']}: step numbering is not consecutive")
            if (
                step["phase"] not in allowed_phases
                or step["operation"] not in phase_operations.get(step["phase"], set())
            ):
                issues.append(f"{row['case_id']}: unsupported phase or operation")
            phase_numbers.append({"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}.get(step["phase"], 9))
            evidence_id = step["evidence_id"]
            if evidence_id and evidence_id not in evidence_times:
                issues.append(f"{row['case_id']}: unknown evidence reference {step['evidence_id']}")
            if evidence_id and request:
                cutoff = parse_time(request["audit_at"])
                evidence_time = evidence_times.get(evidence_id)
                if cutoff and evidence_time and evidence_time > cutoff:
                    issues.append(f"{row['case_id']}: evidence {evidence_id} is after audit_at")
            if step["phase"] == "FINDING" and not evidence_id:
                issues.append(f"{row['case_id']}: finding has no evidence reference")
            if row["deletion_status"] == "COMPLETE" and not evidence_id:
                issues.append(f"{row['case_id']}: COMPLETE verification is not evidence-backed")
            if row["deletion_status"] == "INCOMPLETE" and step["phase"] != "FINDING" and step["evidence_id"]:
                issues.append(f"{row['case_id']}: future action must have an empty evidence_id")
        if phase_numbers != sorted(phase_numbers):
            issues.append(f"{row['case_id']}: phases are out of order")
        if all(isinstance(step, dict) and set(step) == expected_fields for step in steps):
            canonical = ordered_steps([dict(step) for step in steps])
            keys = lambda items: [
                (item["phase"], item["operation"], item["system_id"], item["resource_id"])
                for item in items
            ]
            if keys(steps) != keys(canonical):
                issues.append(f"{row['case_id']}: steps violate required operation/system ordering")
    return issues


def run() -> int:
    requests = read_csv(DATASET / "deletion_requests.csv")
    if len(requests) != 518:
        raise ValueError(f"Expected 518 scored requests, found {len(requests)}")
    aliases_by_user, users_by_email = build_identity_index()
    rules = {row["data_category"]: row for row in read_csv(DATASET / "deletion_rules.csv")}
    observations, warnings = load_evidence()
    load_identity_link_events(observations)
    evidence_times = {
        row["file_id"]: parse_time(row["captured_at"])
        for row in read_csv(DATASET / "evidence_files.csv")
    }
    evidence_times.update(
        {
            row["activity_id"]: parse_time(row["event_time"])
            for row in read_csv(DATASET / "system_activity.csv")
        }
    )
    evidence_times.update(
        {
            row["audit_id"]: parse_time(row["event_time"])
            for row in read_csv(DATASET / "user_audit_log.csv")
        }
    )
    output_rows: list[dict[str, str]] = []

    for request in requests:
        status, steps = audit_case(
            request,
            aliases_by_user,
            users_by_email,
            observations,
            rules,
        )
        output_rows.append(
            {
                "case_id": request["case_id"],
                "deletion_status": status,
                "steps_json": json.dumps(steps, ensure_ascii=False, separators=(",", ":")),
            }
        )
    output_rows.sort(key=lambda row: row["case_id"])

    issues = validate_output(output_rows, requests, evidence_times)
    if issues:
        raise ValueError("Output validation failed:\n" + "\n".join(issues[:30]))
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("case_id", "deletion_status", "steps_json"))
        writer.writeheader()
        writer.writerows(output_rows)

    statuses: dict[str, int] = {}
    for row in output_rows:
        statuses[row["deletion_status"]] = statuses.get(row["deletion_status"], 0) + 1
    print(f"Wrote {len(output_rows)} rows to {OUTPUT}")
    print("Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    print(f"Parsed evidence observations: {len(observations)}")
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
