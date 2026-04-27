# 채택 메트릭 데이터 파이프라인 가이드

## 개요

채택 메트릭 데이터 파이프라인은 의사결정 프레임워크의 채택 상황, 의사결정 품질, 팀 참여도를 추적하고 집계하는 시스템입니다.

## 주요 KPI

### 1. Framework Usage Rate (프레임워크 사용률)
- **정의**: 총 결정 횟수 대비 프레임워크를 사용한 결정의 비율
- **계산식**: (Framework decisions / Total decisions) × 100
- **데이터 원천**: 시스템 로그
- **계산 주기**: 일일, 주간, 월간

### 2. Decision Quality Score (의사결정 품질 점수)
- **정의**: 의사결정의 품질을 평가하는 점수 (1-5점 척도)
- **계산식**: 설문조사 응답의 평균 점수
- **데이터 원천**: 월간 설문조사
- **계산 주기**: 주간, 월간

### 3. Engagement Index (참여도 지수)
- **정의**: 팀의 프레임워크 참여도를 종합적으로 나타내는 지수 (0-100)
- **계산식**: 
  - Usage component (50%): 프레임워크 사용률
  - Users component (30%): 활성 사용자 비율
  - Survey component (20%): 설문조사 응답률
- **데이터 원천**: 시스템 로그 + 설문조사
- **계산 주기**: 주간, 월간

## API 엔드포인트

### 설문조사 제출
```bash
POST /adoption/surveys
Content-Type: application/json

{
  "respondent_id": "user123",
  "survey_type": "adoption",
  "score": 4,
  "feedback": "프레임워크가 의사결정을 단순화하는 데 도움이 되었습니다",
  "survey_date": "2026-04-25"
}
```

**응답:**
```json
{
  "status": "success",
  "message": "Survey response recorded",
  "respondent_id": "user123"
}
```

### 시스템 액션 로깅
```bash
POST /adoption/log
Content-Type: application/json

{
  "user_id": "user123",
  "action_type": "framework_decision",
  "decision_id": "dec456",
  "metadata": {
    "category": "product",
    "outcome": "approved"
  }
}
```

**응답:**
```json
{
  "status": "success",
  "message": "Action logged",
  "user_id": "user123"
}
```

### 일일 메트릭 조회
```bash
GET /adoption/metrics/daily?limit=30
```

**응답:**
```json
{
  "period": "daily",
  "limit": 30,
  "count": 3,
  "data": [
    {
      "metric_date": "2026-04-25",
      "framework_usage_rate": 83.33,
      "active_users": 4,
      "total_decisions": 6,
      "framework_decisions": 5,
      "calculated_at": "2026-04-25T16:53:34.864532+00:00"
    }
  ]
}
```

### 주간 메트릭 조회
```bash
GET /adoption/metrics/weekly?limit=12
```

**응답:**
```json
{
  "period": "weekly",
  "limit": 12,
  "count": 1,
  "data": [
    {
      "week_start": "2026-04-20",
      "framework_usage_rate": 83.33,
      "decision_quality_score": 4.0,
      "engagement_index": 69.67,
      "active_users": 4,
      "survey_responses_count": 3,
      "calculated_at": "2026-04-25T16:53:34.864532+00:00"
    }
  ]
}
```

### 월간 메트릭 조회
```bash
GET /adoption/metrics/monthly?limit=12
```

### 메트릭 수동 계산
```bash
# 특정 날짜의 일일 메트릭 계산
POST /adoption/metrics/calculate/daily?date=2026-04-24

# 특정 주의 주간 메트릭 계산
POST /adoption/metrics/calculate/weekly?week_start=2026-04-20

# 특정 달의 월간 메트릭 계산
POST /adoption/metrics/calculate/monthly?month_start=2026-04-01
```

## 데이터베이스 스키마

### survey_responses 테이블
응답자의 설문조사 응답을 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID (자동 증가) |
| respondent_id | TEXT | 응답자 ID |
| survey_type | TEXT | 설문 유형 (adoption, quality, engagement) |
| survey_date | TEXT | 설문 날짜 (YYYY-MM-DD) |
| score | INTEGER | 점수 (1-5) |
| feedback | TEXT | 추가 피드백 |
| created_at | TEXT | 생성 일시 (ISO 8601) |

### system_logs 테이블
시스템에서 기록되는 모든 액션을 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID (자동 증가) |
| user_id | TEXT | 사용자 ID |
| action_type | TEXT | 액션 유형 (framework_decision, manual_decision 등) |
| decision_id | TEXT | 의사결정 ID (선택) |
| metadata | TEXT | JSON 메타데이터 |
| logged_at | TEXT | 로그 기록 시간 (ISO 8601) |

### adoption_metrics_daily 테이블
일일 채택 메트릭을 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID |
| metric_date | TEXT | 메트릭 날짜 (YYYY-MM-DD, UNIQUE) |
| framework_usage_rate | REAL | 프레임워크 사용률 (%) |
| active_users | INTEGER | 활성 사용자 수 |
| total_decisions | INTEGER | 총 의사결정 수 |
| framework_decisions | INTEGER | 프레임워크 사용 의사결정 수 |
| calculated_at | TEXT | 계산 시각 (ISO 8601) |

