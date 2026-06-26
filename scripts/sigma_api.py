"""Small Sigma REST API client used by the migration scripts."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class SigmaApiError(RuntimeError):
    """Raised when Sigma returns an unsuccessful response."""


@dataclass(frozen=True)
class SigmaConfig:
    base_url: str
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> "SigmaConfig":
        client_id = os.environ.get("SIGMA_CLIENT_ID")
        client_secret = os.environ.get("SIGMA_CLIENT_SECRET")
        missing = [
            name
            for name, value in (
                ("SIGMA_CLIENT_ID", client_id),
                ("SIGMA_CLIENT_SECRET", client_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")

        return cls(
            base_url=os.environ.get("SIGMA_BASE_URL", "https://api.sigmacomputing.com"),
            client_id=client_id or "",
            client_secret=client_secret or "",
        )


class SigmaClient:
    def __init__(self, config: SigmaConfig, timeout: int = 60) -> None:
        self.base_url = config.base_url.rstrip("/")
        self.client_id = config.client_id
        self.client_secret = config.client_secret
        self.timeout = timeout
        self._access_token: str | None = None

    def get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        credentials = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic_token = base64.b64encode(credentials).decode("ascii")
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v2/auth/token",
            data=body,
            headers={
                "Authorization": f"Basic {basic_token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        response = self._open_json(request)
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise SigmaApiError("Token response did not include access_token")
        self._access_token = token
        return token

    def list_members(
        self,
        *,
        include_archived: bool = False,
        include_inactive: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        members: list[dict[str, Any]] = []
        page: str | None = None

        while True:
            params: dict[str, Any] = {
                "limit": str(limit),
                "includeArchived": str(include_archived).lower(),
                "includeInactive": str(include_inactive).lower(),
            }
            if page:
                params["page"] = page

            response = self.get("/v2/members", params)
            if isinstance(response, list):
                members.extend(response)
                break

            if not isinstance(response, dict):
                raise SigmaApiError("Unexpected /v2/members response shape")

            entries = response.get("entries", [])
            if not isinstance(entries, list):
                raise SigmaApiError("Unexpected /v2/members entries shape")
            members.extend(entries)

            next_page = response.get("nextPage")
            if not next_page:
                break
            page = str(next_page)

        return members

    def list_teams(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        return self._list_paginated("/v2/teams", {"limit": str(limit)})

    def list_workbooks(
        self,
        *,
        is_archived: bool | None = False,
        exclude_explorations: bool = False,
        skip_permission_check: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params = {
            "limit": str(limit),
            "excludeExplorations": str(exclude_explorations).lower(),
            "skipPermissionCheck": str(skip_permission_check).lower(),
        }
        if is_archived is not None:
            params["isArchived"] = str(is_archived).lower()
        return self._list_paginated(
            "/v2/workbooks",
            params,
        )

    def list_data_models(
        self,
        *,
        skip_permission_check: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return self._list_paginated(
            "/v2/dataModels",
            {
                "limit": str(limit),
                "skipPermissionCheck": str(skip_permission_check).lower(),
            },
        )

    def list_grants(
        self,
        *,
        inode_id: str,
        direct_grants_only: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        return self._list_paginated(
            "/v2/grants",
            {
                "limit": str(limit),
                "inodeId": inode_id,
                "directGrantsOnly": str(direct_grants_only).lower(),
            },
        )

    def create_member(self, user: dict[str, Any], *, send_invite: bool = True) -> dict[str, Any]:
        params = {"sendInvite": str(send_invite).lower()}
        payload: dict[str, Any] = {
            "email": user["email"],
            "firstName": user["firstName"],
            "lastName": user["lastName"],
        }
        for field in ("memberType", "isGuest", "userKind", "addToTeams"):
            if field in user and user[field] is not None:
                payload[field] = user[field]
        response = self.post("/v2/members", payload, params)
        if not isinstance(response, dict):
            raise SigmaApiError("Unexpected create member response shape")
        return response

    def deactivate_member(self, member_id: str) -> None:
        self.delete(f"/v2/members/{urllib.parse.quote(member_id, safe='')}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request_json("GET", path, params=params)

    def post(self, path: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        return self._request_json("POST", path, params=params, body=body)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request_json("DELETE", path, params=params)

    def _list_paginated(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        page: str | None = None

        while True:
            request_params = dict(params)
            if page:
                request_params["page"] = page
            response = self.get(path, request_params)
            if isinstance(response, list):
                entries.extend(response)
                break
            if not isinstance(response, dict):
                raise SigmaApiError(f"Unexpected {path} response shape")

            page_entries = response.get("entries", [])
            if not isinstance(page_entries, list):
                raise SigmaApiError(f"Unexpected {path} entries shape")
            entries.extend(page_entries)

            next_page = response.get("nextPage")
            if not next_page:
                break
            page = str(next_page)

        return entries

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        return self._open_json(request)

    def _open_json(self, request: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise SigmaApiError(
                f"Sigma API request failed with HTTP {exc.code}: {details}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SigmaApiError(f"Sigma API request failed: {exc}") from exc

        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SigmaApiError(f"Sigma API returned invalid JSON: {payload[:500]}") from exc
