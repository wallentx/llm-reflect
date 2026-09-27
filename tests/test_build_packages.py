"""Generated package bytes must not depend on the host's path ordering."""
import builtins
import importlib.util
import json
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_builder(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_packages = load_builder("build_packages")


def platform_sorted(flavour):
    def sort(values, *, key=None, reverse=False):
        if key is None:
            def key(value):
                path = value[0] if isinstance(value, tuple) else value
                return flavour(path.as_posix()) if isinstance(path, PurePath) else path
        return builtins.sorted(values, key=key, reverse=reverse)
    return sort


class BuildTests(unittest.TestCase):
    def test_standalone_runtime_has_shared_code_and_no_native_plugin_metadata(self):
        runtime = build_packages.render_runtime()
        relative = {path.relative_to(build_packages.RUNTIME_DEST).as_posix() for path in runtime}
        self.assertIn("scripts/providers.py", relative)
        self.assertIn("scripts/memory_adapters.py", relative)
        self.assertIn("scripts/codex_semantic.py", relative)
        self.assertIn("skills/reflect/SKILL.md", relative)
        self.assertTrue(all(not path.startswith(("hooks/", ".codex-plugin/", ".claude-plugin/")) for path in relative))
        self.assertNotIn("provider.json", relative)
        for provider, destination in (("codex", build_packages.CODEX_DEST), ("claude", build_packages.CLAUDE_DEST)):
            plugin = build_packages.render_plugin(ROOT, provider, runtime)
            for path, content in runtime.items():
                self.assertEqual(plugin[destination / path.relative_to(build_packages.RUNTIME_DEST)], content)
            self.assertEqual(json.loads(plugin[destination / "provider.json"])["provider"], provider)

    def test_provider_metadata_changes_only_that_native_package(self):
        runtime = build_packages.render_runtime()
        with tempfile.TemporaryDirectory(prefix="reflect-package-") as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "providers", root / "providers")
            (root / ".claude-plugin").mkdir()
            shutil.copyfile(ROOT / ".claude-plugin/plugin.json", root / ".claude-plugin/plugin.json")
            codex = build_packages.render_plugin(root, "codex", runtime)
            claude = build_packages.render_plugin(root, "claude", runtime)
            path = root / "providers/claude/plugin.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["description"] += " Updated provider metadata."
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(build_packages.render_plugin(root, "codex", runtime), codex)
            self.assertNotEqual(build_packages.render_plugin(root, "claude", runtime), claude)

    def test_input_metadata_bytes_match_across_path_flavours(self):
        results = []
        for flavour in (PurePosixPath, PureWindowsPath):
            with patch.object(build_packages, "sorted", platform_sorted(flavour), create=True):
                results.append(json.dumps(build_packages.inputs(ROOT)))
        self.assertEqual(results[0], results[1])

    def test_all_package_bytes_and_versions_match_across_path_flavours(self):
        results = []
        for flavour in (PurePosixPath, PureWindowsPath):
            with patch.object(build_packages, "sorted", platform_sorted(flavour), create=True):
                results.append(build_packages.render())
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
