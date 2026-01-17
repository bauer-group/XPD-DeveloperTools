#!/bin/bash
# =============================================================================
# DevTools - Swiss Army Knife for Git-based Development
# Runtime Container für Git-Operationen und Entwicklungstools
# =============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="bauer-devtools"
CONTAINER_NAME="devtools-runtime"
DATA_DIR="$SCRIPT_DIR/.data"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'
BOLD='\033[1m'

# Hilfe anzeigen
show_help() {
    echo ""
    echo -e "${BOLD}${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${BLUE}║              DevTools - Developer Swiss Army Knife            ║${NC}"
    echo -e "${BOLD}${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BOLD}Usage:${NC}"
    echo "  $0 <command> [options]"
    echo ""
    echo -e "${BOLD}Commands:${NC}"
    echo ""
    echo -e "  ${CYAN}Runtime Container:${NC}"
    echo "    shell [PROJECT_PATH]    Start interactive shell in DevTools container"
    echo "    run <script> [args]     Run a script in the container"
    echo "    build                   Build/rebuild the DevTools container"
    echo ""
    echo -e "  ${CYAN}Git Tools (via container):${NC}"
    echo "    stats [PROJECT_PATH]    Show repository statistics"
    echo "    cleanup [PROJECT_PATH]  Clean up branches and cache"
    echo "    changelog [options]     Generate changelog"
    echo "    release [options]       Manage releases"
    echo "    lfs-migrate [options]   Migrate repository to Git LFS"
    echo "    history-clean [opts]    Remove large files from git history"
    echo "    branch-rename [opts]    Rename git branches (local + remote)"
    echo "    split-repo [options]    Split monorepo into separate repos"
    echo "    rewrite-commits [opts]  Rewrite commit messages (pattern-based)"
    echo ""
    echo -e "  ${CYAN}GitHub Tools (via container):${NC}"
    echo "    gh-create [options]     Create GitHub repository"
    echo "    gh-topics [options]     Manage repository topics"
    echo "    gh-archive [options]    Archive repositories"
    echo "    gh-workflow [options]   Trigger GitHub Actions workflows"
    echo "    gh-add-workflow [opts]  Add workflow files to repos"
    echo "    gh-clean-releases       Clean releases and tags"
    echo "    gh-visibility [opts]    Change repo visibility (public/private)"
    echo "    gh-clone-org [opts]     Clone all repos from organization"
    echo "    gh-sync-forks [opts]    Sync forked repos with upstream"
    echo "    gh-pr-cleanup [opts]    Clean stale PRs and branches"
    echo "    gh-secrets-audit [opts] Audit secrets across repos"
    echo "    gh-labels-sync [opts]   Sync labels between repos"
    echo "    gh-branch-protect [opt] Manage branch protection rules"
    echo "    gh-packages-cleanup     Clean old package versions"
    echo ""
    echo -e "  ${CYAN}Git Advanced Tools (via container):${NC}"
    echo "    git-mirror [options]    Mirror repo between servers"
    echo "    git-contributors [opts] Show contributor statistics"
    echo ""
    echo -e "  ${CYAN}General:${NC}"
    echo "    help                    Show this help"
    echo "    version                 Show version info"
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo "  $0 shell                          # Shell im aktuellen Verzeichnis"
    echo "  $0 shell /path/to/project         # Shell in einem anderen Projekt"
    echo "  $0 stats                          # Repository-Statistiken"
    echo "  $0 run git-cleanup.sh --dry-run   # Script ausführen"
    echo ""
    echo -e "${BOLD}Note:${NC}"
    echo "  Für Dozzle (Container Monitor) siehe: services/dozzle/"
    echo ""
}

# Docker prüfen
check_docker() {
    if ! docker info &> /dev/null; then
        echo -e "${RED}[ERROR] Docker is not running. Please start Docker first.${NC}"
        exit 1
    fi
}

