"""Exact, reviewable native-memory changes with stale-plan checks and backups."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

import memory_adapters as adapters
import providers
import reflect_core as core


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_path(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Refusing symlink memory destination")
    if path.exists() and not path.is_file():
        raise ValueError("Memory destination is not a file")


def identity(plan):
    return digest(json.dumps({key: value for key, value in plan.items() if key != "approval"},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def create_note(path, content):
    """Create a native note without ever replacing an existing filename."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not hasattr(os, "link"):
        # Android Python may omit hard links. Match the native create-new contract.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        owned = os.fstat(fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            current = path.stat() if path.exists() else None
            if current and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                path.unlink()
            raise
        return
    fd, temporary = tempfile.mkstemp(prefix=".reflect-note-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def prepare(content, project=None, scope=None, filename=None):
    if not isinstance(content, str) or not content.strip() or len(content.encode()) > 1024 * 1024:
        raise ValueError("Memory content must be nonempty UTF-8 text of at most 1 MiB")
    if providers.current() == "codex" and filename is None:
        filename = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-reflect-note.md")
    dest = adapters.destination(project, scope, filename)
    before = None
    if dest["path"] is not None:
        path = Path(dest["path"])
        check_path(path)
        if path.exists() and path.stat().st_size > 1024 * 1024:
            raise ValueError("Existing memory exceeds 1 MiB; review it with the provider's native tools")
        before = path.read_bytes() if path.exists() else None
        if dest["operation"] == "create" and before is not None:
            raise ValueError("Native ad-hoc notes cannot replace an existing note")
    plan = {"schema": 1, "provider": providers.current(), "project": str(core.project_path(project)),
            "scope": dest["scope"], "filename": filename, "path": dest["path"],
            "operation": dest["operation"], "before_sha256": digest(before) if before is not None else None,
            "content": content}
    diff = difflib.unified_diff(
        before.decode("utf-8").splitlines(keepends=True) if before is not None else [], content.splitlines(keepends=True),
        fromfile=dest["path"] or "native-memory", tofile=dest["path"] or "native-memory")
    plan["diff"] = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
    if dest["path"] is None:
        plan["status"] = "requires-native-tool"
        plan["instruction"] = ("Read existing memory with the provider's native tool, review the exact change, "
                               "then apply it with that tool in the current provider conversation. "
                               "This CLI cannot access the remote store.")
    else:
        plan["status"] = "ready-for-review"
    plan["approval"] = identity(plan)
    return plan


def apply(plan, approval):
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("provider") != providers.current():
        raise ValueError("Memory plan belongs to another provider or has an invalid schema")
    if not approval or approval != plan.get("approval") or approval != identity(plan):
        raise ValueError("Approval must identify the exact reviewed memory plan")
    required = {"project", "scope", "filename", "path", "operation", "before_sha256", "content", "diff", "status"}
    if (not required <= plan.keys() or not isinstance(plan["project"], str) or not Path(plan["project"]).is_absolute()
            or not isinstance(plan["scope"], str) or not isinstance(plan["content"], str)
            or not isinstance(plan["diff"], str) or not isinstance(plan["status"], str)
            or not isinstance(plan["operation"], str)
            or (plan["filename"] is not None and not isinstance(plan["filename"], str))
            or (plan["path"] is not None and not isinstance(plan["path"], str))
            or (plan["before_sha256"] is not None and not isinstance(plan["before_sha256"], str))):
        raise ValueError("Invalid memory plan fields")
    project = plan["project"]
    current = prepare(plan["content"], project, plan["scope"], plan["filename"])
    if current["path"] is None:
        raise RuntimeError("Apply this memory change with native tools in the current provider conversation")
    if current["approval"] != approval:
        raise RuntimeError("Memory destination or content changed since review; prepare and review a new plan")
    path = Path(current["path"])
    check_path(path)
    before = path.read_bytes() if path.exists() else None
    # Backup the exact bytes before replacing them. Inspection and prepare never write.
    from installer import atomic_write
    record = core.project_state(project) / "memory-backups" / uuid.uuid4().hex
    check_path(record / "change.json")
    check_path(record / "before.md")
    if before is not None:
        atomic_write(record / "before.md", before)
    core.atomic_json(record / "change.json", current)
    check_path(path)
    if (digest(path.read_bytes()) if path.exists() else None) != current["before_sha256"]:
        raise RuntimeError("Memory changed during application; backup retained, destination not overwritten")
    if current["operation"] == "create":
        create_note(path, current["content"].encode("utf-8"))
    else:
        atomic_write(path, current["content"].encode("utf-8"))
    return {"applied": True, "provider": providers.current(), "path": str(path), "backup": str(record)}
