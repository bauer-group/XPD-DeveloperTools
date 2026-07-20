#!/usr/bin/env python3
# @name: gh-fork-autosync
# @description: Sync every fork in an org from upstream (topic-filtered, no workflows in forks)
# @category: github
# @usage: gh-fork-autosync.py -o <org> [--topic <topic>] [--dry-run]
"""
gh-fork-autosync.py - Organization-wide Fork Sync

Keeps every fork in an organization current with its upstream, driven entirely
through the GitHub API - the forks themselves need no workflow installed.

Per repository:

  Stage A  POST /repos/{org}/{repo}/merge-upstream
           Updates the mirror branch (the branch named like the upstream's
           default branch). This is the "Sync fork" button.

  Stage B  POST /repos/{org}/{repo}/merges
           Only when the fork's own default branch differs from the mirror
           branch. Merges mirror -> fork default server-side, so an integration
           branch such as `workspace/main` stays current too.

Stage B is a separate step because merge-upstream can only sync a fork branch
from the upstream branch *of the same name* - it takes no upstream-branch
parameter. A fork whose default is `workspace/main` therefore cannot be synced
directly; its `main` is synced first, then merged forward.

Not to be confused with gh-sync-forks.py, which syncs an individual fork's
default branch and assumes both sides share a branch name.
"""

import sys
import json
import subprocess
import argparse
from typing import List, Dict, Optional, Tuple

# Farben
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
NC = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'

# Issue titles deliberately match sync-upstream.yml so a repo that also runs the
# workflow updates the existing issue instead of opening a second one.
ISSUE_TITLE_MIRROR = "⚠️ Upstream-Sync-Konflikt: {branch}"
ISSUE_TITLE_INTEGRATE = "⚠️ Merge-Konflikt: {source} → {target}"


