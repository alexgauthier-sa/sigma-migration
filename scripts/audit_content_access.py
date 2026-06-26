#!/usr/bin/env python3
"""Audit workbook and data model ownership and access in Sigma."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sigma_api import SigmaClient, SigmaConfig


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report owners and access grants for all Sigma workbooks and data models."
    )
    parser.add_argument(
        "--output",
        default="reports/sigma-content-access-audit.json",
        help="Path to write the JSON audit report.",
    )
    parser.add_argument(
        "--csv",
        default="reports/sigma-content-access-audit.csv",
        help="Path to write the flattened CSV audit report. Use an empty string to skip CSV.",
    )
    parser.add_argument(
        "--content-type",
        choices=("all", "workbooks", "data-models"),
        default="all",
        help="Which content to audit.",
    )
    parser.add_argument("--include-archived-workbooks", action="store_true")
    parser.add_argument("--exclude-explorations", action="store_true")
    parser.add_argument(
        "--skip-permission-check",
        action="store_true",
        help="Pass skipPermissionCheck=true to supported list endpoints.",
    )
    parser.add_argument(
        "--direct-grants-only",
        action="store_true",
        help="Only report direct grants, excluding inherited access where Sigma supports it.",
    )
    parser.add_argument("--limit", type=int, default=1000, help="Page size for Sigma list endpoints.")
    args = parser.parse_args()

    client = SigmaClient(SigmaConfig.from_env())
    members = client.list_members(include_archived=True, include_inactive=True, limit=args.limit)
    teams = client.list_teams(limit=args.limit)
    member_by_id = {member.get("memberId"): member for member in members if member.get("memberId")}
    team_by_id = {team.get("teamId"): team for team in teams if team.get("teamId")}

    assets: list[dict[str, Any]] = []
    if args.content_type in {"all", "workbooks"}:
        assets.extend(_asset("workbook", workbook) for workbook in _load_workbooks(client, args))
    if args.content_type in {"all", "data-models"}:
        assets.extend(
            _asset("data_model", data_model)
            for data_model in client.list_data_models(
                skip_permission_check=args.skip_permission_check,
                limit=args.limit,
            )
        )

    audited_assets = []
    for asset in assets:
        grants = (
            client.list_grants(
                inode_id=asset["id"],
                direct_grants_only=args.direct_grants_only,
                limit=args.limit,
            )
            if asset["id"]
            else []
        )
        audited_assets.append(
            {
                **asset,
                "owner": _member_summary(asset.get("ownerId"), member_by_id),
                "grants": [
                    _grant_summary(grant, member_by_id=member_by_id, team_by_id=team_by_id)
                    for grant in grants
                ],
            }
        )

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "assets": len(audited_assets),
            "workbooks": sum(1 for asset in audited_assets if asset["type"] == "workbook"),
            "dataModels": sum(1 for asset in audited_assets if asset["type"] == "data_model"),
            "grants": sum(len(asset["grants"]) for asset in audited_assets),
            "membersLoaded": len(members),
            "teamsLoaded": len(teams),
            "directGrantsOnly": args.direct_grants_only,
        },
        "assets": audited_assets,
    }

    _write_json(args.output, report)
    if args.csv:
        _write_csv(args.csv, audited_assets)

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"Wrote JSON audit: {Path(args.output).resolve()}")
    if args.csv:
        print(f"Wrote CSV audit: {Path(args.csv).resolve()}")
    return 0


def _load_workbooks(client: SigmaClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    active_workbooks = client.list_workbooks(
        is_archived=False,
        exclude_explorations=args.exclude_explorations,
        skip_permission_check=args.skip_permission_check,
        limit=args.limit,
    )
    if not args.include_archived_workbooks:
        return active_workbooks

    archived_workbooks = client.list_workbooks(
        is_archived=True,
        exclude_explorations=args.exclude_explorations,
        skip_permission_check=args.skip_permission_check,
        limit=args.limit,
    )
    by_id = {
        workbook.get("workbookId"): workbook
        for workbook in active_workbooks + archived_workbooks
        if workbook.get("workbookId")
    }
    return list(by_id.values())


def _asset(asset_type: str, item: dict[str, Any]) -> dict[str, Any]:
    id_field = "workbookId" if asset_type == "workbook" else "dataModelId"
    return {
        "type": asset_type,
        "id": item.get(id_field),
        "name": item.get("name"),
        "path": item.get("path"),
        "url": item.get("url"),
        "ownerId": item.get("ownerId"),
        "createdBy": item.get("createdBy"),
        "updatedBy": item.get("updatedBy"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "isArchived": item.get("isArchived"),
    }


def _grant_summary(
    grant: dict[str, Any],
    *,
    member_by_id: dict[str, dict[str, Any]],
    team_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    member_id = grant.get("memberId")
    team_id = grant.get("teamId")
    principal_type = "member" if member_id else "team" if team_id else "unknown"
    principal = (
        _member_summary(member_id, member_by_id)
        if member_id
        else _team_summary(team_id, team_by_id)
        if team_id
        else None
    )
    return {
        "grantId": grant.get("grantId"),
        "permission": grant.get("permission"),
        "principalType": principal_type,
        "principal": principal,
        "memberId": member_id,
        "teamId": team_id,
        "createdBy": grant.get("createdBy"),
        "updatedBy": grant.get("updatedBy"),
        "createdAt": grant.get("createdAt"),
        "updatedAt": grant.get("updatedAt"),
    }


def _member_summary(member_id: Any, member_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(member_id, str) or not member_id:
        return None
    member = member_by_id.get(member_id, {})
    return {
        "memberId": member_id,
        "email": member.get("email"),
        "firstName": member.get("firstName"),
        "lastName": member.get("lastName"),
        "memberType": member.get("memberType"),
        "isArchived": member.get("isArchived"),
        "isInactive": member.get("isInactive"),
    }


def _team_summary(team_id: Any, team_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(team_id, str) or not team_id:
        return None
    team = team_by_id.get(team_id, {})
    return {
        "teamId": team_id,
        "name": team.get("name"),
        "visibility": team.get("visibility"),
        "isArchived": team.get("isArchived"),
    }


def _write_json(path: str | Path, report: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: str | Path, assets: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "asset_type",
                "asset_id",
                "asset_name",
                "asset_path",
                "asset_url",
                "relationship",
                "permission",
                "principal_type",
                "principal_id",
                "principal_name",
                "principal_email",
                "grant_id",
                "created_at",
                "updated_at",
            ),
        )
        writer.writeheader()
        for asset in assets:
            owner = asset.get("owner") or {}
            writer.writerow(
                _csv_row(
                    asset,
                    relationship="owner",
                    permission="owner",
                    principal_type="member",
                    principal_id=owner.get("memberId") or asset.get("ownerId"),
                    principal_name=_display_member(owner),
                    principal_email=owner.get("email"),
                )
            )
            for grant in asset["grants"]:
                principal = grant.get("principal") or {}
                writer.writerow(
                    _csv_row(
                        asset,
                        relationship="grant",
                        permission=grant.get("permission"),
                        principal_type=grant.get("principalType"),
                        principal_id=grant.get("memberId") or grant.get("teamId"),
                        principal_name=(
                            _display_member(principal)
                            if grant.get("principalType") == "member"
                            else principal.get("name")
                        ),
                        principal_email=principal.get("email"),
                        grant_id=grant.get("grantId"),
                        created_at=grant.get("createdAt"),
                        updated_at=grant.get("updatedAt"),
                    )
                )


def _csv_row(
    asset: dict[str, Any],
    *,
    relationship: str,
    permission: Any,
    principal_type: Any,
    principal_id: Any,
    principal_name: Any,
    principal_email: Any,
    grant_id: Any = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict[str, Any]:
    return {
        "asset_type": asset.get("type"),
        "asset_id": asset.get("id"),
        "asset_name": asset.get("name"),
        "asset_path": asset.get("path"),
        "asset_url": asset.get("url"),
        "relationship": relationship,
        "permission": permission,
        "principal_type": principal_type,
        "principal_id": principal_id,
        "principal_name": principal_name,
        "principal_email": principal_email,
        "grant_id": grant_id,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _display_member(member: dict[str, Any]) -> str | None:
    name = " ".join(
        part
        for part in (member.get("firstName"), member.get("lastName"))
        if isinstance(part, str) and part
    ).strip()
    return name or member.get("email")


if __name__ == "__main__":
    raise SystemExit(main())
