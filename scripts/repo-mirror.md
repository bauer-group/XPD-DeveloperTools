# repo-mirror

Snapshot a folder tree full of git repositories into a single JSON manifest,
then recreate that tree **1:1 on another machine** — folder skeleton, clones,
submodules and all — or bring an existing copy up to date in one shot.

Repositories are tracked by **identity, not by path**: if you reorganise the
source tree, the target tree is *migrated* to match. Nothing is cloned twice.

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

### 2. Restore / update (rebuild, refresh **or migrate** the tree)

```bash
# --target defaults to C:\Projects on Windows when it exists, else the cwd
python scripts/repo-mirror.py restore -i repos.json

# Explicit target
python scripts/repo-mirror.py restore -i repos.json --target /home/me/code

# Preview everything without touching disk
python scripts/repo-mirror.py restore -i repos.json --dry-run
```

`restore` is idempotent and non-destructive:

| Situation on target                     | Action                                              |
|-----------------------------------------|-----------------------------------------------------|
| Folder missing                          | created                                             |
| **Repo sits at a different path**       | **moved to the manifest's path** (directory rename) |
| Repo missing                            | `git clone --recurse-submodules` + checkout branch  |
| Repo present, clean, has upstream       | `git pull --ff-only` (+ `submodule update`)         |
| Repo present, **uncommitted changes**   | **skipped** — left untouched                        |
| Repo present, **detached HEAD**         | **skipped** — left untouched                        |
| Repo present, no upstream branch        | skipped                                             |
| Path exists but is **not** a git repo   | skipped — never overwritten                         |
| Repo on target, **not in the manifest** | reported (or moved aside with `--archive-orphans`)  |
| Empty folder left behind by a move      | **asked about, one by one** (`y` / `n` / `a` / `o`) |

---

## Moving repos: how it is detected

The problem this solves: you reorganise `C:\Projects` on machine A — a repo
moves from `eCommerce/Shopware5` to `Legacy/Shopware5`. A path-based mirror
would find nothing at the new path, clone a second copy there, and leave the
old one behind for you to clean up by hand.

`restore` therefore **indexes the target tree first** and identifies every repo
it finds, independent of where it currently sits:

| # | Signal                                                     | Score | Survives                                                         |
|---|------------------------------------------------------------|-------|------------------------------------------------------------------|
| 1 | default remote URL, normalised                             | 75    | protocol switch, credentials, `.git`, case                       |
| 2 | root commit fingerprint (`rev-list --max-parents=0 --all`) | 55    | repo renamed on GitHub, moved to another forge, no remote at all |
| 3 | a secondary remote URL (fork ↔ upstream overlap)           | 45    | *never* triggers a move on its own                               |
| 4 | the recorded path                                          | 20    | only ever confirms "stay put"                                    |

Pairing is a **global greedy assignment**: all candidate pairs are sorted by
score and the strongest evidence is consumed first, so a fork and its upstream
(which share root commits) each end up with their own folder. Every repo is
claimed at most once.

Safety rules baked in:

* A **relocation needs score ≥ 55** — a shared secondary remote is never enough.
* If two repos match a manifest entry **equally well**, nothing is moved: the
  entry is reported as `ambiguous` and left for you to resolve.
* A repo that was found but could not be moved is **never cloned again** — that
  is exactly what used to produce the duplicate.
* Moves are plain directory renames. Conflicts, **swaps and cycles** (`A→B`
  while `B→A`) are resolved through a `.repo-mirror-staging` folder that is
  removed again afterwards; if anything is ever left parked there, a
  `journal.json` next to it says what belongs where.
* A repo nested inside a moving parent travels with it — it is not moved twice.

### Renamed on the forge

Signal 2 also re-finds a repo whose remote URL changed entirely. The local
clone then still points at the dead URL, which is reported as `url-drift`.
Rewriting a remote is a change to an existing repo, so it only happens on
request:

```bash
python scripts/repo-mirror.py restore -i repos.json --sync-remotes
```

---

## Options

### `scan`

| Flag                    | Meaning                                                               |
|-------------------------|-----------------------------------------------------------------------|
| `--root PATH`           | Root to scan. Default: `C:\Projects` on Windows if present, else cwd. |
| `-o, --output PATH`     | Manifest output (default `repos.json`).                               |
| `-x, --exclude GLOB`    | Exclude folders by **name or relative path** glob (repeatable).       |
| `--no-default-excludes` | Drop the built-in `BAUER GROUP Products*` / `Z*` excludes.            |
| `--prune-dir NAME`      | Add a folder name to the speed-prune set (repeatable).                |
| `--deep`                | Also descend into `node_modules`, `.venv`, `dist`, … (slower).        |
| `--no-fingerprint`      | Skip the root-commit fingerprint (faster; weaker move detection).     |
| `-j, --jobs N`          | Parallel git workers (default: min(8, cpu)).                          |

```bash
# exclude a whole category by name and a specific sub-path
python scripts/repo-mirror.py scan -x 'Archive*' -x 'eCommerce/legacy'
```

### `restore`

