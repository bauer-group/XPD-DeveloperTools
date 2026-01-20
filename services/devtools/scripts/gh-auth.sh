#!/bin/bash
# @name: gh-auth
# @description: Manage GitHub CLI authentication (persistent)
# @category: github
# @usage: gh-auth.sh [login|logout|status|refresh|switch]
# =============================================================================
# gh-auth.sh - GitHub Authentication Manager
# Manages persistent GitHub CLI authentication stored in /data
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# Data directory (mounted from host)
DATA_DIR="/data"
GH_CONFIG_DIR="$DATA_DIR/gh"

# Required scopes for all DevTools scripts
# - repo: Full control of private repositories (most operations)
# - admin:org: Manage org settings, billing, runners (gh-actions-usage, gh-runners-selfhosted-status)
# - read:org: Read org membership
# - workflow: Update GitHub Actions workflows
# - read:packages: Read packages (gh-packages-cleanup)
# - delete:packages: Delete packages (gh-packages-cleanup)
# - write:packages: Write packages
# - admin:repo_hook: Manage webhooks (gh-add-workflow, gh-webhook-manager)
REQUIRED_SCOPES="repo,admin:org,read:org,workflow,read:packages,delete:packages,write:packages,admin:repo_hook"

usage() {
    echo ""
    echo -e "${BOLD}gh-auth.sh${NC} - GitHub Authentication Manager"
    echo ""
    echo -e "${BOLD}Usage:${NC}"
    echo "  gh-auth.sh <command> [OPTIONS]"
    echo ""
    echo -e "${BOLD}Commands:${NC}"
    echo "  login           Login to GitHub (interactive, with all required scopes)"
    echo "  login-token     Login with a Personal Access Token"
    echo "  refresh         Add missing scopes to existing authentication"
    echo "  logout          Logout from GitHub"
    echo "  status          Show current authentication status"
    echo "  switch          Switch between GitHub accounts"
    echo "  token           Display current token (for debugging)"
    echo ""
    echo -e "${BOLD}Required Scopes:${NC}"
    echo "  repo, admin:org, read:org, workflow, read:packages, delete:packages, write:packages, admin:repo_hook"
    echo ""
    echo -e "${BOLD}Options:${NC}"
    echo "  -h, --help      Show this help"
    echo ""
    echo -e "${BOLD}Examples:${NC}"
    echo "  gh-auth.sh login              # Interactive login (browser)"
    echo "  gh-auth.sh login-token        # Login with PAT"
    echo "  gh-auth.sh refresh            # Add missing scopes"
    echo "  gh-auth.sh status             # Check auth status"
    echo "  gh-auth.sh logout             # Remove credentials"
    echo ""
    echo -e "${BOLD}Note:${NC}"
    echo "  Credentials are stored in /data/gh (mounted from host .data/)"
    echo "  and persist across container restarts."
    echo ""
}

# Ensure data directory exists
ensure_data_dir() {
    if [ ! -d "$DATA_DIR" ]; then
        echo -e "${RED}[ERROR] Data directory not mounted${NC}"
        echo "Make sure to start the container with the .data volume mounted."
        exit 1
    fi

    mkdir -p "$GH_CONFIG_DIR"
    export GH_CONFIG_DIR
}

# Login interactively
do_login() {
    echo ""
    echo -e "${BOLD}${CYAN}GitHub Login${NC}"
    echo ""

    echo -e "${YELLOW}Device Code Authentication${NC}"
    echo ""
    echo -e "Since this is a container environment without a browser,"
    echo -e "you'll use the device code flow:"
    echo ""
    echo -e "  1. A one-time code will be displayed below"
    echo -e "  2. Open ${CYAN}https://github.com/login/device${NC} in your browser"
    echo -e "  3. Enter the code to authenticate"
    echo ""
    echo -e "${CYAN}Requesting scopes:${NC} $REQUIRED_SCOPES"
    echo ""
    read -rp "Press Enter to continue..."
    echo ""

    # Use web login with device code flow and all required scopes
    # Run without filtering to ensure device code is visible
    gh auth login --git-protocol https --web --scopes "$REQUIRED_SCOPES" 2>&1 || true

    echo ""
    echo -e "${GREEN}Login successful!${NC}"
    show_status
}

# Login with token
do_login_token() {
    echo ""
    echo -e "${BOLD}${CYAN}GitHub Login with Token${NC}"
    echo ""

    echo -e "Enter your Personal Access Token (PAT):"
    echo -e "${YELLOW}(Create one at: https://github.com/settings/tokens)${NC}"
    echo ""
    echo -e "${CYAN}Required scopes:${NC}"
    echo "  - repo           : Full control of private repositories"
    echo "  - admin:org      : Manage org settings, billing, runners"
    echo "  - read:org       : Read org membership"
    echo "  - workflow       : Update GitHub Actions workflows"
    echo "  - read:packages  : Read packages"
    echo "  - delete:packages: Delete packages"
    echo "  - write:packages : Write packages"
    echo "  - admin:repo_hook: Manage webhooks"
    echo ""

    read -rsp "Token: " TOKEN
    echo ""

    if [ -z "$TOKEN" ]; then
        echo -e "${RED}[ERROR] No token provided${NC}"
        exit 1
    fi

    echo "$TOKEN" | gh auth login --with-token

    echo ""
    echo -e "${GREEN}Login successful!${NC}"
    show_status
}

