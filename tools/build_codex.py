#!/usr/bin/env python3
"""Build the installable Codex package from upstream inputs and the Codex overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEST = Path("plugins/codex-reflect")


def inputs(root):
    paths = [root / ".claude-plugin/plugin.json", root / "hooks/hooks.json", root / "SKILL.md"]
    for directory, pattern in (("commands", "*.md"), ("scripts", "*.py")):
        paths.extend((root / directory).rglob(pattern))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths, key=lambda path: path.as_posix())}


def render(root=ROOT):
    upstream = json.loads((root / ".claude-plugin/plugin.json").read_text())
    overlay = root / "codex_port"
    manifest = json.loads((overlay / "plugin.json").read_text())
    result = {}
    for p in sorted(overlay.rglob("*"), key=lambda path: path.as_posix()):
        if p.is_file() and "__pycache__" not in p.parts and p != overlay / "plugin.json":
            result[DEST / p.relative_to(overlay)] = p.read_bytes()
    # Vendor verbatim: fixes to detection, filtering, prompts, and inclusion parsing
    # arrive through regeneration. The native adapter never calls Claude storage/CLI APIs.
    for name in ("reflect_utils.py", "semantic_detector.py", "__init__.py"):
        result[DEST / "scripts/vendor" / name] = (root / "scripts/lib" / name).read_bytes()
    result[DEST / "LICENSE"] = (root / "LICENSE").read_bytes()
    result[DEST / "upstream-inputs.json"] = (json.dumps(inputs(root), indent=2) + "\n").encode()
    # A deterministic cache version changes for upstream fixes AND native edits.
    # Identical rebuilds keep the same install identity.
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode())
    # Path ordering is case-insensitive on Windows; hash the same order everywhere.
    for path, content in sorted(result.items(), key=lambda item: item[0].as_posix()):
        digest.update(path.as_posix().encode() + b"\0" + content + b"\0")
    separator = "." if "+" in upstream["version"] else "+"
    manifest["version"] = upstream["version"] + separator + "codex." + digest.hexdigest()[:12]
    result[DEST / ".codex-plugin/plugin.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated files differ")
    args = parser.parse_args()
    expected = render()
    actual = {p.relative_to(ROOT) for p in (ROOT / DEST).rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    stale = sorted(actual - expected.keys())
    changed = [p for p, content in expected.items()
               if not (ROOT / p).exists() or (ROOT / p).read_bytes() != content]
    if args.check:
        for p in changed + stale:
            print("Generated file differs: " + str(p), file=sys.stderr)
        return int(bool(changed or stale))
    if stale:
        raise SystemExit("Unexpected package files; review before removing: " + ", ".join(map(str, stale)))
    for p in changed:
        (ROOT / p).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / p).write_bytes(expected[p])
    print("Built codex-reflect ({} files updated).".format(len(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
