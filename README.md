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

## 필요한 API 키 3개 + 전용 DB 1개

| 항목 | 어디서 발급 | 환경변수 이름 |
|---|---|---|
| UW API 키 | https://unusualwhales.com/information/how-to-check-your-api-usage | `UW_API_KEY` |
| FMP API 키 | FMP 대시보드(기존 급등주 발굴 V1에서 쓰던 키 재사용 가능) | `FMP_API_KEY` |
| Anthropic API 키 | https://console.anthropic.com | `ANTHROPIC_API_KEY` |
| 전용 DB 연결 문자열 | Render의 `nasdaq-universe-db` 대시보드 → Internal Connection String | `UNIVERSE_DATABASE_URL` |

Anthropic API 키는 CANSLIM의 'N'(신규 촉매) 판정에만, DB는 미너비니의 RS Rating·CANSLIM의 L(업종 순위)에만 쓰입니다 — **둘 다 없어도 나머지 기능은 전부 정상 작동**하고, 해당 항목만 판정에서 빠집니다.

## 전용 DB(`nasdaq-universe-db`) 아키텍처

RS Rating(나스닥 전체 대비 상대강도)과 L(업종 내 순위)은 "이 종목이 시장/업종 전체에서 몇 등인가"를 계산해야 해서, 후보 종목만 조회하는 기존 구조로는 불가능합니다. 그래서 **급등주 발굴 V1이 쓰는 `nss-collector-db`와는 완전히 분리된 전용 DB**를 하나 더 써서, 나스닥 전체 종목의 일별 종가를 따로 쌓습니다.

```
[1회성 백필] scripts/backfill_history.py     [매일 자동] scripts/daily_update.py
  나스닥 전체 종목 목록 + 최근 1년 종가 적재      오늘자 전체 시세 1~2콜로 추가, 오래된 행 정리
              └──────────────┬──────────────────────────┘
                    daily_price 테이블 (심볼, 날짜, 종가)
                              │
                  SQL(PERCENT_RANK)로 RS 백분위/업종 순위 계산 — 외부 API 호출 없음
                              │
                    rs_rating_daily 테이블
                              │
              미너비니/CANSLIM 스캔 시 이 테이블만 조회
```

- **백필**: `python scripts/backfill_history.py --test`(최근 5거래일, 시험용) 또는 `--days 252`(본 실행)로 Render Shell에서 직접 실행합니다. **Render 무료 플랜은 Shell을 지원하지 않으므로**, 대신 아래 "무료 플랜에서 실행하는 방법"을 쓰세요.
- **일별 갱신**: `scripts/daily_update.py`를 Render Cron Job으로 등록해 매일 자동 실행합니다(Cron Job도 유료 플랜 필요). 무료 플랜에서는 외부 무료 크론 서비스로 대체합니다.
- 벌크 조회(`/stable/eod-bulk`, 날짜 1개당 전 종목 1콜)를 우선 시도하고, 플랜에서 지원하지 않으면 종목별 조회로 자동 전환합니다.
- **DB에 126거래일(약 6개월)치가 쌓이기 전까지는 RS Rating/L이 응답에 `null`로 표시되고, 그 조건 없이 기존 판정 기준으로만 동작합니다.** 데이터가 쌓일수록 자동으로 더 엄격해집니다.

### 무료 플랜에서 실행하는 방법 (Shell 없이)

`app/admin.py`가 브라우저 주소창만으로 실행할 수 있는 관리자 엔드포인트를 제공합니다. `ADMIN_TOKEN` 환경변수를 하나 정해서 등록한 뒤, 아래 주소를 브라우저에 입력하면 됩니다.

**백필 (요청 타임아웃을 피하기 위해 한 번에 50거래일 이하로, offset을 바꿔가며 여러 번 호출)**
```
https://<서비스주소>/admin/backfill?token=<ADMIN_TOKEN>&days=50&offset=0
https://<서비스주소>/admin/backfill?token=<ADMIN_TOKEN>&days=50&offset=50
https://<서비스주소>/admin/backfill?token=<ADMIN_TOKEN>&days=50&offset=100
... (offset을 50씩 늘려가며 총 252거래일을 채울 때까지 반복)
```

