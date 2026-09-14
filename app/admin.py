"""
관리자용 엔드포인트.

Render 무료 플랜은 Shell을 지원하지 않아서, 백필·일별 갱신을 터미널 없이
브라우저 주소창에 URL을 입력하는 것만으로 실행할 수 있게 만든 임시 통로다.
아무나 실행하면 안 되므로 ADMIN_TOKEN 환경변수와 일치하는 token 쿼리 파라미터가
있어야만 동작한다.

주의: 백필은 한 번 호출에 시간이 걸릴 수 있다. 요청 타임아웃을 피하려면
`days`를 50 이하로 유지하고, `offset`을 바꿔가며 여러 번 나눠 호출할 것.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from app.backfill import run_backfill, run_daily_update
from app.db import DatabaseError, get_connection

router = APIRouter(prefix="/admin")


def _check_token(token: str) -> None:
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="환경변수 ADMIN_TOKEN이 설정되어 있지 않습니다. Render 환경변수에 먼저 등록하세요.",
        )
    if token != expected:
        raise HTTPException(status_code=401, detail="token이 올바르지 않습니다.")


@router.get("/backfill")
def admin_backfill(
    token: str = Query(...),
    days: int = Query(5, ge=1, le=252, description="백필할 거래일 수. 종목별 폴백이 걸리면 이 값은 항상 크게(예: 252) 주는 게 유리함 — 폴백 속도는 날짜 수가 아니라 종목 수에 좌우됨"),
    offset: int = Query(0, ge=0, description="[벌크 경로용] 오늘로부터 몇 거래일 건너뛰고 시작할지"),
    max_tickers: int = Query(30, ge=1, le=4000, description="[종목별 폴백 경로용] 이번 호출에 처리할 종목 수. 기본 30(안전한 시험 크기)"),
    ticker_offset: int = Query(0, ge=0, description="[종목별 폴백 경로용] 유니버스 목록에서 몇 번째 종목부터 시작할지 — 여러 번 호출로 나눠 전체를 채울 때 사용"),
) -> dict:
    _check_token(token)
    try:
        return run_backfill(days=days, offset=offset, max_tickers=max_tickers, ticker_offset=ticker_offset)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/daily-update")
def admin_daily_update(token: str = Query(...)) -> dict:
    _check_token(token)
    try:
        return run_daily_update()
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status")
def admin_status(token: str = Query(...)) -> dict:
    """
    지금까지 얼마나 쌓였는지 한 번에 확인한다. 개별 curl 회차가 성공/실패했는지
    스크롤로 뒤지는 대신, 이 엔드포인트 하나로 전체 진행 상황을 본다.
    """
    _check_token(token)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM nasdaq_universe")
                universe_count = cur.fetchone()[0]

                cur.execute(
                    "SELECT count(DISTINCT symbol), count(DISTINCT price_date), "
                    "min(price_date), max(price_date), count(*) FROM daily_price"
                )
                symbols_with_price, distinct_dates, min_date, max_date, total_rows = cur.fetchone()

                cur.execute(
                    "SELECT symbol, count(*) AS days FROM daily_price GROUP BY symbol "
                    "ORDER BY days DESC LIMIT 1"
                )
                fullest = cur.fetchone()

                cur.execute("SELECT count(*) FROM rs_rating_daily")
                rs_rows = cur.fetchone()[0]

        return {
            "universe_symbols": universe_count,
            "symbols_with_price_data": symbols_with_price,
            "symbols_still_missing": universe_count - symbols_with_price,
            "distinct_trading_days_stored": distinct_dates,
            "price_date_range": [str(min_date), str(max_date)] if min_date else None,
            "most_complete_symbol": {"symbol": fullest[0], "days_stored": fullest[1]} if fullest else None,
            "total_price_rows": total_rows,
            "rs_rating_rows": rs_rows,
        }
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
