#!/usr/bin/env python3
"""repo-mirror -- snapshot a folder tree of git repositories and restore it 1:1.

Two modes:

    scan     Walk a root folder, find every git repository (including nested
             sub-repos), and write a JSON manifest describing the folder
             skeleton + each repo's identity / remotes / current branch.

    restore  Read the manifest on another machine: recreate the folder
             skeleton, *relocate repos that moved in the source tree*, clone
             missing repos, and fast-forward repos that already exist.
             Existing data is never overwritten or reset.

Design notes
------------
* The only third-party dependency is `rich` (console UX). Git is driven via
  the `git` CLI through subprocess -- robust, no GitPython surprises.
* A genuine independent repo has `.git` as a *directory*. A submodule or a
  linked worktree has `.git` as a *file* (a gitdir pointer). We record the
  former (and keep descending into it to catch nested clones) and skip the
  latter -- those are restored automatically by `git clone --recurse-submodules`.
* Repos are matched by *identity*, never by path alone. Reorganising the
  source tree therefore migrates the target tree instead of producing a
  second clone next to the old one:
      1. the default remote URL, normalised (protocol, credentials, port,
         trailing `.git` and case are irrelevant),
      2. the root commit fingerprint -- `git rev-list --max-parents=0 --all`,
         which survives a repo being renamed or moved to another forge,
      3. any secondary remote URL (weak: never triggers a move on its own),
      4. the recorded path (weakest: only ever confirms "stay put").
  Pairing is a global greedy assignment over all (entry, target) candidates
  ordered by score, so the strongest evidence always wins and every repo is
  claimed at most once. A relocation that two targets tie for is reported as
  ambiguous and skipped -- never guessed.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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

SCHEMA = "repo-mirror/2"
# `repo-mirror/1` manifests restore fine -- they simply carry no root-commit
# fingerprint, so relocation falls back to remote-URL matching alone.
SUPPORTED_SCHEMAS = ("repo-mirror/1", "repo-mirror/2")
TOOL_VERSION = "1.1.0"

# Top-level folders to ignore by default (the user's stated exclusions).
DEFAULT_IGNORES = ["BAUER GROUP Products*", "Z*"]

# Scratch folder used to break move cycles (A -> B while B -> A) and
# case-only renames. Created on demand, removed again when empty.
STAGING_DIRNAME = ".repo-mirror-staging"

# Match scores -- higher is stronger evidence that two repos are the same one.
SCORE_DEFAULT_URL = 75   # same default remote  -> authoritative
SCORE_ROOT_COMMIT = 55   # same root commit(s)  -> survives renames/forge moves
SCORE_SECONDARY_URL = 45 # shares a non-default remote (fork/upstream overlap)
SCORE_SAME_PATH = 20     # nothing but the path agrees
# A repo is only *moved* on this much evidence. Weaker matches merely confirm
# that a repo already sitting at the recorded path may stay there.
MIN_RELOCATE_SCORE = SCORE_ROOT_COMMIT


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


def read_remotes(path: str) -> dict[str, str]:
    """Return {remote name: url} from the repo's *local* config."""
    remotes: dict[str, str] = {}
    rc, out, _ = run_git(
        ["config", "--local", "--get-regexp", r"^remote\..*\.url$"], cwd=path
    )
    if rc != 0:
        return remotes
    for line in out.splitlines():
        key, _, url = line.partition(" ")
        m = re.match(r"^remote\.(.+)\.url$", key.strip())
        if m and url.strip():
            remotes[m.group(1)] = url.strip()
    return remotes


def pick_default_remote(remotes: dict[str, str]) -> str | None:
    """`origin` if present, else the alphabetically first remote."""
    if "origin" in remotes:
        return "origin"
    return sorted(remotes)[0] if remotes else None


def read_root_commits(path: str) -> str | None:
    """Fingerprint a repo by its root commit(s) -- a path/URL independent id.

    Every clone of a repository shares the same parentless commits, so this
    identifies a repo even after it was renamed on the forge or migrated to a
    different host. `--all` (not just HEAD) keeps the value stable when the
    machines sit on different branches. Returns None for an empty repo.
    """
    rc, out, _ = run_git(["rev-list", "--max-parents=0", "--all"], cwd=path, timeout=300)
    if rc != 0:
        return None
    roots = sorted(out.split())
    return ",".join(roots) or None