### adoption_metrics_weekly 테이블
주간 채택 메트릭을 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID |
| week_start | TEXT | 주간 시작 날짜 (YYYY-MM-DD, UNIQUE) |
| framework_usage_rate | REAL | 프레임워크 사용률 (%) |
| decision_quality_score | REAL | 의사결정 품질 점수 (1-5) |
| engagement_index | REAL | 참여도 지수 (0-100) |
| active_users | INTEGER | 활성 사용자 수 |
| survey_responses_count | INTEGER | 설문조사 응답 수 |
| calculated_at | TEXT | 계산 시각 (ISO 8601) |

### adoption_metrics_monthly 테이블
월간 채택 메트릭을 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID |
| month_start | TEXT | 월간 시작 날짜 (YYYY-MM-DD, UNIQUE) |
| framework_usage_rate | REAL | 프레임워크 사용률 (%) |
| decision_quality_score | REAL | 의사결정 품질 점수 (1-5) |
| engagement_index | REAL | 참여도 지수 (0-100) |
| active_users | INTEGER | 활성 사용자 수 |
| survey_responses_count | INTEGER | 설문조사 응답 수 |
| calculated_at | TEXT | 계산 시각 (ISO 8601) |

## 자동화된 롤업 스케줄

채택 메트릭은 다음 일정에 따라 자동으로 계산됩니다:

### 일일 롤업
- **실행 시간**: 매일 자정 (UTC)
- **대상**: 전날의 메트릭
- **계산 항목**: 프레임워크 사용률, 활성 사용자 수

### 주간 롤업
- **실행 시간**: 매주 월요일 자정 (UTC)
- **대상**: 지난 주의 데이터
- **계산 항목**: 프레임워크 사용률, 의사결정 품질 점수, 참여도 지수

### 월간 롤업
- **실행 시간**: 매월 1일 자정 (UTC)
- **대상**: 지난 달의 데이터
- **계산 항목**: 프레임워크 사용률, 의사결정 품질 점수, 참여도 지수

## 구현 모듈

### app/adoption_metrics.py
핵심 메트릭 계산 로직을 포함합니다:
- `log_system_action()`: 시스템 액션 로깅
- `record_survey_response()`: 설문조사 응답 기록
- `calculate_daily_metrics()`: 일일 메트릭 계산
- `calculate_weekly_metrics()`: 주간 메트릭 계산
- `calculate_monthly_metrics()`: 월간 메트릭 계산
- `get_daily_metrics()`: 일일 메트릭 조회
- `get_weekly_metrics()`: 주간 메트릭 조회
- `get_monthly_metrics()`: 월간 메트릭 조회

### app/adoption_scheduler.py
자동화된 메트릭 롤업 스케줄러:
- `AdoptionMetricsScheduler`: 일정한 시간에 메트릭을 자동 계산하는 스케줄러

### app/main.py
FastAPI 애플리케이션과의 통합:
- `POST /adoption/surveys`: 설문조사 제출
- `POST /adoption/log`: 시스템 액션 로깅
- `GET /adoption/metrics/daily`: 일일 메트릭 조회
- `GET /adoption/metrics/weekly`: 주간 메트릭 조회
- `GET /adoption/metrics/monthly`: 월간 메트릭 조회
- `POST /adoption/metrics/calculate/daily`: 일일 메트릭 수동 계산
- `POST /adoption/metrics/calculate/weekly`: 주간 메트릭 수동 계산
- `POST /adoption/metrics/calculate/monthly`: 월간 메트릭 수동 계산

## 사용 예시

### Python에서 사용
```python
from app import adoption_metrics

# 시스템 액션 로깅
adoption_metrics.log_system_action(
    user_id="user123",
    action_type="framework_decision",
    decision_id="dec456",
    metadata={"category": "product"}
)

# 설문조사 응답 기록
adoption_metrics.record_survey_response(
    respondent_id="user123",
    survey_type="adoption",
    score=5,
    feedback="Great framework!"
)

# 메트릭 계산
daily = adoption_metrics.calculate_daily_metrics("2026-04-25")
weekly = adoption_metrics.calculate_weekly_metrics("2026-04-20")
monthly = adoption_metrics.calculate_monthly_metrics("2026-04-01")

# 메트릭 조회
metrics = adoption_metrics.get_weekly_metrics(limit=12)
```

### cURL을 사용한 API 호출
```bash
# 설문조사 제출
curl -X POST http://localhost:8509/adoption/surveys \
  -H "Content-Type: application/json" \
  -d '{
    "respondent_id": "user123",
    "survey_type": "adoption",
    "score": 4,
    "feedback": "좋은 프레임워크"
  }'

# 시스템 액션 로깅
curl -X POST http://localhost:8509/adoption/log \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user123",
    "action_type": "framework_decision",
    "decision_id": "dec456"
  }'

# 주간 메트릭 조회
curl http://localhost:8509/adoption/metrics/weekly?limit=12
```

## 성능 고려사항

