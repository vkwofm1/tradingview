# Dispatch 어댑터 사용 가이드

Dispatch 채널 어댑터는 Paperclip 에이전트에서 원격 명령 실행을 지원합니다. SSH 연결 실패 시 자동으로 Dispatch로 폴백됩니다.

## 개요

### 주요 기능

- **이중 채널 지원**: SSH와 Dispatch 두 가지 실행 채널 지원
- **자동 폴백**: SSH 사용 불가 시 자동으로 Dispatch로 전환
- **상태 추적**: 모든 명령 실행 결과 로깅 및 통계
- **타임아웃 관리**: 채널별 타임아웃 설정 및 처리

### 구조

```
ExecutionChannel (Abstract)
├── SSHChannel
└── DispatchChannel

FallbackExecutionAdapter
├── ssh_channel: SSHChannel
├── dispatch_channel: DispatchChannel
└── execution_log: List[CommandResult]
```

## 빠른 시작

### 1. 기본 사용법

```python
from app.dispatch_adapter import FallbackExecutionAdapter
import asyncio

async def main():
    # 어댑터 초기화
    adapter = FallbackExecutionAdapter(
        ssh_config={
            "host": "server.example.com",
            "port": "22",
            "user": "deploy",
            "key_path": "/home/user/.ssh/id_rsa"
        },
        dispatch_config={
            "url": "http://localhost:7654",
            "api_key": "your-api-key",
            "timeout": "30"
        }
    )
    
    # 명령 실행
    result = await adapter.execute("ls -la /tmp")
    
    if result.success:
        print(f"Success via {result.channel}: {result.stdout}")
    else:
        print(f"Failed: {result.stderr}")
    
    # 통계 확인
    stats = adapter.get_statistics()
    print(f"Success rate: {stats['success_rate']}%")
    
    # 정리
    await adapter.close()

asyncio.run(main())
```

### 2. SSH 채널만 사용

```python
from app.dispatch_adapter import SSHChannel
import asyncio

async def main():
    ssh = SSHChannel(
        host="server.example.com",
        user="root"
    )
    
    # 연결 확인
    available = await ssh.is_available()
    if available:
        result = await ssh.execute("echo 'Hello'")
        print(result.stdout)

asyncio.run(main())
```

### 3. Dispatch 채널만 사용

```python
from app.dispatch_adapter import DispatchChannel
import asyncio

async def main():
    dispatch = DispatchChannel(
        dispatch_url="http://dispatch.local:7654",
        api_key="secret-key"
    )
    
    result = await dispatch.execute("whoami")
    print(result.stdout)
    
    await dispatch.close()

asyncio.run(main())
```

## 설정

### SSH 설정

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `host` | str | 필수 | 원격 호스트 주소 |
| `port` | int | 22 | SSH 포트 |
| `user` | str | root | SSH 사용자명 |
| `key_path` | str | None | SSH 개인키 경로 (암호 인증 불가) |

### Dispatch 설정

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `url` | str | 필수 | Dispatch 서버 URL |
| `api_key` | str | None | API 인증 키 (선택) |
| `timeout` | int | 30 | 요청 타임아웃 (초) |

## CommandResult

모든 명령 실행은 `CommandResult` 객체를 반환합니다.

```python
@dataclass
class CommandResult:
    success: bool           # 실행 성공 여부
    exit_code: int | None   # 종료 코드
    stdout: str             # 표준 출력
    stderr: str             # 표준 오류
    channel: str            # 사용한 채널 ('ssh', 'dispatch', 'none')
    execution_time_ms: float # 실행 시간 (밀리초)
    timestamp: datetime     # 실행 시간
```

### 결과 확인

```python
if result.success:
    print(f"✓ {result.channel}: {result.execution_time_ms}ms")
    print(result.stdout)
else:
    print(f"✗ 실패: {result.stderr}")
```

## 폴백 동작

### 실행 순서

1. **SSH 연결 확인**: `is_available()` 호출로 SSH 가능 여부 확인
2. **SSH 실행**: 가능하면 SSH로 명령 실행
3. **Dispatch 폴백**: SSH 실패 또는 불가 시 Dispatch로 폴백
4. **최종 실패**: 둘 다 실패하면 오류 반환

### 폴백 예시

