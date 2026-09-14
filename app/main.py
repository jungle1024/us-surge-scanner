"""
NASDAQ Surge Scanner API

UW(Unusual Whales)와 FMP(Financial Modeling Prep)의 공식 REST API만 사용해
나스닥 상장 주식 중 거래량·변동률 급등주를 찾아주는 독립형 FastAPI 서비스.

이전 버전(TradingView 비공식 엔드포인트 기반)에서 안정성 문제로 교체됨.
"""

from __future__ import annotations

import html
from typing import Any

from fastapi import FastAPI, HTTPException, Query as FastAPIQuery
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.scanner import ScannerError, scan_nasdaq_surge_stocks

app = FastAPI(
    title="NASDAQ Surge Scanner",
    description="UW + FMP 공식 API 기반 나스닥 거래량·변동률 급등주 스캐너",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scan")
def api_scan(
    min_price: float = FastAPIQuery(1.0, ge=0, description="최소 현재가"),
    min_market_cap: float = FastAPIQuery(50_000_000, ge=0, description="최소 시가총액"),
    min_rel_volume: float = FastAPIQuery(1.5, ge=0, description="최소 상대거래량 배수(30일 평균 대비)"),
    min_change_pct: float = FastAPIQuery(5.0, description="최소 당일 등락률(%)"),
    max_change_pct: float | None = FastAPIQuery(None, description="최대 당일 등락률(%), 생략 시 무제한"),
    limit: int = FastAPIQuery(50, ge=1, le=200, description="최대 반환 개수(최대 200)"),
) -> dict[str, Any]:
    """조건에 맞는 나스닥 급등주 목록을 JSON으로 반환한다."""
    try:
        total_candidates, rows = scan_nasdaq_surge_stocks(
            min_price=min_price,
            min_market_cap=min_market_cap,
            min_rel_volume=min_rel_volume,
            min_change_pct=min_change_pct,
            max_change_pct=max_change_pct,
            limit=limit,
        )
    except ScannerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "uw_candidate_count": total_candidates,
        "nasdaq_count": len(rows),
        "results": rows,
    }


def _render_rows_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan='6' style='text-align:center;padding:24px;color:#888;'>조건에 맞는 나스닥 종목이 없습니다.</td></tr>"

    def esc(value: Any) -> str:
        return html.escape(str(value)) if value is not None else "-"

    def fmt(value: Any, spec: str) -> str:
        return format(value, spec) if isinstance(value, (int, float)) else "-"

    cells = []
    for r in rows:
        change = r.get("change_pct")
        change_color = "#d62828" if isinstance(change, (int, float)) and change >= 0 else "#2a6f97"
        cells.append(
            "<tr>"
            f"<td>{esc(r.get('ticker'))}</td>"
            f"<td>{esc(r.get('exchange'))}</td>"
            f"<td>{esc(r.get('sector'))}</td>"
            f"<td style='color:{change_color};font-weight:600;'>{fmt(change, '+.2f')}%</td>"
            f"<td>{fmt(r.get('relative_volume'), '.2f')}x</td>"
            f"<td>{fmt(r.get('market_cap'), ',.0f')}</td>"
            "</tr>"
        )
    return "".join(cells)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    min_price: float = FastAPIQuery(1.0, ge=0),
    min_market_cap: float = FastAPIQuery(50_000_000, ge=0),
    min_rel_volume: float = FastAPIQuery(1.5, ge=0),
    min_change_pct: float = FastAPIQuery(5.0),
    limit: int = FastAPIQuery(50, ge=1, le=200),
) -> str:
    """빠른 확인용 최소 HTML 대시보드. 메인 소비 형태는 /api/scan JSON이다."""
    try:
        total_candidates, rows = scan_nasdaq_surge_stocks(
            min_price=min_price,
            min_market_cap=min_market_cap,
            min_rel_volume=min_rel_volume,
            min_change_pct=min_change_pct,
            limit=limit,
        )
        body = _render_rows_html(rows)
        error_banner = ""
    except ScannerError as exc:
        total_candidates = 0
        rows = []
        body = ""
        error_banner = f"<p style='color:#d62828;'>스캔 실패: {html.escape(str(exc))}</p>"

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>NASDAQ Surge Scanner</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 32px; background: #fafafa; color: #222; }}
  h1 {{ font-size: 20px; }}
  .meta {{ color: #666; margin-bottom: 16px; font-size: 14px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  th, td {{ padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right; font-size: 13px; }}
  th {{ background: #f0f0f0; text-align: right; }}
  th:nth-child(1), th:nth-child(2), th:nth-child(3), td:nth-child(1), td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
  code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
  <h1>📈 NASDAQ Surge Scanner</h1>
  <p class="meta">조건: 가격≥{min_price}, 시총≥{min_market_cap:,.0f}, 상대거래량≥{min_rel_volume}x, 등락률≥{min_change_pct}%
    &nbsp;|&nbsp; UW 1차 후보 {total_candidates}개 중 나스닥 {len(rows)}개 표시 &nbsp;|&nbsp; JSON: <code>/api/scan</code></p>
  {error_banner}
  <table>
    <thead><tr>
      <th>티커</th><th>거래소</th><th>섹터</th><th>등락률</th><th>상대거래량</th><th>시가총액</th>
    </tr></thead>
    <tbody>{body}</tbody>
  </table>
</body>
</html>
"""
