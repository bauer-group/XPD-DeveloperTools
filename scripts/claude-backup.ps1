# =============================================================================
# Claude Code Configuration Backup & Restore
# Backs up critical Claude Code config from %USERPROFILE%\.claude
# =============================================================================

param(
    [Parameter(Position = 0)]
    [ValidateSet("backup", "restore", "list", "")]
    [string]$Action = "backup",

    [Parameter(Position = 1)]
    [string]$BackupFile,

    [int]$Keep = 10
)

$ErrorActionPreference = "Stop"
$ClaudeDir = Join-Path $env:USERPROFILE ".claude"
$ScriptDir = Split-Path -Parent $PSScriptRoot
$BackupDir = Join-Path (Join-Path $ScriptDir ".data") "claude-backups"

# Directories and files to include
$IncludeDirs = @(
    "plugins"
)
$IncludeFiles = @(
    "settings.json"
)
# Project memories (selective: only memory/ subdirs)
$IncludeProjectMemories = $true

# Directories to explicitly exclude
$ExcludeDirs = @(
    ".bauer-standards"
    "backups"
    "cache"
    "debug"
    "downloads"
    "file-history"
    "ide"
    "plans"
    "session-env"
    "sessions"
    "shell-snapshots"
    "statsig"
    "telemetry"
    "todos"
)
$ExcludeFiles = @(
    ".credentials.json"
    "history.jsonl"
    "stats-cache.json"
    "mcp-needs-auth-cache.json"
)

function Write-Info  { param([string]$Msg) Write-Host "[INFO] $Msg" }
function Write-Ok    { param([string]$Msg) Write-Host "[OK] $Msg" -ForegroundColor Green }
function Write-Err   { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }
function Write-Warn  { param([string]$Msg) Write-Host "[WARN] $Msg" -ForegroundColor Yellow }

