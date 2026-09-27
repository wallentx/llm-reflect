"""Keyboard and provider-state tests, including an actual POSIX pseudo-terminal."""
from pathlib import Path
import io
import os
import re
import select
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from tests import test_providers as fixtures
import installer
import tui


class FakeTerminal:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.frames = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def size(self):
        return os.terminal_size((100, 24))

    def draw(self, lines):
        self.frames.append("\n".join(lines))

    def key(self):
        return next(self.keys)


class RenderingTests(unittest.TestCase):
    def test_color_preserves_clipping_sanitization_and_plain_text(self):
        line = tui.Line(("title", "LLM "), ("selected", "Reflect\x1b[2Jlong"))
        frames = []
        for no_color in ("", "1"):
            with patch.dict(os.environ, {"NO_COLOR": no_color}):
                terminal = tui.Terminal()
            terminal.output = io.StringIO()
            terminal.size = lambda: os.terminal_size((14, 3))
            terminal.draw([line])
            frames.append(terminal.output.getvalue())
            terminal.restore()
            if not no_color:
                self.assertTrue(terminal.output.getvalue().endswith(tui.RESET + "\x1b[?25h\x1b[?1049l"))
        plain = "\x1b[H\x1b[2JLLM Reflect ["
        self.assertIn(tui.STYLES["title"], frames[0])
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", frames[0]), plain)
        self.assertEqual(frames[1], plain)


