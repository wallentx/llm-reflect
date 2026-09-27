"""Read-only inspection and bounded, terminal-safe output for the Reflect CLI."""
from collections import Counter, defaultdict
import math
from pathlib import Path

import providers
import reflect_core as core


def context(project=None):
    return {"project": str(core.project_path(project)), "provider": providers.details(),
            "state_dir": str(core.project_state(project)), "queue": str(core.queue_path(project)),
            "staging": str(core.project_state(project) / "staging"),
            "audit": str(core.project_state(project) / "audit")}


def target_summary(rows, reference_limit=100):
    groups = defaultdict(lambda: Counter())
    for row in rows:
        if row["type"] == "skill":
            path = Path(row["path"])
            directory = path.parent.parent
            groups[str(directory)][row["type"]] += 1
    references = sum(row["type"] == "referenced" for row in rows)
    return {"total": len(rows), "existing": sum(row["exists"] for row in rows),
            "counts": dict(Counter(row["type"] for row in rows)),
            "guidance": [row for row in rows if row["type"] not in ("skill", "referenced")],
            "skill_directories": [{"path": path, **dict(counts)} for path, counts in sorted(groups.items())],
            "references": {"count": references, "limit": reference_limit,
                           "limit_reached": references >= reference_limit}}


def status(project=None, memory_dirs=None):
    data = context(project)
    queue = core.queue_review(project)
    data["queue_summary"] = {"total": len(queue), "stale": sum(row["stale"] for row in queue),
                             "types": dict(Counter(row.get("type", "unknown") for row in queue))}
    data["targets"] = target_summary(core.targets(project, memory_dirs=memory_dirs))
    data["memory"] = data["provider"]["memory"]
    data["memory"]["sources"] = sum(row.get("memory", False) for row in data["targets"]["guidance"])
    data["memory"]["pending"] = data["targets"]["counts"].get("memory-draft", 0)
    return data


def clean(value, width=None):
    text = "".join(char if char.isprintable() else " " for char in str(value))
    return text if width is None or len(text) <= width else text[:max(0, width - 3)] + "..."


def table(headers, rows):
    rows = [[clean(value) for value in row] for row in rows]
    widths = [max([len(header)] + [len(row[index]) for row in rows]) for index, header in enumerate(headers)]
    return [" | ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()
            for row in [headers, ["-" * width for width in widths], *rows]]


def confidence(value):
    return "{:.0%}".format(value) if isinstance(value, (int, float)) and math.isfinite(value) else "?"


def target_lines(summary, limit):
    rows = []
    for row in summary["guidance"][:limit]:
        state = "missing" if not row["exists"] else (
            "pending" if row["type"] == "memory-draft" else
            "conditional" if row.get("loading") == "conditional" else
            "active" if row["active"] else "overridden" if row["type"] in ("global", "root", "subdirectory")
            else "supporting")
        rows.append((row["type"], state, row["path"]))
    lines = ["Targets: {} locations, {} existing".format(summary["total"], summary["existing"])]
    lines.extend(table(["Type", "Status", "Path"], rows))
    if len(summary["guidance"]) > limit:
        lines.append("{} more guidance files; increase --limit or use --format json.".format(len(summary["guidance"]) - limit))
    if summary["skill_directories"]:
        lines.extend(["", *table(["Skills", "Directory"], [
            (row.get("skill", 0), row["path"])
            for row in summary["skill_directories"][:limit]])])
        if len(summary["skill_directories"]) > limit:
            lines.append("{} more skill directories; increase --limit or use --format json.".format(len(summary["skill_directories"]) - limit))
    refs = summary["references"]
    lines.append("Referenced documents: {} (supporting only){}".format(
        refs["count"], "; discovery limit reached" if refs["limit_reached"] else ""))
    return lines


