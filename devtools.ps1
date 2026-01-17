# =============================================================================
# DevTools - Swiss Army Knife for Git-based Development
# Runtime Container für Git-Operationen und Entwicklungstools
# =============================================================================

param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"

# Configuration
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ImageName = "bauer-devtools"
$ContainerName = "devtools-runtime"
$DataDir = Join-Path $ScriptDir ".data"
$ToolsConfigPath = Join-Path $ScriptDir "tools.json"

# Load tools configuration
function Get-ToolsConfig {
    if (-not (Test-Path $ToolsConfigPath)) {
        Write-Host "[ERROR] tools.json not found at $ToolsConfigPath" -ForegroundColor Red
        exit 1
    }
    return Get-Content $ToolsConfigPath -Raw | ConvertFrom-Json
}

# Find tool by command or alias
function Find-Tool {
    param([string]$CommandName)

    $config = Get-ToolsConfig
    foreach ($category in $config.categories) {
        foreach ($tool in $category.tools) {
            if ($tool.command -eq $CommandName) {
                return $tool
            }
            if ($tool.aliases) {
                foreach ($alias in $tool.aliases) {
                    if ($alias -eq $CommandName) {
                        return $tool
                    }
                }
            }
        }
    }
    return $null
}

# Hilfe anzeigen
function Show-Help {
    $config = Get-ToolsConfig

    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Blue
    Write-Host "              DevTools - Developer Swiss Army Knife                   " -ForegroundColor Blue
    Write-Host "======================================================================" -ForegroundColor Blue
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor White
    Write-Host "  .\devtools.ps1 <command> [options]"
    Write-Host ""
    Write-Host "Commands:" -ForegroundColor White
    Write-Host ""

    foreach ($category in $config.categories) {
        if ($category.id -eq "general") { continue }

        Write-Host "  $($category.name):" -ForegroundColor Cyan
        foreach ($tool in $category.tools) {
            $cmdDisplay = $tool.command
            # Add aliases in parentheses if available
            if ($tool.aliases -and $tool.aliases.Count -gt 0) {
                $aliasStr = $tool.aliases -join ", "
                $cmdDisplay = "$($tool.command) ($aliasStr)"
            }
            $argsDisplay = if ($tool.args) { " $($tool.args)" } else { "" }
            $fullCmd = "$cmdDisplay$argsDisplay"
            $padding = 50 - $fullCmd.Length
            if ($padding -lt 1) { $padding = 1 }
            $spaces = " " * $padding
            Write-Host "    $fullCmd$spaces$($tool.description)"
        }
        Write-Host ""
    }

    Write-Host "  General:" -ForegroundColor Cyan
    Write-Host "    help                    Show this help"
    Write-Host "    version                 Show version info"
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor White
    Write-Host "  .\devtools.ps1 shell                          # Shell im aktuellen Verzeichnis"
    Write-Host "  .\devtools.ps1 shell C:\Projects\MyApp        # Shell in anderem Projekt"
    Write-Host "  .\devtools.ps1 stats                          # Repository-Statistiken"
    Write-Host "  .\devtools.ps1 run git-cleanup.sh --dry-run   # Script ausfuehren"
    Write-Host ""
    Write-Host "Note:" -ForegroundColor White
    Write-Host "  Fuer Dozzle (Container Monitor) siehe: services\dozzle\"
    Write-Host ""
}

# Docker prüfen
function Test-Docker {
    $ErrorActionPreference = "SilentlyContinue"
    docker info *>$null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
        exit 1
    }
    return $true
}

# Image bauen falls nötig
function Ensure-Image {
    docker image inspect $ImageName *>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INFO] Building DevTools container..." -ForegroundColor Cyan
        Build-Image
    }
}

# Image bauen
function Build-Image {
    Write-Host "[INFO] Building DevTools image..." -ForegroundColor Cyan
    docker build -t $ImageName "$ScriptDir\services\devtools"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to build image" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Image built successfully" -ForegroundColor Green
}

