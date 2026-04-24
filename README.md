# DevTools - Swiss Army Knife for Git-based Development

A collection of Docker-based developer tools for Git workflows and development automation. All tools run in isolated containers for platform independence.

## Services

This repository contains independent tools that can be used separately:

| Service | Description | Location |
|---------|-------------|----------|
| **DevTools** | Git & Python runtime container | `./devtools.sh` |
| **Dozzle** | Docker container log viewer | `services/dozzle/` |

## DevTools Runtime Container

Interactive container with Git, Python 3.13, and shell utilities for Git-based development workflows.

### Features

- **Git Tools** - Advanced Git commands, statistics, history rewriting, and automation
- **GitHub Tools** - Repository management, topics, archiving, and workflow triggers
- **Python Environment** - Full Python 3.13 with common libraries (GitPython, Click, Rich, etc.)
- **Shell Utilities** - curl, jq, yq, git-filter-repo, GitHub CLI, and more
- **Platform Independent** - Runs identically on Windows, macOS, and Linux
- **Auto-configured** - Git credentials from host, helpful aliases pre-installed

### Quick Start

```powershell
# Build the container
.\devtools.ps1 build

# Start interactive shell in current directory
.\devtools.ps1 shell

# Start shell in a specific project
.\devtools.ps1 shell C:\Projects\MyApp
```

### Commands

#### Runtime Container

| Command | Description |
|---------|-------------|
| `shell [PATH]` | Start interactive DevTools shell |
| `run <script>` | Run a script in the container |
| `build` | Build/rebuild DevTools container |

#### Git Tools

| Command | Description |
|---------|-------------|
| `stats [PATH]` | Show repository statistics |
| `cleanup [PATH]` | Clean up branches and cache |
| `changelog` | Generate changelog from commits |
| `release` | Manage semantic versioning releases |
| `lfs-migrate` | Migrate repository to Git LFS |
| `history-clean` | Remove large files from git history |
| `branch-rename` | Rename git branches (local + remote) |
| `split-repo` | Split monorepo into separate repos |
| `rewrite-commits` | Rewrite commit messages (pattern-based) |

#### GitHub Tools

| Command | Description |
|---------|-------------|
| `gh-create` | Create GitHub repository |
| `gh-topics` | Manage repository topics |
| `gh-archive` | Archive repositories by criteria |
| `gh-workflow` | Trigger GitHub Actions workflows |
| `gh-add-workflow` | Add workflow files to repositories |
| `gh-clean-releases` | Clean releases and tags |
| `gh-visibility` | Change repo visibility (public/private) |
| `gh-clone-org` | Clone all repos from organization |
| `gh-sync-forks` | Sync forked repos with upstream |
| `gh-pr-cleanup` | Clean stale PRs and branches |
| `gh-secrets-audit` | Audit secrets across repos |
| `gh-secrets-sync` | Sync local `.env` to repo secrets (push + prune obsolete) |
| `gh-labels-sync` | Sync labels between repos |
| `gh-dependabot-labels` | Sync labels from dependabot.yml |
| `gh-packages-cleanup` | Clean old package versions |
| `gh-branch-protect` | Manage branch protection rules |

#### Advanced Git Tools

| Command | Description |
|---------|-------------|
| `git-mirror` | Mirror repo between Git servers |
| `git-contributors` | Show contributor statistics |

### Tool Details

#### Git History Tools

```bash
# Remove large files from history (requires git-filter-repo)
./devtools.sh history-clean --analyze              # Show large files
./devtools.sh history-clean -s 50M --dry-run       # Preview cleanup

# Rename branches (master → main)
./devtools.sh branch-rename --master-to-main       # Full migration
./devtools.sh branch-rename old-name new-name      # Custom rename

# Split monorepo into separate repos
./devtools.sh split-repo dir1,dir2 -o myorg        # Split to GitHub
./devtools.sh split-repo services/api --submodule  # Keep as submodule

# Rewrite commit messages (remove AI attributions, etc.)
./devtools.sh rewrite-commits --preset claude --dry-run
./devtools.sh rewrite-commits --preset ai-all
./devtools.sh rewrite-commits -p "TICKET-\d+:\s*"  # Custom pattern
```

#### GitHub Management Tools

