"""
나스닥 유니버스 가격 데이터 수집.

두 가지 방식을 모두 지원하고, 벌크 방식을 우선 시도한다:

1. 벌크 방식 (선호): FMP /stable/eod-bulk?date=... — 특정 날짜의 전 종목 종가를 1콜로.
   플랜에 따라 접근 권한이 없을 수 있어, 실패하면 자동으로 2번으로 전환한다.
2. 종목별 방식 (폴백): FMP /stable/historical-price-eod/light — 종목 하나씩,
   대신 한 번에 넉넉한 기간(예: 1년치)을 받아온다.

백필(과거 데이터 채우기)과 일별 갱신(매일 최신 하루치 추가) 양쪽에 재사용한다.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import requests

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
_TIMEOUT_SECONDS = 15


class PriceIngestError(RuntimeError):
    """가격 수집 실패 시 발생."""


def _get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise PriceIngestError("환경변수 FMP_API_KEY가 설정되어 있지 않습니다.")
    return key


def fetch_eod_bulk(price_date: date) -> dict[str, float] | None:
    """
    벌크 엔드포인트로 특정 날짜의 {심볼: 종가} 전체를 반환한다.
    엔드포인트를 이 플랜에서 쓸 수 없거나(403/401) 실패하면 None을 반환한다
    (예외를 던지지 않음 — 호출부가 폴백 여부를 판단하도록).
    """
    api_key = _get_api_key()
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/eod-bulk",
            params={"date": price_date.isoformat(), "apikey": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return None

    if not resp.ok:
        return None

    try:
        rows = resp.json()
    except ValueError:
        return None

    if not isinstance(rows, list):
        return None

    result: dict[str, float] = {}
    for row in rows:
        symbol = row.get("symbol")
        close = row.get("close") if "close" in row else row.get("adjClose")
        if symbol and close is not None:
            result[symbol] = float(close)
    return result or None


def fetch_light_chart(symbol: str, from_date: date, to_date: date) -> list[dict]:
    """
    종목 하나의 [{date, price}] 시계열을 반환한다 (한 번에 from_date~to_date 전체).
    조회 실패 시 빈 리스트를 반환한다(이 종목 하나 때문에 전체 백필이 죽지 않도록).
    """
    api_key = _get_api_key()
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/historical-price-eod/light",
            params={
                "symbol": symbol,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "apikey": api_key,
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return []

    if not resp.ok:
        return []

    rows = resp.json()
    if not isinstance(rows, list):
        return []
    return [{"date": r.get("date"), "price": r.get("price")} for r in rows if r.get("date")]


def fetch_today_quotes_nasdaq() -> dict[str, float]:
    """
    오늘자 나스닥 전체 현재가 스냅샷을 1콜로 받아온다({심볼: 현재가}).
    eod-bulk가 당일 데이터를 아직 안 줄 때(장중 등) 일별 갱신의 폴백으로 쓴다.
    """
    api_key = _get_api_key()
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/quote",
            params={"exchange": "NASDAQ", "apikey": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return {}
    if not resp.ok:
        return {}
    rows = resp.json()
    if not isinstance(rows, list):
        return {}
    return {
        r["symbol"]: float(r["price"])
        for r in rows
        if r.get("symbol") and r.get("price") is not None
    }


def previous_trading_days(from_day: date, count: int) -> list[date]:
    """
    from_day부터 거슬러 올라가며 주말을 제외한 날짜 count개를 반환한다.
    공휴일까지는 걸러내지 못하지만, 거래 없는 날은 eod-bulk가 빈 결과를 주므로
    무시되고 넘어간다.
    """
    days: list[date] = []
    d = from_day
    while len(days) < count:
        if d.weekday() < 5:  # 0=월 ... 4=금
            days.append(d)
        d -= timedelta(days=1)
    return days
