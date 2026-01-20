#!/usr/bin/env python3
# @name: gh-actions-usage
# @description: Show GitHub Actions usage and billing across organization
# @category: github
# @usage: gh-actions-usage.py [-o <org>] [--detailed]
"""
gh-actions-usage.py - GitHub Actions Usage Reporter
Zeigt GitHub Actions Nutzung und Kosten für eine Organisation.
"""

import sys
import json
import subprocess
import argparse
from typing import List, Dict, Optional
from datetime import datetime, timedelta

# Farben
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
NC = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'

# Pricing per minute (as of 2024)
PRICING = {
    "UBUNTU": 0.008,
    "MACOS": 0.08,
    "WINDOWS": 0.016,
    "ubuntu": 0.008,
    "macos": 0.08,
    "windows": 0.016,
}


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


def get_org_billing(org: str) -> Optional[Dict]:
    """Get organization billing information."""
    output = run_gh(["api", f"/orgs/{org}/settings/billing/actions"])
    if not output:
        return None
    return json.loads(output)


def get_org_repos(org: str) -> List[Dict]:
    """Get all non-archived repositories for an organization."""
    output = run_gh([
        "api", f"/orgs/{org}/repos",
        "--paginate",
        "-q", ".[] | select(.archived == false) | {name: .name, visibility: .visibility}"
    ])
    if not output:
        return []

    repos = []
    for line in output.strip().split('\n'):
        if line:
            repos.append(json.loads(line))
    return repos


