"""
나스닥 급등주 스크리너 핵심 로직 (공식 API 버전).

흐름:
1. UW /api/screener/stocks 로 "미국 보통주 중 등락률·상대거래량·시총 조건을 만족하는" 후보를 넉넉히 뽑는다.
   (UW 응답에는 거래소 구분이 없어서 이 단계는 나스닥 외 거래소도 섞여 있다)
2. 후보 티커들을 FMP 회사 프로필로 조회해서 거래소가 NASDAQ인 것만 남긴다.
3. 결과를 짧은 TTL로 캐시해서 UW/FMP 호출 횟수를 아낀다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.fmp_client import FMPClientError, filter_by_exchange
from app.uw_client import UWClientError, fetch_stock_screener

CACHE_TTL_SECONDS = 30

# UW 후보를 몇 배수로 넉넉히 뽑을지. 나스닥이 아닌 종목이 섞여 있어서 걸러내고 나면
# 줄어들기 때문에, 최종 limit보다 넉넉히 가져와야 한다.
CANDIDATE_OVERFETCH_MULTIPLIER = 4
MAX_CANDIDATES = 200


class ScannerError(RuntimeError):
    """스크리닝 파이프라인(UW 또는 FMP) 실패 시 발생."""


@dataclass
class _CacheEntry:
    timestamp: float
    total_candidates: int
    rows: list[dict[str, Any]] = field(default_factory=list)


_cache: dict[str, _CacheEntry] = {}


def _cache_key(**params: Any) -> str:
    return "|".join(f"{k}={v}" for k, v in sorted(params.items()))


def _normalize_row(raw: dict[str, Any], exchange: str) -> dict[str, Any]:
    """UW 원본 행에서 자주 쓰는 필드를 표준 이름으로 뽑아내고, 원본도 함께 남긴다."""

    def first(*keys: str) -> Any:
        for k in keys:
            if k in raw and raw[k] is not None:
                return raw[k]
        return None

    ticker = first("ticker", "symbol")
    marketcap = first("marketcap", "market_cap")
    rel_volume = first("relative_volume", "stock_volume_vs_avg30_volume")
    volume = first("stock_volume", "volume")
    sector = first("sector")

    # UW 스크리너 응답에는 등락률(%) 필드가 직접 내려오지 않는다.
    # close(현재가)와 prev_close(전일 종가)로 직접 계산한다.
    change_pct = first("perc_change", "change")
    if change_pct is not None:
        change_pct = float(change_pct) * 100
    else:
        close = first("close")
        prev_close = first("prev_close")
        if close is not None and prev_close is not None and float(prev_close) != 0:
            change_pct = (float(close) - float(prev_close)) / float(prev_close) * 100

    return {
        "ticker": ticker,
        "exchange": exchange,
        "change_pct": change_pct,
        "market_cap": float(marketcap) if marketcap is not None else None,
        "relative_volume": float(rel_volume) if rel_volume is not None else None,
        "volume": int(float(volume)) if volume is not None else None,
        "sector": sector,
        "raw": raw,
    }


def scan_nasdaq_surge_stocks(
    *,
    min_price: float = 1.0,
    min_market_cap: float = 50_000_000,
    min_rel_volume: float = 1.5,
    min_change_pct: float = 5.0,
    max_change_pct: float | None = None,
    limit: int = 50,
    use_cache: bool = True,
) -> tuple[int, list[dict[str, Any]]]:
    """
    나스닥 상장 보통주 중 거래량·변동률이 급등한 종목을 스크리닝한다.

    :param min_price: 최소 현재가
    :param min_market_cap: 최소 시가총액(달러)
    :param min_rel_volume: 30일 평균 대비 최소 상대거래량 배수
    :param min_change_pct: 최소 당일 등락률(%). 예: 5.0 = 5%
    :param max_change_pct: 최대 당일 등락률(%), None이면 상한 없음
    :param limit: 최종 반환할 나스닥 종목 최대 개수
    :param use_cache: 짧은 TTL 캐시 사용 여부
    :return: (UW에서 1차로 매칭된 후보 총 개수, 나스닥으로 확인된 결과 행 리스트)
    """
    limit = max(1, min(limit, 200))

    params = dict(
        min_price=min_price,
        min_market_cap=min_market_cap,
        min_rel_volume=min_rel_volume,
        min_change_pct=min_change_pct,
        max_change_pct=max_change_pct,
        limit=limit,
    )
    key = _cache_key(**params)
    now = time.time()
    if use_cache:
        entry = _cache.get(key)
        if entry is not None and now - entry.timestamp < CACHE_TTL_SECONDS:
            return entry.total_candidates, entry.rows

    fetch_limit = min(MAX_CANDIDATES, limit * CANDIDATE_OVERFETCH_MULTIPLIER)

    try:
        candidates = fetch_stock_screener(
            issue_types=["Common Stock"],
            min_change=min_change_pct / 100,
            max_change=(max_change_pct / 100) if max_change_pct is not None else None,
            min_underlying_price=min_price,
            min_marketcap=min_market_cap,
            min_stock_volume_vs_avg30_volume=min_rel_volume,
            order="perc_change",
            order_direction="desc",
            limit=fetch_limit,
        )
    except UWClientError as exc:
        raise ScannerError(f"UW 스크리닝 실패: {exc}") from exc

    total_candidates = len(candidates)
    tickers = [c.get("ticker") or c.get("symbol") for c in candidates]
    tickers = [t for t in tickers if t]

    try:
        exchange_by_ticker = filter_by_exchange(tickers, allowed_exchanges={"NASDAQ"})
    except FMPClientError as exc:
        raise ScannerError(f"FMP 거래소 확인 실패: {exc}") from exc

    rows: list[dict[str, Any]] = []
    for raw in candidates:
        ticker = raw.get("ticker") or raw.get("symbol")
        if ticker in exchange_by_ticker:
            rows.append(_normalize_row(raw, exchange_by_ticker[ticker]))
        if len(rows) >= limit:
            break

    if use_cache:
        _cache[key] = _CacheEntry(timestamp=now, total_candidates=total_candidates, rows=rows)

    return total_candidates, rows
