param(
    [string]$TaskName = "ATS-036D-PAPER-Campaign",
    [switch]$PersistCurrentDatabaseUrl
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_ats036d_tick.ps1"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Runner)) { throw "ATS-036D runner script is missing." }
if (-not (Test-Path $Python)) { throw "ATS repository .venv is missing." }

$UserDatabaseUrl = [Environment]::GetEnvironmentVariable("ATS_DATABASE_URL", "User")
if ([string]::IsNullOrWhiteSpace($UserDatabaseUrl) -and $PersistCurrentDatabaseUrl) {
    if ([string]::IsNullOrWhiteSpace($env:ATS_DATABASE_URL)) {
        throw "ATS_DATABASE_URL is not available in the current process."
    }
    [Environment]::SetEnvironmentVariable("ATS_DATABASE_URL", $env:ATS_DATABASE_URL, "User")
    $UserDatabaseUrl = [Environment]::GetEnvironmentVariable("ATS_DATABASE_URL", "User")
}
if ([string]::IsNullOrWhiteSpace($UserDatabaseUrl)) {
    throw "ATS_DATABASE_URL must exist as a User environment variable before scheduler installation. The value is never printed."
}

$CampaignStartUtc = [DateTimeOffset]::Parse("2026-11-12T14:45:00Z")
$CampaignEndUtc = [DateTimeOffset]::Parse("2027-02-04T14:45:00Z")
$LocalStart = $CampaignStartUtc.LocalDateTime
$LocalEnd = $CampaignEndUtc.LocalDateTime
if ((Get-Date) -gt $LocalStart.AddMinutes(5)) {
    throw "Day 1 activation window has already passed; do not silently shift the persisted campaign."
}

$PowerShell = (Get-Command powershell.exe).Source
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Once -At $LocalStart `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration ($LocalEnd - $LocalStart)
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "ATS-036D PAPER-only campaign tick. No Webull order submission. LIVE disabled." `
    -Force | Out-Null

$Task = Get-ScheduledTask -TaskName $TaskName
[pscustomobject]@{
    TaskName = $Task.TaskName
    State = $Task.State
    CampaignId = "e00289e2-4e14-4819-a493-de93263c79ad"
    StartUtc = $CampaignStartUtc.ToString("o")
    StartLocal = $LocalStart.ToString("o")
    IntervalMinutes = 5
    DatabaseConfiguration = "USER_ENVIRONMENT"
    CredentialValuesPrinted = $false
    BrokerOrderSubmission = "DISABLED"
    LiveExecution = "DISABLED"
} | Format-List
