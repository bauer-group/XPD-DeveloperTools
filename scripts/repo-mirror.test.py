#!/usr/bin/env python3
"""Self-test for repo-mirror -- run it after touching scripts/repo-mirror.py.

Builds a throwaway git playground (bare "forge" repos, a source tree and a
target tree), reorganises the source in every way that used to create a
duplicate clone, and asserts that `restore` migrates the target instead.

    python scripts/repo-mirror.test.py

Needs `git` on PATH and `rich` (the tool's own dependency). Nothing outside
the temporary playground is touched; on failure the playground is kept so it
can be inspected. Exit code 0 = all checks passed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("repo-mirror.py")

FAILURES: list[str] = []
CHECKS = 0


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def check(label: str, condition: bool, extra: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}{(' -- ' + extra) if extra else ''}")


def rmtree(path) -> None:
    """shutil.rmtree that copes with git's read-only object files on Windows."""
    def force(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:
        shutil.rmtree(path, onerror=lambda f, p, e: force(f, p, e))


def git(*args: str, cwd: str | Path | None = None) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test",
         "-c", "init.defaultBranch=main", "-c", "protocol.file.allow=always", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{proc.stderr}")
    return proc.stdout


def run_tool(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise RuntimeError(f"repo-mirror {' '.join(args)} exited {proc.returncode}")
    return proc


def load_tool():
    """Import repo-mirror.py as a module (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("repo_mirror", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["repo_mirror"] = module          # @dataclass needs it registered
    spec.loader.exec_module(module)
    return module


def make_forge(forge: Path, name: str) -> Path:
    """Create a bare repo with one commit -- our stand-in for GitHub."""
    bare, seed = forge / f"{name}.git", forge / f"_seed_{name}"
    git("init", str(seed))
    (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-m", f"init {name}", cwd=seed)
    git("clone", "--bare", str(seed), str(bare))
    rmtree(seed)
    return bare


def clone(bare: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    git("clone", str(bare), str(dest))


def repos_in(root: Path) -> set[str]:
    """Every git repo below `root`, as posix paths relative to it."""
    out = set()
    for dirpath, dirnames, _ in os.walk(root):
        if (Path(dirpath) / ".git").is_dir():
            out.add(os.path.relpath(dirpath, root).replace(os.sep, "/"))
        dirnames[:] = [d for d in dirnames if d != ".git"]
    return out


# --------------------------------------------------------------------------- #
# unit checks -- the parts an end-to-end run cannot reach
# --------------------------------------------------------------------------- #
def check_url_normalisation(rm) -> None:
    print("\n[a] remote URLs identify a repo regardless of spelling")
    equivalent = [
        ("git@github.com:Org/Repo.git", "https://github.com/org/repo"),
        ("ssh://git@github.com:22/org/repo.git/", "https://github.com/Org/Repo.git"),
        ("https://user:token@github.com/org/repo.git", "git@github.com:org/repo"),
        (r"\\server\share\repo.git", "//server/share/repo"),
    ]
    for a, b in equivalent:
        check(f"{a} == {b}",
              rm.normalize_remote_url(a) == rm.normalize_remote_url(b),
              f"{rm.normalize_remote_url(a)!r} vs {rm.normalize_remote_url(b)!r}")
    check("different repos stay different",
          rm.normalize_remote_url("git@github.com:org/a.git")
          != rm.normalize_remote_url("git@github.com:org/b.git"))
    check("a windows drive letter is not a host",
          rm.normalize_remote_url(r"C:\repos\foo.git") == "c/repos/foo",
          rm.normalize_remote_url(r"C:\repos\foo.git"))
    check("relative paths keep their separator",
          rm.norm_rel("A/one").split("/") == ["a" if os.name == "nt" else "A", "one"],
          rm.norm_rel("A/one"))


def check_deletion_gate(rm) -> None:
    print("\n[b] deleting an empty folder always needs consent")
    tmp = Path(tempfile.mkdtemp(prefix="repo-mirror-gate-"))
    answers: list[str] = []
    rm.console.input = lambda *a, **k: answers.pop(0)

    def gate(mode: str, replies: list[str], dry_run: bool = False):
        nonlocal answers
        answers = list(replies)
        g = rm.DeletionGate.__new__(rm.DeletionGate)     # bypass the isatty probe
        g.mode, g.dry_run, g.removed, g._warned = mode, dry_run, [], False
        return g

    a, b, c = tmp / "a", tmp / "b", tmp / "c"
    for d in (a, b, c):
        d.mkdir()
    g = gate("ask", ["n", "y", "what?", "a"])
    check("'n' keeps the folder", g.remove(str(a), "a") is False and a.exists())
    check("'y' removes the folder", g.remove(str(b), "b") is True and not b.exists())
    check("an invalid answer re-asks", g.remove(str(c), "c") is True and not c.exists())
    check("'a' switches to remove-all", g.mode == "always")

    d, e = tmp / "d", tmp / "e"
    for x in (d, e):
        x.mkdir()
    g = gate("ask", ["o"])
    check("'o' keeps this folder", g.remove(str(d), "d") is False and d.exists())
    check("'o' switches to keep-all", g.mode == "never")
    check("keep-all never asks again", g.remove(str(e), "e") is False and e.exists())

    check("--keep-empty-dirs deletes nothing",
          gate("never", []).remove(str(d), "d") is False and d.exists())
    check("--prune-empty-dirs deletes without asking",
          gate("always", []).remove(str(d), "d") is True and not d.exists())
    check("--dry-run never touches disk",
          gate("ask", [], dry_run=True).remove(str(e), "e") is True and e.exists())
    rmtree(tmp)


def check_legacy_manifest() -> None:
    print("\n[c] a repo-mirror/1 manifest still restores (URL matching only)")
    play = Path(tempfile.mkdtemp(prefix="repo-mirror-v1-"))
    forge, src, dst = play / "forge", play / "src", play / "dst"
    forge.mkdir(parents=True)
    bare = make_forge(forge, "old")
    manifest = play / "m.json"

    clone(bare, src / "Old" / "repo")
    run_tool("scan", "--root", str(src), "-o", str(manifest))
    shutil.move(str(src / "Old" / "repo"), str(src / "New-Home"))
    (src / "Old").rmdir()
    run_tool("scan", "--root", str(src), "-o", str(manifest))

    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["schema"] = "repo-mirror/1"
    for repo in data["repositories"]:
        repo.pop("root_commits", None)
    manifest.write_text(json.dumps(data), encoding="utf-8")

    clone(bare, dst / "Old" / "repo")
    proc = run_tool("restore", "-i", str(manifest), "--target", str(dst),
                    "--prune-empty-dirs")
    check("v1 manifest is accepted", "unexpected schema" not in proc.stdout)
    check("v1: repo relocated by its remote url", (dst / "New-Home" / ".git").is_dir(),
          proc.stdout[-400:])
    check("v1: old path is gone", not (dst / "Old").exists())
    rmtree(play)


# --------------------------------------------------------------------------- #
# end-to-end
# --------------------------------------------------------------------------- #
def check_end_to_end() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="repo-mirror-test-"))
    forge, src, dst = tmp / "forge", tmp / "src", tmp / "dst"
    forge.mkdir(parents=True)
    manifest = tmp / "repos.json"
    print(f"\nPlayground: {tmp}")

    names = ["alpha", "beta", "swapx", "swapy", "parent", "child", "cased", "renamed"]
    bares = {n: make_forge(forge, n) for n in names}

    clone(bares["alpha"], src / "A" / "one")
    clone(bares["beta"], src / "A" / "two")
    clone(bares["swapx"], src / "S" / "x")
    clone(bares["swapy"], src / "S" / "y")
    clone(bares["parent"], src / "N" / "parent")
    clone(bares["child"], src / "N" / "parent" / "third_party" / "child")   # nested repo
    clone(bares["cased"], src / "C" / "case")
    clone(bares["renamed"], src / "R" / "movedonforge")

    # A repo that never had a remote -- only its root commit identifies it.
    local = src / "L" / "localonly"
    local.mkdir(parents=True)
    git("init", str(local))
    (local / "notes.txt").write_text("local\n", encoding="utf-8")
    git("add", "-A", cwd=local)
    git("commit", "-m", "local only", cwd=local)

    (src / "Empty-Scaffold").mkdir()

    print("\n[1] scan + baseline restore")
    run_tool("scan", "--root", str(src), "-o", str(manifest))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    check("manifest schema is repo-mirror/2", data["schema"] == "repo-mirror/2")
    check("all 9 repos recorded", len(data["repositories"]) == 9,
          str(sorted(r["path"] for r in data["repositories"])))
    check("nested sub-repo recorded as nested",
          any(r["path"] == "N/parent/third_party/child" and r["nested"]
              for r in data["repositories"]))
    check("every repo has a root-commit fingerprint",
          all(r["root_commits"] for r in data["repositories"]))

    run_tool("restore", "-i", str(manifest), "--target", str(dst))
    shutil.copytree(src / "L" / "localonly", dst / "L" / "localonly")  # no remote to clone
    check("baseline: target mirrors source", repos_in(dst) == repos_in(src),
          str(repos_in(dst) ^ repos_in(src)))

    print("\n[2] reorganising the source tree")
    (src / "B").mkdir()
    shutil.move(str(src / "A" / "one"), str(src / "B" / "one"))            # plain move
    shutil.move(str(src / "A" / "two"), str(src / "A" / "two-renamed"))    # rename
    shutil.move(str(src / "S" / "x"), str(src / "S" / "_tmp"))             # swap x <-> y
    shutil.move(str(src / "S" / "y"), str(src / "S" / "x"))
    shutil.move(str(src / "S" / "_tmp"), str(src / "S" / "y"))
    (src / "M").mkdir()
    shutil.move(str(src / "N" / "parent"), str(src / "M" / "parent"))      # parent + nested
    shutil.move(str(src / "L" / "localonly"), str(src / "L2-local"))       # local-only repo
    (src / "N").rmdir()
    (src / "L").rmdir()

    # Renamed on the "forge": new URL *and* a new folder -- only the root
    # commit can still tie the two together.
    renamed_bare = forge / "renamed-v2.git"
    shutil.move(str(bares["renamed"]), str(renamed_bare))
    git("remote", "set-url", "origin", str(renamed_bare), cwd=src / "R" / "movedonforge")
    shutil.move(str(src / "R" / "movedonforge"), str(src / "R" / "renamed-v2"))

    if os.name == "nt":                                                    # case-only rename
        shutil.move(str(src / "C" / "case"), str(src / "C" / "_c"))
        shutil.move(str(src / "C" / "_c"), str(src / "C" / "CASE"))
    else:
        shutil.move(str(src / "C" / "case"), str(src / "C" / "CASE"))

    clone(bares["alpha"], dst / "Unknown" / "stray")   # target-only repo (orphan)
    run_tool("scan", "--root", str(src), "-o", str(manifest))

    print("\n[3] --dry-run must not touch anything")
    before = repos_in(dst)
    proc = run_tool("restore", "-i", str(manifest), "--target", str(dst), "--dry-run")
    check("dry-run reports the relocations", "would-move" in proc.stdout)
    check("dry-run changed nothing on disk", repos_in(dst) == before)

    print("\n[4] restore migrates instead of cloning")
    run_tool("restore", "-i", str(manifest), "--target", str(dst),
             "--prune-empty-dirs", "--sync-remotes")

    src_repos, dst_repos = repos_in(src), repos_in(dst)
    check("no duplicates: target == source (plus the orphan)",
          dst_repos == src_repos | {"Unknown/stray"},
          f"extra: {sorted(dst_repos - src_repos)} | missing: {sorted(src_repos - dst_repos)}")
    check("plain move applied", (dst / "B" / "one" / ".git").is_dir())
    check("old location is gone", not (dst / "A" / "one").exists())
    check("rename applied", (dst / "A" / "two-renamed" / ".git").is_dir())
    check("swap resolved (x)", (dst / "S" / "x" / ".git").is_dir())
    check("swap resolved (y)", (dst / "S" / "y" / ".git").is_dir())
    check("nested repo travelled with its parent",
          (dst / "M" / "parent" / "third_party" / "child" / ".git").is_dir())
    check("old parent path is gone", not (dst / "N").exists())
    check("local-only repo relocated by root commit", (dst / "L2-local" / ".git").is_dir())
    check("forge-renamed repo relocated", (dst / "R" / "renamed-v2" / ".git").is_dir())
    check("remote url synced with --sync-remotes",
          str(renamed_bare) in git("remote", "get-url", "origin",
                                   cwd=dst / "R" / "renamed-v2").strip().replace("/", os.sep))
    check("empty leftovers pruned", not (dst / "L").exists())
    check("orphan repo untouched", (dst / "Unknown" / "stray" / ".git").is_dir())

    def origin_of(p: Path) -> str:
        return Path(git("remote", "get-url", "origin", cwd=p).strip()).name
    check("the swap moved the right repos, not just the names",
          origin_of(dst / "S" / "x") == "swapy.git"
          and origin_of(dst / "S" / "y") == "swapx.git",
          f"x={origin_of(dst / 'S' / 'x')} y={origin_of(dst / 'S' / 'y')}")
    if os.name == "nt":
        actual = [p.name for p in (dst / "C").iterdir()]
        check("case-only rename applied", actual == ["CASE"], str(actual))

    print("\n[5] a second run is a no-op")
    proc = run_tool("restore", "-i", str(manifest), "--target", str(dst))
    check("second run moves nothing", "moved" not in proc.stdout.replace("would-move", ""))
    check("second run clones nothing", "cloned" not in proc.stdout)
    check("second run leaves the tree alone", repos_in(dst) == dst_repos)

    print("\n[6] without a flag, no folder is deleted unattended")
    (dst / "Ghost" / "gone").mkdir(parents=True)
    shutil.move(str(dst / "B" / "one"), str(dst / "Ghost" / "gone" / "one"))
    proc = run_tool("restore", "-i", str(manifest), "--target", str(dst))
    check("the repo is moved back", (dst / "B" / "one" / ".git").is_dir())
    check("the empty folder survives", (dst / "Ghost").exists())
    check("and the reason is reported", "Empty leftover folders are kept" in proc.stdout)

    print("\n[7] orphans can be archived, never deleted")
    proc = run_tool("restore", "-i", str(manifest), "--target", str(dst),
                    "--archive-orphans", "_Archive", "--prune-empty-dirs")
    check("orphan archived", (dst / "_Archive" / "Unknown" / "stray" / ".git").is_dir())
    check("orphan gone from its old place", not (dst / "Unknown" / "stray").exists())
    check("archiving is reported", "archived" in proc.stdout)

    print("\n[8] --no-move reports but never re-clones")
    shutil.move(str(dst / "A" / "two-renamed"), str(dst / "A" / "two-elsewhere"))
    proc = run_tool("restore", "-i", str(manifest), "--target", str(dst), "--no-move")
    check("relocation reported", "--no-move is set" in proc.stdout)
    check("no duplicate created", not (dst / "A" / "two-renamed").exists())
    check("repo left where it was", (dst / "A" / "two-elsewhere" / ".git").is_dir())

    if FAILURES:
        print(f"\nPlayground kept for inspection: {tmp}")
        return 1
    rmtree(tmp)
    return 0


def main() -> int:
    if not TOOL.is_file():
        print(f"repo-mirror.py not found at {TOOL}", file=sys.stderr)
        return 2
    rm = load_tool()
    check_url_normalisation(rm)
    check_deletion_gate(rm)
    check_legacy_manifest()
    check_end_to_end()

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
