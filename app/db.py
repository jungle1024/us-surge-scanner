"""
전용 나스닥 유니버스 DB(nasdaq-universe-db) 연결 및 스키마 관리.

이 DB는 `nss-collector-db`(급등주 발굴 V1이 쓰는 DB)와 완전히 분리된 별도 인스턴스다.
RS Rating(시장 전체 대비 상대강도)과 L(업종 내 순위) 계산에만 쓰며, 다른 프로젝트의
데이터나 스키마에는 전혀 관여하지 않는다.

연결 문자열은 환경변수 UNIVERSE_DATABASE_URL로 주입받는다
(Render 대시보드에서 nasdaq-universe-db의 Internal Connection String을 복사해 등록).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras


class DatabaseError(RuntimeError):
    """DB 연결/쿼리 실패 시 발생."""


def _get_dsn() -> str:
    dsn = os.environ.get("UNIVERSE_DATABASE_URL")
    if not dsn:
        raise DatabaseError(
            "환경변수 UNIVERSE_DATABASE_URL이 설정되어 있지 않습니다. "
            "Render의 nasdaq-universe-db 대시보드에서 Internal Connection String을 복사해 등록하세요."
        )
    return dsn


@contextmanager
def get_connection() -> Iterator[psycopg2.extensions.connection]:
    """커넥션을 열고, 정상 종료 시 커밋 / 예외 시 롤백한다."""
    try:
        conn = psycopg2.connect(_get_dsn())
    except psycopg2.OperationalError as exc:
        raise DatabaseError(f"DB 연결 실패: {exc}") from exc
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nasdaq_universe (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    sector TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS daily_price (
    symbol TEXT NOT NULL,
    price_date DATE NOT NULL,
    close NUMERIC NOT NULL,
    PRIMARY KEY (symbol, price_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_price_date ON daily_price (price_date);
CREATE INDEX IF NOT EXISTS idx_daily_price_symbol ON daily_price (symbol);

CREATE TABLE IF NOT EXISTS rs_rating_daily (
    symbol TEXT NOT NULL,
    price_date DATE NOT NULL,
    return_63d NUMERIC,
    return_126d NUMERIC,
    return_252d NUMERIC,
    rs_percentile NUMERIC,
    sector_rank_pct NUMERIC,
    PRIMARY KEY (symbol, price_date)
);
CREATE INDEX IF NOT EXISTS idx_rs_rating_date ON rs_rating_daily (price_date);

CREATE TABLE IF NOT EXISTS ingest_log (
    id SERIAL PRIMARY KEY,
    price_date DATE,
    source TEXT,
    rows_written INTEGER,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_schema() -> None:
    """테이블이 없으면 생성한다 (있으면 아무 일도 하지 않음, 여러 번 실행해도 안전)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


def dict_cursor(conn: psycopg2.extensions.connection):
    """결과를 dict처럼 다룰 수 있는 커서를 반환한다."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
