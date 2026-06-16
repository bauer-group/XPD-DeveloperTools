#!/usr/bin/env python3
"""repo-mirror -- snapshot a folder tree of git repositories and restore it 1:1.

Two modes:

    scan     Walk a root folder, find every git repository (including nested
             sub-repos), and write a JSON manifest describing the folder
             skeleton + each repo's remotes / current branch.

    restore  Read the manifest on another machine: recreate the folder
             skeleton, clone missing repos, and fast-forward repos that
             already exist. Existing data is never overwritten or reset.

Design notes
------------
* The only third-party dependency is `rich` (console UX). Git is driven via
  the `git` CLI through subprocess -- robust, no GitPython surprises.
* A genuine independent repo has `.git` as a *directory*. A submodule or a
  linked worktree has `.git` as a *file* (a gitdir pointer). We record the
  former (and keep descending into it to catch nested clones) and skip the
  latter -- those are restored automatically by `git clone --recurse-submodules`.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
except ModuleNotFoundError:
    sys.stderr.write(
        "repo-mirror requires the 'rich' package on the host.\n"
        "Install it with:\n"
        "    pip install -r scripts/requirements.txt\n"
        "  (or)  pip install rich\n"
    )
    sys.exit(1)

SCHEMA = "repo-mirror/1"
TOOL_VERSION = "1.0.0"

# Top-level folders to ignore by default (the user's stated exclusions).
DEFAULT_IGNORES = ["BAUER GROUP Products*", "Z*"]


def default_root() -> str:
    """Cross-platform default base path.

    Prefers C:\\Projects when it exists (the primary Windows workstation
    layout); otherwise falls back to the current working directory so the
    tool stays useful on Linux/macOS.
    """
    preferred = Path("C:/Projects")
    if os.name == "nt" and preferred.is_dir():
        return str(preferred)
    return os.getcwd()

# Heavy / reproducible folders that never hold repos we want to mirror.
# Pruned for scan speed; disable with --deep.
PRUNE_DIRS = {
    "node_modules", ".venv", "venv", "env", ".tox", "__pycache__",
    ".next", ".nuxt", ".svelte-kit", "dist", "build", ".gradle",
    "bin", "obj", "target", ".terraform", "vendor",
}

console = Console()


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
def run_git(args: list[str], cwd: str | os.PathLike | None = None,
            timeout: int = 600) -> tuple[int, str, str]:
    """Run a git command, returning (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "", "git executable not found on PATH"


def read_submodule_paths(path: str) -> list[str]:
    """Return submodule paths declared in a repo's .gitmodules (authoritative).

    Reads the tracked .gitmodules directly, so it works even when the
    submodules were never `git submodule init`-ed on this machine.
    """
    gm = os.path.join(path, ".gitmodules")
    if not os.path.isfile(gm):
        return []
    rc, out, _ = run_git(
        ["config", "--file", gm, "--get-regexp", r"^submodule\..*\.path$"], cwd=path
    )
    if rc != 0:
        return []
    paths = []
    for line in out.splitlines():
        _, _, sub = line.partition(" ")
        if sub.strip():
            paths.append(sub.strip())
    return sorted(paths)


def read_repo(path: str) -> dict:
    """Collect the data needed to recreate a repo: remotes + branch + submodules."""
    remotes: dict[str, str] = {}
    rc, out, _ = run_git(["config", "--get-regexp", r"^remote\..*\.url"], cwd=path)
    if rc == 0:
        for line in out.splitlines():
            key, _, url = line.partition(" ")
            m = re.match(r"^remote\.(.+)\.url$", key.strip())
            if m and url.strip():
                remotes[m.group(1)] = url.strip()

    rc, out, _ = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=path)
    if rc == 0 and out.strip():
        head, detached = out.strip(), False
    else:
        rc2, out2, _ = run_git(["rev-parse", "HEAD"], cwd=path)
        head, detached = (out2.strip() if rc2 == 0 else None), True

    default_remote = (
        "origin" if "origin" in remotes
        else (sorted(remotes)[0] if remotes else None)
    )
    return {
        "remotes": remotes,
        "default_remote": default_remote,
        "head": head,
        "detached": detached,
        "submodules": read_submodule_paths(path),
    }


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def is_excluded(name: str, rel_posix: str, patterns: list[str]) -> bool:
    """Glob-match a folder against exclude patterns.

    Each pattern is tried against both the folder *name* (e.g. ``Z*``,
    ``node_modules``) and its *relative posix path* from the scan root
    (e.g. ``eCommerce/Shopware5``, ``**/legacy``), so callers can exclude
    either by name anywhere in the tree or by a specific path.
    """
    return any(
        fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_posix, pat)
        for pat in patterns
    )


