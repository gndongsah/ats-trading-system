param(
    [string]$CampaignId = "e00289e2-4e14-4819-a493-de93263c79ad",
    [string]$PostgresContainer = "ats036b-postgres"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs\ats036d"
$ArtifactDir = Join-Path $RepoRoot "artifacts\forward-test"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null

function Write-SanitizedLog {
    param([string]$Text)
    $stamp = (Get-Date).ToUniversalTime().ToString("o")
    $line = "{0} {1}" -f $stamp, $Text
    Add-Content -Path (Join-Path $LogDir ("ats036d-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))) -Value $line
}

if (-not (Test-Path $Python)) {
    Write-SanitizedLog "HOLD python_venv_missing"
    exit 2
}

$DatabaseUrl = [Environment]::GetEnvironmentVariable("ATS_DATABASE_URL", "User")
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $DatabaseUrl = $env:ATS_DATABASE_URL
}
if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    Write-SanitizedLog "HOLD ATS_DATABASE_URL_missing"
    exit 2
}
$env:ATS_DATABASE_URL = $DatabaseUrl
$env:ATS_CAMPAIGN_ARTIFACT_DIR = $ArtifactDir

try {
    $DockerStatus = (& docker desktop status 2>$null | Out-String)
    if ($LASTEXITCODE -ne 0 -or $DockerStatus -notmatch "running") {
        & docker desktop start *> $null
        $deadline = (Get-Date).AddSeconds(60)
        do {
            Start-Sleep -Seconds 2
            $DockerStatus = (& docker desktop status 2>$null | Out-String)
        } until (($LASTEXITCODE -eq 0 -and $DockerStatus -match "running") -or (Get-Date) -ge $deadline)
    }

    $ContainerExists = (& docker ps -a --filter "name=^/$PostgresContainer$" --format "{{.Names}}" 2>$null | Out-String).Trim()
    if ($ContainerExists -ne $PostgresContainer) {
        Write-SanitizedLog "HOLD postgres_container_missing"
        exit 2
    }
    $DataMount = (& docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Type}}:{{.Name}}{{end}}{{end}}' $PostgresContainer 2>$null | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($DataMount)) {
        Write-SanitizedLog "HOLD postgres_data_volume_missing"
        exit 2
    }
    & docker update --restart unless-stopped $PostgresContainer *> $null
    $Running = (& docker ps --filter "name=^/$PostgresContainer$" --format "{{.Names}}" 2>$null | Out-String).Trim()
    if ($Running -ne $PostgresContainer) {
        & docker start $PostgresContainer *> $null
    }
} catch {
    Write-SanitizedLog ("HOLD docker_runtime_error type={0}" -f $_.Exception.GetType().Name)
    exit 2
}

Push-Location $RepoRoot
try {
    $Output = & $Python -m src.forward_test_campaign_tick `
        --campaign-id $CampaignId `
        --artifact-dir $ArtifactDir 2>&1
    $ExitCode = $LASTEXITCODE
    foreach ($Line in $Output) {
        Write-SanitizedLog ([string]$Line)
    }
    exit $ExitCode
} catch {
    Write-SanitizedLog ("HOLD tick_error type={0}" -f $_.Exception.GetType().Name)
    exit 2
} finally {
    Pop-Location
}
