	©ç¢Ú)zyÞ¶X§’Ê‘ëN¥êÚ¶*'ž‹iz¹¢¶×	©ç¢Ú)zyÞ¶X§’Ê‘ëN¥êÚ¶*'ž‹iz¹¢¶×#!/usr/bin/env python3
"""Fetch the encrypted data branch and atomically rebuild a local SQLite DB."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.crypto_box import derive_new_password
from src.data.sharder import load_index, reassemble_database


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _git(*args: str, text: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=text
    )
    return completed.stdout


def pull(output: Path, env_file: Path) -> None:
    _load_env(env_file)
    student = os.environ.get("STUID") or os.environ.get("StuId", "")
    password = os.environ.get("UISPSW") or os.environ.get("UISPsw", "")
    if not student or not password:
        raise ValueError(".env ä¸­éœ€è¦ StuId/UISPswï¼ˆæˆ– STUID/UISPSWï¼‰")
    _git("fetch", "--depth=1", "origin", "+refs/heads/data:refs/remotes/origin/data")
    names = str(_git("ls-tree", "-r", "--name-only", "origin/data", text=True)).splitlines()
    index_name = "data/icourse-index.enc"
    shard_names = sorted(name for name in names if name.startswith("data/shards/") and name.endswith(".enc"))
    if index_name not in names or not shard_names:
        raise ValueError("data åˆ†æ”¯æ²¡æœ‰å½“å‰åˆ†ç‰‡æ ¼å¼ï¼›è¯·å…ˆæˆåŠŸè¿è¡Œä¸€æ¬¡ Subscriber workflow")
    with tempfile.TemporaryDirectory(prefix="icourse-pull-") as root:
        root_path = Path(root)
        (root_path / "shards").mkdir()
        (root_path / "icourse-index.enc").write_bytes(
            _git("show", f"origin/data:{index_name}")
        )
        for name in shard_names:
            (root_path / "shards" / Path(name).name).write_bytes(
                _git("show", f"origin/data:{name}")
            )
        rebuilt = root_path / "icourse.db"
        encryption_password = derive_new_password(student, password)
        index = load_index(str(root_path / "icourse-index.enc"), encryption_password)
        reassemble_database(
            index, str(root_path / "shards"), str(rebuilt), encryption_password
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_bytes(rebuilt.read_bytes())
        temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/icourse.db"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        pull(args.output, args.env_file)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"pull failed: {exc}") from exc
    print(f"rebuilt {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
