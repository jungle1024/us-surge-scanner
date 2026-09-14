"""
FMP 재무제표 REST 클라이언트.

CANSLIM 스타일 판정에 필요한 분기·연간 EPS 전년 대비 성장률을 계산한다.
FMP의 growth 엔드포인트는 직전 기간 대비(QoQ) 값이라 CANSLIM 원래 정의(전년 동기 대비, YoY)와
다르므로, 원자료(income-statement)를 직접 받아 전년 동기/전년 대비로 직접 계산한다.

문서: https://financialmodelingprep.com/stable/income-statement?symbol=AAPL&period=quarter&apikey=...
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
_TIMEOUT_SECONDS = 10

# 분기/연간 실적은 자주 안 바뀌므로 넉넉하게 캐시.
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6시간
_cache: dict[str, tuple[float, dict[str, float | None]]] = {}


class FundamentalsClientError(RuntimeError):
    """FMP 재무제표 API 호출 실패 시 발생."""


def _get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise FundamentalsClientError(
            "환경변수 FMP_API_KEY가 설정되어 있지 않습니다. Render 서비스의 환경변수를 확인하세요."
        )
    return key


def _eps(row: dict[str, Any]) -> float | None:
    value = row.get("epsDiluted", row.get("eps"))
    return float(value) if value is not None else None


def get_eps_growth(ticker: str) -> dict[str, float | None]:
    """
    분기 EPS 전년동기 대비 성장률(quarterly_yoy_pct)과
    연간 EPS 전년 대비 성장률(annual_yoy_pct)을 %로 반환한다.
    데이터가 부족하거나 조회 실패 시 해당 값은 None.
    """
    now = time.time()
    cached = _cache.get(ticker)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    api_key = _get_api_key()
    result: dict[str, float | None] = {"quarterly_yoy_pct": None, "annual_yoy_pct": None}

    # 분기: 최근 5개 분기를 받아 [0](최신) vs [4](1년 전 같은 분기) 비교
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/income-statement",
            params={"symbol": ticker, "period": "quarter", "limit": 5, "apikey": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
        if resp.ok:
            rows = resp.json()
            if isinstance(rows, list) and len(rows) >= 5:
                latest, year_ago = _eps(rows[0]), _eps(rows[4])
                if latest is not None and year_ago not in (None, 0):
                    result["quarterly_yoy_pct"] = (latest - year_ago) / abs(year_ago) * 100
    except requests.RequestException:
        pass

    # 연간: 최근 2개 회계연도 비교
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/income-statement",
            params={"symbol": ticker, "period": "annual", "limit": 2, "apikey": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
        if resp.ok:
            rows = resp.json()
            if isinstance(rows, list) and len(rows) >= 2:
                latest, prior = _eps(rows[0]), _eps(rows[1])
                if latest is not None and prior not in (None, 0):
                    result["annual_yoy_pct"] = (latest - prior) / abs(prior) * 100
    except requests.RequestException:
        pass

    _cache[ticker] = (now, result)
    return result
