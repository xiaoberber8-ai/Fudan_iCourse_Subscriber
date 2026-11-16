"""Only verify WebVPN + iCourse TOTP login; never crawl, summarize or email."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.webvpn import SecondFactorError, WebVPNSession


def main() -> int:
    for attempt in range(3):
        vpn = WebVPNSession()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                vpn.login()
                vpn.authenticate_icourse()
            print(json.dumps({"authenticated": True, "attempt": attempt + 1}))
            return 0
        except SecondFactorError:
            print(json.dumps({"authenticated": False, "error": "second_factor_failed", "retry": False}))
            return 1
        except Exception as exc:
            print(json.dumps({"authenticated": False, "attempt": attempt + 1,
                              "error_type": type(exc).__name__}))
            if attempt == 2:
                return 1
            time.sleep(5)
        finally:
            vpn.session.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
