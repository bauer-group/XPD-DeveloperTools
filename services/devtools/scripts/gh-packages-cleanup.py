#!/usr/bin/env python3
# @name: gh-packages-cleanup
# @description: Clean old package versions from GitHub Packages
# @category: github
# @usage: gh-packages-cleanup.py [-o <org>] [-t <type>] [--execute]
"""
gh-packages-cleanup.py - GitHub Packages Cleanup Tool
Löscht alte Paketversionen und behält nur die aktuelle Version (mit allen Tags).
Unterstützt: container (Docker), nuget, npm, maven, pypi, rubygems
"""

import sys
import json
import subprocess
import argparse
from typing import List, Dict, Optional, Set
from collections import defaultdict

# Farben
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
NC = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'

PACKAGE_TYPES = ["container", "nuget", "npm", "maven", "pypi", "rubygems"]


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


def get_packages(org: str, package_type: Optional[str] = None, verbose: bool = True) -> List[Dict]:
    """Get all packages for an organization."""
    packages = []

    types_to_check = [package_type] if package_type else PACKAGE_TYPES

    for pkg_type in types_to_check:
        if verbose:
            print(f"  Scanning {pkg_type}...", end=" ", flush=True)

        # Run gh api and capture both stdout and stderr
        cmd = ["gh", "api", f"/orgs/{org}/packages?package_type={pkg_type}", "--paginate"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout.strip()
            error = result.stderr.strip()

            if result.returncode != 0:
                if verbose:
                    if "403" in error or "read:packages" in error.lower():
                        print(f"{RED}no access (missing read:packages scope){NC}")
                    elif "404" in error:
                        print(f"{DIM}none found{NC}")
                    elif "422" in error or "not supported" in error.lower():
                        print(f"{DIM}not available{NC}")
                    else:
                        # Show first line of error for debugging
                        err_msg = error.split('\n')[0][:50] if error else "unknown"
                        print(f"{RED}error: {err_msg}{NC}")
                continue

            if output:
                try:
                    pkgs = json.loads(output)
                    for pkg in pkgs:
                        pkg["package_type"] = pkg_type
                    packages.extend(pkgs)
                    if verbose:
                        print(f"{GREEN}{len(pkgs)} found{NC}")
                except json.JSONDecodeError:
                    if verbose:
                        print(f"{RED}parse error{NC}")
            else:
                if verbose:
                    print(f"{DIM}none{NC}")

        except FileNotFoundError:
            if verbose:
                print(f"{RED}gh not found{NC}")
            break

    return packages


def get_package_versions(org: str, package_type: str, package_name: str) -> List[Dict]:
    """Get all versions of a package."""
    import urllib.parse

    # URL-encode the package name (important for containers with slashes)
    encoded_name = urllib.parse.quote(package_name, safe='')

    cmd = ["gh", "api", f"/orgs/{org}/packages/{package_type}/{encoded_name}/versions", "--paginate"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []
        output = result.stdout.strip()
        if not output:
            return []
        return json.loads(output)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def delete_package_version(org: str, package_type: str, package_name: str,
                          version_id: int, dry_run: bool = True) -> bool:
    """Delete a specific package version."""
    import urllib.parse

    if dry_run:
        return True

    # URL-encode the package name (important for containers with slashes)
    encoded_name = urllib.parse.quote(package_name, safe='')

    try:
        run_gh([
            "api", "-X", "DELETE",
            f"/orgs/{org}/packages/{package_type}/{encoded_name}/versions/{version_id}"
        ])
        return True
    except subprocess.CalledProcessError:
        return False


def get_container_digest(version: Dict) -> Optional[str]:
    """Extract the container digest from version metadata."""
    # Container versions have a 'name' field that contains the digest
    name = version.get("name", "")
    if name.startswith("sha256:"):
        return name

    # Also check metadata
    metadata = version.get("metadata", {})
    container = metadata.get("container", {})

    # Check for digest in tags
    tags = container.get("tags", [])

    # The name field for containers is usually the digest
    return name if name else None


def get_container_tags(version: Dict) -> List[str]:
    """Extract container tags from version metadata."""
    metadata = version.get("metadata", {})
    container = metadata.get("container", {})
    return container.get("tags", [])


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def analyze_container_package(versions: List[Dict], keep_count: int = 1) -> Dict:
    """Analyze container package versions, grouping by digest."""
    # Group versions by digest
    digest_groups: Dict[str, List[Dict]] = defaultdict(list)

    for version in versions:
        digest = get_container_digest(version)
        if digest:
            digest_groups[digest].append(version)

    # Sort digests by newest version's updated_at
    sorted_digests = sorted(
        digest_groups.keys(),
        key=lambda d: max(v.get("updated_at", "") for v in digest_groups[d]),
        reverse=True
    )

    # Determine which to keep
    keep_digests = set(sorted_digests[:keep_count])

    to_keep = []
    to_delete = []

    for digest in sorted_digests:
        versions_for_digest = digest_groups[digest]
        if digest in keep_digests:
            to_keep.extend(versions_for_digest)
        else:
            to_delete.extend(versions_for_digest)

    # Collect all tags for kept versions
    kept_tags = []
    for v in to_keep:
        kept_tags.extend(get_container_tags(v))

    return {
        "to_keep": to_keep,
        "to_delete": to_delete,
        "kept_digests": keep_digests,
        "kept_tags": kept_tags
    }


def analyze_standard_package(versions: List[Dict], keep_count: int = 1) -> Dict:
    """Analyze standard package versions (nuget, npm, etc.)."""
    # Sort by updated_at (newest first)
    sorted_versions = sorted(
        versions,
        key=lambda v: v.get("updated_at", ""),
        reverse=True
    )

    to_keep = sorted_versions[:keep_count]
    to_delete = sorted_versions[keep_count:]

    return {
        "to_keep": to_keep,
        "to_delete": to_delete
    }


def print_header():
    """Print tool header."""
    print()
    print(f"{BOLD}{CYAN}+---------------------------------------------------------------+{NC}")
    print(f"{BOLD}{CYAN}|              GitHub Packages Cleanup Tool                     |{NC}")
    print(f"{BOLD}{CYAN}+---------------------------------------------------------------+{NC}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Clean old package versions from GitHub Packages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run: show what would be deleted (default)
  gh-packages-cleanup.py -o bauer-group

  # Actually delete old versions
  gh-packages-cleanup.py -o bauer-group --execute

  # Only clean container images
  gh-packages-cleanup.py -o bauer-group -t container --execute

  # Only clean a specific package
  gh-packages-cleanup.py -o bauer-group -p my-package --execute

  # Keep last 3 versions instead of just 1
  gh-packages-cleanup.py -o bauer-group -k 3 --execute

  # Skip confirmation prompt
  gh-packages-cleanup.py -o bauer-group --execute --yes
        """
    )

    parser.add_argument(
        "-o", "--org",
        default="bauer-group",
        help="GitHub organization (default: bauer-group)"
    )
    parser.add_argument(
        "-t", "--type",
        choices=PACKAGE_TYPES,
        help="Filter by package type (container, nuget, npm, maven, pypi, rubygems)"
    )
    parser.add_argument(
        "-p", "--package",
        help="Only clean a specific package by name"
    )
    parser.add_argument(
        "-k", "--keep",
        type=int,
        default=1,
        help="Number of versions to keep (default: 1)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete versions (default is dry-run)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max packages to process (default: 100)"
    )

    args = parser.parse_args()

    # Check authentication
    if not check_gh_auth():
        print(f"{RED}[ERROR] GitHub CLI not authenticated{NC}")
        print("Run: gh auth login")
        sys.exit(1)

    print_header()

    dry_run = not args.execute

    if dry_run:
        print(f"{YELLOW}[DRY RUN MODE]{NC} - No changes will be made")
        print(f"Use --execute to actually delete versions")
        print()

    # Get packages
    print(f"Scanning packages in {BOLD}{args.org}{NC}...")
    packages = get_packages(args.org, args.type, verbose=True)
    print()

    if args.package:
        packages = [p for p in packages if p["name"] == args.package]

    packages = packages[:args.limit]

    if not packages:
        print(f"{YELLOW}No packages found{NC}")
        sys.exit(0)

    print(f"Found {len(packages)} package(s)")
    print()

    # Analyze all packages
    total_to_delete = 0
    total_to_keep = 0
    total_size_to_free = 0
    deletion_plan = []

    for pkg in packages:
        pkg_name = pkg["name"]
        pkg_type = pkg.get("package_type", pkg.get("package_type", "unknown"))

        versions = get_package_versions(args.org, pkg_type, pkg_name)

        if not versions:
            continue

        # Analyze based on package type
        if pkg_type == "container":
            analysis = analyze_container_package(versions, args.keep)
        else:
            analysis = analyze_standard_package(versions, args.keep)

        to_keep = analysis["to_keep"]
        to_delete = analysis["to_delete"]

        if not to_delete:
            print(f"{DIM}{pkg_name} ({pkg_type}): nothing to delete{NC}")
            continue

        # Calculate size to free
        size_to_free = sum(
            v.get("metadata", {}).get("container", {}).get("size", 0) or 0
            for v in to_delete
        )

        print(f"{BOLD}{pkg_name}{NC} ({pkg_type})")

        # Show what we're keeping
        if pkg_type == "container":
            kept_digests = analysis.get("kept_digests", set())
            kept_tags = analysis.get("kept_tags", [])
            for digest in kept_digests:
                tags_str = ", ".join(kept_tags) if kept_tags else "untagged"
                print(f"  {GREEN}+ Keep:{NC} {digest[:19]}... ({tags_str})")
        else:
            for v in to_keep:
                print(f"  {GREEN}+ Keep:{NC} {v.get('name', v.get('id'))}")

        # Show what we're deleting
        for v in to_delete:
            version_name = v.get("name", str(v.get("id")))
            if pkg_type == "container":
                tags = get_container_tags(v)
                tags_str = f" ({', '.join(tags)})" if tags else ""
                if len(version_name) > 19:
                    version_name = version_name[:19] + "..."
                print(f"  {RED}- Delete:{NC} {version_name}{tags_str}")
            else:
                print(f"  {RED}- Delete:{NC} {version_name}")

        if size_to_free > 0:
            print(f"  {DIM}Space to free: {format_size(size_to_free)}{NC}")

        print()

        total_to_keep += len(to_keep)
        total_to_delete += len(to_delete)
        total_size_to_free += size_to_free

        deletion_plan.append({
            "package": pkg,
            "to_delete": to_delete
        })

    # Summary
    print(f"{BOLD}{'='*60}{NC}")
    print(f"{BOLD}Summary:{NC}")
    print(f"  Packages scanned: {len(packages)}")
    print(f"  Versions to keep: {GREEN}{total_to_keep}{NC}")
    print(f"  Versions to delete: {RED}{total_to_delete}{NC}")
    if total_size_to_free > 0:
        print(f"  Space to free: ~{format_size(total_size_to_free)}")
    print()

    if total_to_delete == 0:
        print(f"{GREEN}Nothing to clean up!{NC}")
        sys.exit(0)

    if dry_run:
        print(f"{YELLOW}Run with --execute to delete these versions.{NC}")
        sys.exit(0)

    # Confirmation
    if not args.yes:
        print(f"{YELLOW}WARNING: This will permanently delete {total_to_delete} package version(s).{NC}")
        response = input("Continue? (yes/N): ")
        if response.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    print()
    print(f"Deleting {total_to_delete} version(s)...")
    print()

    # Execute deletions
    deleted = 0
    failed = 0

    for item in deletion_plan:
        pkg = item["package"]
        pkg_name = pkg["name"]
        pkg_type = pkg.get("package_type", "unknown")

        for version in item["to_delete"]:
            version_id = version["id"]
            version_name = version.get("name", str(version_id))

            if delete_package_version(args.org, pkg_type, pkg_name, version_id, dry_run=False):
                print(f"  {GREEN}+{NC} Deleted: {pkg_name} / {version_name[:30]}")
                deleted += 1
            else:
                print(f"  {RED}x{NC} Failed: {pkg_name} / {version_name[:30]}")
                failed += 1

    print()
    print(f"{BOLD}Results:{NC}")
    print(f"  {GREEN}Deleted: {deleted}{NC}")
    if failed > 0:
        print(f"  {RED}Failed: {failed}{NC}")
    print()


if __name__ == "__main__":
    main()
