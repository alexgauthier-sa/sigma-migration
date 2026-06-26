"""Diff external users against Sigma members."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_COMPARE_FIELDS = (
    "firstName",
    "lastName",
    "memberType",
    "userKind",
    "isArchived",
    "isInactive",
)

FIELD_ALIASES = {
    "email": ("email", "Email", "mail", "userName", "username", "SIGMA_USER_EMAIL"),
    "firstName": ("firstName", "first_name", "First Name", "givenName", "given_name"),
    "lastName": ("lastName", "last_name", "Last Name", "familyName", "family_name"),
    "memberType": (
        "memberType",
        "member_type",
        "accountType",
        "account_type",
        "SIGMA_ACCOUNT_TYPE",
    ),
    "userKind": ("userKind", "user_kind", "kind"),
    "isArchived": ("isArchived", "is_archived", "archived"),
    "isInactive": ("isInactive", "is_inactive", "inactive"),
    "activeDirectoryUserPrincipalName": (
        "activeDirectoryUserPrincipalName",
        "ACTIVE_DIRECTORY_USER_PRINCIPAL_NAME",
    ),
    "activeDirectoryEnabled": ("activeDirectoryEnabled", "ACTIVE_DIRECTORY_ENABLED"),
    "workdayWorkerStatus": ("workdayWorkerStatus", "WORKDAY_WORKER_STATUS"),
}


@dataclass(frozen=True)
class UserDiff:
    summary: dict[str, int]
    missing_in_sigma: list[dict[str, Any]]
    extra_in_sigma: list[dict[str, Any]]
    changed: list[dict[str, Any]]
    duplicate_external_emails: list[str]
    duplicate_sigma_emails: list[str]


def load_external_users(path: str | Path) -> list[dict[str, Any]]:
    input_path = Path(path)
    if input_path.suffix.lower() == ".csv":
        return load_external_users_csv(input_path)

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        users = payload
    elif isinstance(payload, dict):
        users = _first_list(payload, ("users", "members", "entries", "data"))
    else:
        raise ValueError("External users JSON must be an array or an object containing a user array")

    if not all(isinstance(user, dict) for user in users):
        raise ValueError("Every external user entry must be a JSON object")

    return [normalize_user(user) for user in users]


def load_external_users_csv(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("External users CSV must include a header row")
        return [normalize_user(row) for row in reader if _has_values(row)]


def deduplicate_users(
    users: Iterable[dict[str, Any]],
    *,
    keep: str = "first",
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if keep not in {"first", "last"}:
        raise ValueError("keep must be 'first' or 'last'")

    normalized = [normalize_user(user) for user in users]
    groups = group_users_by_email(normalized)
    deduped: list[dict[str, Any]] = []
    duplicates: dict[str, list[dict[str, Any]]] = {}
    email_order = list(dict.fromkeys(user["email"] for user in normalized))

    for email in email_order:
        entries = groups[email]
        if len(entries) > 1:
            duplicates[email] = entries
        deduped.append(entries[0] if keep == "first" else entries[-1])

    return deduped, duplicates


def group_users_by_email(users: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for user in users:
        normalized = normalize_user(user)
        groups.setdefault(normalized["email"], []).append(normalized)
    return groups


def write_users_json(path: str | Path, users: Iterable[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(list(users), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def diff_users(
    external_users: Iterable[dict[str, Any]],
    sigma_users: Iterable[dict[str, Any]],
    compare_fields: Iterable[str] = DEFAULT_COMPARE_FIELDS,
) -> UserDiff:
    external = [normalize_user(user) for user in external_users]
    sigma = [normalize_user(user) for user in sigma_users]
    external_by_email, duplicate_external = _index_by_email(external)
    sigma_by_email, duplicate_sigma = _index_by_email(sigma)

    external_emails = set(external_by_email)
    sigma_emails = set(sigma_by_email)
    fields = tuple(compare_fields)

    missing = [external_by_email[email] for email in sorted(external_emails - sigma_emails)]
    extra = [sigma_by_email[email] for email in sorted(sigma_emails - external_emails)]

    changed: list[dict[str, Any]] = []
    for email in sorted(external_emails & sigma_emails):
        external_user = external_by_email[email]
        sigma_user = sigma_by_email[email]
        field_changes = {}
        for field in fields:
            if field not in external_user:
                continue
            external_value = external_user.get(field)
            sigma_value = sigma_user.get(field)
            if _canonical_value(external_value) != _canonical_value(sigma_value):
                field_changes[field] = {
                    "external": external_value,
                    "sigma": sigma_value,
                }

        if field_changes:
            changed.append(
                {
                    "email": email,
                    "external": external_user,
                    "sigma": sigma_user,
                    "differences": field_changes,
                }
            )

    return UserDiff(
        summary={
            "external_users": len(external),
            "sigma_users": len(sigma),
            "matched_users": len(external_emails & sigma_emails),
            "missing_in_sigma": len(missing),
            "extra_in_sigma": len(extra),
            "changed": len(changed),
            "duplicate_external_emails": len(duplicate_external),
            "duplicate_sigma_emails": len(duplicate_sigma),
        },
        missing_in_sigma=missing,
        extra_in_sigma=extra,
        changed=changed,
        duplicate_external_emails=duplicate_external,
        duplicate_sigma_emails=duplicate_sigma,
    )


def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: value for key, value in user.items() if value != ""}
    for canonical, aliases in FIELD_ALIASES.items():
        value = _lookup(user, aliases)
        if value not in (None, ""):
            normalized[canonical] = value

    _normalize_sigma_ad_mapping_fields(normalized)

    email = normalized.get("email")
    if not isinstance(email, str) or not email.strip():
        raise ValueError(f"User is missing a usable email field: {user}")
    normalized["email"] = email.strip().lower()
    return normalized


def write_json_report(path: str | Path, diff: UserDiff) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": diff.summary,
        "missingInSigma": diff.missing_in_sigma,
        "extraInSigma": diff.extra_in_sigma,
        "changed": diff.changed,
        "duplicateExternalEmails": diff.duplicate_external_emails,
        "duplicateSigmaEmails": diff.duplicate_sigma_emails,
    }
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv_summary(path: str | Path, diff: UserDiff) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("status", "email", "field", "external", "sigma"),
        )
        writer.writeheader()
        for user in diff.missing_in_sigma:
            writer.writerow(
                {
                    "status": "missing_in_sigma",
                    "email": user["email"],
                    "field": "",
                    "external": json.dumps(user, sort_keys=True),
                    "sigma": "",
                }
            )
        for user in diff.extra_in_sigma:
            writer.writerow(
                {
                    "status": "extra_in_sigma",
                    "email": user["email"],
                    "field": "",
                    "external": "",
                    "sigma": json.dumps(user, sort_keys=True),
                }
            )
        for entry in diff.changed:
            for field, values in entry["differences"].items():
                writer.writerow(
                    {
                        "status": "changed",
                        "email": entry["email"],
                        "field": field,
                        "external": json.dumps(values["external"], sort_keys=True),
                        "sigma": json.dumps(values["sigma"], sort_keys=True),
                    }
                )


def _first_list(payload: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise ValueError(f"External users JSON object must contain one of: {', '.join(keys)}")


def _lookup(user: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in user:
            return user[alias]
    return None


def _has_values(row: dict[str, Any]) -> bool:
    return any(value not in (None, "") for value in row.values())


def _normalize_sigma_ad_mapping_fields(user: dict[str, Any]) -> None:
    if "SIGMA_ACCOUNT_STATUS" in user and "isArchived" not in user:
        active = _coerce_bool(user["SIGMA_ACCOUNT_STATUS"])
        if active is not None:
            user["isArchived"] = not active

    if "activeDirectoryEnabled" in user:
        enabled = _coerce_bool(user["activeDirectoryEnabled"])
        if enabled is not None:
            user["activeDirectoryEnabled"] = enabled

    if "memberType" in user and isinstance(user["memberType"], str):
        user["memberType"] = user["memberType"].strip()


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "t", "yes", "y", "1", "active", "enabled"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", "inactive", "disabled"}:
        return False
    return None


def _index_by_email(users: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for user in users:
        email = user["email"]
        if email in indexed:
            duplicates.append(email)
            continue
        indexed[email] = user
    return indexed, sorted(set(duplicates))


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value