def read_repo(path: str, submodules: list[str], fingerprint: bool) -> dict:
    """Collect the data needed to recreate and re-identify a repo."""
    remotes = read_remotes(path)

    rc, out, _ = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=path)
    if rc == 0 and out.strip():
        head, detached = out.strip(), False
    else:
        rc2, out2, _ = run_git(["rev-parse", "HEAD"], cwd=path)
        head, detached = (out2.strip() if rc2 == 0 else None), True

    return {
        "remotes": remotes,
        "default_remote": pick_default_remote(remotes),
        "head": head,
        "detached": detached,
        "root_commits": read_root_commits(path) if fingerprint else None,
        "submodules": submodules,
    }


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
def normalize_remote_url(url: str) -> str:
    """Reduce a remote URL to a comparable identity string.

    ``git@github.com:Org/Repo.git``, ``https://github.com/Org/Repo`` and
    ``ssh://git@github.com:22/org/repo.git/`` all collapse to
    ``github.com/org/repo`` -- so a protocol switch, an embedded credential or
    a trailing ``.git`` never makes the same repo look like a different one.
    """
    u = url.strip().replace("\\", "/").rstrip("/")
    if not u:
        return ""
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://(.+)$", u)
    if m:
        rest = m.group(1)
    else:
        # scp-like `[user@]host:path`. The {2,} guard keeps a Windows drive
        # letter (`C:/repos/x.git`) from being mistaken for a host.
        m = re.match(r"^(?:[^@/]+@)?(?P<host>[^:/]{2,}):(?P<path>.+)$", u)
        rest = f"{m.group('host')}/{m.group('path')}" if m else u
    head, sep, tail = rest.partition("/")
    head = head.rsplit("@", 1)[-1].split(":", 1)[0]  # drop credentials + port
    rest = head + sep + tail
    if rest.endswith(".git"):
        rest = rest[:-4]
    return rest.strip("/").lower()


@dataclass
class Identity:
    """The path-independent fingerprint of one repository."""
    path: str                       # posix, relative to the tree root
    urls: frozenset[str]            # all remotes, normalised
    default_url: str                # the default remote, normalised ("" if none)
    root_commits: str | None        # root commit fingerprint, if known

    @staticmethod
    def of(path: str, remotes: dict[str, str], default_remote: str | None,
           root_commits: str | None) -> "Identity":
        urls = {normalize_remote_url(u) for u in remotes.values()}
        default = remotes.get(default_remote or "", "")
        return Identity(
            path=path,
            urls=frozenset(u for u in urls if u),
            default_url=normalize_remote_url(default) if default else "",
            root_commits=root_commits or None,
        )


def norm_rel(rel: str) -> str:
    """Comparison key for a manifest-style relative path.

    Manifest paths are posix, but `os.path.normcase` is for *OS* paths -- on
    Windows it also rewrites `/` to `\\`, which silently breaks anything that
    splits on `/` afterwards. This keeps the separator and only applies the
    platform's case rules.
    """
    rel = rel.replace("\\", "/").strip("/")
    return rel.lower() if os.name == "nt" else rel


def path_bonus(a: str, b: str) -> int:
    """Tie-breaker: how much of the *tail* of two relative paths agrees.

    Keeps two clones of the same remote (e.g. `Prod/shop` and `Fork/shop`)
    paired with their nearest counterpart instead of being swapped.
    """
    na, nb = norm_rel(a), norm_rel(b)
    if na == nb:
        return 9
    sa = [s for s in na.split("/") if s]
    sb = [s for s in nb.split("/") if s]
    n = 0
    for x, y in zip(reversed(sa), reversed(sb)):
        if x != y:
            break
        n += 1
    return min(n, 8)


def match_score(src: Identity, dst: Identity) -> int:
    """0 = not the same repository; higher = stronger evidence.

    The tiers are deliberately far apart so that a global sort by score
    resolves fork/upstream look-alikes before weaker evidence is considered.
    """
    same_path = norm_rel(src.path) == norm_rel(dst.path)

    if src.default_url and src.default_url == dst.default_url:
        base = SCORE_DEFAULT_URL
    elif src.root_commits and src.root_commits == dst.root_commits:
        base = SCORE_ROOT_COMMIT
    elif src.urls & dst.urls:
        base = SCORE_SECONDARY_URL
    elif same_path and not (src.urls and dst.urls):
        # Local-only repo (or one side has no remotes at all): the path is the
        # only evidence there is. Enough to leave it alone, never to move it.
        base = SCORE_SAME_PATH
    elif same_path and src.root_commits and dst.root_commits:
        # Same path, both fingerprinted, but the fingerprints disagree --
        # genuinely different repositories that happen to share a folder name.
        return 0
    elif same_path:
        base = SCORE_SAME_PATH
    else:
        return 0

    return base + path_bonus(src.path, dst.path)


# --------------------------------------------------------------------------- #
# tree walk (shared by scan and the target index)
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
    return c != p and c.startswith(p.rstrip(os.sep) + os.sep)


@dataclass
class TreeItem:
    kind: str                 # "repo" | "dir"
    abs_path: str
    rel: str                  # posix, relative to the walked root
    submodules: list[str]
    nested: bool              # repo living inside another repo


