"""Provider capabilities and paths. No provider discovery mutates configuration."""
import json
import os
from pathlib import Path


# Keep identifiers aligned with `panoptes init --provider`.
PROVIDERS = {
    "codex": {"name": "Codex", "directory": ".codex", "skills": ".codex/skills",
              "project_skills": ".agents/skills", "instructions": "AGENTS.md",
              "hooks_file": "hooks.json", "history": "codex", "marketplace": True},
    "claude": {"name": "Claude Code", "directory": ".claude", "skills": ".claude/skills",
               "project_skills": ".claude/skills", "instructions": "CLAUDE.md",
               "hooks_file": "settings.json", "history": "claude", "marketplace": True},
    "cursor": {"name": "Cursor", "directory": ".cursor", "skills": ".cursor/skills",
               "project_skills": ".cursor/skills", "instructions": "AGENTS.md",
               "hooks_file": "hooks.json", "history": "import", "marketplace": False},
    "gemini": {"name": "Gemini CLI", "directory": ".gemini", "skills": ".gemini/skills",
               "project_skills": ".gemini/skills", "instructions": "GEMINI.md",
               "hooks_file": "settings.json", "history": "import", "marketplace": False},
    "antigravity": {"name": "Antigravity CLI", "directory": ".gemini/antigravity-cli",
                    "skills": ".gemini/antigravity-cli/skills", "project_skills": ".agent/skills",
                    "instructions": "GEMINI.md", "hooks_file": None,
                    "history": "import", "marketplace": False},
    "opencode": {"name": "OpenCode", "directory": ".config/opencode",
                 "skills": ".config/opencode/skills", "project_skills": ".opencode/skills",
                 "instructions": "AGENTS.md", "hooks_file": None,
                 "history": "import", "marketplace": False},
    "copilot": {"name": "Copilot CLI", "directory": ".copilot", "skills": ".copilot/skills",
                "project_skills": ".github/skills", "instructions": "AGENTS.md",
                "hooks_file": "settings.json", "history": "import", "marketplace": False},
}

_selected = None


def default_provider():
    binding = Path(__file__).resolve().parents[1] / "provider.json"
    return os.environ.get("REFLECT_PROVIDER") or (
        json.loads(binding.read_text(encoding="utf-8"))["provider"] if binding.is_file() else "codex")


def select(name):
    if name not in PROVIDERS:
        raise ValueError("Unknown Reflect provider: " + str(name))
    global _selected
    _selected = name


def current():
    return _selected or default_provider()


def provider_home(name=None):
    name = name or current()
    if name == "codex":
        path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    elif name == "claude":
        path = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    elif name == "opencode":
        path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "opencode"
    elif name == "copilot":
        path = Path(os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot")))
    else:
        path = Path.home() / PROVIDERS[name]["directory"]
    # Preserve symlink components so installation can reject redirected writes.
    return path.expanduser().absolute()


def skill_home(name=None):
    return provider_home(name) / "skills"


def details():
    name = current()
    info = dict(PROVIDERS[name], id=name)
    info.update(home=str(provider_home()), global_skills=str(skill_home()),
                automatic_capture=name != "antigravity", semantic_cli=name == "codex")
    from memory_adapters import details as memory_details
    info["memory"] = memory_details(name)
    return info


EVENTS = {
    "codex": {"SessionStart": "SessionStart", "UserPromptSubmit": "UserPromptSubmit",
              "PreCompact": "PreCompact", "PostToolUse": "PostToolUse"},
    "claude": {"SessionStart": "SessionStart", "UserPromptSubmit": "UserPromptSubmit",
               "PreCompact": "PreCompact", "PostToolUse": "PostToolUse"},
    "gemini": {"SessionStart": "SessionStart", "BeforeAgent": "UserPromptSubmit",
               "PreCompress": "PreCompact"},
    "cursor": {"sessionStart": "SessionStart", "beforeSubmitPrompt": "UserPromptSubmit",
               "preCompact": "PreCompact"},
    "copilot": {"sessionStart": "SessionStart", "userPromptSubmitted": "UserPromptSubmit",
                "preCompact": "PreCompact"},
    "opencode": {"UserPromptSubmit": "UserPromptSubmit"},
    "antigravity": {},
}


def normalize_hook(event, data):
    """Only map documented fields; ambiguous multi-root workspaces are skipped."""
    name = current()
    canonical = EVENTS[name].get(event)
    if not canonical or not isinstance(data, dict):
        return None, {}
    result = dict(data)
    if name == "cursor":
        roots = data.get("workspace_roots", [])
        if not result.get("cwd") and isinstance(roots, list) and len(roots) == 1:
            result["cwd"] = roots[0]
        result["session_id"] = data.get("conversation_id", data.get("session_id", ""))
        result["turn_id"] = data.get("generation_id", "")
    elif name == "copilot":
        result["session_id"] = data.get("sessionId", data.get("session_id", ""))
        result["turn_id"] = str(data.get("timestamp", ""))
    return canonical, result


def hook_output(event, message):
    name = current()
    canonical = EVENTS[name].get(event)
    if name == "cursor":
        if canonical == "UserPromptSubmit":
            return {"continue": True}
        return {"additional_context": message} if canonical == "SessionStart" and message else {}
    if name in ("copilot", "opencode"):
        return {"additionalContext": message} if canonical == "SessionStart" and message else {}
    if not message:
        return {}
    if canonical == "PreCompact":
        return {"systemMessage": message} if name != "claude" else {}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}
