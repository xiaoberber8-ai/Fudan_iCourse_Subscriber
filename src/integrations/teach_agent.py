	©Á¢⁄)zyﬁ∂Xßí ëÎN•Í⁄∂*'ûãizπ¢∂◊ù	©Á¢⁄)zyﬁ∂Xßí ëÎN•Í⁄∂*'ûãizπ¢∂◊ù"""Export processed lectures to the teach_agent LectureInboxEnvelope v1."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ExportResult:
    eligible: int
    exported: int
    unchanged: int
    paths: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _lecture_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    return parsed.isoformat()


def _segments(conn: sqlite3.Connection, lecture: sqlite3.Row) -> list[dict]:
    rows = conn.execute(
        """SELECT segment_id, start_ms, end_ms, text
           FROM transcript_segments WHERE sub_id = ?
           ORDER BY COALESCE(start_ms, 9223372036854775807), segment_id""",
        (lecture["sub_id"],),
    ).fetchall()
    if rows:
        return [
            {
                "segment_id": row["segment_id"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "text": row["text"],
            }
            for row in rows
            if row["text"] and row["text"].strip()
        ]
    transcript = (lecture["transcript"] or "").strip()
    return (
        [{
            "segment_id": "legacy-full-text",
            "start_ms": None,
            "end_ms": None,
            "text": transcript,
        }]
        if transcript else []
    )


def _ppt_pages(conn: sqlite3.Connection, sub_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT page_num, created_sec, text, ocr_status
           FROM ppt_pages
           WHERE sub_id = ? AND ocr_status = 'done'
             AND TRIM(COALESCE(text, '')) <> ''
           ORDER BY page_num""",
        (sub_id,),
    ).fetchall()
    return [
        {
            "page_id": f"p{row['page_num']}",
            "created_sec": row["created_sec"],
            "text": row["text"],
            "ocr_status": row["ocr_status"],
        }
        for row in rows
    ]


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(_canonical(payload) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def export_pending(
    db_path: Path,
    inbox_dir: Path,
    *,
    student_id: str,
    student_name: str | None = None,
    force: bool = False,
) -> ExportResult:
    """Export each new content revision exactly once into a local inbox."""
    if not student_id.strip():
        raise ValueError("student_id ‰∏çËÉΩ‰∏∫Á©∫")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Keeps the CLI compatible with databases created by older releases.
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS transcript_segments (
                   sub_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                   start_ms INTEGER, end_ms INTEGER, text TEXT NOT NULL,
                   PRIMARY KEY (sub_id, segment_id));
               CREATE TABLE IF NOT EXISTS teach_agent_exports (
                   sub_id TEXT NOT NULL, source_revision TEXT NOT NULL,
                   exported_at TEXT NOT NULL, destination TEXT NOT NULL,
                   PRIMARY KEY (sub_id, source_revision));"""
        )
        lectures = conn.execute(
            """SELECT l.*, c.title AS course_title
               FROM lectures l JOIN courses c ON c.course_id = l.course_id
               WHERE TRIM(COALESCE(l.transcript, '')) <> ''
                  OR EXISTS (
                     SELECT 1 FROM ppt_pages p
                     WHERE p.sub_id = l.sub_id AND p.ocr_status = 'done'
                       AND TRIM(COALESCE(p.text, '')) <> '')
               ORDER BY COALESCE(l.date, ''), l.sub_id"""
        ).fetchall()
        exported: list[str] = []
        unchanged = 0
        for lecture in lectures:
            content = {
                "schema_version": 1,
                "source": "icourse",
                "course_id": str(lecture["course_id"]),
                "lecture_id": str(lecture["sub_id"]),
                "lecture_at": _lecture_at(lecture["date"]),
                "transcript_status": "complete" if lecture["transcript"] else "partial",
                "segments": _segments(conn, lecture),
                "ppt_pages": _ppt_pages(conn, str(lecture["sub_id"])),
                "summary": lecture["summary"],
            }
            revision = "sha256:" + hashlib.sha256(_canonical(content)).hexdigest()
            content["source_revision"] = revision
            if not force and conn.execute(
                """SELECT 1 FROM teach_agent_exports
                   WHERE sub_id = ? AND source_revision = ?""",
                (lecture["sub_id"], revision),
            ).fetchone():
                unchanged += 1
                continue
            envelope = {
                "schema_version": 1,
                "student_id": student_id.strip(),
                "student_name": student_name.strip() if student_name else None,
                "course_title": lecture["course_title"] or str(lecture["course_id"]),
                "lecture_title": lecture["sub_title"],
                "bundle": content,
            }
            filename = (
                f"icourse-{lecture['course_id']}-{lecture['sub_id']}-"
                f"{revision[7:19]}.json"
            )
            target = inbox_dir / filename
            archived = (inbox_dir / "accepted").glob(f"{target.stem}-*.json")
            if not force and (target.exists() or next(archived, None) is not None):
                unchanged += 1
                continue
            _atomic_json(target, envelope)
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO teach_agent_exports
                       (sub_id, source_revision, exported_at, destination)
                       VALUES (?, ?, ?, ?)""",
                    (lecture["sub_id"], revision, datetime.now().isoformat(), str(target)),
                )
            exported.append(str(target))
        return ExportResult(len(lectures), len(exported), unchanged, tuple(exported))
    finally:
        conn.close()
