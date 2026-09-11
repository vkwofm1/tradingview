# 용량 부족 시 D PostgreSQL 자동 이관

## 실행 조건

`tradingview-pressure-archive.timer`가 매일 07:00 KST(최대 30초 분산)에 WSL 가용 공간과
원본 DB의 테이블 크기 메타데이터만 확인한다. WSL이 꺼져 점검을 놓쳤으면 다음 시작 시
한 번 보충한다. 공간이 충분하면 D에 연결하거나 행을 읽고 옮기지 않는다.
기존 매일 전체 백업 타이머는 비활성 상태를 유지한다. 나이만으로 이관하지 않는다.

- 기본 진입: 여유 60GiB 이하 또는 사용률 85% 이상.
- 기본 회복: 여유 80GiB 이상 **그리고** 사용률 80% 이하.
- 큰 테이블의 압축 임시 공간을 확보하기 위해 기준을 상향할 수 있다.
  `테이블 예산 = 현재 크기×1.1 + 1GiB`, `압축 예약 = 최대 테이블 예산×2 + 20GiB`.
  진입 여유 기준은 `max(60GiB, 압축 예약+20GiB)`, 회복 기준은
  `max(80GiB, 압축 예약+40GiB)`다. 실제 기준은 상태 JSON에 바이트 단위로 표시한다.
- WSL과 C 각각에 압축 예약 공간, D에 최소 50GiB를 남긴다.
  갑작스러운 디스크 사용 증가로 이 예약을 확보할 수 없으면 원본 정리를 시작하지 않는다.
- D가 분리됐거나 조회 DB가 꺼져 있으면 이관하지 않는다. WSL에 대신 저장하지 않는다.

## 보호하는 데이터

- 대상은 TradingView `market_candles`, `market_data`뿐이다. 계좌·주문·다른 DB는 건드리지 않는다.
- 시간 및 최종 수집 시각이 최근 90일 이내인 행은 보호한다.
- 봉은 `(collector, symbol, interval)`별 최근 최소 2,000개를 남긴다.
- 봉 이관은 `1m/3m/5m/15m/30m/60m/1h`만 허용한다. 일봉 및 다른 주기는 모두 보존한다.
- 스냅샷은 `(collector, symbol)`별 최근 최소 20개를 남긴다.
- 실행 중이거나 완료 시각 없는 작업의 행은 제외한다. 원본 `jobs`는 삭제하지 않는다.

90일은 **삭제 주기가 아니라 최소 보호 범위**다. 그보다 오래된 백테스트·연구 자료는
D 보관 DB에서 조회한다. 운영 원본만 직접 조회하는 소비자는 이 최소 보호 범위를 전제로
해야 하며, 긴 과거 이력 조회는 D로 분리한다. 주문 권한이나 전략 설정은 바꾸지 않는다.

## 이관과 검증

1. 지정된 `k3d-dev-cluster/default`의 원본 DB·스키마 식별자와 WSL 저장 장치를 확인한다.
2. 각 테이블에서 오래된 행부터 최대 2,000개와 연결된 작업 정보를 읽는다.
3. D의 `backups/pressure/pressure_<uuid>.json.gz`에 원문을 기록·fsync하고 SHA256을 재검증한다.
4. D PostgreSQL의 `archive_meta.pressure_batches`와 인덱스가 있는 `archive_cold` 테이블에
   같은 트랜잭션으로 저장한다. 별도 `archive_reader` 접속으로 커밋된 원문과 형식화된 행을
   모두 비교한다. `fsync`, `synchronous_commit`, D의 실제 데이터 경로도 검증한다.
5. 중단 복구용 상태를 먼저 기록한 뒤 **전체 행 내용이 여전히 같은 원본만** 삭제한다.
   수집기가 수정했거나 보호 대상이 된 행은 삭제하지 않고 후속 정리를 중단한다.
6. 한 번에 테이블당 최대 25묶음, 총 최대 100,000행으로 제한한다.
   해당 테이블만 `VACUUM FULL`로 압축해 테이블 파일 감소와 WSL 가용 공간 증가를 모두 확인한다.
   실제 공간이 증가했지만 목표에 못 미치면 다음 점검에서 이어간다.

일반 DELETE/VACUUM은 파일시스템 공간 반환을 보장하지 않는다.
압축은 잠금 획득 1초, SQL 실행 30초 제한으로 실행한다. **압축 중 해당 테이블의 조회·수집이
대기할 수 있다.** 큰 테이블에서 제한 시간을 넘으면 자동으로 끝없이 압축하지 않고,
이미 D에 검증된 데이터는 보존한 채 추가 원본 정리를 정지한다. 이 경우 별도 유지보수
시간에 원인을 해결해야 한다. 자동 파티션 변환·무제한 압축·WSL 종료는 하지 않는다.

근거: [PostgreSQL 공간 회수](https://www.postgresql.org/docs/16/routine-vacuuming.html).
WSL 내부 여유 공간을 확보하는 기능이며 Windows의 VHDX 파일 자체를 자동 축소하지는 않는다.

## 조회

기존 `D:\PostgreSQL\query-archive.cmd`와 같은 SSPI 접속을 사용한다.

```sql
SELECT collector,symbol,interval,candle_time,payload
FROM archive_meta.migrated_market_candles
WHERE collector='stocks' AND symbol='AAPL'
ORDER BY candle_time DESC LIMIT 100;

SELECT * FROM archive_meta.migrated_market_data
WHERE collector='stocks' AND symbol='AAPL'
ORDER BY collected_at DESC LIMIT 20;

SELECT batch_id,verified_at,manifest
FROM archive_meta.pressure_batches ORDER BY verified_at DESC LIMIT 10;
```

기존 전체 백업의 `public` 테이블과 새 용량 기반 이관 자료를 구분한다. 재수집·수동 재시도
이력은 배치별로 보존하므로 조회 시 필요한 배치/수집 시점을 선택한다. 연결 작업 정보는
`archive_meta.migrated_jobs`에 있다. 복구 파일과 보관 DB는 자동 만료·삭제하지 않는다.
두 사본 모두 D에 있으므로 D 하드웨어 고장에 대비한 별도 장치의 백업을 대신하지 않는다.

## 확인·장애·중지

```bash
systemctl --user status tradingview-pressure-archive.timer tradingview-pressure-archive.service
journalctl --user -u tradingview-pressure-archive.service -n 30
```

상태: `~/.local/state/tradingview-archive/pressure-status.json`.
`idle`은 공간 충분, `partial`은 검증된 공간 회수 후 이어가기, `recovered`는 목표 회복이다.
삭제 전 연결 실패 등은 `retry_wait`로 다음 점검에 재시도한다. 삭제 중단·결과 불명·압축 실패·
보호 범위 밖 이관 대상 고갈은 `requires_attention=true`로 표시하고 추가 원본 정리를 멈춘다.
실패는 systemd 실패 상태·journal·JSON에 남기며 별도 메신저 전송은 하지 않는다.

운영자가 해당 배치의 D 저장 내용, 원본 잔존 행, 여유 공간을 확인하고 원인을 해결한 후에만
`python3 scripts/pressure_archive.py --run --retry`로 재개한다. 오류를 지우기 위해 상태 파일을
무조건 삭제하거나 보호 일수·최소 행 수를 낮추지 않는다.

기본 실행(인자 없음)은 읽기 전용 용량 점검이며 `--run`에서만 이관한다.
중지: `systemctl --user disable --now tradingview-pressure-archive.timer`.
진행 중 서비스는 별도로 확인한다. 이미 검증한 D 데이터나 기존 전체 백업을 지우지 않는다.