```bash
# Create repository
./devtools.sh gh-create myrepo --public --init
./devtools.sh gh-create -o myorg myrepo -t "python,cli" --license MIT

# Manage topics across repos
./devtools.sh gh-topics -o myorg --analyze           # Topic statistics
./devtools.sh gh-topics -o myorg --add python,api    # Add to all repos
./devtools.sh gh-topics myorg/repo --sync cli,tool   # Ensure topics exist

# Archive inactive repositories
./devtools.sh gh-archive -o myorg --inactive 365 --dry-run
./devtools.sh gh-archive -o myorg --empty            # Archive empty repos
./devtools.sh gh-archive myorg/old-repo              # Archive single repo

# Trigger GitHub Actions
./devtools.sh gh-workflow myorg/repo --list          # List workflows
./devtools.sh gh-workflow myorg/repo ci.yml          # Trigger workflow
./devtools.sh gh-workflow myorg/repo deploy.yml -i env=prod --wait

# Add workflow files to repos
./devtools.sh gh-add-workflow -o myorg --topic api -f ci.yml
./devtools.sh gh-add-workflow myorg/repo -f deploy.yml --skip-existing

# Clean releases and tags
./devtools.sh gh-clean-releases myorg/repo --list    # List releases/tags
./devtools.sh gh-clean-releases myorg/repo --all --dry-run
./devtools.sh gh-clean-releases -o myorg --topic old --prereleases

# Change repository visibility
./devtools.sh gh-visibility myorg/repo --public
./devtools.sh gh-visibility -o myorg --topic open-source --public --dry-run
./devtools.sh gh-visibility -o myorg --current private --list
```

#### Organization & Repository Management

```bash
# Clone all repos from organization
./devtools.sh gh-clone-org myorg -o ~/backup/myorg
./devtools.sh gh-clone-org myorg -t python --shallow     # Only Python repos
./devtools.sh gh-clone-org myorg --pull                  # Update existing clones

# Sync forked repos with upstream
./devtools.sh gh-sync-forks --list                       # Show fork status
./devtools.sh gh-sync-forks myuser/my-fork               # Sync single fork
./devtools.sh gh-sync-forks --all --behind               # Sync all behind forks

# Clean stale PRs and branches
./devtools.sh gh-pr-cleanup myorg/repo --list --stale-days 30
./devtools.sh gh-pr-cleanup myorg/repo --close-stale --stale-days 60
./devtools.sh gh-pr-cleanup myorg/repo --delete-merged-branches

# Audit secrets across repos
./devtools.sh gh-secrets-audit -o myorg                  # Audit all repos
./devtools.sh gh-secrets-audit -o myorg --compare        # Compare coverage
./devtools.sh gh-secrets-audit myorg/repo --all          # Include Dependabot/envs

# Sync local .env to repo secrets (push + prune obsolete)
./devtools.sh run gh-secrets-sync.py --dry-run           # Preview plan
./devtools.sh run gh-secrets-sync.py                     # Apply (prompts before deletions)
./devtools.sh run gh-secrets-sync.py --yes               # Apply without prompt (CI)
./devtools.sh run gh-secrets-sync.py -R owner/repo       # Target a different repo

# Sync labels between repos
./devtools.sh gh-labels-sync source/repo target/repo
./devtools.sh gh-labels-sync --preset standard myorg/repo
./devtools.sh gh-labels-sync source/repo --export > labels.json

# Manage branch protection
./devtools.sh gh-branch-protect myorg/repo --list
./devtools.sh gh-branch-protect myorg/repo main --preset strict
./devtools.sh gh-branch-protect --org myorg --branch main --preset standard

# Sync dependabot labels across repos
./devtools.sh gh-dependabot-labels                        # Dry run
./devtools.sh gh-dependabot-labels --execute              # Create labels
./devtools.sh gh-dependabot-labels --execute --cleanup    # Also remove old labels

# Clean old package versions
./devtools.sh gh-packages-cleanup                         # Dry run
./devtools.sh gh-packages-cleanup --execute               # Delete old versions
./devtools.sh gh-packages-cleanup -t container            # Only containers
```

#### Mirror & Statistics Tools

```bash
# Mirror repo between servers
./devtools.sh git-mirror source.git target.git
./devtools.sh git-mirror source.git target.git --force --lfs
./devtools.sh git-mirror source.git target.git --wiki

# Contributor statistics
./devtools.sh git-contributors                           # Current repo
./devtools.sh git-contributors --since "30 days ago"
./devtools.sh git-contributors --detailed --activity
./devtools.sh git-contributors --json > contributors.json
```

### Inside the Container

**Shell Scripts:**
- `git-stats.sh` - Comprehensive repository statistics
- `git-cleanup.sh` - Clean up merged/stale branches
- `git-lfs-migrate.sh` - LFS migration with 100+ file patterns
- `git-history-clean.sh` - Remove large files from history
- `git-branch-rename.sh` - Rename branches with remote sync
- `git-mirror.sh` - Mirror repositories between Git servers
- `gh-create-repo.sh` - Create GitHub repositories
- `gh-trigger-workflow.sh` - Trigger GitHub Actions
- `gh-clone-org.sh` - Clone all repos from organization
- `help-devtools` - Show all available commands

