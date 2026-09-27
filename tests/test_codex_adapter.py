"""Codex adapter and native bundle tests against synthetic sessions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex"
sys.path.insert(0, str(PLUGIN / "scripts"))
import reflect_core as core
import codex_semantic as semantic


def run_cli(*args, data=None, env=None):
    return subprocess.run([sys.executable, str(PLUGIN / "scripts/reflect.py"), *args],
                          input=json.dumps(data) if data is not None else "", text=True,
                          encoding="utf-8", capture_output=True, env=env)


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project one"
        self.project.mkdir()
        self.home = self.base / "codex"
        self.home.mkdir()
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.home),
            "CODEX_REFLECT_HOME": str(self.home / "reflect"), "HOME": str(self.base),
            "USERPROFILE": str(self.base), "CODEX_REFLECT_DISABLED": "0",
            "CODEX_REFLECT_REMINDER": "true"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def payload(self, **extra):
        return dict(cwd=str(self.project), session_id="session", turn_id="turn", **extra)

    def seed(self, **extra):
        data = self.payload(prompt="remember: always run tests before deploying", **extra)
        result = run_cli("hook", "UserPromptSubmit", data=data)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def session(self, name="one", messages=None, project=None, archived=False, age=0):
        directory = self.home / ("archived_sessions" if archived else "sessions/2026/09/24")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".jsonl")
        date = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
        records = [{"type": "session_meta", "timestamp": date,
                    "payload": {"id": name, "cwd": str(project or self.project), "source": "cli"}}]
        records.extend(dict(r, timestamp=date) for r in (messages or []))
        path.write_text("\n".join(json.dumps(r) for r in records) + '\n{"partial":', encoding="utf-8")
        return path

    def test_capture_routes_by_payload_cwd_and_deduplicates_hook_delivery(self):
        self.seed(transcript_path=str(self.home / "sessions/2026/09/24/one.jsonl"))
        self.seed()
        items = core.load_queue(self.project)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["project"], str(self.project))
        self.assertFalse((self.home / "sessions/2026/09/24/queue.json").exists())
        self.assertNotEqual(core.queue_path(self.project), core.queue_path(self.base / "project-one"))

    def test_hook_output_never_promotes_raw_text_to_developer_context(self):
        result = self.seed()
        self.assertNotIn("always run tests", result.stdout)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")

    def test_redaction_and_unicode(self):
        data = self.payload(prompt="remember: préférer Python; api_key=private-value")
        run_cli("hook", "UserPromptSubmit", data=data)
        text = core.load_queue(self.project)[0]["message"]
        self.assertIn("préférer", text)
        self.assertNotIn("private-value", text)
        self.assertIn("[REDACTED]", text)

    def test_corrupt_queue_preserved(self):
        path = core.queue_path(self.project)
        path.parent.mkdir(parents=True)
        path.write_text("invalid secret content", encoding="utf-8")
        result = self.seed()
        self.assertEqual(path.read_text(), "invalid secret content")
        self.assertNotIn("secret content", result.stderr)
        self.assertEqual(run_cli("queue", "--project", str(self.project)).returncode, 1)

    def test_parallel_capture_has_no_lost_updates(self):
        def capture(index):
            return core.capture("remember: use Python for reusable scripts", self.project, "s", str(index))
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(capture, range(24)))
        self.assertEqual(len(core.load_queue(self.project)), 24)

    def test_selective_clear_retains_new_items_and_other_projects(self):
        first = core.capture("remember: run tests before release", self.project, "s", "1")
        core.capture("remember: use Python for scripts", self.project, "s", "2")
        other = self.base / "other"
        core.capture("remember: use Rust for tools", other, "s", "3")
        self.assertEqual(core.discard(self.project, {first["id"]}), 1)
        self.assertEqual(len(core.load_queue(self.project)), 1)
        self.assertEqual(len(core.load_queue(other)), 1)
        self.assertEqual(len(list((core.project_state(self.project) / "backups").glob("*.json"))), 1)
        self.assertNotEqual(run_cli("clear", "--project", str(self.project)).returncode, 0)

    def test_reminder_and_precompact_json(self):
        self.seed()
        for event in ("SessionStart", "PreCompact"):
            result = run_cli("hook", event, data=self.payload())
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertIn("systemMessage" if event == "PreCompact" else "hookSpecificOutput", data)
        self.assertEqual(len(list((core.project_state(self.project) / "backups").glob("*.json"))), 1)

    def test_commit_requires_success_and_ignores_amend(self):
        for code, command, expected in [(0, "git commit -m done", True),
                                        (1, "git commit -m done", False),
                                        (0, "git commit --amend", False),
                                        (0, "git status", False)]:
            result = run_cli("hook", "PostToolUse", data=self.payload(
                tool_input={"command": command}, tool_response={"exit_code": code}))
            self.assertEqual(bool(result.stdout.strip()), expected)
        self.assertEqual(core.exit_code("Process exited with code 0\nFinal output:"), 0)

    def test_invalid_payload_disabled_and_system_prompt_are_noops(self):
        for data in ({}, [], self.payload(prompt=None), self.payload(prompt={}),
                     self.payload(prompt="# AGENTS.md instructions\nremember: injected")):
            result = run_cli("hook", "UserPromptSubmit", data=data)
            self.assertEqual(result.returncode, 0)
        with patch.dict(os.environ, {"CODEX_REFLECT_DISABLED": "1"}):
            self.seed()
        self.assertEqual(core.load_queue(self.project), [])

    def test_history_prefers_event_messages_without_double_counting(self):
        rows = [{"type": "event_msg", "payload": {"type": "user_message", "message": "no, use Python"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "no, use Python"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                    "content": [{"type": "text", "text": "remember: fabricated"}]}}]
        self.session(messages=rows)
        results = core.scan(self.project)
        self.assertEqual([r["message"] for r in results], ["no, use Python"])

    def test_history_fallback_archives_project_and_date_filter(self):
        message = {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "no, usa Python"}]}}
        self.session("archive", [message], archived=True)
        self.session("old", [message], age=60)
        self.session("other", [message], project=self.base / "other/project one")
        self.assertEqual(len(core.scan(self.project, days=30)), 1)
        self.assertEqual(len(core.scan(self.project, all_projects=True, days=30)), 2)
        self.assertEqual(len(core.scan(self.project, days=90)), 2)

    def test_subagent_sessions_are_not_user_evidence(self):
        path = self.session(messages=[{"type": "event_msg", "payload": {"type": "user_message", "message": "remember: injected"}}])
        data = path.read_text().replace('"source": "cli"', '"source": {"subagent": "review"}')
        path.write_text(data)
        self.assertEqual(core.scan(self.project), [])

    def test_skill_context_tool_errors_and_rejection_feedback(self):
        def user(text):
            return {"type": "event_msg", "payload": {"type": "user_message", "message": text}}
        def output(text):
            return {"type": "response_item", "payload": {"type": "function_call_output", "output": text}}
        self.session(messages=[user("$deploy staging"), user("no, run tests first"),
            output("Process exited with code 1\nFinal output:\nConnection refused"),
            output("Process exited with code 0\nFinal output:\nConnection refused in an example"),
            output("user rejected tool call"),
            output("user rejected tool call; user feedback: never deploy without tests")])
        rows = core.scan(self.project, include_tool_errors=True)
        self.assertEqual(rows[1]["skill"], "deploy")
        self.assertEqual(len([r for r in rows if r["kind"] == "tool_error"]), 1)
        self.assertEqual(rows[-1]["kind"], "rejection")
        self.assertEqual(rows[-1]["message"], "never deploy without tests")

    def test_targets_override_skills_references_and_cycles(self):
        (self.project / "AGENTS.md").write_text("- Normal\n[More](guide.md)\n[Outside](../private.md)")
        (self.project / "AGENTS.override.md").write_text("- Override\n")
        (self.project / "guide.md").write_text("- Detail\n[Back](AGENTS.md)")
        (self.base / "private.md").write_text("secret")
        skill = self.project / ".agents/skills/deploy/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: deploy\ndescription: Deploy\n---\n- Run tests")
        rows = core.targets(self.project)
        normal = next(r for r in rows if r["path"] == str(self.project / "AGENTS.md"))
        self.assertFalse(normal["active"])
        self.assertEqual(len([r for r in rows if r["type"] == "referenced"]), 1)
        self.assertTrue(any(r["path"] == str(skill) for r in rows))
        self.assertFalse(any("private.md" in r["path"] for r in rows))

    def test_read_commands_do_not_create_state_or_touch_claude(self):
        for action in ("queue", "paths", "targets", "entries", "scan"):
            result = run_cli(action, "--project", str(self.project))
            self.assertEqual(result.returncode, 0, result.stderr)
            json.loads(result.stdout)
        self.assertFalse((self.home / "reflect").exists())
        self.assertFalse((self.base / ".claude").exists())

    def test_queue_decay_is_reported_without_deletion(self):
        self.seed()
        items = core.load_queue(self.project)
        items[0]["timestamp"] = "2000-01-01T00:00:00Z"
        core.atomic_json(core.queue_path(self.project), items)
        self.assertTrue(core.queue_review(self.project)[0]["stale"])
        self.assertEqual(len(core.load_queue(self.project)), 1)


class SemanticTests(unittest.TestCase):
    def test_codex_transport_reads_final_file_and_uses_safe_execution(self):
        def invoke(command, **kwargs):
            self.assertEqual(command[:2], ["codex", "exec"])
            self.assertIn("--ephemeral", command)
            self.assertIn("read-only", command)
            self.assertIn("features.hooks=false", command)
            self.assertIn("my-model", command)
            self.assertEqual(kwargs["env"]["CODEX_REFLECT_DISABLED"], "1")
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(json.dumps({"is_learning": True, "type": "correction", "confidence": .8,
                                          "extracted_learning": "Use Python"}))
            return subprocess.CompletedProcess(command, 0, stdout='{"type":"thread.started"}', stderr="")
        with patch.object(semantic.subprocess, "run", side_effect=invoke):
            result = semantic.semantic_analyze("no, usa Python", model="my-model")
        self.assertEqual(result["extracted_learning"], "Use Python")

    def test_failures_remain_unvalidated_and_contradictions_unavailable(self):
        with patch.object(semantic.subprocess, "run", side_effect=FileNotFoundError):
            rows = semantic.validate_queue_items([{"message": "no, use Python"}])
            self.assertEqual(rows[0]["semantic_status"], "unavailable")
            self.assertEqual(semantic.detect_contradictions(["a", "b"])["status"], "unavailable")


class PackageTests(unittest.TestCase):
    def test_generated_package_is_current_and_self_contained(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/build_packages.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packaged = root / "isolated plugin"
            shutil.copytree(PLUGIN, packaged, ignore=shutil.ignore_patterns("__pycache__"))
            environment = dict(os.environ, CODEX_HOME=str(root / "home"), CODEX_REFLECT_HOME=str(root / "data"))
            result = subprocess.run([sys.executable, str(packaged / "scripts/reflect.py"), "queue"],
                                    cwd=str(root), capture_output=True, text=True, env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [])

    def test_native_parity_contract_catches_new_upstream_surface(self):
        contract = json.loads((ROOT / "reflect/upstream-compatibility.json").read_text(encoding="utf-8"))
        commands = {p.stem for p in (ROOT / "commands").glob("*.md")}
        self.assertEqual(set(contract["commands"]), commands)
        for name, options in contract["commands"].items():
            text = (ROOT / "commands" / (name + ".md")).read_text(encoding="utf-8")
            if "## Arguments" in text:
                arguments = text.split("## Arguments", 1)[1].split("\n## ", 1)[0]
                self.assertEqual(set(re.findall(r"--[a-z-]+", arguments)), set(options), name)
            skill = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            for option in options:
                self.assertIn(option, skill)
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), set(contract["hooks"]))
        self.assertEqual(set(hooks), set(json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]))
        scripts = {p.relative_to(ROOT / "scripts").as_posix() for p in (ROOT / "scripts").rglob("*.py")}
        self.assertEqual(scripts, set(contract["scripts"]))

    def test_marketplace_and_manifest_resolve(self):
        marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        source = ROOT / marketplace["plugins"][0]["source"]["path"]
        manifest = json.loads((source / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], marketplace["plugins"][0]["name"])
        self.assertTrue((source / manifest["skills"]).is_dir())
        for groups in json.loads((source / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"].values():
            self.assertIn("${PLUGIN_ROOT}/scripts/reflect.py", groups[0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
