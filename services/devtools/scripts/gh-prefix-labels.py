#!/usr/bin/env python3
# @name: gh-prefix-labels
# @description: Assign topics to repos based on name prefix (configurable via JSON)
# @category: github
# @usage: gh-prefix-labels.py [-o <org>] [--execute] [--config <path>]
"""
gh-prefix-labels.py - Repository Prefix Topic Manager
Assigns topics (tags) to repositories based on their name prefix.
Configuration is stored in prefix-labels.json.
"""

import sys
import json
import subprocess
import argparse
from typing import List, Dict, Set, Optional, Tuple
from pathlib import Path

# Colors
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
NC = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'


def run_gh(args: List[str], capture: bool = True) -> Optional[str]:
    """Run GitHub CLI command."""
    cmd = ["gh"] + args
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        else:
            subprocess.run(cmd, check=True)
            return None
    except subprocess.CalledProcessError as e:
        if capture:
            return None
        raise


def check_gh_auth() -> bool:
    """Check if gh CLI is authenticated."""
    try:
        subprocess.run(["gh", "auth", "status"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_config(config_path: str) -> Dict:
    """Load prefix-labels configuration from JSON file."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"{RED}[ERROR] Config file not found: {config_path}{NC}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"{RED}[ERROR] Invalid JSON in config file: {e}{NC}")
        sys.exit(1)


def get_all_managed_topics(config: Dict) -> Set[str]:
    """Get all topics that are managed by this tool."""
    topics = set()
    for prefix_data in config.get("prefixes", {}).values():
        topics.update(prefix_data.get("labels", []))
    return topics


def get_org_repos(org: str) -> List[Dict]:
    """Get all repositories for an organization."""
    output = run_gh([
        "api", f"/orgs/{org}/repos",
        "--paginate",
        "-q", ".[] | {name: .name, archived: .archived, topics: .topics}"
    ])
    if not output:
        return []

    repos = []
    for line in output.strip().split('\n'):
        if line:
            try:
                repos.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return repos


def set_repo_topics(org: str, repo: str, topics: List[str]) -> bool:
    """Set topics for a repository (replaces all topics)."""
    topics_json = json.dumps({"names": topics})
    try:
        proc = subprocess.run(
            ["gh", "api", f"/repos/{org}/{repo}/topics", "-X", "PUT",
             "-H", "Accept: application/vnd.github+json", "--input", "-"],
            input=topics_json,
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def match_prefix(repo_name: str, prefixes: Dict) -> Tuple[Optional[str], List[str]]:
    """Match repository name against prefixes and return matching topics."""
    repo_upper = repo_name.upper()

    for prefix, data in prefixes.items():
        if repo_upper.startswith(prefix.upper()):
            return prefix, data.get("labels", [])

    return None, []


def extract_prefix(repo_name: str) -> Optional[str]:
    """Extract prefix from repo name (everything before first dash including dash)."""
    if '-' in repo_name:
        parts = repo_name.split('-', 1)
        return parts[0].upper() + '-'
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Assign topics to repos based on name prefix",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run: show what would be changed
  gh-prefix-labels.py

  # Actually apply changes
  gh-prefix-labels.py --execute

  # Remove ALL unmanaged topics (clean slate)
  gh-prefix-labels.py --clean-unmanaged --execute

  # Report unknown prefixes (for config maintenance)
  gh-prefix-labels.py --report-unknown

  # Process specific repo only
  gh-prefix-labels.py --repo CS-MyProject

  # Show current config
  gh-prefix-labels.py --show-config

Configuration:
  Edit config/prefix-labels.json to customize prefix-to-topic mappings.
        """
    )

    parser.add_argument(
        "-o", "--org",
        default="bauer-group",
        help="GitHub organization (default: bauer-group)"
    )
    parser.add_argument(
        "--repo",
        help="Process specific repository only"
    )
    parser.add_argument(
        "--config",
        help="Path to config file (default: config/prefix-labels.json)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes (default: dry-run)"
    )
    parser.add_argument(
        "--clean-unmanaged",
        action="store_true",
        help="Remove all topics not defined in config (use with caution!)"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show current configuration and exit"
    )
    parser.add_argument(
        "--report-unknown",
        action="store_true",
        help="Report prefixes found in repo names that have no config"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    # Determine config path
    if args.config:
        config_path = args.config
    else:
        # Search for config in multiple locations
        possible_paths = [
            # 1. Relative to script
            Path(__file__).parent.parent / "config" / "prefix-labels.json",
            # 2. Workspace mount (repo root at /workspace)
            Path("/workspace/services/devtools/config/prefix-labels.json"),
            # 3. Current working directory
            Path.cwd() / "services/devtools/config/prefix-labels.json",
            Path.cwd() / "config" / "prefix-labels.json",
        ]

        config_path = None
        for path in possible_paths:
            if path.exists():
                config_path = path
                break

        if not config_path:
            print(f"{RED}[ERROR] Config file not found in:{NC}")
            for path in possible_paths:
                print(f"  - {path}")
            print(f"\n{DIM}Use --config <path> to specify location{NC}")
            sys.exit(1)

    # Load configuration
    config = load_config(str(config_path))

    # Show config and exit
    if args.show_config:
        print(f"\n{BOLD}Prefix-Topic Configuration:{NC}\n")
        print(f"Config file: {config_path}")
        print(f"Version: {config.get('version', 'unknown')}")
        print()
        print(f"{BOLD}Prefix Mappings:{NC}")
        for prefix, data in config.get("prefixes", {}).items():
            topics = ", ".join(data.get("labels", []))
            desc = data.get("description", "")
            print(f"  {CYAN}{prefix:<12}{NC} -> {topics}")
            if desc:
                print(f"               {DIM}{desc}{NC}")
        print()
        return

    # Check authentication
    if not check_gh_auth():
        print(f"{RED}[ERROR] GitHub CLI not authenticated{NC}")
        print("Run: gh auth login")
        sys.exit(1)

    # Get all managed topics
    managed_topics = get_all_managed_topics(config)
    prefixes = config.get("prefixes", {})

    # Header
    if not args.json:
        print()
        print(f"{BOLD}{CYAN}+---------------------------------------------------------------+{NC}")
        print(f"{BOLD}{CYAN}|              Repository Prefix Topic Manager                  |{NC}")
        print(f"{BOLD}{CYAN}+---------------------------------------------------------------+{NC}")
        print()

        if not args.execute:
            print(f"{YELLOW}[DRY-RUN] No changes will be made. Use --execute to apply.{NC}")
            print()

    # Get repositories
    if args.repo:
        # Fetch single repo
        output = run_gh([
            "api", f"/repos/{args.org}/{args.repo}",
            "-q", "{name: .name, archived: .archived, topics: .topics}"
        ])
        if output:
            repos = [json.loads(output)]
        else:
            print(f"{RED}[ERROR] Repository not found: {args.repo}{NC}")
            sys.exit(1)
    else:
        if not args.json:
            print(f"Fetching repositories for {BOLD}{args.org}{NC}...")
        repos = get_org_repos(args.org)

    if not args.json:
        print(f"  Found {len(repos)} repositories")
        print()

    # Statistics
    stats = {
        "total": len(repos),
        "matched": 0,
        "unmatched": 0,
        "skipped_archived": 0,
        "topics_added": 0,
        "topics_removed": 0,
        "unmanaged_removed": 0,
        "repos_changed": 0,
        "unchanged": 0
    }

    results = []
    unknown_prefixes = {}  # prefix -> list of repos

    # Process each repository
    for repo in sorted(repos, key=lambda r: r["name"]):
        repo_name = repo["name"]

        # Skip archived repos
        if repo.get("archived"):
            stats["skipped_archived"] += 1
            continue

        # Match prefix
        matched_prefix, target_topics = match_prefix(repo_name, prefixes)
        target_topics_set = set(target_topics)

        # Track unknown prefixes and skip them (no changes for unmatched repos)
        if not matched_prefix:
            stats["unmatched"] += 1
            detected_prefix = extract_prefix(repo_name)
            if detected_prefix:
                if detected_prefix not in unknown_prefixes:
                    unknown_prefixes[detected_prefix] = []
                unknown_prefixes[detected_prefix].append(repo_name)
            continue  # Skip repos without known prefix

        stats["matched"] += 1

        # Get current topics
        current_topics = set(repo.get("topics", []) or [])

        # Separate managed and unmanaged topics
        current_managed = current_topics & managed_topics
        current_unmanaged = current_topics - managed_topics

        # Calculate changes (only for managed topics)
        to_add = target_topics_set - current_managed
        to_remove = current_managed - target_topics_set

        # Handle unmanaged topics
        unmanaged_to_remove = set()
        if args.clean_unmanaged and current_unmanaged:
            unmanaged_to_remove = current_unmanaged

        # Build new topic list
        if args.clean_unmanaged:
            # Remove all unmanaged topics
            new_topics = target_topics_set
        else:
            # Keep unmanaged topics
            new_topics = current_unmanaged | target_topics_set

        result = {
            "repo": repo_name,
            "prefix": matched_prefix,
            "target_topics": list(target_topics_set),
            "current_topics": list(current_topics),
            "new_topics": list(new_topics),
            "added": list(to_add),
            "removed": list(to_remove),
            "unmanaged_removed": list(unmanaged_to_remove)
        }
        results.append(result)

        has_changes = to_add or to_remove or unmanaged_to_remove
        if not has_changes:
            stats["unchanged"] += 1
            continue

        stats["repos_changed"] += 1
        stats["topics_added"] += len(to_add)
        stats["topics_removed"] += len(to_remove)
        stats["unmanaged_removed"] += len(unmanaged_to_remove)

        # Print changes
        if not args.json:
            prefix_str = f"[{matched_prefix}]" if matched_prefix else "[no prefix]"
            print(f"{BOLD}{repo_name}{NC} {DIM}{prefix_str}{NC}")

            if current_unmanaged and not args.clean_unmanaged:
                print(f"  {DIM}Keeping unmanaged: {', '.join(sorted(current_unmanaged))}{NC}")

            for topic in sorted(to_remove):
                print(f"  {RED}- {topic}{NC}")

            for topic in sorted(unmanaged_to_remove):
                print(f"  {RED}- {topic} (unmanaged){NC}")

            for topic in sorted(to_add):
                print(f"  {GREEN}+ {topic}{NC}")

            if args.execute:
                final_topics = list(new_topics)
                if set_repo_topics(args.org, repo_name, final_topics):
                    print(f"  {GREEN}✓ Updated{NC}")
                else:
                    print(f"  {RED}✗ Failed{NC}")

            print()

    # JSON output
    if args.json:
        output = {
            "org": args.org,
            "config_file": str(config_path),
            "dry_run": not args.execute,
            "stats": stats,
            "unknown_prefixes": unknown_prefixes,
            "results": results
        }
        print(json.dumps(output, indent=2))
        return

    # Summary
    print(f"{BOLD}Summary:{NC}")
    print(f"  Total repos: {stats['total']}")
    print(f"  Matched prefix: {GREEN}{stats['matched']}{NC}")
    print(f"  No prefix match: {YELLOW}{stats['unmatched']}{NC}")
    print(f"  Skipped (archived): {DIM}{stats['skipped_archived']}{NC}")
    print()
    print(f"  Repos to change: {stats['repos_changed']}")
    print(f"  Topics to add: {GREEN}+{stats['topics_added']}{NC}")
    print(f"  Topics to remove: {RED}-{stats['topics_removed']}{NC}")
    if stats['unmanaged_removed'] > 0:
        print(f"  Unmanaged to remove: {RED}-{stats['unmanaged_removed']}{NC}")
    print(f"  Unchanged: {stats['unchanged']}")
    print()

    # Report unknown prefixes
    if args.report_unknown and unknown_prefixes:
        print(f"{BOLD}Unknown Prefixes (not in config):{NC}")
        for prefix, repos in sorted(unknown_prefixes.items()):
            print(f"  {YELLOW}{prefix:<12}{NC} ({len(repos)} repos)")
            for repo in repos[:5]:
                print(f"    {DIM}- {repo}{NC}")
            if len(repos) > 5:
                print(f"    {DIM}... and {len(repos) - 5} more{NC}")
        print()
        print(f"{DIM}Add these prefixes to config/prefix-labels.json if needed{NC}")
        print()

    if not args.execute and stats['repos_changed'] > 0:
        print(f"{YELLOW}Run with --execute to apply these changes{NC}")
        print()


if __name__ == "__main__":
    main()
