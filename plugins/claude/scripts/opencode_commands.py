"""Protect global inline OpenCode commands from file-based command overrides."""
import json
from pathlib import Path
import re


def _jsonc(text):
    """Remove JSONC comments and trailing commas while preserving string data."""
    characters = list(text)
    quoted = escaped = False
    index = 0
    while index < len(characters):
        character = characters[index]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            index += 1
            continue
        if character == '"':
            quoted = True
        elif character == "/" and index + 1 < len(characters):
            following = characters[index + 1]
            if following == "/":
                end = index + 2
                while end < len(characters) and characters[end] not in "\r\n":
                    end += 1
                characters[index:end] = " " * (end - index)
                index = end
                continue
            if following == "*":
                end = text.find("*/", index + 2)
                if end == -1:
                    raise ValueError("Unterminated JSONC comment")
                end += 2
                characters[index:end] = ["\n" if item == "\n" else " " for item in characters[index:end]]
                index = end
                continue
        index += 1

    quoted = escaped = False
    for index, character in enumerate(characters):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character == ",":
            following = index + 1
            while following < len(characters) and characters[following] in " \t\r\n":
                following += 1
            if following < len(characters) and characters[following] in "}]":
                characters[index] = " "
    def reject_constant(value):
        raise ValueError("Invalid JSON constant")

    return json.loads("".join(characters), parse_constant=reject_constant)


def check_inline_commands(config_home, names):
    """Reject existing global inline commands that generated files would shadow."""
    names = set(names)
    for filename in ("config.json", "opencode.json", "opencode.jsonc"):
        path = Path(config_home) / filename
        if not path.exists():
            continue
        try:
            configuration = _jsonc(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            raise ValueError("Cannot safely inspect OpenCode configuration: " + str(path)) from None
        if not isinstance(configuration, dict):
            raise ValueError("Expected an OpenCode configuration object: " + str(path))
        if any(re.search(r"\{(?:env|file):[^}]+\}", key) for key in configuration):
            raise ValueError("Cannot inspect dynamic OpenCode configuration keys: " + str(path))
        for key in ("command", "commands"):
            commands = configuration.get(key, {})
            if not isinstance(commands, dict):
                raise ValueError("Expected an OpenCode " + key + " object: " + str(path))
            if any(re.search(r"\{(?:env|file):[^}]+\}", name) for name in commands):
                raise ValueError("Cannot inspect dynamic OpenCode command names: " + str(path))
            conflicts = sorted(names.intersection(commands))
            if conflicts:
                raise ValueError("Existing OpenCode command /" + conflicts[0] + " in " + str(path))
