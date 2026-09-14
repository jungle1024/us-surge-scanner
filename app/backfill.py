"""
백필·일별 갱신의 실제 로직. `app/admin.py`(무료 플랜에서 Shell 없이 웹 주소로 실행)와
`scripts/*.py`(Shell이 있는 유료 플랜에서 CLI로 실행) 양쪽에서 공유한다.

무료 플랜의 웹 요청 타임아웃을 피하기 위해, 백필은 한 번에 전체(약 252거래일)를
받지 않고 offset/days로 구간을 나눠 여러 번 호출하도록 설계했다.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import psycopg2.extras

from app.db import get_connection, init_schema
from app.price_ingest import (
    fetch_eod_bulk,
    fetch_light_chart,
    fetch_today_quotes_nasdaq,
    previous_trading_days,
)
from app.rs_rating import prune_old_prices, recompute_rankings, upsert_daily_prices
from app.universe_client import fetch_nasdaq_common_stock_universe


def _save_universe(universe: list[dict]) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO nasdaq_universe (symbol, company_name, sector, is_active, updated_at)
                VALUES %s
                ON CONFLICT (symbol) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector = EXCLUDED.sector,
                    is_active = TRUE,
                    updated_at = EXCLUDED.updated_at
                """,
                [(u["symbol"], u["company_name"], u["sector"], True, date.today()) for u in universe],
            )


def _backfill_via_bulk(
    trading_days: list[date], universe_symbols: set[str], log: list[str]
) -> bool:
    for i, day in enumerate(trading_days):
        snapshot = fetch_eod_bulk(day)
        if snapshot is None:
            if i == 0:
                log.append(f"[벌크 미지원 감지] {day} 조회 실패 — 종목별 방식으로 전환합니다.")
                return False
            log.append(f"{day}: 데이터 없음(휴장일 추정) — 건너뜀")
            continue
        filtered = {sym: price for sym, price in snapshot.items() if sym in universe_symbols}
        written = upsert_daily_prices(day, filtered)
        log.append(f"{day}: {written}개 종목 저장 (전체 응답 {len(snapshot)}개 중 유니버스 매칭 {len(filtered)}개)")
        time.sleep(0.1)
    return True


def _backfill_via_per_ticker(
    universe: list[dict],
    from_date: date,
    to_date: date,
    log: list[str],
    max_tickers: int | None = None,
    ticker_offset: int = 0,
) -> None:
    universe_slice = universe[ticker_offset:]
    targets = universe_slice[:max_tickers] if max_tickers else universe_slice
    total = len(targets)
    log.append(
        f"(종목 {ticker_offset}~{ticker_offset + total}번째, 전체 {len(universe)}종목 중 {total}개 처리)"
    )
    for idx, u in enumerate(targets, start=1):
        symbol = u["symbol"]
        series = fetch_light_chart(symbol, from_date, to_date)
        by_date: dict[str, dict[str, float]] = {}
        for row in series:
            if row.get("price") is not None:
                by_date.setdefault(row["date"], {})[symbol] = row["price"]
        for d_str, price_map in by_date.items():
            upsert_daily_prices(date.fromisoformat(d_str), price_map)
        if idx % 25 == 0 or idx == total:
            log.append(f"진행 {idx}/{total} 종목 ({symbol} 완료)")
        time.sleep(0.15)