def is_subpath(child: str, parent: str) -> bool:
    """True if `child` lives strictly under `parent` (case-insensitive)."""
    c, p = os.path.normcase(child), os.path.normcase(parent)
    return c != p and c.startswith(p + os.sep)


def scan(root: str, exclude_patterns: list[str], deep: bool,
         prune_dirs: set[str]) -> dict:
    root = os.path.abspath(root)
    repos: list[dict] = []
    directories: list[str] = []
    repo_roots: list[str] = []
    # Absolute (normcased) paths of registered submodules -- skipped entirely
    # because `git clone --recurse-submodules` restores them from the parent.
    submodule_paths: set[str] = set()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Scanning[/] {task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("...", total=None)
        for dirpath, dirnames, _ in os.walk(root, topdown=True):
            dirnames.sort()
            here = os.path.normcase(os.path.abspath(dirpath))
            rel = os.path.relpath(dirpath, root)
            rel_norm = "" if rel == "." else rel.replace(os.sep, "/")
            git_entry = os.path.join(dirpath, ".git")

            # Prune children in place: never descend into .git, user-excluded
            # folders (by name OR relative path), or heavy/reproducible dirs.
            kept = []
            for d in dirnames:
                if d == ".git":
                    continue
                child_rel = f"{rel_norm}/{d}" if rel_norm else d
                if is_excluded(d, child_rel, exclude_patterns):
                    continue
                if not deep and d in prune_dirs:
                    continue
                kept.append(d)
            dirnames[:] = kept

            # Authoritative submodule check: a path declared in a parent's
            # .gitmodules is owned by that parent -- do not record or descend.
            if here in submodule_paths:
                dirnames[:] = []
                continue

            if os.path.isdir(git_entry):
                # Genuine repo (independent clone) -> record, keep descending
                # so we still find nested sub-repos beneath it.
                info = read_repo(dirpath)
                info["path"] = rel_norm
                info["nested"] = any(is_subpath(dirpath, r) for r in repo_roots)
                repos.append(info)
                repo_roots.append(dirpath)
                for sub in info.get("submodules", []):
                    sub_abs = os.path.normcase(
                        os.path.abspath(os.path.join(dirpath, *sub.split("/")))
                    )
                    submodule_paths.add(sub_abs)
                progress.update(task, description=rel_norm or ".")
                continue

            if os.path.isfile(git_entry):
                # .git file = initialised submodule or linked worktree.
                # git restores it for us; don't record, don't descend.
                dirnames[:] = []
                continue

            # Plain scaffold folder -- record only if not inside a repo.
            if rel_norm and not any(is_subpath(dirpath, r) for r in repo_roots):
                directories.append(rel_norm)

    return {
        "schema": SCHEMA,
        "tool": "repo-mirror",
        "version": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "host": socket.gethostname(),
            "platform": sys.platform,
            "root": root,
            "exclude_patterns": exclude_patterns,
            "prune_dirs": sorted(prune_dirs) if not deep else [],
            "deep": deep,
        },
        "directories": sorted(directories),
        "repositories": sorted(repos, key=lambda r: r["path"].lower()),
    }


def print_scan_summary(manifest: dict) -> None:
    repos = manifest["repositories"]
    nested = [r for r in repos if r.get("nested")]
    with_subs = [r for r in repos if r.get("submodules")]
    no_remote = [r for r in repos if not r.get("default_remote")]
    total_subs = sum(len(r.get("submodules", [])) for r in repos)

    table = Table(title="Scan result", title_style="bold", show_edge=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="bold")
    table.add_row("Repositories", str(len(repos)))
    table.add_row("  of which nested sub-repos", str(len(nested)))
    table.add_row("  with git submodules", f"{len(with_subs)} ({total_subs} submodules)")
    table.add_row("  without a remote (local-only)", str(len(no_remote)))
    table.add_row("Scaffold directories", str(len(manifest["directories"])))
    console.print(table)

    if nested:
        console.print("[dim]Nested sub-repos detected:[/]")
        for r in nested:
            console.print(f"  [magenta]{r['path']}[/]")
    if with_subs:
        console.print("[dim]Repos with submodules (restored via --recurse-submodules):[/]")
        for r in with_subs:
            console.print(f"  [blue]{r['path']}[/] [dim]({len(r['submodules'])})[/]")
    if no_remote:
        console.print("[yellow]Local-only repos (cannot be cloned on restore):[/]")
        for r in no_remote:
            console.print(f"  [yellow]{r['path']}[/]")


