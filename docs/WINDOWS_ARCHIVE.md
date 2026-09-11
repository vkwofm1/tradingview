# D드라이브 PostgreSQL 보관·조회 DB

## 범위

- 원본: Kubernetes `default/tradingview-postgres`, DB `tradingview`, PostgreSQL 16.
- 보관: Windows PostgreSQL 16.15, `D:\PostgreSQL\data`, 포트 `127.0.0.1:55432`.
- 조회 DB: `tradingview_archive`. 수집·매매 원본 접속 설정은 바꾸지 않는다.
- 공식 EDB Windows 바이너리 ZIP을 사용한다. 새 Windows 서비스나 관리자 권한은 요구하지 않는다.
- 바이너리 SHA256: `25e6fcdfb8caec38691bf461125e7564508760666f7b8e5dc6a5f0818f58f81e`.
- D는 Samsung M3 Portable USB HDD다. 복원·인덱스 생성은 순차 실행하고,
  인덱스 작업 메모리는 512MB로 제한한다. 원본 덤프에는 모든 인덱스 정의를 보존하되
  보관 DB는 PK·유일성·외래키와 종목별 시간 복합 인덱스를 유지하고,
  중복 선두 컬럼·운영 작업 조회용 일반 인덱스 7개는 생성하지 않는다.
  대용량 임시 복원 테이블은 적재·인덱스 생성 중 자동 vacuum을 중지하고,
  보관 DB로 게시한 뒤 정상 유지관리를 다시 켠다. 운영 원본 설정은 변경하지 않는다.
- Windows 로그인 시 사용자 시작프로그램이 서버를 기동한다. 로그오프 상태의
  Windows 시스템 서비스는 아니며, 서비스 등록이 필요하면 별도 관리자 작업이 필요하다.
- WSL 증설은 아래와 같이 완료했으며, 용량 기반 자동 이관만 검증된 원본 정리를 허용한다.
  위키 기록은 하지 않는다.

## 현재 상태와 요청된 관리 방식

- 2026-09-11 사용자 의도 확인 후 매일 전체 백업 예약
  `tradingview-archive.timer`를 중지·비활성화했다. D의 조회 DB와 검증된 기존 덤프,
  Windows 로그인 시 보관 DB를 시작하는 설정은 유지한다.
- 요청된 방식은 WSL 가상디스크를 256GiB에서 406GiB로 150GiB 늘리고,
  **공간이 부족해질 때만** 오래된 투자 데이터부터 D의 PostgreSQL로 이관하는 것이다.
  고정 보관일이 지났다는 이유만으로 원본 데이터를 삭제하지 않는다.
- 이관 데이터의 보관·조회 가능 여부를 검증한 후에만 해당 원본 정리를 허용한다.
  D 연결 실패·용량 부족·검증 실패 시에는 원본을 보존한다.
- WSL 증설은 2026-09-12 07:16 KST 완료했다. 가상디스크는 406GiB이며
  파일시스템 확장도 검증했다. 후속 확인 시 `df -h /`는 전체 399G, 여유 180G,
  사용률 53%였다(파일시스템 관리 영역·예약 공간 때문에 가상디스크 크기와 다름).
  재기동 후 TradingView Deployment와 PostgreSQL StatefulSet은 각각 Ready 1/1이었다.
- 용량 부족 시 자동 이관은 별도 `tradingview-pressure-archive.timer`로 관리한다.
  조건·보호 범위·실제 공간 회수 제한·장애 시 정지는 [용량 기반 이관](PRESSURE_ARCHIVE.md)을 따른다.
- 주요 수집 테이블은 일반 테이블이므로 검증된 이관 후 제한 시간 내 압축과 실제 공간
  증가를 확인한다. 실패하면 추가 정리를 멈추며, 이관된 데이터는 D에 보존한다.

## WSL 150GiB 증설 실행기

`scripts/expand_wsl_disk.ps1`과 `scripts/finish_wsl_resize.sh`를 Windows의
`%LOCALAPPDATA%\WSLMaintenance`에 함께 설치한다. 관리자 PowerShell에서 전자를
`-Execute`로 실행하면 등록된 `Ubuntu-24.04`의 동적 VHDX만 256→406GiB로 확장한다.
정상 systemd 종료가 완료되고 VHD가 분리된 경우에만 확장하며, 다른 크기의 디스크나
다른 실행 중 배포판이 있으면 중단한다. 축소·포맷·자동 파일시스템 복구는 하지 않는다.

Windows에 실행기를 두므로 WSL 종료 후에도 작업을 이어갈 수 있다. 재기동 후 ext4
확장과 기존 Docker·containerd·k3d-start·port-forward 호스트 서비스 활성 상태를
확인한다. 이는 개별 투자 주문·수집 데이터 정상성 검증을 대신하지 않는다.
실행 상태는 같은 폴더의 `resize-status.json`, 상세 출력은 `resize-transcript.log`에 남긴다.
`complete`가 아닌 상태를 증설·서비스 검증 완료로 취급하지 않는다.

WSL에서 시작할 때는 `scripts/launch_wsl_expansion.ps1`도 같은 폴더에 설치하고
Windows PowerShell로 이 런처를 실행한다. Windows의 별도 프로세스에서 UAC를 요청해
호출한 WSL 세션 종료와 분리하고, 한글 프로필 경로는 인코딩된 명령으로 전달한다.
`launch-status.json`은 승인·프로세스 실행 상태이고 실제 증설 결과는 아니다.
실행기 진입 전 오류는 `bootstrap-error.log`에 기록한다.