def gh(args: List[str]) -> Tuple[int, str]:
    """Run gh and return (returncode, combined stdout+stderr).

    Never raises - callers classify the outcome from the return code and text,
    because HTTP 409 (conflict) is an expected result here, not a failure.

    encoding is pinned to UTF-8: text=True alone decodes with the locale
    encoding, and on a cp1252 Windows console any non-Latin-1 byte in an API
    response kills the reader thread, leaving stdout as None next to a zero
    return code.
    """
    result = subprocess.run(["gh"] + args, capture_output=True,
                            encoding="utf-8", errors="replace")
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def check_gh_auth() -> bool:
    """Check if gh CLI is authenticated."""
    try:
        subprocess.run(["gh", "auth", "status"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def is_conflict(output: str) -> bool:
    """Recognise GitHub's merge-conflict response."""
    lowered = output.lower()
    return "http 409" in lowered or "merge conflict" in lowered


def discover_forks(org: str, topic: Optional[str], limit: int) -> Optional[Tuple[List[Dict], bool]]:
    """List forks in an org, optionally narrowed to one topic.

    Returns (repos, truncated), or None when discovery itself failed.
    truncated is True when the result hit --limit, meaning the org may hold
    repositories this run never saw.

    The None case is kept distinct from an empty list on purpose: a failed
    lookup must not be reported as "nothing to do".
    """
    rc, out = gh([
        "repo", "list", org,
        "--fork",
        "--json", "name,nameWithOwner,repositoryTopics",
        "--limit", str(limit),
    ])
    if rc != 0:
        print(f"{RED}[ERROR] Could not list repositories for '{org}'{NC}", file=sys.stderr)
        print(out, file=sys.stderr)
        return None

    repos = json.loads(out) if out else []
    truncated = len(repos) >= limit

    if topic:
        selected = []
        for repo in repos:
            topics = [t["name"] for t in (repo.get("repositoryTopics") or [])]
            if topic in topics:
                selected.append(repo)
        repos = selected

    return repos, truncated


def repo_details(repo: str) -> Optional[Dict]:
    """Fetch default branch and upstream parent for a repository."""
    rc, out = gh(["api", f"repos/{repo}"])
    if rc != 0:
        return None

    data = json.loads(out)
    parent = data.get("parent") or {}
    return {
        "default_branch": data.get("default_branch"),
        "parent": parent.get("full_name"),
        "parent_default_branch": parent.get("default_branch"),
    }


def branch_exists(repo: str, branch: str) -> bool:
    """Check whether a branch exists in the fork."""
    rc, _ = gh(["api", f"repos/{repo}/branches/{branch}", "--silent"])
    return rc == 0


def sync_mirror(repo: str, branch: str) -> Tuple[str, str]:
    """Stage A - update the mirror branch from upstream.

    Returns (status, detail) with status in:
    synced | up-to-date | conflict | error
    """
    rc, out = gh([
        "api", "--method", "POST",
        f"repos/{repo}/merge-upstream",
        "-f", f"branch={branch}",
    ])

    if rc == 0:
        try:
            merge_type = json.loads(out).get("merge_type", "unknown")
        except (json.JSONDecodeError, AttributeError):
            merge_type = "unknown"
        if merge_type == "none":
            return "up-to-date", merge_type
        return "synced", merge_type

    if is_conflict(out):
        return "conflict", "diverged from upstream"

    return "error", out.splitlines()[0] if out else "unknown error"


def merge_forward(repo: str, base: str, head: str) -> Tuple[str, str]:
    """Stage B - server-side merge of head into base.

    Returns (status, detail) with status in:
    integrated | up-to-date | conflict | error
    """
    rc, out = gh([
        "api", "--method", "POST",
        f"repos/{repo}/merges",
        "-f", f"base={base}",
        "-f", f"head={head}",
    ])

    if rc == 0:
        # 201 returns the merge commit, 204 (nothing to merge) returns no body.
        return ("integrated", "merge commit created") if out.strip() else ("up-to-date", "nothing to merge")

    if is_conflict(out):
        return "conflict", "merge conflict"

    return "error", out.splitlines()[0] if out else "unknown error"


def ensure_conflict_issue(repo: str, title: str, body: str) -> bool:
    """Open a conflict issue, or comment on the existing one. Best effort."""
    for label, color, desc in (
        ("sync", "1D76DB", "Upstream sync automation"),
        ("conflict", "B60205", "Requires manual resolution"),
        ("automated", "0E8A16", "Created by automation"),
    ):
        gh(["label", "create", label, "-R", repo, "--color", color, "--description", desc])

    rc, out = gh([
        "issue", "list", "-R", repo,
        "--state", "open", "--label", "sync", "--label", "conflict",
        "--json", "number,title",
    ])
    if rc == 0 and out:
        try:
            for issue in json.loads(out):
                if issue.get("title") == title:
                    rc, _ = gh(["issue", "comment", str(issue["number"]), "-R", repo,
                                "--body", "Konflikt besteht weiterhin (gh-fork-autosync)."])
                    return rc == 0
        except json.JSONDecodeError:
            pass

    rc, _ = gh([
        "issue", "create", "-R", repo,
        "--title", title, "--body", body,
        "--label", "sync", "--label", "conflict", "--label", "automated",
    ])
    if rc != 0:
        # Label creation may be denied on repos where we lack triage rights.
        rc, _ = gh(["issue", "create", "-R", repo, "--title", title, "--body", body])
    return rc == 0


def mirror_conflict_body(repo: str, parent: str, branch: str) -> str:
    return (
        "## Automatischer Upstream-Sync fehlgeschlagen\n\n"
        f"Der Mirror-Branch `{branch}` konnte nicht automatisch mit dem Upstream "
        f"(`{parent}`) synchronisiert werden - er ist divergiert und erfordert eine "
        "manuelle Auflösung.\n\n"
        "Erzeugt von `gh-fork-autosync`.\n\n"
        "### Manuelle Auflösung\n"
        "```bash\n"
        f"git clone https://github.com/{repo}.git && cd {repo.split('/')[-1]}\n"
        f"git checkout {branch}\n"
        f"git remote add upstream https://github.com/{parent}.git\n"
        "git fetch upstream\n"
        f"git merge upstream/{branch}\n"
        "# Konflikte auflösen, dann:\n"
        f"git push origin {branch}\n"
        "```\n"
    )


def integrate_conflict_body(repo: str, source: str, target: str) -> str:
    return (
        "## Automatische Integration fehlgeschlagen\n\n"
        f"Der Merge von `{source}` nach `{target}` konnte nicht automatisch "
        "abgeschlossen werden. Eine manuelle Auflösung ist erforderlich.\n\n"
        "Erzeugt von `gh-fork-autosync`.\n\n"
        "### Manuelle Auflösung\n"
        "```bash\n"
        f"git clone https://github.com/{repo}.git && cd {repo.split('/')[-1]}\n"
        f"git checkout {target}\n"
        f"git merge origin/{source}\n"
        "# Konflikte auflösen, dann:\n"
        f"git push origin {target}\n"
        "```\n"
    )


def process_repo(repo: str, dry_run: bool, create_issues: bool) -> Dict:
    """Run both stages for one repository and return a result record."""
    result = {"repo": repo, "mirror": None, "target": None,
              "stage_a": "skipped", "stage_b": "skipped", "detail": ""}

    details = repo_details(repo)
    if not details:
        result["stage_a"] = "error"
        result["detail"] = "could not read repository"
        return result

    parent = details["parent"]
    if not parent:
        result["stage_a"] = "not-a-fork"
        result["detail"] = "no upstream parent"
        return result

    mirror = details["parent_default_branch"]
    fork_default = details["default_branch"]
    result["mirror"] = mirror
    result["target"] = fork_default

    if not mirror:
        result["stage_a"] = "error"
        result["detail"] = "upstream has no default branch"
        return result

    # merge-upstream syncs from the identically named upstream branch, so the
    # fork must actually carry that branch.
    if not branch_exists(repo, mirror):
        result["stage_a"] = "no-mirror-branch"
        result["detail"] = f"fork has no branch '{mirror}'"
        return result

    if dry_run:
        result["stage_a"] = "dry-run"
        result["stage_b"] = "dry-run" if fork_default != mirror else "skipped"
        result["detail"] = f"would sync '{mirror}' from {parent}"
        if fork_default != mirror:
            result["detail"] += f", then merge '{mirror}' -> '{fork_default}'"
        return result

    # ---- Stage A -----------------------------------------------------------
    status, detail = sync_mirror(repo, mirror)
    result["stage_a"] = status
    result["detail"] = detail

    if status == "conflict":
        if create_issues:
            ensure_conflict_issue(
                repo,
                ISSUE_TITLE_MIRROR.format(branch=mirror),
                mirror_conflict_body(repo, parent, mirror),
            )
        return result

    if status == "error":
        return result

    # ---- Stage B -----------------------------------------------------------
    if fork_default == mirror:
        return result

    status_b, detail_b = merge_forward(repo, base=fork_default, head=mirror)
    result["stage_b"] = status_b
    result["detail"] = f"{detail}; {detail_b}"

    if status_b == "conflict" and create_issues:
        ensure_conflict_issue(
            repo,
            ISSUE_TITLE_INTEGRATE.format(source=mirror, target=fork_default),
            integrate_conflict_body(repo, mirror, fork_default),
        )

    return result


STATUS_STYLE = {
    "synced": (GREEN, "synced"),
    "integrated": (GREEN, "integrated"),
    "up-to-date": (DIM, "up to date"),
    "dry-run": (CYAN, "dry run"),
    "skipped": (DIM, "-"),
    "conflict": (YELLOW, "CONFLICT"),
    "no-mirror-branch": (YELLOW, "no mirror branch"),
    "not-a-fork": (YELLOW, "not a fork"),
    "error": (RED, "ERROR"),
}


def render(status: str) -> str:
    color, label = STATUS_STYLE.get(status, (RED, status))
    return f"{color}{label}{NC}"


def main():
    parser = argparse.ArgumentParser(
        description="Sync every fork in an organization from its upstream",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what would happen for every tagged fork
  gh-fork-autosync.py -o mirrored-projects --topic forked-repo --dry-run

  # Sync them
  gh-fork-autosync.py -o mirrored-projects --topic forked-repo

  # Every fork in the org, regardless of topic
  gh-fork-autosync.py -o mirrored-projects

  # Sync without opening conflict issues
  gh-fork-autosync.py -o mirrored-projects --topic forked-repo --no-issues

  # Single repository
  gh-fork-autosync.py -o mirrored-projects --repo Bugsink
        """
    )
    parser.add_argument("-o", "--org", required=True,
                        help="Organization to process")
    parser.add_argument("--topic",
                        help="Only repos carrying this topic (e.g. forked-repo)")
    parser.add_argument("--repo",
                        help="Restrict to a single repository name within the org")
    parser.add_argument("--no-issues", action="store_true",
                        help="Do not open/update conflict issues, only report")
    parser.add_argument("-d", "--dry-run", action="store_true",
                        help="Show what would be done, change nothing")
    parser.add_argument("--limit", type=int, default=1000,
                        help="Max repos to fetch (default: 1000)")

    args = parser.parse_args()

    if not check_gh_auth():
        print(f"{RED}[ERROR] GitHub CLI not authenticated{NC}")
        print("Run: gh auth login")
        sys.exit(1)

    print()
    print(f"{BOLD}{CYAN}╔═══════════════════════════════════════════════════════════════╗{NC}")
    print(f"{BOLD}{CYAN}║                  GitHub Fork Auto-Sync                        ║{NC}")
    print(f"{BOLD}{CYAN}╚═══════════════════════════════════════════════════════════════╝{NC}")
    print()

    print(f"Fetching forks from {args.org}...")
    discovered = discover_forks(args.org, args.topic, args.limit)
    if discovered is None:
        print(f"{RED}Aborting: repository discovery failed - nothing was processed.{NC}")
        print()
        sys.exit(2)
    repos, truncated = discovered

    if args.repo:
        repos = [r for r in repos if r["name"] == args.repo]

    filters = ["forks only"]
    if args.topic:
        filters.append(f"topic '{args.topic}'")
    if args.repo:
        filters.append(f"repo '{args.repo}'")
    print(f"Found {len(repos)} repositories ({', '.join(filters)})")

    if truncated:
        print(f"{YELLOW}[WARN] Result hit --limit {args.limit}; the org may hold "
              f"more repositories that were not processed.{NC}")
    print()

    if not repos:
        print(f"{YELLOW}No repositories to process{NC}")
        print()
        sys.exit(0)

    if args.dry_run:
        print(f"{YELLOW}DRY RUN - No changes will be made{NC}")
        print()

    results = []
    for repo in repos:
        name = repo["nameWithOwner"]
        print(f"{CYAN}→{NC} {name}")
        result = process_repo(name, args.dry_run, create_issues=not args.no_issues)
        results.append(result)

        stages = f"  mirror({result['mirror'] or '?'}): {render(result['stage_a'])}"
        if result["stage_b"] != "skipped":
            stages += f"  →  {result['target']}: {render(result['stage_b'])}"
        print(stages)
        if result["detail"]:
            print(f"  {DIM}{result['detail']}{NC}")

    # ---- Summary -----------------------------------------------------------
    def count(*statuses):
        return sum(1 for r in results
                   if r["stage_a"] in statuses or r["stage_b"] in statuses)

    changed = count("synced", "integrated")
    conflicts = count("conflict")
    errors = count("error", "no-mirror-branch", "not-a-fork")

    print()
    print(f"{BOLD}Summary:{NC}")
    print(f"  Processed:  {len(results)}")
    print(f"  Updated:    {GREEN}{changed}{NC}")
    print(f"  Conflicts:  {YELLOW}{conflicts}{NC}")
    print(f"  Errors:     {RED}{errors}{NC}")

    if conflicts or errors:
        print()
        for r in results:
            for stage in ("stage_a", "stage_b"):
                if r[stage] in ("conflict", "error", "no-mirror-branch", "not-a-fork"):
                    print(f"  {render(r[stage])}  {r['repo']}  {DIM}{r['detail']}{NC}")
    print()

    # Conflicts are an expected, actionable state and must not read as success.
    sys.exit(1 if (conflicts or errors) else 0)


if __name__ == "__main__":
    main()
