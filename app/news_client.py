"""
FMP 종목 뉴스 REST 클라이언트.

CANSLIM의 'N'(New — 신제품·신경영진·신고가 등 새로운 촉매) 판정 재료로 쓸
최근 헤드라인을 가져온다. 실제 "새로운 촉매인지" 판단은 이 헤드라인들을
Claude(app/anthropic_client.py)에게 넘겨서 하게 한다.

문서: https://financialmodelingprep.com/stable/news/stock?symbols=AAPL&apikey=...
"""

from __future__ import annotations

import os
import time

import requests

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
_TIMEOUT_SECONDS = 10

_CACHE_TTL_SECONDS = 3 * 60 * 60  # 3시간
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


class NewsClientError(RuntimeError):
    """FMP 뉴스 API 호출 실패 시 발생."""


def _get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise NewsClientError(
            "환경변수 FMP_API_KEY가 설정되어 있지 않습니다. Render 서비스의 환경변수를 확인하세요."
        )
    return key


def fetch_recent_headlines(ticker: str, limit: int = 5) -> list[dict[str, str]]:
    """
    최근 뉴스 헤드라인 목록을 반환한다. 각 항목: {"title": ..., "text": ..., "published_date": ...}
    조회 실패 시 빈 리스트(예외를 던지지 않음 — 뉴스 부족이 전체 스캔을 막을 이유는 아니므로).
    """
    cache_key = f"{ticker}:{limit}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    api_key = _get_api_key()
    try:
        resp = requests.get(
            f"{FMP_BASE_URL}/news/stock",
            params={"symbols": ticker, "limit": limit, "apikey": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return []

    if not resp.ok:
        _cache[cache_key] = (now, [])
        return []

    rows = resp.json()
    headlines = []
    if isinstance(rows, list):
        for row in rows[:limit]:
            headlines.append({
                "title": row.get("title", ""),
                "text": row.get("text", ""),
                "published_date": row.get("publishedDate", ""),
            })

    _cache[cache_key] = (now, headlines)
    return headlines
