"""
Anthropic API(Claude) 클라이언트.

CANSLIM의 'N'(New — 신제품, 신경영진, 신고가 등 새로운 촉매)은 숫자로 판정할 수 없는
정성적 조건이라, 최근 뉴스 헤드라인을 Claude에게 보여주고 "새로운 촉매로 볼 만한
뉴스가 있는지"를 판단하게 한다.

문서: https://docs.claude.com/en/api/messages
"""

from __future__ import annotations

import json
import os
import time

import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# 단순 분류 작업이라 저렴하고 빠른 모델을 쓴다.
MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT_SECONDS = 20

_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6시간
_cache: dict[str, tuple[float, dict[str, object]]] = {}


class AnthropicClientError(RuntimeError):
    """Anthropic API 호출 실패 시 발생."""


def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AnthropicClientError(
            "환경변수 ANTHROPIC_API_KEY가 설정되어 있지 않습니다. "
            "Anthropic 콘솔에서 발급받은 API 키를 Render 서비스의 환경변수로 등록하세요."
        )
    return key


_SYSTEM_PROMPT = (
    "너는 CANSLIM 투자 기법의 'N'(New) 조건을 판정하는 애널리스트야. "
    "N은 신제품 출시, 새로운 경영진, 새로운 산업 동향, 52주 신고가 경신 등 "
    "주가를 밀어올릴 만한 '새로운' 촉매가 있는지를 뜻해. "
    "주어진 최근 뉴스 헤드라인만 보고 판단하고, 반드시 아래 JSON 형식으로만 답해. "
    '{"is_new": true 또는 false, "reason": "한국어로 한 문장 이유"}'
)


def judge_new_catalyst(ticker: str, headlines: list[dict[str, str]]) -> dict[str, object] | None:
    """
    :param headlines: [{"title":..., "text":..., "published_date":...}, ...]
    :return: {"is_new": bool, "reason": str} 또는 판정 불가 시 None.
             (뉴스가 없거나, API 키가 없거나, 호출 실패 시 — 이 종목 하나의 실패가
             전체 CANSLIM 스캔을 막지 않도록 예외를 던지지 않고 None을 반환한다.)
    """
    if not headlines:
        return None

    cache_key = f"{ticker}:{len(headlines)}:{headlines[0].get('published_date', '')}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]  # type: ignore[return-value]

    try:
        api_key = _get_api_key()
    except AnthropicClientError:
        return None

    headline_text = "\n".join(
        f"- ({h.get('published_date', '')}) {h.get('title', '')}: {h.get('text', '')}"
        for h in headlines
    )
    user_prompt = f"티커: {ticker}\n\n최근 뉴스:\n{headline_text}"

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 200,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return None

    if not resp.ok:
        return None

    try:
        body = resp.json()
        text = body["content"][0]["text"]
        # 혹시 모델이 앞뒤에 설명을 덧붙였을 경우를 대비해 JSON 블록만 추출한다.
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end])
        result = {"is_new": bool(parsed.get("is_new")), "reason": str(parsed.get("reason", ""))}
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None

    _cache[cache_key] = (now, result)
    return result
