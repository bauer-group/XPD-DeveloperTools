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
if /i "%CMD%"=="git-authors-fix" goto script
if /i "%CMD%"=="git-branch-rename" goto script
if /i "%CMD%"=="branch-rename" goto script
if /i "%CMD%"=="git-changelog" goto script
if /i "%CMD%"=="changelog" goto script
if /i "%CMD%"=="git-cleanup" goto script
if /i "%CMD%"=="cleanup" goto script
if /i "%CMD%"=="git-contributors" goto script
if /i "%CMD%"=="contributors" goto script
if /i "%CMD%"=="git-find-large-files" goto script
if /i "%CMD%"=="git-history-clean" goto script
if /i "%CMD%"=="history-clean" goto script
if /i "%CMD%"=="git-lfs-migrate" goto script
if /i "%CMD%"=="lfs-migrate" goto script
if /i "%CMD%"=="lfs" goto script
if /i "%CMD%"=="git-mirror" goto script
if /i "%CMD%"=="mirror" goto script
if /i "%CMD%"=="git-release" goto script
if /i "%CMD%"=="release" goto script
if /i "%CMD%"=="git-rewrite-commits" goto script
if /i "%CMD%"=="rewrite-commits" goto script
if /i "%CMD%"=="git-split-repo" goto script
if /i "%CMD%"=="split-repo" goto script
if /i "%CMD%"=="git-squash-history" goto script
if /i "%CMD%"=="git-stats" goto script
if /i "%CMD%"=="stats" goto script
if /i "%CMD%"=="gh-actions-usage" goto script
if /i "%CMD%"=="gh-add-workflow" goto script
if /i "%CMD%"=="gh-archive-repos" goto script
if /i "%CMD%"=="gh-archive" goto script
if /i "%CMD%"=="gh-auth" goto script
if /i "%CMD%"=="gh-branch-protection" goto script
if /i "%CMD%"=="gh-branch-protect" goto script
if /i "%CMD%"=="gh-clean-releases" goto script
if /i "%CMD%"=="gh-clone-org" goto script
if /i "%CMD%"=="gh-codeowners-sync" goto script
if /i "%CMD%"=="gh-create-repo" goto script
if /i "%CMD%"=="gh-create" goto script
if /i "%CMD%"=="gh-dependabot-labels" goto script
if /i "%CMD%"=="gh-environments-audit" goto script
if /i "%CMD%"=="gh-labels-sync" goto script
if /i "%CMD%"=="gh-license-audit" goto script
if /i "%CMD%"=="gh-packages-cleanup" goto script
if /i "%CMD%"=="gh-pr-cleanup" goto script
if /i "%CMD%"=="gh-prefix-labels" goto script
if /i "%CMD%"=="gh-repo-settings" goto script
if /i "%CMD%"=="gh-runners-selfhosted-status" goto script
if /i "%CMD%"=="gh-secrets-audit" goto script
if /i "%CMD%"=="gh-stale-branches" goto script
if /i "%CMD%"=="gh-sync-forks" goto script
if /i "%CMD%"=="gh-template-sync" goto script
if /i "%CMD%"=="gh-topic-manager" goto script
if /i "%CMD%"=="gh-topics" goto script
if /i "%CMD%"=="gh-trigger-workflow" goto script
if /i "%CMD%"=="gh-workflow" goto script
if /i "%CMD%"=="gh-visibility" goto script
if /i "%CMD%"=="gh-webhook-manager" goto script
if /i "%CMD%"=="claude-backup" goto native_script
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
if /i "%CMD%"=="git-authors-fix" set "S=git-authors-fix.py"
if /i "%CMD%"=="git-branch-rename" set "S=git-branch-rename.sh"
if /i "%CMD%"=="branch-rename" set "S=git-branch-rename.sh"
if /i "%CMD%"=="git-changelog" set "S=git-changelog.py"
if /i "%CMD%"=="changelog" set "S=git-changelog.py"
if /i "%CMD%"=="git-cleanup" set "S=git-cleanup.sh"
if /i "%CMD%"=="cleanup" set "S=git-cleanup.sh"
if /i "%CMD%"=="git-contributors" set "S=git-contributors.py"
if /i "%CMD%"=="contributors" set "S=git-contributors.py"
if /i "%CMD%"=="git-find-large-files" set "S=git-find-large-files.py"
if /i "%CMD%"=="git-history-clean" set "S=git-history-clean.sh"
if /i "%CMD%"=="history-clean" set "S=git-history-clean.sh"
if /i "%CMD%"=="git-lfs-migrate" set "S=git-lfs-migrate.sh"
if /i "%CMD%"=="lfs-migrate" set "S=git-lfs-migrate.sh"
if /i "%CMD%"=="lfs" set "S=git-lfs-migrate.sh"
if /i "%CMD%"=="git-mirror" set "S=git-mirror.sh"
if /i "%CMD%"=="mirror" set "S=git-mirror.sh"
if /i "%CMD%"=="git-release" set "S=git-release.py"
if /i "%CMD%"=="release" set "S=git-release.py"
if /i "%CMD%"=="git-rewrite-commits" set "S=git-rewrite-commits.py"
if /i "%CMD%"=="rewrite-commits" set "S=git-rewrite-commits.py"
if /i "%CMD%"=="git-split-repo" set "S=git-split-repo.py"
if /i "%CMD%"=="split-repo" set "S=git-split-repo.py"
if /i "%CMD%"=="git-squash-history" set "S=git-squash-history.py"
if /i "%CMD%"=="git-stats" set "S=git-stats.sh"
if /i "%CMD%"=="stats" set "S=git-stats.sh"
if /i "%CMD%"=="gh-actions-usage" set "S=gh-actions-usage.py"
if /i "%CMD%"=="gh-add-workflow" set "S=gh-add-workflow.py"
if /i "%CMD%"=="gh-archive-repos" set "S=gh-archive-repos.py"
if /i "%CMD%"=="gh-archive" set "S=gh-archive-repos.py"
if /i "%CMD%"=="gh-auth" set "S=gh-auth.sh"
if /i "%CMD%"=="gh-branch-protection" set "S=gh-branch-protection.py"
if /i "%CMD%"=="gh-branch-protect" set "S=gh-branch-protection.py"
if /i "%CMD%"=="gh-clean-releases" set "S=gh-clean-releases.py"
if /i "%CMD%"=="gh-clone-org" set "S=gh-clone-org.sh"
if /i "%CMD%"=="gh-codeowners-sync" set "S=gh-codeowners-sync.py"
if /i "%CMD%"=="gh-create-repo" set "S=gh-create-repo.sh"
if /i "%CMD%"=="gh-create" set "S=gh-create-repo.sh"
if /i "%CMD%"=="gh-dependabot-labels" set "S=gh-dependabot-labels.py"
if /i "%CMD%"=="gh-environments-audit" set "S=gh-environments-audit.py"
if /i "%CMD%"=="gh-labels-sync" set "S=gh-labels-sync.py"
if /i "%CMD%"=="gh-license-audit" set "S=gh-license-audit.py"
if /i "%CMD%"=="gh-packages-cleanup" set "S=gh-packages-cleanup.py"
if /i "%CMD%"=="gh-pr-cleanup" set "S=gh-pr-cleanup.py"
if /i "%CMD%"=="gh-prefix-labels" set "S=gh-prefix-labels.py"
if /i "%CMD%"=="gh-repo-settings" set "S=gh-repo-settings.py"
if /i "%CMD%"=="gh-runners-selfhosted-status" set "S=gh-runners-selfhosted-status.py"
if /i "%CMD%"=="gh-secrets-audit" set "S=gh-secrets-audit.py"
if /i "%CMD%"=="gh-stale-branches" set "S=gh-stale-branches.py"
if /i "%CMD%"=="gh-sync-forks" set "S=gh-sync-forks.py"
if /i "%CMD%"=="gh-template-sync" set "S=gh-template-sync.py"
if /i "%CMD%"=="gh-topic-manager" set "S=gh-topic-manager.py"
if /i "%CMD%"=="gh-topics" set "S=gh-topic-manager.py"
if /i "%CMD%"=="gh-trigger-workflow" set "S=gh-trigger-workflow.sh"
if /i "%CMD%"=="gh-workflow" set "S=gh-trigger-workflow.sh"
if /i "%CMD%"=="gh-visibility" set "S=gh-visibility.py"
if /i "%CMD%"=="gh-webhook-manager" set "S=gh-webhook-manager.py"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
docker run --rm -v "%CD%:/workspace" -v "%DATA_DIR%:/data" -w /workspace %IMAGE_NAME% /bin/bash -lc "%S% %~2 %~3 %~4 %~5"
goto :eof

