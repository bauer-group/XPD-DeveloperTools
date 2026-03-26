# =============================================================================
# Generate devtools.cmd from tools.json
# =============================================================================

param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\devtools.cmd")
)

$ScriptDir = Split-Path -Parent $PSScriptRoot
$ToolsConfigPath = Join-Path $ScriptDir "tools.json"

if (-not (Test-Path $ToolsConfigPath)) {
    Write-Host "[ERROR] tools.json not found at $ToolsConfigPath" -ForegroundColor Red
    exit 1
}

$config = Get-Content $ToolsConfigPath -Raw | ConvertFrom-Json

# Collect all tools with scripts
$tools = @()
foreach ($category in $config.categories) {
    foreach ($tool in $category.tools) {
        if ($tool.script) {
            $tools += @{
                command = $tool.command
                script = $tool.script
                runtime = if ($tool.runtime) { $tool.runtime } else { "docker" }
                description = $tool.description
                args = $tool.args
                aliases = $tool.aliases
            }
        }
    }
}

# Generate CMD content
$cmdContent = @"
@echo off
:: =============================================================================
:: DevTools - Swiss Army Knife for Git-based Development
:: Runtime Container for Git operations and development tools
:: AUTO-GENERATED from tools.json - Do not edit manually!
:: Run: .\scripts\generate-cmd.ps1 to regenerate
:: =============================================================================

set "SCRIPT_DIR=%~dp0"
set "IMAGE_NAME=bauer-devtools"
set "CONTAINER_NAME=devtools-runtime"
set "DATA_DIR=%SCRIPT_DIR%.data"

:: Get command
set "CMD=%1"
if "%CMD%"=="" set "CMD=help"

:: Route commands
if /i "%CMD%"=="shell" goto shell
if /i "%CMD%"=="build" goto build
if /i "%CMD%"=="run" goto run
if /i "%CMD%"=="stats" goto stats
if /i "%CMD%"=="cleanup" goto cleanup

"@

# Add routing for each tool
foreach ($tool in $tools) {
    $target = if ($tool.runtime -ne "docker") { "native_script" } else { "script" }
    if ($tool.command -notin @("stats", "cleanup")) {
        $cmdContent += "if /i `"%CMD%`"==`"$($tool.command)`" goto $target`n"
    }
    # Add aliases
    if ($tool.aliases) {
        foreach ($alias in $tool.aliases) {
            $cmdContent += "if /i `"%CMD%`"==`"$alias`" goto $target`n"
        }
    }
}

$cmdContent += @"
if /i "%CMD%"=="version" goto version
if /i "%CMD%"=="--version" goto version
if /i "%CMD%"=="-v" goto version
if /i "%CMD%"=="help" goto help
if /i "%CMD%"=="--help" goto help
if /i "%CMD%"=="-h" goto help
echo [ERROR] Unknown command: %CMD%
goto help

:: =============================================================================
:shell
:: =============================================================================
setlocal
set "P=%~2"
if "%P%"=="" set "P=%CD%"
pushd "%P%" 2>nul || goto shell_err
set "P=%CD%"
popd
call :check_docker || goto :eof
call :ensure_image || goto :eof
echo [INFO] Starting DevTools shell...
echo [INFO] Mounting: %P%
for /f "tokens=*" %%i in ('git config --global user.name 2^>nul') do set "GIT_NAME=%%i"
for /f "tokens=*" %%i in ('git config --global user.email 2^>nul') do set "GIT_EMAIL=%%i"
for %%i in ("%P%") do set "PROJECT_NAME=%%~nxi"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
docker run -it --rm --name %CONTAINER_NAME% -v "%P%:/workspace" -v "%DATA_DIR%:/data" -e "GIT_USER_NAME=%GIT_NAME%" -e "GIT_USER_EMAIL=%GIT_EMAIL%" -e "PROJECT_NAME=%PROJECT_NAME%" -w /workspace %IMAGE_NAME%
endlocal
goto :eof

:shell_err
echo [ERROR] Directory not found: %P%
exit /b 1

:: =============================================================================
:build
:: =============================================================================
call :check_docker || goto :eof
echo [INFO] Building DevTools image...
docker build -t %IMAGE_NAME% "%SCRIPT_DIR%services\devtools" || goto build_err
echo [OK] Image built successfully
goto :eof

:build_err
echo [ERROR] Failed to build image
exit /b 1

:: =============================================================================
:run
:: =============================================================================
if "%~2"=="" goto run_err
call :check_docker || goto :eof
call :ensure_image || goto :eof
set "SCRIPT=%~2"
echo [INFO] Running: %SCRIPT% %3 %4 %5 %6
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
docker run --rm -v "%CD%:/workspace" -v "%DATA_DIR%:/data" -w /workspace %IMAGE_NAME% /bin/bash -lc "%SCRIPT% %~3 %~4 %~5 %~6"
goto :eof

:run_err
echo [ERROR] Script name required
exit /b 1

:: =============================================================================
:stats
:: =============================================================================
set "P=%~2"
if "%P%"=="" set "P=%CD%"
call :check_docker || goto :eof
call :ensure_image || goto :eof
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
docker run --rm -v "%P%:/workspace" -v "%DATA_DIR%:/data" -w /workspace %IMAGE_NAME% /bin/bash -lc "git-stats.sh"
goto :eof

