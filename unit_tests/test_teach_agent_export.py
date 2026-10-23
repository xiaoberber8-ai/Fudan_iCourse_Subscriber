from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.data.schema import SCHEMA_SQL
from src.integrations.teach_agent import export_pending


class TeachAgentExportTest(unittest.TestCase):
    def test_export_is_atomic_versioned_and_idempotent(self):
        with TemporaryDirectory() as root:
            db_path = Path(root) / "icourse.db"
            inbox = Path(root) / "inbox"
            conn = sqlite3.connect(db_path)
            conn.executescript(SCHEMA_SQL)
            conn.execute("INSERT INTO courses VALUES ('c1', '机器学习', '教师')")
            conn.execute(
                """INSERT INTO lectures
                   (sub_id, course_id, sub_title, date, transcript, summary, processed_at)
                   VALUES ('l1', 'c1', '支持向量机', '2026-10-20',
                           '最大间隔分类器', '课程摘要', '2026-10-20T12:00:00')"""
            )
            conn.execute(
                """INSERT INTO transcript_segments
                   (sub_id, segment_id, start_ms, end_ms, text)
                   VALUES ('l1', 's1', 1000, 3000, '最大间隔分类器')"""
            )
            conn.execute(
                """INSERT INTO ppt_pages
                   (sub_id, page_num, created_sec, text, ocr_status)
                   VALUES ('l1', 1, 2, 'SVM', 'done')"""
            )
            conn.commit()
            conn.close()

            first = export_pending(db_path, inbox, student_id="student-1")
            second = export_pending(db_path, inbox, student_id="student-1")

            self.assertEqual(first.exported, 1)
            self.assertEqual(second.exported, 0)
            self.assertEqual(second.unchanged, 1)
            files = list(inbox.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(list(inbox.glob("*.tmp")), [])
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["bundle"]["segments"][0]["start_ms"], 1000)
            self.assertTrue(payload["bundle"]["source_revision"].startswith("sha256:"))

    def test_transcript_segments_survive_encrypted_shard_roundtrip(self):
        try:
            from src.data.sharder import reassemble_database, shard_database
        except ModuleNotFoundError as exc:
            if exc.name == "Crypto":
                self.skipTest("pycryptodome is not installed in the bare test interpreter")
            raise
        with TemporaryDirectory() as root:
            db_path = Path(root) / "source.db"
            output_dir = Path(root) / "sharded"
            rebuilt = Path(root) / "rebuilt.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(SCHEMA_SQL)
            conn.execute("INSERT INTO courses VALUES ('c1', '机器学习', '教师')")
            conn.execute(
                "INSERT INTO lectures (sub_id, course_id, transcript) VALUES ('l1', 'c1', 'SVM')"
            )
            conn.execute(
                """INSERT INTO transcript_segments
                   (sub_id, segment_id, start_ms, end_ms, text)
                   VALUES ('l1', 's1', 1000, 2000, 'SVM')"""
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('subscribed_course_ids', 'c1')"
            )
            conn.commit()
            conn.close()

            index = shard_database(str(db_path), str(output_dir), "test-password")
            reassemble_database(index, str(output_dir / "shards"), str(rebuilt), "test-password")

            conn = sqlite3.connect(rebuilt)
            row = conn.execute(
                "SELECT start_ms, end_ms, text FROM transcript_segments"
            ).fetchone()
            conn.close()
            self.assertEqual(row, (1000, 2000, "SVM"))


if __name__ == "__main__":
    unittest.main()
