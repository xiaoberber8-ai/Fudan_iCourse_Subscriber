import json
import tempfile
import unittest
from pathlib import Path

from src.runtime.run_report import RunReport


class FakeDatabase:
    def __init__(self, lectures, unsent=()):
        self.lectures = lectures
        self.unsent = [{"sub_id": sub_id} for sub_id in unsent]

    def get_lecture(self, sub_id):
        return self.lectures.get(sub_id)

    def get_unsent_lectures(self):
        return self.unsent

    def get_unprocessed_lectures(self, max_errors=3):
        return [
            {"sub_id": sub_id, "course_id": row.get("course_id", "40121")}
            for sub_id, row in self.lectures.items()
            if not row.get("processed_at")
        ]


class RunReportTests(unittest.TestCase):
    def test_green_run_requires_processing_and_delivery(self):
        db = FakeDatabase({
            "a": {"processed_at": "now", "summary": "done"},
        })
        report = RunReport(["40121"])
        report.course_checked("40121")
        report.finalize(db, ["a"], email_items=1, email_sent=True)

        self.assertTrue(report.ok)
        self.assertEqual(report.completed_lecture_ids, ["a"])
        self.assertIn("✅ 成功", report.markdown())

    def test_retry_or_unsent_summary_makes_run_fail(self):
        db = FakeDatabase(
            {"a": {"processed_at": None, "summary": None}},
            unsent=("old",),
        )
        report = RunReport(["40121"])
        report.course_checked("40121")
        report.finalize(db, ["a"], email_items=1, email_sent=False)

        self.assertFalse(report.ok)
        self.assertEqual(report.retry_lecture_ids, ["a"])
        self.assertEqual(report.unsent_lecture_ids, ["old"])
        self.assertIn("❌ 需要处理", report.markdown())

    def test_course_enumeration_error_makes_run_fail_and_json_is_valid(self):
        report = RunReport(["40121"])
        report.course_failed("40121", RuntimeError("login expired"))
        report.finalize(FakeDatabase({}), [], 0, True)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "report.json"
            report.write_json(path)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(report.ok)
        self.assertIn("login expired", payload["course_errors"]["40121"])

    def test_abandoned_lecture_stays_visible_after_retry_limit(self):
        db = FakeDatabase({
            "stuck": {
                "course_id": "40121",
                "processed_at": None,
                "summary": None,
                "error_count": 3,
            },
        })
        report = RunReport(["40121"])
        report.course_checked("40121")
        report.finalize(db, [], 0, True)

        self.assertFalse(report.ok)
        self.assertEqual(report.retry_lecture_ids, ["stuck"])


if __name__ == "__main__":
    unittest.main()
