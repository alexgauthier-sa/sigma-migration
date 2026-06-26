import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.diff_users import (
    deduplicate_users,
    diff_users,
    group_users_by_email,
    load_external_users,
    normalize_user,
)


class DiffUsersTest(unittest.TestCase):
    def test_normalize_user_accepts_common_aliases(self):
        user = normalize_user(
            {
                "Email": " ADA@EXAMPLE.COM ",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "account_type": "Creator",
            }
        )

        self.assertEqual(user["email"], "ada@example.com")
        self.assertEqual(user["firstName"], "Ada")
        self.assertEqual(user["lastName"], "Lovelace")
        self.assertEqual(user["memberType"], "Creator")

    def test_diff_reports_missing_extra_and_changed_users(self):
        external = [
            {"email": "ada@example.com", "firstName": "Ada", "lastName": "Lovelace", "memberType": "Creator"},
            {"email": "grace@example.com", "firstName": "Grace", "lastName": "Hopper", "memberType": "Viewer"},
        ]
        sigma = [
            {"email": "ada@example.com", "firstName": "Ada", "lastName": "Byron", "memberType": "Creator"},
            {"email": "alan@example.com", "firstName": "Alan", "lastName": "Turing", "memberType": "Creator"},
        ]

        report = diff_users(external, sigma)

        self.assertEqual(report.summary["matched_users"], 1)
        self.assertEqual(report.summary["missing_in_sigma"], 1)
        self.assertEqual(report.summary["extra_in_sigma"], 1)
        self.assertEqual(report.summary["changed"], 1)
        self.assertEqual(report.missing_in_sigma[0]["email"], "grace@example.com")
        self.assertEqual(report.extra_in_sigma[0]["email"], "alan@example.com")
        self.assertEqual(report.changed[0]["differences"]["lastName"]["external"], "Lovelace")
        self.assertEqual(report.changed[0]["differences"]["lastName"]["sigma"], "Byron")

    def test_duplicate_emails_are_reported(self):
        report = diff_users(
            [{"email": "ada@example.com"}, {"email": "ADA@example.com"}],
            [{"email": "ada@example.com"}],
        )

        self.assertEqual(report.duplicate_external_emails, ["ada@example.com"])
        self.assertEqual(report.summary["duplicate_external_emails"], 1)

    def test_deduplicate_users_can_keep_last(self):
        deduped, duplicates = deduplicate_users(
            [
                {"email": "ada@example.com", "firstName": "Ada"},
                {"email": "ADA@example.com", "firstName": "Augusta"},
            ],
            keep="last",
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["firstName"], "Augusta")
        self.assertEqual(list(duplicates), ["ada@example.com"])

    def test_group_users_by_email_normalizes_email(self):
        groups = group_users_by_email(
            [{"email": " ADA@example.com "}, {"Email": "ada@EXAMPLE.com"}]
        )

        self.assertEqual(len(groups["ada@example.com"]), 2)

    def test_load_external_users_accepts_sigma_ad_mapping_csv(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "users.csv"
            path.write_text(
                "\n".join(
                    [
                        "SIGMA_USER_EMAIL,SIGMA_ACCOUNT_TYPE,SIGMA_ACCOUNT_STATUS,ACTIVE_DIRECTORY_USER_PRINCIPAL_NAME,ACTIVE_DIRECTORY_ENABLED,WORKDAY_WORKER_STATUS",
                        "AC.Hassall@ccm.com,essential,TRUE,AC.Hassall@myccmortgage.com,TRUE,Active",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            users = load_external_users(path)

        self.assertEqual(users[0]["email"], "ac.hassall@ccm.com")
        self.assertEqual(users[0]["memberType"], "essential")
        self.assertEqual(users[0]["isArchived"], False)
        self.assertEqual(users[0]["activeDirectoryUserPrincipalName"], "AC.Hassall@myccmortgage.com")
        self.assertEqual(users[0]["activeDirectoryEnabled"], True)
        self.assertEqual(users[0]["workdayWorkerStatus"], "Active")

    def test_diff_skips_fields_missing_from_external_user(self):
        report = diff_users(
            [{"email": "ada@example.com", "memberType": "Creator"}],
            [{"email": "ada@example.com", "firstName": "Ada", "lastName": "Lovelace", "memberType": "Creator"}],
        )

        self.assertEqual(report.summary["changed"], 0)


if __name__ == "__main__":
    unittest.main()