# Refresh scopes (add missing scopes to existing login)
do_refresh() {
    echo ""
    echo -e "${BOLD}${CYAN}Refresh GitHub Scopes${NC}"
    echo ""

    if ! gh auth status &>/dev/null; then
        echo -e "${RED}[ERROR] Not logged in. Please login first.${NC}"
        exit 1
    fi

    echo -e "${CYAN}Current authentication:${NC}"
    gh auth status 2>&1 | head -5
    echo ""

    echo -e "${YELLOW}This will re-authenticate with additional scopes.${NC}"
    echo -e "${CYAN}Requesting scopes:${NC} $REQUIRED_SCOPES"
    echo ""
    echo -e "Since this is a container environment without a browser,"
    echo -e "you'll use the device code flow:"
    echo ""
    echo -e "  1. A one-time code will be displayed below"
    echo -e "  2. Open ${CYAN}https://github.com/login/device${NC} in your browser"
    echo -e "  3. Enter the code to grant the new permissions"
    echo ""
    read -rp "Press Enter to continue..."
    echo ""

    # Use login with scopes (same as do_login, works with device code flow)
    # Run without filtering to ensure device code is visible
    gh auth login --git-protocol https --web --scopes "$REQUIRED_SCOPES" 2>&1 || true

    echo ""
    echo -e "${GREEN}Scopes refreshed!${NC}"
    show_status
}

# Logout
do_logout() {
    echo ""
    echo -e "${BOLD}${CYAN}GitHub Logout${NC}"
    echo ""

    if gh auth status &>/dev/null; then
        # Use -h to specify host and avoid interactive prompt
        gh auth logout -h github.com
        echo -e "${GREEN}Logged out successfully${NC}"
    else
        echo -e "${YELLOW}Not currently logged in${NC}"
    fi

    echo ""
}

# Show status
show_status() {
    echo ""
    echo -e "${BOLD}${CYAN}GitHub Authentication Status${NC}"
    echo ""

    if gh auth status 2>&1; then
        echo ""
        echo -e "${CYAN}Logged in user:${NC}"
        gh api user --jq '.login + " (" + .name + ")"' 2>/dev/null || echo "  (unable to fetch user info)"

        echo ""
        echo -e "${CYAN}Config location:${NC} $GH_CONFIG_DIR"

        # Check for required scopes by testing package access
        echo ""
        echo -e "${CYAN}Scope check:${NC}"
        if gh api /user/packages?package_type=container &>/dev/null; then
            echo -e "  ${GREEN}✓${NC} read:packages"
        else
            echo -e "  ${RED}✗${NC} read:packages (run: gh-auth.sh refresh)"
        fi

        # Test repo access
        if gh api /user/repos?per_page=1 &>/dev/null; then
            echo -e "  ${GREEN}✓${NC} repo"
        else
            echo -e "  ${RED}✗${NC} repo"
        fi

        # Test org access
        if gh api /user/orgs?per_page=1 &>/dev/null; then
            echo -e "  ${GREEN}✓${NC} read:org"
        else
            echo -e "  ${RED}✗${NC} read:org"
        fi

        # Test admin:org (billing access)
        # Note: This may fail if user is not org admin, even with scope
        if gh api /orgs/bauer-group/settings/billing/actions &>/dev/null; then
            echo -e "  ${GREEN}✓${NC} admin:org (billing)"
        else
            echo -e "  ${YELLOW}?${NC} admin:org (billing - may need org admin role)"
        fi

        # Test runners access
        if gh api /orgs/bauer-group/actions/runners &>/dev/null; then
            echo -e "  ${GREEN}✓${NC} admin:org (runners)"
        else
            echo -e "  ${YELLOW}?${NC} admin:org (runners - may need org admin role)"
        fi
    fi

    echo ""
}

# Switch accounts
do_switch() {
    echo ""
    echo -e "${BOLD}${CYAN}Switch GitHub Account${NC}"
    echo ""

    # Show current accounts
    echo -e "${CYAN}Current accounts:${NC}"
    gh auth status 2>&1 | grep -E "Logged in|account" || echo "  No accounts found"
    echo ""

    echo "To switch accounts:"
    echo "  1. Run: gh-auth.sh logout"
    echo "  2. Run: gh-auth.sh login"
    echo ""
}

# Show token (for debugging)
show_token() {
    echo ""
    echo -e "${BOLD}${CYAN}GitHub Token${NC}"
    echo ""

    if gh auth status &>/dev/null; then
        echo -e "${YELLOW}WARNING: This displays your authentication token!${NC}"
        read -rp "Continue? (y/N): " CONFIRM

        if [[ "$CONFIRM" =~ ^[yY]$ ]]; then
            gh auth token
        fi
    else
        echo -e "${RED}Not logged in${NC}"
    fi

    echo ""
}

# Parse arguments
COMMAND=""

while [[ $# -gt 0 ]]; do
    case $1 in
        login)
            COMMAND="login"
            shift
            ;;
        login-token)
            COMMAND="login-token"
            shift
            ;;
        refresh)
            COMMAND="refresh"
            shift
            ;;
        logout)
            COMMAND="logout"
            shift
            ;;
        status)
            COMMAND="status"
            shift
            ;;
        switch)
            COMMAND="switch"
            shift
            ;;
        token)
            COMMAND="token"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown command: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Default to status if no command
if [ -z "$COMMAND" ]; then
    COMMAND="status"
fi

# Setup
ensure_data_dir

# Execute command
case $COMMAND in
    login)
        do_login
        ;;
    login-token)
        do_login_token
        ;;
    refresh)
        do_refresh
        ;;
    logout)
        do_logout
        ;;
    status)
        show_status
        ;;
    switch)
        do_switch
        ;;
    token)
        show_token
        ;;
esac
