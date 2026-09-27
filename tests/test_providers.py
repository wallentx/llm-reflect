"""Provider fixtures and installer isolation. Never use the operator's configuration."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins/codex-reflect"
sys.path.insert(0, str(PACKAGE / "scripts"))
import installer
import providers
import reflect_core as core


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reflect-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.project = self.home / "project one"
        self.project.mkdir()
        values = {"HOME": str(self.home), "USERPROFILE": str(self.home),
                  "CODEX_HOME": str(self.home / ".codex"), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                  "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_STATE_HOME": str(self.home / ".local/state"),
                  "CODEX_REFLECT_HOME": str(self.home / ".codex/reflect"), "REFLECT_PROVIDER": "codex",
                  "REFLECT_HOME": str(self.home / "state"), "REFLECT_DISABLED": "0", "REFLECT_REMINDER": "true"}
        self.env = patch.dict(os.environ, values)
        self.env.start()
        self.addCleanup(self.env.stop)
        providers.select("codex")
        self.addCleanup(providers.select, "codex")

    def cli(self, provider, *args, data=None, text=None, script=None):
        return subprocess.run([sys.executable, str(script or PACKAGE / "scripts/reflect.py"),
                               "--provider", provider, *args],
                              input=json.dumps(data) if data is not None else text,
                              text=True, capture_output=True, cwd=str(self.project))

    def init(self, *args):
        return self.cli("codex", "init", *args)

    def snapshot(self):
        return {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}

    def test_install_and_marketplace_runtimes_reject_python_below_311_without_writes(self):
        scripts = (ROOT / "tools/install.py", PACKAGE / "scripts/reflect.py",
                   ROOT / "plugins/claude-reflect/scripts/reflect.py")
        runner = ("import runpy, sys; "
                  "sys.version_info = tuple(map(int, sys.argv[1].split('.'))); "
                  "sys.argv = [sys.argv[2]]; "
                  "runpy.run_path(sys.argv[0], run_name='__main__')")
        before = self.snapshot()
        for version in ("3.8.0", "3.10.0"):
            for script in scripts:
                with self.subTest(version=version, script=script):
                    result = subprocess.run([sys.executable, "-c", runner, version, str(script)],
                                            cwd=str(self.project), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Python 3.11 or newer", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(self.snapshot(), before)

    def test_all_native_capture_payloads_and_queue_isolation(self):
        events = {"codex": "UserPromptSubmit", "claude": "UserPromptSubmit", "gemini": "BeforeAgent",
                  "cursor": "beforeSubmitPrompt", "copilot": "userPromptSubmitted", "opencode": "UserPromptSubmit"}
        for name, event in events.items():
            with self.subTest(name=name):
                payload = {"cwd": str(self.project), "prompt": "remember: use Python for scripts",
                           "session_id": "s", "turn_id": "t"}
                if name == "cursor":
                    payload.pop("cwd")
                    payload.update(workspace_roots=[str(self.project)], conversation_id="s", generation_id="t")
                if name == "copilot":
                    payload.update(sessionId="s", timestamp=1234)
                result = self.cli(name, "hook", event, data=payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("use Python", result.stdout)
                self.cli(name, "hook", event, data=payload)
                queue = json.loads(self.cli(name, "queue", "--project", str(self.project)).stdout)
                self.assertEqual(len(queue), 1)
                self.assertEqual(queue[0]["provider"], name)
        self.assertEqual(json.loads(self.cli("antigravity", "queue").stdout), [])

    def test_cursor_ambiguous_project_and_synthetic_payloads_are_skipped(self):
        for data in ({"workspace_roots": [str(self.project), str(self.home)], "prompt": "remember: use Python"},
                     {"cwd": "relative", "prompt": "remember: use Python"}, [], {"prompt": {}}):
            result = self.cli("cursor", "hook", "beforeSubmitPrompt", data=data)
            self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.cli("cursor", "queue").stdout), [])

    def test_manual_capture_and_semantic_provider_boundary(self):
        result = self.cli("antigravity", "capture", "--project", str(self.project), text="remember: run focused tests")
        self.assertTrue(json.loads(result.stdout)["queued"])
        for name in providers.PROVIDERS:
            if name == "codex":
                continue
            result = self.cli(name, "queue", "--semantic")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Codex-only", result.stderr)

    def test_history_import_filters_provider_project_date_and_role(self):
        history = self.home / "export.jsonl"
        date = datetime.now(timezone.utc).isoformat()
        base = {"provider": "cursor", "project": str(self.project), "session_id": "session",
                "id": "one", "timestamp": date, "role": "user", "text": "remember: use Python"}
        rows = [base, dict(base, id="two", role="assistant"), dict(base, id="three", project=str(self.home)),
                dict(base, id="four", timestamp="2001-01-01T00:00:00Z")]
        history.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
        result = self.cli("cursor", "scan", "--history", str(history))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 1)
        self.assertEqual(len(json.loads(self.cli("cursor", "scan", "--history", str(history), "--all-projects").stdout)), 2)
        self.assertNotEqual(self.cli("gemini", "scan", "--history", str(history)).returncode, 0)
        self.assertIn("--history FILE", self.cli("cursor", "scan").stderr)
        self.assertFalse((self.home / "state").exists())

    def test_claude_native_history_ignores_assistant_and_sidechains(self):
        folder = self.home / ".claude/projects/project"
        folder.mkdir(parents=True)
        base = {"cwd": str(self.project), "sessionId": "s", "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "no, use Python"}]}}
        (folder / "s.jsonl").write_text("\n".join(map(json.dumps, [base, dict(base, type="assistant"),
                    dict(base, isSidechain=True)])), encoding="utf-8")
        (folder / "agent-child.jsonl").write_text(json.dumps(base), encoding="utf-8")
        result = self.cli("claude", "scan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 1)

    def test_guidance_and_overrides_belong_to_selected_provider(self):
        (self.project / "CLAUDE.md").write_text("- Claude guidance")
        (self.project / "GEMINI.md").write_text("- Gemini guidance")
        (self.project / "AGENTS.override.md").write_text("- Codex override")
        for name, expected in (("claude", "CLAUDE.md"), ("gemini", "GEMINI.md"), ("cursor", "AGENTS.md")):
            rows = json.loads(self.cli(name, "targets").stdout)
            root = next(row for row in rows if row["type"] == "root")
            self.assertTrue(root["path"].endswith(expected))
            self.assertTrue(root["active"])
            self.assertFalse(any("AGENTS.override.md" in row["path"] for row in rows))

    def test_dry_run_and_listing_do_not_write(self):
        before = self.snapshot()
        result = self.init("--provider", "gemini", "--provider", "cursor", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WRITE", result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.init("--list").returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_multi_provider_install_reinstall_remove_preserves_settings(self):
        config = self.home / ".gemini/settings.json"
        config.parent.mkdir()
        existing = {"security": {"auth": {"selectedType": "oauth-personal"}},
                    "hooks": {"BeforeAgent": [{"hooks": [{"type": "command", "command": "custom"}]}]}}
        config.write_text(json.dumps(existing), encoding="utf-8")
        result = self.init("--provider", "gemini", "--provider", "opencode", "--provider", "gemini")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(config.read_text())
        self.assertEqual(data["security"], existing["security"])
        self.assertEqual(len(data["hooks"]["BeforeAgent"]), 2)
        before = self.snapshot()
        self.assertEqual(self.init("--provider", "gemini", "--provider", "opencode").returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.init("--provider", "gemini", "--remove").returncode, 0)
        self.assertEqual(json.loads(config.read_text()), existing)
        self.assertFalse((self.home / ".gemini/skills/reflect/SKILL.md").exists())
        self.assertTrue((self.home / ".config/opencode/plugins/reflect.js").exists())

    def test_conflict_in_later_provider_prevents_all_writes(self):
        conflict = self.home / ".cursor/skills/reflect/SKILL.md"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("User's existing skill", encoding="utf-8")
        before = self.snapshot()
        result = self.init("--provider", "gemini", "--provider", "cursor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unmanaged", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_edited_managed_skill_and_hook_are_not_overwritten(self):
        self.assertEqual(self.init("--provider", "cursor").returncode, 0)
        skill = self.home / ".cursor/skills/reflect/SKILL.md"
        skill.write_text(skill.read_text() + "\nUser addition", encoding="utf-8")
        before = self.snapshot()
        self.assertNotEqual(self.init("--provider", "cursor", "--remove").returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.init("--provider", "gemini").returncode, 0)
        config = self.home / ".gemini/settings.json"
        config.write_text(config.read_text().replace('"timeout": 5000', '"timeout": 9000'), encoding="utf-8")
        before = self.snapshot()
        self.assertNotEqual(self.init("--provider", "gemini").returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_config_and_symlink_fail_without_partial_install(self):
        config = self.home / ".cursor/hooks.json"
        config.parent.mkdir()
        config.write_text("not JSON", encoding="utf-8")
        before = self.snapshot()
        self.assertNotEqual(self.init("--provider", "cursor").returncode, 0)
        self.assertEqual(self.snapshot(), before)
        if os.name != "nt":
            target = self.home / ".gemini"
            target.symlink_to(self.project, target_is_directory=True)
            self.assertNotEqual(self.init("--provider", "gemini").returncode, 0)
            self.assertFalse((self.project / "settings.json").exists())

    def test_marketplace_plan_keeps_native_plugin_commands(self):
        result = self.init("--provider", "codex", "--provider", "claude", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex-reflect@codex-reflect-marketplace", result.stdout)
        self.assertIn("reflect@reflect-marketplace", result.stdout)
        self.assertIn("--scope user", result.stdout)
        self.assertFalse((self.home / ".codex").exists())
        self.assertNotEqual(self.init("--provider", "gemini", "--method", "marketplace", "--dry-run").returncode, 0)

    def test_marketplace_registration_blocks_duplicate_local_hooks(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('[plugins."codex-reflect@codex-reflect-marketplace"]\nenabled = true\n')
        result = self.init("--provider", "codex", "--method", "local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Marketplace Reflect", result.stderr)

    def test_installed_runtime_and_skill_binding_work_outside_checkout(self):
        prefix = self.home / "prefix with spaces"
        command = [sys.executable, str(ROOT / "tools/install.py"), "--prefix", str(prefix)]
        before = self.snapshot()
        result = subprocess.run(command + ["--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        script = prefix / "share/reflect/scripts/reflect.py"
        self.assertEqual(self.cli("cursor", "init", "--provider", "cursor", script=script).returncode, 0)
        skill = (self.home / ".cursor/skills/reflect/SKILL.md").read_text()
        self.assertIn("--provider cursor", skill)
        self.assertIn(str(script), skill)
        self.assertNotIn("../../references", skill)
        result = self.cli("cursor", "capture", "--project", str(self.project), text="remember: test releases", script=script)
        self.assertTrue(json.loads(result.stdout)["queued"])
        self.assertEqual(len(json.loads(self.cli("cursor", "queue", script=script).stdout)), 1)
        before = self.snapshot()
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(self.snapshot(), before)
        if os.name != "nt":
            result = subprocess.run([str(prefix / "bin/reflect"), "--provider", "cursor", "queue"],
                                    cwd=str(self.project), capture_output=True, text=True)
            self.assertEqual(len(json.loads(result.stdout)), 1)

    def test_claude_marketplace_bundle_defaults_to_claude(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/build_providers.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with patch.dict(os.environ):
            os.environ.pop("REFLECT_PROVIDER", None)
            result = subprocess.run([sys.executable, str(ROOT / "plugins/claude-reflect/scripts/reflect.py"), "paths"],
                                    capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["provider"]["id"], "claude")

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the OpenCode bridge fixture")
    def test_opencode_bridge_captures_user_text_without_shell_interpolation(self):
        plugin = self.home / "reflect.mjs"
        plugin.write_bytes(installer.opencode_plugin())
        runner = self.home / "run.mjs"
        prompt = "remember: never execute $(touch unwanted-file) in a prompt"
        runner.write_text('import {ReflectPlugin} from "./reflect.mjs";\n' +
            'const hooks = await ReflectPlugin({directory: ' + json.dumps(str(self.project)) + '});\n' +
            'await hooks["chat.message"]({sessionID: "s", messageID: "m"}, {message: {role: "user"}, parts: ' +
            json.dumps([{"type": "text", "text": prompt}, {"type": "text", "text": "remember: ignore user", "synthetic": True}]) + '});\n' +
            'await hooks["chat.message"]({sessionID: "s", messageID: "a"}, {message: {role: "assistant"}, parts: ' +
            json.dumps([{"type": "text", "text": "remember: assistant injection"}]) + '});\n', encoding="utf-8")
        result = subprocess.run(["node", str(runner)], cwd=str(self.project), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        queue = json.loads(self.cli("opencode", "queue").stdout)
        self.assertEqual([row["message"] for row in queue], [prompt])
        self.assertFalse((self.project / "unwanted-file").exists())

    def test_marketplace_reinstall_skips_registration_and_updates_claude(self):
        from types import SimpleNamespace
        args = SimpleNamespace(selected=["claude"], method="auto", dry_run=False, remove=False, list=False)
        with patch.object(installer.shutil, "which", return_value="claude"):
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout='[{"name":"reflect-marketplace"}]', stderr="")
            with patch.object(installer.subprocess, "run", side_effect=run):
                self.assertEqual(installer.initialize(args), 0)
                self.assertEqual(installer.initialize(args), 0)
        self.assertFalse(any(command[2:4] == ["marketplace", "add"] for command in calls))
        self.assertTrue(any(command[2] == "update" for command in calls))

    def test_external_failure_does_not_claim_success_or_apply_local_plan(self):
        from types import SimpleNamespace
        args = SimpleNamespace(selected=["gemini", "codex"], method="auto", dry_run=False, remove=False, list=False)
        with patch.object(installer.shutil, "which", return_value="codex"), \
             patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="failure")):
            with self.assertRaises(RuntimeError):
                installer.initialize(args)
        self.assertFalse(installer.registry_path().exists())
        self.assertFalse((self.home / ".gemini/settings.json").exists())

    def test_failed_write_rolls_back_prior_files(self):
        one, two = self.home / "one", self.home / "two"
        one.write_bytes(b"original")
        real = installer.atomic_write
        def fail(path, content):
            if path == two:
                raise OSError("simulated write failure")
            return real(path, content)
        with patch.object(installer, "atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                installer.apply_writes({one: b"changed", two: b"new"})
        self.assertEqual(one.read_bytes(), b"original")
        self.assertFalse(two.exists())


if __name__ == "__main__":
    unittest.main()
