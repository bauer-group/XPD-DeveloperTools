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

import os
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

# A single collector issue in one reporting repo, not one issue per fork:
# GitHub disables issues on forks by default, so per-fork issues mostly cannot
# be created at all. Matched by title prefix, so it survives body rewrites.
COLLECTOR_TITLE = "⚠️ Fork-Sync: Konflikte in {org}"

# Statuses that mean a repository needs a human.
PROBLEM_STATUSES = ("conflict", "error", "no-mirror-branch", "not-a-fork")


def gh(args: List[str], token: Optional[str] = None) -> Tuple[int, str]:
    """Run gh and return (returncode, combined stdout+stderr).

    Never raises - callers classify the outcome from the return code and text,
    because HTTP 409 (conflict) is an expected result here, not a failure.

    token overrides GH_TOKEN for this call only. The collector issue usually
    lives in a different organization than the forks, and a fine-grained PAT is
    scoped to exactly one resource owner, so one token cannot cover both.

    encoding is pinned to UTF-8: text=True alone decodes with the locale
    encoding, and on a cp1252 Windows console any non-Latin-1 byte in an API
    response kills the reader thread, leaving stdout as None next to a zero
    return code.
    """
    env = None
    if token:
        env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}
    result = subprocess.run(["gh"] + args, capture_output=True,
                            encoding="utf-8", errors="replace", env=env)
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


def upsert_collector_issue(issue_repo: str, org: str, problems: List[Dict],
                           run_url: Optional[str],
                           token: Optional[str] = None) -> bool:
    """Maintain a single collector issue listing every fork that needs attention.

    One issue in one repository, rather than one per fork: GitHub disables
    issues on forks by default, so a per-fork issue usually cannot be created
    at all.

    Reopens/updates while problems exist, closes once they are gone.
    """
    existing = find_collector_issue(issue_repo, org, token)

    if not problems:
        if existing:
            gh(["issue", "comment", str(existing), "-R", issue_repo,
                "--body", "Alle Forks synchronisieren wieder sauber - automatisch geschlossen."], token)
            rc, _ = gh(["issue", "close", str(existing), "-R", issue_repo], token)
            return rc == 0
        return True

    body = collector_body(org, problems, run_url)

    if existing:
        rc, _ = gh(["issue", "edit", str(existing), "-R", issue_repo, "--body", body], token)
        if rc == 0:
            gh(["issue", "reopen", str(existing), "-R", issue_repo], token)
        return rc == 0

    for label, color, desc in (
        ("sync", "1D76DB", "Upstream sync automation"),
        ("conflict", "B60205", "Requires manual resolution"),
        ("automated", "0E8A16", "Created by automation"),
    ):
        gh(["label", "create", label, "-R", issue_repo,
            "--color", color, "--description", desc], token)

    rc, _ = gh([
        "issue", "create", "-R", issue_repo,
        "--title", COLLECTOR_TITLE.format(org=org), "--body", body,
        "--label", "sync", "--label", "conflict", "--label", "automated",
    ], token)
    if rc != 0:
        # Labels may not exist and creation may be denied; the issue matters more.
        rc, _ = gh(["issue", "create", "-R", issue_repo,
                    "--title", COLLECTOR_TITLE.format(org=org), "--body", body], token)
    return rc == 0