| Flag                    | Meaning                                                                                                          |
|-------------------------|------------------------------------------------------------------------------------------------------------------|
| `-i, --input PATH`      | Manifest to restore from (default `repos.json`).                                                                 |
| `--target PATH`         | Target root. Default: `C:\Projects` on Windows if present, else cwd.                                             |
| `--no-clone`            | Only update existing repos; do not clone missing ones.                                                           |
| `--no-update`           | Only clone missing repos; do not touch existing ones.                                                            |
| `--no-move`             | Report relocations but do not perform them. Affected repos are then neither migrated nor re-cloned.              |
| `--prune-empty-dirs`    | Remove folders a move left behind empty **without asking**.                                                      |
| `--keep-empty-dirs`     | Never remove them and never ask.                                                                                 |
| `--sync-remotes`        | Rewrite a matched repo's default remote when the manifest has a newer URL.                                       |
| `--archive-orphans DIR` | Move repos that are not in the manifest into `DIR` (relative to `--target` unless absolute). Nothing is deleted. |
| `--no-fingerprint`      | Do not fingerprint while indexing the target (faster; weaker matching).                                          |
| `-x, --exclude GLOB`    | Override the manifest's exclude patterns while indexing the target.                                              |
| `-j, --jobs N`          | Parallel git workers (default: min(8, cpu)).                                                                     |
| `--dry-run`             | Show planned actions — including moves and prunes — without changing anything.                                   |

Aliases: `scan` ≡ `create`, `restore` ≡ `update`.

---

## Deleting is the only thing you get asked about

Moving a repo is a rename — reversible, so it just happens. Removing the empty
category folder a move left behind is not, so **every single folder is
confirmed individually**:

```text
Remove empty folder 'eCommerce'? (yes / no / all / none)
```

* `y` remove this one · `n` keep it · `a` remove all further ones · `o` keep all further ones
* Enter alone = **no**.
* A **non-interactive session never deletes** (cron, CI, piped stdin) — it says
  so and keeps everything. Use `--prune-empty-dirs` there if you want pruning.
* Folders the manifest lists as part of the skeleton are never candidates.
* `--archive-orphans` *moves*, it never deletes.

---

## How it handles tricky trees

* **Nested independent repos** — a clone living *inside* another repo (often
  git-ignored, e.g. a vendored reference checkout) is detected and recorded as
  its own entry, so it survives the restore. Identified by a `.git`
  **directory**. Clone order puts parents before their nested children, so a
  parent never finds its folder occupied by an already-cloned child.
* **Submodules** — detected authoritatively from each repo's `.gitmodules`
  (works even when uninitialised). They are **not** recorded as separate repos;
  instead the parent is restored with `--recurse-submodules`, and existing
  repos get `git submodule update --init --recursive` after a fast-forward —
  recursively, so nested submodules (e.g. a vendored SDK) come along too.
* **Linked worktrees / initialised submodules** (`.git` is a *file*) are
  skipped — git reconstructs them from their parent.
* **Local-only repos** (no remote) are recorded and flagged; on restore they
  cannot be cloned, but once present they are still relocated correctly via
  their root-commit fingerprint.
* **Case-only renames** (`shop` → `Shop`) are applied on Windows too, via the
  staging folder — a plain rename would be a no-op there.

---

## Manifest format (`repo-mirror/2`)

```jsonc
{
  "schema": "repo-mirror/2",
  "tool": "repo-mirror",
  "version": "1.1.0",
  "generated_at": "2026-06-16T...Z",
  "source": {
    "host": "WS-...",
    "platform": "win32",
    "root": "C:\\Projects",
    "exclude_patterns": ["BAUER GROUP Products*", "Z*"],
    "prune_dirs": ["node_modules", "..."],
    "deep": false,
    "fingerprint": true
  },
  "directories": ["Company-Pages", "Container-Solution", "..."],
  "repositories": [
    {
      "path": "eCommerce/Shopware5",
      "remotes": { "origin": "https://github.com/..." },
      "default_remote": "origin",
      "head": "main",
      "detached": false,
      "root_commits": "9f3c1a...,",
      "submodules": ["Plugins/AmazonToolkit", "..."],
      "nested": false
    }
  ]
}
```

Repo paths use forward slashes and are reconstructed per-OS on restore, so a
manifest taken on Windows restores cleanly on Linux/macOS.

`repo-mirror/1` manifests still restore; they simply carry no fingerprint, so
relocation falls back to remote-URL matching alone.

---

## Update policy (the safe default)

Existing repos are only ever **fast-forwarded** — never merged, rebased, reset
or force-touched. Anything with local work (dirty tree, detached HEAD, no
upstream) is deliberately left alone. To change this, edit the single
`decide_update_action()` function in `scripts/repo-mirror.py`.

---

## Self-test

```bash
python scripts/repo-mirror.test.py
```

Builds a throwaway playground of real git repos in the temp folder, reorganises
it (move, rename, swap, nested parent, case-only rename, forge rename,
local-only repo, orphan) and asserts that `restore` migrates instead of
cloning. Touches nothing outside the playground; exit code 0 = all green.
