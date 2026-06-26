#!/usr/bin/env python3
"""Compare external JSON or CSV users to the current Sigma member list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diff_users import DEFAULT_COMPARE_FIELDS, diff_users, load_external_users, write_csv_summary, write_json_report
from sigma_api import SigmaClient, SigmaConfig


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare external users in JSON or CSV format to Sigma members and write a diff report.",
        epilog=(
            "CSV input supports the Sigma + Active Directory mapping headers: "
            "SIGMA_USER_EMAIL, SIGMA_ACCOUNT_TYPE, SIGMA_ACCOUNT_STATUS, "
            "ACTIVE_DIRECTORY_USER_PRINCIPAL_NAME, ACTIVE_DIRECTORY_ENABLED, "
            "WORKDAY_WORKER_STATUS."
        ),
    )
    parser.add_argument(
        "--external-users",
        required=True,
        help="Path to external users JSON or CSV.",
    )
    parser.add_argument(
        "--output",
        default="sigma-user-diff.json",
        help="Path to write the JSON diff report. Defaults to sigma-user-diff.json.",
    )
    parser.add_argument(
        "--csv",
        help="Optional path to write a flattened CSV summary of missing/extra/changed users.",
    )
    parser.add_argument(
        "--sigma-users",
        help="Optional path to a saved Sigma members JSON file. When provided, the API is not called.",
    )
    parser.add_argument(
        "--field",
        action="append",
        dest="fields",
        help=(
            "Field to compare for matched users. Can be repeated. "
            f"Defaults to: {', '.join(DEFAULT_COMPARE_FIELDS)}."
        ),
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include Sigma users with isArchived=true when calling the API.",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include Sigma users with isInactive=true when calling the API.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Sigma page size for /v2/members. Maximum supported by Sigma is 1000.",
    )
    args = parser.parse_args()

    external_users = load_external_users(args.external_users)
    sigma_users = _load_sigma_users(args)
    report = diff_users(
        external_users,
        sigma_users,
        compare_fields=args.fields or DEFAULT_COMPARE_FIELDS,
    )

    write_json_report(args.output, report)
    if args.csv:
        write_csv_summary(args.csv, report)

    print(json.dumps(report.summary, indent=2, sort_keys=True))
    print(f"Wrote JSON report: {Path(args.output).resolve()}")
    if args.csv:
        print(f"Wrote CSV summary: {Path(args.csv).resolve()}")
    return 1 if report.summary["missing_in_sigma"] or report.summary["changed"] else 0


def _load_sigma_users(args: argparse.Namespace) -> list[dict]:
    if args.sigma_users:
        with Path(args.sigma_users).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            return payload["entries"]
        raise ValueError("--sigma-users must be a JSON array or an object with an entries array")

    config = SigmaConfig.from_env()
    client = SigmaClient(config)
    return client.list_members(
        include_archived=args.include_archived,
        include_inactive=args.include_inactive,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
