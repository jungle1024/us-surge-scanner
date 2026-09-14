"""
가격 데이터 저장, RS Rating·업종 내 순위 계산, 조회.

RS Rating과 L(업종 리더십) 계산 자체는 외부 API 호출 없이, DB 안에서
SQL(윈도 함수 PERCENT_RANK)로 끝낸다 — 이게 이 아키텍처의 핵심이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg2.extras

from app.db import DatabaseError, get_connection

# 며칠어치 종가를 들고 있을지. 252거래일(약 1년)이면 12개월 수익률까지 계산 가능.
# 여유를 조금 둔다.
RETENTION_TRADING_DAYS = 280


def upsert_daily_prices(price_date: date, prices: dict[str, float]) -> int:
    """{심볼: 종가} 를 해당 날짜의 daily_price 행으로 저장(UPSERT)한다. 저장된 행 수를 반환."""
    if not prices:
        return 0
    rows = [(symbol, price_date, close) for symbol, close in prices.items()]
    with get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO daily_price (symbol, price_date, close)
                VALUES %s
                ON CONFLICT (symbol, price_date) DO UPDATE SET close = EXCLUDED.close
                """,
                rows,
            )
            written = cur.rowcount
    return written


def prune_old_prices() -> int:
    """각 심볼별로 최신 RETENTION_TRADING_DAYS 거래일보다 오래된 행을 지운다."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM daily_price dp
                WHERE dp.price_date < (
                    SELECT price_date FROM (
                        SELECT price_date,
                               ROW_NUMBER() OVER (ORDER BY price_date DESC) AS rn
                        FROM (SELECT DISTINCT price_date FROM daily_price) d
                    ) ranked
                    WHERE rn = %s
                )
                """,
                (RETENTION_TRADING_DAYS,),
            )
            deleted = cur.rowcount
    return deleted


# rn=1이 최신(오늘), rn=64가 약 63거래일 전(3개월), rn=127이 126거래일 전(6개월),
# rn=253이 252거래일 전(12개월) — "그 지점 포함 몇 번째로 최근 거래일인가"로 계산하므로
# 공휴일 등으로 실제 날짜가 밀려도 항상 정확히 그만큼의 거래일 전을 가리킨다.
_RECOMPUTE_SQL = """
WITH ranked_prices AS (
    SELECT symbol, price_date, close,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY price_date DESC) AS rn
    FROM daily_price
),
pivoted AS (
    SELECT
        symbol,
        MAX(CASE WHEN rn = 1 THEN close END) AS close_now,
        MAX(CASE WHEN rn = 1 THEN price_date END) AS latest_date,
        MAX(CASE WHEN rn = 64 THEN close END) AS close_63d,
        MAX(CASE WHEN rn = 127 THEN close END) AS close_126d,
        MAX(CASE WHEN rn = 253 THEN close END) AS close_252d
    FROM ranked_prices
    WHERE rn IN (1, 64, 127, 253)
    GROUP BY symbol
),
returns AS (
    SELECT
        symbol, latest_date,
        (close_now - close_63d) / NULLIF(close_63d, 0) AS return_63d,
        (close_now - close_126d) / NULLIF(close_126d, 0) AS return_126d,
        (close_now - close_252d) / NULLIF(close_252d, 0) AS return_252d
    FROM pivoted
    WHERE close_now IS NOT NULL
),
ranked AS (
    SELECT r.symbol, r.latest_date, r.return_63d, r.return_126d, r.return_252d, u.sector,
           PERCENT_RANK() OVER (ORDER BY r.return_126d) * 99 AS rs_percentile,
           PERCENT_RANK() OVER (PARTITION BY u.sector ORDER BY r.return_126d) * 99 AS sector_rank_pct
    FROM returns r
    JOIN nasdaq_universe u ON u.symbol = r.symbol
    WHERE r.return_126d IS NOT NULL
)
INSERT INTO rs_rating_daily (symbol, price_date, return_63d, return_126d, return_252d, rs_percentile, sector_rank_pct)
SELECT symbol, latest_date, return_63d, return_126d, return_252d, rs_percentile, sector_rank_pct
FROM ranked
ON CONFLICT (symbol, price_date) DO UPDATE SET
    return_63d = EXCLUDED.return_63d,
    return_126d = EXCLUDED.return_126d,
    return_252d = EXCLUDED.return_252d,
    rs_percentile = EXCLUDED.rs_percentile,
    sector_rank_pct = EXCLUDED.sector_rank_pct;
"""


def recompute_rankings() -> int:
    """
    daily_price에 쌓인 데이터로 전 종목의 RS 백분위·업종 내 순위를 다시 계산해서
    rs_rating_daily에 저장한다. 126거래일(6개월)치가 없는 종목은 계산에서 빠진다
    (데이터가 아직 부족한 초기 몇 달은 결과가 없을 수 있음 — 정상).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_RECOMPUTE_SQL)
            updated = cur.rowcount
    return updated


def get_rs_rating(symbol: str) -> dict[str, Any] | None:
    """종목의 가장 최근 RS 백분위·업종 내 순위를 반환한다. 데이터 없으면 None."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT return_63d, return_126d, return_252d, rs_percentile, sector_rank_pct, price_date
                    FROM rs_rating_daily
                    WHERE symbol = %s
                    ORDER BY price_date DESC
                    LIMIT 1
                    """,
                    (symbol,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except DatabaseError:
        return None