def find_collector_issue(issue_repo: str, org: str,
                         token: Optional[str] = None) -> Optional[int]:
    """Return the number of this org's collector issue, open or closed.

    Matched on the full title including the org, so one reporting repo can hold
    a separate collector issue per organization.
    """
    rc, out = gh([
        "issue", "list", "-R", issue_repo, "--state", "all", "--limit", "100",
        "--json", "number,title",
    ], token)
    if rc != 0 or not out:
        return None

    wanted = COLLECTOR_TITLE.format(org=org)
    try:
        for issue in json.loads(out):
            if issue.get("title") == wanted:
                return issue["number"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def collector_body(org: str, problems: List[Dict], run_url: Optional[str]) -> str:
    lines = [
        f"## Fork-Sync: {len(problems)} Repositories brauchen Aufmerksamkeit",
        "",
        f"Organisation: `{org}`  ",
        "Erzeugt und aktualisiert von `gh-fork-autosync`.",
        "",
        "| Repository | Stufe | Status | Detail |",
        "|------------|-------|--------|--------|",
    ]
    for r in problems:
        for stage, label in (("stage_a", f"mirror `{r['mirror']}`"),
                             ("stage_b", f"integrate → `{r['target']}`")):
            if r[stage] in PROBLEM_STATUSES:
                lines.append(
                    f"| `{r['repo']}` | {label} | **{r[stage]}** | {r['detail']} |"
                )

    lines += [
        "",
        "### Auflösung eines divergierten Mirror-Branches",
        "",
        "```bash",
        "git clone https://github.com/<org>/<repo>.git && cd <repo>",
        "git checkout <mirror-branch>",
        "git remote add upstream https://github.com/<upstream>.git",
        "git fetch upstream",
        "git merge upstream/<mirror-branch>",
        "# Konflikte auflösen, dann:",
        "git push origin <mirror-branch>",
        "```",
        "",
        "Trägt der Fork eigene Commits auf dem Mirror-Branch, gehören diese auf",
        "einen Integrationsbranch (siehe `Bugsink` mit `workspace/main`) - der",
        "Mirror-Branch sollte dem Upstream folgen können.",
        "",
        "Dieses Issue schließt sich automatisch, sobald alle Forks wieder sauber",
        "synchronisieren.",
    ]
    if run_url:
        lines += ["", f"Letzter Lauf: {run_url}"]
    return "\n".join(lines)


def process_repo(repo: str, dry_run: bool) -> Dict:
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
        return result

    if status == "error":
        return result

    # ---- Stage B -----------------------------------------------------------
    if fork_default == mirror:
        return result

    status_b, detail_b = merge_forward(repo, base=fork_default, head=mirror)
    result["stage_b"] = status_b
    result["detail"] = f"{detail}; {detail_b}"

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

  # Collect every problem in one issue instead of just reporting
  gh-fork-autosync.py -o mirrored-projects --topic forked-repo \
      --issue-repo bauer-group/XPD-DeveloperTools

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
    parser.add_argument("--issue-repo",
                        help="owner/repo for the collector issue listing every "
                             "fork that needs attention (omit = report only). "
                             "Set ISSUE_GH_TOKEN if that repo needs a different "
                             "token than the forks")
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
        result = process_repo(name, args.dry_run)
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

    problems = [r for r in results
                if r["stage_a"] in PROBLEM_STATUSES or r["stage_b"] in PROBLEM_STATUSES]

    if problems:
        print()
        for r in problems:
            for stage in ("stage_a", "stage_b"):
                if r[stage] in PROBLEM_STATUSES:
                    print(f"  {render(r[stage])}  {r['repo']}  {DIM}{r['detail']}{NC}")
    print()

    # ---- Collector issue ---------------------------------------------------
    # The issue describes the state of a *full* scan. A run narrowed to one
    # repository sees only a slice, so an empty problem list there says nothing
    # about the rest of the org - closing on that basis would hide real
    # conflicts.
    if args.issue_repo and args.repo:
        print(f"{YELLOW}[WARN] --issue-repo ignored: a --repo run covers only "
              f"part of the org and must not update the collector issue.{NC}")
        print()
    elif args.issue_repo and not args.dry_run:
        run_url = None
        if os.environ.get("GITHUB_RUN_ID"):
            run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}"
                       f"/{os.environ.get('GITHUB_REPOSITORY', '')}"
                       f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")

        # Read from the environment rather than a CLI flag so the token never
        # appears in the process list.
        issue_token = os.environ.get("ISSUE_GH_TOKEN") or None

        if upsert_collector_issue(args.issue_repo, args.org, problems, run_url,
                                  issue_token):
            if problems:
                print(f"Collector issue updated in {args.issue_repo}")
            else:
                print(f"No problems - collector issue in {args.issue_repo} closed if it was open")
        else:
            # Do not let a broken report make a bad run look clean.
            print(f"{RED}[WARN] Could not maintain the collector issue in "
                  f"{args.issue_repo}{NC}", file=sys.stderr)
        print()

    # Conflicts are an expected, actionable state and must not read as success.
    sys.exit(1 if (conflicts or errors) else 0)


if __name__ == "__main__":
    main()
