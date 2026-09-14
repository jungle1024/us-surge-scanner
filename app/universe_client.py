"""
나스닥 보통주 전체 종목 목록을 FMP에서 받아온다 (ETF·펀드 제외).

문서: https://financialmodelingprep.com/stable/company-screener
     ?exchange=NASDAQ&isEtf=false&isFund=false&isActivelyTrading=true&limit=...&page=...
"""

from __future__ import annotations

import os
import time

import requests

FMP_BASE_URL = "https://financialmodelingprep.com/stable"
_TIMEOUT_SECONDS = 20


class UniverseClientError(RuntimeError):
    """FMP 유니버스 조회 실패 시 발생."""


def _get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise UniverseClientError("환경변수 FMP_API_KEY가 설정되어 있지 않습니다.")
    return key


def fetch_nasdaq_common_stock_universe(
    *, page_size: int = 1000, max_pages: int = 20
) -> list[dict]:
    """
    나스닥 보통주 전체를 {symbol, company_name, sector} 리스트로 반환한다.
    ETF·펀드·비활성 종목은 제외한다. 페이지네이션으로 전체를 순회한다.
    """
    api_key = _get_api_key()
    results: list[dict] = []
    seen: set[str] = set()

    for page in range(max_pages):
        try:
            resp = requests.get(
                f"{FMP_BASE_URL}/company-screener",
                params={
                    "exchange": "NASDAQ",
                    "isEtf": "false",
                    "isFund": "false",
                    "isActivelyTrading": "true",
                    "limit": page_size,
                    "page": page,
                    "apikey": api_key,
                },
                timeout=_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise UniverseClientError(f"FMP 유니버스 조회 실패(네트워크): {exc}") from exc

        if not resp.ok:
            raise UniverseClientError(f"FMP 유니버스 조회 실패: {resp.status_code} {resp.text[:200]}")

        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            symbol = row.get("symbol")
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            results.append({
                "symbol": symbol,
                "company_name": row.get("companyName"),
                "sector": row.get("sector"),
            })

        if len(rows) < page_size:
            break
        time.sleep(0.2)  # 레이트리밋 여유

    return results