**일별 갱신 (매일 한 번 자동 호출 필요 — [cron-job.org](https://cron-job.org) 같은 무료 외부 크론 서비스에 아래 주소를 등록)**
```
https://<서비스주소>/admin/daily-update?token=<ADMIN_TOKEN>
```

두 엔드포인트 모두 실행 결과를 JSON으로 즉시 반환합니다(몇 개 종목을 저장했는지, RS 랭킹이 몇 개 갱신됐는지 등).

## 로컬 실행

```bash
export UW_API_KEY=여기에_UW_토큰
export FMP_API_KEY=여기에_FMP_키
export ANTHROPIC_API_KEY=여기에_Anthropic_키   # 없어도 실행되지만 CANSLIM의 N 판정만 빠짐
export UNIVERSE_DATABASE_URL=여기에_DB_연결문자열   # 없어도 실행되지만 RS Rating/L 판정만 빠짐
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
| `limit` | 50 | 1차 스캔에서 최종 반환할 나스닥 종목 최대 개수(최대 200) |
| `strategy` | surge | 스크리닝 방법론: `surge` / `volume_breakout` / `minervini` / `canslim` |

## 스크리닝 방법론 (strategy)

1차 스캔(가격·시총·등락률·상대거래량)으로 나스닥 급등 후보를 뽑은 뒤, `strategy` 값에 따라 추가 조건을 적용합니다.

- **surge** (기본값): 추가 조건 없음. 순수 등락률·거래량 급등주.
- **volume_breakout**: 52주 신고가 대비 10% 이내 + 거래량 실림. 추가 API 호출 없음(1차 스캔 데이터로 판정).
- **minervini**: 미너비니 추세 템플릿 8개 조건 중 **7개**를 적용 — 현재가 > 50일선 > 150일선 > 200일선 정배열, 52주 저점 대비 30%+ 상승, 52주 고점 대비 25% 이내, 200일선 1개월 이상 상승 추세, **RS Rating(나스닥 전체 대비 상대강도) 70 이상**(전용 DB 기반, 데이터 축적 전에는 생략). 상위 15개 후보에 대해서만 UW 이동평균(SMA)을 조회합니다(종목당 4회 호출 + RS Rating DB 조회 1회).
- **canslim**: CANSLIM 7개 요소(C·A·N·S·L·I·M) **전부** 적용:
  - **C·A**: 분기·연간 EPS 전년 대비 25%+ 성장 (FMP 재무제표)
  - **N**: 최근 뉴스에 신제품·신경영진 등 새로운 촉매가 있는지 **Claude(Anthropic API)가 헤드라인을 읽고 판정**
  - **S**: 상대거래량 1.5배 이상 (1차 스캔 데이터)
  - **L**: **업종 내 상대강도 순위 70퍼센타일 이상**(전용 DB 기반, 데이터 축적 전에는 생략)
  - **I**: 기관 보유 비중이 직전 분기 대비 증가 (FMP 13F 데이터)
  - **M**: 나스닥 대표 ETF(QQQ)가 50일선 위 — 시장 전체가 상승 국면일 때만 통과 (스캔당 1회만 계산, 전 종목 공통 적용)
  - 상위 8개 후보에만 적용(종목당 FMP 3회 + 뉴스 1회 + Claude 1회 + RS DB 조회 1회 — 다른 방법론보다 훨씬 느립니다).

⚠️ RS Rating과 L은 전용 DB(`nasdaq-universe-db`)에 최소 126거래일(약 6개월)치 데이터가 쌓여야 정상적으로 계산됩니다. 그 전까지는 이 두 조건이 `null`로 표시되며, 판정에서 자동으로 제외됩니다(나머지 조건만으로 통과 여부가 결정됨). 'N' 판정은 Claude가 최근 뉴스 헤드라인 몇 개만 보고 내리는 판단이라 오판이 있을 수 있습니다.

예시:
```
GET /api/scan?strategy=minervini&limit=30
GET /api/scan?strategy=canslim&min_change_pct=3
GET /api/scan?strategy=volume_breakout
```

대시보드(`/`)에서도 상단 방법론 링크를 클릭해 전환할 수 있습니다.

## Render 배포

리포지토리 루트의 `render.yaml`을 사용해 Blueprint로 배포합니다.

1. GitHub에 이 프로젝트를 업로드
2. Render 대시보드 → New → Blueprint → 리포지토리 선택
3. `render.yaml`이 자동 인식되어 `nasdaq-surge-scanner` 웹 서비스가 생성됨
4. **배포 전에 반드시** Render 서비스의 Environment 탭에서 `UW_API_KEY`, `FMP_API_KEY` 값을
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
