# Paperclip 드리프트 모니터링 문제 해결

Paperclip 드리프트 모니터링 시스템 운영 중 발생하는 일반적인 문제와 해결 방법을 정리했습니다.

## 목차

- [설정 관련 문제](#설정-관련-문제)
- [API 연결 문제](#api-연결-문제)
- [인증 문제](#인증-문제)
- [모니터링 동작 문제](#모니터링-동작-문제)
- [복구 실패 문제](#복구-실패-문제)
- [성능 및 로그 문제](#성능-및-로그-문제)
- [알람 문제](#알람-문제)
- [진단 및 정보 수집](#진단-및-정보-수집)

## 설정 관련 문제

### 문제: 필수 환경 변수 누락

**증상**:
```
Missing required configuration: PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_COMPANY_ID
```

**원인**: 필수 환경 변수가 설정되지 않음

**해결책**:

다음 환경 변수를 반드시 설정해야 합니다:

```bash
# 1. 환경 변수 확인
echo $PAPERCLIP_API_URL
echo $PAPERCLIP_API_KEY
echo $PAPERCLIP_COMPANY_ID

# 2. 누락된 변수 설정
export PAPERCLIP_API_URL="http://localhost:8000"  # Paperclip API 서버 주소
export PAPERCLIP_API_KEY="your-api-key"           # API 인증 토큰
export PAPERCLIP_COMPANY_ID="company-uuid"        # 회사 ID

# 3. 환경 파일에 저장 (권장)
# .env 또는 .env.local 파일 생성
echo "PAPERCLIP_API_URL=http://localhost:8000" >> .env
echo "PAPERCLIP_API_KEY=your-api-key" >> .env
echo "PAPERCLIP_COMPANY_ID=company-uuid" >> .env

# 4. 환경 파일 로드 및 실행
source .env
python scripts/monitor-drift.py
```

---

### 문제: API_URL 포맷 오류

**증상**:
```
Invalid API URL format
또는
Cannot connect to API
```

**원인**: API_URL이 잘못된 포맷

**해결책**:

```bash
# 올바른 포맷 확인
# ✓ http://localhost:8000
# ✓ https://api.example.com
# ✓ http://192.168.1.1:8000
# ✗ localhost:8000          (http:// 누락)
# ✗ http://localhost:8000/  (마지막 슬래시)

# 정확한 포맷으로 설정
export PAPERCLIP_API_URL="http://api.paperclip.local:8000"

# 연결 테스트
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/health"
```

---

## API 연결 문제

### 문제: API 서버에 연결할 수 없음

**증상**:
```
httpx.ConnectError: [Errno -2] Name or service not known
또는
Connection refused
또는
Connection timeout
```

**원인**: 
1. API 서버가 실행 중이지 않음
2. 잘못된 API_URL
3. 네트워크 연결 문제

**해결책**:

```bash
# 1. API 서버 상태 확인
curl -v http://localhost:8000/health

# 2. 포트 사용 확인
lsof -i :8000              # macOS/Linux
netstat -ano | findstr :8000  # Windows

# 3. 호스트명 해석 확인
ping api.paperclip.local
nslookup api.paperclip.local

# 4. 네트워크 경로 확인
traceroute api.paperclip.local
mtr api.paperclip.local

# 5. API 서버 로그 확인
# Docker 사용 시
docker-compose logs paperclip-api

# 6. 방화벽 규칙 확인
sudo ufw status  # Ubuntu/Linux
```

---

### 문제: 타임아웃 오류

**증상**:
```
httpx.TimeoutException: ReadTimeout(30.0s)
또는
Connection timed out
```

**원인**: 
1. API 서버 응답 시간이 30초 초과
2. 네트워크 대역폭 문제
3. 대량의 이슈 처리로 인한 지연

**해결책**:

```bash
# 1. 네트워크 지연 확인
ping -c 10 api.paperclip.local
# latency가 매우 높으면 네트워크 문제

# 2. API 서버 성능 확인
# 서버 로그에서 느린 쿼리 확인
tail -f app.log | grep "duration"

# 3. 데이터베이스 성능 확인
# 블로커 정보 조회가 느린 경우가 많음
psql -U user -d paperclip -c "SELECT COUNT(*) FROM issues WHERE status='in_progress';"

# 4. 한 번에 처리하는 이슈 수 줄이기
# 모니터 코드에서 페이지네이션 설정
# 또는 모니터링 주기 조정

# 5. API 서버 리소스 확인
# CPU, 메모리, 디스크 사용률 확인
docker stats paperclip-api
```

---

## 인증 문제

### 문제: 인증 토큰 오류

**증상**:
```
Unauthorized (401)
또는
Invalid bearer token
또는
Authentication failed
```

**원인**: 
1. API_KEY가 잘못됨
2. API_KEY가 만료됨
3. 토큰 포맷 오류

**해결책**:

```bash
# 1. API_KEY 확인
echo $PAPERCLIP_API_KEY
# 비어있으면 설정하기
export PAPERCLIP_API_KEY="your-correct-api-key"

# 2. API_KEY 유효성 확인
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/user"
# 200 응답이 오면 유효함

# 3. API_KEY 갱신
# Paperclip 관리자 페이지에서 새 토큰 발급
# https://admin.paperclip.local/settings/api-keys

# 4. 토큰 포맷 확인
# ✓ Bearer <token>
# ✓ Authorization header에만 포함
# ✗ URL 파라미터에 포함
# ✗ 토큰 앞에 "Bearer" 누락
```

---

### 문제: 권한 부족

**증상**:
```
Forbidden (403)
또는
Insufficient permissions to perform this action
```

**원인**: 사용된 API 토큰에 필요한 권한이 없음

**해결책**:

```bash
# 1. 현재 토큰의 권한 확인
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/user/permissions"

# 2. 필요한 권한 확인
# 드리프트 모니터는 다음 권한 필요:
# - issues:read (이슈 조회)
# - issues:update (이슈 상태 변경)
# - issues:comment (코멘트 추가)

# 3. 관리자에게 권한 요청
# Paperclip 관리자가 다음을 설정:
# - API 토큰에 위의 권한 추가
# - 회사(company) 범위 설정

# 4. 새 토큰으로 교체
export PAPERCLIP_API_KEY="new-api-key-with-permissions"
```

---

## 모니터링 동작 문제

### 문제: 드리프트가 감지되지 않음

**증상**:
```
"issues_with_drift": 0
모니터링 실행 후에도 아무 이슈도 복구되지 않음
```

**원인**: 
1. 드리프트 상태의 이슈가 없음
2. 드리프트 감지 로직 오류
3. 필터링 조건 오류

**해결책**:

```bash
# 1. 현재 in_progress 이슈 확인
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/companies/$PAPERCLIP_COMPANY_ID/issues?status=in_progress"

# 2. 각 이슈의 블로커 상태 확인
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/issues/ISSUE-ID"
# "blockedBy" 필드 확인

# 3. 수동으로 테스트 케이스 생성
# - in_progress 상태의 이슈
# - 해결되지 않은 blocker가 있는 이슈
# 이 두 조건을 모두 만족해야 드리프트로 감지됨

# 4. 모니터링 로그 레벨 상향
# LOGGER_LEVEL=DEBUG로 설정하여 상세 로그 확인
LOGGER_LEVEL=DEBUG python scripts/monitor-drift.py

# 5. 드리프트 감지 조건 확인
# app/paperclip_drift_monitor.py의 detect_drift() 메서드 검증
```

---

### 문제: 잘못된 이슈가 복구됨

**증상**:
```
in_progress 상태의 정상적인 이슈가 blocked로 복구되는 현상
```

**원인**: 드리프트 감지 조건이 너무 광범위

**해결책**:

```bash
# 1. 복구된 이슈 목록 확인
# 모니터링 로그에서 "Successfully restored" 검색
tail -f app.log | grep "Successfully restored"

# 2. 복구된 이슈의 blocker 상태 확인
# 실제로 미해결 blocker가 있었는지 확인
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/issues/ISSUE-ID"

# 3. blocker 상태 업데이트 확인
# 블로커가 실제로 완료 상태인지 확인
# status가 "done"이면 드리프트로 감지되면 안 됨

# 4. 감지 기준 조정 (필요시)
# app/paperclip_drift_monitor.py의 check_blocker_resolved() 수정
# 추가 조건 (예: 완료 이후 경과 시간) 추가 검토
```

---

## 복구 실패 문제

### 문제: 이슈 상태 변경 실패

**증상**:
```
Failed to restore {issue} to blocked
또는
API call returned error while updating issue
```

**원인**: 
1. 이슈를 잠금 상태(locked)
2. 워크플로우 규칙 충돌
3. 동시 수정으로 인한 충돌

**해결책**:

```bash
# 1. 이슈 상태 확인
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/issues/ISSUE-ID"
# "locked", "archivedAt" 필드 확인

# 2. 이슈 잠금 해제
curl -X PATCH \
  -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"locked": false}' \
  "http://localhost:8000/api/issues/ISSUE-ID"

# 3. 워크플로우 규칙 확인
# Paperclip 관리 페이지에서:
# Settings → Workflow Rules → 상태 전이 조건 검증
# blocked ← in_progress 전이가 허용되는지 확인

# 4. 모니터링 실행 시간대 변경
# 다른 사용자의 작업과 겹치지 않도록 조정
# 예: 야간 시간에 실행하도록 스케줄 조정

# 5. 충돌 처리 로직 추가
# 모니터 코드에서 재시도 로직 또는 로깅 강화
```

---

## 성능 및 로그 문제

### 문제: 모니터링 실행 시간이 너무 오래 걸림

**증상**:
```
모니터링이 5분 이상 소요됨
또는 다음 실행 주기 전에 완료되지 않음
```

**원인**: 
1. 이슈 수가 매우 많음
2. 각 이슈의 블로커 조회가 느림
3. API 응답 시간 지연

**해결책**:

```bash
# 1. 성능 프로파일링
# 모니터 코드에 timing 로그 추가
# 각 단계의 소요 시간 측정

# 2. API 응답 시간 확인
# X-Response-Time 헤더 확인
curl -v -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "http://localhost:8000/api/companies/$PAPERCLIP_COMPANY_ID/issues?status=in_progress" \
  2>&1 | grep "X-Response-Time"

# 3. 데이터베이스 쿼리 최적화
# PostgreSQL 쿼리 계획 확인
psql -U user -d paperclip -c "EXPLAIN ANALYZE SELECT * FROM issues WHERE status='in_progress';"

# 4. 배치 처리 도입
# 이슈를 작은 배치로 나누어 처리
# 예: 한 번에 10개씩 처리, 배치 간 딜레이 추가

# 5. 필터 조건 추가
# 모니터링 대상을 제한
# 예: 특정 프로젝트만, 특정 우선순위만

# 6. 모니터링 주기 조정
# 예: 5분마다 → 10분마다 실행
```

---

### 문제: 로그가 출력되지 않음 또는 불완전함

**증상**:
```
프로세스는 실행되지만 로그가 보이지 않음
또는 부분적인 로그만 출력됨
```

**원인**: 
1. 로그 레벨 설정 오류
2. 로그 출력 리다이렉션 문제
3. 백그라운드 프로세스로 실행

**해결책**:

```bash
# 1. 로그 레벨 확인 및 변경
# 기본값: INFO
# 디버깅: DEBUG
export LOGGER_LEVEL=DEBUG
python scripts/monitor-drift.py

# 2. 로그 파일로 저장
# 로그 출력을 파일로 캡처
python scripts/monitor-drift.py > drift-monitor.log 2>&1

# 3. 실시간 로그 모니터링
# 백그라운드에서 실행 중일 때
tail -f drift-monitor.log

# 4. 구조화된 로그 출력
# 모니터 코드에서 JSON 로그 활성화
# 로그 parsing 및 분석 용이

# 5. 로그 보관
# 스토리지 부족으로 로그 삭제되는 것 방지
# logrotate 설정
echo "/var/log/paperclip-drift/*.log {
    daily
    rotate 7
    compress
}" > /etc/logrotate.d/paperclip-drift
```

---

## 알람 문제

### 문제: 알람이 발생하지 않음

**증상**:
```
반복 드리프트가 발생해도 알람이 트리거되지 않음
또는 ALARM 로그가 출력되지 않음
```

**원인**: 
1. 알람 임계값 설정 오류
2. 드리프트 이벤트 카운팅 오류
3. 시간 범위 설정 오류

**해결책**:

```bash
# 1. 알람 임계값 확인
# 기본값: 1시간 내 2회 이상 드리프트
# app/paperclip_drift_monitor.py에서 확인:
# if drift_count + 1 >= 2:

# 2. 드리프트 이벤트 로그 확인
# 이벤트가 제대로 기록되는지 확인
# 로그에서 "drift_count" 검색

# 3. 시간 범위 검증
# get_drift_count_1h() 메서드 확인
# 현재 시간 기준 정확히 1시간 이내만 카운트되는지 확인

# 4. 모니터링 수동 테스트
# in_progress 이슈 생성
# blocker 추가
# 같은 이슈를 수동으로 여러 번 드리프트 발생
# 알람 트리거 확인

# 5. 알람 채널 설정 확인
# 알람이 로깅되기만 하는지, 아니면 외부 알림도 전송되는지 확인
# 필요시 Slack, PagerDuty 등 통합 설정
```

---

### 문제: 원치 않은 알람 발생

**증상**:
```
정상적인 드리프트에도 알람이 과도하게 발생
```

**원인**: 알람 임계값이 너무 낮음

**해결책**:

```bash
# 1. 알람 임계값 상향 조정
# app/paperclip_drift_monitor.py의 다음 라인 수정:
# if drift_count + 1 >= 2:  # 2 → 3 또는 그 이상으로 변경

# 2. 시간 범위 확장
# get_drift_count_1h() 메서드 수정:
# 1시간 → 3시간 또는 더 길게 조정

# 3. 드리프트 근본 원인 파악 및 해결
# 같은 이슈가 반복적으로 드리프트하는 이유 조사
# 워크플로우 규칙 또는 사용자 행동 패턴 분석
```

---

## 진단 및 정보 수집

### 환경 정보 출력

문제 보고 시 다음 정보를 포함하세요:

```bash
# 1. 환경 변수 확인 (민감 정보 마스킹)
echo "PAPERCLIP_API_URL: $PAPERCLIP_API_URL"
echo "PAPERCLIP_API_KEY: ${PAPERCLIP_API_KEY:0:10}..."
echo "PAPERCLIP_COMPANY_ID: $PAPERCLIP_COMPANY_ID"
echo "PAPERCLIP_RUN_ID: $PAPERCLIP_RUN_ID"

# 2. Python 환경 정보
python --version
which python
pip list | grep -E "(paperclip|httpx|pytest)"

# 3. API 연결 진단
curl -v -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/health"

# 4. 현재 이슈 상태
curl -H "Authorization: Bearer $PAPERCLIP_API_KEY" \
  "$PAPERCLIP_API_URL/api/companies/$PAPERCLIP_COMPANY_ID/issues?status=in_progress" | jq . | head -50

# 5. 최근 모니터링 로그
tail -100 drift-monitor.log
```

---

### 로그 분석 팁

```bash
# 1. 에러만 필터링
grep -i "error\|failed\|exception" drift-monitor.log

# 2. 특정 이슈 추적
grep "GLMA-123" drift-monitor.log

# 3. 타임스탬프별 정렬
tail -1000 drift-monitor.log | sort -k1,2

# 4. 성능 분석
grep "duration" drift-monitor.log | awk '{print $NF}' | sort -n | tail -10

# 5. 요약 보고서 생성
echo "=== Monitoring Summary ==="
echo "Total runs: $(grep -c "Checking.*issues" drift-monitor.log)"
echo "Drifts detected: $(grep -c "Drift detected" drift-monitor.log)"
echo "Issues recovered: $(grep -c "Successfully restored" drift-monitor.log)"
echo "Alarms triggered: $(grep -c "ALARM:" drift-monitor.log)"
```

---

## 자주 묻는 질문 (FAQ)

### Q: 드리프트 모니터링은 얼마나 자주 실행해야 하나요?

**A**: 권장 주기는 5~10분입니다.
- 너무 자주(1분 미만): API 부하 증가, 중복 복구
- 적정 범위(5~10분): 드리프트를 신속히 감지하면서 안정적
- 너무 드물게(30분 이상): 드리프트 감지 지연

### Q: 드리프트 이벤트 로그는 어디에 저장되나요?

**A**: 현재는 모니터 인스턴스 메모리에만 저장됩니다.
- 프로덕션 운영 시 데이터베이스에 저장하도록 개선 권장
- 임시 보관(모니터 프로세스 실행 중)

### Q: 여러 회사를 모니터링할 수 있나요?

**A**: 현재는 단일 회사만 지원합니다.
- 각 회사별로 별도 모니터 인스턴스 실행
- 또는 다중 회사 지원하도록 코드 수정

### Q: 자동 복구가 이슈 작성자에게 알림을 보내나요?

**A**: 자동 복구 시 코멘트를 남깁니다.
- 자동 복구 사실과 이유를 명시한 코멘트 추가
- 이슈 활동 피드에 표시됨
- 이슈 구독자들은 알림을 받을 수 있습니다

---

## 지원 및 문의

문제가 해결되지 않으면:

1. **로그 수집**: 위의 "로그 분석 팁" 섹션 참조
2. **환경 정보**: "환경 정보 출력" 섹션의 정보 포함
3. **재현 단계**: 문제를 재현하는 정확한 단계
4. **커스텀 설정**: 모니터 코드의 수정 사항이 있으면 포함

문제 보고는 프로젝트의 Issue 페이지로 제출하세요.
