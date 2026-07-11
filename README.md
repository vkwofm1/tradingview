# TradingView Crawl Server

Market data crawling server for stocks and crypto using FastAPI and Python.

## 📋 목차

- [시스템 요구사항](#시스템-요구사항)
- [빠른 시작](#빠른-시작)
- [개발 환경 설정](#개발-환경-설정)
- [데이터베이스 설정](#데이터베이스-설정)
- [애플리케이션 실행](#애플리케이션-실행)
- [테스트](#테스트)
- [Docker를 통한 실행](#docker를-통한-실행)
- [문제 해결](#문제-해결)

## 시스템 요구사항

### 필수 요구사항

- **Python**: 3.11 이상
- **pip**: Python 패키지 관리자
- **Git**: 버전 관리
- **PostgreSQL**: PostgreSQL 16 이상 (운영 데이터 단일 진실소스)

### 추천 요구사항

- **Docker & Docker Compose**: 컨테이너화된 환경에서 실행
- **Virtual Environment**: Python 가상 환경 (venv 또는 pyenv)
- **Make** (선택): 개발 작업 자동화

### 운영 체제별 설치

#### macOS

```bash
# Python 3.11+ 설치 (Homebrew 사용)
brew install python@3.12

# PostgreSQL 설치 (선택)
brew install postgresql@16

# Docker 설치
brew install --cask docker
```

#### Ubuntu/Debian

```bash
# Python 3.11+ 설치
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip

# PostgreSQL 설치 (선택)
sudo apt install postgresql postgresql-contrib

# Docker 설치
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

#### Windows

1. [Python 3.12](https://www.python.org/downloads/) 설치
2. [PostgreSQL](https://www.postgresql.org/download/windows/) 설치 (선택)
3. [Docker Desktop](https://www.docker.com/products/docker-desktop) 설치

## 빠른 시작

### 옵션 1: Docker Compose (권장)

Docker를 사용하는 것이 가장 간단합니다.

```bash
# 환경 파일 생성
cp .env.example .env

# Docker Compose로 시작
docker-compose up -d

# 로그 확인
docker-compose logs -f crawl
```

애플리케이션은 `http://localhost:8509`에서 실행됩니다.

### 옵션 2: 로컬 개발 환경

```bash
# 1. 저장소 클론
git clone <repository-url>
cd tradingview-crawl

# 2. 환경 파일 설정
cp .env.example .env

# 3. 가상 환경 생성 및 활성화
python3.12 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 또는
.venv\Scripts\activate  # Windows

# 4. 의존성 설치
pip install -e ".[dev]"

# 5. 애플리케이션 실행
uvicorn app.main:app --reload --port 8509
```

애플리케이션은 `http://localhost:8509`에서 실행됩니다.

## 개발 환경 설정

### 1. 저장소 클론

```bash
git clone <repository-url>
cd tradingview-crawl
```

### 2. 가상 환경 설정

Python 가상 환경을 생성하여 의존성 격리를 권장합니다.

```bash
# venv를 사용한 가상 환경 생성
python3.12 -m venv .venv

# 가상 환경 활성화
source .venv/bin/activate  # macOS/Linux
# 또는
.venv\Scripts\activate  # Windows

# 가상 환경 비활성화 (나중에 필요할 때)
deactivate
```

### 3. 의존성 설치

```bash
# 기본 의존성 설치
pip install -e .

# 개발 의존성 포함 (테스트, 린팅 도구 등)
pip install -e ".[dev]"

# pip 최신 버전으로 업그레이드
pip install --upgrade pip
```

### 4. 환경 변수 설정

```bash
# 환경 파일 생성
cp .env.example .env

# .env 파일 편집하여 필요한 설정 수정
cat .env
```

#### 환경 변수 설명

| 변수 | 설명 | 기본값 |
|------|------|-------|
| `DB_TYPE` | 데이터베이스 타입 (`postgres`; `sqlite`는 테스트/마이그레이션 전용) | `postgres` |
| `DATABASE_URL` | PostgreSQL 연결 문자열 | 필수 |
| `POSTGRES_PASSWORD` | PostgreSQL 비밀번호 | `tradingview_dev_password` |

## 데이터베이스 설정

### SQLite (테스트/마이그레이션 호환 전용)

SQLite는 운영 런타임에서 사용하지 않습니다. 격리된 단위 테스트나 과거 데이터
마이그레이션에서만 명시적으로 선택합니다.

```bash
# SQLite 사용 설정
export DB_TYPE=sqlite
export DB_PATH=data.db
```

### PostgreSQL

#### 로컬 PostgreSQL 설정

```bash
# PostgreSQL 서비스 시작 (macOS)
brew services start postgresql@16

# PostgreSQL 서비스 시작 (Linux)
sudo systemctl start postgresql

# 데이터베이스 및 사용자 생성
psql -U postgres -c "CREATE USER tradingview WITH PASSWORD 'tradingview_dev_password';"
psql -U postgres -c "CREATE DATABASE tradingview OWNER tradingview;"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE tradingview TO tradingview;"

# 환경 변수 설정
export DB_TYPE=postgres
export DATABASE_URL="postgresql://tradingview:tradingview_dev_password@localhost:5432/tradingview"
```

#### Docker PostgreSQL 설정

```bash
# docker-compose를 사용하여 PostgreSQL 시작
docker-compose up -d postgres

# PostgreSQL 상태 확인
docker-compose ps postgres

# PostgreSQL 접속
docker-compose exec postgres psql -U tradingview -d tradingview
```

#### 마이그레이션 기록

운영 마이그레이션은 2026-07-11 완료되었습니다. 아래 도구는 보존된 SQLite
archive를 검증하거나 별도 개발 환경에서 이관을 재현할 때만 사용합니다.

```bash
# 마이그레이션 스크립트 실행
python scripts/migrate_to_postgres.py

# 마이그레이션 검증
python scripts/verify_migration.py

# 마이그레이션 요약 확인
cat docs/MIGRATION_SUMMARY.md
```

자세한 내용은 [POSTGRES_MIGRATION.md](docs/POSTGRES_MIGRATION.md)를 참조하세요.

## 애플리케이션 실행

### 개발 환경에서 실행

```bash
# 가상 환경 활성화
source .venv/bin/activate

# 자동 재로드 기능과 함께 실행 (개발 환경)
uvicorn app.main:app --reload --port 8509

# 또는 기본 설정으로 실행
uvicorn app.main:app --host 0.0.0.0 --port 8509
```

#### 애플리케이션 주소

- **기본 URL**: `http://localhost:8509`
- **API 문서**: `http://localhost:8509/docs` (Swagger UI)
- **대체 문서**: `http://localhost:8509/redoc` (ReDoc)
- **헬스 체크**: `http://localhost:8509/health`

### 프로덕션 환경에서 실행

```bash
# Gunicorn을 사용하여 실행 (권장)
pip install gunicorn
gunicorn app.main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8509

# 또는 uvicorn으로 실행
uvicorn app.main:app --host 0.0.0.0 --port 8509 --workers 4
```

## 테스트

### 테스트 실행

```bash
# 가상 환경 활성화
source .venv/bin/activate

# 모든 테스트 실행
pytest

# 특정 파일의 테스트 실행
pytest tests/test_module.py

# 특정 테스트 함수 실행
pytest tests/test_module.py::test_function_name

# 자세한 출력과 함께 테스트 실행
pytest -v

# 커버리지 리포트 포함
pytest --cov=app --cov-report=html
```

### 테스트 파일 구조

```
tests/
├── test_paperclip_drift_monitor.py  # 데이터 드리프트 모니터링 테스트
└── ... (기타 테스트 파일)
```

## Docker를 통한 실행

### Docker Compose 사용

Docker Compose를 사용하면 PostgreSQL과 애플리케이션을 함께 실행할 수 있습니다.

#### 시작

```bash
# 환경 파일 생성
cp .env.example .env

# 서비스 시작 (백그라운드)
docker-compose up -d

# 로그 확인
docker-compose logs -f crawl

# 서비스 상태 확인
docker-compose ps
```

#### 중지

```bash
# 서비스 중지
docker-compose down

# 볼륨을 포함하여 완전히 제거
docker-compose down -v
```

#### 데이터베이스 초기화

```bash
# PostgreSQL 볼륨 제거 (주의: 데이터 손실)
docker-compose down -v
docker-compose up -d
```

### Dockerfile를 사용한 빌드

```bash
# Docker 이미지 빌드
docker build -t tradingview-crawl:latest .

# Docker 컨테이너 실행
docker run -d \
  -p 8509:8509 \
  --add-host=host.docker.internal:host-gateway \
  -e DB_TYPE=postgres \
  -e DATABASE_URL=postgresql://tradingview:tradingview_dev_password@host.docker.internal:5432/tradingview \
  -v crawl-data:/app/data \
  --name tradingview-crawl \
  tradingview-crawl:latest

# 로그 확인
docker logs -f tradingview-crawl

# 컨테이너 중지
docker stop tradingview-crawl
docker rm tradingview-crawl
```

## 모니터링 및 진단

### 데이터베이스 모니터링

```bash
# 데이터베이스 진단 도구 실행
python scripts/diagnose_db.py

# 데이터베이스 복구 스크립트 실행
bash scripts/diagnose_and_recover.sh
```

자세한 내용은 [DATABASE_MONITORING.md](docs/DATABASE_MONITORING.md)를 참조하세요.

### 데이터 드리프트 모니터링

```bash
# 데이터 드리프트 모니터링 실행
python scripts/monitor-drift.py
```

## 문제 해결

### 일반적인 문제

#### Python 버전 오류

```
Error: Python 3.11 or higher is required
```

**해결책**: Python 3.12 설치 후 가상 환경 재생성

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

#### 포트 이미 사용 중

```
Error: Address already in use: ('0.0.0.0', 8509)
```

**해결책**: 다른 포트 사용 또는 기존 프로세스 종료

```bash
# 다른 포트로 실행
uvicorn app.main:app --port 8510

# 또는 기존 프로세스 확인 및 종료
lsof -i :8509  # macOS/Linux
netstat -ano | findstr :8509  # Windows
```

#### PostgreSQL 연결 오류

```
Error: could not connect to server: Connection refused
```

**해결책**: PostgreSQL 서비스 시작

```bash
# macOS
brew services start postgresql@16

# Linux
sudo systemctl start postgresql

# Docker
docker-compose up -d postgres
```

#### 데이터베이스 마이그레이션 오류

마이그레이션 중 오류 발생 시:

```bash
# 상태 확인
python scripts/verify_migration.py

# 데이터베이스 복구
bash scripts/diagnose_and_recover.sh
```

자세한 내용은 [DATABASE_RECOVERY.md](docs/DATABASE_RECOVERY.md)를 참조하세요.

### 로그 확인

```bash
# 로컬 개발 환경
# 애플리케이션 로그는 터미널에 출력됩니다.

# Docker 환경
docker-compose logs -f crawl  # 애플리케이션
docker-compose logs -f postgres  # PostgreSQL
```

### 성능 튜닝

#### Python 최적화

```bash
# 최적화 모드로 실행
PYTHONOPTIMIZE=2 uvicorn app.main:app
```

#### PostgreSQL 연결 풀 최적화

환경 변수로 연결 풀 설정 가능:

```bash
export DATABASE_URL="postgresql://tradingview:password@localhost:5432/tradingview?application_name=tradingview_crawl"
```

## 프로젝트 구조

```
tradingview-crawl/
├── app/
│   ├── main.py              # FastAPI 애플리케이션 진입점
│   ├── db.py                # 데이터베이스 연결 및 설정
│   ├── monitoring.py        # 모니터링 기능
│   ├── db_monitoring.py     # 데이터베이스 모니터링
│   └── collectors/          # 데이터 수집 모듈
│       ├── stocks.py        # 주식 데이터 수집
│       ├── crypto.py        # 암호화폐 데이터 수집
│       ├── bithumb.py       # Bithumb 수집
│       └── upbit.py         # Upbit 수집
├── scripts/
│   ├── diagnose_db.py       # 데이터베이스 진단
│   ├── migrate_to_postgres.py  # SQLite → PostgreSQL 마이그레이션
│   ├── verify_migration.py   # 마이그레이션 검증
│   └── monitor-drift.py     # 데이터 드리프트 모니터링
├── tests/
│   └── test_*.py            # 테스트 파일
├── docs/
│   ├── POSTGRES_MIGRATION.md       # PostgreSQL 마이그레이션 가이드
│   ├── DATABASE_MONITORING.md      # 데이터베이스 모니터링 가이드
│   ├── DATABASE_RECOVERY.md        # 데이터베이스 복구 가이드
│   └── QUICK_MIGRATION_START.md    # 마이그레이션 빠른 시작
├── .env.example             # 환경 변수 템플릿
├── docker-compose.yml       # Docker Compose 설정
├── Dockerfile              # Docker 빌드 설정
├── pyproject.toml          # Python 프로젝트 설정
└── README.md               # 이 파일
```

## 추가 문서

- [PostgreSQL 마이그레이션 가이드](docs/POSTGRES_MIGRATION.md)
- [데이터베이스 모니터링 가이드](docs/DATABASE_MONITORING.md)
- [데이터베이스 복구 가이드](docs/DATABASE_RECOVERY.md)
- [마이그레이션 빠른 시작](docs/QUICK_MIGRATION_START.md)

## 개발 팀

이 프로젝트는 GLM Data Operations 팀이 관리합니다.

## 라이선스

이 프로젝트의 라이선스 정보는 LICENSE 파일을 참조하세요.
