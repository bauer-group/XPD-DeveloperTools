# =============================================================================
# Generate tools.json from script headers
# Scans all scripts in services/devtools/scripts/ and extracts @-tags
# =============================================================================

param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\tools.json")
)

$ScriptDir = Split-Path -Parent $PSScriptRoot
$ScriptsPath = Join-Path $ScriptDir "services\devtools\scripts"

if (-not (Test-Path $ScriptsPath)) {
    Write-Host "[ERROR] Scripts directory not found at $ScriptsPath" -ForegroundColor Red
    exit 1
}

# Extract metadata from a script file
function Get-ScriptMetadata {
    param([string]$FilePath)

    $content = Get-Content $FilePath -TotalCount 15 -ErrorAction SilentlyContinue
    if (-not $content) { return $null }

    $metadata = @{
        name = $null
        description = $null
        category = $null
        usage = $null
        script = Split-Path -Leaf $FilePath
    }

    foreach ($line in $content) {
        if ($line -match '^\s*#\s*@name:\s*(.+)$') {
            $metadata.name = $Matches[1].Trim()
        }
        elseif ($line -match '^\s*#\s*@description:\s*(.+)$') {
            $metadata.description = $Matches[1].Trim()
        }
        elseif ($line -match '^\s*#\s*@category:\s*(.+)$') {
            $metadata.category = $Matches[1].Trim()
        }
        elseif ($line -match '^\s*#\s*@usage:\s*(.+)$') {
            $metadata.usage = $Matches[1].Trim()
        }
    }

    # Skip if no name found
    if (-not $metadata.name) { return $null }

    return $metadata
}

# Parse usage to extract command and args
function Parse-Usage {
    param([string]$Usage, [string]$Name)

    # Default: use name as command
    $command = $Name
    $args = $null

    if ($Usage) {
        # Remove script extension from usage: "gh-packages-cleanup.py [-o <org>]" -> "gh-packages-cleanup [-o <org>]"
        $Usage = $Usage -replace '\.(py|sh)\s*', ' '
        $Usage = $Usage.Trim()

        # Split into command and args
        if ($Usage -match '^(\S+)\s*(.*)$') {
            $command = $Matches[1]
            $args = $Matches[2].Trim()
            if ($args -eq '') { $args = $null }
        }
    }

    return @{
        command = $command
        args = $args
    }
}

# Short aliases for common commands
$commandAliases = @{
    "git-stats" = @("stats")
    "git-cleanup" = @("cleanup")
    "git-changelog" = @("changelog")
    "git-release" = @("release")
    "git-lfs-migrate" = @("lfs-migrate", "lfs")
    "git-history-clean" = @("history-clean")
    "git-branch-rename" = @("branch-rename")
    "git-split-repo" = @("split-repo")
    "git-rewrite-commits" = @("rewrite-commits")
    "git-mirror" = @("mirror")
    "git-contributors" = @("contributors")
    "gh-create-repo" = @("gh-create")
    "gh-topic-manager" = @("gh-topics")
    "gh-archive-repos" = @("gh-archive")
    "gh-trigger-workflow" = @("gh-workflow")
    "gh-branch-protection" = @("gh-branch-protect")
}

# Collect all scripts
$allScripts = Get-ChildItem -Path $ScriptsPath -Filter "*.sh" -File
$allScripts += Get-ChildItem -Path $ScriptsPath -Filter "*.py" -File

$tools = @{}

foreach ($script in $allScripts) {
    # Skip help script
    if ($script.Name -eq "help-devtools.sh") { continue }

    $metadata = Get-ScriptMetadata -FilePath $script.FullName
    if (-not $metadata) { continue }

    $parsed = Parse-Usage -Usage $metadata.usage -Name $metadata.name
    $category = if ($metadata.category) { $metadata.category } else { "other" }

    if (-not $tools.ContainsKey($category)) {
        $tools[$category] = @()
    }

    $toolEntry = [ordered]@{
        command = $parsed.command
        script = $metadata.script
        description = $metadata.description
    }

    if ($parsed.args) {
        $toolEntry.args = $parsed.args
    }

    # Add aliases if defined
    if ($commandAliases.ContainsKey($parsed.command)) {
        $toolEntry.aliases = $commandAliases[$parsed.command]
    }

    $tools[$category] += $toolEntry
}

# Build category structure with proper names
$categoryNames = @{
    "git" = "Git Tools"
    "github" = "GitHub Tools"
    "system" = "System Tools"
    "other" = "Other Tools"
}

$categories = @()

# Add runtime category (built-in commands, not from scripts)
$categories += [ordered]@{
    id = "runtime"
    name = "Runtime Container"
    tools = @(
        [ordered]@{ command = "shell"; script = $null; description = "Start interactive shell"; args = "[PROJECT_PATH]" }
        [ordered]@{ command = "run"; script = $null; description = "Run a script in the container"; args = "<script> [args]" }
        [ordered]@{ command = "build"; script = $null; description = "Build/rebuild the container" }
    )
}

# Add discovered categories
$categoryOrder = @("git", "github", "system", "other")
foreach ($catId in $categoryOrder) {
    if ($tools.ContainsKey($catId) -and $tools[$catId].Count -gt 0) {
        $catName = if ($categoryNames.ContainsKey($catId)) { $categoryNames[$catId] } else { "$catId Tools" }

        # Sort tools by command name
        $sortedTools = $tools[$catId] | Sort-Object { $_.command }

        $categories += [ordered]@{
            id = $catId
            name = $catName
            tools = @($sortedTools)
        }
    }
}

# Add general category
$categories += [ordered]@{
    id = "general"
    name = "General"
    tools = @(
        [ordered]@{ command = "help"; script = $null; description = "Show this help" }
        [ordered]@{ command = "version"; script = $null; description = "Show version info"; aliases = @("--version", "-v") }
    )
}

# Build final structure
$config = [ordered]@{
    "_generated" = "Auto-generated from script headers. Run: .\scripts\generate-tools-json.ps1"
    "_source" = "services/devtools/scripts/*.sh, *.py"
    categories = $categories
}

# Write JSON
$json = $config | ConvertTo-Json -Depth 10
$json | Out-File -FilePath $OutputPath -Encoding UTF8

# Count tools
$toolCount = ($categories | ForEach-Object { $_.tools } | Measure-Object).Count

Write-Host "[OK] Generated $OutputPath" -ForegroundColor Green
Write-Host "     Found $toolCount tools in $($categories.Count) categories" -ForegroundColor Cyan
