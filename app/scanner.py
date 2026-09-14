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

from app.anthropic_client import judge_new_catalyst
from app.fmp_client import FMPClientError, filter_by_exchange, get_institutional_ownership_trend
from app.fundamentals_client import FundamentalsClientError, get_eps_growth
from app.market_client import MarketClientError, get_market_direction
from app.news_client import NewsClientError, fetch_recent_headlines
from app.rs_rating import get_rs_rating
from app.technical_client import TechnicalClientError, get_latest_sma, get_sma_trend
from app.uw_client import UWClientError, fetch_stock_screener

CACHE_TTL_SECONDS = 30

# 미너비니는 후보 1개당 UW를 여러 번 호출하므로, 응답 시간과 API 사용량을
# 통제하기 위해 이미 등락률 순으로 정렬된 상위 N개 후보에만 적용한다.
STRATEGY_ENRICH_LIMIT = 15

# CANSLIM은 종목당 FMP 호출 3회 + 뉴스 조회 + Claude 호출까지 붙어서 더 느리고 비싸다.
# 응답 시간을 감당할 수 있는 수준으로 더 적게 적용한다.
CANSLIM_ENRICH_LIMIT = 8

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
    week_52_high = first("week_52_high")
    week_52_low = first("week_52_low")
    close = first("close")

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
        "close": float(close) if close is not None else None,
        "week_52_high": float(week_52_high) if week_52_high is not None else None,
        "week_52_low": float(week_52_low) if week_52_low is not None else None,
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


# ---------------------------------------------------------------------------
# 스크리닝 방법론 확장: 거래량 돌파 / 미너비니 추세 템플릿 / CANSLIM 스타일
#
# 세 함수 모두 scan_nasdaq_surge_stocks()가 이미 뽑아준 결과(rows)를 입력으로 받아,
# 그 위에 조건을 더 걸거나 지표를 추가로 붙인 뒤 (필터링된 결과, 판정 근거) 형태로 반환한다.
# 즉 1차 스크리닝 범위(가격·시총·등락률·상대거래량)는 그대로 두고, "여기서 어떤 스타일에
# 더 잘 맞는가"를 판정하는 2차 필터다.
# ---------------------------------------------------------------------------


def apply_volume_breakout_filter(
    rows: list[dict[str, Any]],
    *,
    near_high_pct: float = 10.0,
) -> list[dict[str, Any]]:
    """
    거래량 돌파(Volume Breakthrough) 스타일: 52주 신고가 근처에서 거래량이 실린 종목만 남긴다.

    추가 API 호출 없음 — UW 1차 스크리닝 결과에 이미 포함된 52주 고점 필드로 판정한다.

    :param near_high_pct: 52주 고점 대비 몇 % 이내여야 "신고가 근접"으로 볼지 (기본 10%)
    """
    result = []
    for row in rows:
        close = row.get("close")
        week_52_high = row.get("week_52_high")
        if close is None or not week_52_high:
            continue
        pct_from_high = (week_52_high - close) / week_52_high * 100
        if pct_from_high <= near_high_pct:
            row = {**row, "pct_from_52w_high": pct_from_high}
            result.append(row)
    return result