# =============================================================================
# BACKUP
# =============================================================================
function Invoke-Backup {
    if (-not (Test-Path $ClaudeDir)) {
        Write-Err "Claude directory not found: $ClaudeDir"
        exit 1
    }

    if (-not (Test-Path $BackupDir)) {
        New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    }

    $timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
    $zipName = "claude-backup_$timestamp.zip"
    $zipPath = Join-Path $BackupDir $zipName
    $tempDir = Join-Path $env:TEMP "claude-backup-$timestamp"

    try {
        Write-Info "Backing up Claude Code config..."
        Write-Info "Source: $ClaudeDir"

        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

        $fileCount = 0

        # Copy top-level files
        foreach ($file in $IncludeFiles) {
            $src = Join-Path $ClaudeDir $file
            if (Test-Path $src) {
                Copy-Item $src -Destination (Join-Path $tempDir $file)
                $fileCount++
                Write-Info "  + $file"
            }
        }

        # Copy included directories
        foreach ($dir in $IncludeDirs) {
            $src = Join-Path $ClaudeDir $dir
            if (Test-Path $src) {
                $dest = Join-Path $tempDir $dir
                Copy-Item $src -Destination $dest -Recurse
                $count = (Get-ChildItem $dest -Recurse -File).Count
                $fileCount += $count
                Write-Info "  + $dir/ ($count files)"
            }
        }

        # Copy project memories
        if ($IncludeProjectMemories) {
            $projectsDir = Join-Path $ClaudeDir "projects"
            if (Test-Path $projectsDir) {
                $memoryDirs = Get-ChildItem $projectsDir -Directory | ForEach-Object {
                    $memDir = Join-Path $_.FullName "memory"
                    if (Test-Path $memDir) { $_ }
                }

                if ($memoryDirs) {
                    $memCount = 0
                    foreach ($projDir in $memoryDirs) {
                        $memSrc = Join-Path $projDir.FullName "memory"
                        $relPath = Join-Path (Join-Path "projects" $projDir.Name) "memory"
                        $memDest = Join-Path $tempDir $relPath
                        New-Item -ItemType Directory -Path $memDest -Force | Out-Null
                        Copy-Item (Join-Path $memSrc "*") -Destination $memDest -Recurse -ErrorAction SilentlyContinue
                        $count = (Get-ChildItem $memDest -Recurse -File -ErrorAction SilentlyContinue).Count
                        $memCount += $count
                    }
                    $fileCount += $memCount
                    Write-Info "  + projects/*/memory/ ($memCount files across $($memoryDirs.Count) projects)"
                }

                # Also backup per-project CLAUDE.md and settings
                $projConfigs = Get-ChildItem $projectsDir -Directory | ForEach-Object {
                    $settingsFile = Join-Path $_.FullName "settings.json"
                    $claudeMd = Join-Path $_.FullName "CLAUDE.md"
                    @{ Dir = $_; HasSettings = (Test-Path $settingsFile); HasClaudeMd = (Test-Path $claudeMd) }
                } | Where-Object { $_.HasSettings -or $_.HasClaudeMd }

                foreach ($proj in $projConfigs) {
                    $relBase = Join-Path "projects" $proj.Dir.Name
                    $destBase = Join-Path $tempDir $relBase
                    if (-not (Test-Path $destBase)) {
                        New-Item -ItemType Directory -Path $destBase -Force | Out-Null
                    }
                    if ($proj.HasSettings) {
                        Copy-Item (Join-Path $proj.Dir.FullName "settings.json") -Destination $destBase
                        $fileCount++
                    }
                    if ($proj.HasClaudeMd) {
                        Copy-Item (Join-Path $proj.Dir.FullName "CLAUDE.md") -Destination $destBase
                        $fileCount++
                    }
                }
            }
        }

        if ($fileCount -eq 0) {
            Write-Warn "No files found to backup"
            return
        }

        # Create ZIP
        Compress-Archive -Path (Join-Path $tempDir "*") -DestinationPath $zipPath -Force
        $zipSize = [math]::Round((Get-Item $zipPath).Length / 1KB, 1)

        Write-Ok "Backup created: $zipName ($($zipSize) KB, $fileCount files)"
        Write-Info "Location: $zipPath"

        # Cleanup old backups
        $backups = Get-ChildItem $BackupDir -Filter "claude-backup_*.zip" | Sort-Object Name -Descending
        if ($backups.Count -gt $Keep) {
            $toRemove = $backups | Select-Object -Skip $Keep
            foreach ($old in $toRemove) {
                Remove-Item $old.FullName -Force
                Write-Info "Removed old backup: $($old.Name)"
            }
        }
    }
    finally {
        if (Test-Path $tempDir) {
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# =============================================================================
# RESTORE
# =============================================================================
function Invoke-Restore {
    if (-not $BackupFile) {
        # Use most recent backup
        if (-not (Test-Path $BackupDir)) {
            Write-Err "No backups found in $BackupDir"
            exit 1
        }
        $latest = Get-ChildItem $BackupDir -Filter "claude-backup_*.zip" | Sort-Object Name -Descending | Select-Object -First 1
        if (-not $latest) {
            Write-Err "No backup files found"
            exit 1
        }
        $BackupFile = $latest.FullName
        Write-Info "Using latest backup: $($latest.Name)"
    }

    if (-not (Test-Path $BackupFile)) {
        # Try relative to backup dir
        $tryPath = Join-Path $BackupDir $BackupFile
        if (Test-Path $tryPath) {
            $BackupFile = $tryPath
        } else {
            Write-Err "Backup file not found: $BackupFile"
            exit 1
        }
    }

    if (-not (Test-Path $ClaudeDir)) {
        New-Item -ItemType Directory -Path $ClaudeDir -Force | Out-Null
    }

    $tempDir = Join-Path $env:TEMP "claude-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

    try {
        Write-Info "Restoring from: $BackupFile"

        Expand-Archive -Path $BackupFile -DestinationPath $tempDir -Force

        $fileCount = 0

        # Restore top-level files
        foreach ($file in $IncludeFiles) {
            $src = Join-Path $tempDir $file
            if (Test-Path $src) {
                $dest = Join-Path $ClaudeDir $file
                # Create backup of current file before overwriting
                if (Test-Path $dest) {
                    $bak = "$dest.pre-restore"
                    Copy-Item $dest -Destination $bak -Force
                    Write-Info "  Existing $file saved as $file.pre-restore"
                }
                Copy-Item $src -Destination $dest -Force
                $fileCount++
                Write-Info "  + $file"
            }
        }

        # Restore directories
        foreach ($dir in $IncludeDirs) {
            $src = Join-Path $tempDir $dir
            if (Test-Path $src) {
                $dest = Join-Path $ClaudeDir $dir
                if (-not (Test-Path $dest)) {
                    New-Item -ItemType Directory -Path $dest -Force | Out-Null
                }
                Copy-Item (Join-Path $src "*") -Destination $dest -Recurse -Force
                $count = (Get-ChildItem $src -Recurse -File).Count
                $fileCount += $count
                Write-Info "  + $dir/ ($count files)"
            }
        }

        # Restore project data
        $projSrc = Join-Path $tempDir "projects"
        if (Test-Path $projSrc) {
            $projDest = Join-Path $ClaudeDir "projects"
            foreach ($projDir in (Get-ChildItem $projSrc -Directory)) {
                $targetDir = Join-Path $projDest $projDir.Name
                if (-not (Test-Path $targetDir)) {
                    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
                }
                Copy-Item (Join-Path $projDir.FullName "*") -Destination $targetDir -Recurse -Force
                $count = (Get-ChildItem $projDir.FullName -Recurse -File).Count
                $fileCount += $count
            }
            $projCount = (Get-ChildItem $projSrc -Directory).Count
            Write-Info "  + projects/ ($projCount projects)"
        }

        Write-Ok "Restored $fileCount files from backup"
        Write-Warn "Restart Claude Code for changes to take effect"
    }
    finally {
        if (Test-Path $tempDir) {
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# =============================================================================
# LIST
# =============================================================================
function Invoke-List {
    if (-not (Test-Path $BackupDir)) {
        Write-Info "No backups directory found"
        return
    }

    $backups = Get-ChildItem $BackupDir -Filter "claude-backup_*.zip" | Sort-Object Name -Descending

    if ($backups.Count -eq 0) {
        Write-Info "No backups found"
        return
    }

    Write-Host ""
    Write-Host "  Claude Code Backups ($($backups.Count) found)"
    Write-Host "  $('=' * 50)"
    Write-Host ""

    foreach ($backup in $backups) {
        $size = [math]::Round($backup.Length / 1KB, 1)
        $date = $backup.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
        Write-Host "  $date  $($size.ToString().PadLeft(8)) KB  $($backup.Name)"
    }

    Write-Host ""
    Write-Host "  Location: $BackupDir"
    Write-Host ""
}

# =============================================================================
# MAIN
# =============================================================================
switch ($Action) {
    "backup"  { Invoke-Backup }
    "restore" { Invoke-Restore }
    "list"    { Invoke-List }
    default   { Invoke-Backup }
}
