# 미국 주식 근거 데이터

`stocks` collector는 현재가, 완료 일봉(`1d`), 정확히 60분 길이의 완료봉(`60m`),
OHLCV, 연간 재무 지표, 밸류에이션과 조건부 가격 계획을 저장한다.
시세는 Yahoo Finance의 정규장 데이터이며 실시간 거래소 호가를 보장하지 않는다.
미국 거래일·DST·조기 폐장을 XNYS 달력으로 확인하고 종료 후 5분을 기다린다.
장 마감 직전 30분 봉은 `60m`에 넣지 않는다. 과거 1분봉의 재개/복원은 별개다.

## 수집·조회

- `python scripts/collect_jobs.py us-stocks --symbols AAPL,MSFT --batch-size 50`
- 기존 `us-stocks-1m` 명령은 호환 별칭이다. 1분봉 수집을 의미하지 않는다.
- REST: `POST /collect/sync`에 `{"collector":"stocks","symbols":["AAPL"]}`.
- REST: `GET /stocks/evidence?symbols=AAPL,MSFT`.
- MCP: `collect_us_stocks`, `query_us_stock_evidence`.
- 기존 `query_market_data(collector="stocks")`에도 전체 근거가 포함된다.
- 완료봉은 `query_market_candles(collector="stocks", symbol="AAPL", interval="60m")`.

목록 우선순위는 명시적 요청 → 활성 stocks 수집 정책 → `US_STOCK_SYMBOLS`
→ 기존 기본 5종목(AAPL, MSFT, GOOGL, AMZN, TSLA)이다. 자동 종목 추천은 하지 않는다.
기본 서버 수집 간격은 15분이며 `SCHED_STOCKS_INTERVAL`로 설정할 수 있다.
재무 조회는 동일 심볼의 검증된 지표를 24시간 캐시한다.

## 지표와 판정

FCF = 영업현금흐름 − 절대값(CapEx).
순부채 = 총부채 − 현금·단기투자자산.
ROIC = 영업이익 × (1 − 유효세율) ÷ 기초·기말 평균 투하자본.
유효세율은 동일 연도 법인세/세전이익을 우선 사용한다. 법인세 항목이 없으면
제공사의 같은 연도 `TaxRateForCalcs`를 사용하고 `tax_rate_source`로 구분한다.
세전이익이 양수이고 세율이 0~1인 경우만 계산하며, 세액을 임의 복원하지 않는다.
투하자본은 부채 + 자기자본 − 현금·단기투자자산으로 정의한다.
결산일이 일치하는 연간 재무제표만 사용하고 TTM과 혼합하지 않는다.
원자료 입력, 산식, 결산일, 조회 시각, 출처를 보존한다. 과거 시점 백테스트용 공시
가용 시각은 보장하지 않는다. 비USD 재무제표·금융사/ETF 등의 미지원 지표는 미확인으로 남긴다.

밸류에이션의 시가총액은 최신 가격 × 제공사 주식수의 추정치이고, EV에는 해당
결산일 순부채를 사용한다. P/FCF와 EV/영업이익 등은 비양수 분모에서 만들지 않는다.

가격 계획은 최근 완료 일봉 20개의 지지·저항을 사용하는 **검토용 가설**이다.
목표가는 관측 고가 중 3번째 높은 값, 손절가는 관측 최저가에서 최근 14일 평균
고저폭의 25%를 차감한다. 비용 후 손익비 3:1이 가능한 진입 상한을 계산하며,
현재가보다 낮으면 `waiting_for_pullback`이다. 목표가를 억지로 올리지 않는다.

비용은 진입/청산 각각 수수료·세금·스프레드·슬리피지·해당 환전비를 포함한다.
`US_STOCK_ENTRY_COST_BPS`, `US_STOCK_EXIT_COST_BPS`, `US_STOCK_COST_SOURCE`,
`US_STOCK_COST_CONFIRMED=1`로 실비 근거를 설정한다. 미설정 시 편도 20bp의
**미검증 예시 가정**이다. `cost_verified_3r`는 신선한 데이터와 확인된 비용이
있어야 참이다. 이 서비스는 주문·전략 승격 권한을 부여하지 않는다.

성공 스냅샷·봉·job 완료 상태는 원자적으로 공개한다. 일부 종목이 실패하면
성공 종목은 독립 완료 job으로 남기고 부모 job은 실패한다. 조회 시 신선도를
재검사하므로 과거 성공 데이터가 영구히 `ready`로 남지 않는다.