def walk_tree(root: str, exclude_patterns: list[str], deep: bool,
              prune_dirs: set[str], skip_names: set[str] | None = None):
    """Yield every git repo and every scaffold folder under `root`.

    Shared by `scan` (source side) and by the target index used during
    `restore`, so both sides always see exactly the same universe of
    repositories -- which is what makes move detection trustworthy.
    """
    root = os.path.abspath(root)
    skip_names = skip_names or set()
    repo_roots: list[str] = []
    # Absolute (normcased) paths of registered submodules -- skipped entirely
    # because `git clone --recurse-submodules` restores them from the parent.
    submodule_paths: set[str] = set()

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
            if d == ".git" or d in skip_names:
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
            subs = read_submodule_paths(dirpath)
            for sub in subs:
                submodule_paths.add(os.path.normcase(
                    os.path.abspath(os.path.join(dirpath, *sub.split("/")))
                ))
            nested = any(is_subpath(dirpath, r) for r in repo_roots)
            repo_roots.append(dirpath)
            yield TreeItem("repo", dirpath, rel_norm, subs, nested)
            continue

        if os.path.isfile(git_entry):
            # .git file = initialised submodule or linked worktree.
            # git restores it for us; don't record, don't descend.
            dirnames[:] = []
            continue

        # Plain scaffold folder -- record only if not inside a repo.
        if rel_norm and not any(is_subpath(dirpath, r) for r in repo_roots):
            yield TreeItem("dir", dirpath, rel_norm, [], False)


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def scan(root: str, exclude_patterns: list[str], deep: bool,
         prune_dirs: set[str], fingerprint: bool, jobs: int) -> dict:
    root = os.path.abspath(root)
    found: list[TreeItem] = []
    directories: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Scanning[/] {task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("...", total=None)
        for item in walk_tree(root, exclude_patterns, deep, prune_dirs,
                              skip_names={STAGING_DIRNAME}):
            if item.kind == "repo":
                found.append(item)
            else:
                directories.append(item.rel)
            progress.update(task, description=item.rel or ".")

    # Reading each repo's metadata is I/O bound (2-3 git calls, plus the root
    # commit walk) -- do it in parallel once the tree walk itself is done.
    def describe(item: TreeItem) -> dict:
        info = read_repo(item.abs_path, item.submodules, fingerprint)
        info["path"] = item.rel
        info["nested"] = item.nested
        return info

    repos: list[dict] = []
    if found:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]Reading repositories"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("repos", total=len(found))
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                for info in pool.map(describe, found):
                    repos.append(info)
                    progress.advance(task)

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
            "fingerprint": fingerprint,
        },
        "directories": sorted(directories),
        "repositories": sorted(repos, key=lambda r: r["path"].lower()),
    }


def print_scan_summary(manifest: dict) -> None:
    repos = manifest["repositories"]
    nested = [r for r in repos if r.get("nested")]
    with_subs = [r for r in repos if r.get("submodules")]
    no_remote = [r for r in repos if not r.get("default_remote")]
    no_identity = [r for r in repos
                   if not r.get("default_remote") and not r.get("root_commits")]
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
    if no_identity:
        console.print(
            "[yellow]No identity (no remote, no root commit) -- these cannot be "
            "tracked across a move:[/]"
        )
        for r in no_identity:
            console.print(f"  [yellow]{r['path']}[/]")


# --------------------------------------------------------------------------- #
# target index + matching
# --------------------------------------------------------------------------- #
@dataclass
class TargetRepo:
    """A git repository that already exists in the target tree."""
    abs_path: str
    rel: str
    identity: Identity
    nested: bool = False
    # Filled in during the move phase: where this repo ended up.
    placed_rel: str = field(default="", init=False)


def index_target(root: str, exclude_patterns: list[str], deep: bool,
                 prune_dirs: set[str], fingerprint: bool, jobs: int) -> list[TargetRepo]:
    """Find and identify every repo that already exists under `root`."""
    if not os.path.isdir(root):
        return []

    found: list[TreeItem] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Indexing target[/] {task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("...", total=None)
        for item in walk_tree(root, exclude_patterns, deep, prune_dirs):
            if item.kind == "repo":
                found.append(item)
                progress.update(task, description=item.rel or ".")

    def describe(item: TreeItem) -> TargetRepo:
        remotes = read_remotes(item.abs_path)
        roots = read_root_commits(item.abs_path) if fingerprint else None
        ident = Identity.of(item.rel, remotes, pick_default_remote(remotes), roots)
        return TargetRepo(item.abs_path, item.rel, ident, item.nested)

    if not found:
        return []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(describe, found))