# --------------------------------------------------------------------------- #
# restore / update
# --------------------------------------------------------------------------- #
def decide_update_action(repo_path: Path) -> tuple[bool, str]:
    """Policy gate for an existing repo. Return (do_pull, reason).

    Default policy is deliberately conservative -- it honours
    "vorhandene repos werden nicht angefasst":
      * uncommitted changes  -> leave untouched
      * detached HEAD        -> leave untouched
      * no upstream branch   -> leave untouched
      * otherwise            -> fast-forward only (never a merge/rebase/reset)
    """
    rc, out, _ = run_git(["status", "--porcelain"], cwd=repo_path)
    if out.strip():
        return False, "uncommitted changes (left untouched)"

    rc, out, _ = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo_path)
    if rc != 0 or not out.strip():
        return False, "detached HEAD (left untouched)"

    rc, _, _ = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=repo_path
    )
    if rc != 0:
        return False, "no upstream tracking branch"

    return True, "fast-forward"


def process_repo(entry: dict, target_root: str, do_clone: bool,
                 do_update: bool, dry_run: bool) -> dict:
    rel = entry["path"]
    target = Path(target_root).joinpath(*rel.split("/"))
    remotes: dict = entry.get("remotes", {})
    default_remote = entry.get("default_remote")
    url = remotes.get(default_remote) if default_remote else None

    def result(status: str, detail: str = "") -> dict:
        return {"path": rel, "status": status, "detail": detail}

    is_repo = (target / ".git").exists()  # dir (repo) or file (submodule)

    # ---- existing repo -> update --------------------------------------- #
    if is_repo:
        if not do_update:
            return result("skipped", "update disabled")
        ok, reason = decide_update_action(target)
        if not ok:
            return result("skipped", reason)
        if dry_run:
            return result("would-update", reason)
        run_git(["fetch", "--prune", "--tags", "--quiet"], cwd=target)
        rc, _, err = run_git(["pull", "--ff-only", "--quiet"], cwd=target)
        if rc != 0:
            last = err.strip().splitlines()[-1] if err.strip() else "unknown"
            return result("failed", f"pull failed: {last}")
        # Correct submodule handling: bring submodule working trees in sync
        # with the (now fast-forwarded) superproject, recursively.
        if entry.get("submodules"):
            run_git(["submodule", "update", "--init", "--recursive"], cwd=target)
            return result("updated", reason + " +submodules")
        return result("updated", reason)

    # ---- something non-git already sits there -> never overwrite -------- #
    if target.exists() and any(target.iterdir()):
        return result("skipped", "path exists and is not a git repo")

    # ---- clone --------------------------------------------------------- #
    if not do_clone:
        return result("skipped", "clone disabled")
    if not url:
        return result("skipped", "no remote url to clone from")
    if dry_run:
        return result("would-clone", url)

    target.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = run_git(["clone", "--recurse-submodules", url, str(target)])
    if rc != 0:
        return result("failed", f"clone failed: {err.strip().splitlines()[-1] if err.strip() else 'unknown'}")

    # Add any additional remotes and restore the recorded branch.
    for name, u in remotes.items():
        if name != default_remote:
            run_git(["remote", "add", name, u], cwd=target)
    head = entry.get("head")
    if head and not entry.get("detached"):
        run_git(["checkout", head], cwd=target)
    return result("cloned", entry.get("head") or "")


