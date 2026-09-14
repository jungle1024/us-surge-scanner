"""
일별 갱신 CLI (Render Shell/Cron Job 등 터미널이 있는 환경용).
무료 플랜처럼 Shell·Cron Job이 없으면 대신 /admin/daily-update 웹 엔드포인트를
외부 무료 크론 서비스(cron-job.org 등)로 매일 호출한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backfill import run_daily_update  # noqa: E402


def main() -> None:
    result = run_daily_update()
    for line in result.get("log", []):
        print(line)
    print(json.dumps({k: v for k, v in result.items() if k != "log"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