def match_repos(entries: list[dict], targets: list[TargetRepo]
                ) -> tuple[dict[int, TargetRepo], dict[int, list[TargetRepo]], list[TargetRepo]]:
    """Pair manifest entries with the repos found on the target machine.

    Returns (assignment, ambiguous, orphans):
      assignment  entry index -> the target repo that *is* that entry
      ambiguous   entry index -> tied candidates; relocation refused on purpose
      orphans     target repos no manifest entry claims

    The assignment is a global greedy pass over all candidate pairs ordered by
    score, so the strongest evidence is consumed first and each repo is
    claimed exactly once. Ties that would trigger a *move* are refused rather
    than guessed -- a wrong move is far more expensive than a missing one.
    """
    sources = [
        Identity.of(e["path"], e.get("remotes", {}), e.get("default_remote"),
                    e.get("root_commits"))
        for e in entries
    ]

    # Narrow the candidate set per entry instead of comparing N*M pairs.
    by_url: dict[str, set[int]] = {}
    by_root: dict[str, set[int]] = {}
    by_path: dict[str, set[int]] = {}
    for j, t in enumerate(targets):
        for u in t.identity.urls:
            by_url.setdefault(u, set()).add(j)
        if t.identity.root_commits:
            by_root.setdefault(t.identity.root_commits, set()).add(j)
        by_path.setdefault(norm_rel(t.rel), set()).add(j)

    candidates: dict[int, list[tuple[int, int]]] = {}   # entry -> [(score, target)]
    pairs: list[tuple[int, int, int]] = []              # (score, entry, target)
    for i, src in enumerate(sources):
        pool: set[int] = set()
        for u in src.urls:
            pool |= by_url.get(u, set())
        if src.root_commits:
            pool |= by_root.get(src.root_commits, set())
        pool |= by_path.get(norm_rel(src.path), set())

        scored = []
        for j in pool:
            score = match_score(src, targets[j].identity)
            if score <= 0:
                continue
            # A relocation needs real evidence; a weak match may only confirm
            # that a repo already sitting at the recorded path stays put.
            moves = norm_rel(targets[j].rel) != norm_rel(src.path)
            if moves and score < MIN_RELOCATE_SCORE:
                continue
            scored.append((score, j))
        if scored:
            scored.sort(key=lambda s: (-s[0], targets[s[1]].rel.lower()))
            candidates[i] = scored
            pairs.extend((score, i, j) for score, j in scored)

    pairs.sort(key=lambda p: (-p[0], entries[p[1]]["path"].lower(), targets[p[2]].rel.lower()))

    assignment: dict[int, TargetRepo] = {}
    ambiguous: dict[int, list[TargetRepo]] = {}
    taken: set[int] = set()
    done: set[int] = set()
    for score, i, j in pairs:
        if i in done or j in taken:
            continue
        moves = norm_rel(targets[j].rel) != norm_rel(entries[i]["path"])
        if moves:
            tied = [k for s, k in candidates[i] if s == score and k not in taken]
            if len(tied) > 1:
                ambiguous[i] = [targets[k] for k in tied]
                done.add(i)
                continue
        assignment[i] = targets[j]
        taken.add(j)
        done.add(i)

    orphans = [t for j, t in enumerate(targets) if j not in taken]
    return assignment, ambiguous, orphans


# --------------------------------------------------------------------------- #
# relocation
# --------------------------------------------------------------------------- #
@dataclass
class Move:
    src_abs: str
    dst_abs: str
    src_rel: str
    dst_rel: str


def plan_moves(entries: list[dict], assignment: dict[int, TargetRepo],
               target_root: str) -> list[Move]:
    """Turn the assignment into the minimal set of directory renames.

    A repo nested inside a moving parent travels with it, so those implied
    moves are dropped -- otherwise we would try to move a folder that has
    already been carried to its destination.
    """
    moves: list[Move] = []
    for i, t in assignment.items():
        dst_rel = entries[i]["path"]
        if t.rel == dst_rel:                       # exact string: catches case-only renames
            continue
        moves.append(Move(
            src_abs=t.abs_path,
            dst_abs=str(Path(target_root).joinpath(*dst_rel.split("/"))),
            src_rel=t.rel,
            dst_rel=dst_rel,
        ))

    kept: list[Move] = []
    for m in moves:
        implied = False
        for p in moves:
            if p is m or not is_subpath(m.src_abs, p.src_abs):
                continue
            carried = os.path.join(p.dst_abs, os.path.relpath(m.src_abs, p.src_abs))
            if os.path.normcase(carried) == os.path.normcase(m.dst_abs):
                implied = True
                break
        if not implied:
            kept.append(m)
    # Deepest first: a nested repo escaping its parent must leave before the
    # parent itself is renamed out from under it.
    kept.sort(key=lambda m: (-m.src_rel.count("/"), m.src_rel.lower()))
    return kept


