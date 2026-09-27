"""Provider memory discovery and destinations; never initializes native storage."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import unicodedata

import providers
from vendor import reflect_utils as upstream


ADAPTERS = {
    "codex": {"feature": "Managed memories", "storage": "local", "write": "ad-hoc-note",
              "scopes": ["global"], "detail": "Read consolidated memory; add native ad-hoc notes, never rewrite generated files."},
    "claude": {"feature": "Auto memory", "storage": "local", "write": "file",
               "scopes": ["private", "project", "global"], "detail": "Repository memory, including configured autoMemoryDirectory."},
    "gemini": {"feature": "Private project memory and GEMINI.md", "storage": "local", "write": "file",
               "scopes": ["private", "project", "global"], "detail": "Project registry/ownership markers and legacy SHA-256 directories; inbox drafts stay pending."},
    "cursor": {"feature": "Cursor Memories", "storage": "native-tool", "write": "native-tool",
               "scopes": ["project"], "detail": "Memories are managed by Cursor; use native memory tools or an explicit saved copy."},
    "copilot": {"feature": "Copilot Memory", "storage": "native-tool", "write": "native-tool",
                "scopes": ["project", "global"], "detail": "Repository facts and user preferences are managed by Copilot; use native tools or a saved copy."},
    "antigravity": {"feature": "Persistent rules", "storage": "instructions", "write": "file",
                    "scopes": ["project", "global"], "detail": "AGY CLI exposes rule files; no documented local knowledge-item interface is assumed."},
    "opencode": {"feature": "Persistent instructions", "storage": "instructions", "write": "file",
                 "scopes": ["project", "global"], "detail": "AGENTS.md; third-party memory plugins need their own adapter."},
}


def details(name=None):
    name = name or providers.current()
    return dict(ADAPTERS[name], provider=name, cli_read=ADAPTERS[name]["storage"] != "native-tool",
                cli_write=ADAPTERS[name]["write"] != "native-tool")


def project_path(project=None):
    return Path(project or os.getcwd()).expanduser().resolve()


def read_json(path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Memory configuration exceeds 1 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid memory configuration")
    return data


def repository_root(project=None, shared=False):
    root = project_path(project)
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}}
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute",
                                 "--show-toplevel", "--git-common-dir"],
                                capture_output=True, text=True, encoding="utf-8", timeout=2,
                                env=environment, check=False)
        paths = result.stdout.splitlines()
        if result.returncode == 0 and len(paths) == 2 and Path(paths[0]).is_absolute():
            candidate = Path(paths[0]).resolve()
            if candidate == root or candidate in root.parents:
                root = candidate
                common = Path(paths[1]).resolve()
                if shared and common.name == ".git" and common.is_dir():
                    root = common.parent
    except (OSError, subprocess.TimeoutExpired):
        pass
    return root


def within(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return path == root or root in path.parents


def safe_file(path, root):
    path, root = Path(path), Path(root)
    return (path.is_file() and within(path, root)
            and not any(part.is_symlink() for part in (path, *path.parents)))


def marker_owner(path):
    if path.stat().st_size > 4096:
        raise ValueError("Memory ownership marker exceeds 4 KiB")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw or not Path(raw).is_absolute():
        raise ValueError("Invalid memory ownership marker")
    return project_path(raw)


def claude_directory(project=None):
    root, home = project_path(project), providers.provider_home("claude")
    chosen = None
    for path in (home / "settings.json", root / ".claude/settings.json", root / ".claude/settings.local.json"):
        value = read_json(path).get("autoMemoryDirectory")
        if not isinstance(value, str) or not value:
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValueError("autoMemoryDirectory must be absolute or start with ~/")
        # Repository settings cannot redirect a read to an arbitrary user directory.
        if path.parent == home or within(candidate, root) or within(candidate, home):
            chosen = candidate.absolute()
        else:
            raise ValueError("External autoMemoryDirectory in project settings requires an explicit --memory-dir")
    if chosen is not None:
        return chosen
    folder = os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME")
    if folder and Path(folder).name == folder and folder not in (".", ".."):
        return home / "projects" / folder / "memory"
    canonical = unicodedata.normalize("NFC", str(repository_root(root, shared=True)))
    folder = upstream._encode_project_path(canonical)
    if len(folder) > upstream.MAX_PROJECT_FOLDER_NAME_LEN:
        folder = folder[:upstream.MAX_PROJECT_FOLDER_NAME_LEN] + "-" + upstream._long_name_hash(canonical)
    return home / "projects" / folder / "memory"


def codex_directory():
    home = providers.provider_home("codex")
    config = home / "config.toml"
    if config.is_file():
        if config.stat().st_size > 1024 * 1024:
            raise ValueError("Memory configuration exceeds 1 MiB")
        with config.open("rb") as stream:
            memory = tomllib.load(stream).get("memories", {})
        if not isinstance(memory, dict):
            raise ValueError("Invalid Codex memories configuration")
        version = memory.get("version", "v1")
        if version not in ("v1", "v2"):
            raise ValueError("Unsupported Codex memory version")
        if version == "v2":
            return home / "memories_v2"
    return home / "memories"


def gemini_directories(project=None):
    project = project_path(project)
    home = providers.provider_home("gemini")
    runtime = Path.home() / ".cache/.gemini" if os.environ.get("SANDBOX") == "sandbox-exec" else home
    temp = runtime / "tmp"
    registered = read_json(runtime / "projects.json").get("projects", {})
    if not isinstance(registered, dict):
        raise ValueError("Invalid Gemini project registry")
    matches = []
    for key, slug in registered.items():
        if not isinstance(key, str) or not Path(key).is_absolute() or not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError("Invalid Gemini project registry entry")
        owner = Path(key).resolve()
        if within(project, owner):
            matches.append((len(owner.parts), owner, slug))
    directories = []
    if matches:
        _, owner, slug = max(matches, key=lambda row: row[0])
        marker = temp / slug / ".project_root"
        if marker.is_file() and (not safe_file(marker, temp) or marker_owner(marker) != owner):
            raise ValueError("Gemini memory ownership marker disagrees with registry")
        directories.append(temp / slug / "memory")
    else:
        # Registry recovery reads ownership markers only; never initializes/migrates it.
        for marker in sorted(temp.glob("*/.project_root")):
            if not safe_file(marker, temp):
                continue
            if marker_owner(marker) == project:
                directories.append(marker.parent / "memory")
    for owner in (project, repository_root(project)):
        directory = temp / hashlib.sha256(str(owner).encode()).hexdigest() / "memory"
        if directory not in directories:
            directories.append(directory)
    return directories


def source(path, root, scope="private", kind="auto-memory", writable=True, **extra):
    if not safe_file(path, root):
        return None
    return dict(path=str(Path(path).absolute()), type=kind, exists=True, active=False,
                memory=True, scope=scope, writable=writable, provider=providers.current(), **extra)


def discover(project=None, memory_dirs=None):
    name, project = providers.current(), project_path(project)
    home = providers.provider_home()
    found = []

    def add(path, root, **extra):
        row = source(path, root, **extra)
        if row is not None and row["path"] not in {item["path"] for item in found}:
            found.append(row)

    if memory_dirs:
        for directory in memory_dirs:
            root = Path(directory).expanduser().absolute()
            for path in sorted(root.glob("*.md")):
                add(path, root, writable=False, scope="imported", kind="imported-memory")
    elif name == "claude":
        directory = claude_directory(project)
        for path in sorted(directory.glob("*.md")):
            add(path, directory)
    elif name == "codex":
        directory = codex_directory()
        for filename in ("MEMORY.md", "memory_summary.md"):
            add(directory / filename, directory, scope="global", writable=False, kind="managed-memory")
        for path in sorted((directory / "extensions/ad_hoc/notes").glob("*.md")):
            add(path, directory, scope="global", writable=False, kind="memory-note")
        for path in sorted((directory / "skills").glob("*/SKILL.md")):
            add(path, directory, scope="global", writable=False, kind="managed-memory")
    elif name == "gemini":
        for directory in gemini_directories(project):
            for path in sorted(directory.glob("*.md")):
                add(path, directory)
            for path in sorted((directory / "skills").glob("*/SKILL.md")):
                add(path, directory, kind="memory-draft", writable=False, scope="pending")
            for pattern in ("*.patch", "*/*.patch"):
                for path in sorted((directory / "skills").glob(pattern)):
                    add(path, directory, kind="memory-draft", writable=False, scope="pending", format="patch")
            for kind in ("private", "global"):
                for path in sorted((directory / ".inbox" / kind).glob("*.patch")):
                    add(path, directory, kind="memory-draft", writable=False, scope="pending", format="patch")
    # Instruction-backed memory is still a destination on every provider.
    for path in (project / providers.PROVIDERS[name]["instructions"],
                 home / ("copilot-instructions.md" if name == "copilot" else providers.PROVIDERS[name]["instructions"])):
        if name in ("cursor", "antigravity") and path.parent == home:
            continue
        add(path, path.parent, kind="instruction-memory", scope="project" if path.parent == project else "global",
            writable=name not in ("codex", "cursor", "copilot"))
    if name == "claude":
        add(project / ".claude/CLAUDE.md", project, kind="instruction-memory", scope="project")
    if name == "antigravity":
        root = Path.home() / ".gemini"
        for directory in (root, root / "config", project / ".agents"):
            for filename in ("AGENTS.md", "GEMINI.md"):
                add(directory / filename, directory, kind="instruction-memory", scope="global" if within(directory, root) else "project")
        for directory in (root / "config/rules", project / ".agents/rules", project / ".agent/rules"):
            for path in sorted(directory.glob("*.md")):
                add(path, directory, kind="instruction-memory", scope="global" if within(directory, root) else "project")
    if name == "cursor":
        for path in sorted((project / ".cursor/rules").rglob("*.mdc")):
            add(path, project, kind="instruction-memory", scope="project")
    if name == "copilot":
        add(project / ".github/copilot-instructions.md", project, kind="instruction-memory", scope="project")
    return found


def inspect(project=None, memory_dirs=None):
    result = details()
    result.update(project=str(project_path(project)), sources=discover(project, memory_dirs))
    result["pending"] = sum(row["scope"] == "pending" for row in result["sources"])
    result["access"] = "saved-copy" if memory_dirs else result["storage"]
    return result


def markdown_entries(text):
    """Skip metadata, headings, comments, and code; retain prose with original lines."""
    frontmatter = text.startswith("---\n") or text.startswith("---\r\n")
    fence, comment = None, False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if frontmatter:
            if number > 1 and stripped in ("---", "..."):
                frontmatter = False
            continue
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence:
            continue
        if "<!--" in stripped:
            comment = True
        if comment:
            if "-->" in stripped:
                comment = False
            continue
        if not stripped or stripped.startswith("#") or stripped in ("---", "***", "___"):
            continue
        entry = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", stripped).strip()
        if entry:
            yield number, entry


def destination(project=None, scope=None, filename=None):
    name, project, home = providers.current(), project_path(project), providers.provider_home()
    adapter = details()
    scope = scope or ("private" if name in ("claude", "gemini") else "global" if name == "codex" else "project")
    if scope not in adapter["scopes"]:
        raise ValueError("Unsupported memory scope for " + name)
    if adapter["write"] == "native-tool":
        return dict(adapter, scope=scope, path=None, operation="native-memory-write")
    if filename is not None and name != "codex":
        raise ValueError("--filename is only supported for Codex ad-hoc notes")
    if name == "codex":
        if not filename or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[a-z0-9-]{1,80}\.md", filename):
            raise ValueError("Codex notes require YYYY-MM-DDTHH-MM-SS-<slug>.md")
        path = codex_directory() / "extensions/ad_hoc/notes" / filename
    elif scope == "private":
        path = (claude_directory(project) if name == "claude" else gemini_directories(project)[0]) / "MEMORY.md"
    elif scope == "global":
        path = (Path.home() / ".gemini/GEMINI.md" if name == "antigravity" else
                home / providers.PROVIDERS[name]["instructions"])
    else:
        path = project / providers.PROVIDERS[name]["instructions"]
    return dict(adapter, scope=scope, path=str(path.absolute()), operation="create" if name == "codex" else "replace")
