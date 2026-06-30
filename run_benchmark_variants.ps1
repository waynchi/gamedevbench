$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

if (-not $env:GODOT_EXEC_PATH) {
    $env:GODOT_EXEC_PATH = "godot"
}
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $repo "results\benchmark_variant_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Run-Agent {
    param(
        [string]$Name,
        [string[]]$Arguments
    )

    $log = Join-Path $logDir "$Name.log"
    "[$(Get-Date -Format o)] Starting $Name" | Tee-Object -FilePath $log -Append
    & python -m gamedevbench.src.benchmark_runner @Arguments 2>&1 |
        Tee-Object -FilePath $log -Append
    $exitCode = $LASTEXITCODE
    "[$(Get-Date -Format o)] Finished $Name with exit code $exitCode" |
        Tee-Object -FilePath $log -Append
    if ($exitCode -ne 0) {
        throw "$Name benchmark failed with exit code $exitCode"
    }
}

Run-Agent "pi-stock" @(
    "--agent", "pi-stock",
    "--model", "deepseek/deepseek-v4-flash",
    "run", "--task-list", "benchmark_24.yaml"
)

Run-Agent "omo" @(
    "--agent", "omo",
    "--model", "opencode-go/deepseek-v4-flash",
    "run", "--task-list", "benchmark_24.yaml"
)