:: =============================================================================
:native_script
:: =============================================================================
set "NS="
if /i "%CMD%"=="claude-backup" set "NS=claude-backup.py"
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
echo   - 41 tools from tools.json
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
echo.
echo   Runtime Container:
echo.
echo   Git Tools:
echo   git-authors-fix --old-email ^<email^> --new-name ^<name^> --new-ema...
echo       Fix author name and email in git history
echo   git-branch-rename (branch-rename) ^<old-name^> ^<new-name^> [--upda...
echo       Rename git branches (local + remote)
echo   git-changelog (changelog) [--format md^|json] [--from ^<tag^>]
echo       Generate changelog from git commits
echo   git-cleanup (cleanup) [--dry-run] [--all]
echo       Clean up branches, cache and optimize repository
echo   git-contributors (contributors) [--since ^<date^>] [--format table^...
echo       Show contributor statistics for a repository
echo   git-find-large-files [--size ^<min-size^>] [--top ^<n^>]
echo       Find large files in git history
echo   git-history-clean (history-clean) [--size 10M] [--dry-run]
echo       Remove large files from git history
echo   git-lfs-migrate (lfs-migrate, lfs) [--patterns "*.zip,*.bin"] [--dr...
echo       Migrate repository to Git LFS for binary files
echo   git-mirror (mirror) ^<source-url^> ^<target-url^>
echo       Mirror repository between git servers
echo   git-release (release) [major^|minor^|patch] [--dry-run]
echo       Manage releases with semantic versioning
echo   git-rewrite-commits (rewrite-commits) [--pattern "..."] [--dry-run]
echo       Rewrite commit messages based on patterns
echo   git-split-repo (split-repo) ^<folder^> [--target ^<url^>]
echo       Split monorepo into separate repositories
echo   git-squash-history [--before ^<date^>] [--keep-recent ^<n^>]
echo       Squash old git history to reduce repository size
echo   git-stats (stats)
echo       Show repository statistics
echo.
echo   GitHub Tools:
echo   gh-actions-usage [-o ^<org^>] [--detailed]
echo       Show GitHub Actions usage and billing across organization
echo   gh-add-workflow ^<workflow-file^> [--topic ^<topic^>] [--repos ^<li...
echo       Add workflow files to GitHub repositories
echo   gh-archive-repos (gh-archive) [--topic ^<topic^>] [--older-than ^<d...
echo       Archive GitHub repositories
echo   gh-auth [login^|logout^|status^|refresh^|switch]
echo       Manage GitHub CLI authentication (persistent)
echo   gh-branch-protection (gh-branch-protect) ^<repo^> [--branch ^<name^...
echo       Manage branch protection rules
echo   gh-clean-releases [--repo ^<name^>] [--keep-latest ^<n^>]
echo       Clean GitHub releases and tags
echo   gh-clone-org ^<org-name^> [--topic ^<topic^>] [--archived]
echo       Clone all repositories from a GitHub organization
echo   gh-codeowners-sync [-o ^<org^>] --source ^<repo^> [--execute]
echo       Sync CODEOWNERS file across organization repositories
echo   gh-create-repo (gh-create) ^<name^> [-d "desc"] [-t topics] [-u url...
echo       Create a new GitHub repository with full configuration
echo   gh-dependabot-labels [-o ^<org^>] [--execute] [--cleanup]
echo       Sync labels from dependabot.yml across organization repos
echo   gh-environments-audit [-o ^<org^>]
echo       Audit deployment environments across organization repositories
echo   gh-labels-sync ^<source-repo^> ^<target-repo^>
echo       Sync labels between GitHub repositories
echo   gh-license-audit [-o ^<org^>] [--missing-only]
echo       Audit license files across organization repositories
echo   gh-packages-cleanup [-o ^<org^>] [-t ^<type^>] [--execute]
echo       Clean old package versions from GitHub Packages
echo   gh-pr-cleanup [--repo ^<name^>] [--older-than ^<days^>]
echo       Clean up stale pull requests and branches
echo   gh-prefix-labels [-o ^<org^>] [--execute] [--config ^<path^>]
echo       Assign topics to repos based on name prefix (configurable via JSON)
echo   gh-repo-settings [-o ^<org^>] [--topic ^<topic^>] [--execute]
echo       Manage repository settings across organization
echo   gh-runners-selfhosted-status [-o ^<org^>]
echo       Show self-hosted runner status across organization
echo   gh-secrets-audit [--org ^<name^>] [--repos ^<list^>]
echo       Audit GitHub repository secrets
echo   gh-stale-branches [-o ^<org^>] [--days ^<n^>] [--delete]
echo       Find stale branches across organization repositories
echo   gh-sync-forks [--repo ^<name^>] [--all]
echo       Sync forked repositories with upstream
echo   gh-template-sync [-o ^<org^>] --source ^<repo^> [--execute]
echo       Sync issue and PR templates across organization repositories
echo   gh-topic-manager (gh-topics) ^<repo^> [--add ^<topics^>] [--remove ...
echo       Manage GitHub repository topics
echo   gh-trigger-workflow (gh-workflow) ^<repo^> ^<workflow^> [--ref ^<br...
echo       Trigger GitHub Actions workflows manually
echo   gh-visibility ^<repo^> [--public^|--private^|--internal]
echo       Change GitHub repository visibility
echo   gh-webhook-manager [-o ^<org^>] [--list^|--add^|--delete] [--execute]
echo       Manage webhooks across organization repositories
echo.
echo   Local Tools:
echo   claude-backup [backup^|restore^|list] [--keep ^<n^>]
echo       Backup and restore Claude Code configuration
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