def restore(manifest: dict, target_root: str, do_clone: bool, do_update: bool,
            jobs: int, dry_run: bool) -> None:
    target_root = os.path.abspath(target_root)

    # 1) Recreate the folder skeleton (existing folders are left as-is).
    created = 0
    for rel in manifest.get("directories", []):
        p = Path(target_root).joinpath(*rel.split("/"))
        if not p.exists():
            created += 1
            if not dry_run:
                p.mkdir(parents=True, exist_ok=True)
    console.print(
        f"[blue]Skeleton:[/] {created} folder(s) "
        f"{'would be ' if dry_run else ''}created under {target_root}"
    )

    # 2) Clone / update every repository (in parallel).
    repos = manifest.get("repositories", [])
    results: list[dict] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Repositories", total=len(repos))
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(process_repo, e, target_root, do_clone, do_update, dry_run)
                for e in repos
            ]
            for fut in futures:
                res = fut.result()
                results.append(res)
                colour = {
                    "cloned": "green", "updated": "green",
                    "would-clone": "cyan", "would-update": "cyan",
                    "skipped": "yellow", "failed": "red",
                }.get(res["status"], "white")
                progress.console.print(
                    f"[{colour}]{res['status']:<13}[/] {res['path']}"
                    + (f"  [dim]{res['detail']}[/]" if res["detail"] else "")
                )
                progress.advance(task)

    print_restore_summary(results)


def print_restore_summary(results: list[dict]) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    table = Table(title="Restore result", title_style="bold", show_edge=False)
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    for status in ("cloned", "updated", "would-clone", "would-update", "skipped", "failed"):
        if status in counts:
            table.add_row(status, str(counts[status]))
    console.print(table)

    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        console.print("[red]Failures:[/]")
        for r in failed:
            console.print(f"  [red]{r['path']}[/] -- {r['detail']}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo_mirror.py",
        description="Snapshot a folder tree of git repos and restore it 1:1 elsewhere.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", aliases=["create"], help="Analyse a tree -> JSON manifest.")
    p_scan.add_argument("--root", default=default_root(),
                        help="Root folder to scan (default: %(default)s).")
    p_scan.add_argument("--output", "-o", default="repos.json", help="Manifest output path.")
    p_scan.add_argument("-x", "--exclude", "--ignore", action="append", default=[],
                        dest="exclude", metavar="GLOB",
                        help="Exclude a folder by name OR relative path glob "
                             "(repeatable), e.g. -x 'Archive*' -x 'eCommerce/*'.")
    p_scan.add_argument("--no-default-excludes", "--no-default-ignores",
                        dest="no_default_excludes", action="store_true",
                        help=f"Drop the built-in excludes {DEFAULT_IGNORES}.")
    p_scan.add_argument("--prune-dir", action="append", default=[], metavar="NAME",
                        help="Add a folder name to the heavy/skip-for-speed set "
                             "(repeatable). Ignored when --deep is set.")
    p_scan.add_argument("--deep", action="store_true",
                        help="Also descend into node_modules/.venv/dist/... (slower).")

    p_res = sub.add_parser("restore", aliases=["update"],
                           help="Recreate folders, clone missing repos, update existing ones.")
    p_res.add_argument("--input", "-i", default="repos.json", help="Manifest to restore from.")
    p_res.add_argument("--target", default=default_root(),
                       help="Target root folder (default: %(default)s).")
    p_res.add_argument("--no-clone", action="store_true", help="Do not clone missing repos.")
    p_res.add_argument("--no-update", action="store_true", help="Do not update existing repos.")
    p_res.add_argument("--jobs", "-j", type=int, default=min(8, (os.cpu_count() or 4)),
                       help="Parallel git workers (default: %(default)s).")
    p_res.add_argument("--dry-run", action="store_true", help="Show actions without touching disk.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in ("scan", "create"):
        patterns = list(args.exclude)
        if not args.no_default_excludes:
            patterns = DEFAULT_IGNORES + patterns
        prune_dirs = PRUNE_DIRS | set(args.prune_dir)
        console.print(f"[bold]Scanning[/] {os.path.abspath(args.root)}")
        console.print(f"[dim]Excluding:[/] {patterns or '(none)'}")
        if not args.deep:
            console.print(f"[dim]Pruning (speed):[/] {sorted(prune_dirs)}")
        manifest = scan(args.root, patterns, args.deep, prune_dirs)
        Path(args.output).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print_scan_summary(manifest)
        console.print(f"[green]Manifest written:[/] {os.path.abspath(args.output)}")
        return 0

    # restore / update
    if not os.path.isfile(args.input):
        console.print(f"[red]Manifest not found:[/] {args.input}")
        return 1
    manifest = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        console.print(f"[yellow]Warning:[/] unexpected schema '{manifest.get('schema')}'.")
    restore(
        manifest,
        target_root=args.target,
        do_clone=not args.no_clone,
        do_update=not args.no_update,
        jobs=args.jobs,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[red]Aborted.[/]")
        sys.exit(130)