1. **인덱스**: 시스템 로그 및 설문조사 응답 테이블에 인덱스가 생성되어 있어 빠른 조회가 가능합니다.
2. **집계**: 메트릭 계산은 미리 집계된 `adoption_metrics_*` 테이블에 저장되어 있어 대시보드 조회가 빠릅니다.
3. **스케일링**: PostgreSQL로 마이그레이션하면 더 큰 데이터셋을 효율적으로 처리할 수 있습니다.

## Phase 4: 피드백 아카이브 및 보고 (Feedback Archival & Reporting)

### 개요
Phase 4는 오래된 설문조사 응답과 시스템 로그를 아카이브하고 월간 보고서를 자동 생성합니다.

### 아카이브 기능
- **자동 아카이브**: 매월 15일 자정(UTC)에 90일 이상 된 데이터를 아카이브합니다.
- **아카이브 테이블**: 
  - `survey_responses_archive`: 아카이브된 설문조사 응답
  - `system_logs_archive`: 아카이브된 시스템 로그
- **데이터 보존**: 아카이브된 데이터는 별도 테이블에 저장되어 쿼리 성능 유지

### 보고 기능
- **자동 월간 보고**: 매월 15일에 지난 달의 보고서 자동 생성
- **보고서 내용**:
  - 프레임워크 사용률
  - 의사결정 품질 점수
  - 참여도 지수
  - 활성 사용자 수
  - 설문조사 응답 수
  - 액션 유형별 분석

### API 엔드포인트

#### 피드백 아카이브
```bash
POST /adoption/archival?days_to_keep=90
```

**응답:**
```json
{
  "status": "success",
  "message": "Feedback archival completed",
  "archived_surveys": 150,
  "archived_logs": 2500,
  "cutoff_date": "2026-01-27T00:00:00+00:00",
  "completed_at": "2026-04-27T12:34:56+00:00"
}
```

#### 월간 보고서 생성
```bash
POST /adoption/reports/generate?month_start=2026-04-01
```

**응답:**
```json
{
  "status": "success",
  "message": "Monthly report generated",
  "report": {
    "month": "2026-04-01",
    "framework_usage_rate": 83.33,
    "decision_quality_score": 4.2,
    "engagement_index": 72.5,
    "active_users": 15,
    "survey_responses_count": 12,
    "average_survey_score": 4.2,
    "action_breakdown": [
      {"action_type": "framework_decision", "count": 50},
      {"action_type": "manual_decision", "count": 10}
    ]
  }
}
```

#### 보고서 조회
```bash
GET /adoption/reports?report_type=monthly&limit=12
```

**응답:**
```json
{
  "report_type": "monthly",
  "limit": 12,
  "count": 3,
  "data": [
    {
      "id": 1,
      "report_type": "monthly",
      "period_start": "2026-04-01",
      "period_end": "2026-05-01",
      "summary": {...},
      "created_at": "2026-04-27T00:05:00+00:00"
    }
  ]
}
```

### 데이터베이스 스키마 (Phase 4)

#### survey_responses_archive 테이블
아카이브된 설문조사 응답을 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID |
| respondent_id | TEXT | 응답자 ID |
| survey_type | TEXT | 설문 유형 |
| survey_date | TEXT | 설문 날짜 |
| score | INTEGER | 점수 |
| feedback | TEXT | 피드백 |
| created_at | TEXT | 생성 일시 |
| archived_at | TEXT | 아카이브 일시 |

#### system_logs_archive 테이블
아카이브된 시스템 로그를 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID |
| user_id | TEXT | 사용자 ID |
| action_type | TEXT | 액션 유형 |
| decision_id | TEXT | 의사결정 ID |
| metadata | TEXT | 메타데이터 |
| logged_at | TEXT | 로그 기록 시간 |
| archived_at | TEXT | 아카이브 시간 |

#### archival_reports 테이블
생성된 보고서를 저장합니다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER | 고유 ID |
| report_type | TEXT | 보고서 유형 |
| period_start | TEXT | 기간 시작 |
| period_end | TEXT | 기간 종료 |
| summary | TEXT | JSON 요약 |
| created_at | TEXT | 생성 시각 |

### 구현 모듈 (Phase 4)

#### app/adoption_metrics.py 추가 함수
- `archive_old_feedback()`: 오래된 데이터 아카이브
- `generate_monthly_report()`: 월간 보고서 생성
- `get_archival_reports()`: 아카이브된 보고서 조회

#### app/adoption_scheduler.py 추가
- `_archival_loop()`: 매월 15일 자정에 아카이브 실행

## 모니터링 및 유지보수

1. **스케줄러 로그**: 애플리케이션 로그에서 메트릭 계산 결과를 확인할 수 있습니다.
2. **아카이브 모니터링**: 아카이브 프로세스의 성공 여부를 로그에서 확인합니다.
3. **보고서 검증**: 월간 보고서의 데이터 일관성을 정기적으로 검증해야 합니다.
4. **데이터 검증**: 정기적으로 메트릭 데이터의 일관성을 검증해야 합니다.
5. **백업**: 중요한 메트릭 데이터는 정기적으로 백업해야 합니다.