# Image bauen falls nötig
ensure_image() {
    if ! docker image inspect "$IMAGE_NAME" &> /dev/null; then
        echo -e "${CYAN}[INFO] Building DevTools container...${NC}"
        build_image
    fi
}

# Image bauen
build_image() {
    echo -e "${CYAN}[INFO] Building DevTools image...${NC}"
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR/services/devtools"
    echo -e "${GREEN}[OK] Image built successfully${NC}"
}

# Container starten (interaktiv)
start_shell() {
    local project_path="${1:-$(pwd)}"

    # Absoluten Pfad sicherstellen
    project_path="$(cd "$project_path" 2>/dev/null && pwd)"

    if [ ! -d "$project_path" ]; then
        echo -e "${RED}[ERROR] Directory not found: $project_path${NC}"
        exit 1
    fi

    check_docker
    ensure_image

    echo -e "${CYAN}[INFO] Starting DevTools shell...${NC}"
    echo -e "${CYAN}[INFO] Mounting: $project_path${NC}"

    # Git-Konfiguration vom Host übernehmen
    local git_name git_email
    git_name=$(git config --global user.name 2>/dev/null || echo "")
    git_email=$(git config --global user.email 2>/dev/null || echo "")

    local project_name
    project_name=$(basename "$project_path")

    # Ensure data directory exists
    mkdir -p "$DATA_DIR"

    docker run -it --rm \
        --name "$CONTAINER_NAME" \
        -v "$project_path:/workspace" \
        -v "$DATA_DIR:/data" \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -e "GIT_USER_NAME=$git_name" \
        -e "GIT_USER_EMAIL=$git_email" \
        -e "PROJECT_NAME=$project_name" \
        -w /workspace \
        "$IMAGE_NAME"
}

# Script im Container ausführen
run_script() {
    local script="$1"
    shift
    local project_path="${PROJECT_PATH:-$(pwd)}"

    check_docker
    ensure_image

    echo -e "${CYAN}[INFO] Running: $script $*${NC}"

    # Ensure data directory exists
    mkdir -p "$DATA_DIR"

    docker run --rm \
        -v "$project_path:/workspace" \
        -v "$DATA_DIR:/data" \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -w /workspace \
        "$IMAGE_NAME" \
        /bin/bash -lc "$script $*"
}

# Git-Statistiken
git_stats() {
    local project_path="${1:-$(pwd)}"
    PROJECT_PATH="$project_path" run_script "git-stats.sh"
}

# Git-Cleanup
git_cleanup() {
    local project_path="${1:-$(pwd)}"
    shift || true
    PROJECT_PATH="$project_path" run_script "git-cleanup.sh" "$@"
}

# Changelog generieren
git_changelog() {
    run_script "git-changelog.py" "$@"
}

# Release verwalten
git_release() {
    run_script "git-release.py" "$@"
}

# LFS Migration
git_lfs_migrate() {
    run_script "git-lfs-migrate.sh" "$@"
}

# History Clean
git_history_clean() {
    run_script "git-history-clean.sh" "$@"
}

# Branch Rename
git_branch_rename() {
    run_script "git-branch-rename.sh" "$@"
}

# Split Repo
git_split_repo() {
    run_script "git-split-repo.py" "$@"
}

# Rewrite Commits
git_rewrite_commits() {
    run_script "git-rewrite-commits.py" "$@"
}

# GitHub Create Repo
gh_create_repo() {
    run_script "gh-create-repo.sh" "$@"
}

# GitHub Topic Manager
gh_topic_manager() {
    run_script "gh-topic-manager.py" "$@"
}

# GitHub Archive Repos
gh_archive_repos() {
    run_script "gh-archive-repos.py" "$@"
}

# GitHub Trigger Workflow
gh_trigger_workflow() {
    run_script "gh-trigger-workflow.sh" "$@"
}

# GitHub Add Workflow
gh_add_workflow() {
    run_script "gh-add-workflow.py" "$@"
}

# GitHub Clean Releases
gh_clean_releases() {
    run_script "gh-clean-releases.py" "$@"
}

