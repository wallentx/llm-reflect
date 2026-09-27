#!/usr/bin/env python3
"""Build the reviewed Claude marketplace package from the shared Codex overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from build_codex import ROOT, DEST, render as render_codex

CLAUDE_DEST = Path("plugins/claude-reflect")


def render(root=ROOT):
    shared = render_codex(root)
    result = {}
    for path, data in shared.items():
        relative = path.relative_to(DEST)
        if relative.parts[0] in ("scripts", "skills", "references") or relative.name == "LICENSE":
            result[CLAUDE_DEST / relative] = data
    result[CLAUDE_DEST / "provider.json"] = b'{"provider": "claude"}\n'
    hooks = json.loads((root / "codex_port/hooks/hooks.json").read_text(encoding="utf-8"))
    for groups in hooks["hooks"].values():
        for group in groups:
            for entry in group["hooks"]:
                entry["command"] = entry["command"].replace("${PLUGIN_ROOT}", "${CLAUDE_PLUGIN_ROOT}")
                entry["command"] = entry["command"].replace(" hook ", " --provider claude hook ")
    result[CLAUDE_DEST / "hooks/hooks.json"] = (json.dumps(hooks, indent=2) + "\n").encode()
    codex_manifest = json.loads(shared[DEST / ".codex-plugin/plugin.json"])
    manifest = {"name": "reflect", "description": "LLM Reflect for Claude Code: reviewed learning proposals, correction capture, and skill discovery.",
                "author": codex_manifest["author"], "license": codex_manifest["license"],
                "repository": codex_manifest["repository"]}
    # Provider-specific manifest/hook changes must also invalidate its cache.
    content_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode())
    for path, data in sorted(result.items(), key=lambda item: item[0].as_posix()):
        content_hash.update(path.as_posix().encode() + b"\0" + data + b"\0")
    manifest["version"] = codex_manifest["version"] + ".claude." + content_hash.hexdigest()[:12]
    result[CLAUDE_DEST / ".claude-plugin/plugin.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    actual = {p.relative_to(ROOT) for p in (ROOT / CLAUDE_DEST).rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    stale = actual - expected.keys()
    changed = [p for p, content in expected.items() if not (ROOT / p).exists() or (ROOT / p).read_bytes() != content]
    if args.check:
        for path in changed + sorted(stale):
            print("Generated file differs: " + str(path), file=sys.stderr)
        return int(bool(changed or stale))
    if stale:
        raise SystemExit("Unexpected package files; review before removing: " + ", ".join(map(str, stale)))
    for path in changed:
        (ROOT / path).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / path).write_bytes(expected[path])
    print("Built Claude Reflect ({} files updated).".format(len(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
