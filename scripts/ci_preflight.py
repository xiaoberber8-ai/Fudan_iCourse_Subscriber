"""Fail fast when required GitHub Actions secrets are missing or malformed."""

from __future__ import annotations

import os
import re


REQUIRED = (
    "STUID",
    "UISPSW",
    "COURSE_IDS",
    "SMTP_EMAIL",
    "SMTP_PASSWORD",
    "RECEIVER_EMAIL",
)
MODEL_KEYS = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "GEMINI_API_KEY",
)


def validate(env: dict[str, str]) -> list[str]:
    errors: list[str] = []
    missing = [name for name in REQUIRED if not env.get(name, "").strip()]
    if missing:
        errors.append("缺少必需 Secrets: " + ", ".join(missing))

    course_ids = [
        value.strip()
        for value in env.get("COURSE_IDS", "").split(",")
        if value.strip()
    ]
    if course_ids and any(not re.fullmatch(r"\d+", value) for value in course_ids):
        errors.append("COURSE_IDS 必须是逗号分隔的数字课程 ID")
    if len(course_ids) != len(set(course_ids)):
        errors.append("COURSE_IDS 中存在重复课程 ID")

    for name in ("SMTP_EMAIL", "RECEIVER_EMAIL"):
        value = env.get(name, "").strip()
        if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            errors.append(f"{name} 不是有效邮箱地址")

    if not any(env.get(name, "").strip() for name in MODEL_KEYS):
        errors.append("至少配置一个模型 Secret: " + ", ".join(MODEL_KEYS))
    return errors


def main() -> int:
    errors = validate(dict(os.environ))
    if errors:
        for message in errors:
            print(f"::error title=Subscriber 配置预检失败::{message}")
        return 1
    course_count = len(
        [value for value in os.environ["COURSE_IDS"].split(",") if value.strip()]
    )
    print(f"Configuration valid: {course_count} course(s), SMTP and LLM ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
