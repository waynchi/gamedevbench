$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$variantRoot = Join-Path $repo ".benchmark-config"

$piSource = Join-Path $HOME ".pi\agent"
$piTarget = Join-Path $variantRoot "pi-stock"
New-Item -ItemType Directory -Force -Path $piTarget | Out-Null
foreach ($name in @("auth.json", "models.json", "settings.json")) {
    $source = Join-Path $piSource $name
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing Pi configuration file: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $piTarget $name) -Force
}
foreach ($name in @("SYSTEM.md", "system.md")) {
    $path = Join-Path $piTarget $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

$omoXdg = Join-Path $variantRoot "omo"
$omoConfig = Join-Path $omoXdg "opencode"
New-Item -ItemType Directory -Force -Path $omoConfig | Out-Null

$oldXdg = $env:XDG_CONFIG_HOME
$oldConfigDir = $env:OPENCODE_CONFIG_DIR
try {
    $env:XDG_CONFIG_HOME = $omoXdg
    $env:OPENCODE_CONFIG_DIR = $omoConfig
    & npx -y oh-my-openagent@4.10.0 install --no-tui --platform=opencode --claude=no --openai=no --gemini=no --copilot=no --opencode-zen=no --zai-coding-plan=no --opencode-go=yes --kimi-for-coding=no --vercel-ai-gateway=no --skip-auth
    if ($LASTEXITCODE -ne 0) {
        throw "OMO installer failed with exit code $LASTEXITCODE"
    }
} finally {
    $env:XDG_CONFIG_HOME = $oldXdg
    $env:OPENCODE_CONFIG_DIR = $oldConfigDir
}

$template = Join-Path $repo "benchmark_configs\omo_deepseek_v4_flash.jsonc"
$opencodeTemplate = Join-Path $repo "benchmark_configs\omo_opencode.json"
foreach ($name in @(
    "oh-my-openagent.json",
    "oh-my-opencode.json",
    "oh-my-opencode.jsonc"
)) {
    $path = Join-Path $omoConfig $name
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}
Copy-Item -LiteralPath $template -Destination (Join-Path $omoConfig "oh-my-openagent.jsonc") -Force
Copy-Item -LiteralPath $opencodeTemplate -Destination (Join-Path $omoConfig "opencode.json") -Force
Set-Content -LiteralPath (Join-Path $omoConfig "benchmark-omo-version.txt") -Value "4.10.0" -Encoding utf8

Write-Host "Prepared Pi stock config: $piTarget"
Write-Host "Prepared isolated OMO config: $omoConfig"
