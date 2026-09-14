"""
Financial Modeling Prep(FMP) 공식 공개 REST API 클라이언트.

이 서비스에서는 딱 한 가지 용도로만 쓴다: UW가 뽑아준 급등 후보 종목이
정말 나스닥(NASDAQ) 상장 종목인지 확인하는 것. (UW 스크리너 응답에는
거래소 구분 필드가 없기 때문)

문서: https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey=...
"""

from __future__ import annotations

import os
import time

import requests

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
FMP_TIMEOUT_SECONDS = 10

# 거래소는 하루에도 잘 안 바뀌는 정보라, 넉넉하게 오래 캐시해서 FMP 호출 횟수를 줄인다.
_EXCHANGE_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6시간
_exchange_cache: dict[str, tuple[float, str | None]] = {}


class FMPClientError(RuntimeError):
    """FMP API 호출 실패 시 발생."""


def _get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise FMPClientError(
            "환경변수 FMP_API_KEY가 설정되어 있지 않습니다. "
            "FMP 대시보드에서 발급받은 API 키를 Render 서비스의 환경변수로 등록하세요."
        )
    return key


def get_exchange(ticker: str) -> str | None:
    """
    단일 티커의 상장 거래소(예: 'NASDAQ', 'NYSE')를 반환한다. 조회 실패 시 None.
    """
    now = time.time()
    cached = _exchange_cache.get(ticker)
    if cached is not None and now - cached[0] < _EXCHANGE_CACHE_TTL_SECONDS:
        return cached[1]

    api_key = _get_api_key()
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/profile",
            params={"symbol": ticker, "apikey": api_key},
            timeout=FMP_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return cached[1] if cached else None

    exchange = None
    if resp.ok:
        rows = resp.json()
        if isinstance(rows, list) and rows:
            exchange = rows[0].get("exchange")

    _exchange_cache[ticker] = (now, exchange)
    return exchange


def filter_by_exchange(tickers: list[str], allowed_exchanges: set[str]) -> dict[str, str]:
    """
    tickers 각각의 거래소를 조회해서, allowed_exchanges에 속하는 것만
    {티커: 거래소} 딕셔너리로 반환한다. (순차 호출 — 후보 종목 수는 보통
    수십 개 수준이라 병렬화 없이도 충분히 빠르다.)
    """
    result: dict[str, str] = {}
    for ticker in tickers:
        exchange = get_exchange(ticker)
        if exchange and exchange.upper() in allowed_exchanges:
            result[ticker] = exchange
    return result
