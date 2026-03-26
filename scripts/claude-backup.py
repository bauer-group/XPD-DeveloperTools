#!/usr/bin/env python3
"""
Claude Code Configuration Backup & Restore
Backs up critical Claude Code config from ~/.claude
Cross-platform: Windows, macOS, Linux
"""

import argparse
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# What to backup
INCLUDE_FILES = [
    "settings.json",
]
INCLUDE_DIRS = [
    "plugins",
]
# Per-project: only memory/, settings.json, CLAUDE.md
PROJECT_ITEMS = ["memory", "settings.json", "CLAUDE.md"]

# Explicitly excluded (for documentation / awareness)
EXCLUDE = [
    ".bauer-standards",  # Git repo, self-managed
    ".credentials.json",  # Sensitive auth tokens
    "backups",  # Claude internal backups
    "cache",
    "debug",
    "downloads",
    "file-history",
    "history.jsonl",
    "ide",
    "mcp-needs-auth-cache.json",
    "plans",
    "session-env",
    "sessions",
    "shell-snapshots",
    "stats-cache.json",
    "statsig",
    "telemetry",
    "todos",
]


def info(msg):
    print(f"[INFO] {msg}")


def ok(msg):
    print(f"[OK] {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def err(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)


def get_claude_dir():
    return Path.home() / ".claude"


def get_backup_dir():
    script_dir = Path(__file__).resolve().parent.parent
    return script_dir / ".data" / "claude-backups"


def backup(args):
    claude_dir = get_claude_dir()
    if not claude_dir.is_dir():
        err(f"Claude directory not found: {claude_dir}")
        sys.exit(1)

    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    zip_name = f"claude-backup_{timestamp}.zip"
    zip_path = backup_dir / zip_name

    info("Backing up Claude Code config...")
    info(f"Source: {claude_dir}")

    file_count = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Top-level files
        for filename in INCLUDE_FILES:
            src = claude_dir / filename
            if src.is_file():
                zf.write(src, filename)
                file_count += 1
                info(f"  + {filename}")

        # Included directories
        for dirname in INCLUDE_DIRS:
            src = claude_dir / dirname
            if src.is_dir():
                count = 0
                for item in src.rglob("*"):
                    if item.is_file():
                        arcname = str(item.relative_to(claude_dir))
                        zf.write(item, arcname)
                        count += 1
                file_count += count
                info(f"  + {dirname}/ ({count} files)")

        # Project memories and settings
        projects_dir = claude_dir / "projects"
        if projects_dir.is_dir():
            mem_count = 0
            proj_count = 0
            config_count = 0

            for proj_dir in sorted(projects_dir.iterdir()):
                if not proj_dir.is_dir():
                    continue

                proj_has_content = False

                # Memory directory
                mem_dir = proj_dir / "memory"
                if mem_dir.is_dir():
                    for item in mem_dir.rglob("*"):
                        if item.is_file():
                            arcname = str(item.relative_to(claude_dir))
                            zf.write(item, arcname)
                            mem_count += 1
                            proj_has_content = True

                # Per-project config files
                for config_name in ["settings.json", "CLAUDE.md"]:
                    config_file = proj_dir / config_name
                    if config_file.is_file():
                        arcname = str(config_file.relative_to(claude_dir))
                        zf.write(config_file, arcname)
                        config_count += 1
                        proj_has_content = True

                if proj_has_content:
                    proj_count += 1

            if mem_count > 0:
                info(f"  + projects/*/memory/ ({mem_count} files across {proj_count} projects)")
            if config_count > 0:
                info(f"  + projects/*/config ({config_count} files)")
            file_count += mem_count + config_count

    if file_count == 0:
        warn("No files found to backup")
        zip_path.unlink(missing_ok=True)
        return

    zip_size = zip_path.stat().st_size / 1024
    ok(f"Backup created: {zip_name} ({zip_size:.1f} KB, {file_count} files)")
    info(f"Location: {zip_path}")

    # Cleanup old backups
    cleanup_old_backups(backup_dir, args.keep)


def cleanup_old_backups(backup_dir, keep):
    backups = sorted(backup_dir.glob("claude-backup_*.zip"), reverse=True)
    if len(backups) > keep:
        for old in backups[keep:]:
            old.unlink()
            info(f"Removed old backup: {old.name}")


def restore(args):
    backup_dir = get_backup_dir()
    claude_dir = get_claude_dir()

    # Determine backup file
    if args.file:
        zip_path = Path(args.file)
        if not zip_path.is_file():
            # Try relative to backup dir
            zip_path = backup_dir / args.file
        if not zip_path.is_file():
            err(f"Backup file not found: {args.file}")
            sys.exit(1)
    else:
        # Use most recent
        if not backup_dir.is_dir():
            err(f"No backups found in {backup_dir}")
            sys.exit(1)
        backups = sorted(backup_dir.glob("claude-backup_*.zip"), reverse=True)
        if not backups:
            err("No backup files found")
            sys.exit(1)
        zip_path = backups[0]
        info(f"Using latest backup: {zip_path.name}")

    claude_dir.mkdir(parents=True, exist_ok=True)

    info(f"Restoring from: {zip_path}")

    file_count = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.namelist()

        for member in members:
            dest = claude_dir / member
            # Skip directory entries
            if member.endswith("/"):
                continue

            # Pre-restore backup for top-level config files
            if member in INCLUDE_FILES and dest.is_file():
                pre_restore = dest.with_suffix(dest.suffix + ".pre-restore")
                shutil.copy2(dest, pre_restore)
                if file_count == 0:  # Only print once per file
                    info(f"  Existing {member} saved as {member}.pre-restore")

            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                dst.write(src.read())
            file_count += 1

    ok(f"Restored {file_count} files from backup")
    warn("Restart Claude Code for changes to take effect")


def list_backups(args):
    backup_dir = get_backup_dir()

    if not backup_dir.is_dir():
        info("No backups directory found")
        return

    backups = sorted(backup_dir.glob("claude-backup_*.zip"), reverse=True)

    if not backups:
        info("No backups found")
        return

    print()
    print(f"  Claude Code Backups ({len(backups)} found)")
    print(f"  {'=' * 50}")
    print()

    for b in backups:
        size_kb = b.stat().st_size / 1024
        date = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {date}  {size_kb:>8.1f} KB  {b.name}")

    print()
    print(f"  Location: {backup_dir}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Claude Code Configuration Backup & Restore",
        prog="devtools claude-backup",
    )
    sub = parser.add_subparsers(dest="action")

    # backup (default)
    bp = sub.add_parser("backup", help="Create a backup (default)")
    bp.add_argument("--keep", type=int, default=10, help="Number of backups to keep (default: 10)")

    # restore
    rp = sub.add_parser("restore", help="Restore from a backup")
    rp.add_argument("file", nargs="?", default=None, help="Backup file (default: latest)")

    # list
    sub.add_parser("list", help="List available backups")

    args = parser.parse_args()

    if args.action is None or args.action == "backup":
        if not hasattr(args, "keep"):
            args.keep = 10
        backup(args)
    elif args.action == "restore":
        restore(args)
    elif args.action == "list":
        list_backups(args)


if __name__ == "__main__":
    main()
