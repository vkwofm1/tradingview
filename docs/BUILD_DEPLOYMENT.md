# 빌드/배포 프로세스 가이드

이 문서는 애플리케이션의 빌드, 테스트, 배포 전체 과정을 설명합니다.

## 목차

- [빌드 환경 구성](#빌드-환경-구성)
- [로컬 개발 환경 설정](#로컬-개발-환경-설정)
- [Docker를 이용한 빌드](#docker를-이용한-빌드)
- [배포 파이프라인](#배포-파이프라인)
- [환경 변수 관리](#환경-변수-관리)
- [배포 검증](#배포-검증)
- [롤백 절차](#롤백-절차)
- [문제 해결](#문제-해결)

## 빌드 환경 구성

### 필수 요구사항

- **Python**: 3.12 이상
- **Docker**: 최신 버전
- **Docker Compose**: 최신 버전
- **Git**: 최신 버전

### 시스템 확인

```bash
# 필수 도구 버전 확인
python3 --version
docker --version
docker-compose --version
git --version
```

## 로컬 개발 환경 설정

### 1. 개발 환경 구성

```bash
# 1. 저장소 클론
git clone https://github.com/vkwofm1/tradingview.git
cd tradingview

# 2. 가상 환경 설정
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 또는
.venv\Scripts\activate  # Windows

# 3. 의존성 설치
pip install -e ".[dev]"

# 4. 환경 변수 설정
cp .env.example .env
# .env 파일 수정 필요시 편집
```

### 2. Docker Compose를 이용한 로컬 실행

```bash
# 전체 스택 시작 (PostgreSQL + 애플리케이션)
docker-compose up -d

# 로그 확인
docker-compose logs -f crawl

# 서비스 상태 확인
docker-compose ps

# 서비스 중지
docker-compose down

# 데이터 초기화 후 재시작
docker-compose down -v
docker-compose up -d
```

### 3. 서비스 헬스 체크

```bash
# API 헬스 체크 (별도 터미널에서)
curl http://localhost:8509/health

# PostgreSQL 접속 확인
psql -h localhost -U tradingview -d tradingview -c "SELECT 1;"

# 컨테이너 상태 확인
docker-compose ps

# 로그에서 오류 확인
docker-compose logs postgres
docker-compose logs crawl
```

## Docker를 이용한 빌드

### 1. 이미지 빌드

```bash
# 로컬에서 이미지 빌드
docker build -t tradingview:dev .

# 빌드 진행 상황 확인
docker build -t tradingview:dev --progress=plain .

# 캐시 없이 빌드
docker build --no-cache -t tradingview:dev .
```

### 2. Dockerfile 구조

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY app/__init__.py app/__init__.py
RUN pip install --no-cache-dir .
COPY . .
EXPOSE 8509
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8509"]
```

**빌드 단계 설명:**
- **Stage 1**: 베이스 이미지 (python:3.12-slim) 로드
- **Stage 2**: 프로젝트 의존성 설치 (캐시 활용)
- **Stage 3**: 전체 코드 복사
- **Stage 4**: 애플리케이션 시작

### 3. 이미지 최적화

```bash
# 이미지 크기 확인
docker images tradingview

# 주요 최적화 사항
# 1. 가벼운 베이스 이미지 (python:3.12-slim)
# 2. --no-cache-dir 옵션으로 pip 캐시 제거
# 3. 멀티스테이지 빌드 (필요시)
# 4. 불필요한 파일 제외 (.dockerignore)
```

## 배포 파이프라인

### CI/CD 워크플로우

자동 배포 파이프라인은 `main` 브랜치에 푸시 또는 `workflow_dispatch`로 수동 실행됩니다.

```
Push to main / Manual trigger
    ↓
Build Docker Image (GHCR)
    ↓
Push to Registry
    ↓
Update ArgoCD Config
    ↓
ArgoCD Deploy to K8s
```

### 1. 빌드 및 푸시 단계

파일: `.github/workflows/deploy.yml`

```yaml
- name: Build and push
  uses: docker/build-push-action@v5
  with:
    context: .
    push: true
    tags: |
      ghcr.io/vkwofm1/tradingview:YYYYMMDD-HHMMSS
      ghcr.io/vkwofm1/tradingview:latest
```

**특징:**
- 자동 이미지 태그 생성 (타임스탬프 기반: `YYYYMMDD-HHMMSS`)
- 두 개의 태그 생성: 버전별 + `latest`
- GHCR (GitHub Container Registry)에 푸시

### 2. ArgoCD 설정 업데이트

```bash
# 워크플로우에서 자동 실행
git clone git@github.com:vkwofm1/argo-deploy.git
cd argo-deploy
sed -i "s|image: ghcr.io/vkwofm1/tradingview:.*|image: ghcr.io/vkwofm1/tradingview:NEWTAG|" tradingview/values.yaml
git commit -m "Update tradingview image to NEWTAG"
git push
```

**프로세스:**
- ArgoCD 저장소의 `tradingview/values.yaml` 업데이트
- 쿠버네티스 클러스터가 자동으로 새 이미지 배포

### 3. 수동 배포

```bash
# 긴급 수동 배포 필요시
cd /path/to/argo-deploy

# 1. 이미지 태그 확인
docker images ghcr.io/vkwofm1/tradingview

# 2. values.yaml 업데이트
vi tradingview/values.yaml
# image: ghcr.io/vkwofm1/tradingview:수정할버전

# 3. 변경사항 커밋 및 푸시
git add tradingview/values.yaml
git commit -m "Manual update: tradingview image"
git push

# 4. ArgoCD 동기화 (UI 또는 CLI)
argocd app sync tradingview
```

## 환경 변수 관리

### 1. 로컬 개발 환경

```bash
# .env 파일 생성
cat > .env << EOF
DB_TYPE=postgres
DATABASE_URL=postgresql://tradingview:tradingview_dev_password@postgres:5432/tradingview
DEBUG=True
LOG_LEVEL=DEBUG
EOF

# Docker Compose에서 자동으로 로드
docker-compose up
```

### 2. Docker Compose 환경 변수

```yaml
# docker-compose.yml의 환경 변수
environment:
  DB_TYPE: ${DB_TYPE:-postgres}
  DATABASE_URL: ${DATABASE_URL:-postgresql://...}
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-tradingview_dev_password}
```

### 3. 프로덕션 환경 변수

```bash
# GitHub Secrets에서 관리
POSTGRES_PASSWORD: 프로덕션 암호
DATABASE_URL: postgresql://user:pass@host:5432/db
DEPLOY_SSH_KEY: ArgoCD 저장소 배포 키
```

### 4. 환경 변수 오버라이드

```bash
# 커맨드라인으로 오버라이드
DB_TYPE=postgres DATABASE_URL=postgresql://... docker-compose up

# 또는 .env 파일 사용
export $(cat .env | xargs)
docker-compose up
```

## 배포 검증

### 1. 빌드 성공 확인

```bash
# GitHub Actions 워크플로우 확인
# https://github.com/vkwofm1/tradingview/actions

# 또는 CLI로 확인
gh run list --repo vkwofm1/tradingview

# 특정 워크플로우 실행 상태 확인
gh run view <RUN_ID>
```

### 2. 이미지 검증

```bash
# 로컬에서 이미지 실행하여 테스트
docker run -p 8509:8509 ghcr.io/vkwofm1/tradingview:latest

# 헬스 체크
curl http://localhost:8509/health

# 로그 확인
docker logs <CONTAINER_ID>
```

### 3. 배포 후 검증

```bash
# 1. 클러스터 파드 확인
kubectl get pods -n tradingview

# 2. 배포 상태 확인
kubectl rollout status deployment/tradingview -n tradingview

# 3. 서비스 접근 확인
kubectl port-forward svc/tradingview 8509:8509 -n tradingview
curl http://localhost:8509/health

# 4. 로그 확인
kubectl logs -f deployment/tradingview -n tradingview

# 5. 이벤트 확인
kubectl describe deployment tradingview -n tradingview
```

### 4. 성능 모니터링

```bash
# 리소스 사용량 확인
kubectl top pods -n tradingview

# 배포 리소스 정보
kubectl get deployment tradingview -n tradingview -o json | jq '.spec.template.spec.containers[0].resources'

# 현재 실행 중인 이미지 버전 확인
kubectl get pods -n tradingview -o jsonpath='{.items[0].spec.containers[0].image}'
```

## 롤백 절차

### 1. 자동 롤백 (실패시)

워크플로우 실패시 자동으로 이전 버전 상태를 유지합니다.

### 2. 수동 롤백

```bash
# 1. 이전 배포 확인
kubectl rollout history deployment/tradingview -n tradingview

# 2. 특정 리비전으로 롤백
kubectl rollout undo deployment/tradingview -n tradingview --to-revision=<REVISION>

# 또는 ArgoCD를 이용한 롤백
cd argo-deploy
git log --oneline tradingview/values.yaml
git revert <COMMIT_HASH>
git push

# ArgoCD 동기화
argocd app sync tradingview
```

### 3. 데이터베이스 롤백

```bash
# PostgreSQL 백업에서 복원
kubectl exec -it <POSTGRES_POD> -n tradingview -- \
  psql -U tradingview -d tradingview < backup.sql

# 자세한 정보는 DATABASE_RECOVERY.md 참고
```

## 문제 해결

### 1. 빌드 실패

```bash
# 캐시 초기화 후 재빌드
docker build --no-cache -t tradingview:dev .

# 빌드 로그 확인
docker build -t tradingview:dev --progress=plain . 2>&1 | tail -50

# Dockerfile 문법 검사
docker buildx build --dry-run .
```

### 2. 런타임 오류

```bash
# 컨테이너 로그 확인
docker logs <CONTAINER_ID>

# 상세 로그 보기
docker logs -f <CONTAINER_ID>

# 컨테이너 진입
docker exec -it <CONTAINER_ID> /bin/bash

# 환경 변수 확인
docker exec <CONTAINER_ID> env | grep -E "DB_|DATABASE"
```

### 3. 네트워크 문제

```bash
# 컨테이너 간 네트워크 확인
docker network ls
docker network inspect bridge

# DNS 확인
docker exec <CONTAINER_ID> nslookup postgres

# 포트 바인딩 확인
docker port <CONTAINER_ID>
```

### 4. 배포 문제

```bash
# 파드 상태 확인
kubectl describe pod <POD_NAME> -n tradingview

# 파드 로그 확인
kubectl logs <POD_NAME> -n tradingview --previous

# 이미지 풀 오류
kubectl get events -n tradingview

# 리소스 부족 확인
kubectl describe node
```

### 5. 데이터베이스 연결 오류

```bash
# 데이터베이스 상태 확인
docker-compose exec postgres pg_isready -U tradingview

# 연결 테스트
docker-compose exec postgres \
  psql -U tradingview -h postgres -d tradingview -c "SELECT 1;"

# 환경 변수 확인
docker-compose exec crawl env | grep DATABASE
```

### 6. 권한 문제

```bash
# Docker 데몬 권한
sudo usermod -aG docker $USER

# 저장소 클론 권한
ssh-keyscan github.com >> ~/.ssh/known_hosts
ssh -T git@github.com

# 파드 실행 권한
kubectl auth can-i create deployments --as=system:serviceaccount:tradingview:default
```

## 성능 최적화

### 1. 빌드 속도 개선

```bash
# .dockerignore 사용
cat > .dockerignore << EOF
.git
.github
.pytest_cache
__pycache__
.venv
.env
.DS_Store
*.pyc
EOF

# 레이어 캐싱 활용
# Dockerfile에서 자주 변경되는 코드는 끝에 배치
```

### 2. 이미지 크기 최소화

```bash
# 현재 이미지 크기
docker images ghcr.io/vkwofm1/tradingview

# 불필요한 파일 제거
# 1. .dockerignore에 추가
# 2. 멀티스테이지 빌드 사용
# 3. pip 캐시 제거 (--no-cache-dir)
```

### 3. 런타임 성능

```bash
# uvicorn 워커 수 조정
CMD ["uvicorn", "app.main:app", "--workers", "4", "--port", "8509"]

# 타임아웃 설정
CMD ["uvicorn", "app.main:app", "--timeout-keep-alive", "65"]
```

## 배포 체크리스트

배포 전 다음 항목을 확인하세요:

- [ ] 모든 테스트 통과 (`pytest`)
- [ ] 린팅 검사 통과 (`ruff check`)
- [ ] 타입 체크 통과
- [ ] 코드 리뷰 완료
- [ ] 변경사항 문서화 완료
- [ ] 마이그레이션 필요시 준비 완료
- [ ] 환경 변수 설정 확인
- [ ] 롤백 계획 수립
- [ ] 모니터링 대시보드 준비
- [ ] 문제 발생시 연락처 준비

## 참고 자료

- [Docker 공식 문서](https://docs.docker.com/)
- [Docker Compose 공식 문서](https://docs.docker.com/compose/)
- [GitHub Actions 문서](https://docs.github.com/en/actions)
- [Kubernetes 공식 문서](https://kubernetes.io/docs/)
- [ArgoCD 문서](https://argo-cd.readthedocs.io/)
- [FastAPI 배포 가이드](https://fastapi.tiangolo.com/deployment/)
