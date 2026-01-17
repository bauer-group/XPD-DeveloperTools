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

# Hilfe anzeigen
function Show-Help {
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
    Write-Host "  Runtime Container:" -ForegroundColor Cyan
    Write-Host "    shell [PROJECT_PATH]    Start interactive shell in DevTools container"
    Write-Host "    run <script> [args]     Run a script in the container"
    Write-Host "    build                   Build/rebuild the DevTools container"
    Write-Host ""
    Write-Host "  Git Tools (via container):" -ForegroundColor Cyan
    Write-Host "    stats [PROJECT_PATH]    Show repository statistics"
    Write-Host "    cleanup [PROJECT_PATH]  Clean up branches and cache"
    Write-Host "    changelog [options]     Generate changelog"
    Write-Host "    release [options]       Manage releases"
    Write-Host "    lfs-migrate [options]   Migrate repository to Git LFS"
    Write-Host "    history-clean [opts]    Remove large files from git history"
    Write-Host "    branch-rename [opts]    Rename git branches (local + remote)"
    Write-Host "    split-repo [options]    Split monorepo into separate repos"
    Write-Host "    rewrite-commits [opts]  Rewrite commit messages (pattern-based)"
    Write-Host ""
    Write-Host "  GitHub Tools (via container):" -ForegroundColor Cyan
    Write-Host "    gh-create [options]     Create GitHub repository"
    Write-Host "    gh-topics [options]     Manage repository topics"
    Write-Host "    gh-archive [options]    Archive repositories"
    Write-Host "    gh-workflow [options]   Trigger GitHub Actions workflows"
    Write-Host "    gh-add-workflow [opts]  Add workflow files to repos"
    Write-Host "    gh-clean-releases       Clean releases and tags"
    Write-Host "    gh-visibility [opts]    Change repo visibility (public/private)"
    Write-Host "    gh-clone-org [opts]     Clone all repos from organization"
    Write-Host "    gh-sync-forks [opts]    Sync forked repos with upstream"
    Write-Host "    gh-pr-cleanup [opts]    Clean stale PRs and branches"
    Write-Host "    gh-secrets-audit [opts] Audit secrets across repos"
    Write-Host "    gh-labels-sync [opts]   Sync labels between repos"
    Write-Host "    gh-branch-protect [opt] Manage branch protection rules"
    Write-Host "    gh-packages-cleanup     Clean old package versions"
    Write-Host ""
    Write-Host "  Git Advanced Tools (via container):" -ForegroundColor Cyan
    Write-Host "    git-mirror [options]    Mirror repo between servers"
    Write-Host "    git-contributors [opts] Show contributor statistics"
    Write-Host ""
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

# Git-Statistiken
function Get-GitStats {
    param([string]$ProjectPath = (Get-Location).Path)
    Invoke-Script -Script "git-stats.sh" -ProjectPath $ProjectPath
}

# Git-Cleanup
function Invoke-GitCleanup {
    param(
        [string]$ProjectPath = (Get-Location).Path,
        [string[]]$CleanupArgs
    )
    Invoke-Script -Script "git-cleanup.sh" -ScriptArgs $CleanupArgs -ProjectPath $ProjectPath
}

# Changelog generieren
function Get-GitChangelog {
    param([string[]]$ChangelogArgs)
    Invoke-Script -Script "git-changelog.py" -ScriptArgs $ChangelogArgs
}

# Release verwalten
function Invoke-GitRelease {
    param([string[]]$ReleaseArgs)
    Invoke-Script -Script "git-release.py" -ScriptArgs $ReleaseArgs
}

# LFS Migration
function Invoke-GitLfsMigrate {
    param([string[]]$LfsArgs)
    Invoke-Script -Script "git-lfs-migrate.sh" -ScriptArgs $LfsArgs
}

# History Clean
function Invoke-GitHistoryClean {
    param([string[]]$HistoryArgs)
    Invoke-Script -Script "git-history-clean.sh" -ScriptArgs $HistoryArgs
}

# Branch Rename
function Invoke-GitBranchRename {
    param([string[]]$BranchArgs)
    Invoke-Script -Script "git-branch-rename.sh" -ScriptArgs $BranchArgs
}

# Split Repo
function Invoke-GitSplitRepo {
    param([string[]]$SplitArgs)
    Invoke-Script -Script "git-split-repo.py" -ScriptArgs $SplitArgs
}

# Rewrite Commits
function Invoke-GitRewriteCommits {
    param([string[]]$RewriteArgs)
    Invoke-Script -Script "git-rewrite-commits.py" -ScriptArgs $RewriteArgs
}

# GitHub Create Repo
function Invoke-GhCreateRepo {
    param([string[]]$CreateArgs)
    Invoke-Script -Script "gh-create-repo.sh" -ScriptArgs $CreateArgs
}

# GitHub Topic Manager
function Invoke-GhTopicManager {
    param([string[]]$TopicArgs)
    Invoke-Script -Script "gh-topic-manager.py" -ScriptArgs $TopicArgs
}

# GitHub Archive Repos
function Invoke-GhArchiveRepos {
    param([string[]]$ArchiveArgs)
    Invoke-Script -Script "gh-archive-repos.py" -ScriptArgs $ArchiveArgs
}

# GitHub Trigger Workflow
function Invoke-GhTriggerWorkflow {
    param([string[]]$WorkflowArgs)
    Invoke-Script -Script "gh-trigger-workflow.sh" -ScriptArgs $WorkflowArgs
}

# GitHub Add Workflow
function Invoke-GhAddWorkflow {
    param([string[]]$AddWorkflowArgs)
    Invoke-Script -Script "gh-add-workflow.py" -ScriptArgs $AddWorkflowArgs
}

# GitHub Clean Releases
function Invoke-GhCleanReleases {
    param([string[]]$CleanArgs)
    Invoke-Script -Script "gh-clean-releases.py" -ScriptArgs $CleanArgs
}

# GitHub Visibility
function Invoke-GhVisibility {
    param([string[]]$VisibilityArgs)
    Invoke-Script -Script "gh-visibility.py" -ScriptArgs $VisibilityArgs
}

# GitHub Clone Org
function Invoke-GhCloneOrg {
    param([string[]]$CloneArgs)
    Invoke-Script -Script "gh-clone-org.sh" -ScriptArgs $CloneArgs
}

# GitHub Sync Forks
function Invoke-GhSyncForks {
    param([string[]]$SyncArgs)
    Invoke-Script -Script "gh-sync-forks.py" -ScriptArgs $SyncArgs
}

# GitHub PR Cleanup
function Invoke-GhPrCleanup {
    param([string[]]$CleanupArgs)
    Invoke-Script -Script "gh-pr-cleanup.py" -ScriptArgs $CleanupArgs
}

# GitHub Secrets Audit
function Invoke-GhSecretsAudit {
    param([string[]]$AuditArgs)
    Invoke-Script -Script "gh-secrets-audit.py" -ScriptArgs $AuditArgs
}

# GitHub Labels Sync
function Invoke-GhLabelsSync {
    param([string[]]$LabelsArgs)
    Invoke-Script -Script "gh-labels-sync.py" -ScriptArgs $LabelsArgs
}

# GitHub Branch Protection
function Invoke-GhBranchProtection {
    param([string[]]$ProtectionArgs)
    Invoke-Script -Script "gh-branch-protection.py" -ScriptArgs $ProtectionArgs
}

# GitHub Packages Cleanup
function Invoke-GhPackagesCleanup {
    param([string[]]$CleanupArgs)
    Invoke-Script -Script "gh-packages-cleanup.py" -ScriptArgs $CleanupArgs
}

# Git Mirror
function Invoke-GitMirror {
    param([string[]]$MirrorArgs)
    Invoke-Script -Script "git-mirror.sh" -ScriptArgs $MirrorArgs
}

# Git Contributors
function Invoke-GitContributors {
    param([string[]]$ContribArgs)
    Invoke-Script -Script "git-contributors.py" -ScriptArgs $ContribArgs
}

# Version
function Show-Version {
    Write-Host "DevTools v1.0.0" -ForegroundColor White
    Write-Host "Swiss Army Knife for Git-based Development"
    Write-Host ""
    Write-Host "Components:"
    Write-Host "  - DevTools Runtime Container (Git, Python, Shell)"
    Write-Host "  - Git Tools (stats, cleanup, changelog, release, lfs-migrate, history-clean, branch-rename, split-repo, rewrite-commits, mirror, contributors)"
    Write-Host "  - GitHub Tools (gh-create, gh-topics, gh-archive, gh-workflow, gh-add-workflow, gh-clean-releases, gh-visibility, gh-clone-org, gh-sync-forks, gh-pr-cleanup, gh-secrets-audit, gh-labels-sync, gh-branch-protect, gh-packages-cleanup)"
}

# Hauptlogik
switch ($Command.ToLower()) {
    "shell" {
        $path = if ($Arguments -and $Arguments.Count -gt 0) { $Arguments[0] } else { (Get-Location).Path }
        Start-Shell -ProjectPath $path
    }
    "run" {
        if (-not $Arguments -or $Arguments.Count -eq 0) {
            Write-Host "[ERROR] Script name required" -ForegroundColor Red
            exit 1
        }
        $script = $Arguments[0]
        $scriptArgs = if ($Arguments.Count -gt 1) { $Arguments[1..($Arguments.Count - 1)] } else { @() }
        Invoke-Script -Script $script -ScriptArgs $scriptArgs
    }
    "build" {
        Test-Docker | Out-Null
        Build-Image
    }
    "stats" {
        $path = if ($Arguments -and $Arguments.Count -gt 0) { $Arguments[0] } else { (Get-Location).Path }
        Get-GitStats -ProjectPath $path
    }
    "cleanup" {
        $path = if ($Arguments -and $Arguments.Count -gt 0) { $Arguments[0] } else { (Get-Location).Path }
        Invoke-GitCleanup -ProjectPath $path -CleanupArgs $Arguments
    }
    "changelog" {
        Get-GitChangelog -ChangelogArgs $Arguments
    }
    "release" {
        Invoke-GitRelease -ReleaseArgs $Arguments
    }
    { $_ -in "lfs-migrate", "lfs" } {
        Invoke-GitLfsMigrate -LfsArgs $Arguments
    }
    "history-clean" {
        Invoke-GitHistoryClean -HistoryArgs $Arguments
    }
    "branch-rename" {
        Invoke-GitBranchRename -BranchArgs $Arguments
    }
    "split-repo" {
        Invoke-GitSplitRepo -SplitArgs $Arguments
    }
    "rewrite-commits" {
        Invoke-GitRewriteCommits -RewriteArgs $Arguments
    }
    "gh-create" {
        Invoke-GhCreateRepo -CreateArgs $Arguments
    }
    "gh-topics" {
        Invoke-GhTopicManager -TopicArgs $Arguments
    }
    "gh-archive" {
        Invoke-GhArchiveRepos -ArchiveArgs $Arguments
    }
    "gh-workflow" {
        Invoke-GhTriggerWorkflow -WorkflowArgs $Arguments
    }
    "gh-add-workflow" {
        Invoke-GhAddWorkflow -AddWorkflowArgs $Arguments
    }
    "gh-clean-releases" {
        Invoke-GhCleanReleases -CleanArgs $Arguments
    }
    "gh-visibility" {
        Invoke-GhVisibility -VisibilityArgs $Arguments
    }
    "gh-clone-org" {
        Invoke-GhCloneOrg -CloneArgs $Arguments
    }
    "gh-sync-forks" {
        Invoke-GhSyncForks -SyncArgs $Arguments
    }
    "gh-pr-cleanup" {
        Invoke-GhPrCleanup -CleanupArgs $Arguments
    }
    "gh-secrets-audit" {
        Invoke-GhSecretsAudit -AuditArgs $Arguments
    }
    "gh-labels-sync" {
        Invoke-GhLabelsSync -LabelsArgs $Arguments
    }
    { $_ -in "gh-branch-protect", "gh-branch-protection" } {
        Invoke-GhBranchProtection -ProtectionArgs $Arguments
    }
    "gh-packages-cleanup" {
        Invoke-GhPackagesCleanup -CleanupArgs $Arguments
    }
    { $_ -in "git-mirror", "mirror" } {
        Invoke-GitMirror -MirrorArgs $Arguments
    }
    { $_ -in "git-contributors", "contributors" } {
        Invoke-GitContributors -ContribArgs $Arguments
    }
    { $_ -in "version", "--version", "-v" } {
        Show-Version
    }
    { $_ -in "help", "--help", "-h" } {
        Show-Help
    }
    default {
        Write-Host "[ERROR] Unknown command: $Command" -ForegroundColor Red
        Show-Help
        exit 1
    }
}