```python
# SSH 사용 불가 → Dispatch 사용
result = await adapter.execute("deploy.sh")
# 로그: "SSH connection check failed" → "Falling back to Dispatch"
# 결과: result.channel == "dispatch"

# SSH 명령 실패 → Dispatch 재시도
result = await adapter.execute("failing-command")
# 로그: "SSH execution succeeded" → "SSH execution failed: ..."
# 로그: "Falling back to Dispatch" 
# 결과: result.channel == "dispatch"
```

## 실행 로그 및 통계

### 실행 로그 조회

```python
# 최근 100개 항목 조회 (기본값)
logs = adapter.get_execution_log(limit=50)

for log in logs:
    print(f"{log['timestamp']}: {log['channel']} - {log['success']}")
```

### 통계 조회

```python
stats = adapter.get_statistics()

print(f"총 실행: {stats['total_executions']}")
print(f"성공: {stats['successful']}")
print(f"실패: {stats['failed']}")
print(f"성공률: {stats['success_rate']:.2f}%")
print(f"SSH: {stats['ssh_executions']}")
print(f"Dispatch: {stats['dispatch_executions']}")
print(f"평균 실행 시간: {stats['avg_execution_time_ms']:.2f}ms")
```

## 오류 처리

### 타임아웃

```python
result = await adapter.execute("sleep 100", timeout_sec=5)
if "timed out" in result.stderr.lower():
    print("Command exceeded 5 second timeout")
```

### 채널 불가

```python
# SSH와 Dispatch 모두 불가
result = await adapter.execute("test")
if result.channel == "none":
    print("No execution channel available")
    # 재설정하거나 대기 후 재시도
```

### 부분 실패

```python
# SSH 실패했지만 Dispatch는 성공
result = await adapter.execute("cmd")
if result.success and result.channel == "dispatch":
    print("Successfully executed via fallback channel")
```

## FastAPI 통합 예제

```python
from fastapi import FastAPI
from app.dispatch_adapter import FallbackExecutionAdapter

app = FastAPI()

# 전역 어댑터 초기화
adapter = None

@app.on_event("startup")
async def startup():
    global adapter
    adapter = FallbackExecutionAdapter(
        ssh_config={...},
        dispatch_config={...}
    )

@app.on_event("shutdown")
async def shutdown():
    global adapter
    if adapter:
        await adapter.close()

@app.post("/execute")
async def execute_command(command: str):
    result = await adapter.execute(command)
    return result.to_dict()

@app.get("/statistics")
async def get_stats():
    return adapter.get_statistics()
```

## 테스트

단위 테스트는 `tests/test_dispatch_adapter.py`에 포함되어 있습니다.

```bash
# 모든 테스트 실행
python3 -m pytest tests/test_dispatch_adapter.py -v

# 특정 클래스만 테스트
python3 -m pytest tests/test_dispatch_adapter.py::TestFallbackExecutionAdapter -v

# 커버리지 확인
python3 -m pytest tests/test_dispatch_adapter.py --cov=app.dispatch_adapter
```

## 로깅

어댑터는 Python `logging` 모듈을 사용합니다.

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("app.dispatch_adapter")

# 이제 DEBUG 레벨 로그가 출력됩니다
```

## 성능 고려사항

- **연결 재사용**: SSH 연결은 매번 새로 생성됩니다. 반복 실행 시 성능 영향 가능
- **타임아웃**: 너무 짧은 타임아웃은 예기치 않은 실패 유발 가능
- **로그 크기**: 장기 실행 시 `execution_log` 크기에 주의 필요

## 문제 해결

### SSH 연결 실패

```
SSH connection check failed for localhost: [Errno 111] Connection refused
```

해결:
- SSH 서버 실행 확인: `ssh -v user@host`
- 키 권한 확인: `chmod 600 ~/.ssh/id_rsa`
- 방화벽 확인: SSH 포트(기본 22) 개방 여부 확인

### Dispatch 연결 실패

```
Dispatch health check failed: Connection refused
```

해결:
- Dispatch 서버 실행 확인: `curl http://localhost:7654/health`
- URL과 포트 확인
- API 키 확인

### 명령 실행 오류

```
All connection attempts failed
```

해결:
- 명령 문법 확인
- 권한 확인 (필요시 sudo 사용)
- 타임아웃 증가 시도

## 라이선스 및 기여

Paperclip 에이전트 통합의 일부입니다.
