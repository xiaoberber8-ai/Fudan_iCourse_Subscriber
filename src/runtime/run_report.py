"""Machine-readable and GitHub-friendly status for one subscriber run."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.database import Database


@dataclass
class RunReport:
    """Collect the business outcome separately from the process exit status.

    A green GitHub job must mean more than "Python did not crash": every
    configured course was inspected, every queued lecture reached a terminal
    state, and every generated summary was delivered.
    """

    course_ids: list[str]
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None
    courses_checked: list[str] = field(default_factory=list)
    course_errors: dict[str, str] = field(default_factory=dict)
    queued_lecture_ids: list[str] = field(default_factory=list)
    completed_lecture_ids: list[str] = field(default_factory=list)
    retry_lecture_ids: list[str] = field(default_factory=list)
    terminal_without_summary_ids: list[str] = field(default_factory=list)
    email_items: int = 0
    email_sent: bool = True
    unsent_lecture_ids: list[str] = field(default_factory=list)

    def course_checked(self, course_id: str) -> None:
        if course_id not in self.courses_checked:
            self.courses_checked.append(course_id)

    def course_failed(self, course_id: str, exc: BaseException) -> None:
        self.course_errors[course_id] = f"{type(exc).__name__}: {exc}"

    def finalize(
        self,
        db: "Database",
        queued_lecture_ids: list[str],
        email_items: int,
        email_sent: bool,
    ) -> None:
        self.queued_lecture_ids = list(dict.fromkeys(queued_lecture_ids))
        self.email_items = email_items
        self.email_sent = email_sent

        for sub_id in self.queued_lecture_ids:
            row = db.get_lecture(sub_id)
            if not row or not row.get("processed_at"):
                self.retry_lecture_ids.append(sub_id)
            elif row.get("summary"):
                self.completed_lecture_ids.append(sub_id)
            else:
                self.terminal_without_summary_ids.append(sub_id)

        # A lecture stops appearing in the normal retry queue after its
        # error limit is reached.  Keep those abandoned rows visible here;
        # otherwise a permanently failed lecture would eventually turn the
        # workflow green without ever being transcribed.
        unresolved = db.get_unprocessed_lectures(max_errors=2_147_483_647)
        subscribed = set(self.course_ids)
        for row in unresolved:
            sub_id = str(row["sub_id"])
            if (
                row.get("course_id") in subscribed
                and sub_id not in self.retry_lecture_ids
            ):
                self.retry_lecture_ids.append(sub_id)

        self.unsent_lecture_ids = [
            str(row["sub_id"]) for row in db.get_unsent_lectures()
        ]
        self.finished_at = datetime.now(timezone.utc).isoformat()

    @property
    def ok(self) -> bool:
        return not (
            self.course_errors
            or self.retry_lecture_ids
            or not self.email_sent
            or self.unsent_lecture_ids
        )

    def write_json(self, path: str | os.PathLike[str]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def markdown(self) -> str:
        status = "✅ 成功" if self.ok else "❌ 需要处理"
        rows = [
            "## iCourse 每日运行报告",
            "",
            f"**状态：{status}**",
            "",
            "| 指标 | 数量 |",
            "|---|---:|",
            f"| 配置课程 | {len(self.course_ids)} |",
            f"| 已检查课程 | {len(self.courses_checked)} |",
            f"| 课程枚举失败 | {len(self.course_errors)} |",
            f"| 本轮待处理课次 | {len(self.queued_lecture_ids)} |",
            f"| 已完成并生成总结 | {len(self.completed_lecture_ids)} |",
            f"| 等待重试 | {len(self.retry_lecture_ids)} |",
            f"| 无音频/无总结终态 | {len(self.terminal_without_summary_ids)} |",
            f"| 本轮邮件条目 | {self.email_items} |",
            f"| 仍未发送 | {len(self.unsent_lecture_ids)} |",
        ]
        if self.course_errors:
            rows.extend(["", "### 课程枚举失败"])
            rows.extend(
                f"- `{course_id}`: {message}"
                for course_id, message in self.course_errors.items()
            )
        if self.retry_lecture_ids:
            rows.extend(["", "### 等待下次重试", ""])
            rows.append(", ".join(f"`{sid}`" for sid in self.retry_lecture_ids))
        if self.unsent_lecture_ids:
            rows.extend(["", "### 尚未成功发送邮件", ""])
            rows.append(", ".join(f"`{sid}`" for sid in self.unsent_lecture_ids))
        return "\n".join(rows) + "\n"

    def publish(self) -> None:
        """Write the JSON artifact, job summary, and actionable annotations."""
        self.write_json(os.environ.get("RUN_REPORT_PATH", "run-report.json"))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(self.markdown())

        for course_id, message in self.course_errors.items():
            print(f"::error title=课程枚举失败::{course_id}: {message}")
        if self.retry_lecture_ids:
            joined = ", ".join(self.retry_lecture_ids)
            print(f"::error title=课次处理不完整::等待重试: {joined}")
        if not self.email_sent or self.unsent_lecture_ids:
            print("::error title=邮件未送达::生成的课程总结尚未全部发送")

