param([switch]$Execute)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (!$Execute) { throw 'Explicit -Execute is required; this shuts down WSL.' }

# Run from a native Windows directory so shutdown cannot interrupt this runner.
$distro = 'Ubuntu-24.04'
$originalBytes = 256GB
$targetBytes = 406GB
$runtime = Join-Path $env:LOCALAPPDATA 'WSLMaintenance'
$statusPath = Join-Path $runtime 'resize-status.json'
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
$helper = Join-Path $runtime 'finish_wsl_resize.sh'
$needsRestart = $false
$status = [ordered]@{
    distribution = $distro; target_bytes = $targetBytes
    state = 'preflight'; started_at = [DateTime]::UtcNow.ToString('o')
    vhd_resized = $false; filesystem_verified = $false; services_verified = $false
}
function Save-Status([string]$state) {
    $status.state = $state
    $status.updated_at = [DateTime]::UtcNow.ToString('o')
    $status | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
function Check-Exit([string]$operation) {
    if ($LASTEXITCODE -ne 0) { throw "$operation failed ($LASTEXITCODE)" }
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Start-Transcript -LiteralPath (Join-Path $runtime 'resize-transcript.log') -Append | Out-Null
try {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows administrator approval is required. No shutdown performed.'
    }
    $entries = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
        ForEach-Object { Get-ItemProperty $_.PSPath } |
        Where-Object DistributionName -eq $distro)
    if ($entries.Count -ne 1) { throw 'Expected distribution not found for this Windows account.' }
    $vhdPath = Join-Path $entries[0].BasePath 'ext4.vhdx'
    if (!(Test-Path -LiteralPath $helper) -or !(Test-Path -LiteralPath $vhdPath)) {
        throw 'Required helper or VHD missing.'
    }
    $vhd = Get-VHD -Path $vhdPath
    if ($vhd.VhdType -ne 'Dynamic' -or $vhd.VhdFormat -ne 'VHDX') {
        throw 'Unexpected disk format; refusing resize.'
    }
    if ($vhd.Size -ne $originalBytes -and $vhd.Size -ne $targetBytes) {
        throw 'Expected 256GiB or already-expanded 406GiB; refusing any other resize.'
    }
    $status.original_bytes = $vhd.Size
    $status.vhd_path = $vhdPath
    $drive = [IO.Path]::GetPathRoot($vhdPath).Substring(0,1)
    if ((Get-Volume -DriveLetter $drive).SizeRemaining -lt (($targetBytes - $vhd.Size) + 20GB)) {
        throw 'Insufficient host free space for requested growth and safety reserve.'
    }
    $running = @(& $wsl --list --running --quiet | ForEach-Object { ($_ -replace "`0", '').Trim() } |
        Where-Object { $_ })
    Check-Exit 'list running WSL distributions'
    if (@($running | Where-Object { $_ -ne $distro }).Count -gt 0) {
        throw 'Other WSL distributions are running; refusing to stop unapproved distributions.'
    }
    Save-Status 'shutting_down'
    $needsRestart = $true
    if ($running -contains $distro) {
        & $wsl -d $distro -u root --exec /usr/bin/systemctl poweroff --no-block
        Check-Exit 'graceful systemd shutdown request'
        $deadline = [DateTime]::UtcNow.AddMinutes(3)
        do {
            Start-Sleep -Seconds 2
            $running = @(& $wsl --list --running --quiet | ForEach-Object { ($_ -replace "`0", '').Trim() })
            Check-Exit 'wait for graceful WSL shutdown'
            if ([DateTime]::UtcNow -gt $deadline) { throw 'Graceful shutdown timed out; no forced termination or resize.' }
        } while ($running -contains $distro)
    }
    & $wsl --shutdown
    Check-Exit 'WSL VM shutdown'
    $offlineVhd = Get-VHD -Path $vhdPath
    if ($offlineVhd.Attached) { throw 'VHD remains attached; refusing resize.' }
    Save-Status 'resizing_vhd'
    if ($offlineVhd.Size -eq $originalBytes) {
        Resize-VHD -Path $vhdPath -SizeBytes $targetBytes
    } elseif ($offlineVhd.Size -ne $targetBytes) {
        throw 'VHD size changed unexpectedly; refusing resize.'
    }
    if ((Get-VHD -Path $vhdPath).Size -ne $targetBytes) { throw 'VHD size verification failed.' }
    $status.vhd_resized = $true
    Save-Status 'restarting_and_expanding_filesystem'
    $helperLinux = '/mnt/' + $helper.Substring(0,1).ToLowerInvariant() + $helper.Substring(2).Replace('\','/')
    & $wsl -d $distro -u root --exec /bin/bash $helperLinux
    Check-Exit 'filesystem expansion and service verification'
    $status.filesystem_verified = $true
    $status.services_verified = $true
    $needsRestart = $false
    Save-Status 'complete'
    Write-Host 'SUCCESS: WSL is 406GiB; filesystem and host services verified.'
} catch {
    $status.error = $_.Exception.Message
    Save-Status 'failed'
    Write-Host ('FAILED: ' + $_.Exception.Message)
    if ($needsRestart) {
        # Recovery starts the same distro. Never shrink, format, delete or repair automatically.
        & $wsl -d $distro -u root --exec /bin/true
        $status.restart_exit_code = $LASTEXITCODE
        Save-Status 'failed'
    }
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
