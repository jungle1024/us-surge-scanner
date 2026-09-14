"""
Financial Modeling Prep(FMP) 공식 공개 REST API 클라이언트.

용도:
1. 나스닥 거래소 확인 — UW가 뽑아준 급등 후보가 정말 NASDAQ 상장인지 확인
2. 시세 조회 — 시장 방향(M) 판정용 (QQQ 현재가)
3. 기관 보유 추이 — CANSLIM의 'I'(기관 매수) 판정용

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


# 실시간 시세는 자주 캐시를 새로 받아야 하지만, 여기서는 "시장 방향(M)" 판정처럼
# 대략적인 참고용으로만 쓰므로 짧게 캐시한다.
_QUOTE_CACHE_TTL_SECONDS = 15 * 60  # 15분
_quote_cache: dict[str, tuple[float, float | None]] = {}


def get_quote_price(ticker: str) -> float | None:
    """단일 티커의 현재가를 반환한다. 조회 실패 시 None."""
    now = time.time()
    cached = _quote_cache.get(ticker)
    if cached is not None and now - cached[0] < _QUOTE_CACHE_TTL_SECONDS:
        return cached[1]

    api_key = _get_api_key()
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/quote",
            params={"symbol": ticker, "apikey": api_key},
            timeout=FMP_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return cached[1] if cached else None

    price = None
    if resp.ok:
        rows = resp.json()
        if isinstance(rows, list) and rows:
            price = rows[0].get("price")
            price = float(price) if price is not None else None

    _quote_cache[ticker] = (now, price)
    return price


# 기관 보유 데이터(13F)는 분기 단위로만 갱신되므로 넉넉하게 캐시한다.
_OWNERSHIP_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24시간
_ownership_cache: dict[str, tuple[float, dict[str, float | None]]] = {}


def _latest_reportable_quarter() -> tuple[int, int]:
    """
    13F는 분기 마감 후 최대 45일 뒤에 제출되므로, 현재 분기 데이터는 아직 없을 가능성이 높다.
    안전하게 "직전 분기"를 우선 조회 대상으로 삼는다.
    """
    import datetime

    today = datetime.date.today()
    quarter = (today.month - 1) // 3 + 1
    year = today.year
    quarter -= 1
    if quarter == 0:
        quarter = 4
        year -= 1
    return year, quarter


def get_institutional_ownership_trend(ticker: str) -> dict[str, float | None]:
    """
    최근 분기 기관 보유 비중 변화를 반환한다.
    CANSLIM의 'I'(기관 매수) 판정에 쓴다 — 기관 보유 비중이 전분기 대비 늘었는지가 핵심.

    :return: {"ownership_pct": float | None, "ownership_pct_change": float | None}
             둘 다 None이면 조회 실패 또는 데이터 없음.
    """
    now = time.time()
    cached = _ownership_cache.get(ticker)
    if cached is not None and now - cached[0] < _OWNERSHIP_CACHE_TTL_SECONDS:
        return cached[1]

    api_key = _get_api_key()
    result: dict[str, float | None] = {"ownership_pct": None, "ownership_pct_change": None}

    year, quarter = _latest_reportable_quarter()
    # 최신 분기 데이터가 아직 없으면 한 분기 더 거슬러 올라가 재시도한다.
    for _ in range(2):
        try:
            resp = requests.get(
                f"{FMP_BASE_URL}/institutional-ownership/symbol-positions-summary",
                params={"symbol": ticker, "year": year, "quarter": quarter, "apikey": api_key},
                timeout=FMP_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            break

        if resp.ok:
            rows = resp.json()
            if isinstance(rows, list) and rows:
                row = rows[0]
                result["ownership_pct"] = row.get("ownershipPercent")
                result["ownership_pct_change"] = row.get("ownershipPercentChange")
                break

        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1

    _ownership_cache[ticker] = (now, result)
    return result