# GitHub Visibility
gh_visibility() {
    run_script "gh-visibility.py" "$@"
}

# GitHub Clone Org
gh_clone_org() {
    run_script "gh-clone-org.sh" "$@"
}

# GitHub Sync Forks
gh_sync_forks() {
    run_script "gh-sync-forks.py" "$@"
}

# GitHub PR Cleanup
gh_pr_cleanup() {
    run_script "gh-pr-cleanup.py" "$@"
}

# GitHub Secrets Audit
gh_secrets_audit() {
    run_script "gh-secrets-audit.py" "$@"
}

# GitHub Labels Sync
gh_labels_sync() {
    run_script "gh-labels-sync.py" "$@"
}

# GitHub Branch Protection
gh_branch_protection() {
    run_script "gh-branch-protection.py" "$@"
}

# GitHub Packages Cleanup
gh_packages_cleanup() {
    run_script "gh-packages-cleanup.py" "$@"
}

# Git Mirror
git_mirror() {
    run_script "git-mirror.sh" "$@"
}

# Git Contributors
git_contributors() {
    run_script "git-contributors.py" "$@"
}

# Version
show_version() {
    echo -e "${BOLD}DevTools${NC} v1.0.0"
    echo "Swiss Army Knife for Git-based Development"
    echo ""
    echo "Components:"
    echo "  - DevTools Runtime Container (Git, Python, Shell)"
    echo "  - Git Tools (stats, cleanup, changelog, release, lfs-migrate, history-clean, branch-rename, split-repo, rewrite-commits, mirror, contributors)"
    echo "  - GitHub Tools (gh-create, gh-topics, gh-archive, gh-workflow, gh-add-workflow, gh-clean-releases, gh-visibility, gh-clone-org, gh-sync-forks, gh-pr-cleanup, gh-secrets-audit, gh-labels-sync, gh-branch-protect, gh-packages-cleanup)"
}

# Hauptlogik
main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        shell)
            start_shell "$@"
            ;;
        run)
            run_script "$@"
            ;;
        build)
            check_docker
            build_image
            ;;
        stats)
            git_stats "$@"
            ;;
        cleanup)
            git_cleanup "$@"
            ;;
        changelog)
            git_changelog "$@"
            ;;
        release)
            git_release "$@"
            ;;
        lfs-migrate|lfs)
            git_lfs_migrate "$@"
            ;;
        history-clean)
            git_history_clean "$@"
            ;;
        branch-rename)
            git_branch_rename "$@"
            ;;
        split-repo)
            git_split_repo "$@"
            ;;
        rewrite-commits)
            git_rewrite_commits "$@"
            ;;
        gh-create)
            gh_create_repo "$@"
            ;;
        gh-topics)
            gh_topic_manager "$@"
            ;;
        gh-archive)
            gh_archive_repos "$@"
            ;;
        gh-workflow)
            gh_trigger_workflow "$@"
            ;;
        gh-add-workflow)
            gh_add_workflow "$@"
            ;;
        gh-clean-releases)
            gh_clean_releases "$@"
            ;;
        gh-visibility)
            gh_visibility "$@"
            ;;
        gh-clone-org)
            gh_clone_org "$@"
            ;;
        gh-sync-forks)
            gh_sync_forks "$@"
            ;;
        gh-pr-cleanup)
            gh_pr_cleanup "$@"
            ;;
        gh-secrets-audit)
            gh_secrets_audit "$@"
            ;;
        gh-labels-sync)
            gh_labels_sync "$@"
            ;;
        gh-branch-protect|gh-branch-protection)
            gh_branch_protection "$@"
            ;;
        gh-packages-cleanup)
            gh_packages_cleanup "$@"
            ;;
        git-mirror|mirror)
            git_mirror "$@"
            ;;
        git-contributors|contributors)
            git_contributors "$@"
            ;;
        version|--version|-v)
            show_version
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            echo -e "${RED}Unknown command: $cmd${NC}"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
