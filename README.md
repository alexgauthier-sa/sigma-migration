# Sigma user diff scripts

These scripts load an external user list from JSON, fetch the current Sigma user base through the Sigma REST API, and write a diff report.

## Sigma API configuration

Create Sigma API client credentials owned by an admin user, then export:

```bash
export SIGMA_CLIENT_ID="your-client-id"
export SIGMA_CLIENT_SECRET="your-client-secret"
export SIGMA_BASE_URL="https://api.sigmacomputing.com"
```

`SIGMA_BASE_URL` is optional and defaults to `https://api.sigmacomputing.com`. Use your deployment-specific Sigma API host if different.

## External user JSON or CSV

The input can be a JSON array:

```json
[
  {
    "email": "ada@example.com",
    "firstName": "Ada",
    "lastName": "Lovelace",
    "memberType": "Creator",
    "userKind": "internal"
  }
]
```

It can also be wrapped as `{ "users": [...] }`, `{ "members": [...] }`, `{ "entries": [...] }`, or `{ "data": [...] }`.

Common aliases are normalized, including `Email`, `first_name`, `last_name`, `member_type`, `account_type`, and `user_kind`.

The scripts also accept CSV files with this Sigma and Active Directory mapping format:

```csv
SIGMA_USER_EMAIL,SIGMA_ACCOUNT_TYPE,SIGMA_ACCOUNT_STATUS,ACTIVE_DIRECTORY_USER_PRINCIPAL_NAME,ACTIVE_DIRECTORY_ENABLED,WORKDAY_WORKER_STATUS
xxx@xxxc.com,essential,TRUE,xxx@xxx.com,TRUE,Active
```

CSV mappings:

- `SIGMA_USER_EMAIL` -> `email`
- `SIGMA_ACCOUNT_TYPE` -> `memberType`
- `SIGMA_ACCOUNT_STATUS` -> `isArchived` where `TRUE` means active and not archived
- `ACTIVE_DIRECTORY_USER_PRINCIPAL_NAME` -> `activeDirectoryUserPrincipalName`
- `ACTIVE_DIRECTORY_ENABLED` -> `activeDirectoryEnabled`
- `WORKDAY_WORKER_STATUS` -> `workdayWorkerStatus`

When a CSV omits fields such as `firstName` and `lastName`, those missing fields are skipped during matched-user comparison.

## Run a live Sigma comparison

```bash
python3 scripts/user_migration_driver.py diff \
  --external-users examples/external_users.json \
  --output reports/sigma-user-diff.json \
  --csv reports/sigma-user-diff.csv \
  --include-archived \
  --include-inactive
```

The script calls `GET /v2/members` with `limit=1000` and follows `nextPage` until all members are fetched.

The process exits with code `1` when external users are missing in Sigma or matched users have changed fields. Extra Sigma users are reported but do not fail the process by themselves.

The original comparison script is still available:

```bash
python3 scripts/compare_sigma_users.py --external-users examples/external_users.json
```

## Ingest missing users

Use `ingest` to create users that exist in the JSON file but are missing from Sigma. It previews changes by default:

```bash
python3 scripts/user_migration_driver.py ingest \
  --external-users examples/external_users.json \
  --output reports/sigma-user-diff.json
```

Create the users with:

```bash
python3 scripts/user_migration_driver.py ingest \
  --external-users examples/external_users.json \
  --output reports/sigma-user-diff.json \
  --apply
```

By default, Sigma invitations are sent for created non-embed users. Disable them with `--no-send-invite`.

## Delete duplicates

For duplicate rows in the external JSON, write a deduped file:

```bash
python3 scripts/user_migration_driver.py dedupe-json \
  --external-users users.json \
  --output users.deduped.json \
  --keep first
```

For duplicate users already in Sigma, preview which member records would be deactivated:

```bash
python3 scripts/user_migration_driver.py deactivate-duplicate-sigma-users \
  --keep oldest \
  --include-archived \
  --include-inactive
```

Apply the Sigma duplicate cleanup with:

```bash
python3 scripts/user_migration_driver.py deactivate-duplicate-sigma-users \
  --keep oldest \
  --include-archived \
  --include-inactive \
  --apply
```

Sigma users are deactivated rather than permanently deleted because Sigma's member API supports deactivation for user removal.

## Audit workbook and data model access

Generate a report showing the owner and access grants for all workbooks and data models:

```bash
python3 scripts/audit_content_access.py \
  --output reports/sigma-content-access-audit.json \
  --csv reports/sigma-content-access-audit.csv \
  --skip-permission-check
```

Limit the audit to one content type:

```bash
python3 scripts/audit_content_access.py --content-type workbooks
python3 scripts/audit_content_access.py --content-type data-models
```

Use `--direct-grants-only` to exclude inherited access where Sigma supports it. The CSV contains one row per owner or grant relationship.

## Compare fields

By default, matched users are compared on:

- `firstName`
- `lastName`
- `memberType`
- `userKind`
- `isArchived`
- `isInactive`

Override this with repeated `--field` flags:

```bash
python3 scripts/compare_sigma_users.py \
  --external-users users.json \
  --field firstName \
  --field lastName \
  --field memberType
```

## Offline comparison

For local testing or saved exports, pass a Sigma members JSON file and the API will not be called:

```bash
python3 scripts/compare_sigma_users.py \
  --external-users users.json \
  --sigma-users sigma-members.json \
  --output sigma-user-diff.json
```

## Run tests

```bash
python3 -m unittest discover -s tests
```