def enrich_with_minervini(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    미너비니 추세 템플릿(Trend Template)을 적용한다 (원래 8개 조건 중 7개):
    1. 현재가 > 50일선 > 150일선 > 200일선 (정배열)
    2. 현재가가 52주 저점 대비 30% 이상 상승
    3. 현재가가 52주 고점 대비 25% 이내
    4. 200일선이 최소 1개월(21거래일)간 상승 추세
    5. RS Rating(나스닥 전체 대비 상대강도) 70 이상 — 전용 DB(nasdaq-universe-db)에서 조회.
       DB에 아직 126거래일치 데이터가 쌓이지 않았으면 이 조건은 건너뛰고 1~4번만으로 판정한다
       (rs_percentile이 응답에 None으로 표시되면 "아직 판정 제외 중"이라는 뜻).

    상위 STRATEGY_ENRICH_LIMIT개 후보에만 적용(UW 호출 4회/종목 + RS Rating DB 조회 1회).
    minervini_pass가 True/False/None(데이터 부족으로 판정 불가)인 채로 전체를 반환한다 —
    걸러내는 건 호출부에서 원하는 대로 하도록.
    """
    result = []
    for row in rows[:STRATEGY_ENRICH_LIMIT]:
        ticker = row.get("ticker")
        close = row.get("close")
        week_52_high = row.get("week_52_high")
        week_52_low = row.get("week_52_low")

        try:
            sma_50 = get_latest_sma(ticker, 50)
            sma_150 = get_latest_sma(ticker, 150)
            sma_200 = get_latest_sma(ticker, 200)
            sma_200_trend = get_sma_trend(ticker, 200, lookback_days=21)
        except TechnicalClientError as exc:
            raise ScannerError(f"미너비니 지표 조회 실패({ticker}): {exc}") from exc

        sma_200_rising = None
        if sma_200_trend is not None:
            sma_200_now, sma_200_past = sma_200_trend
            sma_200_rising = sma_200_now > sma_200_past

        rs = get_rs_rating(ticker)
        rs_percentile = rs["rs_percentile"] if rs else None

        core_pass = None
        if None not in (close, sma_50, sma_150, sma_200, week_52_high, week_52_low, sma_200_rising):
            above_low_pct = (close - week_52_low) / week_52_low * 100
            below_high_pct = (week_52_high - close) / week_52_high * 100
            core_pass = (
                close > sma_50 > sma_150 > sma_200
                and above_low_pct >= 30
                and below_high_pct <= 25
                and sma_200_rising
            )

        # RS Rating 데이터가 아직 없으면(초기 데이터 축적 기간) 이 조건 없이 core_pass만으로 판정한다.
        if core_pass is not None and rs_percentile is not None:
            minervini_pass = core_pass and rs_percentile >= 70
        else:
            minervini_pass = core_pass

        result.append({
            **row,
            "sma_50": sma_50,
            "sma_150": sma_150,
            "sma_200": sma_200,
            "sma_200_rising": sma_200_rising,
            "rs_percentile": rs_percentile,
            "minervini_pass": minervini_pass,
        })
    return result


def enrich_with_canslim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    CANSLIM 스타일을 적용한다 (7글자 전체: C·A·N·S·L·I·M):
    - C: 최근 분기 EPS가 전년 동기 대비 25% 이상 성장
    - A: 최근 회계연도 EPS가 전년 대비 25% 이상 성장
    - N: 최근 뉴스에 '새로운 촉매'(신제품/신경영진/긍정적 이벤트 등)가 있는지 Claude가 판정
    - S: 상대거래량 1.5배 이상 (수급)
    - L: 업종 내 상대강도 순위 70퍼센타일 이상 — 전용 DB(nasdaq-universe-db)에서 조회.
      DB에 아직 데이터가 부족하면 이 조건은 건너뛰고 나머지만으로 판정한다.
    - I: 기관 보유 비중이 직전 분기 대비 증가
    - M: 시장 전체(QQQ)가 50일선 위 — 상승장에서만 유효한 전략이라는 원 취지를 반영

    상위 CANSLIM_ENRICH_LIMIT개 후보에만 적용(종목당 FMP 3회 + 뉴스 1회 + Claude 1회 + RS DB 조회 1회).
    canslim_pass가 True/False/None(데이터 부족)인 채로 전체를 반환한다.
    """
    try:
        market = get_market_direction()
    except MarketClientError as exc:
        raise ScannerError(f"CANSLIM 시장 방향(M) 판정 실패: {exc}") from exc
    market_bullish = market["is_bullish"]

    result = []
    for row in rows[:CANSLIM_ENRICH_LIMIT]:
        ticker = row.get("ticker")
        rel_volume = row.get("relative_volume")

        try:
            eps_growth = get_eps_growth(ticker)
        except FundamentalsClientError as exc:
            raise ScannerError(f"CANSLIM 지표 조회 실패({ticker}): {exc}") from exc
        quarterly_yoy = eps_growth["quarterly_yoy_pct"]
        annual_yoy = eps_growth["annual_yoy_pct"]

        try:
            ownership = get_institutional_ownership_trend(ticker)
        except FMPClientError as exc:
            raise ScannerError(f"CANSLIM 기관 보유(I) 조회 실패({ticker}): {exc}") from exc
        ownership_pct_change = ownership["ownership_pct_change"]
        institutional_buying = (
            ownership_pct_change > 0 if ownership_pct_change is not None else None
        )

        try:
            headlines = fetch_recent_headlines(ticker)
        except NewsClientError as exc:
            raise ScannerError(f"CANSLIM 뉴스(N) 조회 실패({ticker}): {exc}") from exc
        new_catalyst = judge_new_catalyst(ticker, headlines)
        is_new = new_catalyst["is_new"] if new_catalyst else None
        new_catalyst_reason = new_catalyst["reason"] if new_catalyst else None

        rs = get_rs_rating(ticker)
        sector_rank_pct = rs["sector_rank_pct"] if rs else None

        core_pass = None
        core_conditions = (quarterly_yoy, annual_yoy, rel_volume, market_bullish, institutional_buying, is_new)
        if None not in core_conditions:
            core_pass = (
                quarterly_yoy >= 25
                and annual_yoy >= 25
                and rel_volume >= 1.5
                and market_bullish
                and institutional_buying
                and is_new
            )

        # 업종 내 순위 데이터가 아직 없으면(초기 데이터 축적 기간) 이 조건 없이 core_pass만으로 판정한다.
        if core_pass is not None and sector_rank_pct is not None:
            canslim_pass = core_pass and sector_rank_pct >= 70
        else:
            canslim_pass = core_pass

        result.append({
            **row,
            "eps_quarterly_yoy_pct": quarterly_yoy,
            "eps_annual_yoy_pct": annual_yoy,
            "market_bullish": market_bullish,
            "institutional_ownership_pct": ownership["ownership_pct"],
            "institutional_ownership_pct_change": ownership_pct_change,
            "new_catalyst": is_new,
            "new_catalyst_reason": new_catalyst_reason,
            "sector_rank_pct": sector_rank_pct,
            "canslim_pass": canslim_pass,
        })
    return result