**Python Tools:**
- `git-changelog.py` - Generate changelog from commits
- `git-release.py` - Semantic versioning release manager
- `git-split-repo.py` - Split monorepo into separate repos
- `git-rewrite-commits.py` - Pattern-based commit message rewriting
- `git-contributors.py` - Contributor statistics with activity patterns
- `gh-topic-manager.py` - Manage repository topics
- `gh-archive-repos.py` - Archive repositories by criteria
- `gh-add-workflow.py` - Add workflow files to repos
- `gh-clean-releases.py` - Clean releases and tags
- `gh-visibility.py` - Change repository visibility
- `gh-sync-forks.py` - Sync forked repos with upstream
- `gh-pr-cleanup.py` - Clean stale PRs and branches
- `gh-secrets-audit.py` - Audit secrets across repos
- `gh-secrets-sync.py` - Sync local `.env` to repo secrets (push + prune obsolete, allowlisted via `.env.example`)
- `gh-labels-sync.py` - Sync labels between repos
- `gh-dependabot-labels.py` - Sync labels from dependabot.yml
- `gh-packages-cleanup.py` - Clean old package versions
- `gh-branch-protection.py` - Manage branch protection rules

**Pre-configured Git Aliases:**
| Alias | Command |
|-------|---------|
| `git st` | `status -sb` |
| `git lg` | Log graph (20 commits) |
| `git lga` | Full log graph, all branches |
| `git branches` | List branches by date |
| `git last` | Show last commit |
| `git undo` | Soft reset last commit |
| `git amend` | Amend last commit |

### Examples

```bash
# Repository statistics
./devtools.sh stats

# Clean up branches (preview)
./devtools.sh cleanup --dry-run

# Generate changelog
./devtools.sh run "git-changelog.py -o CHANGELOG.md"

# Interactive release
./devtools.sh release release

# Migrate to LFS
./devtools.sh lfs-migrate --dry-run
./devtools.sh lfs-migrate --push
```

---

## Dozzle - Container Monitor

Independent Docker container log viewer. See [services/dozzle/README.md](services/dozzle/README.md) for details.

### Quick Start

```bash
cd services/dozzle
cp .env.example .env
./scripts/dozzle.sh start
```

---

## Project Structure

```
DeveloperTools/
├── devtools.sh              # DevTools CLI (Linux/macOS)
├── devtools.ps1             # DevTools CLI (Windows PowerShell)
├── devtools.cmd             # DevTools CLI (Windows CMD)
│
├── services/
│   ├── devtools/            # DevTools Runtime Container
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── requirements.txt
│   │   └── scripts/
│   │       ├── git-stats.sh
│   │       ├── git-cleanup.sh
│   │       ├── git-changelog.py
│   │       ├── git-release.py
│   │       ├── git-lfs-migrate.sh
│   │       ├── git-history-clean.sh
│   │       ├── git-branch-rename.sh
│   │       ├── git-split-repo.py
│   │       ├── git-rewrite-commits.py
│   │       ├── gh-create-repo.sh
│   │       ├── gh-topic-manager.py
│   │       ├── gh-archive-repos.py
│   │       ├── gh-trigger-workflow.sh
│   │       ├── gh-add-workflow.py
│   │       ├── gh-clean-releases.py
│   │       ├── gh-visibility.py
│   │       ├── gh-clone-org.sh
│   │       ├── gh-sync-forks.py
│   │       ├── gh-pr-cleanup.py
│   │       ├── gh-secrets-audit.py
│   │       ├── gh-labels-sync.py
│   │       ├── gh-dependabot-labels.py
│   │       ├── gh-packages-cleanup.py
│   │       ├── gh-branch-protection.py
│   │       ├── git-mirror.sh
│   │       ├── git-contributors.py
│   │       └── help-devtools.sh
│   │
│   └── dozzle/              # Container Monitor (independent)
│       ├── docker-compose.yml
│       ├── .env.example
│       ├── README.md
│       ├── data/
│       │   └── users.yml.example
│       └── scripts/
│           ├── dozzle.sh
│           └── dozzle.ps1
│
└── .github/                 # CI/CD workflows
```

## Adding New Tools

1. Add scripts to `services/devtools/scripts/` with metadata header:

   ```bash
   #!/bin/bash
   # @name: my-tool
   # @description: Short description of the tool
   # @category: git|github
   # @usage: my-tool [options]
   ```

2. Add Python dependencies to `requirements.txt` if needed
3. Rebuild the container: `devtools build`
4. The tool is automatically discovered by `help-devtools`
5. Optionally add CLI shortcut to `devtools.sh`, `devtools.ps1`, and `devtools.cmd`

## Requirements

- Docker Desktop (Windows/macOS) or Docker Engine (Linux)
- Docker Compose v2+ (for Dozzle)
- GitHub CLI (`gh`) for GitHub tools (installed in container)

## License

MIT
