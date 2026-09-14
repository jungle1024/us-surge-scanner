"""
관리자용 엔드포인트.

Render 무료 플랜은 Shell을 지원하지 않아서, 백필·일별 갱신을 터미널 없이
브라우저 주소창에 URL을 입력하는 것만으로 실행할 수 있게 만든 임시 통로다.
아무나 실행하면 안 되므로 ADMIN_TOKEN 환경변수와 일치하는 token 쿼리 파라미터가
있어야만 동작한다.

주의: 백필은 한 번 호출에 시간이 걸릴 수 있다. 요청 타임아웃을 피하려면
`days`를 50 이하로 유지하고, `offset`을 바꿔가며 여러 번 나눠 호출할 것.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from app.backfill import run_backfill, run_daily_update
from app.db import DatabaseError

router = APIRouter(prefix="/admin")


def _check_token(token: str) -> None:
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="환경변수 ADMIN_TOKEN이 설정되어 있지 않습니다. Render 환경변수에 먼저 등록하세요.",
        )
    if token != expected:
        raise HTTPException(status_code=401, detail="token이 올바르지 않습니다.")


@router.get("/backfill")
def admin_backfill(
    token: str = Query(...),
    days: int = Query(5, ge=1, le=252, description="백필할 거래일 수 (요청 타임아웃 방지를 위해 50 이하 권장)"),
    offset: int = Query(0, ge=0, description="오늘로부터 몇 거래일 건너뛰고 시작할지"),
) -> dict:
    _check_token(token)
    try:
        return run_backfill(days=days, offset=offset)
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/daily-update")
def admin_daily_update(token: str = Query(...)) -> dict:
    _check_token(token)
    try:
        return run_daily_update()
    except DatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
