"""Shared storage and transcript analysis; upstream owns language detection."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

import memory_adapters
from vendor import reflect_utils as upstream
import providers


def codex_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()


def state_home():
    if "REFLECT_HOME" in os.environ:
        return Path(os.environ["REFLECT_HOME"]).expanduser().resolve() / providers.current()
    if providers.current() == "codex":
        return Path(os.environ.get("CODEX_REFLECT_HOME", str(codex_home() / "reflect"))).expanduser().resolve()
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base.expanduser().resolve() / "reflect" / providers.current()


def project_path(project=None):
    return Path(project or os.getcwd()).expanduser().resolve()


def project_state(project=None):
    canonical = os.path.normcase(str(project_path(project)))
    return state_home() / "projects" / hashlib.sha256(canonical.encode()).hexdigest()[:24]


def queue_path(project=None):
    return project_state(project) / "queue.json"


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def queue_lock(project=None):
    directory = project_state(project)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / ".queue.lock"
    deadline = time.monotonic() + 2
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Queue busy; retry. A crashed writer may have left .queue.lock.")
            time.sleep(0.02)
    try:
        yield
    finally:
        lock.unlink()


def load_queue(project=None):
    path = queue_path(project)
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or any(not isinstance(i, dict) or not i.get("id") for i in items):
        raise ValueError("Invalid queue; restore a backup before writing: " + str(path))
    return items


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def queue_review(project=None):
    now = datetime.now(timezone.utc)
    rows = []
    for item in load_queue(project):
        date = timestamp(item.get("timestamp"))
        age = max(0, (now - date).days) if date else None
        rows.append(dict(item, age_days=age,
                         stale=age is None or age > item.get("decay_days", 90)))
    return rows


def discard(project=None, ids=None):
    """Remove only reviewed IDs; an explicit None means discard the full queue."""
    with queue_lock(project):
        items = load_queue(project)
        kept = [i for i in items if i["id"] not in ids] if ids is not None else []
        if len(kept) != len(items):
            backup_queue(project, items)
            atomic_json(queue_path(project), kept)
        return len(items) - len(kept)


def backup_queue(project=None, items=None):
    items = load_queue(project) if items is None else items
    if not items:
        return None
    path = project_state(project) / "backups" / (uuid.uuid4().hex + ".json")
    atomic_json(path, items)
    return path


# This is defense in depth, not a claim that regex can sanitize arbitrary history.
# Human/model screening is required before any material reaches a learning auditor.
def redact(text):
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
                  "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"(?i)\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{12,}|github_pat_[\w]{12,}|AKIA[A-Z0-9]{16})\b",
                  "[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*)(?:Bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
                  r"\1[REDACTED]", text)
    return re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)


def user_text(text):
    if not isinstance(text, str) or not upstream.should_include_message(text.strip()):
        return False
    return not text.lstrip().startswith(("# AGENTS.md instructions", "# Context from my IDE",
                                        "<environment_context>", "<permissions instructions>"))


def capture(prompt, project, session_id="", turn_id=""):
    if not user_text(prompt):
        return None
    if len(prompt) > upstream.MAX_CAPTURE_PROMPT_LENGTH and "remember:" not in prompt.lower():
        return None
    kind, patterns, confidence, sentiment, decay = upstream.detect_patterns(prompt)
    if not kind:
        return None
    safe = redact(prompt)
    item = upstream.create_queue_item(safe, kind, patterns, confidence, sentiment, decay,
                                      str(project_path(project)))
    identity = "\0".join((session_id, turn_id, safe)) if turn_id else uuid.uuid4().hex
    item.update(id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                session_id=session_id, turn_id=turn_id, provider=providers.current())
    with queue_lock(project):
        items = load_queue(project)
        if any(i["id"] == item["id"] for i in items):
            return None
        atomic_json(queue_path(project), items + [item])
    return item


def jsonl(path):
    """Ignore malformed individual records, including a partially written tail."""
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def session_metadata(path):
    for index, record in enumerate(jsonl(path)):
        payload = record.get("payload", {})
        if record.get("type") == "session_meta" and isinstance(payload, dict):
            return dict(payload, timestamp=record.get("timestamp", payload.get("timestamp")))
        if index >= 20:
            break
    return {}


def session_files(project=None, all_projects=False, days=None):
    if providers.current() != "codex":
        from history import native_session_files
        return native_session_files(project, all_projects, days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    result = []
    for directory in (codex_home() / "sessions", codex_home() / "archived_sessions"):
        for path in directory.rglob("*.jsonl"):
            if path.is_symlink():
                continue
            try:
                if cutoff and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                    continue
                meta = session_metadata(path)
                cwd = meta.get("cwd")
                if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                    continue
                # Subagent prompts are instructions generated by an agent, not user feedback.
                source = meta.get("source")
                if isinstance(source, dict) and "subagent" in source:
                    continue
                if not all_projects and project_path(cwd) != project_path(project):
                    continue
                result.append(path)
            except OSError:
                continue
    return sorted(result, key=lambda p: str(p))


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(i.get("text", "") for i in content
                         if isinstance(i, dict) and i.get("type") in ("input_text", "text")
                         and isinstance(i.get("text"), str))
    return ""


def output_text(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, dict):
            return output_text(parsed)
        return value
    if isinstance(value, dict):
        return output_text(value.get("output", value.get("content", "")))
    if isinstance(value, list):
        return "\n".join(output_text(v) if not isinstance(v, dict) or "text" not in v
                         else str(v["text"]) for v in value)
    return ""


def exit_code(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return exit_code(parsed)
        match = re.search(r"(?:Process exited with code|exit code[: ]|exit_code[\"': ]+)\s*(-?\d+)", value, re.I)
        return int(match.group(1)) if match else None
    if isinstance(value, dict):
        code = value.get("exit_code", value.get("metadata", {}).get("exit_code")
                         if isinstance(value.get("metadata"), dict) else None)
        return code if isinstance(code, int) else exit_code(value.get("output", ""))
    return None


def session_records(path, days=None):
    """Normalize public user text and function outputs; never treat assistant text as corrections."""
    if providers.current() != "codex":
        from history import claude_records
        yield from claude_records(path, days)
        return
    has_events = any(r.get("type") == "event_msg" and isinstance(r.get("payload"), dict)
                     and r["payload"].get("type") == "user_message" for r in jsonl(path))
    meta = session_metadata(path)
    project = meta.get("cwd", "")
    session = meta.get("id", Path(path).stem)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    skill = None
    for index, record in enumerate(jsonl(path)):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        date = timestamp(record.get("timestamp"))
        if record.get("type") == "turn_context" and isinstance(payload.get("cwd"), str):
            project = payload["cwd"]
        if cutoff and (date is None or date < cutoff):
            continue
        text = ""
        kind = None
        if record.get("type") == "event_msg" and payload.get("type") == "user_message":
            text, kind = payload.get("message", ""), "user"
        elif record.get("type") == "response_item":
            if payload.get("type") == "message" and payload.get("role") == "user" and not has_events:
                text, kind = content_text(payload.get("content")), "user"
            elif payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                value = payload.get("output", "")
                text = output_text(value)
                rejected = re.search(r"user (?:rejected|denied|cancelled)|the user doesn't want to proceed", text, re.I)
                if rejected:
                    kind = "rejection"
                    feedback = re.search(r"(?:the user said:|user feedback:)\s*(.+)", text, re.I | re.S)
                    text = feedback.group(1) if feedback else ""
                elif exit_code(value) not in (None, 0) or text.startswith(("Error:", "Error executing", "Traceback (")):
                    kind = "tool_error"
        if kind == "user" and user_text(text):
            invoked = re.match(r"^\s*(?:\$|/)([\w:-]+)", text)
            if invoked:
                skill = invoked.group(1)
        elif kind == "user":
            continue
        if kind and text and isinstance(text, str):
            yield {"kind": kind, "message": redact(text), "project": project,
                   "session_id": session, "timestamp": record.get("timestamp"),
                   "record": index, "skill": skill}


def scan(project=None, all_projects=False, days=30, corrections_only=False, include_tool_errors=False,
         history_files=None):
    rows = []
    seen = set()
    if history_files:
        from history import imported_records
        sources = ((path, imported_records(path, days)) for path in history_files)
    else:
        if providers.PROVIDERS[providers.current()]["history"] == "import":
            raise ValueError("Native history is unavailable for this provider; use --history FILE (normalized JSONL), or review queue.")
        sources = ((path, session_records(path, days)) for path in session_files(project, all_projects, days))
    for path, records in sources:
        for row in records:
            if not all_projects and project_path(row["project"]) != project_path(project):
                continue
            identity = (row["session_id"], row["record"], row["kind"])
            if identity in seen:
                continue
            seen.add(identity)
            kind, patterns, confidence, sentiment, decay = upstream.detect_patterns(row["message"])
            if row["kind"] == "tool_error":
                if not include_tool_errors:
                    continue
                if any(re.search(p, row["message"], re.I) for p in upstream.TOOL_ERROR_EXCLUDE_PATTERNS):
                    continue
                for error_type, pattern, guideline in upstream.PROJECT_SPECIFIC_ERROR_PATTERNS:
                    if re.search(pattern, row["message"], re.I):
                        row.update(error_type=error_type, suggested_guideline=guideline)
                        break
                else:
                    continue
            elif corrections_only and not kind and row["kind"] != "rejection":
                continue
            row.update(provider=providers.current(), type=kind, patterns=patterns, confidence=confidence,
                       sentiment=sentiment, decay_days=decay)
            rows.append(row)
    return rows


def claude_memory_directory(project=None):
    """Compatibility entry point; the Claude memory adapter owns path resolution."""
    return memory_adapters.claude_directory(project)


def targets(project=None, max_depth=3, max_nodes=100, memory_dirs=None):
    """Discover provider guidance and skills, plus bounded referenced Markdown."""
    root = project_path(project)
    home = providers.provider_home()
    name = providers.current()
    spec = providers.PROVIDERS[name]
    global_skills = Path.home() / ".agents/skills"
    allowed = [root, home, global_skills.resolve(), providers.skill_home()]
    if name == "antigravity":
        allowed.append((Path.home() / ".gemini").resolve())
    found, seen = [], set()

    def add(path, kind, active=True, **extra):
        resolved = path.resolve()
        if resolved in seen or (path.exists() and not path.is_file()):
            return
        if not any(resolved == base or base in resolved.parents for base in allowed):
            return
        seen.add(resolved)
        found.append(dict(path=str(resolved), type=kind, exists=path.exists(), active=active, **extra))

    def guidance(directory, kind):
        override = directory / "AGENTS.override.md"
        normal = directory / spec["instructions"]
        has_override = name == "codex" and override.is_file()
        if has_override:
            add(override, kind)
        if normal.is_file() or kind in ("global", "root"):
            add(normal, kind, active=not has_override)
        if name == "claude" and (directory / "AGENTS.md").is_file():
            # Actual loading depends on Claude version and instruction-file settings.
            add(directory / "AGENTS.md", "cross-agent", active=False, loading="conditional")

    # Cursor's user rules live in its UI, not ~/.cursor/AGENTS.md.
    if name == "copilot":
        add(home / "copilot-instructions.md", "global")
    elif name != "cursor":
        guidance(Path.home() / ".gemini" if name == "antigravity" else home, "global")
    guidance(root, "root")
    excluded = upstream.EXCLUDED_DIRS | {".codex", ".claude", ".agents", ".cursor", ".gemini", ".opencode", ".github", ".agent"}
    for directory, dirs, _ in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (Path(directory) / d).is_symlink())
        if Path(directory) != root:
            guidance(Path(directory), "subdirectory")
    directories = [root / spec["project_skills"], providers.skill_home()]
    if name in ("codex", "cursor", "gemini", "opencode", "copilot"):
        directories.extend((root / ".agents/skills", global_skills))
    if name == "codex":
        directories.append(root / ".codex/skills")
    for directory in directories:
        for path in sorted(directory.glob("*/SKILL.md")):
            add(path, "skill")
    if name in ("claude", "cursor"):
        for directory in (root / ("." + name) / "rules", home / "rules"):
            for pattern in ("*.md", "*.mdc"):
                for path in sorted(directory.rglob(pattern)):
                    add(path, "rule")
    if name == "claude":
        add(root / "CLAUDE.local.md", "local")
    if name == "copilot":
        add(root / ".github/copilot-instructions.md", "root")
        for directory in (root / ".github/instructions", home / "instructions"):
            for path in sorted(directory.rglob("*.instructions.md")):
                add(path, "rule")
    for item in memory_adapters.discover(root, memory_dirs):
        path = Path(item["path"])
        if path.resolve() in seen:
            # Keep guidance precedence/type while adding native memory capabilities.
            existing = next(row for row in found if row["path"] == str(path.resolve()))
            existing.update({key: item[key] for key in ("memory", "scope", "writable", "provider")})
            continue
        allowed.append(path.parent.resolve())
        add(path, item["type"], active=item["type"] == "instruction-memory",
            **{key: value for key, value in item.items() if key not in ("path", "type", "active", "exists")})
    for path in sorted((project_state(project) / "staging").glob("*.md")):
        # Staging is not automatically loaded as instructions by Codex.
        if state_home() not in allowed:
            allowed.append(state_home())
        add(path, "staging", active=False)
    pending = deque((Path(f["path"]), 0) for f in found if f["exists"] and f["type"] not in ("staging", "memory-draft"))
    count = 0
    while pending and count < max_nodes:
        source, depth = pending.popleft()
        if depth >= max_depth:
            continue
        for raw in upstream._parse_inclusions(source):
            path = upstream._resolve_inclusion(raw, source, allowed)
            if path is None or path in seen:
                continue
            add(path, "referenced", active=False, referenced_from=str(source), depth=depth + 1)
            pending.append((path, depth + 1))
            count += 1
            if count >= max_nodes:
                break
    return found


def memory_entries(project=None, memory_dirs=None, memory_only=False):
    rows = []
    for target in targets(project, memory_dirs=memory_dirs):
        if not target["exists"] or target["type"] == "memory-draft" or (memory_only and not target.get("memory")):
            continue
        text = upstream._read_text_capped(Path(target["path"]))
        if text is None:
            continue
        entries = (memory_adapters.markdown_entries(text) if target.get("memory") else
                   ((number, line.strip()[2:]) for number, line in enumerate(text.splitlines(), 1)
                    if line.strip().startswith("- ")))
        for number, entry in entries:
            if entry:
                rows.append(dict(text=entry, source_file=target["path"], source_type=target["type"], line_number=number))
    return rows
