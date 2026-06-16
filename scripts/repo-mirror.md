# repo-mirror

Snapshot a folder tree full of git repositories into a single JSON manifest,
then recreate that tree **1:1 on another machine** — folder skeleton, clones,
submodules and all — or bring an existing copy up to date in one shot.

This is a **host (native) tool**: it runs on the host's Python (not in the
DevTools container), because it operates on the host filesystem and uses the
host's git credentials. Cross-platform (Windows / Linux / macOS). The only
dependency is [`rich`](https://github.com/Textualize/rich).

---

## Install & run

```bash
pip install -r scripts/requirements.txt          # installs rich on the host
```

```bash
# Windows (via the generated launcher)
devtools.cmd repo-mirror scan -o repos.json

# Any platform (direct)
python scripts/repo-mirror.py scan -o repos.json
```

`git` must be on `PATH`.

---

## Usage

### 1. Scan (create the manifest)

On the source machine:

```bash
# --root defaults to C:\Projects on Windows when it exists, else the cwd
python scripts/repo-mirror.py scan -o repos.json

# Any path, any OS
python scripts/repo-mirror.py scan --root /home/me/code -o repos.json
```

Copy `repos.json` to the other machine (or commit it somewhere).

### 2. Restore / update (rebuild or refresh the tree)

```bash
# --target defaults to C:\Projects on Windows when it exists, else the cwd
python scripts/repo-mirror.py restore -i repos.json

# Explicit target
python scripts/repo-mirror.py restore -i repos.json --target /home/me/code

# Preview everything without touching disk
python scripts/repo-mirror.py restore -i repos.json --dry-run
```

`restore` is idempotent and non-destructive:

| Situation on target                   | Action                                             |
| ------------------------------------- | -------------------------------------------------- |
| Folder missing                        | created                                            |
| Repo missing                          | `git clone --recurse-submodules` + checkout branch |
| Repo present, clean, has upstream     | `git pull --ff-only` (+ `submodule update`)        |
| Repo present, **uncommitted changes** | **skipped** — left untouched                       |
| Repo present, **detached HEAD**       | **skipped** — left untouched                       |
| Repo present, no upstream branch      | skipped                                            |
| Path exists but is **not** a git repo | skipped — never overwritten                        |

---

## Options

### `scan`

| Flag                    | Meaning                                                              |
| ----------------------- | ------------------------------------------------------------------- |
| `--root PATH`           | Root to scan. Default: `C:\Projects` on Windows if present, else cwd. |
| `-o, --output PATH`     | Manifest output (default `repos.json`).                             |
| `-x, --exclude GLOB`    | Exclude folders by **name or relative path** glob (repeatable).     |
| `--no-default-excludes` | Drop the built-in `BAUER GROUP Products*` / `Z*` excludes.          |
| `--prune-dir NAME`      | Add a folder name to the speed-prune set (repeatable).             |
| `--deep`                | Also descend into `node_modules`, `.venv`, `dist`, … (slower).      |

```bash
# exclude a whole category by name and a specific sub-path
python scripts/repo-mirror.py scan -x 'Archive*' -x 'eCommerce/legacy'
```

### `restore`

| Flag               | Meaning                                                       |
| ------------------ | ------------------------------------------------------------- |
| `-i, --input PATH` | Manifest to restore from (default `repos.json`).             |
| `--target PATH`    | Target root. Default: `C:\Projects` on Windows if present, else cwd. |
| `--no-clone`       | Only update existing repos; do not clone missing ones.       |
| `--no-update`      | Only clone missing repos; do not touch existing ones.        |
| `-j, --jobs N`     | Parallel git workers (default: min(8, cpu)).                 |
| `--dry-run`        | Show planned actions without changing anything.              |

Aliases: `scan` ≡ `create`, `restore` ≡ `update`.

---

## How it handles tricky trees

* **Nested independent repos** — a clone living *inside* another repo (often
  git-ignored, e.g. a vendored reference checkout) is detected and recorded as
  its own entry, so it survives the restore. Identified by a `.git`
  **directory**.
* **Submodules** — detected authoritatively from each repo's `.gitmodules`
  (works even when uninitialised). They are **not** recorded as separate repos;
  instead the parent is restored with `--recurse-submodules`, and existing
  repos get `git submodule update --init --recursive` after a fast-forward —
  recursively, so nested submodules (e.g. a vendored SDK) come along too.
* **Linked worktrees / initialised submodules** (`.git` is a *file*) are
  skipped — git reconstructs them from their parent.
* **Local-only repos** (no remote) are recorded and flagged; on restore they
  are skipped (nothing to clone from).

---

## Manifest format (`repo-mirror/1`)

```jsonc
{
  "schema": "repo-mirror/1",
  "tool": "repo-mirror",
  "version": "1.0.0",
  "generated_at": "2026-06-16T...Z",
  "source": {
    "host": "WS-...",
    "platform": "win32",
    "root": "C:\\Projects",
    "exclude_patterns": ["BAUER GROUP Products*", "Z*"],
    "prune_dirs": ["node_modules", "..."],
    "deep": false
  },
  "directories": ["Company-Pages", "Container-Solution", "..."],
  "repositories": [
    {
      "path": "eCommerce/Shopware5",
      "remotes": { "origin": "https://github.com/..." },
      "default_remote": "origin",
      "head": "main",
      "detached": false,
      "submodules": ["Plugins/AmazonToolkit", "..."],
      "nested": false
    }
  ]
}
```

Repo paths use forward slashes and are reconstructed per-OS on restore, so a
manifest taken on Windows restores cleanly on Linux/macOS.

---

## Update policy (the safe default)

Existing repos are only ever **fast-forwarded** — never merged, rebased, reset
or force-touched. Anything with local work (dirty tree, detached HEAD, no
upstream) is deliberately left alone. To change this, edit the single
`decide_update_action()` function in `scripts/repo-mirror.py`.
