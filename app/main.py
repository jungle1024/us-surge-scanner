"""
NASDAQ Surge Scanner API

UW(Unusual Whales)와 FMP(Financial Modeling Prep)의 공식 REST API만 사용해
나스닥 상장 주식 중 거래량·변동률 급등주를 찾아주는 독립형 FastAPI 서비스.

기본 스캔(등락률·상대거래량·시총) 위에, 세 가지 스크리닝 방법론을 선택 적용할 수 있다:
- surge (기본값): 추가 조건 없음 — 순수 등락률·거래량 급등주
- volume_breakout: 52주 신고가 근접 + 거래량 실림
- minervini: 미너비니 추세 템플릿 간이 적용 (이동평균 정배열)
- canslim: CANSLIM 스타일 간이 적용 (EPS 성장률)

이전 버전(TradingView 비공식 엔드포인트 기반)에서 안정성 문제로 교체됨.
"""

from __future__ import annotations

import html
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query as FastAPIQuery
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.scanner import (
    ScannerError,
    apply_volume_breakout_filter,
    enrich_with_canslim,
    enrich_with_minervini,
    scan_nasdaq_surge_stocks,
)

app = FastAPI(
    title="NASDAQ Surge Scanner",
    description="UW + FMP 공식 API 기반 나스닥 거래량·변동률 급등주 스캐너",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

Strategy = Literal["surge", "volume_breakout", "minervini", "canslim"]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _apply_strategy(rows: list[dict[str, Any]], strategy: Strategy) -> list[dict[str, Any]]:
    """기본 스캔 결과(rows)에 선택한 방법론을 적용해 필터링/보강한다."""
    if strategy == "surge":
        return rows
    if strategy == "volume_breakout":
        return apply_volume_breakout_filter(rows)
    if strategy == "minervini":
        enriched = enrich_with_minervini(rows)
        return [r for r in enriched if r.get("minervini_pass")]
    if strategy == "canslim":
        enriched = enrich_with_canslim(rows)
        return [r for r in enriched if r.get("canslim_pass")]
    raise HTTPException(status_code=400, detail=f"알 수 없는 strategy: {strategy}")


@app.get("/api/scan")
def api_scan(
    min_price: float = FastAPIQuery(1.0, ge=0, description="최소 현재가"),
    min_market_cap: float = FastAPIQuery(50_000_000, ge=0, description="최소 시가총액"),
    min_rel_volume: float = FastAPIQuery(1.5, ge=0, description="최소 상대거래량 배수(30일 평균 대비)"),
    min_change_pct: float = FastAPIQuery(5.0, description="최소 당일 등락률(%)"),
    max_change_pct: float | None = FastAPIQuery(None, description="최대 당일 등락률(%), 생략 시 무제한"),
    limit: int = FastAPIQuery(50, ge=1, le=200, description="최대 반환 개수(최대 200, 1차 스캔 기준)"),
    strategy: Strategy = FastAPIQuery(
        "surge",
        description="적용할 스크리닝 방법론: surge / volume_breakout / minervini / canslim",
    ),
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
        final_rows = _apply_strategy(rows, strategy)
    except ScannerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "strategy": strategy,
        "uw_candidate_count": total_candidates,
        "nasdaq_count": len(rows),
        "strategy_match_count": len(final_rows),
        "results": final_rows,
    }


def _render_rows_html(rows: list[dict[str, Any]], strategy: Strategy) -> str:
    colspan = {"surge": 6, "volume_breakout": 7, "minervini": 8, "canslim": 8}[strategy]
    if not rows:
        return f"<tr><td colspan='{colspan}' style='text-align:center;padding:24px;color:#888;'>조건에 맞는 나스닥 종목이 없습니다.</td></tr>"

    def esc(value: Any) -> str:
        return html.escape(str(value)) if value is not None else "-"

    def fmt(value: Any, spec: str) -> str:
        return format(value, spec) if isinstance(value, (int, float)) else "-"

    cells = []
    for r in rows:
        change = r.get("change_pct")
        change_color = "#d62828" if isinstance(change, (int, float)) and change >= 0 else "#2a6f97"
        base = (
            "<tr>"
            f"<td>{esc(r.get('ticker'))}</td>"
            f"<td>{esc(r.get('exchange'))}</td>"
            f"<td>{esc(r.get('sector'))}</td>"
            f"<td style='color:{change_color};font-weight:600;'>{fmt(change, '+.2f')}%</td>"
            f"<td>{fmt(r.get('relative_volume'), '.2f')}x</td>"
            f"<td>{fmt(r.get('market_cap'), ',.0f')}</td>"
        )
        if strategy == "volume_breakout":
            base += f"<td>{fmt(r.get('pct_from_52w_high'), '.2f')}%</td>"
        elif strategy == "minervini":
            base += (
                f"<td>{fmt(r.get('sma_50'), '.2f')} / {fmt(r.get('sma_150'), '.2f')} / {fmt(r.get('sma_200'), '.2f')}</td>"
                f"<td>{esc(r.get('minervini_pass'))}</td>"
            )
        elif strategy == "canslim":
            base += (
                f"<td>{fmt(r.get('eps_quarterly_yoy_pct'), '+.1f')}% / {fmt(r.get('eps_annual_yoy_pct'), '+.1f')}%</td>"
                f"<td>{esc(r.get('canslim_pass'))}</td>"
            )
        base += "</tr>"
        cells.append(base)
    return "".join(cells)


def _render_summary(
    strategy: Strategy,
    total_candidates: int,
    nasdaq_count: int,
    final_rows: list[dict[str, Any]],
) -> str:
    """상단에 표시할 '이 화면이 무엇을 찾은 결과인지' 요약 문단을 만든다."""
    match_count = len(final_rows)
    top = final_rows[0] if final_rows else None

    lines = [f"<p>{_STRATEGY_DESCRIPTIONS[strategy]}</p>"]
    lines.append(
        f"<p>UW 1차 후보 <b>{total_candidates}개</b> → 나스닥 상장 <b>{nasdaq_count}개</b> "
        f"→ 이 조건을 만족하는 종목 <b>{match_count}개</b>를 찾았습니다.</p>"
    )

    if match_count == 0:
        lines.append("<p style='color:#888;'>지금 이 조건을 만족하는 종목이 없습니다. 조건이 너무 엄격하거나, 지금 시장에 마땅한 후보가 없는 상태일 수 있습니다.</p>")
    elif top:
        ticker = top.get("ticker")
        change = top.get("change_pct")
        change_str = f"{change:+.2f}%" if isinstance(change, (int, float)) else "-"
        if strategy == "surge":
            lines.append(f"<p>가장 두드러진 종목은 <b>{ticker}</b>({change_str})입니다.</p>")
        elif strategy == "volume_breakout":
            pct = top.get("pct_from_52w_high")
            pct_str = f"{pct:.2f}%" if isinstance(pct, (int, float)) else "-"
            lines.append(f"<p>가장 신고가에 가까운 종목은 <b>{ticker}</b>(52주 고점 대비 {pct_str}, 등락률 {change_str})입니다.</p>")
        elif strategy == "minervini":
            lines.append(f"<p>추세 템플릿을 통과한 종목 중 오늘 등락률이 가장 높은 건 <b>{ticker}</b>({change_str})입니다.</p>")
        elif strategy == "canslim":
            q = top.get("eps_quarterly_yoy_pct")
            a = top.get("eps_annual_yoy_pct")
            q_str = f"{q:+.1f}%" if isinstance(q, (int, float)) else "-"
            a_str = f"{a:+.1f}%" if isinstance(a, (int, float)) else "-"
            lines.append(f"<p>실적 성장이 가장 뚜렷한 종목은 <b>{ticker}</b>(분기 EPS {q_str}, 연간 EPS {a_str})입니다.</p>")

    return "".join(lines)


_STRATEGY_LABELS = {
    "surge": "순수 급등주(등락률·거래량)",
    "volume_breakout": "거래량 돌파(52주 신고가 근접)",
    "minervini": "미너비니 추세 템플릿(간이)",
    "canslim": "CANSLIM 스타일(간이)",
}

_STRATEGY_DESCRIPTIONS = {
    "surge": "오늘 등락률·거래량이 급격히 튄 나스닥 종목을 그대로 모아 보여줍니다. 추가 판정 없이 순위만 매깁니다.",
    "volume_breakout": "급등 후보 중에서도 52주 신고가 근처(10% 이내)까지 도달한 종목만 골라냅니다. '많이 올랐지만 아직 눌려있는' 종목과 '신고가를 뚫는' 종목을 구분합니다.",
    "minervini": "이동평균(50/150/200일)이 짧은 기간일수록 위에 있는 정배열 구조인 종목만 골라냅니다. 마크 미너비니의 추세 템플릿을 5개 핵심 조건으로 간이 적용했습니다.",
    "canslim": "최근 분기·연간 EPS 성장률이 각각 25% 이상인 종목만 골라냅니다. 윌리엄 오닐의 CANSLIM 중 실적 관련 3개 요소(C·A·S)를 간이 적용했습니다.",
}

_STRATEGY_ICONS = {
    "surge": "📈",
    "volume_breakout": "🚀",
    "minervini": "📐",
    "canslim": "💰",
}

_STRATEGY_EXTRA_HEADERS = {
    "surge": "",
    "volume_breakout": "<th>52주 고점 대비</th>",
    "minervini": "<th>SMA 50/150/200</th><th>미너비니 통과</th>",
    "canslim": "<th>EPS 성장(분기/연간)</th><th>CANSLIM 통과</th>",
}


@app.get("/scan", response_class=HTMLResponse)
def scan_page(
    min_price: float = FastAPIQuery(1.0, ge=0),
    min_market_cap: float = FastAPIQuery(50_000_000, ge=0),
    min_rel_volume: float = FastAPIQuery(1.5, ge=0),
    min_change_pct: float = FastAPIQuery(5.0),
    limit: int = FastAPIQuery(50, ge=1, le=200),
    strategy: Strategy = FastAPIQuery("surge"),
) -> str:
    """스캔 결과 화면 — 상단에 결과 요약, 하단에 발굴 종목 표. 메인 소비 형태는 /api/scan JSON이다."""
    rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    total_candidates = 0
    try:
        total_candidates, rows = scan_nasdaq_surge_stocks(
            min_price=min_price,
            min_market_cap=min_market_cap,
            min_rel_volume=min_rel_volume,
            min_change_pct=min_change_pct,
            limit=limit,
        )
        final_rows = _apply_strategy(rows, strategy)
        summary = _render_summary(strategy, total_candidates, len(rows), final_rows)
        body = _render_rows_html(final_rows, strategy)
        error_banner = ""
    except ScannerError as exc:
        summary = ""
        body = ""
        error_banner = f"<p style='color:#d62828;'>스캔 실패: {html.escape(str(exc))}</p>"

    strategy_nav = " | ".join(
        f"<a href='/scan?strategy={key}'>{'<b>' + label + '</b>' if key == strategy else label}</a>"
        for key, label in _STRATEGY_LABELS.items()
    )

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{_STRATEGY_ICONS[strategy]} {_STRATEGY_LABELS[strategy]} - NASDAQ Surge Scanner</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 32px; background: #fafafa; color: #222; }}
  h1 {{ font-size: 20px; }}
  .back {{ font-size: 13px; margin-bottom: 12px; }}
  .back a {{ color: #666; text-decoration: none; }}
  .summary {{ background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; font-size: 14px; line-height: 1.6; }}
  .summary p {{ margin: 4px 0; }}
  .nav {{ margin-bottom: 16px; font-size: 13px; }}
  .nav a {{ color: #2a6f97; text-decoration: none; margin-right: 4px; }}
  .params {{ color: #999; font-size: 12px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  th, td {{ padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right; font-size: 13px; }}
  th {{ background: #f0f0f0; text-align: right; }}
  th:nth-child(1), th:nth-child(2), th:nth-child(3), td:nth-child(1), td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
  code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
  <p class="back">&larr; <a href="/">미국 나스닥 스캐너 메인으로</a></p>
  <h1>{_STRATEGY_ICONS[strategy]} {_STRATEGY_LABELS[strategy]}</h1>
  <p class="nav">다른 방법론: {strategy_nav}</p>
  <div class="summary">{summary}{error_banner}</div>
  <p class="params">조건: 가격≥{min_price}, 시총≥{min_market_cap:,.0f}, 상대거래량≥{min_rel_volume}x, 등락률≥{min_change_pct}%
    &nbsp;|&nbsp; JSON: <code>/api/scan?strategy={strategy}</code></p>
  <table>
    <thead><tr>
      <th>티커</th><th>거래소</th><th>섹터</th><th>등락률</th><th>상대거래량</th><th>시가총액</th>{_STRATEGY_EXTRA_HEADERS[strategy]}
    </tr></thead>
    <tbody>{body}</tbody>
  </table>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def landing() -> str:
    """메인 페이지 — 방법론별 배너를 클릭하면 각 스캔 화면(/scan?strategy=...)으로 이동한다."""
    cards = "".join(
        f"""
        <a class="card" href="/scan?strategy={key}">
          <div class="card-icon">{_STRATEGY_ICONS[key]}</div>
          <div class="card-title">{label}</div>
          <div class="card-desc">{_STRATEGY_DESCRIPTIONS[key]}</div>
        </a>
        """
        for key, label in _STRATEGY_LABELS.items()
    )

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>미국 나스닥 스캐너</title>
<style>
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 0; background: #fafafa; color: #222; }}
  .hero {{ padding: 56px 32px 32px; text-align: center; }}
  .hero h1 {{ font-size: 32px; margin: 0 0 8px; }}
  .hero p {{ color: #666; font-size: 15px; margin: 0; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; max-width: 1000px; margin: 32px auto; padding: 0 32px 56px; }}
  .card {{ display: block; background: #fff; border: 1px solid #eee; border-radius: 12px; padding: 24px; text-decoration: none; color: #222; transition: box-shadow 0.15s, transform 0.15s; }}
  .card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.08); transform: translateY(-2px); }}
  .card-icon {{ font-size: 32px; margin-bottom: 12px; }}
  .card-title {{ font-size: 17px; font-weight: 700; margin-bottom: 8px; }}
  .card-desc {{ font-size: 13px; color: #666; line-height: 1.5; }}
  .footer {{ text-align: center; color: #999; font-size: 12px; padding-bottom: 40px; }}
  .footer code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
  <div class="hero">
    <h1>🇺🇸 미국 나스닥 스캐너</h1>
    <p>UW(Unusual Whales) + FMP(Financial Modeling Prep) 공식 API 기반. 아래 방법론 중 하나를 선택하세요.</p>
  </div>
  <div class="grid">{cards}</div>
  <p class="footer">JSON API: <code>/api/scan?strategy=surge|volume_breakout|minervini|canslim</code></p>
</body>
</html>
"""