def run_backfill(
    *, days: int = 5, offset: int = 0, max_tickers: int | None = 30, ticker_offset: int = 0
) -> dict[str, Any]:
    """
    오늘로부터 offset거래일만큼 건너뛴 지점부터, days거래일치를 백필한다.
    예: offset=0,days=50 → 가장 최근 50거래일 / offset=50,days=50 → 그 다음 50거래일.
    무료 플랜의 요청 타임아웃을 피하려면 한 번 호출당 days를 50 이하로 유지할 것.

    벌크 엔드포인트가 이 플랜에서 안 되면 종목별 폴백으로 전환되는데, 이 경로는 속도가
    "며칠치를 받아오는가"가 아니라 "몇 종목을 처리하는가"에 좌우된다. 그래서 폴백 시에는
    max_tickers/ticker_offset으로 종목 단위로 나눠서 여러 번 호출하는 걸 전제로 한다.

    :param max_tickers: 폴백 경로에서 이번 호출에 처리할 종목 수. 기본값 30(안전한 시험 크기).
        전체 유니버스를 다 처리하려면 None으로 명시하되, 무료 플랜에서는 요청이 타임아웃날
        가능성이 높으므로 권장하지 않는다 — 대신 ticker_offset을 늘려가며 여러 번 호출할 것.
    :param ticker_offset: 유니버스 목록에서 몇 번째 종목부터 시작할지 (여러 번 호출로 나눠 처리할 때 사용).
    """
    log: list[str] = []
    log.append("1) 스키마 확인/생성")
    init_schema()

    log.append("2) 나스닥 보통주 유니버스 조회")
    universe = fetch_nasdaq_common_stock_universe()
    log.append(f"   유니버스 {len(universe)}종목 확보")
    _save_universe(universe)
    universe_symbols = {u["symbol"] for u in universe}

    anchor = date.today() - timedelta(days=1)
    all_days = previous_trading_days(anchor, offset + days)
    trading_days = all_days[offset:]
    if not trading_days:
        log.append("이 구간에 해당하는 거래일이 없습니다(offset이 너무 큼).")
        return {"log": log}

    log.append(f"3) 가격 백필 ({trading_days[-1]} ~ {trading_days[0]}, {len(trading_days)}거래일)")
    bulk_ok = _backfill_via_bulk(trading_days, universe_symbols, log)
    if not bulk_ok:
        log.append("4) 종목별 방식으로 백필")
        _backfill_via_per_ticker(
            universe, trading_days[-1], trading_days[0], log,
            max_tickers=max_tickers, ticker_offset=ticker_offset,
        )

    log.append("5) RS 백분위/업종 순위 재계산")
    updated = recompute_rankings()
    log.append(f"   {updated}개 종목 랭킹 계산 완료")

    return {
        "universe_size": len(universe),
        "trading_days_covered": [d.isoformat() for d in trading_days],
        "bulk_endpoint_available": bulk_ok,
        "rs_ratings_updated": updated,
        "log": log,
    }


def run_daily_update() -> dict[str, Any]:
    """오늘자 데이터 1건을 추가하고, 오래된 데이터 정리 + 랭킹 재계산까지 수행한다."""
    log: list[str] = []
    log.append("1) 스키마 확인")
    init_schema()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, updated_at FROM nasdaq_universe")
            rows = cur.fetchall()

    if not rows or (date.today() - min(r[1] for r in rows)) > timedelta(days=7):
        log.append("2) 유니버스 갱신(7일 이상 경과 또는 최초 실행)")
        universe = fetch_nasdaq_common_stock_universe()
        _save_universe(universe)
        symbols = {u["symbol"] for u in universe}
    else:
        symbols = {r[0] for r in rows}
        log.append(f"2) 유니버스 최신 상태 유지 ({len(symbols)}종목)")

    today = date.today()
    log.append(f"3) {today} 가격 조회")
    snapshot = fetch_eod_bulk(today)
    source = "eod-bulk"
    if not snapshot:
        log.append("   eod-bulk 결과 없음 — 시세 조회 폴백 사용")
        snapshot = fetch_today_quotes_nasdaq()
        source = "quote-fallback"

    filtered = {sym: price for sym, price in snapshot.items() if sym in symbols}
    written = upsert_daily_prices(today, filtered)
    log.append(f"   {written}개 종목 저장 (출처: {source})")

    log.append("4) 오래된 데이터 정리")
    deleted = prune_old_prices()
    log.append(f"   {deleted}행 삭제")

    log.append("5) RS 백분위/업종 순위 재계산")
    updated = recompute_rankings()
    log.append(f"   {updated}개 종목 랭킹 갱신")

    return {"written": written, "source": source, "deleted": deleted, "rs_ratings_updated": updated, "log": log}
