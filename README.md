# NASDAQ Surge Scanner

UW(Unusual Whales)와 FMP(Financial Modeling Prep)의 **공식 REST API**만 사용해
나스닥(NASDAQ) 상장 주식 중 **거래량·변동률이 급등한 종목**을 찾아주는 독립형 FastAPI 서비스입니다.

이전 버전은 TradingView의 비공식 내부 엔드포인트를 사용했으나, 문서화되지 않은 API라
예고 없이 차단될 위험이 있어 공식 API 기반으로 교체했습니다.

## 데이터 흐름

1. **UW** `/api/screener/stocks` — 미국 보통주 전체 중 등락률·상대거래량·시가총액 조건에
   맞는 후보를 뽑습니다. (거래소 구분 없이 NASDAQ/NYSE/AMEX 섞여서 나옵니다)
2. **FMP** `/stable/profile` — 후보 종목 각각의 상장 거래소를 확인해서 NASDAQ인 것만 남깁니다.
3. 결과를 30초 TTL로 캐시해서 API 호출 횟수를 아낍니다.

## 필요한 API 키 2개

| 키 | 어디서 발급 | 환경변수 이름 |
|---|---|---|
| UW API 토큰 | https://unusualwhales.com/information/how-to-check-your-api-usage | `UW_API_TOKEN` |
| FMP API 키 | FMP 대시보드(기존 급등주 발굴 V1에서 쓰던 키 재사용 가능) | `FMP_API_KEY` |

## 로컬 실행

```bash
export UW_API_TOKEN=여기에_UW_토큰
export FMP_API_KEY=여기에_FMP_키
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- API: `GET http://localhost:8000/api/scan`
- 간단 대시보드: `GET http://localhost:8000/`
- API 문서(Swagger): `http://localhost:8000/docs`

## API 사용 예시

```
GET /api/scan?min_change_pct=10&min_rel_volume=2&limit=30
```

응답:

```json
{
  "uw_candidate_count": 47,
  "nasdaq_count": 12,
  "results": [
    {
      "ticker": "ABCD",
      "exchange": "NASDAQ",
      "change_pct": 18.5,
      "market_cap": 250000000,
      "relative_volume": 4.2,
      "volume": 5000000,
      "sector": "Technology",
      "raw": { "...UW 원본 필드 전체..." }
    }
  ]
}
```

`uw_candidate_count`는 UW 1차 스크리닝에 걸린 전체 종목 수(나스닥 외 포함), `nasdaq_count`는
그중 나스닥으로 확인된 개수입니다. `raw`에는 UW가 내려준 원본 필드가 그대로 들어있어서,
정규화 과정에서 빠진 값이 필요하면 여기서 확인할 수 있습니다.

### 쿼리 파라미터

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `min_price` | 1.0 | 최소 현재가 |
| `min_market_cap` | 50000000 | 최소 시가총액 |
| `min_rel_volume` | 1.5 | 최소 상대거래량 배수(30일 평균 대비) |
| `min_change_pct` | 5.0 | 최소 당일 등락률(%) |
| `max_change_pct` | 없음 | 최대 당일 등락률(%) |
| `limit` | 50 | 최종 반환할 나스닥 종목 최대 개수(최대 200) |

## Render 배포

리포지토리 루트의 `render.yaml`을 사용해 Blueprint로 배포합니다.

1. GitHub에 이 프로젝트를 업로드
2. Render 대시보드 → New → Blueprint → 리포지토리 선택
3. `render.yaml`이 자동 인식되어 `nasdaq-surge-scanner` 웹 서비스가 생성됨
4. **배포 전에 반드시** Render 서비스의 Environment 탭에서 `UW_API_TOKEN`, `FMP_API_KEY` 값을
   입력해야 합니다(`sync: false`로 설정되어 있어 Render가 값을 직접 물어봅니다).
5. 배포 완료 후 `https://<서비스명>.onrender.com/api/scan` 으로 접근

## 설계 메모 / 알려진 한계

- **UW 응답 필드명은 최초 실행 시 확인 필요**: 이 개발 환경은 `api.unusualwhales.com`으로
  나가는 네트워크가 막혀 있어 실제 응답을 직접 받아보지 못했습니다. 공식 문서 기준으로
  필드명을 추정해 작성했으니(`app/scanner.py`의 `_normalize_row`), 첫 배포 후 `/api/scan` 응답의
  `results[].raw`를 한 번 확인해서 `change_pct`/`relative_volume` 등이 제대로 채워지는지
  점검해주세요. 비어 있다면 `raw`에 실제로 들어있는 키 이름으로 `_normalize_row`의 후보
  키 목록을 조정하면 됩니다.
- **FMP 호출 횟수**: 나스닥 확인을 위해 후보 종목마다 FMP를 한 번씩 호출합니다. 후보가
  수십~200개 수준이면 무료/기본 플랜 요청 한도 내에서 충분하지만, 조건을 아주 느슨하게
  설정하면 호출이 늘어날 수 있습니다. 거래소 조회 결과는 6시간 캐시됩니다.
- **UW 응답 자체에 NASDAQ 필터가 없음**: `/api/screener/stocks`에는 거래소를 직접 지정하는
  파라미터가 없어서, 이 서비스처럼 "1차로 넉넉히 뽑고 2차로 FMP로 거르는" 2단계 구조가
  필요합니다.