## 인증과 접근

현재 Windows 계정 `유한승@SERVER`만 SSPI로 인증한다. 비밀번호나 `trust` 인증은
사용하지 않는다. NTFS ACL은 해당 사용자와 SYSTEM으로 제한한다.
접속 정보는 Secret Manager의 `TRADING_ARCHIVE_POSTGRES_CONNECTION`에 등록한다.
한글 계정의 SSPI 매핑은 UTF-8 정본을 보존하고 Windows 시스템 코드페이지로 변환한다.

- 관리 역할: `archive_owner` (로컬 Windows 계정 인증, 백업 복원·병합용).
- 조회 역할: `archive_reader` (SELECT 권한만, 기본 read-only, 쿼리 제한 60초).
- 외부 수신·방화벽 개방은 하지 않는다.
- 원본의 PGDATA 디렉터리를 Windows로 복사하지 않고 SQL 덤프를 복원한다.

## 조회

Windows에서 `D:\PostgreSQL\query-archive.cmd`를 실행한다.
pgAdmin을 사용하는 경우 호스트 `127.0.0.1`, 포트 `55432`, 데이터베이스
`tradingview_archive`, 사용자 `archive_reader`, SSPI 인증을 사용한다.

```sql
SELECT * FROM archive_meta.runs ORDER BY completed_at DESC LIMIT 5;
SELECT * FROM archive_meta.candle_coverage WHERE collector = 'stocks';
SELECT symbol, collected_at, payload::jsonb -> 'fundamentals' AS fundamentals
FROM public.market_data WHERE collector = 'stocks'
ORDER BY collected_at DESC LIMIT 10;
```

## 수동 전체 백업 도구

기존 `scripts/archive_to_windows.py --run`은 수동 전체 백업 도구로 남겨둔다.
이 도구는 용량 부족 시 오래된 데이터만 이관하는 기능이 아니며 원본을 삭제하지 않는다.
배포용 timer 파일에는 기존 04:20 KST 일정이 남아 있지만 현재 예약은 비활성화되어 있다.
아래 절차는 수동으로 전체 백업을 실행했을 때만 적용된다.

1. D드라이브 마운트와 `max(50GiB, 원본 DB 크기×3+10GiB)`의 여유 공간을 확인한다.
2. 원본 읽기 전용 repeatable-read snapshot을 유지하며, 같은 snapshot으로
   수집 테이블 행 수를 세고 전체 DB의 custom-format 덤프를 D에 직접 저장한다.
3. SHA256을 기록하고 별도 임시 DB에 복원해 `jobs`, `market_data`, `market_candles`
   행 수를 원본 snapshot과 대조한다. 덤프에는 나머지 원본 테이블도 포함된다.
4. 최초에는 복원 DB를 보관 DB로 게시한다. 이후 수집 테이블 3개는 키 `id`로
   트랜잭션 안에서 병합하고 현재 원본과 겹치는 행 전체를 비교한다.
   원본에서 사라진 과거 행은 보관 DB에서 삭제하지 않는다.
5. 스키마 변경·키 충돌·행 불일치 시 병합을 롤백하고 실패로 남긴다.
6. 검증 후 이번 임시 복원 DB만 삭제한다. 최초 원본 덤프는 그대로 보존하고,
   이후에는 과거 누적 행을 포함하는 보관 DB 전체를 다시 덤프한다. 원본 전체
   `.source.dump`도 보존하므로 누적 병합 대상이 아닌 테이블의 최신 상태도 복구할 수 있다.
7. 결과를 `archive_meta.runs`와 덤프 옆 JSON manifest에 기록한다.
   원본·누적 덤프를 최근 7회분(최소 2회분) 유지하며(데이터 보관일 기준이 아님),
   복원 시점의 원본 행 수 대조와
   누적 덤프의 형식 검증을 구분해서 기록한다. 실패 시 `.pending.json`에 원본
   snapshot 정보와 해시가 남아 덤프를 재수집하지 않고 수동 복구할 수 있다.

보관 DB 데이터 자체는 누적 보존한다. 미검증 덤프·복원 실패 DB는 자동 삭제하지
않으며, 용량 부족 시 작업이 실패하고 원본에는 영향을 주지 않는다.
보관 DB와 덤프가 같은 D드라이브에 있으므로 D 하드웨어 고장에 대한 독립 복사본은
아니다. 별도 디스크·외부 백업은 추가로 마련해야 한다.

## 확인·중지

```bash
systemctl --user status tradingview-archive.timer tradingview-archive.service
journalctl --user -u tradingview-archive.service -n 30
```

타이머는 현재 비활성화되어 있으며, 이 상태에서도 D 보관 DB 조회는 가능하다.
Windows 시작프로그램의 `TradingViewArchive.vbs`는 조회 DB 자동 시작용이므로 유지한다.
보관 DB 전체 기동까지 중단하려는 경우에만 별도로 시작프로그램 해제를 검토한다.
보관 DB 파일·덤프를 지우거나 운영 DB에 역복원하지 않는다.
Windows 보관 서버와 수동 전체 백업은 운영 서비스 이미지나 원본 데이터를 변경하지 않는다.
별도의 용량 기반 이관 기능에서만 검증된 원본 정리와 제한된 압축을 수행한다.
