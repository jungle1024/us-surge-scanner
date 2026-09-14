"""
UW 기술적 지표 REST 클라이언트.

미너비니 추세 템플릿(Trend Template) 판정에 필요한 이동평균(SMA)을 조회한다.
문서: https://api.unusualwhales.com/docs/operations/PublicApi.AvFundamentalController.technical_indicator
- GET /api/stock/{ticker}/technical-indicator/{function}
"""

from __future__ import annotations

import os
import time

import requests

UW_BASE_URL = "https://api.unusualwhales.com"
_TIMEOUT_SECONDS = 15

# 일봉 이동평균은 장중에도 거의 안 바뀌므로 넉넉하게 캐시해서 호출 횟수를 아낀다.
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6시간
_cache: dict[str, tuple[float, float | None]] = {}


class TechnicalClientError(RuntimeError):
    """UW 기술적 지표 API 호출 실패 시 발생."""


def _get_token() -> str:
    token = os.environ.get("UW_API_KEY")
    if not token:
        raise TechnicalClientError(
            "환경변수 UW_API_KEY가 설정되어 있지 않습니다. Render 서비스의 환경변수를 확인하세요."
        )
    return token


def get_latest_sma(ticker: str, period: int) -> float | None:
    """
    가장 최근 거래일 기준 단순이동평균(SMA)을 반환한다.
    조회 실패(레이트리밋, 데이터 없음 등) 시 예외를 던지지 않고 None을 반환한다 —
    한 종목의 지표 조회 실패가 전체 스캔을 중단시키지 않도록 하기 위함.
    """
    cache_key = f"{ticker}:{period}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    token = _get_token()
    try:
        resp = requests.get(
            f"{UW_BASE_URL}/api/stock/{ticker}/technical-indicator/SMA",
            params={"interval": "daily", "time_period": period},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return None

    if not resp.ok:
        _cache[cache_key] = (now, None)
        return None

    body = resp.json()
    rows = body.get("data", body) if isinstance(body, dict) else body
    if not rows:
        _cache[cache_key] = (now, None)
        return None

    # 응답은 최신 날짜가 맨 앞에 오는 배열: [{"date": ..., "values": {"SMA": "260.55"}}, ...]
    value = rows[0].get("values", {}).get("SMA")
    result = float(value) if value is not None else None
    _cache[cache_key] = (now, result)
    return result
