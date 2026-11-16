import unittest

from scripts.ci_preflight import validate


class PreflightTests(unittest.TestCase):
    def valid_env(self):
        return {
            "STUID": "student",
            "UISPSW": "password",
            "UIS_TOTP_SECRET": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            "COURSE_IDS": "40121,39173",
            "SMTP_EMAIL": "sender@example.com",
            "SMTP_PASSWORD": "smtp-token",
            "RECEIVER_EMAIL": "receiver@example.com",
            "DEEPSEEK_API_KEY": "configured",
        }

    def test_accepts_complete_deepseek_configuration(self):
        self.assertEqual(validate(self.valid_env()), [])

    def test_reports_missing_required_and_model_secrets(self):
        errors = validate({})
        self.assertTrue(any("STUID" in message for message in errors))
        self.assertTrue(any("DEEPSEEK_API_KEY" in message for message in errors))

    def test_rejects_duplicate_or_non_numeric_course_ids(self):
        env = self.valid_env()
        env["COURSE_IDS"] = "40121,abc,40121"
        errors = validate(env)
        self.assertTrue(any("数字课程 ID" in message for message in errors))
        self.assertTrue(any("重复课程 ID" in message for message in errors))

    def test_rejects_invalid_totp_seed_without_echoing_it(self):
        env = self.valid_env()
        env["UIS_TOTP_SECRET"] = "private-invalid-seed"
        errors = validate(env)
        self.assertTrue(any("UIS_TOTP_SECRET" in message for message in errors))
        self.assertNotIn("private-invalid-seed", str(errors))


if __name__ == "__main__":
    unittest.main()
