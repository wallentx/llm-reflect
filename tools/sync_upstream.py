#!/usr/bin/env python3
"""Preview or merge the upstream sync branch into dev, rebuild, and validate locally."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=str(ROOT), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def run(*args):
    subprocess.run(args, cwd=str(ROOT), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Merge into dev without committing or pushing")
    parser.add_argument("--fetch", action="store_true", help="Refresh origin/main first (no branch checkout)")
    parser.add_argument("--source", default="origin/main", choices=["main", "origin/main"],
                        help="Upstream sync branch to consume; default origin/main")
    args = parser.parse_args()
    if args.fetch:
        result = git("fetch", "origin", "main")
        print(result.stderr.strip())
    source = git("rev-parse", "--verify", args.source + "^{commit}").stdout.strip()
    branch = git("branch", "--show-current").stdout.strip()
    if branch != "dev":
        raise SystemExit("Run this from dev; main is reserved for upstream synchronization.")
    print("Source: {} ({})".format(args.source, source[:12]))
    print(git("log", "--oneline", "HEAD.." + source).stdout or "No new upstream commits.\n", end="")
    print(git("diff", "--stat", "HEAD..." + source).stdout, end="")
    if not args.apply:
        print("Preview only. To merge locally: python3 tools/sync_upstream.py --apply")
        return 0
    if git("status", "--porcelain").stdout.strip():
        raise SystemExit("Working tree must be clean; commit or preserve your work before syncing.")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        location = Path(git("rev-parse", "--git-path", marker).stdout.strip())
        if not location.is_absolute():
            location = ROOT / location
        if location.exists():
            raise SystemExit("An operation is already in progress: " + marker)
    if importlib.util.find_spec("pytest") is None:
        raise SystemExit("pytest is required before merging. Use: "
                         "uv run --no-project --with pytest python tools/sync_upstream.py --apply")
    old_inputs_path = ROOT / "packages/reflect/upstream-inputs.json"
    old_inputs = json.loads(old_inputs_path.read_text()) if old_inputs_path.exists() else {}
    result = git("merge", "--no-ff", "--no-commit", source, check=False)
    print(result.stdout, end="")
    print(result.stderr, file=sys.stderr, end="")
    if result.returncode:
        print("Merge stopped. Resolve conflicts, run tools/build_packages.py and tests, then review the merge."
              " No reset, commit, or push was performed.", file=sys.stderr)
        return result.returncode
    run(sys.executable, "tools/build_packages.py")
    new_inputs = json.loads(old_inputs_path.read_text())
    changed = sorted(p for p in old_inputs.keys() | new_inputs.keys() if old_inputs.get(p) != new_inputs.get(p))
    if changed:
        print("Upstream inputs changed; review native parity for:\n" + "\n".join(changed))
    run(sys.executable, "-m", "pytest", "tests", "-q")
    run(sys.executable, "tools/build_packages.py", "--check")
    print("Validated. Review git diff HEAD, stage generated files, and commit when ready. No push performed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        print("Sync stopped at a failed command; local changes are preserved.", file=sys.stderr)
        sys.exit(exc.returncode)
