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
TOOLS_CONFIG="$SCRIPT_DIR/tools.json"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'
BOLD='\033[1m'

# Check if jq is available (for JSON parsing)
check_jq() {
    if ! command -v jq &> /dev/null; then
        echo -e "${RED}[ERROR] jq is required but not installed.${NC}"
        echo "Install with: brew install jq (macOS) or apt install jq (Linux)"
        exit 1
    fi
}

# Find script for a command
find_script() {
    local cmd="$1"
    check_jq

    if [[ ! -f "$TOOLS_CONFIG" ]]; then
        echo ""
        return
    fi

    # Search by command
    local script
    script=$(jq -r --arg cmd "$cmd" '
        .categories[].tools[] |
        select(.command == $cmd or (.aliases // [] | index($cmd) != null)) |
        .script // ""
    ' "$TOOLS_CONFIG" 2>/dev/null | head -1)

    echo "$script"
}

# Hilfe anzeigen
show_help() {
    check_jq

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

    # Parse JSON and display help dynamically
    if [[ -f "$TOOLS_CONFIG" ]]; then
        jq -r '
            .categories[] |
            select(.id != "general") |
            "  \u001b[0;36m\(.name):\u001b[0m",
            (.tools[] |
                .command + (if .aliases then " (" + (.aliases | join(", ")) + ")" else "" end) + (if .args then " " + .args else "" end) as $cmd |
                (50 - ($cmd | length)) as $pad |
                "    " + $cmd + (" " * (if $pad > 0 then $pad else 1 end)) + .description
            ),
            ""
        ' "$TOOLS_CONFIG" 2>/dev/null || echo "  (tools.json not found or invalid)"
    fi

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

# Version
show_version() {
    check_jq

    local tool_count=0
    local cat_count=0

    if [[ -f "$TOOLS_CONFIG" ]]; then
        tool_count=$(jq '[.categories[].tools[]] | length' "$TOOLS_CONFIG" 2>/dev/null || echo "0")
        cat_count=$(jq '.categories | length' "$TOOLS_CONFIG" 2>/dev/null || echo "0")
    fi

    echo -e "${BOLD}DevTools${NC} v1.0.0"
    echo "Swiss Army Knife for Git-based Development"
    echo ""
    echo "Components:"
    echo "  - DevTools Runtime Container (Git, Python, Shell)"
    echo "  - $tool_count tools across $cat_count categories"
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
        help|--help|-h)
            show_help
            ;;
        version|--version|-v)
            show_version
            ;;
        stats)
            local project_path="${1:-$(pwd)}"
            shift || true
            PROJECT_PATH="$project_path" run_script "git-stats.sh" "$@"
            ;;
        cleanup)
            local project_path="${1:-$(pwd)}"
            shift || true
            PROJECT_PATH="$project_path" run_script "git-cleanup.sh" "$@"
            ;;
        *)
            # Dynamic lookup
            local script
            script=$(find_script "$cmd")

            if [[ -n "$script" && "$script" != "null" ]]; then
                run_script "$script" "$@"
            else
                echo -e "${RED}Unknown command: $cmd${NC}"
                show_help
                exit 1
            fi
            ;;
    esac
}

main "$@"
