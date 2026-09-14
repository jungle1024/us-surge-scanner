"""
백필 CLI (Render Shell 등 터미널이 있는 환경용).
무료 플랜처럼 Shell이 없으면 대신 /admin/backfill 웹 엔드포인트를 쓴다.

사용 예:
    python scripts/backfill_history.py --test              # 최근 5거래일, 폴백 시 30종목만 시험
    python scripts/backfill_history.py --days 252          # 벌크 경로 전체 1년치
    python scripts/backfill_history.py --days 252 --max-tickers 4000 --ticker-offset 0    # 폴백 경로 전체(느림, 유료 Shell 권장)
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
    parser.add_argument("--max-tickers", type=int, default=30, help="종목별 폴백 시 이번 실행에 처리할 종목 수")
    parser.add_argument("--ticker-offset", type=int, default=0, help="종목별 폴백 시 몇 번째 종목부터 시작할지")
    args = parser.parse_args()

    days = 5 if args.test else args.days
    result = run_backfill(days=days, offset=args.offset, max_tickers=args.max_tickers, ticker_offset=args.ticker_offset)
    for line in result.get("log", []):
        print(line)
    print(json.dumps({k: v for k, v in result.items() if k != "log"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
