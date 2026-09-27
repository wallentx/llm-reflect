"""Read-only reports and Claude discovery against isolated provider fixtures."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_providers as fixtures
import providers
import memory_adapters as adapters
import reflect_core as core
import reports


class InspectionTests(unittest.TestCase):
    cli = fixtures.ProviderTests.cli
    snapshot = fixtures.ProviderTests.snapshot

    def setUp(self):
        fixtures.ProviderTests.setUp(self)
        self.name_env = patch.dict(os.environ, {"CLAUDE_CODE_PROJECT_DIR_NAME": ""})
        self.name_env.start()
        self.addCleanup(self.name_env.stop)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def git(self, project, *args):
        result = subprocess.run(["git", "-C", str(project), *args], capture_output=True,
                                text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_status_resolves_context_without_history_enumeration_or_writes(self):
        self.write(self.project / "AGENTS.md", "- Use Python\n")
        before = self.snapshot()
        with patch.object(core, "session_files", side_effect=AssertionError("history was enumerated")):
            data = reports.status(self.project)
        self.assertEqual(data["provider"]["id"], "codex")
        self.assertEqual(data["project"], str(self.project))
        self.assertEqual(data["queue_summary"]["total"], 0)
        self.assertEqual(data["targets"]["counts"]["root"], 1)
        result = self.cli("codex", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pending corrections: 0 (0 stale)", result.stdout)
        self.assertIn("History: codex; automatic capture: yes; model helper: yes", result.stdout)
        self.assertEqual(json.loads(self.cli("codex", "status", "--format", "json").stdout), data)
        self.assertEqual(self.snapshot(), before)

    def test_target_text_groups_skills_and_marks_missing_and_overridden_guidance(self):
        self.write(self.project / "AGENTS.md", "@notes.md\n")
        self.write(self.project / "AGENTS.override.md", "- Override\n")
        self.write(self.project / "notes.md", "- Supporting\n")
        for name in ("one", "two"):
            self.write(self.project / ".agents/skills" / name / "SKILL.md", "- Workflow\n")
        self.write(self.home / ".codex/skills/global/SKILL.md", "- Global\n")
        before = self.snapshot()
        result = self.cli("codex", "targets", "--format", "text")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("overridden", result.stdout)
        self.assertIn("missing", result.stdout)
        self.assertIn("Skills | Directory", result.stdout)
        self.assertIn("Referenced documents: 1 (supporting only)", result.stdout)
        self.assertNotIn("SKILL.md", result.stdout)
        short = self.cli("codex", "targets", "--format", "text", "--limit", "1")
        self.assertIn("more guidance files", short.stdout)
        self.assertIn("more skill directories", short.stdout)
        complete = json.loads(self.cli("codex", "targets", "--limit", "1").stdout)
        self.assertEqual(complete, core.targets(self.project))
        self.assertEqual(sum(row["type"] == "skill" for row in complete), 3)
        self.assertEqual(self.snapshot(), before)

    def test_reference_limit_is_reported(self):
        self.write(self.project / "AGENTS.md", "\n".join("@note{}.md".format(n) for n in range(101)))
        for n in range(101):
            self.write(self.project / "note{}.md".format(n), "- Note\n")
        data = reports.status(self.project)
        self.assertEqual(data["targets"]["references"], {"count": 100, "limit": 100, "limit_reached": True})
        self.assertIn("discovery limit reached", reports.render("status", data))

    def test_queue_text_handles_empty_stale_and_untrusted_data_without_writes(self):
        self.assertEqual(self.cli("codex", "queue", "--format", "text").returncode, 0)
        date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        items = [{"id": "first", "message": "remember: " + "x" * 120 + "\x1b[2J\nsecret",
                  "timestamp": date, "confidence": .8},
                 {"id": "second", "message": "no, use Python", "confidence": None}]
        self.write(core.queue_path(self.project), json.dumps(items))
        before = self.snapshot()
        result = self.cli("codex", "queue", "--format", "text", "--limit", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("80%", result.stdout)
        self.assertIn("stale", result.stdout)
        self.assertIn("120d", result.stdout)
        self.assertIn("1 more records", result.stdout)
        self.assertNotIn("second", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        full_text = self.cli("codex", "queue", "--format", "text")
        self.assertEqual(full_text.returncode, 0, full_text.stderr)
        self.assertIn("second", full_text.stdout)
        data = json.loads(self.cli("codex", "queue", "--limit", "1").stdout)
        self.assertEqual(len(data), 2)
        self.assertIn("\x1b", data[0]["message"])
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("\x1b", reports.clean("x\x1b[2J\ny"))

    def test_scan_report_preserves_project_and_session_counts_and_complete_json(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = [{"provider": "cursor", "project": str(project), "session_id": session,
                 "id": str(index), "timestamp": now, "role": "user", "text": "no, use Python"}
                for index, (project, session) in enumerate([
                    (self.project, "same"), (self.project, "other"), (self.home / "other", "same")])]
        history = self.write(self.home / "export.jsonl", "\n".join(map(json.dumps, rows)))
        before = self.snapshot()
        args = ("scan", "--history", str(history), "--all-projects", "--days", "14", "--limit", "1")
        result = self.cli("cursor", *args, "--format", "text")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sessions: 3 | Projects: 2", result.stdout)
        self.assertIn("2 more records", result.stdout)
        self.assertEqual(len(json.loads(self.cli("cursor", *args).stdout)), 3)
        self.assertEqual(self.snapshot(), before)

    def test_compare_text_shows_regex_and_semantic_disagreements_and_failures(self):
        rows = [dict(project=str(self.project), session_id="s", kind="user", skill=None,
                     message="no, use Python", type="correction", semantic=None),
                dict(project=str(self.project), session_id="s", kind="user", skill=None,
                     message="a message", type=None, semantic={"is_learning": True, "confidence": .9})]
        text = reports.render("compare", rows, self.project)
        self.assertIn("Regex / Semantic", text)
        self.assertIn("correction / unavailable", text)
        self.assertIn("none / 90%", text)
        queue = [dict(id="item", confidence=.8, message="remember: use Python", semantic_status="unavailable")]
        self.assertIn("unavailable", reports.render("queue", queue, self.project))

    def test_launcher_preserves_foreign_working_directory_and_provider_binding(self):
        before = self.snapshot()
        result = self.cli("claude", "status", "--format", "json", script=fixtures.ROOT / "tools/reflect.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["project"], str(self.project))
        self.assertEqual(data["provider"]["id"], "claude")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_display_limit_is_rejected_without_writes(self):
        before = self.snapshot()
        result = self.cli("codex", "status", "--limit", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--limit must be positive", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_claude_native_notes_and_cross_agent_files_are_discovered(self):
        providers.select("claude")
        self.write(self.project / "CLAUDE.md", "- Claude instruction\n")
        agent = self.write(self.project / "AGENTS.md", "- Shared instruction\n")
        child = self.write(self.project / "child/AGENTS.md", "- Child instruction\n")
        self.write(self.project / "AGENTS.override.md", "- Codex only\n")
        commands = [self.write(directory / "nested/deploy.md", "- Run deployment checks\n")
                    for directory in (self.project / "commands", self.project / ".claude/commands",
                                      self.home / ".claude/commands")]
        memory = self.write(core.claude_memory_directory(self.project) / "testing.md",
                            "# Testing\n\nPrefer focused tests.\n- Check Windows too.\n")
        before = self.snapshot()
        targets = json.loads(self.cli("claude", "targets").stdout)
        by_path = {row["path"]: row for row in targets}
        for path in (agent, child):
            self.assertEqual(by_path[str(path)]["type"], "cross-agent")
            self.assertEqual(by_path[str(path)]["loading"], "conditional")
        for path in commands:
            self.assertNotIn(str(path), by_path)
        self.assertEqual(by_path[str(memory)]["type"], "auto-memory")
        self.assertFalse(by_path[str(memory)]["active"])
        self.assertFalse(any("AGENTS.override.md" in row["path"] for row in targets))
        entries = json.loads(self.cli("claude", "entries").stdout)
        native = [row for row in entries if row["source_type"] == "auto-memory"]
        self.assertEqual([(row["text"], row["line_number"]) for row in native],
                         [("Prefer focused tests.", 3), ("Check Windows too.", 4)])
        text = self.cli("claude", "targets", "--format", "text").stdout
        self.assertIn("conditional", text)
        self.assertIn("testing.md", text)
        for name in ("codex", "cursor", "gemini", "opencode", "copilot", "antigravity"):
            with self.subTest(provider=name):
                rows = json.loads(self.cli(name, "targets").stdout)
                self.assertFalse(any(row["type"] in ("command", "cross-agent") for row in rows))
                self.assertNotIn(str(memory), {row["path"] for row in rows})
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(shutil.which("git"), "Git is needed for repository memory fixtures")
    def test_claude_memory_is_shared_by_repository_subdirectories_and_worktrees(self):
        providers.select("claude")
        self.git(self.project, "init", "-b", "reflect-fixture")
        self.git(self.project, "-c", "user.name=Reflect Fixture", "-c", "user.email=fixture@example.test",
                 "commit", "--allow-empty", "-m", "Fixture")
        child = self.project / "nested"
        child.mkdir()
        worktree = self.home / "second worktree"
        self.git(self.project, "worktree", "add", "--detach", str(worktree))
        expected = self.home / ".claude/projects" / core.upstream.get_project_folder_name(str(self.project)) / "memory"
        note = self.write(expected / "notes.md", "Use repository-wide conventions.\n")
        before = self.snapshot()
        for project in (self.project, child, worktree):
            with self.subTest(project=project):
                self.assertEqual(core.claude_memory_directory(project), expected)
                self.assertIn(str(note), {row["path"] for row in core.targets(project)})
        self.assertEqual(self.snapshot(), before)

    def test_claude_memory_falls_back_without_git_and_uses_explicit_project_name(self):
        providers.select("claude")
        expected = self.home / ".claude/projects" / core.upstream.get_project_folder_name(str(self.project)) / "memory"
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("git", 2)):
            with self.subTest(failure=type(failure).__name__), patch.object(adapters.subprocess, "run", side_effect=failure):
                self.assertEqual(core.claude_memory_directory(self.project), expected)
        with patch.dict(os.environ, {"CLAUDE_CODE_PROJECT_DIR_NAME": "shared"}):
            self.assertEqual(core.claude_memory_directory(self.project), self.home / ".claude/projects/shared/memory")
        with patch.dict(os.environ, {"CLAUDE_CODE_PROJECT_DIR_NAME": "../escape"}), \
                patch.object(adapters.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(core.claude_memory_directory(self.project), expected)

    def test_long_memory_paths_use_upstream_hash_without_default_config_lookup(self):
        providers.select("claude")
        project = self.project / ("long" * 60)
        canonical = str(project)
        encoded = core.upstream._encode_project_path(canonical)
        folder = encoded[:core.upstream.MAX_PROJECT_FOLDER_NAME_LEN] + "-" + core.upstream._long_name_hash(canonical)
        with patch.object(adapters.subprocess, "run", side_effect=FileNotFoundError()), \
                patch.object(core.upstream, "get_claude_dir", side_effect=AssertionError("wrong config root")):
            self.assertEqual(core.claude_memory_directory(project), self.home / ".claude/projects" / folder / "memory")

    def test_claude_discovery_rejects_command_and_memory_symlinks_outside_scope(self):
        providers.select("claude")
        outside = self.write(self.home / "outside.md", "- Out of scope\n")
        destinations = (self.project / ".claude/commands/escape.md",
                        core.claude_memory_directory(self.project) / "escape.md")
        try:
            for destination in destinations:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation is unavailable on this host")
        before = self.snapshot()
        self.assertNotIn(str(outside), {row["path"] for row in core.targets(self.project)})
        self.assertEqual(core.memory_entries(self.project), [])
        self.assertEqual(self.snapshot(), before)
