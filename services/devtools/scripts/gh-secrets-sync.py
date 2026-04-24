#!/usr/bin/env python3
# @name: gh-secrets-sync
# @description: Sync a local .env file to GitHub repository secrets (push + prune obsolete)
# @category: github
# @usage: gh-secrets-sync [--env <path>] [--example <path>] [--repo <owner/repo>] [--dry-run] [--yes]
"""
gh-secrets-sync.py — bidirectional .env ↔ GitHub secrets sync.

Push adds/overwrites. Sync (this tool) additionally DELETES GitHub
secrets that were removed from `.env`, but only if they are listed in
`.env.example`. External secrets (Teams webhook, Codecov token etc.)
are invisible to the sync and never touched.

  .env.example acts as the allowlist of project-managed secrets.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Set, Tuple

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
NC = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def run_gh(args: list, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["gh"] + args
    return subprocess.run(cmd, capture_output=capture, text=True, check=check)


def check_gh_auth() -> bool:
    try:
        subprocess.run(["gh", "auth", "status"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def parse_env_keys(path: Path, include_commented: bool = False) -> Set[str]:
    """Extract KEY names from a dotenv file. Optionally include commented lines."""
    keys: Set[str] = set()
    if not path.exists():
        return keys
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        is_comment = line.startswith("#")
        if is_comment and not include_commented:
            continue
        stripped = line.lstrip("#").lstrip() if is_comment else line
        # KEY=... — key must start with uppercase letter, [A-Z0-9_]+
        eq = stripped.find("=")
        if eq <= 0:
            continue
        key = stripped[:eq].strip()
        if not key or not key[0].isalpha() or not key[0].isupper():
            continue
        if not all(c.isupper() or c.isdigit() or c == "_" for c in key):
            continue
        keys.add(key)
    return keys


def gh_secret_list(repo: str = None) -> Set[str]:
    args = ["secret", "list", "--json", "name"]
    if repo:
        args += ["-R", repo]
    out = run_gh(args).stdout
    return {entry["name"] for entry in json.loads(out)}


def gh_secret_set_from_file(env_path: Path, repo: str = None) -> None:
    args = ["secret", "set", "-f", str(env_path)]
    if repo:
        args += ["-R", repo]
    subprocess.run(["gh"] + args, check=True)


def gh_secret_delete(name: str, repo: str = None) -> None:
    args = ["secret", "delete", name]
    if repo:
        args += ["-R", repo]
    subprocess.run(["gh"] + args, check=True)


def detect_repo() -> str:
    """Detect owner/repo from current working directory's git origin via gh."""
    try:
        out = run_gh(["repo", "view", "--json", "nameWithOwner"]).stdout
        return json.loads(out)["nameWithOwner"]
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="gh-secrets-sync",
        description="Sync local .env to GitHub repository secrets (push + prune obsolete).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  gh-secrets-sync --dry-run            # preview the plan
  gh-secrets-sync                      # apply (prompts before deletions)
  gh-secrets-sync --yes                # apply without prompt (CI-safe)
  gh-secrets-sync -R owner/other-repo  # target a different repo
""",
    )
    parser.add_argument("--env", default=".env", help="Path to .env file (default: ./.env)")
    parser.add_argument(
        "--example",
        default=".env.example",
        help="Path to .env.example, the allowlist of managed keys (default: ./.env.example)",
    )
    parser.add_argument(
        "-R",
        "--repo",
        default=None,
        help="Target repo as owner/name (default: detect from current dir's origin)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan, make no changes")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation before deletions")
    args = parser.parse_args()

    if not check_gh_auth():
        print(f"{RED}error: gh CLI is not authenticated. Run `gh auth login` first.{NC}", file=sys.stderr)
        return 2

    env_path = Path(args.env).resolve()
    example_path = Path(args.example).resolve()

    if not env_path.exists():
        print(f"{RED}error: {env_path} does not exist{NC}", file=sys.stderr)
        return 2
    if not example_path.exists():
        print(
            f"{YELLOW}warning: {example_path} does not exist — allowlist empty, no deletions possible{NC}",
            file=sys.stderr,
        )

    repo = args.repo or detect_repo()
    if not repo:
        print(
            f"{RED}error: could not detect repo from current directory. Pass --repo owner/name.{NC}",
            file=sys.stderr,
        )
        return 2

    env_keys = parse_env_keys(env_path)
    universe = parse_env_keys(example_path, include_commented=True)
    gh_keys = gh_secret_list(repo)

    to_delete = sorted(k for k in gh_keys if k in universe and k not in env_keys)
    external = sorted(k for k in gh_keys if k not in universe)
    already = sorted(k for k in env_keys if k in gh_keys)
    adding = sorted(k for k in env_keys if k not in gh_keys)

    print(f"\n{BOLD}Target:{NC}        {CYAN}{repo}{NC}")
    print(f"{BOLD}.env:{NC}          {len(env_keys)} key(s) set")
    print(f"{BOLD}.env.example:{NC}  {len(universe)} managed key(s) (allowlist)")
    print(f"{BOLD}GitHub:{NC}        {len(gh_keys)} secret(s) currently")

    print(f"\n{BOLD}Plan:{NC}")
    if adding:
        print(f"  {GREEN}+ add    :{NC} {len(adding)} — {', '.join(adding)}")
    if already:
        print(f"  {CYAN}~ update :{NC} {len(already)} — {', '.join(already)}")
    if to_delete:
        print(f"  {RED}- DELETE :{NC} {len(to_delete)} — {', '.join(to_delete)}")
    else:
        print(f"  {DIM}- delete : (none){NC}")
    if external:
        print(f"  {DIM}· skip   :{NC} {len(external)} external — {', '.join(external)}")

    if args.dry_run:
        print(f"\n{DIM}(dry-run: no changes applied){NC}")
        return 0

    will_mutate = bool(adding or already or to_delete)
    if not will_mutate:
        print(f"\n{GREEN}✓ Nothing to sync.{NC}")
        return 0

    if to_delete and not args.yes:
        try:
            answer = input(f"\nDelete {len(to_delete)} GitHub secret(s)? [y/N] ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{YELLOW}Aborted.{NC}")
            return 1
        if answer.lower() not in ("y", "yes"):
            print(f"{YELLOW}Aborted.{NC}")
            return 1

    if adding or already:
        print(f"\n{BOLD}Pushing .env → GitHub...{NC}")
        gh_secret_set_from_file(env_path, repo)

    for name in to_delete:
        print(f"\n{BOLD}Deleting secret {name}...{NC}")
        gh_secret_delete(name, repo)

    print(f"\n{GREEN}✓ Sync complete.{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
