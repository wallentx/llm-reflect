#!/usr/bin/env python3
"""Install or update LLM Reflect and its provider integrations."""
import sys

if sys.version_info < (3, 11):
    sys.exit("LLM Reflect requires Python 3.11 or newer.")

import argparse
import json
import os
from pathlib import Path
import subprocess
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-reflect/scripts"))
from installer import apply_writes, encoded, file_plan, read_json, shell_command
import installer
import providers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-configure", action="store_true", help="Install the command without opening the provider TUI")
    parser.add_argument("-u", "--update", action="store_true", help="Pull this branch and update installed providers without opening the TUI")
    parser.add_argument("--provider", action="append", choices=sorted(providers.PROVIDERS), help="Update only this installed provider (repeatable)")
    parser.add_argument("--no-pull", action="store_true", help="Update using the current checkout, without fetching Git changes")
    parser.add_argument("--refreshed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (args.provider or args.no_pull or args.refreshed) and not args.update:
        parser.error("--provider and --no-pull require --update")
    if args.update:
        installer.update_providers(installer.installation_state()[1], args.provider)
    if args.update and not args.no_pull and not args.refreshed:
        from updater import refresh_checkout
        refresh_checkout(ROOT, args.dry_run)
        if not args.dry_run:
            # Reload imports and installer logic from the freshly pulled checkout.
            return subprocess.run([sys.executable, str(ROOT / "tools/install.py"),
                                   *sys.argv[1:], "--refreshed"], check=False).returncode
    from build_codex import render
    from build_providers import render as render_providers
    for path, content in list(render().items()) + list(render_providers().items()):
        if not (ROOT / path).is_file() or (ROOT / path).read_bytes() != content:
            raise ValueError("Generated packages are stale. Run python3 tools/build_codex.py and python3 tools/build_providers.py first.")
    prefix = args.prefix.expanduser().absolute()
    destination = prefix / "share/reflect"
    manifest_path = destination / "install-manifest.json"
    old = read_json(manifest_path).get("files", {})
    files, writes = {}, {}
    source = ROOT / "plugins/codex-reflect"
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            file_plan(destination / path.relative_to(source), path.read_bytes(), old, writes, files)
    file_plan(destination / "source.json", encoded({"checkout": str(ROOT), "prefix": str(prefix)}), old, writes, files)
    script = destination / "scripts/reflect.py"
    if os.name == "nt":
        launcher = prefix / "bin/reflect.cmd"
        content = ("@echo off\r\n" + shell_command([sys.executable, script]) + " %*\r\n").encode()
    else:
        launcher = prefix / "bin/reflect"
        # Shell trampoline supports Python and installation paths containing spaces.
        content = ("#!/bin/sh\nexec " + shell_command([sys.executable, script]) + ' "$@"\n').encode()
        # Termux has /bin/sh on some hosts only. Use the actual available shell.
        import shutil
        shell = shutil.which("sh")
        if shell and " " not in shell:
            content = content.replace(b"#!/bin/sh", ("#!" + shell).encode(), 1)
    file_plan(launcher, content, old, writes, files)
    for stale in old.keys() - files.keys():
        file_plan(stale, None, old, writes, files)
    new_manifest = encoded({"files": files})
    if not manifest_path.exists() or manifest_path.read_bytes() != new_manifest:
        writes[manifest_path] = new_manifest
    apply_writes(writes, args.dry_run)
    if not args.dry_run:
        launcher.chmod(0o755)
    print(("Would update: " if args.update and args.dry_run else "Updated: " if args.update else
           "Would install: " if args.dry_run else "Installed: ") + str(launcher), flush=True)
    if args.update and not args.no_configure:
        if args.dry_run:
            # Preview new templates with the actual destination's runtime bindings.
            installer.PACKAGE = destination
            installer.TEMPLATES = source
            return installer.initialize(argparse.Namespace(selected=args.provider, method="auto", remove=False,
                                        list=False, dry_run=True, refresh_only=True))
        command = [sys.executable, str(script), "init", "--refresh-only"]
        for name in args.provider or []:
            command.extend(["--provider", name])
        return subprocess.run(command, check=False).returncode
    if not args.dry_run and not args.no_configure and sys.stdin.isatty() and sys.stderr.isatty():
        return subprocess.run([sys.executable, str(script), "init"], check=False).returncode
    print("Next: " + shell_command([launcher, "init"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print("reflect install: " + str(exc), file=sys.stderr)
        sys.exit(1)
