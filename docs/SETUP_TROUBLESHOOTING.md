# 개발 환경 설정 문제 해결

개발 환경 설정 중 발생하는 일반적인 문제와 해결 방법을 정리했습니다.

## 목차

- [Python 관련 문제](#python-관련-문제)
- [가상 환경 문제](#가상-환경-문제)
- [의존성 설치 문제](#의존성-설치-문제)
- [데이터베이스 관련 문제](#데이터베이스-관련-문제)
- [애플리케이션 실행 문제](#애플리케이션-실행-문제)
- [Docker 관련 문제](#docker-관련-문제)
- [테스트 실행 문제](#테스트-실행-문제)
- [운영 체제별 문제](#운영-체제별-문제)

## Python 관련 문제

### 문제: Python 버전이 맞지 않음

**증상**:
```
python: command not found
또는
Python 3.11 is required but you have Python 3.10
```

**원인**: Python 3.11 이상이 설치되지 않았거나, PATH에 등록되지 않음

**해결책**:

#### macOS

```bash
# Homebrew로 설치
brew install python@3.12

# 기본 python 버전 확인
python3 --version

# 특정 버전으로 가상 환경 생성
python3.12 -m venv .venv
```

#### Ubuntu/Debian

```bash
# Python 3.12 설치
sudo apt update
sudo apt install python3.12 python3.12-venv

# 설치 확인
python3.12 --version
```

#### Windows

1. [python.org](https://www.python.org/downloads/)에서 Python 3.12 설치
2. 설치 시 **"Add Python to PATH"** 체크
3. 설치 후 명령 프롬프트 재시작

```bash
python --version
```

---

### 문제: python3.12 명령을 찾을 수 없음

**증상**:
```
zsh: command not found: python3.12
```

**원인**: Python 3.12가 설치되었지만, PATH에 등록되지 않음

**해결책**:

#### macOS (Homebrew)

```bash
# Homebrew 설치 경로 확인
brew --prefix python@3.12

# 심링크 생성 (또는 경로 설정)
ln -s $(brew --prefix python@3.12)/bin/python3.12 /usr/local/bin/python3.12

# 또는 Shell 설정 파일에 추가
# ~/.bash_profile 또는 ~/.zshrc에 다음 추가:
export PATH="$(brew --prefix python@3.12)/bin:$PATH"

# 재시작
source ~/.zshrc
```

#### Linux

```bash
# 심링크 확인
ls -l /usr/bin/python3.12

# 없으면 update-alternatives로 등록
sudo update-alternatives --install /usr/bin/python3.12 python3.12 /usr/bin/python3.12 1
```

---

## 가상 환경 문제

### 문제: 가상 환경 활성화 실패

**증상**:
```
zsh: permission denied: .venv/bin/activate
또는
command not found: activate
```

**원인**: 활성화 스크립트의 권한 문제 또는 잘못된 경로

**해결책**:

```bash
# 1. 올바른 명령 사용 확인
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate.bat  # Windows CMD
.venv\Scripts\Activate.ps1  # Windows PowerShell

# 2. 권한 설정
chmod +x .venv/bin/activate

# 3. 가상 환경 재생성
rm -rf .venv
python3.12 -m venv .venv
source .venv/bin/activate
```

---

### 문제: "venv module not found" 또는 가상 환경 생성 실패

**증상**:
```
The virtual environment was not created successfully because ensurepip is not available.
```

**원인**: venv 모듈이 설치되지 않음 (특히 Linux에서)

**해결책**:

#### Ubuntu/Debian

```bash
# venv 모듈 설치
sudo apt install python3.12-venv

# 그 후 다시 시도
python3.12 -m venv .venv
```

#### CentOS/RHEL

```bash
# Python 3.12 개발 패키지 설치
sudo yum install python312-devel

python3.12 -m venv .venv
```

---

### 문제: 가상 환경이 여전히 활성화되어 있음

**증상**:
```
(.venv) $  # 프롬프트에 여전히 (.venv)가 표시됨
```

**원인**: deactivate 명령이 제대로 작동하지 않음

**해결책**:

```bash
# 1. deactivate 명령 실행
deactivate

# 2. 위에서 안 되면 새 터미널 창 열기
# 또는 터미널 다시 시작

# 3. 강제로 비활성화
unset VIRTUAL_ENV
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
```

---

## 의존성 설치 문제

### 문제: pip install 중 "permission denied" 오류

**증상**:
```
ERROR: Could not install packages due to an EnvironmentError: [Errno 13] Permission denied: '/usr/lib/python3.12'
```

**원인**: 시스템 Python에 직접 설치하려고 함 (가상 환경이 활성화되지 않음)

**해결책**:

```bash
# 1. 가상 환경이 활성화되었는지 확인
which python  # 반드시 .venv/bin/python이어야 함

# 2. 가상 환경 활성화
source .venv/bin/activate

# 3. pip 업그레이드
pip install --upgrade pip

# 4. 의존성 설치
pip install -e ".[dev]"
```

---

### 문제: "No module named 'X'" 오류

**증상**:
```
ModuleNotFoundError: No module named 'fastapi'
```

**원인**: 
1. 의존성이 설치되지 않음
2. 가상 환경이 활성화되지 않음
3. 잘못된 Python 인터프리터 사용

**해결책**:

```bash
# 1. 가상 환경 활성화 확인
which python
# 반드시 .venv/bin/python이어야 함

# 2. 설치된 패키지 확인
pip list | grep fastapi

# 3. 의존성 다시 설치
pip install -e ".[dev]"

# 4. IDE에서 Python 인터프리터 경로 확인
# VS Code: settings.json의 "python.defaultInterpreterPath" 확인
# PyCharm: Project Settings → Python Interpreter 확인
```

---

### 문제: 패키지 충돌 또는 버전 오류

**증상**:
```
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed
또는
Conflict detected: package-a version X is required, but Y is installed
```

**원인**: 패키지 의존성 충돌

**해결책**:

```bash
# 1. 가상 환경 재설정
deactivate
rm -rf .venv

# 2. 깨끗하게 다시 생성
python3.12 -m venv .venv
source .venv/bin/activate

# 3. pip 업그레이드
pip install --upgrade pip setuptools wheel

# 4. 의존성 설치
pip install -e ".[dev]"

# 5. 문제 진단
pip check
```

---

## 데이터베이스 관련 문제

### 문제: SQLite 데이터베이스 파일 접근 불가

**증상**:
```
sqlite3.OperationalError: unable to open database file
또는
Permission denied: 'data.db'
```

**원인**: 데이터베이스 파일 권한 문제

**해결책**:

```bash
# 1. 파일 권한 확인
ls -la data.db

# 2. 권한 설정 (사용자만)
chmod 600 data.db

# 3. 폴더 권한 설정
chmod 700 .

# 4. 파일 삭제 후 재생성
rm data.db  # 기존 데이터는 삭제됨
# 애플리케이션 다시 시작하면 새 파일 생성됨
```

---

### 문제: PostgreSQL 연결 실패

**증상**:
```
psycopg.OperationalError: could not connect to server: Connection refused
또는
FATAL: Ident authentication failed for user "tradingview"
```

**원인**: 
1. PostgreSQL 서비스가 실행 중이지 않음
2. 잘못된 연결 문자열
3. 사용자/비밀번호 오류

**해결책**:

#### macOS

```bash
# 1. PostgreSQL 서비스 상태 확인
brew services list | grep postgres

# 2. 서비스 시작
brew services start postgresql@16

# 3. 서비스 중지 (필요한 경우)
brew services stop postgresql@16

# 4. 상태 재확인
psql -U postgres -c "SELECT version();"
```

#### Ubuntu/Linux

```bash
# 1. 서비스 상태 확인
sudo systemctl status postgresql

# 2. 서비스 시작
sudo systemctl start postgresql

# 3. 자동 시작 설정
sudo systemctl enable postgresql

# 4. 연결 확인
psql -U postgres -c "SELECT version();"
```

#### Windows

```bash
# 1. 서비스 상태 확인
sc query postgresql-x64-16

# 2. 서비스 시작
net start postgresql-x64-16

# 3. 서비스 중지
net stop postgresql-x64-16
```

#### Docker를 사용하는 경우

```bash
# 1. 컨테이너 상태 확인
docker-compose ps postgres

# 2. 컨테이너 시작
docker-compose up -d postgres

# 3. 로그 확인
docker-compose logs postgres

# 4. 연결 테스트
docker-compose exec postgres psql -U tradingview -d tradingview -c "SELECT 1;"
```

---

### 문제: 데이터베이스 사용자/권한 오류

**증상**:
```
FATAL: password authentication failed for user "tradingview"
또는
permission denied for schema public
```

**원인**: 데이터베이스 사용자가 없거나 권한이 없음

**해결책**:

```bash
# 1. PostgreSQL 접속
psql -U postgres

# 2. 사용자 생성
CREATE USER tradingview WITH PASSWORD 'tradingview_dev_password';

# 3. 데이터베이스 생성
CREATE DATABASE tradingview OWNER tradingview;

# 4. 권한 설정
GRANT ALL PRIVILEGES ON DATABASE tradingview TO tradingview;
\c tradingview
GRANT ALL ON SCHEMA public TO tradingview;

# 5. 확인
\du  # 사용자 목록
\l   # 데이터베이스 목록
```

---

## 애플리케이션 실행 문제

### 문제: 포트 이미 사용 중

**증상**:
```
OSError: [Errno 48] Address already in use
또는
ERROR: Address already in use: ('0.0.0.0', 8509)
```

**원인**: 포트 8509가 이미 사용 중

**해결책**:

```bash
# 1. 포트 사용 중인 프로세스 확인
lsof -i :8509              # macOS/Linux
netstat -ano | findstr :8509  # Windows

# 2. 프로세스 종료
kill -9 <PID>             # macOS/Linux
taskkill /PID <PID> /F    # Windows

# 3. 다른 포트로 실행
uvicorn app.main:app --port 8510

# 4. 또는 백그라운드에서 실행
nohup uvicorn app.main:app &  # Linux
# 또는 새 터미널에서 실행
```

---

### 문제: "ModuleNotFoundError: No module named 'app'"

**증상**:
```
ModuleNotFoundError: No module named 'app'
```

**원인**: 
1. 프로젝트 루트 디렉토리에서 실행하지 않음
2. PYTHONPATH 설정 부재

**해결책**:

```bash
# 1. 프로젝트 루트 디렉토리 확인
pwd
# tradingview-crawl 디렉토리여야 함

# 2. 올바른 위치에서 실행
cd /path/to/tradingview-crawl
source .venv/bin/activate
uvicorn app.main:app --reload

# 3. PYTHONPATH 설정 (필요한 경우)
export PYTHONPATH="${PYTHONPATH}:/path/to/tradingview-crawl"
```

---

## Docker 관련 문제

### 문제: Docker 서비스가 실행 중이지 않음

**증상**:
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock
또는
Docker daemon is not running
```

**원인**: Docker Desktop이 실행되지 않음

**해결책**:

#### macOS

```bash
# Docker Desktop 애플리케이션 실행
open -a Docker

# 또는 명령줄에서
docker run hello-world  # 자동으로 Docker Desktop 실행
```

#### Linux

```bash
# Docker 데몬 시작
sudo systemctl start docker

# 자동 시작 설정
sudo systemctl enable docker

# 사용자 그룹에 추가 (sudo 없이 실행하려면)
sudo usermod -aG docker $USER
newgrp docker
```

#### Windows

```bash
# Docker Desktop 애플리케이션 시작
# 시작 메뉴에서 "Docker Desktop" 검색하여 실행
```

---

### 문제: Docker 컨테이너 빌드 실패

**증상**:
```
ERROR [X/Y] RUN pip install
또는
failed to solve with frontend dockerfile.v0
```

**원인**: 
1. 의존성 설치 실패
2. 네트워크 문제
3. 디스크 공간 부족

**해결책**:

```bash
# 1. 기존 이미지/컨테이너 정리
docker-compose down -v
docker system prune -a

# 2. 네트워크 확인
docker run --rm alpine ping -c 1 8.8.8.8

# 3. 디스크 공간 확인
df -h

# 4. 다시 빌드
docker-compose up --build

# 5. 빌드 로그 자세히 보기
docker-compose build --verbose
```

---

### 문제: 컨테이너 간 통신 실패

**증상**:
```
psycopg.OperationalError: could not translate host name "postgres" to address
```

**원인**: Docker 네트워크 연결 문제

**해결책**:

```bash
# 1. 네트워크 확인
docker network ls
docker network inspect tradingview-crawl_default

# 2. 컨테이너 상태 확인
docker-compose ps

# 3. 재시작
docker-compose restart

# 4. 완전히 다시 생성
docker-compose down
docker-compose up -d

# 5. 헬스 체크 확인
docker-compose exec postgres pg_isready -U tradingview
```

---

## 테스트 실행 문제

### 문제: "pytest: command not found"

**증상**:
```
zsh: command not found: pytest
```

**원인**: 개발 의존성이 설치되지 않음

**해결책**:

```bash
# 1. 가상 환경 활성화
source .venv/bin/activate

# 2. 개발 의존성 설치
pip install -e ".[dev]"

# 3. pytest 설치 확인
which pytest

# 4. 테스트 실행
pytest -v
```

---

### 문제: 테스트 실패 - "No module named 'X'"

**증상**:
```
ModuleNotFoundError: No module named 'httpx'
```

**원인**: 테스트 의존성이 설치되지 않음

**해결책**:

```bash
# 1. 개발 의존성 재설치
pip install -e ".[dev]"

# 2. 설치된 패키지 확인
pip list | grep httpx

# 3. 테스트 다시 실행
pytest -v
```

---

### 문제: 비동기 테스트 실패

**증상**:
```
RuntimeError: asyncio.run() cannot be called from a running event loop
또는
No asyncio mode set
```

**원인**: pytest-asyncio 설정 오류

**해결책**:

```bash
# 1. pytest-asyncio 버전 확인
pip list | grep pytest-asyncio

# 2. 최신 버전 설치
pip install --upgrade pytest-asyncio

# 3. pyproject.toml 설정 확인
cat pyproject.toml | grep asyncio

# 4. 설정이 [tool.pytest.ini_options]에 있어야 함:
# asyncio_mode = "auto"
```

---

## 운영 체제별 문제

### macOS 특정 문제

#### M1/M2 칩 호환성

**문제**: Apple Silicon에서 일부 패키지가 작동하지 않음

**해결책**:

```bash
# 1. Rosetta 2를 통해 Intel 에뮬레이션으로 실행
arch -x86_64 zsh

# 2. 또는 네이티브 ARM 패키지 사용
# Python 3.12 ARM 버전 설치
arch -arm64 brew install python@3.12

# 3. 가상 환경 재생성
arch -arm64 python3.12 -m venv .venv
```

#### 권한 문제

**문제**: 시스템 디렉토리에 쓰기 권한 없음

**해결책**:

```bash
# Homebrew로 설치한 Python 사용
brew install python@3.12

# 또는 pyenv 사용
brew install pyenv
pyenv install 3.12.0
pyenv local 3.12.0
```

---

### Windows 특정 문제

#### PowerShell 실행 정책

**문제**: `.venv\Scripts\Activate.ps1` 실행 불가

**해결책**:

```powershell
# 1. 현재 실행 정책 확인
Get-ExecutionPolicy

# 2. 정책 변경 (현재 사용자만)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 3. 다시 시도
.venv\Scripts\Activate.ps1
```

#### 경로 문자열

**문제**: 경로에 공백이 있으면 오류 발생

**해결책**:

```powershell
# 경로를 따옴표로 감싸기
& ".\.venv\Scripts\Activate.ps1"

# 또는 CMD 사용
cmd /c ".venv\Scripts\activate.bat"
```

---

### Linux 특정 문제

#### 시스템 패키지 관리자 충돌

**문제**: apt나 yum과 pip 패키지 충돌

**해결책**:

```bash
# 항상 가상 환경 사용
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 시스템 전역 설치는 하지 않기
# (sudo pip install 사용 금지)
```

---

## 진단 및 정보 수집

### 환경 정보 출력

문제 보고 시 다음 정보를 포함하세요:

```bash
# Python 정보
python --version
python -c "import sys; print(sys.executable)"

# 가상 환경 정보
which python
echo $VIRTUAL_ENV

# 설치된 패키지
pip list

# 데이터베이스 정보 (PostgreSQL)
psql --version
psql -U postgres -c "SELECT version();"

# Docker 정보 (Docker 사용 시)
docker --version
docker-compose --version
docker-compose ps
```

### 로그 수집

```bash
# 애플리케이션 로그
tail -f app.log

# Docker 로그
docker-compose logs --tail=100

# 시스템 로그
sudo journalctl -u docker -n 50  # Linux
```

---

## 지원 및 문의

문제가 해결되지 않으면:

1. **로그 수집**: 위의 "로그 수집" 섹션 참조
2. **환경 정보**: "환경 정보 출력" 섹션의 정보 포함
3. **상세한 에러 메시지**: 전체 스택 트레이스 포함
4. **재현 단계**: 문제를 재현하는 정확한 단계
5. **운영 체제 및 버전**: macOS, Linux, Windows와 버전 명시

문제 보고는 프로젝트의 Issue 페이지로 제출하세요.
