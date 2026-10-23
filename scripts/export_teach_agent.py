#!/usr/bin/env python3
"""Export new/changed Subscriber lectures into a local teach_agent inbox."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.integrations.teach_agent import export_pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/icourse.db"))
    parser.add_argument(
        "--inbox",
        type=Path,
        default=Path(os.environ.get("TEACH_AGENT_INBOX", "../teach_agent/data/inbox")),
    )
    parser.add_argument(
        "--student-id", default=os.environ.get("TEACH_AGENT_STUDENT_ID", "local-student")
    )
    parser.add_argument("--student-name", default=os.environ.get("TEACH_AGENT_STUDENT_NAME"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"database not found: {args.db}")
    result = export_pending(
        args.db,
        args.inbox,
        student_id=args.student_id,
        student_name=args.student_name,
        force=args.force,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
