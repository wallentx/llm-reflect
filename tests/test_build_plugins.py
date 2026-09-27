"""Generated package bytes must not depend on the host's path ordering."""
import builtins
import importlib.util
import json
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_builder(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_codex = load_builder("build_codex")
with patch.dict(sys.modules, {"build_codex": build_codex}):
    build_providers = load_builder("build_providers")


def platform_sorted(flavour):
    def sort(values, *, key=None, reverse=False):
        if key is None:
            def key(value):
                path = value[0] if isinstance(value, tuple) else value
                return flavour(path.as_posix()) if isinstance(path, PurePath) else path
        return builtins.sorted(values, key=key, reverse=reverse)
    return sort


class BuildTests(unittest.TestCase):
    def test_input_metadata_bytes_match_across_path_flavours(self):
        results = []
        for flavour in (PurePosixPath, PureWindowsPath):
            with patch.object(build_codex, "sorted", platform_sorted(flavour), create=True):
                results.append(json.dumps(build_codex.inputs(ROOT)))
        self.assertEqual(results[0], results[1])

    def test_both_package_bytes_and_versions_match_across_path_flavours(self):
        results = []
        for flavour in (PurePosixPath, PureWindowsPath):
            with patch.object(build_codex, "sorted", platform_sorted(flavour), create=True), \
                    patch.object(build_providers, "sorted", platform_sorted(flavour), create=True):
                results.append((build_codex.render(), build_providers.render()))
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
