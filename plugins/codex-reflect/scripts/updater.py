"""Refresh a checkout without discarding edits, then run its current installer."""
from pathlib import Path
import subprocess
import sys

from installer import PACKAGE, read_json, shell_command, source_root


def refresh_checkout(root, dry_run=False):
    root = Path(root).resolve()
    def git(*arguments):
        try:
            result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *arguments], capture_output=True,
                                    text=True, encoding="utf-8", check=False, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git update timed out after 60 seconds; rerun the update") from exc
        if result.returncode:
            raise ValueError("Cannot update checkout: " + result.stderr.strip() +
                             ". Use --no-pull to install its current files.")
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Update source must be the original repository root: " + str(root))
    if git("status", "--porcelain"):
        raise ValueError("Checkout has local edits: " + str(root) +
                         ". Commit them first, or use --no-pull to install the current files.")
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    command = ["git", "-C", str(root), "pull", "--ff-only", "--no-rebase", "--no-autostash"]
    print("Update " + branch + " from " + upstream + ": " + shell_command(command), flush=True)
    if not dry_run:
        try:
            result = subprocess.run(command, check=False, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git update timed out after 60 seconds; rerun the update") from exc
        if result.returncode:
            raise RuntimeError("Checkout update failed; resolve the Git error before rerunning. No reset or stash was performed.")


def installation_prefix():
    metadata = read_json(PACKAGE / "source.json")
    if metadata.get("prefix"):
        return Path(metadata["prefix"]).expanduser().absolute()
    # Installations made before update support only recorded the checkout.
    if PACKAGE.name == "reflect" and PACKAGE.parent.name == "share":
        return PACKAGE.parent.parent
    return Path.home() / ".local"


def run_update(selected=None, dry_run=False, no_pull=False):
    root = source_root()
    script = root / "tools/install.py"
    if not script.is_file():
        raise ValueError("Original checkout is unavailable: " + str(root) +
                         ". Clone llm-reflect and run its install.sh -u.")
    command = [sys.executable, str(script), "--update", "--prefix", str(installation_prefix())]
    for name in selected or []:
        command.extend(["--provider", name])
    if dry_run:
        command.append("--dry-run")
    if no_pull:
        command.append("--no-pull")
    print("RUN    " + shell_command(command), flush=True)
    return subprocess.run(command, check=False).returncode
