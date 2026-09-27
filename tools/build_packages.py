#!/usr/bin/env python3
"""Build the shared LLM Reflect runtime and native provider plugin bundles."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DEST = Path("packages/reflect")
CODEX_DEST = Path("plugins/codex")
CLAUDE_DEST = Path("plugins/claude")
DESTINATIONS = (RUNTIME_DEST, CODEX_DEST, CLAUDE_DEST)


def encoded(data):
    return (json.dumps(data, indent=2) + "\n").encode()


def inputs(root):
    paths = [root / ".claude-plugin/plugin.json", root / "hooks/hooks.json", root / "SKILL.md"]
    for directory, pattern in (("commands", "*.md"), ("scripts", "*.py")):
        paths.extend((root / directory).rglob(pattern))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths, key=lambda path: path.as_posix())}


def package_version(root, destination, files, suffix, manifest=None):
    base = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    digest = hashlib.sha256(json.dumps(manifest or {}, sort_keys=True).encode())
    # Use explicit POSIX ordering, independent of Windows case-insensitive paths.
    for path, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
        digest.update(path.relative_to(destination).as_posix().encode() + b"\0" + content + b"\0")
    return base + ("." if "+" in base else "+") + suffix + "." + digest.hexdigest()[:12]


def render_runtime(root=ROOT):
    source = root / "reflect"
    result = {RUNTIME_DEST / path.relative_to(source): path.read_bytes()
              for path in sorted(source.rglob("*"), key=lambda path: path.as_posix())
              if path.is_file() and "__pycache__" not in path.parts}
    # Upstream detection, filtering and prompts are vendored verbatim.
    for name in ("reflect_utils.py", "semantic_detector.py", "__init__.py"):
        result[RUNTIME_DEST / "scripts/vendor" / name] = (root / "scripts/lib" / name).read_bytes()
    result[RUNTIME_DEST / "LICENSE"] = (root / "LICENSE").read_bytes()
    result[RUNTIME_DEST / "upstream-inputs.json"] = encoded(inputs(root))
    result[RUNTIME_DEST / "runtime.json"] = encoded({"name": "llm-reflect", "version":
        package_version(root, RUNTIME_DEST, result, "reflect")})
    return result


def render_plugin(root, provider, runtime):
    destination = CODEX_DEST if provider == "codex" else CLAUDE_DEST
    result = {destination / path.relative_to(RUNTIME_DEST): data for path, data in runtime.items()}
    result[destination / "provider.json"] = encoded({"provider": provider})
    source = root / "providers" / provider
    result[destination / "hooks/hooks.json"] = (source / "hooks/hooks.json").read_bytes()
    manifest = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    manifest["version"] = package_version(root, destination, result, provider, manifest)
    result[destination / (".codex-plugin" if provider == "codex" else ".claude-plugin") / "plugin.json"] = encoded(manifest)
    return result


def render_codex(root=ROOT):
    return render_plugin(root, "codex", render_runtime(root))


def render_claude(root=ROOT):
    return render_plugin(root, "claude", render_runtime(root))


def render(root=ROOT):
    runtime = render_runtime(root)
    return {**runtime, **render_plugin(root, "codex", runtime), **render_plugin(root, "claude", runtime)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if any generated package differs")
    args = parser.parse_args()
    expected = render()
    actual = {path.relative_to(ROOT) for destination in DESTINATIONS for path in (ROOT / destination).rglob("*")
              if path.is_file() and "__pycache__" not in path.parts}
    stale = sorted(actual - expected.keys(), key=lambda path: path.as_posix())
    changed = [path for path, content in expected.items()
               if not (ROOT / path).is_file() or (ROOT / path).read_bytes() != content]
    if args.check:
        for path in changed + stale:
            print("Generated file differs: " + str(path), file=sys.stderr)
        return int(bool(changed or stale))
    if stale:
        raise SystemExit("Unexpected package files; review before removing: " + ", ".join(map(str, stale)))
    for path in changed:
        (ROOT / path).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / path).write_bytes(expected[path])
    print("Built shared runtime, Codex plugin and Claude plugin ({} files updated).".format(len(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
