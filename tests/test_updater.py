"""Provider updates, update-only TUI actions, and a real fast-forward checkout."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tests import test_providers as fixtures
from tests import test_tui as tui_fixtures
import installer
import providers
import tui
import updater

ROOT = fixtures.ROOT


class UpdateTests(unittest.TestCase):
    cli = fixtures.ProviderTests.cli
    init = fixtures.ProviderTests.init
    snapshot = fixtures.ProviderTests.snapshot
    upstream = tui_fixtures.PickerTests.upstream

    def setUp(self):
        fixtures.ProviderTests.setUp(self)

    def arguments(self, selected=None, dry_run=False, **extra):
        return argparse.Namespace(selected=selected, dry_run=dry_run, remove=False,
                                  method="auto", list=False, refresh_only=True, **extra)

    def test_update_selection_includes_only_llm_reflect_and_deduplicates(self):
        installs = {"gemini": {"method": "local"}, "codex": {"method": "marketplace"},
                    "claude": {"method": "upstream"}}
        self.assertEqual(installer.update_providers(installs), ["codex", "gemini"])
        self.assertEqual(installer.update_providers(installs, ["gemini", "gemini"]), ["gemini"])
        for name in ("cursor", "claude"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "No installed LLM Reflect"):
                installer.update_providers(installs, [name])

    def test_refresh_does_not_install_new_providers_or_remove_upstream(self):
        self.upstream()
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        before = self.snapshot()
        self.assertEqual(installer.initialize(self.arguments()), 0)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "No installed LLM Reflect"):
            installer.initialize(self.arguments(["cursor"]))
        self.assertEqual(self.snapshot(), before)

    def test_marketplace_updates_use_native_commands_and_keep_existing_methods(self):
        installer.atomic_write(installer.registry_path(), installer.encoded({"providers": {
            "codex": {"method": "marketplace"}, "claude": {"method": "marketplace"}}}))
        self.upstream()
        config = self.home / ".codex/config.toml"
        config.parent.mkdir(exist_ok=True)
        config.write_text('[marketplaces.codex-reflect-marketplace]\nsource_type = "git"\n'
                          'source = "https://example.invalid/reflect.git"\n'
                          '[plugins."codex-reflect@codex-reflect-marketplace"]\nenabled = false\n', encoding="utf-8")
        upstream = self.native.read_bytes()
        settings = config.read_bytes()
        with patch.object(installer.shutil, "which", return_value="provider"), \
             patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(installer.initialize(self.arguments()), 0)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["codex", "plugin", "marketplace", "upgrade", "codex-reflect-marketplace"],
            ["claude", "plugin", "marketplace", "update", "reflect-marketplace"],
            ["claude", "plugin", "update", "reflect@reflect-marketplace", "--scope", "user"]])
        self.assertEqual(self.native.read_bytes(), upstream)
        self.assertEqual(config.read_bytes(), settings)
        self.assertEqual(installer.read_json(installer.registry_path())["providers"], {
            "codex": {"method": "marketplace"}, "claude": {"method": "marketplace"}})

    def test_codex_local_marketplace_reinstalls_without_git_upgrade(self):
        self.assertEqual(installer.marketplace_commands("codex", False, True, updating=True), [
            ["codex", "plugin", "add", "codex-reflect@codex-reflect-marketplace"]])
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('[plugins."codex-reflect@codex-reflect-marketplace"]\nenabled = false\n', encoding="utf-8")
        self.assertEqual(installer.marketplace_commands("codex", False, True, updating=True), [])

    def test_failed_marketplace_update_leaves_local_integrations_and_registry_intact(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        data = installer.read_json(installer.registry_path())
        data["providers"]["claude"] = {"method": "marketplace"}
        installer.atomic_write(installer.registry_path(), installer.encoded(data))
        before = self.snapshot()
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, "marketplace command failed"):
                installer.initialize(self.arguments())
        self.assertEqual(self.snapshot(), before)

    def test_edited_managed_skill_stops_updates_without_overwriting(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        skill = providers.skill_home("gemini") / "reflect/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nUser edits.\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "locally edited"):
            installer.initialize(self.arguments())
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_is_read_only_and_shows_marketplace_refreshes(self):
        installer.atomic_write(installer.registry_path(), installer.encoded({"providers": {
            "claude": {"method": "marketplace"}}}))
        before = self.snapshot()
        output = io.StringIO()
        with redirect_stdout(output), patch.object(installer.subprocess, "run") as run:
            self.assertEqual(installer.initialize(self.arguments(dry_run=True)), 0)
        run.assert_not_called()
        self.assertIn("marketplace update reflect-marketplace", output.getvalue())
        self.assertEqual(self.snapshot(), before)

    def test_update_key_ignores_new_checked_rows_and_keeps_unchecked_installs(self):
        installs = {"gemini": {"method": "local"}, "cursor": {"method": "local"},
                    "claude": {"method": "upstream"}}
        # Check a new Codex install; uncheck Cursor; update Gemini only.
        screen = tui_fixtures.FakeTerminal(["toggle", "down", "down", "toggle", "u", "y"])
        with patch.object(tui, "Terminal", return_value=screen):
            result = tui.choose_providers(installer.provider_rows(installs), installs)
        self.assertEqual(result, (["gemini"], []))
        self.assertTrue(result.updating)
        self.assertIn("u: update checked", screen.frames[0])
        self.assertIn("Update: gemini", screen.frames[-1])
        self.assertIn("Remove: none", screen.frames[-1])

    def test_update_review_can_cancel_without_running_updater(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        screen = tui_fixtures.FakeTerminal(["u", "enter", "escape"])
        before = self.snapshot()
        with patch.object(tui, "Terminal", return_value=screen), patch.object(updater, "run_update") as update:
            self.assertEqual(installer.initialize(argparse.Namespace(selected=None, method="auto",
                            remove=False, dry_run=False, list=False)), 0)
        update.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_update_key_closes_tui_and_calls_checkout_update(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        screen = tui_fixtures.FakeTerminal(["u", "y"])
        with patch.object(tui, "Terminal", return_value=screen), patch.object(updater, "run_update", return_value=0) as update:
            self.assertEqual(installer.initialize(argparse.Namespace(selected=None, method="auto",
                            remove=False, dry_run=False, list=False, no_pull=True)), 0)
        update.assert_called_once_with(["gemini"], False, True)

    def test_update_key_preserves_coinstalled_upstream_and_has_no_uninstall_action(self):
        installs = {"claude": {"method": "marketplace", "upstream": [{
            "plugin": "claude-reflect@upstream", "scope": "user"}]}}
        screen = tui_fixtures.FakeTerminal(["u", "y"])
        with patch.object(tui, "Terminal", return_value=screen):
            result = tui.choose_providers(installer.provider_rows(installs), installs)
        self.assertEqual(result, (["claude"], []))
        self.assertIn("Remove: none", screen.frames[-1])
        self.assertNotIn("claude-reflect@upstream", screen.frames[-1])

    def test_uninstall_picker_does_not_treat_u_as_update(self):
        installs = {"gemini": {"method": "local"}}
        screen = tui_fixtures.FakeTerminal(["u", "toggle", "enter", "y"])
        with patch.object(tui, "Terminal", return_value=screen):
            result = tui.choose_providers(installer.provider_rows(installs, True), installs, removing=True)
        self.assertEqual(result, ([], ["gemini"]))
        self.assertFalse(result.updating)
        self.assertNotIn("u: update", screen.frames[0])

    def test_update_preserves_prefix_and_flags_in_fresh_bootstrap(self):
        package = self.home / "custom prefix/share/reflect"
        package.mkdir(parents=True)
        (package / "source.json").write_bytes(installer.encoded({"checkout": str(ROOT)}))
        with patch.object(updater, "PACKAGE", package), \
             patch.object(updater, "source_root", return_value=ROOT), \
             patch.object(updater.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(updater.run_update(["gemini"], True, True), 0)
        self.assertEqual(run.call_args.args[0], [sys.executable, str(ROOT / "tools/install.py"),
                         "--update", "--prefix", str(package.parent.parent), "--provider", "gemini",
                         "--dry-run", "--no-pull"])

    def test_bootstrap_dry_run_binds_skills_to_destination_without_writes(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        prefix = self.home / "new prefix"
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(ROOT / "tools/install.py"), "-u", "--no-pull",
                                 "--dry-run", "--prefix", str(prefix)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would update:", result.stdout)
        self.assertIn(str(self.home / ".gemini/skills/reflect/SKILL.md"), result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_bootstrap_preview_uses_new_checkout_instead_of_old_source_metadata(self):
        package = self.home / "prefix/share/reflect"
        package.mkdir(parents=True)
        (package / "source.json").write_bytes(installer.encoded({"checkout": str(self.home / "old missing checkout")}))
        with patch.object(installer, "PACKAGE", package), patch.object(installer, "TEMPLATES", fixtures.PACKAGE):
            self.assertEqual(installer.source_root(), ROOT)

    def test_uninstalled_provider_fails_before_runtime_copy(self):
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(ROOT / "tools/install.py"), "-u", "--no-pull",
                                 "--provider", "cursor", "--prefix", str(self.home / "prefix")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No installed LLM Reflect", result.stderr)
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name != "nt" and shutil.which("sh"), "POSIX shell wrapper requires sh")
    def test_shell_installer_forwards_update_and_dry_run_flags(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        before = self.snapshot()
        result = subprocess.run(["sh", str(ROOT / "install.sh"), "-u", "--no-pull", "--dry-run",
                                 "--prefix", str(self.home / "prefix")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would update:", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_partial_update_preserves_unchecked_provider_and_its_edits(self):
        self.assertEqual(self.init("--provider", "gemini", "--provider", "cursor").returncode, 0)
        cursor = providers.skill_home("cursor") / "reflect/SKILL.md"
        cursor.write_text(cursor.read_text(encoding="utf-8") + "\nUser edits.\n", encoding="utf-8")
        before = cursor.read_bytes()
        registry = installer.read_json(installer.registry_path())["providers"]["cursor"]
        prefix = self.home / "prefix"
        result = subprocess.run([sys.executable, str(ROOT / "tools/install.py"), "-u", "--no-pull",
                                 "--provider", "gemini", "--prefix", str(prefix)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(cursor.read_bytes(), before)
        self.assertEqual(installer.read_json(installer.registry_path())["providers"]["cursor"], registry)
        self.assertIn((prefix / "share/reflect/scripts/reflect.py").as_posix(),
                      (providers.skill_home("gemini") / "reflect/SKILL.md").read_text(encoding="utf-8"))

    def test_update_migrates_owned_codex_bootstrap_metadata_to_shared_runtime(self):
        prefix = self.home / "old custom prefix"
        bootstrap = [sys.executable, str(ROOT / "tools/install.py"), "--prefix", str(prefix), "--no-configure"]
        result = subprocess.run(bootstrap, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        package = prefix / "share/reflect"
        script = package / "scripts/reflect.py"
        result = self.cli("gemini", "init", "--provider", "gemini", script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.cli("gemini", "capture", text="remember: preserve this queued correction", script=script)
        queue = self.cli("gemini", "queue", script=script).stdout
        settings = (self.home / ".gemini/settings.json").read_bytes()
        manifest_path = package / "install-manifest.json"
        manifest = installer.read_json(manifest_path)
        # Older standalone installs copied Codex's plugin-only files and recorded
        # only the checkout. The new bundle must remove those owned files safely.
        old_files = {package / ".codex-plugin/plugin.json": b'{"name":"codex-reflect"}\n',
                     package / "hooks/hooks.json": b'{"hooks":{}}\n',
                     package / "source.json": installer.encoded({"checkout": str(ROOT)})}
        for path, content in old_files.items():
            installer.atomic_write(path, content)
            manifest["files"][str(path)] = installer.digest(content)
        installer.atomic_write(manifest_path, installer.encoded(manifest))
        result = self.cli("gemini", "init", "-u", "--no-pull", script=script)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse((package / ".codex-plugin/plugin.json").exists())
        self.assertFalse((package / "hooks/hooks.json").exists())
        self.assertTrue((package / "runtime.json").is_file())
        self.assertEqual(installer.read_json(package / "source.json")["prefix"], str(prefix))
        self.assertEqual(self.cli("gemini", "queue", script=script).stdout, queue)
        self.assertEqual((self.home / ".gemini/settings.json").read_bytes(), settings)
        self.assertEqual(installer.read_json(installer.registry_path())["providers"]["gemini"]["method"], "local")


@unittest.skipUnless(shutil.which("git"), "Git is required for checkout update fixtures")
class CheckoutUpdateTests(unittest.TestCase):
    cli = fixtures.ProviderTests.cli
    snapshot = fixtures.ProviderTests.snapshot
    setUp = UpdateTests.setUp
    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", check=True).stdout.strip()

    def commit(self, root):
        self.git(root, "add", ".")
        self.git(root, "-c", "user.name=Reflect Test Fixture", "-c", "user.email=reflect-test@example.invalid",
                 "commit", "-qm", "Update fixture")

    def checkout(self, full=False):
        upstream = self.home / "upstream"
        if full:
            shutil.copytree(ROOT, upstream, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv"))
        else:
            upstream.mkdir()
            (upstream / "content").write_text("old\n", encoding="utf-8")
        self.git(upstream, "init", "-b", "wallentx/update-fixture")
        self.commit(upstream)
        checkout = self.home / "checkout with spaces"
        self.git(self.home, "clone", str(upstream), str(checkout))
        return upstream, checkout

    def test_git_dry_run_does_not_fetch_or_change_checkout(self):
        upstream, checkout = self.checkout()
        (upstream / "content").write_text("new\n", encoding="utf-8")
        self.commit(upstream)
        before = self.snapshot()
        updater.refresh_checkout(checkout, dry_run=True)
        self.assertEqual(self.snapshot(), before)

    def test_git_update_fast_forwards_same_branch(self):
        upstream, checkout = self.checkout()
        (upstream / "content").write_text("new\n", encoding="utf-8")
        self.commit(upstream)
        updater.refresh_checkout(checkout)
        self.assertEqual((checkout / "content").read_text(encoding="utf-8"), "new\n")
        self.assertEqual(self.git(checkout, "branch", "--show-current"), "wallentx/update-fixture")

    def test_dirty_detached_and_untracked_branches_stop_before_pull(self):
        _, checkout = self.checkout()
        (checkout / "content").write_text("local edit\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "local edits"):
            updater.refresh_checkout(checkout)
        self.assertEqual((checkout / "content").read_text(encoding="utf-8"), "local edit\n")
        (checkout / "content").write_text("old\n", encoding="utf-8")
        self.git(checkout, "checkout", "--detach")
        with self.assertRaises(ValueError):
            updater.refresh_checkout(checkout)
        self.git(checkout, "checkout", "wallentx/update-fixture")
        self.git(checkout, "branch", "--unset-upstream")
        with self.assertRaises(ValueError):
            updater.refresh_checkout(checkout)

    def test_diverged_branch_is_not_reset_or_rebased(self):
        upstream, checkout = self.checkout()
        (checkout / "content").write_text("local commit\n", encoding="utf-8")
        self.commit(checkout)
        local_head = self.git(checkout, "rev-parse", "HEAD")
        (upstream / "content").write_text("remote commit\n", encoding="utf-8")
        self.commit(upstream)
        with self.assertRaisesRegex(RuntimeError, "Checkout update failed"):
            updater.refresh_checkout(checkout)
        self.assertEqual(self.git(checkout, "rev-parse", "HEAD"), local_head)
        self.assertEqual((checkout / "content").read_text(encoding="utf-8"), "local commit\n")

    def test_bootstrap_pulls_reloads_installed_runtime_and_refreshes_all_local_providers(self):
        upstream, checkout = self.checkout(full=True)
        prefix = self.home / "custom prefix"
        def install(*args):
            result = subprocess.run([sys.executable, str(checkout / "tools/install.py"), "--prefix", str(prefix), *args],
                                    capture_output=True, text=True, encoding="utf-8", cwd=str(self.project))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result
        install("--no-configure")
        script = prefix / "share/reflect/scripts/reflect.py"
        names = list(providers.PROVIDERS)
        options = [arg for name in names for arg in ("--provider", name)]
        result = self.cli("codex", "init", "--method", "local", *options, script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        queues = {}
        for name in names:
            self.cli(name, "capture", "--project", str(self.project), text="remember: keep this correction", script=script)
            queues[name] = self.cli(name, "queue", script=script).stdout
        memories = self.home / ".codex/memories/MEMORY.md"
        memories.parent.mkdir(parents=True)
        memories.write_text("Keep existing native memory\n", encoding="utf-8")
        settings = self.home / ".gemini/settings.json"
        data = installer.read_json(settings)
        data["unrelated"] = {"keep": True}
        settings.write_bytes(installer.encoded(data))
        skill = upstream / "reflect/skills/reflect/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nUpdated fixture skill.\n", encoding="utf-8")
        runtime = upstream / "reflect/scripts/updater.py"
        runtime.write_text(runtime.read_text(encoding="utf-8") + "\n# Updated fixture runtime.\n", encoding="utf-8")
        subprocess.run([sys.executable, str(upstream / "tools/build_packages.py")], check=True, capture_output=True)
        self.commit(upstream)
        # Invoke the old installed runtime, which must preserve the custom prefix
        # and use the newly pulled installer in a fresh process.
        result = self.cli("codex", "init", "-u", script=script)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Updated fixture runtime", (script.parent / "updater.py").read_text(encoding="utf-8"))
        for name in names:
            self.assertIn("Updated fixture skill", (providers.skill_home(name) / "reflect/SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(self.cli(name, "queue", script=script).stdout, queues[name])
        self.assertEqual(memories.read_text(encoding="utf-8"), "Keep existing native memory\n")
        self.assertEqual(installer.read_json(settings)["unrelated"], {"keep": True})
        self.assertTrue(all(entry["method"] == "local" for entry in installer.read_json(installer.registry_path())["providers"].values()))
        self.assertFalse((self.home / ".local/bin/reflect").exists())
        self.assertEqual(self.git(checkout, "branch", "--show-current"), "wallentx/update-fixture")
