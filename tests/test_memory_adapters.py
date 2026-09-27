"""Native memory formats and reviewed writes, isolated from operator state."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_inspection as fixtures
import memory_adapters as adapters
import memory_changes as changes
import providers
import reflect_core as core


class MemoryAdapterTests(unittest.TestCase):
    cli = fixtures.InspectionTests.cli
    snapshot = fixtures.InspectionTests.snapshot
    write = fixtures.InspectionTests.write

    def setUp(self):
        fixtures.InspectionTests.setUp(self)

    def test_every_provider_has_explicit_memory_capabilities_without_discovery(self):
        before = self.snapshot()
        with patch.object(adapters, "discover", side_effect=AssertionError("unrequested provider read")):
            rows = [adapters.details(name) for name in providers.PROVIDERS]
        self.assertEqual({row["provider"] for row in rows}, set(providers.PROVIDERS))
        for row in rows:
            self.assertTrue(row["feature"])
            self.assertTrue(row["scopes"])
            self.assertEqual(row["cli_write"], row["write"] != "native-tool")
        result = self.cli("codex", "memory", "--all-providers", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["adapters"]), 7)
        self.assertEqual(self.snapshot(), before)

    def test_codex_reads_consolidated_memory_and_notes_without_raw_history(self):
        directory = self.home / ".codex/memories"
        index = self.write(directory / "MEMORY.md", "# Preferences\nUse focused tests.\n")
        summary = self.write(directory / "memory_summary.md", "- Prefer Python\n")
        note = self.write(directory / "extensions/ad_hoc/notes/2026-09-27T00-00-00-testing.md", "Check native OS behavior.\n")
        self.write(directory / "raw_memories.md", "private raw history\n")
        self.write(directory / "rollout_summaries/raw.md", "private transcript\n")
        before = self.snapshot()
        data = adapters.inspect(self.project)
        self.assertEqual({row["path"] for row in data["sources"]}, set(map(str, (index, summary, note))))
        self.assertTrue(all(not row["writable"] for row in data["sources"]))
        entries = core.memory_entries(self.project, memory_only=True)
        self.assertEqual({row["text"] for row in entries}, {"Use focused tests.", "Prefer Python", "Check native OS behavior."})
        self.assertEqual(self.snapshot(), before)

    def test_codex_memory_v2_uses_separate_native_root(self):
        self.write(self.home / ".codex/config.toml", '[memories]\nversion = "v2"\n')
        self.write(self.home / ".codex/memories/MEMORY.md", "Old memory\n")
        current = self.write(self.home / ".codex/memories_v2/MEMORY.md", "Current memory\n")
        self.assertEqual(adapters.codex_directory(), current.parent)
        self.assertEqual([row["path"] for row in adapters.discover(self.project)], [str(current)])

    def test_claude_user_custom_directory_and_config_home_are_respected(self):
        providers.select("claude")
        custom_home = self.home / "Claude config"
        memory = self.home / "custom memory"
        self.write(custom_home / "settings.json", json.dumps({"autoMemoryDirectory": str(memory)}))
        note = self.write(memory / "notes.md", "Remember the deployment convention.\n")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(custom_home)}):
            before = self.snapshot()
            self.assertEqual(adapters.claude_directory(self.project), memory)
            self.assertIn(str(note), {row["path"] for row in core.targets(self.project)})
            self.assertEqual(self.snapshot(), before)

    def test_claude_repository_cannot_redirect_memory_reads_outside_scope(self):
        providers.select("claude")
        outside = self.home / "outside"
        self.write(self.project / ".claude/settings.json", json.dumps({"autoMemoryDirectory": str(outside)}))
        note = self.write(outside / "note.md", "Private fact\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "explicit --memory-dir"):
            adapters.discover(self.project)
        rows = adapters.discover(self.project, [outside])
        self.assertIn(str(note), {row["path"] for row in rows})
        self.assertTrue(all(not row["writable"] for row in rows))
        self.assertEqual(self.snapshot(), before)

    def test_gemini_registry_and_legacy_hash_memory_keep_drafts_pending(self):
        providers.select("gemini")
        home = self.home / ".gemini"
        key = os.path.normcase(str(self.project))
        self.write(home / "projects.json", json.dumps({"projects": {key: "project-one"}}))
        self.write(home / "tmp/project-one/.project_root", str(self.project))
        memory = home / "tmp/project-one/memory"
        index = self.write(memory / "MEMORY.md", "# Context\nUse pytest.\n")
        draft = self.write(memory / "skills/release/SKILL.md", "- Unapproved draft\n")
        proposal = self.write(memory / ".inbox/private/context.patch", "+Unapproved patch\n")
        old = self.write(home / "tmp" / hashlib.sha256(str(self.project).encode()).hexdigest() / "memory/GEMINI.md", "Older private note.\n")
        unrelated = self.write(home / "tmp/other/memory/MEMORY.md", "Unrelated note\n")
        before = self.snapshot()
        data = adapters.inspect(self.project)
        paths = {row["path"] for row in data["sources"]}
        self.assertEqual(paths, set(map(str, (index, draft, proposal, old))))
        self.assertNotIn(str(unrelated), paths)
        self.assertEqual(data["pending"], 2)
        entries = core.memory_entries(self.project, memory_only=True)
        self.assertEqual({row["text"] for row in entries}, {"Use pytest.", "Older private note."})
        child = self.project / "nested"
        child.mkdir()
        self.assertIn(memory, adapters.gemini_directories(child))
        self.assertEqual(self.snapshot(), before)

    def test_gemini_marker_recovery_and_conflicting_registry_are_read_only(self):
        providers.select("gemini")
        home = self.home / ".gemini"
        self.write(home / "tmp/owned/.project_root", str(self.project))
        note = self.write(home / "tmp/owned/memory/MEMORY.md", "Owned project memory.\n")
        before = self.snapshot()
        self.assertIn(str(note), {row["path"] for row in adapters.discover(self.project)})
        self.assertFalse((home / "projects.json").exists())
        self.assertEqual(self.snapshot(), before)
        self.write(home / "projects.json", json.dumps({"projects": {os.path.normcase(str(self.project)): "owned"}}))
        self.write(home / "tmp/owned/.project_root", str(self.home / "different project"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "ownership"):
            adapters.discover(self.project)
        self.assertEqual(self.snapshot(), before)

    def test_gemini_registry_rejects_traversal_ids_and_invalid_schema(self):
        providers.select("gemini")
        for data in ({"projects": []}, {"projects": {str(self.project): "../../escape"}}):
            self.write(self.home / ".gemini/projects.json", json.dumps(data))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                adapters.discover(self.project)
            self.assertEqual(self.snapshot(), before)

    def test_instruction_adapters_and_remote_saved_copies_preserve_provider_scope(self):
        for name, filename in (("opencode", "AGENTS.md"), ("antigravity", "GEMINI.md")):
            providers.select(name)
            path = self.write(self.project / filename, "Remember the verification command.\n")
            self.assertIn(str(path), {row["path"] for row in adapters.discover(self.project)})
            self.assertEqual(adapters.details()["storage"], "instructions")
        rules = self.write(self.project / ".agents/rules/testing.md", "---\ntrigger: always_on\n---\nUse focused tests.\n")
        providers.select("antigravity")
        self.assertIn(str(rules), {row["path"] for row in core.targets(self.project)})
        copy = self.write(self.home / "saved copy/MEMORIES.md", "- Captured preference\n")
        for name in ("cursor", "copilot"):
            providers.select(name)
            before = self.snapshot()
            data = adapters.inspect(self.project, [copy.parent])
            self.assertEqual(data["access"], "saved-copy")
            self.assertFalse(data["cli_write"])
            rows = core.memory_entries(self.project, [copy.parent], memory_only=True)
            self.assertIn("Captured preference", {row["text"] for row in rows})
            self.assertEqual(self.snapshot(), before)

    def test_memory_prose_parser_keeps_source_lines_and_skips_metadata_code_and_comments(self):
        text = ("---\nname: Note\n---\n# Heading\nA durable preference.\n- Bullet\n"
                "```sh\nprivate code\n```\n<!-- private comment\ncontinued -->\n1. Another preference\n")
        self.assertEqual(list(adapters.markdown_entries(text)),
                         [(5, "A durable preference."), (6, "Bullet"), (12, "Another preference")])

    def test_memory_sources_reject_symlinks_outside_selected_store(self):
        providers.select("codex")
        outside = self.write(self.home / "outside.md", "Outside\n")
        path = self.home / ".codex/memories/MEMORY.md"
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation is unavailable")
        self.assertEqual(adapters.discover(self.project), [])

    def test_copilot_home_override_is_reflected_in_native_instructions(self):
        providers.select("copilot")
        home = self.home / "Copilot config"
        path = self.write(home / "copilot-instructions.md", "- Prefer concise replies\n")
        with patch.dict(os.environ, {"COPILOT_HOME": str(home)}):
            self.assertEqual(providers.provider_home(), home)
            self.assertIn(str(path), {row["path"] for row in adapters.discover(self.project)})


class ReviewedWriteTests(unittest.TestCase):
    cli = fixtures.InspectionTests.cli
    snapshot = fixtures.InspectionTests.snapshot
    write = fixtures.InspectionTests.write

    def setUp(self):
        fixtures.InspectionTests.setUp(self)

    def test_preview_is_read_only_and_application_preserves_exact_backup(self):
        for name in ("claude", "gemini", "opencode", "antigravity"):
            providers.select(name)
            scope = "private" if name in ("claude", "gemini") else "project"
            destination = Path(adapters.destination(self.project, scope)["path"])
            self.write(destination, "# Memory\nOld preference.\n")
            before = self.snapshot()
            plan = changes.prepare("# Memory\nUpdated preference.\n", self.project, scope)
            self.assertEqual(self.snapshot(), before)
            self.assertIn("-Old preference.", plan["diff"])
            self.assertIn("+Updated preference.", plan["diff"])
            result = changes.apply(plan, plan["approval"])
            self.assertTrue(result["applied"])
            self.assertEqual(destination.read_text(encoding="utf-8"), plan["content"])
            self.assertEqual((Path(result["backup"]) / "before.md").read_text(encoding="utf-8"), "# Memory\nOld preference.\n")
            self.assertEqual(core.load_queue(self.project), [])

    def test_stale_or_tampered_plan_is_rejected_before_any_writes(self):
        providers.select("claude")
        path = Path(adapters.destination(self.project, "project")["path"])
        self.write(path, "Original\n")
        plan = changes.prepare("Reviewed change\n", self.project, "project")
        self.write(path, "Concurrent change\n")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "changed since review"):
            changes.apply(plan, plan["approval"])
        self.assertEqual(self.snapshot(), before)
        for key, value in (("content", "Unreviewed\n"), ("diff", "Misleading diff\n"), ("path", str(self.home / "elsewhere.md"))):
            modified = dict(plan, **{key: value})
            with self.assertRaises(ValueError):
                changes.apply(modified, plan["approval"])
            self.assertEqual(self.snapshot(), before)

    def test_stdin_preview_and_missing_newline_diff_preserve_exact_content(self):
        providers.select("claude")
        self.write(self.project / "CLAUDE.md", "Old preference")
        before = self.snapshot()
        result = self.cli("claude", "memory-plan", "--scope", "project", "--content", "-", text="New preference")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertIn("-Old preference\n\\ No newline at end of file\n+New preference", plan["diff"])
        self.assertEqual(plan["content"], "New preference")
        self.assertEqual(self.snapshot(), before)
        changes.apply(plan, plan["approval"])
        self.assertEqual((self.project / "CLAUDE.md").read_bytes(), b"New preference")

    def test_codex_only_adds_native_notes_and_never_overwrites_managed_memory(self):
        generated = self.write(self.home / ".codex/memories/MEMORY.md", "Generated memory\n")
        filename = "2026-09-27T01-02-03-testing.md"
        before = self.snapshot()
        plan = changes.prepare("A reviewed note.\n", self.project, "global", filename)
        self.assertEqual(self.snapshot(), before)
        result = changes.apply(plan, plan["approval"])
        self.assertEqual(Path(result["path"]).read_text(encoding="utf-8"), "A reviewed note.\n")
        self.assertEqual(generated.read_text(encoding="utf-8"), "Generated memory\n")
        with self.assertRaises(ValueError):
            changes.prepare("Replacement\n", self.project, "global", filename)
        for invalid in ("../MEMORY.md", "MEMORY.md", "2026-09-27T01-02-03-../x.md"):
            with self.assertRaises(ValueError):
                changes.prepare("A note\n", self.project, "global", invalid)

    def test_note_publication_never_overwrites_a_concurrently_created_note(self):
        path = self.home / "notes/native.md"
        self.write(path, "Another writer\n")
        with self.assertRaises(FileExistsError):
            changes.create_note(path, b"New note\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "Another writer\n")
        self.assertEqual(list(path.parent.glob(".reflect-note-*")), [])

    def test_cli_plan_and_apply_require_exact_approval_and_matching_project(self):
        content = self.write(self.home / "new memory.md", "# Memory\nPréférer les tests ciblés.\n")
        before = self.snapshot()
        result = self.cli("claude", "memory-plan", "--content", str(content), "--scope", "project")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        plan = json.loads(result.stdout)
        saved = self.write(self.home / "plan.json", result.stdout)
        before = self.snapshot()
        rejected = self.cli("claude", "memory-apply", "--plan", str(saved), "--approval", "wrong")
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(self.snapshot(), before)
        mismatch = self.cli("claude", "memory-apply", "--project", str(self.home), "--plan", str(saved), "--approval", plan["approval"])
        self.assertEqual(mismatch.returncode, 2)
        self.assertEqual(self.snapshot(), before)
        applied = self.cli("claude", "memory-apply", "--plan", str(saved), "--approval", plan["approval"])
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertTrue(json.loads(applied.stdout)["applied"])
        self.assertEqual((self.project / "CLAUDE.md").read_text(encoding="utf-8"), content.read_text(encoding="utf-8"))

    def test_remote_write_is_a_native_tool_handoff_without_local_fake_memories(self):
        for name in ("cursor", "copilot"):
            providers.select(name)
            before = self.snapshot()
            plan = changes.prepare("A reviewed preference.\n", self.project, "project")
            self.assertEqual(plan["status"], "requires-native-tool")
            self.assertIsNone(plan["path"])
            self.assertEqual(plan["operation"], "native-memory-write")
            with self.assertRaisesRegex(RuntimeError, "native tools"):
                changes.apply(plan, plan["approval"])
            self.assertEqual(self.snapshot(), before)

    def test_malformed_plan_fields_fail_without_writes(self):
        providers.select("claude")
        plan = changes.prepare("A note\n", self.project, "project")
        before = self.snapshot()
        for key in ("project", "scope", "filename", "content", "diff", "status", "operation", "path", "before_sha256"):
            malformed = dict(plan, **{key: []})
            malformed["approval"] = changes.identity(malformed)
            with self.subTest(key=key), self.assertRaises(ValueError):
                changes.apply(malformed, malformed["approval"])
            self.assertEqual(self.snapshot(), before)

    def test_write_rejects_wrong_provider_invalid_scope_and_symlink_paths(self):
        providers.select("claude")
        plan = changes.prepare("A note\n", self.project, "project")
        providers.select("gemini")
        with self.assertRaises(ValueError):
            changes.apply(plan, plan["approval"])
        with self.assertRaises(ValueError):
            changes.prepare("A note\n", self.project, "private", "arbitrary.md")
        providers.select("opencode")
        with self.assertRaises(ValueError):
            changes.prepare("A note\n", self.project, "private")
        outside = self.write(self.home / "outside.md", "Outside\n")
        try:
            (self.project / "AGENTS.md").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation is unavailable")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            changes.prepare("A note\n", self.project, "project")
        self.assertEqual(self.snapshot(), before)
