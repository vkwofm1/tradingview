param([switch]$Worker)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $env:LOCALAPPDATA 'WSLMaintenance'
$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

if (!$Worker) {
    # The Windows provider owns the launcher, independent of the WSL command/session.
    $command = '"' + $powerShell + '" -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Worker'
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $command; CurrentDirectory = $runtime
    }
    if ($result.ReturnValue -ne 0) { throw "Detached Windows launch failed: $($result.ReturnValue)" }
    [PSCustomObject]@{ state = 'windows_launcher_started'; pid = $result.ProcessId } | ConvertTo-Json
    exit
}

$launchStatus = Join-Path $runtime 'launch-status.json'
function Save-Launch([string]$state, [string]$detail) {
    [PSCustomObject]@{ state = $state; detail = $detail; updated_at = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $launchStatus -Encoding UTF8
}
try {
    Save-Launch 'awaiting_administrator_approval' 'No shutdown until elevation and preflight succeed.'
    # Encoding avoids a Unicode profile path being reparsed by native command-line quoting.
    $bootstrap = @'
$ErrorActionPreference = 'Stop'
try {
    & (Join-Path $env:LOCALAPPDATA 'WSLMaintenance\expand_wsl_disk.ps1') -Execute
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'WSLMaintenance\bootstrap-error.log') -Encoding UTF8
    exit 1
}
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
    $process = Start-Process -FilePath $powerShell -Verb RunAs -WorkingDirectory $runtime -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
    ) -PassThru
    Save-Launch 'elevated_runner_started' "Windows PID $($process.Id); see resize-status.json."
    $process.WaitForExit()
    Save-Launch 'elevated_runner_exited' 'See resize-status.json and resize-transcript.log for actual outcome.'
} catch {
    Save-Launch 'launch_failed' $_.Exception.Message
    exit 1
}