class PickerTests(unittest.TestCase):
    cli = fixtures.ProviderTests.cli
    init = fixtures.ProviderTests.init
    snapshot = fixtures.ProviderTests.snapshot

    def setUp(self):
        fixtures.ProviderTests.setUp(self)

    def interactive(self, keys, remove=False, dry_run=False, method="auto"):
        from types import SimpleNamespace
        screen = FakeTerminal(keys)
        args = SimpleNamespace(selected=None, method=method, remove=remove, dry_run=dry_run, list=False)
        with patch.object(tui, "Terminal", return_value=screen):
            result = installer.initialize(args)
        return result, screen

    def test_checkbox_selection_installs_and_removes_in_one_reconciliation(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        self.cli("gemini", "capture", "--project", str(self.project), text="remember: test release changes")
        queue = self.cli("gemini", "queue").stdout
        result, screen = self.interactive(["down", "down", "toggle", "down", "toggle", "enter", "y"])
        self.assertEqual(result, 0)
        self.assertIn("[x] Gemini CLI", screen.frames[0])
        self.assertTrue((self.home / ".cursor/skills/reflect/SKILL.md").exists())
        self.assertFalse((self.home / ".gemini/skills/reflect/SKILL.md").exists())
        self.assertEqual(self.cli("gemini", "queue").stdout, queue)

    def test_cancel_and_default_cancel_on_review_are_noops(self):
        for keys in (["escape"], ["toggle", "enter", "enter", "escape"], ["cancel"], ["toggle", "enter", "cancel"]):
            with self.subTest(keys=keys):
                before = self.snapshot()
                self.assertEqual(self.interactive(keys)[0], 0)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(installer.registry_path().parent.exists())

    def test_uninstall_picker_only_removes_checked_integration(self):
        self.assertEqual(self.init("--provider", "cursor", "--provider", "gemini").returncode, 0)
        result, screen = self.interactive(["toggle", "enter", "y"], remove=True)
        self.assertEqual(result, 0)
        self.assertIn("[ ] Cursor", screen.frames[0])
        self.assertNotIn("Claude Code", screen.frames[0])
        self.assertFalse((self.home / ".cursor/skills/reflect/SKILL.md").exists())
        self.assertTrue((self.home / ".gemini/skills/reflect/SKILL.md").exists())

    def test_interactive_dry_run_does_not_write_or_confirm(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        before = self.snapshot()
        result, screen = self.interactive(["down", "down", "toggle", "down", "toggle", "enter"], dry_run=True)
        self.assertEqual(result, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any("Review changes" in frame for frame in screen.frames))

    def test_edited_removal_target_prevents_partial_install(self):
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        skill = self.home / ".gemini/skills/reflect/SKILL.md"
        skill.write_text(skill.read_text() + "\nUser addition", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "locally edited"):
            self.interactive(["down", "down", "toggle", "down", "toggle", "enter", "y"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.home / ".cursor/skills").exists())

    def test_native_marketplace_install_is_preselected_and_can_be_removed(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('[plugins."codex-reflect@codex-reflect-marketplace"]\nenabled = true\n')
        with patch.object(installer.shutil, "which", return_value="codex"), \
             patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            result, screen = self.interactive(["toggle", "enter", "y"])
        self.assertEqual(result, 0)
        self.assertIn("[x] Codex", screen.frames[0])
        run.assert_called_once_with(["codex", "plugin", "remove", "codex-reflect@codex-reflect-marketplace"], check=False)

    def upstream(self, plugins=None):
        self.native = self.home / ".claude/plugins/installed_plugins.json"
        self.native.parent.mkdir(parents=True, exist_ok=True)
        self.upstream_id = "claude-reflect@claude-reflect-marketplace"
        self.native.write_bytes(installer.encoded({"version": 2, "plugins": plugins if plugins is not None else {
            self.upstream_id: [{"scope": "user", "version": "3.2.0"}],
            "unrelated@another-marketplace": [{"scope": "user"}]}}))
        self.upstream_data = self.home / ".claude/plugins/data/upstream/queue.jsonl"
        self.upstream_data.parent.mkdir(parents=True, exist_ok=True)
        self.upstream_data.write_bytes(b"upstream queue stays here\n")

    def claude_run(self, command, **kwargs):
        if command[2:4] == ["marketplace", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        data = installer.read_json(self.native)
        if command[2] == "install":
            data["plugins"][command[3]] = [{"scope": "user", "version": "llm-reflect"}]
        elif command[2] == "uninstall":
            scope = command[command.index("--scope") + 1]
            if command[3].split("@")[0] == "claude-reflect":
                self.assertIn("--keep-data", command)
            entries = [entry for entry in data["plugins"].get(command[3], []) if entry.get("scope", "user") != scope]
            if entries:
                data["plugins"][command[3]] = entries
            else:
                data["plugins"].pop(command[3], None)
        self.native.write_bytes(installer.encoded(data))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def test_upstream_is_marked_preselected_and_untouched_selection_is_noop(self):
        self.upstream()
        before = self.snapshot()
        result, screen = self.interactive(["enter"])
        self.assertEqual(result, 0)
        self.assertIn("[x] Claude Code", screen.frames[0])
        self.assertIn(tui.UPSTREAM_MARKER, screen.frames[0])
        self.assertIn("Upstream claude-reflect. Uncheck: remove; recheck: LLM Reflect.", screen.frames[0])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(installer.registry_path().parent.exists())

    def test_unchecking_upstream_removes_exact_plugin_and_retains_data(self):
        self.upstream()
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", side_effect=self.claude_run) as run:
            result, screen = self.interactive(["down", "toggle", "enter", "y"])
        self.assertEqual(result, 0)
        run.assert_called_once_with(["claude", "plugin", "uninstall", self.upstream_id,
                                     "--scope", "user", "--keep-data"], check=False)
        self.assertNotIn(self.upstream_id, installer.read_json(self.native)["plugins"])
        self.assertIn("unrelated@another-marketplace", installer.read_json(self.native)["plugins"])
        self.assertEqual(self.upstream_data.read_bytes(), b"upstream queue stays here\n")
        self.assertIn("Remove: " + self.upstream_id + " (user)", screen.frames[-1])
        self.assertIn("Install/update: none", screen.frames[-1])

    def test_rechecking_upstream_installs_llm_reflect_before_native_uninstall(self):
        self.upstream()
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", side_effect=self.claude_run) as run:
            result, screen = self.interactive(["down", "toggle", "toggle", "enter", "y"])
        self.assertEqual(result, 0)
        commands = [call.args[0] for call in run.call_args_list]
        install = ["claude", "plugin", "install", "reflect@reflect-marketplace", "--scope", "user"]
        remove = ["claude", "plugin", "uninstall", self.upstream_id, "--scope", "user", "--keep-data"]
        self.assertLess(commands.index(install), commands.index(remove))
        self.assertIn("Install/update: claude (LLM Reflect)", screen.frames[-1])
        self.assertIn("Remove: " + self.upstream_id + " (user)", screen.frames[-1])
        self.assertIn("queues are not migrated", screen.frames[-1])
        self.assertIn("replace with LLM Reflect", screen.frames[-2])
        plugins = installer.read_json(self.native)["plugins"]
        self.assertIn("reflect@reflect-marketplace", plugins)
        self.assertNotIn(self.upstream_id, plugins)
        self.assertEqual(installer.read_json(installer.registry_path())["providers"]["claude"]["method"], "marketplace")
        self.assertEqual(self.upstream_data.read_bytes(), b"upstream queue stays here\n")

    def test_upstream_can_be_replaced_with_local_skills_and_hooks(self):
        self.upstream()
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", side_effect=self.claude_run) as run:
            result, _ = self.interactive(["down", "toggle", "toggle", "enter", "y"], method="local")
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 1)
        self.assertTrue((self.home / ".claude/skills/reflect/SKILL.md").exists())
        self.assertNotIn(self.upstream_id, installer.read_json(self.native)["plugins"])

    def test_cancel_after_reselecting_upstream_does_not_change_any_files(self):
        self.upstream()
        before = self.snapshot()
        with patch.object(installer.subprocess, "run") as run:
            self.assertEqual(self.interactive(["down", "toggle", "toggle", "enter", "cancel"])[0], 0)
        run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_upstream_uninstall_picker_and_dry_run(self):
        self.upstream()
        before = self.snapshot()
        with patch.object(installer.subprocess, "run") as run:
            self.assertEqual(self.interactive(["toggle", "enter"], remove=True, dry_run=True)[0], 0)
        run.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", side_effect=self.claude_run) as run:
            result, screen = self.interactive(["toggle", "enter", "y"], remove=True)
        self.assertEqual(result, 0)
        self.assertIn("Check to uninstall; data stays.", screen.frames[0])
        self.assertEqual(run.call_count, 1)
        self.assertNotIn(self.upstream_id, installer.read_json(self.native)["plugins"])

    def test_upstream_native_install_failure_keeps_original_plugin_and_registry(self):
        self.upstream()
        before = self.snapshot()
        def fail_install(command, **kwargs):
            if command[2] == "install":
                return subprocess.CompletedProcess(command, 1)
            return self.claude_run(command, **kwargs)
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", side_effect=fail_install) as run:
            with self.assertRaisesRegex(RuntimeError, "marketplace command failed"):
                self.interactive(["down", "toggle", "toggle", "enter", "y"])
        self.assertFalse(any(call.args[0][2] == "uninstall" for call in run.call_args_list))
        self.assertEqual(self.snapshot(), before)

    def test_upstream_local_conflict_is_validated_before_uninstall(self):
        self.upstream()
        skill = self.home / ".claude/skills/reflect/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Unmanaged skill\n")
        before = self.snapshot()
        with patch.object(installer.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Unmanaged"):
                self.interactive(["down", "toggle", "toggle", "enter", "y"], method="local")
        run.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_unchecking_coexisting_upstream_and_llm_reflect_removes_both_plugins(self):
        self.upstream()
        data = installer.read_json(self.native)
        data["plugins"]["reflect@reflect-marketplace"] = [{"scope": "user"}]
        self.native.write_bytes(installer.encoded(data))
        with patch.object(installer.shutil, "which", return_value="claude"), \
             patch.object(installer.subprocess, "run", side_effect=self.claude_run) as run:
            result, screen = self.interactive(["down", "toggle", "enter", "y"])
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        self.assertIn("claude (LLM Reflect)", screen.frames[-1])
        self.assertEqual(list(installer.read_json(self.native)["plugins"]), ["unrelated@another-marketplace"])

    def test_changed_upstream_registration_during_picker_prevents_cli_mutations(self):
        self.upstream()
        def change_registration(*args, **kwargs):
            data = installer.read_json(self.native)
            data["plugins"][self.upstream_id][0]["version"] = "new version"
            self.native.write_bytes(installer.encoded(data))
            return ["claude"], []
        with patch.object(tui, "choose_providers", side_effect=change_registration), \
             patch.object(installer.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "changed while selecting"):
                self.interactive([])
        run.assert_not_called()
        self.assertFalse(installer.registry_path().exists())

    def test_upstream_scopes_are_exact_and_other_projects_are_left_alone(self):
        plugin = "claude-reflect@custom-marketplace"
        foreign = self.home / "another project"
        foreign.mkdir()
        entries = [{"scope": "user"}, {"scope": "project", "projectPath": str(self.project)},
                   {"scope": "local", "projectPath": str(self.project)},
                   {"scope": "project", "projectPath": str(foreign)}]
        self.upstream({plugin: entries})
        with patch.object(installer.Path, "cwd", return_value=self.project):
            _, installed = installer.installation_state()
        commands = installer.upstream_uninstall_commands(installed["claude"]["upstream"])
        self.assertEqual([command[5] for command in commands], ["user", "project", "local"])
        self.assertTrue(all(command[3] == plugin for command in commands))
        self.upstream({plugin: [entries[-1]]})
        with patch.object(installer.Path, "cwd", return_value=self.project):
            self.assertNotIn("claude", installer.installation_state()[1])

    def test_scripted_upstream_migration_dry_run_prints_both_plugins_without_writing(self):
        self.upstream()
        before = self.snapshot()
        result = self.init("--provider", "claude", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plugin install reflect@reflect-marketplace --scope user", result.stdout)
        self.assertIn("plugin uninstall " + self.upstream_id + " --scope user --keep-data", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_wrapped_upstream_legend_stays_within_picker_height(self):
        self.upstream()
        _, installed = installer.installation_state()
        screen = FakeTerminal(["enter"])
        screen.size = lambda: os.terminal_size((32, 14))
        tui.checklist(screen, installer.provider_rows(installed), installed)
        self.assertLessEqual(len(screen.frames[0].splitlines()), 14)
        self.assertIn("Upstream claude-reflect.", screen.frames[0])

    def test_nonterminal_setup_requires_explicit_provider_and_does_not_write(self):
        before = self.snapshot()
        result = self.init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--provider NAME", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(installer.registry_path().parent.exists())

    def test_uninstall_command_and_old_remove_flag_remain_scriptable(self):
        for action in (("uninstall", "--provider", "gemini"), ("init", "--remove", "--provider", "gemini")):
            self.assertEqual(self.init("--provider", "gemini").returncode, 0)
            result = self.cli("codex", *action)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.home / ".gemini/skills/reflect/SKILL.md").exists())

    def test_narrow_review_screen_does_not_apply_hidden_changes(self):
        screen = FakeTerminal(["y", "cancel"])
        screen.size = lambda: os.terminal_size((20, 8))
        result = tui.confirm(screen, list(installer.providers.PROVIDERS), [])
        self.assertIsNone(result)
        self.assertIn("Resize", screen.frames[0])

    @unittest.skipIf(os.name == "nt", "POSIX pseudo-terminals are unavailable on Windows")
    def test_real_terminal_arrow_space_confirmation_and_terminal_restoration(self):
        self.run_terminal(cancel=False)

    @unittest.skipIf(os.name == "nt", "POSIX pseudo-terminals are unavailable on Windows")
    def test_real_terminal_ctrl_c_restores_and_cancels_without_writing(self):
        self.run_terminal(cancel=True)

    @unittest.skipIf(os.name == "nt", "POSIX pseudo-terminals are unavailable on Windows")
    def test_checkout_installer_opens_picker_after_copying_standalone_runtime(self):
        self.run_terminal(cancel=True, bootstrap=True)

    @unittest.skipIf(os.name == "nt", "POSIX pseudo-terminals are unavailable on Windows")
    def test_real_terminal_shows_orange_upstream_marker_without_changing_install(self):
        self.run_terminal(cancel=True, upstream=True)

    def run_terminal(self, cancel, bootstrap=False, upstream=False):
        import pty
        import termios
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        attributes = termios.tcgetattr(slave)
        if upstream:
            self.upstream()
        before = self.snapshot()
        prefix = self.home / "prefix"
        command = (["sh", str(fixtures.ROOT / "install.sh"), "--prefix", str(prefix)] if bootstrap else
                   [sys.executable, str(fixtures.PACKAGE / "scripts/reflect.py"), "init", "--method", "local"])
        process = subprocess.Popen(command,
                                   stdin=slave, stderr=slave, stdout=subprocess.PIPE, cwd=str(self.project),
                                   env=dict(os.environ, TERM="xterm-256color", NO_COLOR=""))
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.addCleanup(cleanup)
        output = bytearray()
        def read_until(marker):
            deadline = time.monotonic() + 8
            while marker not in output:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail("TUI marker not received: " + repr(marker) + "; " + repr(bytes(output)[-800:]))
                if process.poll() is not None:
                    self.fail("TUI exited before " + repr(marker) + "; " + repr(bytes(output)[-800:]))
                if select.select([master], [], [], min(remaining, 0.25))[0]:
                    output.extend(os.read(master, 65536))
        read_until(b"Enter: review")
        self.assertIn(tui.STYLES["title"].encode("ascii"), output)
        if upstream:
            read_until((tui.UPSTREAM_MARKER + " Upstream claude-reflect.").encode("utf-8"))
            self.assertIn((tui.STYLES["upstream"] + tui.UPSTREAM_MARKER).encode("utf-8"), output)
        if cancel:
            os.write(master, b"\x03")
        else:
            os.write(master, b"\x1b[B\x1b[B \r")
            read_until(b"Review changes")
            os.write(master, b"y")
        stdout, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, bytes(output))
        restored = termios.tcgetattr(slave)
        if sys.platform == "darwin":
            # macOS sets this transient input-state bit when ICANON is restored.
            # All settings, including echo, signals and control characters, must match.
            restored[3] &= ~termios.PENDIN
            attributes[3] &= ~termios.PENDIN
        self.assertEqual(restored, attributes)
        if cancel:
            self.assertIn(b"Cancelled", stdout)
            if bootstrap:
                self.assertTrue((prefix / "bin/reflect").is_file())
                self.assertFalse(installer.registry_path().exists())
            else:
                self.assertEqual(self.snapshot(), before)
        else:
            self.assertIn(b"Provider setup complete", stdout)
            self.assertTrue((self.home / ".cursor/skills/reflect/SKILL.md").exists())
            self.assertFalse((self.home / ".codex/skills/reflect/SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
