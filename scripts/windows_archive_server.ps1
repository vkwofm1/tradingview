param([ValidateSet('Init','Start','Status','Query')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$root = 'D:\PostgreSQL'
$bin = "$root\16.15\pgsql\bin"
$data = "$root\data"
$env:PGCLIENTENCODING = 'UTF8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Check-Exit([string]$operation) {
    if ($LASTEXITCODE -ne 0) { throw "$operation failed ($LASTEXITCODE)" }
}

if (!(Test-Path -LiteralPath "$bin\postgres.exe")) { throw 'Official PostgreSQL binaries missing' }
if ($Action -eq 'Init') {
    if (Test-Path -LiteralPath $data) { throw 'Refusing to initialize an existing data directory' }
    # 현재 Windows 계정과 SYSTEM만 보관 파일에 접근한다. 기존 폴더는 재초기화하지 않는다.
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $root /inheritance:r /grant:r "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    Check-Exit 'archive ACL'
    & "$bin\initdb.exe" -D $data -U archive_owner --encoding=UTF8 --locale=C --auth-host=sspi --auth-local=reject
    Check-Exit 'initdb'
}
if ($Action -eq 'Status') {
    & "$bin\pg_ctl.exe" -D $data status
    exit $LASTEXITCODE
}
if ($Action -eq 'Query') {
    & "$bin\psql.exe" -X -w -h 127.0.0.1 -p 55432 -U archive_reader -d tradingview_archive
    exit $LASTEXITCODE
}
# Windows SSPI의 SAM 이름은 시스템 ANSI 코드페이지를 사용한다.
# UTF-8 정본은 보존하고 정확한 동일 계정 매핑만 해당 코드페이지로 변환한다.
$mappingText = [IO.File]::ReadAllText("$root\pg_ident.utf8.conf", [Text.Encoding]::UTF8)
$mappingBytes = [Text.Encoding]::Default.GetBytes($mappingText)
$mappingChanged = !(Test-Path "$root\pg_ident.conf") -or
    [Convert]::ToBase64String([IO.File]::ReadAllBytes("$root\pg_ident.conf")) -ne [Convert]::ToBase64String($mappingBytes)
if ($mappingChanged) { [IO.File]::WriteAllBytes("$root\pg_ident.conf", $mappingBytes) }
& "$bin\pg_ctl.exe" -D $data status *> $null
if ($LASTEXITCODE -eq 3) {
    & "$bin\pg_ctl.exe" -D $data -l "$root\server-start.log" -o '-c config_file=D:/PostgreSQL/postgresql.conf' -w -t 60 start
    Check-Exit 'postgres start'
} elseif ($LASTEXITCODE -ne 0) { throw 'Invalid archive server state' }
elseif ($mappingChanged) {
    & "$bin\pg_ctl.exe" -D $data reload
    Check-Exit 'identity mapping reload'
}
& "$bin\psql.exe" -X -w -h 127.0.0.1 -p 55432 -U archive_owner -d postgres -v ON_ERROR_STOP=1 -c 'SELECT version();'
Check-Exit 'SSPI connection'