def render(action, data, project=None, limit=20):
    details = data if action == "status" else context(project)
    lines = ["Provider: " + clean(details["provider"]["id"]), "Project: " + clean(details["project"])]
    if action in ("status", "paths"):
        values = data if action == "paths" else details
        lines.extend("{}: {}".format(key.title(), clean(values[key])) for key in ("queue", "staging", "audit"))
        provider = details["provider"]
        lines.append("History: {}; automatic capture: {}; model helper: {}".format(
            provider["history"], "yes" if provider["automatic_capture"] else "no",
            "yes" if provider["semantic_cli"] else "no"))
        if action == "status":
            queue = data["queue_summary"]
            memory = data["memory"]
            lines.append("Memory: {}; {} local sources; {} pending drafts; writes: {}".format(
                memory["feature"], memory["sources"], memory["pending"], memory["write"]))
            lines.extend(["Pending corrections: {} ({} stale)".format(queue["total"], queue["stale"]), "",
                          *target_lines(data["targets"], limit)])
        elif "session_files" in data:
            lines.append("Session files: {}".format(len(data["session_files"])))
    elif action == "targets":
        lines.extend(["", *target_lines(target_summary(data), limit)])
    elif action == "memory":
        if "adapters" in data:
            lines.extend(table(["Provider", "Storage", "Writes", "Scopes"], [
                (row["provider"], row["storage"], row["write"], ", ".join(row["scopes"])) for row in data["adapters"]]))
        else:
            lines.extend(["Memory: " + clean(data["feature"]), "Access: " + clean(data["access"]),
                          "Writes: " + clean(data["write"]), clean(data["detail"]),
                          "Local sources: {} ({} pending drafts)".format(len(data["sources"]), data["pending"])])
            lines.extend(table(["Type", "Scope", "Writable", "Path"], [
                (row["type"], row["scope"], "yes" if row["writable"] else "no", row["path"])
                for row in data["sources"][:limit]]))
            if len(data["sources"]) > limit:
                lines.append("{} more sources; increase --limit or use --format json.".format(len(data["sources"]) - limit))
    elif action == "memory-plan":
        lines.extend(["Status: " + clean(data["status"]), "Approval: " + data["approval"],
                      data.get("instruction", ""), "\n".join(clean(line) for line in data["diff"].splitlines())])
    elif action == "memory-apply":
        lines.extend(["Applied: " + clean(data["path"]), "Backup: " + clean(data["backup"])])
    elif action in ("queue", "entries", "scan", "compare"):
        lines.extend(["", "{}: {} records".format(action.title(), len(data))])
        if action == "queue":
            headers = ["ID", "Confidence", "Age", "Status", "Learning"]
            rows = [(row["id"], confidence(row.get("confidence")),
                     "?" if row.get("age_days") is None else str(row["age_days"]) + "d",
                     "stale" if row.get("stale") else "pending", clean(row.get("extracted_learning", row.get("message", "")), 100))
                    for row in data[:limit]]
            if any("semantic_status" in row for row in data):
                headers.insert(4, "Semantic")
                rows = [(*row[:4], item.get("semantic_status", "not run"), row[4])
                        for row, item in zip(rows, data[:limit])]
        elif action == "entries":
            headers = ["Type", "Source", "Line", "Entry"]
            rows = [(row["source_type"], row["source_file"], row["line_number"], clean(row["text"], 100))
                    for row in data[:limit]]
        else:
            headers = ["Project", "Session", "Kind", "Skill", "Message"]
            rows = [(row["project"], row["session_id"], row["kind"], row.get("skill") or "-", clean(row["message"], 100))
                    for row in data[:limit]]
            lines.append("Sessions: {} | Projects: {}".format(
                len({(row["project"], row["session_id"]) for row in data}), len({row["project"] for row in data})))
            if action == "compare" or any("semantic" in row for row in data):
                headers.insert(4, "Regex / Semantic")
                rows = [(*row[:4], "{} / {}".format(
                    item.get("type") or "none",
                    "unavailable" if item.get("semantic") is None else
                    confidence(item["semantic"].get("confidence")) if item["semantic"].get("is_learning") else "none"),
                    row[4]) for row, item in zip(rows, data[:limit])]
        lines.extend(table(headers, rows))
        if len(data) > limit:
            lines.append("{} more records; increase --limit or use --format json.".format(len(data) - limit))
    elif action == "clear":
        lines.append("Removed: {} pending corrections".format(data["removed"]))
    else:
        lines.append("Semantic analysis: " + clean(data.get("status", "unknown")))
        rows = data.get("contradictions", [])
        lines.extend(table(["Entry 1", "Entry 2", "Conflict"], [
            (clean(row.get("entry1", ""), 100), clean(row.get("entry2", ""), 100), clean(row.get("conflict", ""), 100))
            for row in rows[:limit]]))
        if len(rows) > limit:
            lines.append("{} more contradictions; increase --limit or use --format json.".format(len(rows) - limit))
    return "\n".join(lines)