:: =============================================================================
:cleanup
:: =============================================================================
set "P=%~2"
if "%P%"=="" set "P=%CD%"
call :check_docker || goto :eof
call :ensure_image || goto :eof
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
docker run --rm -v "%P%:/workspace" -v "%DATA_DIR%:/data" -w /workspace %IMAGE_NAME% /bin/bash -lc "git-cleanup.sh %~3 %~4 %~5"
goto :eof

:: =============================================================================
:script
:: =============================================================================
call :check_docker || goto :eof
call :ensure_image || goto :eof
set "S="

"@

# Add script mappings (Docker tools only)
foreach ($tool in $tools) {
    if ($tool.runtime -ne "docker") { continue }
    if ($tool.command -notin @("stats", "cleanup")) {
        $cmdContent += "if /i `"%CMD%`"==`"$($tool.command)`" set `"S=$($tool.script)`"`n"
    }
    # Add aliases
    if ($tool.aliases) {
        foreach ($alias in $tool.aliases) {
            $cmdContent += "if /i `"%CMD%`"==`"$alias`" set `"S=$($tool.script)`"`n"
        }
    }
}

$cmdContent += @"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
docker run --rm -v "%CD%:/workspace" -v "%DATA_DIR%:/data" -w /workspace %IMAGE_NAME% /bin/bash -lc "%S% %~2 %~3 %~4 %~5"
goto :eof

:: =============================================================================
:native_script
:: =============================================================================
set "NS="

"@

# Add native script mappings
foreach ($tool in $tools) {
    if ($tool.runtime -eq "docker") { continue }
    $cmdContent += "if /i `"%CMD%`"==`"$($tool.command)`" set `"NS=$($tool.script)`"`n"
    if ($tool.aliases) {
        foreach ($alias in $tool.aliases) {
            $cmdContent += "if /i `"%CMD%`"==`"$alias`" set `"NS=$($tool.script)`"`n"
        }
    }
}

$cmdContent += @"
python "%SCRIPT_DIR%scripts\%NS%" %~2 %~3 %~4 %~5 %~6
goto :eof

:: =============================================================================
:version
:: =============================================================================
echo DevTools v1.0.0
echo Swiss Army Knife for Git-based Development
echo.
echo Components:
echo   - DevTools Runtime Container (Git, Python, Shell)
echo   - $($tools.Count) tools from tools.json
goto :eof

:: =============================================================================
:help
:: =============================================================================
echo.
echo ======================================================================
echo               DevTools - Developer Swiss Army Knife
echo ======================================================================
echo.
echo Usage: devtools ^<command^> [options]
echo.
echo Commands:
echo   shell [PATH]          Start interactive shell (default: current dir)
echo   build                 Build/rebuild the container
echo   run ^<script^> [args]   Run a script in the container

"@

# Add help entries grouped by category
foreach ($category in $config.categories) {
    if ($category.id -eq "general") { continue }
    if (-not $category.tools -or $category.tools.Count -eq 0) { continue }

    $cmdContent += "echo.`necho   $($category.name):`n"

    foreach ($tool in $category.tools) {
        if (-not $tool.script) { continue }

        $cmdDisplay = $tool.command
        # Add aliases in parentheses
        if ($tool.aliases -and $tool.aliases.Count -gt 0) {
            $aliasStr = $tool.aliases -join ", "
            $cmdDisplay = "$($tool.command) ($aliasStr)"
        }
        $argsDisplay = if ($tool.args) { " $($tool.args)" } else { "" }
        $fullCmd = "$cmdDisplay$argsDisplay"
        # Escape special CMD characters
        $fullCmd = $fullCmd -replace '<', '^<'
        $fullCmd = $fullCmd -replace '>', '^>'
        $fullCmd = $fullCmd -replace '\|', '^|'
        # Truncate if too long for CMD echo
        if ($fullCmd.Length -gt 70) {
            $fullCmd = $fullCmd.Substring(0, 67) + "..."
        }
        $cmdContent += "echo   $fullCmd`necho       $($tool.description)`n"
    }
}

$cmdContent += @"
echo.
echo   General:
echo   help                  Show this help
echo   version               Show version
echo.
echo Examples:
echo   devtools shell
echo   devtools shell "C:\My Projects\App"
echo   devtools stats
echo   devtools build
echo.
goto :eof

:: =============================================================================
:: Helper functions
:: =============================================================================

:check_docker
docker info >nul 2>&1
if errorlevel 1 echo [ERROR] Docker is not running. Please start Docker Desktop first. & exit /b 1
goto :eof

:ensure_image
docker image inspect %IMAGE_NAME% >nul 2>&1
if errorlevel 1 call :build
goto :eof
"@

# Write output file - CMD requires CRLF line endings
$cmdContent = $cmdContent -replace "`r`n", "`n" -replace "`n", "`r`n"
[System.IO.File]::WriteAllText($OutputPath, $cmdContent, [System.Text.Encoding]::ASCII)

Write-Host "[OK] Generated $OutputPath with $($tools.Count) tools" -ForegroundColor Green