class DeletionGate:
    """Ask before the only irreversible step this tool has: removing a folder.

    Relocating a repo is a rename -- reversible, and never asked about.
    Deleting the empty scaffolding a relocation leaves behind is not, so by
    default every single folder is confirmed individually with
    yes / no / all / none. `--prune-empty-dirs` and `--keep-empty-dirs`
    pre-answer that question; a non-interactive session never deletes.
    """

    def __init__(self, mode: str, dry_run: bool) -> None:
        self.mode = mode                    # "ask" | "always" | "never"
        self.dry_run = dry_run
        self.removed: list[str] = []
        self._warned = False
        if mode == "ask" and not (sys.stdin.isatty() and sys.stdout.isatty()):
            self.mode = "never"
            self._warned = True

    def _ask(self, rel: str) -> bool:
        while True:
            try:
                answer = console.input(
                    f"[yellow]Remove empty folder[/] '{rel}'? "
                    "[dim]([bold]y[/]es / [bold]n[/]o / [bold]a[/]ll / n[bold]o[/]ne)[/] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print()
                self.mode = "never"
                return False
            if answer in ("y", "yes", "j", "ja"):
                return True
            if answer in ("", "n", "no", "nein"):
                return False
            if answer in ("a", "all", "alle"):
                self.mode = "always"
                return True
            if answer in ("o", "none", "q", "quit", "keine"):
                self.mode = "never"
                return False
            console.print("[dim]Please answer y, n, a or o.[/]")

    def remove(self, abs_path: str, rel: str) -> bool:
        """Confirm and delete one empty folder. True if it is (to be) gone."""
        if self.mode == "never":
            if self._warned:
                self._warned = False
                console.print(
                    "[dim]Empty leftover folders are kept "
                    "(no interactive terminal -- use --prune-empty-dirs to remove them).[/]"
                )
            return False
        if self.dry_run:
            self.removed.append(rel)
            return True
        if self.mode == "ask" and not self._ask(rel):
            return False
        try:
            os.rmdir(abs_path)
        except OSError as exc:
            console.print(f"[yellow]Could not remove[/] {rel}: {exc}")
            return False
        self.removed.append(rel)
        return True


def destination_is_free(dst: str) -> bool:
    """True if `dst` can receive a directory (missing, or an empty folder)."""
    if not os.path.exists(dst):
        return True
    return os.path.isdir(dst) and not os.listdir(dst)


def rename_dir(src: str, dst: str) -> None:
    """Move a directory, clearing an empty placeholder at the destination."""
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isdir(dst) and not os.listdir(dst):
        os.rmdir(dst)
    try:
        os.rename(src, dst)
    except OSError:
        if os.path.exists(dst):
            # `shutil.move` would drop `src` *inside* an existing directory --
            # never silently nest a repo; let the caller report the failure.
            raise
        shutil.move(src, dst)          # different volume / mount point


def prune_empty_parents(start_abs: str, target_root: str, keep: set[str],
                        gate: DeletionGate, gone: set[str]) -> list[str]:
    """Offer to remove folders a move left behind empty, walking upwards.

    Only ever touches directories that are *genuinely* empty and that the
    manifest does not list as part of the folder skeleton, so the source
    tree's scaffolding always survives. `gone` carries the paths that are
    already (or, in a dry run, would be) removed, so a folder emptied by
    several moves is still recognised as empty.
    """
    removed: list[str] = []
    current = os.path.dirname(os.path.abspath(start_abs))
    root = os.path.abspath(target_root)
    while is_subpath(current, root):
        rel = os.path.relpath(current, root).replace(os.sep, "/")
        if norm_rel(rel) in keep:
            break
        try:
            entries = os.listdir(current)
        except OSError:
            break
        if any(os.path.normcase(os.path.join(current, e)) not in gone for e in entries):
            break
        if not gate.remove(current, rel):
            break
        gone.add(os.path.normcase(current))
        removed.append(rel)
        current = os.path.dirname(current)
    return removed


def execute_moves(moves: list[Move], target_root: str, keep_dirs: set[str],
                  gate: DeletionGate, dry_run: bool) -> list[dict]:
    """Perform the relocations, resolving conflicts, cycles and swaps.

    Straightforward moves run directly. When every remaining move is blocked
    (A wants B's folder while B wants A's), one is parked in a staging folder
    to break the cycle and placed afterwards.
    """
    results: list[dict] = []
    staging = os.path.join(os.path.abspath(target_root), STAGING_DIRNAME)
    staged: list[tuple[str, Move]] = []
    pending = list(moves)
    vacated: set[str] = set()      # paths that no longer hold anything

    def record(m: Move, status: str, detail: str) -> None:
        results.append({"path": m.dst_rel, "status": status, "detail": detail})

    def prune_behind(m: Move) -> str:
        vacated.add(os.path.normcase(m.src_abs))
        emptied = prune_empty_parents(m.src_abs, target_root, keep_dirs, gate, vacated)
        verb = "would prune" if dry_run else "pruned"
        return f" ({verb} {len(emptied)} empty folder(s))" if emptied else ""

    def do_move(m: Move, src: str) -> bool:
        try:
            rename_dir(src, m.dst_abs)
        except OSError as exc:
            record(m, "move-failed", f"{m.src_rel} -> {m.dst_rel}: {exc}")
            return False
        record(m, "moved", f"from {m.src_rel}" + prune_behind(m))
        return True

    if dry_run:
        for m in pending:
            free = destination_is_free(m.dst_abs)
            record(m, "would-move" if free else "would-move (staged)",
                   f"from {m.src_rel}" + prune_behind(m))
        return results

    while pending:
        progressed = False
        for m in list(pending):
            # A pending descendant must escape before its parent is renamed.
            if any(is_subpath(o.src_abs, m.src_abs) for o in pending if o is not m):
                continue
            if not os.path.isdir(m.src_abs):
                record(m, "move-failed", f"source vanished: {m.src_rel}")
                pending.remove(m)
                progressed = True
                continue
            if not destination_is_free(m.dst_abs):
                continue
            do_move(m, m.src_abs)
            pending.remove(m)
            progressed = True

        if progressed or not pending:
            continue

        # Deadlock: park the deepest blocked move so the others can proceed.
        blocked = next(
            (m for m in pending
             if not any(is_subpath(o.src_abs, m.src_abs) for o in pending if o is not m)),
            pending[0],
        )
        pending.remove(blocked)
        try:
            os.makedirs(staging, exist_ok=True)
            tmp = os.path.join(staging, f"{len(staged):04d}")
            rename_dir(blocked.src_abs, tmp)
            staged.append((tmp, blocked))
            write_staging_journal(staging, staged)
            prune_behind(blocked)
        except OSError as exc:
            record(blocked, "move-failed", f"{blocked.src_rel}: {exc}")

    for tmp, m in staged:
        if destination_is_free(m.dst_abs):
            do_move(m, tmp)
        else:
            record(m, "move-failed",
                   f"destination occupied: {m.dst_rel} (repo parked in "
                   f"{STAGING_DIRNAME}/{os.path.basename(tmp)})")

    clear_staging(staging)
    return results


def write_staging_journal(staging: str, staged: list[tuple[str, Move]]) -> None:
    """Leave a breadcrumb so a crash mid-swap is recoverable by hand."""
    try:
        Path(staging, "journal.json").write_text(
            json.dumps(
                [{"parked": os.path.basename(t), "destination": m.dst_rel,
                  "origin": m.src_rel} for t, m in staged],
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_staging(staging: str) -> None:
    """Remove the staging folder -- but only when nothing is parked in it."""
    if not os.path.isdir(staging):
        return
    leftovers = [n for n in os.listdir(staging) if n != "journal.json"]
    if leftovers:
        console.print(
            f"[red]Warning:[/] {len(leftovers)} repo(s) are still parked in "
            f"{staging} -- see journal.json and move them back manually."
        )
        return
    try:
        Path(staging, "journal.json").unlink(missing_ok=True)
        os.rmdir(staging)
    except OSError:
        pass


def archive_orphans(orphans: list[TargetRepo], target_root: str, archive_dir: str,
                    keep_dirs: set[str], gate: DeletionGate, dry_run: bool) -> list[dict]:
    """Move repos that the manifest no longer knows into an archive folder.

    Nothing is ever deleted: the repo keeps its relative layout underneath
    `archive_dir`, so it can be moved back with a single rename.
    """
    root = os.path.abspath(target_root)
    base = archive_dir if os.path.isabs(archive_dir) else os.path.join(root, archive_dir)
    results: list[dict] = []
    vacated: set[str] = set()
    # Only the outermost repo of a nest needs moving; nested ones travel along.
    outermost = [
        o for o in orphans
        if not any(is_subpath(o.abs_path, p.abs_path) for p in orphans if p is not o)
    ]
    for o in sorted(outermost, key=lambda r: r.rel.lower()):
        dst = os.path.join(base, *o.rel.split("/"))
        rel_dst = os.path.relpath(dst, root).replace(os.sep, "/")
        if is_subpath(o.abs_path, base) or os.path.normcase(o.abs_path) == os.path.normcase(base):
            continue                       # already inside the archive
        if not dry_run:
            if not destination_is_free(dst):
                results.append({"path": o.rel, "status": "archive-failed",
                                "detail": f"destination occupied: {rel_dst}"})
                continue
            try:
                rename_dir(o.abs_path, dst)
            except OSError as exc:
                results.append({"path": o.rel, "status": "archive-failed", "detail": str(exc)})
                continue
        vacated.add(os.path.normcase(o.abs_path))
        prune_empty_parents(o.abs_path, root, keep_dirs, gate, vacated)
        results.append({"path": o.rel,
                        "status": "would-archive" if dry_run else "archived",
                        "detail": rel_dst})
    return results


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


def process_repo(entry: dict, target_root: str, do_clone: bool, do_update: bool,
                 dry_run: bool, blocked_reason: str | None = None,
                 probe: Path | None = None) -> dict:
    rel = entry["path"]
    target = Path(target_root).joinpath(*rel.split("/"))
    # In a dry run the relocation has not happened yet, so inspect the repo
    # where it currently sits -- otherwise the preview would claim a clone for
    # a repo it just said it would move. Writes always go to `target`.
    here = probe or target
    remotes: dict = entry.get("remotes", {})
    default_remote = entry.get("default_remote")
    url = remotes.get(default_remote) if default_remote else None

    def result(status: str, detail: str = "") -> dict:
        return {"path": rel, "status": status, "detail": detail}

    is_repo = (here / ".git").exists()  # dir (repo) or file (submodule)

    # ---- existing repo -> update --------------------------------------- #
    if is_repo:
        if not do_update:
            return result("skipped", "update disabled")
        ok, reason = decide_update_action(here)
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

    # ---- the repo exists, just not here -> never clone a second copy ---- #
    if blocked_reason:
        return result("skipped", blocked_reason)

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


def report_url_drift(entries: list[dict], assignment: dict[int, TargetRepo],
                     target_root: str, sync: bool, dry_run: bool) -> list[dict]:
    """Flag (and optionally fix) repos whose remote URL moved on the forge.

    Root-commit matching happily re-finds a repository that was renamed or
    migrated to another host -- but the local clone then still points at the
    old URL and every later `pull` fails. Rewriting a remote is a change to an
    existing repo, so it stays opt-in (`--sync-remotes`); without it we only
    say what is stale.
    """
    results: list[dict] = []
    for i, t in assignment.items():
        entry = entries[i]
        want = normalize_remote_url(entry.get("remotes", {}).get(entry.get("default_remote") or "", ""))
        have = t.identity.default_url
        if not want or not have or want == have:
            continue
        url = entry["remotes"][entry["default_remote"]]
        results.append({"path": entry["path"], "status": "url-drift",
                        "detail": f"{have} -> {want}"})
        if not sync or dry_run:
            continue
        repo = Path(target_root).joinpath(*entry["path"].split("/"))
        remotes = read_remotes(str(repo))
        name = pick_default_remote(remotes)
        if not name:
            continue
        rc, _, err = run_git(["remote", "set-url", name, url], cwd=repo)
        results[-1]["status"] = "remote-synced" if rc == 0 else "failed"
        if rc != 0:
            results[-1]["detail"] = f"remote set-url failed: {err.strip()}"
    return results


def clone_waves(entries: list[dict]) -> list[list[int]]:
    """Order the clone/update phase so a parent is never processed after its
    nested sub-repo.

    Cloning into a non-empty folder is refused (rightly -- we never overwrite),
    so if a worker clones `app/vendor/sdk` first, the clone of `app` finds its
    target occupied and is skipped. Everything that is not nested runs in one
    parallel wave; nested repos follow, shallowest first.
    """
    waves: dict[int, list[int]] = {}
    for i, e in enumerate(entries):
        depth = e["path"].count("/") if e.get("nested") else -1
        waves.setdefault(depth, []).append(i)
    return [waves[k] for k in sorted(waves)]


def print_move_plan(entries: list[dict], moves: list[Move],
                    ambiguous: dict[int, list[TargetRepo]]) -> None:
    if moves:
        table = Table(title=f"Relocations detected ({len(moves)})",
                      title_style="bold", show_edge=False)
        table.add_column("From (on this machine)", style="yellow")
        table.add_column("", style="dim")
        table.add_column("To (per manifest)", style="green")
        for m in moves:
            table.add_row(m.src_rel, "->", m.dst_rel)
        console.print(table)
    if ambiguous:
        console.print("[yellow]Ambiguous -- several repos match equally well, "
                      "nothing moved:[/]")
        for i, cands in ambiguous.items():
            names = ", ".join(c.rel for c in cands)
            console.print(f"  [yellow]{entries[i]['path']}[/] [dim]<- {names}[/]")


def restore(manifest: dict, target_root: str, do_clone: bool, do_update: bool,
            do_move: bool, prune_mode: str, fingerprint: bool,
            exclude_patterns: list[str] | None, archive_dir: str | None,
            sync_remotes: bool, jobs: int, dry_run: bool) -> None:
    target_root = os.path.abspath(target_root)
    entries: list[dict] = manifest.get("repositories", [])
    source = manifest.get("source", {})
    gate = DeletionGate(prune_mode, dry_run)

    deep = bool(source.get("deep"))
    prune_dirs = set(source.get("prune_dirs") or []) or PRUNE_DIRS
    patterns = list(exclude_patterns if exclude_patterns is not None
                    else source.get("exclude_patterns", []))

    # 1) Identify what is already on this machine, wherever it currently sits.
    targets = index_target(target_root, patterns, deep, prune_dirs, fingerprint, jobs)
    assignment, ambiguous, orphans = match_repos(entries, targets)
    console.print(
        f"[blue]Target index:[/] {len(targets)} repo(s) found, "
        f"{len(assignment)} matched to the manifest, {len(orphans)} unknown"
    )

    # 2) Migrate everything the source tree reorganised.
    keep_dirs = {norm_rel(d) for d in manifest.get("directories", [])}
    moves = plan_moves(entries, assignment, target_root) if do_move else []
    if do_move:
        print_move_plan(entries, moves, ambiguous)
    elif any(t.rel != entries[i]["path"] for i, t in assignment.items()):
        console.print("[yellow]Relocations detected but --no-move is set:[/] "
                      "those repos are left where they are and will not be cloned.")

    move_results = execute_moves(moves, target_root, keep_dirs, gate, dry_run) \
        if moves else []
    for res in move_results:
        colour = {"moved": "magenta", "move-failed": "red"}.get(res["status"], "cyan")
        console.print(f"[{colour}]{res['status']:<13}[/] {res['path']}"
                      + (f"  [dim]{res['detail']}[/]" if res["detail"] else ""))

    # 2b) Repos this machine has but the manifest does not know about. Done
    #     before cloning so an archived folder frees its path for a new clone.
    archive_results: list[dict] = []
    if orphans and archive_dir:
        archive_results = archive_orphans(orphans, target_root, archive_dir,
                                          keep_dirs, gate, dry_run)
        for res in archive_results:
            colour = "red" if res["status"] == "archive-failed" else "magenta"
            console.print(f"[{colour}]{res['status']:<13}[/] {res['path']}"
                          + (f"  [dim]-> {res['detail']}[/]" if res["detail"] else ""))

    # Where does each matched repo live now? Anything that did not reach its
    # recorded path must not be cloned again -- that is what caused duplicates.
    moved_ok = {m.dst_rel for m in moves
                if any(r["status"] == "moved" and r["path"] == m.dst_rel
                       for r in move_results)}
    blocked: dict[int, str] = {}
    probe: dict[int, Path] = {}
    for i, t in assignment.items():
        dst = entries[i]["path"]
        if t.rel == dst:
            continue
        if dry_run and do_move:
            probe[i] = Path(t.abs_path)      # preview: it is still over there
        elif dst not in moved_ok:
            blocked[i] = f"already on disk at '{t.rel}' (not relocated)"
    for i, cands in ambiguous.items():
        names = ", ".join(f"'{c.rel}'" for c in cands)
        blocked[i] = f"ambiguous: {names} match equally well -- resolve by hand"

    # 2c) A repo that was renamed on the forge is found again by its root
    #     commit, but its clone still points at the old URL.
    drift_results = report_url_drift(entries, assignment, target_root, sync_remotes, dry_run)
    for res in drift_results:
        colour = {"remote-synced": "green", "failed": "red"}.get(res["status"], "yellow")
        console.print(f"[{colour}]{res['status']:<13}[/] {res['path']}  [dim]{res['detail']}[/]")
    if drift_results and not sync_remotes:
        console.print("[dim]Remote URLs differ from the manifest -- "
                      "rerun with --sync-remotes to update them.[/]")

    # 3) Recreate the folder skeleton (existing folders are left as-is).
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

    # 4) Clone / update every repository (in parallel, parents before children).
    results: list[dict] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Repositories", total=len(entries))
        for wave in clone_waves(entries):
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = [
                    pool.submit(process_repo, entries[i], target_root, do_clone,
                                do_update, dry_run, blocked.get(i), probe.get(i))
                    for i in wave
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

    print_restore_summary(results, [*move_results, *archive_results, *drift_results])
    if gate.removed:
        console.print(
            f"[dim]Empty folders {'that would be ' if dry_run else ''}removed: "
            f"{len(gate.removed)}[/]"
        )
    if orphans and not archive_dir:
        console.print(
            f"[yellow]Not in the manifest ({len(orphans)}), left untouched[/] "
            f"[dim]-- use --archive-orphans <DIR> to move them aside:[/]"
        )
        for o in orphans[:20]:
            console.print(f"  [yellow]{o.rel}[/]")
        if len(orphans) > 20:
            console.print(f"  [dim]... and {len(orphans) - 20} more[/]")


def print_restore_summary(results: list[dict], extras: list[dict]) -> None:
    everything = [*extras, *results]
    counts: dict[str, int] = {}
    for r in everything:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    table = Table(title="Restore result", title_style="bold", show_edge=False)
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    for status in ("moved", "would-move", "would-move (staged)", "move-failed",
                   "archived", "would-archive", "archive-failed",
                   "url-drift", "remote-synced",
                   "cloned", "updated", "would-clone", "would-update",
                   "skipped", "failed"):
        if status in counts:
            table.add_row(status, str(counts[status]))
    console.print(table)

    failed = [r for r in everything
              if r["status"] in ("failed", "move-failed", "archive-failed")]
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
    default_jobs = min(8, (os.cpu_count() or 4))

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
    p_scan.add_argument("--no-fingerprint", dest="fingerprint", action="store_false",
                        help="Skip the root-commit fingerprint. Faster, but a repo "
                             "that was renamed on the forge can no longer be tracked.")
    p_scan.add_argument("--jobs", "-j", type=int, default=default_jobs,
                        help="Parallel git workers (default: %(default)s).")

    p_res = sub.add_parser("restore", aliases=["update"],
                           help="Recreate folders, migrate moved repos, clone missing "
                                "ones, update existing ones.")
    p_res.add_argument("--input", "-i", default="repos.json", help="Manifest to restore from.")
    p_res.add_argument("--target", default=default_root(),
                       help="Target root folder (default: %(default)s).")
    p_res.add_argument("--no-clone", action="store_true", help="Do not clone missing repos.")
    p_res.add_argument("--no-update", action="store_true", help="Do not update existing repos.")
    p_res.add_argument("--no-move", action="store_true",
                       help="Detect and report relocations but do not perform them "
                            "(moved repos are then neither migrated nor re-cloned).")
    prune = p_res.add_mutually_exclusive_group()
    prune.add_argument("--prune-empty-dirs", dest="prune_mode", action="store_const",
                       const="always", default="ask",
                       help="Remove folders a relocation left behind empty without "
                            "asking (default: confirm each one; y/n/all/none).")
    prune.add_argument("--keep-empty-dirs", dest="prune_mode", action="store_const",
                       const="never",
                       help="Never remove leftover empty folders and never ask.")
    p_res.add_argument("--no-fingerprint", dest="fingerprint", action="store_false",
                       help="Do not compute root-commit fingerprints while indexing "
                            "the target (faster; weaker move detection).")
    p_res.add_argument("-x", "--exclude", "--ignore", action="append", default=None,
                       dest="exclude", metavar="GLOB",
                       help="Override the manifest's exclude patterns while indexing "
                            "the target tree (repeatable).")
    p_res.add_argument("--sync-remotes", action="store_true",
                       help="Rewrite a matched repo's default remote when the manifest "
                            "has a newer URL (repo renamed/migrated on the forge). "
                            "Reported either way.")
    p_res.add_argument("--archive-orphans", metavar="DIR", default=None,
                       help="Move repos that are not in the manifest into DIR "
                            "(relative to --target unless absolute). Nothing is deleted.")
    p_res.add_argument("--jobs", "-j", type=int, default=default_jobs,
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
        manifest = scan(args.root, patterns, args.deep, prune_dirs,
                        args.fingerprint, max(1, args.jobs))
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
    if manifest.get("schema") not in SUPPORTED_SCHEMAS:
        console.print(f"[yellow]Warning:[/] unexpected schema '{manifest.get('schema')}'.")
    restore(
        manifest,
        target_root=args.target,
        do_clone=not args.no_clone,
        do_update=not args.no_update,
        do_move=not args.no_move,
        prune_mode=args.prune_mode,
        fingerprint=args.fingerprint,
        exclude_patterns=args.exclude,
        archive_dir=args.archive_orphans,
        sync_remotes=args.sync_remotes,
        jobs=max(1, args.jobs),
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[red]Aborted.[/]")
        sys.exit(130)
