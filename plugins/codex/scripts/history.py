"""Native Claude history and an explicit, provider-neutral JSONL import contract.

Other providers' private databases are deliberately not guessed at. Imports are
read-only inputs, never copied into a queue or interpreted as agent instructions.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import providers


def native_session_files(project, all_projects, days):
    from reflect_core import jsonl, project_path
    if providers.current() != "claude":
        return []
    result = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    for path in sorted((providers.provider_home() / "projects").glob("*/*.jsonl")):
        if path.is_symlink() or path.name.startswith("agent-"):
            continue
        if cutoff and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
            continue
        for row in jsonl(path):
            cwd = row.get("cwd")
            if row.get("isSidechain"):
                break
            if isinstance(cwd, str) and Path(cwd).is_absolute():
                if all_projects or project_path(cwd) == project_path(project):
                    result.append(path)
                break
    return result


def claude_records(path, days):
    from reflect_core import content_text, jsonl, redact, timestamp, user_text
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    cwd, session, skill = None, Path(path).stem, None
    for index, row in enumerate(jsonl(path)):
        if row.get("isSidechain"):
            continue
        if isinstance(row.get("cwd"), str) and Path(row["cwd"]).is_absolute():
            cwd = row["cwd"]
        session = row.get("sessionId", session)
        date = timestamp(row.get("timestamp"))
        message = row.get("message", {})
        if not cwd or not isinstance(message, dict) or (cutoff and (not date or date < cutoff)):
            continue
        if row.get("type") != "user" or message.get("role") != "user" or row.get("isMeta"):
            continue
        content = message.get("content")
        text = content_text(content)
        if user_text(text):
            import re
            invoked = re.match(r"^\s*(?:\$|/)([\w:-]+)", text)
            if invoked:
                skill = invoked.group(1)
            yield {"kind": "user", "message": redact(text), "project": cwd,
                   "session_id": session, "timestamp": row.get("timestamp"), "record": index, "skill": skill}
        if isinstance(content, list):
            for part_index, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("is_error"):
                    text = content_text(part.get("content"))
                    if text:
                        yield {"kind": "tool_error", "message": redact(text), "project": cwd,
                               "session_id": session, "timestamp": row.get("timestamp"),
                               "record": "{}:{}".format(index, part_index), "skill": skill}


def imported_records(path, days):
    """Require explicit project, provider, session, role and date for every record."""
    import json
    from reflect_core import redact, timestamp, user_text
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    with Path(path).open(encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("History import must contain JSON objects")
            required = ("provider", "project", "session_id", "id", "timestamp", "role", "text")
            if any(not isinstance(row.get(key), str) or not row[key] for key in required):
                raise ValueError("History import requires provider/project/session_id/id/timestamp/role/text")
            if row["provider"] != providers.current() or not Path(row["project"]).is_absolute():
                raise ValueError("History import provider or absolute project mismatch")
            date = timestamp(row["timestamp"])
            if date is None:
                raise ValueError("Invalid history timestamp")
            if cutoff and date < cutoff:
                continue
            # Tool/assistant/system text cannot become human preference evidence.
            if row["role"] != "user" or not user_text(row["text"]):
                continue
            yield {"kind": "user", "message": redact(row["text"]), "project": row["project"],
                   "session_id": row["session_id"], "timestamp": row["timestamp"],
                   "record": row["id"], "skill": None}
