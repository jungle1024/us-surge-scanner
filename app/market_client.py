"""
시장 전체 방향(CANSLIM의 'M') 판정.

나스닥 대표 ETF인 QQQ의 현재가가 50일 이동평균 위에 있는지로 "시장이 상승 추세인가"를
간단히 판정한다. CANSLIM 원래 정의의 "시장 국면 판단"을 가장 단순한 형태로 근사한 것이다.

종목별이 아니라 시장 전체에 대해 한 번만 계산하면 되므로, 스캔 1회당 이 판정은
한 번만 수행되고 결과는 짧게 캐시된다.
"""

from __future__ import annotations

import time

from app.fmp_client import FMPClientError, get_quote_price
from app.technical_client import TechnicalClientError, get_latest_sma

MARKET_PROXY_TICKER = "QQQ"

_CACHE_TTL_SECONDS = 30 * 60  # 30분
_cache: dict[str, tuple[float, dict[str, float | bool | None]]] = {}


class MarketClientError(RuntimeError):
    """시장 방향 판정 실패 시 발생."""


def get_market_direction() -> dict[str, float | bool | None]:
    """
    :return: {"is_bullish": bool | None, "price": float | None, "sma_50": float | None}
             is_bullish가 None이면 데이터 부족으로 판정 불가.
    """
    now = time.time()
    cached = _cache.get("market")
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        price = get_quote_price(MARKET_PROXY_TICKER)
    except FMPClientError as exc:
        raise MarketClientError(f"시장 방향 판정 실패(시세 조회): {exc}") from exc

    try:
        sma_50 = get_latest_sma(MARKET_PROXY_TICKER, 50)
    except TechnicalClientError as exc:
        raise MarketClientError(f"시장 방향 판정 실패(이동평균 조회): {exc}") from exc

    is_bullish = price > sma_50 if (price is not None and sma_50 is not None) else None
    result = {"is_bullish": is_bullish, "price": price, "sma_50": sma_50}

    _cache["market"] = (now, result)
    return result
