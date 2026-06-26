#!/usr/bin/env python3
"""Driver for Sigma user diff, ingest, and duplicate cleanup workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from diff_users import (
    DEFAULT_COMPARE_FIELDS,
    deduplicate_users,
    diff_users,
    group_users_by_email,
    load_external_users,
    write_csv_summary,
    write_json_report,
    write_users_json,
)
from sigma_api import SigmaClient, SigmaConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Sigma user migration driver.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diff_parser = subparsers.add_parser("diff", help="Compare external JSON users to Sigma.")
    _add_compare_args(diff_parser)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Create Sigma members that exist in the external JSON but not in Sigma.",
    )
    _add_compare_args(ingest_parser)
    ingest_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create missing users in Sigma. Without this flag, prints a dry run.",
    )
    ingest_parser.add_argument(
        "--send-invite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send Sigma invitations for created non-embed users. Defaults to true.",
    )

    dedupe_json_parser = subparsers.add_parser(
        "dedupe-json",
        help="Remove duplicate emails from an external users JSON file.",
    )
    dedupe_json_parser.add_argument("--external-users", required=True)
    dedupe_json_parser.add_argument("--output", required=True)
    dedupe_json_parser.add_argument("--keep", choices=("first", "last"), default="first")

    dedupe_sigma_parser = subparsers.add_parser(
        "deactivate-duplicate-sigma-users",
        help="Deactivate duplicate Sigma users by email, keeping one record per email.",
    )
    dedupe_sigma_parser.add_argument("--keep", choices=("first", "oldest", "newest"), default="first")
    dedupe_sigma_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually deactivate duplicate Sigma users. Without this flag, prints a dry run.",
    )
    dedupe_sigma_parser.add_argument("--include-archived", action="store_true")
    dedupe_sigma_parser.add_argument("--include-inactive", action="store_true")
    dedupe_sigma_parser.add_argument("--limit", type=int, default=1000)

    args = parser.parse_args()

    if args.command == "diff":
        return run_diff(args)
    if args.command == "ingest":
        return run_ingest(args)
    if args.command == "dedupe-json":
        return run_dedupe_json(args)
    if args.command == "deactivate-duplicate-sigma-users":
        return run_deactivate_duplicate_sigma_users(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


def run_diff(args: argparse.Namespace) -> int:
    external_users = load_external_users(args.external_users)
    sigma_users = _load_sigma_users(args)
    report = diff_users(external_users, sigma_users, args.fields or DEFAULT_COMPARE_FIELDS)
    write_json_report(args.output, report)
    if args.csv:
        write_csv_summary(args.csv, report)
    print(json.dumps(report.summary, indent=2, sort_keys=True))
    print(f"Wrote JSON report: {Path(args.output).resolve()}")
    if args.csv:
        print(f"Wrote CSV summary: {Path(args.csv).resolve()}")
    return 1 if report.summary["missing_in_sigma"] or report.summary["changed"] else 0


def run_ingest(args: argparse.Namespace) -> int:
    external_users = load_external_users(args.external_users)
    sigma_users = _load_sigma_users(args)
    report = diff_users(external_users, sigma_users, args.fields or DEFAULT_COMPARE_FIELDS)
    write_json_report(args.output, report)
    if args.csv:
        write_csv_summary(args.csv, report)

    missing_users = report.missing_in_sigma
    print(json.dumps(report.summary, indent=2, sort_keys=True))
    if not missing_users:
        print("No missing users to ingest.")
        return 0

    if not args.apply:
        print("Dry run: these users would be created in Sigma:")
        for user in missing_users:
            print(f"- {user['email']}")
        print("Re-run with --apply to create them.")
        return 1

    client = SigmaClient(SigmaConfig.from_env())
    created = []
    for user in missing_users:
        _validate_user_for_create(user)
        created.append(client.create_member(user, send_invite=args.send_invite))

    print(f"Created {len(created)} Sigma user(s).")
    return 0


def run_dedupe_json(args: argparse.Namespace) -> int:
    users = load_external_users(args.external_users)
    deduped, duplicates = deduplicate_users(users, keep=args.keep)
    write_users_json(args.output, deduped)
    print(
        json.dumps(
            {
                "input_users": len(users),
                "output_users": len(deduped),
                "duplicate_email_groups": len(duplicates),
                "removed_users": len(users) - len(deduped),
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"Wrote deduped users: {Path(args.output).resolve()}")
    return 1 if duplicates else 0


def run_deactivate_duplicate_sigma_users(args: argparse.Namespace) -> int:
    client = SigmaClient(SigmaConfig.from_env())
    users = client.list_members(
        include_archived=args.include_archived,
        include_inactive=args.include_inactive,
        limit=args.limit,
    )
    groups = {
        email: entries
        for email, entries in group_users_by_email(users).items()
        if len(entries) > 1
    }

    planned = []
    for email, entries in sorted(groups.items()):
        keeper = _select_keeper(entries, args.keep)
        for user in entries:
            if user.get("memberId") != keeper.get("memberId"):
                planned.append({"email": email, "keep": keeper, "deactivate": user})

    print(
        json.dumps(
            {
                "duplicate_email_groups": len(groups),
                "users_to_deactivate": len(planned),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if not planned:
        return 0

    if not args.apply:
        print("Dry run: these Sigma users would be deactivated:")
        for item in planned:
            user = item["deactivate"]
            keeper = item["keep"]
            print(
                f"- {item['email']}: deactivate {user.get('memberId')} "
                f"and keep {keeper.get('memberId')}"
            )
        print("Re-run with --apply to deactivate duplicate Sigma users.")
        return 1

    for item in planned:
        member_id = item["deactivate"].get("memberId")
        if not isinstance(member_id, str) or not member_id:
            raise ValueError(f"Duplicate user is missing memberId: {item['deactivate']}")
        client.deactivate_member(member_id)

    print(f"Deactivated {len(planned)} duplicate Sigma user(s).")
    return 0


def _add_compare_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--external-users", required=True, help="Path to external users JSON.")
    parser.add_argument("--output", default="sigma-user-diff.json", help="JSON report path.")
    parser.add_argument("--csv", help="Optional flattened CSV report path.")
    parser.add_argument("--sigma-users", help="Optional saved Sigma members JSON file.")
    parser.add_argument(
        "--field",
        action="append",
        dest="fields",
        help=(
            "Field to compare for matched users. Can be repeated. "
            f"Defaults to: {', '.join(DEFAULT_COMPARE_FIELDS)}."
        ),
    )
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)


def _load_sigma_users(args: argparse.Namespace) -> list[dict[str, Any]]:
    if getattr(args, "sigma_users", None):
        with Path(args.sigma_users).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            return payload["entries"]
        raise ValueError("--sigma-users must be a JSON array or an object with an entries array")

    client = SigmaClient(SigmaConfig.from_env())
    return client.list_members(
        include_archived=args.include_archived,
        include_inactive=args.include_inactive,
        limit=args.limit,
    )


def _validate_user_for_create(user: dict[str, Any]) -> None:
    missing = [
        field
        for field in ("email", "firstName", "lastName")
        if not isinstance(user.get(field), str) or not user[field].strip()
    ]
    if missing:
        raise ValueError(
            f"Cannot create {user.get('email', '<unknown email>')}; missing {', '.join(missing)}"
        )


def _select_keeper(users: list[dict[str, Any]], keep: str) -> dict[str, Any]:
    if keep == "first":
        return users[0]
    if keep == "oldest":
        return min(users, key=lambda user: str(user.get("createdAt", "")))
    if keep == "newest":
        return max(users, key=lambda user: str(user.get("createdAt", "")))
    raise ValueError("keep must be first, oldest, or newest")


if __name__ == "__main__":
    raise SystemExit(main())
