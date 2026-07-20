#!/usr/bin/env python3
# @name: gh-topic-manager
# @description: Manage GitHub repository topics
# @category: github
# @usage: gh-topic-manager.py <repo> [--add <topics>] [--remove <topics>] [--fork|--no-fork]
"""
gh-topic-manager.py - GitHub Repository Topics Manager
Verwaltet Topics für einzelne oder mehrere Repositories.
"""

import sys
import json
import subprocess
import argparse
from typing import List, Dict, Set, Optional
from collections import Counter

# Farben
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
NC = '\033[0m'
BOLD = '\033[1m'


def run_gh(args: List[str], capture: bool = True) -> Optional[str]:
    """Run GitHub CLI command.

    Raises CalledProcessError on failure so callers can react; stderr from gh
    is surfaced instead of being swallowed.
    """
    cmd = ["gh"] + args
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        else:
            subprocess.run(cmd, check=True)
            return None
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        if stderr:
            print(f"{RED}[gh] {stderr}{NC}", file=sys.stderr)
        raise


def check_gh_auth() -> bool:
    """Check if gh CLI is authenticated."""
    try:
        subprocess.run(["gh", "auth", "status"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_repos(org: Optional[str] = None, pattern: Optional[str] = None,
              limit: int = 100, fork: Optional[bool] = None) -> List[Dict]:
    """Get list of repositories.

    fork: True = only forks, False = only non-forks, None = no fork filter.
    """
    args = ["repo", "list"]

    if org:
        args.append(org)

    args.extend(["--json", "name,nameWithOwner,repositoryTopics,isFork",
                 "--limit", str(limit)])

    try:
        output = run_gh(args)
    except subprocess.CalledProcessError:
        return []
    if not output:
        return []

    repos = json.loads(output)

    # gh returns repositoryTopics as [{"name": ...}] or null - flatten it so
    # callers can read repo["topics"] as a plain list of strings.
    for repo in repos:
        raw_topics = repo.pop("repositoryTopics", None) or []
        repo["topics"] = [t["name"] for t in raw_topics]

    # Filter by fork status if specified
    if fork is not None:
        repos = [r for r in repos if r.get("isFork", False) is fork]

    # Filter by pattern if specified
    if pattern:
        import fnmatch
        repos = [r for r in repos if fnmatch.fnmatch(r["name"], pattern)]

    return repos


def get_repo_topics(repo: str) -> List[str]:
    """Get topics for a specific repository."""
    try:
        output = run_gh(["repo", "view", repo, "--json", "repositoryTopics"])
    except subprocess.CalledProcessError:
        return []
    if not output:
        return []

    data = json.loads(output)
    return [t["name"] for t in (data.get("repositoryTopics") or [])]


def add_topics(repo: str, topics: List[str], dry_run: bool = False) -> bool:
    """Add topics to a repository."""
    if dry_run:
        print(f"  Would add: {', '.join(topics)}")
        return True

    for topic in topics:
        try:
            run_gh(["repo", "edit", repo, "--add-topic", topic])
        except subprocess.CalledProcessError:
            print(f"{RED}  Failed to add topic: {topic}{NC}")
            return False

    return True


def remove_topics(repo: str, topics: List[str], dry_run: bool = False) -> bool:
    """Remove topics from a repository."""
    if dry_run:
        print(f"  Would remove: {', '.join(topics)}")
        return True

    for topic in topics:
        try:
            run_gh(["repo", "edit", repo, "--remove-topic", topic])
        except subprocess.CalledProcessError:
            print(f"{RED}  Failed to remove topic: {topic}{NC}")
            return False

    return True


def replace_topic(repo: str, old_topic: str, new_topic: str, dry_run: bool = False) -> bool:
    """Replace one topic with another."""
    current = get_repo_topics(repo)
    if old_topic not in current:
        return False

    if dry_run:
        print(f"  Would replace: {old_topic} → {new_topic}")
        return True

    try:
        run_gh(["repo", "edit", repo, "--remove-topic", old_topic])
        run_gh(["repo", "edit", repo, "--add-topic", new_topic])
        return True
    except subprocess.CalledProcessError:
        return False


def analyze_topics(repos: List[Dict]) -> Dict:
    """Analyze topic usage across repositories."""
    all_topics = []
    repos_with_topics = 0
    repos_without_topics = 0

    for repo in repos:
        topics = repo.get("topics", [])
        if topics:
            repos_with_topics += 1
            all_topics.extend(topics)
        else:
            repos_without_topics += 1

    return {
        "total_repos": len(repos),
        "with_topics": repos_with_topics,
        "without_topics": repos_without_topics,
        "topic_counts": Counter(all_topics),
        "unique_topics": len(set(all_topics))
    }


def main():
    parser = argparse.ArgumentParser(
        description="Manage GitHub repository topics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all topics in an organization
  gh-topic-manager.py -o myorg --analyze

  # Add topics to a single repo
  gh-topic-manager.py myorg/myrepo --add python,cli

  # Add topics to multiple repos by pattern
  gh-topic-manager.py -o myorg --pattern "api-*" --add microservice,rest

  # Tag every fork in an org (leaves own repos untouched)
  gh-topic-manager.py -o myorg --fork --sync forked-repo --limit 1000

  # Combine both filters: forks whose name starts with "terraform-"
  gh-topic-manager.py -o myorg --fork --pattern "terraform-*" --add forked-repo

  # Remove topics
  gh-topic-manager.py myorg/myrepo --remove deprecated,old

  # Replace a topic across all repos
  gh-topic-manager.py -o myorg --replace old-topic=new-topic

  # Sync topics (add missing, keep existing)
  gh-topic-manager.py myorg/myrepo --sync python,cli,api

  # List repos missing specific topics
  gh-topic-manager.py -o myorg --missing python
        """
    )

    parser.add_argument(
        "repo",
        nargs="?",
        help="Repository (owner/name) or leave empty for org-wide operations"
    )
    parser.add_argument(
        "-o", "--org",
        help="Organization name for bulk operations"
    )
    parser.add_argument(
        "--pattern",
        help="Filter repos by name pattern (e.g., 'api-*')"
    )
    fork_group = parser.add_mutually_exclusive_group()
    fork_group.add_argument(
        "--fork",
        action="store_true",
        help="Only forked repos (requires --org, combinable with --pattern)"
    )
    fork_group.add_argument(
        "--no-fork",
        action="store_true",
        help="Only non-forked repos (requires --org, combinable with --pattern)"
    )
    parser.add_argument(
        "--add",
        help="Comma-separated topics to add"
    )
    parser.add_argument(
        "--remove",
        help="Comma-separated topics to remove"
    )
    parser.add_argument(
        "--replace",
        help="Replace topic (format: old=new)"
    )
    parser.add_argument(
        "--sync",
        help="Sync topics (ensure these exist, keep others)"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Analyze topic usage across repos"
    )
    parser.add_argument(
        "--missing",
        help="List repos missing specified topics"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List topics for repo(s)"
    )
    parser.add_argument(
        "-d", "--dry-run",
        action="store_true",
        help="Show what would be changed"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max repos to process (default: 100)"
    )

    args = parser.parse_args()

    # Check authentication
    if not check_gh_auth():
        print(f"{RED}[ERROR] GitHub CLI not authenticated{NC}")
        print("Run: gh auth login")
        sys.exit(1)

    print()
    print(f"{BOLD}{CYAN}╔═══════════════════════════════════════════════════════════════╗{NC}")
    print(f"{BOLD}{CYAN}║                  GitHub Topic Manager                         ║{NC}")
    print(f"{BOLD}{CYAN}╚═══════════════════════════════════════════════════════════════╝{NC}")
    print()

    # Fork filter: True = only forks, False = only non-forks, None = no filter
    fork_filter = True if args.fork else (False if args.no_fork else None)

    # Determine target repos
    repos = []
    if args.repo:
        if fork_filter is not None:
            print(f"{RED}[ERROR] --fork/--no-fork only apply to --org mode{NC}")
            sys.exit(1)
        # Single repo
        topics = get_repo_topics(args.repo)
        repos = [{"nameWithOwner": args.repo, "name": args.repo.split("/")[-1], "topics": topics}]
    elif args.org:
        # Organization repos
        print(f"Fetching repositories from {args.org}...")
        repos = get_repos(org=args.org, pattern=args.pattern, limit=args.limit,
                          fork=fork_filter)

        active_filters = []
        if fork_filter is True:
            active_filters.append("forks only")
        elif fork_filter is False:
            active_filters.append("non-forks only")
        if args.pattern:
            active_filters.append(f"name matches '{args.pattern}'")

        if active_filters:
            print(f"Found {len(repos)} repositories ({', '.join(active_filters)})")
        else:
            print(f"Found {len(repos)} repositories")
        print()
    else:
        print(f"{RED}[ERROR] Specify either a repo or --org{NC}")
        sys.exit(1)

    if not repos:
        print(f"{YELLOW}No repositories found{NC}")
        sys.exit(0)

    # Analyze mode
    if args.analyze:
        stats = analyze_topics(repos)

        print(f"{BOLD}Topic Analysis:{NC}")
        print(f"  Total repositories: {stats['total_repos']}")
        print(f"  With topics: {GREEN}{stats['with_topics']}{NC}")
        print(f"  Without topics: {YELLOW}{stats['without_topics']}{NC}")
        print(f"  Unique topics: {stats['unique_topics']}")
        print()

        if stats['topic_counts']:
            print(f"{BOLD}Top Topics:{NC}")
            for topic, count in stats['topic_counts'].most_common(20):
                bar = "█" * min(count, 30)
                print(f"  {topic:30} {bar} {count}")
        print()
        sys.exit(0)

    # Missing topics mode
    if args.missing:
        required = set(t.strip() for t in args.missing.split(","))
        print(f"{BOLD}Repos missing topics: {', '.join(required)}{NC}")
        print()

        missing_repos = []
        for repo in repos:
            current = set(repo.get("topics", []))
            missing = required - current
            if missing:
                missing_repos.append((repo["nameWithOwner"], missing))

        if missing_repos:
            for name, missing in missing_repos:
                print(f"  {CYAN}{name}{NC}: missing {', '.join(missing)}")
            print()
            print(f"Total: {len(missing_repos)} repos missing topics")
        else:
            print(f"{GREEN}All repos have required topics{NC}")
        print()
        sys.exit(0)

    # List mode
    if args.list:
        for repo in repos:
            topics = repo.get("topics", [])
            if topics:
                print(f"{CYAN}{repo['nameWithOwner']}{NC}: {', '.join(topics)}")
            else:
                print(f"{CYAN}{repo['nameWithOwner']}{NC}: {YELLOW}(no topics){NC}")
        print()
        sys.exit(0)

    # Modification modes
    if args.dry_run:
        print(f"{YELLOW}DRY RUN - No changes will be made{NC}")
        print()

    modified = 0
    failed = 0

    # Add topics
    if args.add:
        topics_to_add = [t.strip() for t in args.add.split(",")]
        print(f"{BOLD}Adding topics: {', '.join(topics_to_add)}{NC}")
        print()

        for repo in repos:
            print(f"{CYAN}→{NC} {repo['nameWithOwner']}")
            if add_topics(repo["nameWithOwner"], topics_to_add, args.dry_run):
                modified += 1
            else:
                failed += 1

    # Remove topics
    if args.remove:
        topics_to_remove = [t.strip() for t in args.remove.split(",")]
        print(f"{BOLD}Removing topics: {', '.join(topics_to_remove)}{NC}")
        print()

        for repo in repos:
            current = set(repo.get("topics", []))
            to_remove = [t for t in topics_to_remove if t in current]
            if to_remove:
                print(f"{CYAN}→{NC} {repo['nameWithOwner']}")
                if remove_topics(repo["nameWithOwner"], to_remove, args.dry_run):
                    modified += 1
                else:
                    failed += 1

    # Replace topic
    if args.replace:
        if "=" not in args.replace:
            print(f"{RED}[ERROR] Replace format: old=new{NC}")
            sys.exit(1)

        old_topic, new_topic = args.replace.split("=", 1)
        print(f"{BOLD}Replacing: {old_topic} → {new_topic}{NC}")
        print()

        for repo in repos:
            current = repo.get("topics", [])
            if old_topic in current:
                print(f"{CYAN}→{NC} {repo['nameWithOwner']}")
                if replace_topic(repo["nameWithOwner"], old_topic, new_topic, args.dry_run):
                    modified += 1
                else:
                    failed += 1

    # Sync topics
    if args.sync:
        required = set(t.strip() for t in args.sync.split(","))
        print(f"{BOLD}Syncing topics: {', '.join(required)}{NC}")
        print()

        for repo in repos:
            current = set(repo.get("topics", []))
            missing = required - current
            if missing:
                print(f"{CYAN}→{NC} {repo['nameWithOwner']}")
                if add_topics(repo["nameWithOwner"], list(missing), args.dry_run):
                    modified += 1
                else:
                    failed += 1

    # Summary
    if args.add or args.remove or args.replace or args.sync:
        print()
        print(f"{GREEN}✓ {modified} repositories modified{NC}")
        if failed:
            # Exit non-zero so a scheduled run cannot report success while
            # writes were denied - a token missing Administration:write fails
            # every single edit and would otherwise look like "nothing to do".
            print(f"{RED}✗ {failed} repositories failed{NC}")
            print()
            sys.exit(1)
        print()


if __name__ == "__main__":
    main()