# Container starten (interaktiv)
function Start-Shell {
    param([string]$ProjectPath = (Get-Location).Path)

    # Absoluten Pfad sicherstellen
    $ProjectPath = (Resolve-Path $ProjectPath -ErrorAction SilentlyContinue).Path
    if (-not $ProjectPath) {
        $ProjectPath = (Get-Location).Path
    }

    if (-not (Test-Path $ProjectPath -PathType Container)) {
        Write-Host "[ERROR] Directory not found: $ProjectPath" -ForegroundColor Red
        exit 1
    }

    Test-Docker | Out-Null
    Ensure-Image

    Write-Host "[INFO] Starting DevTools shell..." -ForegroundColor Cyan
    Write-Host "[INFO] Mounting: $ProjectPath" -ForegroundColor Cyan

    # Git-Konfiguration vom Host übernehmen
    $gitName = git config --global user.name 2>$null
    $gitEmail = git config --global user.email 2>$null

    $projectName = Split-Path -Leaf $ProjectPath

    # Ensure data directory exists
    if (-not (Test-Path $DataDir)) {
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    }

    docker run -it --rm `
        --name $ContainerName `
        -v "${ProjectPath}:/workspace" `
        -v "${DataDir}:/data" `
        -v /var/run/docker.sock:/var/run/docker.sock `
        -e "GIT_USER_NAME=$gitName" `
        -e "GIT_USER_EMAIL=$gitEmail" `
        -e "PROJECT_NAME=$projectName" `
        -w /workspace `
        $ImageName
}

# Script im Container ausführen
function Invoke-Script {
    param(
        [string]$Script,
        [string[]]$ScriptArgs,
        [string]$ProjectPath = (Get-Location).Path
    )

    Test-Docker | Out-Null
    Ensure-Image

    $allArgs = if ($ScriptArgs) { $ScriptArgs -join ' ' } else { '' }
    Write-Host "[INFO] Running: $Script $allArgs" -ForegroundColor Cyan

    # Ensure data directory exists
    if (-not (Test-Path $DataDir)) {
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    }

    docker run --rm `
        -v "${ProjectPath}:/workspace" `
        -v "${DataDir}:/data" `
        -v /var/run/docker.sock:/var/run/docker.sock `
        -w /workspace `
        $ImageName `
        /bin/bash -lc "$Script $allArgs"
}

# Version
function Show-Version {
    $config = Get-ToolsConfig
    $toolCount = ($config.categories | ForEach-Object { $_.tools } | Measure-Object).Count

    Write-Host "DevTools v1.0.0" -ForegroundColor White
    Write-Host "Swiss Army Knife for Git-based Development"
    Write-Host ""
    Write-Host "Components:"
    Write-Host "  - DevTools Runtime Container (Git, Python, Shell)"
    Write-Host "  - $toolCount tools across $($config.categories.Count) categories"
}

# Hauptlogik
$cmd = $Command.ToLower()

# Built-in commands
switch ($cmd) {
    "shell" {
        $path = if ($Arguments -and $Arguments.Count -gt 0) { $Arguments[0] } else { (Get-Location).Path }
        Start-Shell -ProjectPath $path
        exit
    }
    "run" {
        if (-not $Arguments -or $Arguments.Count -eq 0) {
            Write-Host "[ERROR] Script name required" -ForegroundColor Red
            exit 1
        }
        $script = $Arguments[0]
        $scriptArgs = if ($Arguments.Count -gt 1) { $Arguments[1..($Arguments.Count - 1)] } else { @() }
        Invoke-Script -Script $script -ScriptArgs $scriptArgs
        exit
    }
    "build" {
        Test-Docker | Out-Null
        Build-Image
        exit
    }
    { $_ -in "help", "--help", "-h" } {
        Show-Help
        exit
    }
    { $_ -in "version", "--version", "-v" } {
        Show-Version
        exit
    }
}

# Dynamic tool lookup
$tool = Find-Tool -CommandName $cmd

if ($tool -and $tool.script) {
    # Special handling for stats and cleanup (project path as first arg)
    if ($cmd -in "stats", "cleanup") {
        $path = if ($Arguments -and $Arguments.Count -gt 0) { $Arguments[0] } else { (Get-Location).Path }
        $scriptArgs = if ($Arguments.Count -gt 1) { $Arguments[1..($Arguments.Count - 1)] } else { @() }
        Invoke-Script -Script $tool.script -ScriptArgs $scriptArgs -ProjectPath $path
    } else {
        Invoke-Script -Script $tool.script -ScriptArgs $Arguments
    }
} else {
    Write-Host "[ERROR] Unknown command: $Command" -ForegroundColor Red
    Show-Help
    exit 1
}
