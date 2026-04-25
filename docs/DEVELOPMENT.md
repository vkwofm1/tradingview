# 개발 가이드

개발 환경 설정, 워크플로우, 그리고 베스트 프랙티스에 대한 상세 가이드입니다.

## 목차

- [IDE 설정](#ide-설정)
- [개발 워크플로우](#개발-워크플로우)
- [코드 스타일 및 컨벤션](#코드-스타일-및-컨벤션)
- [디버깅](#디버깅)
- [성능 프로파일링](#성능-프로파일링)
- [의존성 관리](#의존성-관리)
- [깃 워크플로우](#깃-워크플로우)

## IDE 설정

### VS Code

#### 1. 확장 프로그램 설치

```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.vscode-pylance",
    "ms-python.debugpy",
    "charliermarsh.ruff",
    "ms-vscode.makefile-tools",
    "eamodio.gitlens"
  ]
}
```

**설치 방법**: VS Code의 확장 마켓플레이스에서 검색하여 설치

#### 2. 작업 공간 설정

`.vscode/settings.json` 생성:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": false,
  "python.formatting.provider": "none",
  "[python]": {
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll": "explicit"
    },
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
  "python.analysis.typeCheckingMode": "basic",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["tests"]
}
```

`.vscode/launch.json` 생성:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "FastAPI Server",
      "type": "python",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--reload", "--port", "8509"],
      "jinja": true,
      "justMyCode": true,
      "console": "integratedTerminal"
    },
    {
      "name": "pytest",
      "type": "python",
      "request": "launch",
      "module": "pytest",
      "args": ["${file}"],
      "justMyCode": true,
      "console": "integratedTerminal"
    }
  ]
}
```

### PyCharm

#### 1. 프로젝트 설정

- **File** → **Open** → 프로젝트 폴더 선택
- **PyCharm** → **Preferences** (macOS) 또는 **File** → **Settings** (Linux/Windows)

#### 2. Python 인터프리터 설정

1. **Preferences/Settings** 열기
2. **Project** → **Python Interpreter**
3. **Add** 클릭
4. **Existing Environment** 선택
5. `.venv/bin/python` 경로 선택

#### 3. 실행 구성 설정

1. **Run** → **Edit Configurations**
2. **+** 클릭 후 **Python** 선택
3. 다음 설정:
   ```
   Name: FastAPI Server
   Module: uvicorn
   Parameters: app.main:app --reload --port 8509
   ```

### 기타 IDE

- **Sublime Text**: LSP 클라이언트 플러그인 + Pylance 사용
- **Vim/Neovim**: nvim-lspconfig + pyright 설정
- **JetBrains IDEs**: PyCharm 설정과 동일

## 개발 워크플로우

### 1. 기능 개발

```bash
# 1. 새로운 브랜치 생성
git checkout -b feature/my-feature

# 2. 가상 환경 활성화
source .venv/bin/activate

# 3. 개발 서버 시작
uvicorn app.main:app --reload --port 8509

# 4. 다른 터미널에서 코드 편집
# IDE 또는 에디터에서 코드 작성

# 5. 테스트 실행
pytest -v

# 6. 커밋 및 푸시
git add .
git commit -m "feat: 기능 설명"
git push origin feature/my-feature
```

### 2. 버그 수정

```bash
# 1. 이슈 브랜치 생성
git checkout -b fix/bug-description

# 2. 버그 재현 테스트 작성
# tests/test_fix.py 작성

# 3. 버그 수정
# 관련 코드 수정

# 4. 테스트 통과 확인
pytest -v tests/test_fix.py

# 5. 커밋 및 푸시
git add .
git commit -m "fix: 버그 설명"
git push origin fix/bug-description
```

### 3. 코드 리뷰

```bash
# 원격 브랜치 최신화
git fetch origin

# 리뷰할 브랜치 확인
git checkout review-branch
git pull origin review-branch

# 변경사항 검토
git diff main

# 로컬에서 테스트
pytest -v
```

## 코드 스타일 및 컨벤션

### Python 코드 스타일

이 프로젝트는 **PEP 8** 스타일 가이드를 따릅니다.

#### Ruff를 사용한 린팅

```bash
# 전체 프로젝트 린팅
ruff check app tests

# 자동 수정
ruff check app tests --fix

# 포맷팅
ruff format app tests
```

#### 설정 파일 (`pyproject.toml`)

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]  # 라인 길이는 포맷터에서 처리

[tool.ruff.lint.isort]
known-first-party = ["app"]
```

### 네이밍 컨벤션

```python
# 모듈 및 파일명: snake_case
# 예: app/collectors/stocks.py

# 클래스: PascalCase
class StockCollector:
    pass

# 함수 및 메서드: snake_case
def fetch_stock_data():
    pass

# 상수: UPPER_SNAKE_CASE
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

# 비공개 변수/함수: _leading_underscore
_internal_state = {}
def _helper_function():
    pass
```

### 타입 힌팅

```python
from typing import Optional, List, Dict, Any

# 함수 타입 힌팅
def fetch_data(symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    데이터를 가져옵니다.
    
    Args:
        symbol: 주식 심볼
        limit: 반환할 데이터의 최대 개수
        
    Returns:
        데이터 리스트
    """
    pass

# 선택적 타입
def process(data: Optional[str] = None) -> Optional[bool]:
    pass
```

### 주석 및 문서화

```python
def complex_calculation(value: float) -> float:
    """복잡한 계산을 수행합니다."""
    # 특이한 동작이나 이유를 설명
    result = value * 2.5  # 특정 이유로 2.5를 곱함
    return result

class DataProcessor:
    """데이터 처리 클래스입니다."""
    
    def process(self, data: List[Dict]) -> None:
        """데이터를 처리합니다."""
        pass
```

## 디버깅

### VS Code에서 디버깅

1. 중단점 설정: 라인 번호 왼쪽 클릭
2. **Run** → **Start Debugging** 또는 **F5**
3. 디버그 콘솔에서 변수 검사

### Python 인터프리터에서 디버깅

```bash
# pdb를 사용한 디버깅
python -m pdb scripts/diagnose_db.py

# 코드 내에서 중단점 설정
import pdb; pdb.set_trace()

# 또는 breakpoint() 사용 (Python 3.7+)
breakpoint()
```

### 로깅을 통한 디버깅

```python
import logging

logger = logging.getLogger(__name__)

# 디버그 레벨 로깅
logger.debug("변수 값: %s", value)
logger.info("작업 완료")
logger.warning("주의: 문제 발생")
logger.error("오류 발생", exc_info=True)
```

## 성능 프로파일링

### cProfile을 사용한 프로파일링

```bash
# 전체 애플리케이션 프로파일링
python -m cProfile -s cumulative app/main.py > profile.txt

# 특정 스크립트 프로파일링
python -m cProfile -s cumulative scripts/diagnose_db.py
```

### 메모리 프로파일링

```bash
# memory_profiler 설치
pip install memory-profiler

# 메모리 사용량 프로파일링
python -m memory_profiler app/main.py
```

### 실시간 성능 모니터링

```python
import time

def measure_performance(func):
    """함수 실행 시간을 측정합니다."""
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"{func.__name__} 실행 시간: {elapsed:.2f}초")
        return result
    return wrapper

@measure_performance
def slow_function():
    time.sleep(2)
```

## 의존성 관리

### 패키지 추가

```bash
# 기본 패키지 추가
pip install package-name

# 개발 패키지 추가
pip install --dev package-name

# 프로젝트에 저장
pip install -e .  # pyproject.toml 재읽기
```

### 의존성 업그레이드

```bash
# 특정 패키지 업그레이드
pip install --upgrade package-name

# 모든 패키지 최신 버전으로 업그레이드
pip install --upgrade pip
pip list --outdated
```

### 의존성 확인

```bash
# 설치된 패키지 목록
pip list

# 특정 패키지 정보
pip show package-name

# 의존성 트리
pip install pipdeptree
pipdeptree
```

## 깃 워크플로우

### 커밋 메시지 규칙

```
<type>: <제목>

<본문>

<footer>
```

**Type 종류**:
- `feat`: 새로운 기능
- `fix`: 버그 수정
- `docs`: 문서 수정
- `style`: 코드 스타일 변경 (포맷팅 등)
- `refactor`: 코드 리팩토링
- `test`: 테스트 추가 또는 수정
- `chore`: 빌드, 의존성 등의 변경

**예시**:
```
feat: PostgreSQL 마이그레이션 기능 추가

- SQLite에서 PostgreSQL로 데이터 마이그레이션
- 기존 데이터 검증 기능 포함
- 마이그레이션 롤백 기능 지원

Fixes #GLMA-679
```

### 브랜치 전략

```
main (프로덕션)
 ↑
 └─ develop (개발)
     ↑
     ├─ feature/* (기능 개발)
     ├─ fix/* (버그 수정)
     └─ docs/* (문서)
```

### 일일 커밋 체크리스트

- [ ] 테스트 실행 (`pytest`)
- [ ] 린팅 통과 (`ruff check`)
- [ ] 타입 체크 통과
- [ ] 커밋 메시지가 명확한가?
- [ ] 불필요한 파일 커밋하지 않았는가?

## 유용한 개발 도구

### Pre-commit 훅

```bash
# pre-commit 설치
pip install pre-commit

# 설정 파일 생성 (.pre-commit-config.yaml)
# git push 전에 자동 검사
pre-commit install
```

### Makefile 사용

```makefile
.PHONY: help install dev test lint format clean

help:
	@echo "사용 가능한 명령:"
	@echo "  make install   - 의존성 설치"
	@echo "  make dev       - 개발 서버 실행"
	@echo "  make test      - 테스트 실행"
	@echo "  make lint      - 린팅 검사"
	@echo "  make format    - 코드 포맷"
	@echo "  make clean     - 임시 파일 삭제"

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --port 8509

test:
	pytest -v

lint:
	ruff check app tests

format:
	ruff format app tests
	ruff check app tests --fix

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov
```

사용:
```bash
make help
make dev
make test
make format
```

## 자주 하는 질문

### Q: 가상 환경을 재설정하려면?

A: 다음 명령어를 실행하세요:

```bash
rm -rf .venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Q: 특정 Python 버전으로 개발하려면?

A: pyenv를 사용하세요:

```bash
# pyenv 설치
brew install pyenv  # macOS

# 원하는 버전 설치
pyenv install 3.12.0

# 프로젝트에서 사용할 버전 설정
pyenv local 3.12.0
```

### Q: 데이터베이스 상태를 초기화하려면?

A: 다음 중 선택:

```bash
# SQLite 초기화
rm data.db

# PostgreSQL 초기화 (Docker)
docker-compose down -v
docker-compose up -d postgres
```

## 참고 자료

- [FastAPI 공식 문서](https://fastapi.tiangolo.com/)
- [Python PEP 8 스타일 가이드](https://www.python.org/dev/peps/pep-0008/)
- [pytest 문서](https://pytest.org/)
- [Git 워크플로우](https://git-scm.com/book/en/v2)
