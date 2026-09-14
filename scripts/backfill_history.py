"""
백필 CLI (Render Shell 등 터미널이 있는 환경용).
무료 플랜처럼 Shell이 없으면 대신 /admin/backfill 웹 엔드포인트를 쓴다.

사용 예:
    python scripts/backfill_history.py --test              # 최근 5거래일만 시험
    python scripts/backfill_history.py --days 252          # 전체 1년치
    python scripts/backfill_history.py --days 50 --offset 50   # 51~100거래일 전 구간만
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backfill import run_backfill  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="최근 5거래일로 시험 실행")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    days = 5 if args.test else args.days
    result = run_backfill(days=days, offset=args.offset)
    for line in result.get("log", []):
        print(line)
    print(json.dumps({k: v for k, v in result.items() if k != "log"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
