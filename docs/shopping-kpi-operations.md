# Shopping-Auto v2 KPI 운영 재산출 가이드

## 1) 원천데이터 경로

- `vector_store/dashboard/daily_stats.json`
- `vector_store/dashboard/transactions.json`

두 파일은 운영 스냅샷 입력 경로이며, KPI 재산출 시 동일 경로를 기본값으로 사용합니다.

## 2) 자격증명 주입 경로 (보안)

- 필수 env:
  - `NAVER_CLIENT_ID`
  - `NAVER_CLIENT_SECRET`
- 주입 권장 경로:
  - 런타임 시크릿 매니저(운영 환경)
  - 로컬 검증은 `.env` 사용 가능(단, `.env`는 커밋 금지)
- 저장소에는 `.env.example`에 키 이름만 선언하고 실제 값은 절대 기록하지 않습니다.

## 3) KPI 재산출 실행

```bash
python scripts/recompute_shopping_kpis.py
```

출력 파일:

- `vector_store/dashboard/kpi_snapshot.json`

## 4) KPI 정의

- `profit_rate_pct`: `(revenue - ad_cost - cogs) / revenue * 100`
- `category_mix`: `transactions.json`의 카테고리별 매출 비중(%)
- `conversion_rate_pct`: `orders / impressions * 100`
- `average_margin_rate_pct`: `(revenue - cogs) / revenue * 100`
- `average_margin_multiple_x`: `revenue / cogs`

## 5) 검증 절차

```bash
pytest -q tests/test_shopping_kpi.py
```

테스트는 KPI 4종 계산값과 카테고리 비중 합계(100%)를 검증합니다.