def get_workflow_runs(org: str, repo: str, days: int = 30) -> List[Dict]:
    """Get recent workflow runs for a repository."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    output = run_gh([
        "api", f"repos/{org}/{repo}/actions/runs",
        "-q", f".workflow_runs[] | select(.created_at >= \"{since}\") | {{id: .id, name: .name, status: .status, conclusion: .conclusion, run_started_at: .run_started_at, updated_at: .updated_at}}"
    ])
    if not output:
        return []

    runs = []
    for line in output.strip().split('\n'):
        if line:
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return runs


def get_workflow_usage(org: str, repo: str, run_id: int) -> Optional[Dict]:
    """Get usage for a specific workflow run."""
    output = run_gh(["api", f"repos/{org}/{repo}/actions/runs/{run_id}/timing"])
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def format_duration(ms: int) -> str:
    """Format milliseconds to human readable."""
    seconds = ms // 1000
    minutes = seconds // 60
    hours = minutes // 60

    if hours > 0:
        return f"{hours}h {minutes % 60}m"
    elif minutes > 0:
        return f"{minutes}m {seconds % 60}s"
    else:
        return f"{seconds}s"


def format_minutes(minutes: int) -> str:
    """Format minutes to human readable."""
    hours = minutes // 60
    if hours > 0:
        return f"{hours}h {minutes % 60}m"
    return f"{minutes}m"


def get_org_runners(org: str) -> List[Dict]:
    """Get organization self-hosted runners."""
    output = run_gh(["api", f"/orgs/{org}/actions/runners"])
    if not output:
        return []
    try:
        data = json.loads(output)
        return data.get("runners", [])
    except json.JSONDecodeError:
        return []


def get_recent_workflow_summary(org: str, repos: List[Dict], days: int = 30) -> Dict:
    """Get a quick summary of recent workflow activity."""
    total_runs = 0
    total_success = 0
    total_failure = 0
    active_repos = 0

    for repo in repos[:50]:  # Limit to first 50 repos for speed
        runs = get_workflow_runs(org, repo["name"], days)
        if runs:
            active_repos += 1
            total_runs += len(runs)
            total_success += len([r for r in runs if r.get("conclusion") == "success"])
            total_failure += len([r for r in runs if r.get("conclusion") == "failure"])

    return {
        "total_runs": total_runs,
        "success": total_success,
        "failure": total_failure,
        "active_repos": active_repos
    }


def main():
    parser = argparse.ArgumentParser(
        description="Show GitHub Actions usage and billing across organization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show organization billing summary
  gh-actions-usage.py

  # Show detailed per-repo usage
  gh-actions-usage.py --detailed

  # Show usage for specific repo
  gh-actions-usage.py --repo myrepo

  # Show last 7 days
  gh-actions-usage.py --days 7

  # Export as JSON
  gh-actions-usage.py --json
        """
    )

    parser.add_argument(
        "-o", "--org",
        default="bauer-group",
        help="GitHub organization (default: bauer-group)"
    )
    parser.add_argument(
        "--repo",
        help="Show usage for specific repository"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to analyze (default: 30)"
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed per-repo breakdown"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    # Check authentication
    if not check_gh_auth():
        print(f"{RED}[ERROR] GitHub CLI not authenticated{NC}")
        print("Run: gh auth login")
        sys.exit(1)

    # Header
    if not args.json:
        print()
        print(f"{BOLD}{CYAN}+---------------------------------------------------------------+{NC}")
        print(f"{BOLD}{CYAN}|              GitHub Actions Usage Report                      |{NC}")
        print(f"{BOLD}{CYAN}+---------------------------------------------------------------+{NC}")
        print()

    # Get billing info
    if not args.json:
        print(f"Fetching data for {BOLD}{args.org}{NC}...")

    billing = get_org_billing(args.org)
    runners = get_org_runners(args.org)
    repos = get_org_repos(args.org)

    if args.json:
        summary = get_recent_workflow_summary(args.org, repos, args.days)
        output_data = {
            "org": args.org,
            "billing": billing,
            "runners": [{"name": r.get("name"), "status": r.get("status"), "busy": r.get("busy"), "os": r.get("os")} for r in runners],
            "activity_summary": summary,
            "repos": []
        }

    if not args.json:
        print()

        # Self-hosted runners info
        if runners:
            online = len([r for r in runners if r.get("status") == "online"])
            offline = len([r for r in runners if r.get("status") == "offline"])
            busy = len([r for r in runners if r.get("busy")])

            print(f"{BOLD}Self-hosted Runners:{NC}")
            print(f"  Total: {len(runners)} | {GREEN}Online: {online}{NC} | {RED}Offline: {offline}{NC} | {YELLOW}Busy: {busy}{NC}")
            print()

        # Billing info - always show, even if 0
        print(f"{BOLD}Billing Summary:{NC}")

        if billing:
            total_minutes = billing.get("total_minutes_used", 0)
            included_minutes = billing.get("included_minutes", 0)
            paid_minutes = billing.get("total_paid_minutes_used", 0)
            minutes_by_os = billing.get("minutes_used_breakdown", {})

            # Calculate usage percentage
            usage_pct = (total_minutes / included_minutes * 100) if included_minutes > 0 else 0

            print(f"  Included minutes: {CYAN}{included_minutes:,}{NC}")
            print(f"  Used minutes: {total_minutes:,} ({usage_pct:.1f}%)")

            # Progress bar
            bar_width = 30
            filled = int(bar_width * min(usage_pct / 100, 1))
            bar_color = GREEN if usage_pct < 50 else YELLOW if usage_pct < 80 else RED
            bar = f"{bar_color}{'█' * filled}{NC}{'░' * (bar_width - filled)}"
            print(f"  [{bar}]")

            if paid_minutes > 0:
                print(f"  {YELLOW}Paid minutes: {paid_minutes:,}{NC}")

            print()

            # Minutes by OS (only if any usage)
            has_os_usage = any(m > 0 for m in minutes_by_os.values())
            if has_os_usage:
                print(f"{BOLD}Minutes by OS:{NC}")
                total_cost = 0
                for os_name, minutes in minutes_by_os.items():
                    if minutes > 0:
                        rate = PRICING.get(os_name, 0.008)
                        cost = minutes * rate
                        total_cost += cost
                        print(f"  {os_name}: {minutes:,} min (${cost:.2f})")
                print(f"  {BOLD}Estimated cost: ${total_cost:.2f}{NC}")
                print()
            else:
                if runners:
                    print(f"  {DIM}No GitHub-hosted runner usage (using self-hosted runners){NC}")
                else:
                    print(f"  {DIM}No usage this billing period{NC}")
                print()
        else:
            print(f"  {YELLOW}Could not fetch billing data{NC}")
            print()

        # Quick activity summary (always show)
        print(f"{BOLD}Recent Activity (last {args.days} days):{NC}")
        summary = get_recent_workflow_summary(args.org, repos, args.days)

        if summary["total_runs"] > 0:
            success_rate = (summary["success"] / summary["total_runs"] * 100) if summary["total_runs"] > 0 else 0
            status_color = GREEN if success_rate >= 90 else YELLOW if success_rate >= 70 else RED

            print(f"  Active repos: {summary['active_repos']}")
            print(f"  Total workflow runs: {summary['total_runs']}")
            print(f"  {GREEN}✓ Success: {summary['success']}{NC} | {RED}✗ Failure: {summary['failure']}{NC}")
            print(f"  Success rate: {status_color}{success_rate:.1f}%{NC}")
        else:
            print(f"  {DIM}No workflow runs found{NC}")
        print()

    # Get per-repo usage if detailed
    if args.detailed or args.repo:
        if args.repo:
            repos = [{"name": args.repo}]
        else:
            if not args.json:
                print(f"Scanning repositories...")
            repos = get_org_repos(args.org)

        if not args.json:
            print(f"  Found {len(repos)} repos")
            print()
            print(f"{BOLD}Per-Repository Usage (last {args.days} days):{NC}")
            print()

        repo_stats = []

        for repo in repos:
            repo_name = repo["name"]
            runs = get_workflow_runs(args.org, repo_name, args.days)

            if not runs:
                continue

            success = len([r for r in runs if r.get("conclusion") == "success"])
            failure = len([r for r in runs if r.get("conclusion") == "failure"])
            cancelled = len([r for r in runs if r.get("conclusion") == "cancelled"])

            stat = {
                "name": repo_name,
                "runs": len(runs),
                "success": success,
                "failure": failure,
                "cancelled": cancelled
            }
            repo_stats.append(stat)

            if args.json:
                output_data["repos"].append(stat)

        # Sort by run count
        repo_stats.sort(key=lambda x: x["runs"], reverse=True)

        if not args.json:
            for stat in repo_stats[:20]:  # Top 20
                success_rate = (stat["success"] / stat["runs"] * 100) if stat["runs"] > 0 else 0
                status_color = GREEN if success_rate >= 90 else YELLOW if success_rate >= 70 else RED

                print(f"{BOLD}{stat['name']}{NC}")
                print(f"  Runs: {stat['runs']} | {GREEN}✓{stat['success']}{NC} {RED}✗{stat['failure']}{NC} {DIM}○{stat['cancelled']}{NC}")
                print(f"  Success rate: {status_color}{success_rate:.0f}%{NC}")
                print()

            if len(repo_stats) > 20:
                print(f"{DIM}... and {len(repo_stats) - 20} more repos{NC}")
                print()

    # JSON output
    if args.json:
        print(json.dumps(output_data, indent=2))
        return

    # Hint for more details
    if not args.detailed and not args.repo:
        print(f"{DIM}Use --detailed for per-repo breakdown{NC}")
        print()


if __name__ == "__main__":
    main()
