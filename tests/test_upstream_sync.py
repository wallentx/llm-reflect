"""Exercise the sync helper in disposable repositories, including merge conflicts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="reflect-sync-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in ("tools", "scripts", "reflect", "providers", "packages", "commands", "hooks", ".claude-plugin", "plugins"):
            shutil.copytree(ROOT / directory, self.root / directory, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "SKILL.md", self.root / "SKILL.md")
        shutil.copyfile(ROOT / "LICENSE", self.root / "LICENSE")
        (self.root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_minimal.py").write_text("def test_generated():\n    assert True\n", encoding="utf-8")
        self.git("init", "-b", "dev")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.git("config", "user.name", "Synthetic Test")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.git("branch", "main")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, check=True).stdout.strip()

    def sync(self, *args):
        return subprocess.run([sys.executable, "tools/sync_upstream.py", "--source", "main", *args],
                              cwd=str(self.root), capture_output=True, text=True)

    def upstream_change(self):
        self.git("switch", "main")
        source = self.root / "scripts/lib/reflect_utils.py"
        source.write_text(source.read_text(encoding="utf-8") + "\n# Synthetic upstream detector update.\n",
                          encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "upstream fix")
        self.git("switch", "dev")

    def test_preview_does_not_change_refs_or_tree(self):
        self.upstream_change()
        head = self.git("rev-parse", "HEAD")
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("upstream fix", result.stdout)
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_apply_rebuilds_leaves_merge_uncommitted_and_main_untouched(self):
        self.upstream_change()
        head = self.git("rev-parse", "HEAD")
        main = self.git("rev-parse", "main")
        result = self.sync("--apply")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        self.assertEqual(main, self.git("rev-parse", "main"))
        self.assertEqual(main, self.git("rev-parse", "MERGE_HEAD"))
        vendor = self.root / "plugins/codex/scripts/vendor/reflect_utils.py"
        self.assertIn("Synthetic upstream detector update", vendor.read_text(encoding="utf-8"))
        self.assertIn("Validated", result.stdout)

    def test_refuses_dirty_and_wrong_branch(self):
        (self.root / "user-work.txt").write_text("keep this", encoding="utf-8")
        result = self.sync("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean", result.stderr)
        self.assertEqual((self.root / "user-work.txt").read_text(encoding="utf-8"), "keep this")
        self.git("switch", "main")
        result = self.sync("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reserved", result.stderr)

    def test_conflict_preserved_without_reset(self):
        self.git("switch", "main")
        (self.root / "SKILL.md").write_text("upstream competing edit\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "upstream conflicting change")
        self.git("switch", "dev")
        (self.root / "SKILL.md").write_text("user competing edit\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "local conflicting change")
        main = self.git("rev-parse", "main")
        result = self.sync("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UU SKILL.md", self.git("status", "--porcelain"))
        self.assertEqual(main, self.git("rev-parse", "main"))
        self.assertIn("user competing edit", (self.root / "SKILL.md").read_text(encoding="utf-8"))

    def test_native_changes_update_cache_version_and_rebuild_is_stable(self):
        def build():
            subprocess.run([sys.executable, "tools/build_packages.py"], cwd=str(self.root), capture_output=True, check=True)
            return json.loads((self.root / "plugins/codex/.codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
        first = build()
        self.assertEqual(build(), first)
        path = self.root / "reflect/skills/reflect/SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nSynthetic native improvement.\n", encoding="utf-8")
        self.assertNotEqual(build(), first)


if __name__ == "__main__":
    unittest.main()
