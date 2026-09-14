"""
Unusual Whales(UW) 공식 공개 REST API 클라이언트.

문서: https://api.unusualwhales.com/docs/operations/PublicApi.ScreenerController.stock_screener
- Base URL: https://api.unusualwhales.com
- 인증: HTTP 헤더 Authorization: Bearer <API_TOKEN>
- API 키 발급/확인: https://unusualwhales.com/information/how-to-check-your-api-usage
"""

from __future__ import annotations

import os

import requests

UW_BASE_URL = "https://api.unusualwhales.com"
UW_TIMEOUT_SECONDS = 15


class UWClientError(RuntimeError):
    """UW API 호출 실패 시 발생."""


def _get_token() -> str:
    token = os.environ.get("UW_API_KEY")
    if not token:
        raise UWClientError(
            "환경변수 UW_API_KEY이 설정되어 있지 않습니다. "
            "https://unusualwhales.com/information/how-to-check-your-api-usage 에서 발급받은 "
            "API 토큰을 Render 서비스의 환경변수로 등록하세요."
        )
    return token


def fetch_stock_screener(**params: object) -> list[dict]:
    """
    GET /api/screener/stocks 를 호출해 스크리너 결과 목록을 반환한다.

    params는 UW 공식 문서의 쿼리 파라미터를 그대로 전달한다.
    (예: min_change=0.05, min_marketcap=50000000, min_stock_volume_vs_avg30_volume=1.5 ...)
    배열형 파라미터(issue_types, sectors)는 리스트로 넘기면 requests가 issue_types[]=... 형태로
    자동 변환해주지 않으므로, 아래에서 직접 '키[]' 형식으로 바꿔준다.
    """
    token = _get_token()

    query: list[tuple[str, object]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                query.append((f"{key}[]", item))
        else:
            query.append((key, value))

    try:
        resp = requests.get(
            f"{UW_BASE_URL}/api/screener/stocks",
            params=query,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=UW_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise UWClientError(f"UW API 요청 실패(네트워크): {exc}") from exc

    if not resp.ok:
        raise UWClientError(f"UW API 오류 {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    # UW 응답은 보통 {"data": [...]} 형태다.
    if isinstance(body, dict):
        return body.get("data", [])
    if isinstance(body, list):
        return body
    raise UWClientError(f"예상치 못한 UW 응답 형식: {type(body)}")
