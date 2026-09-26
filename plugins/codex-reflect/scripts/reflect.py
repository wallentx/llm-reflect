#!/usr/bin/env python3
"""LLM Reflect: provider-aware correction capture and reviewed learning helpers."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
sys.dont_write_bytecode = True
import providers

from reflect_core import (backup_queue, capture, codex_home, discard,
                          exit_code, load_queue, memory_entries, project_path,
                          project_state, queue_path, queue_review, scan, session_files,
                          targets, upstream)


def hook(event, data):
    if os.environ.get("REFLECT_DISABLED", os.environ.get("CODEX_REFLECT_DISABLED")) == "1" or not isinstance(data, dict):
        return
    original_event = event
    event, data = providers.normalize_hook(event, data)
    if event is None:
        return
    # Never infer a project from a transcript's parent or the hook process cwd.
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return
    message = None
    if event == "UserPromptSubmit":
        item = capture(data.get("prompt"), cwd, str(data.get("session_id", "")), str(data.get("turn_id", "")))
        if item:
            message = "[reflect] Correction queued for review. Run $reflect when ready. Capture is not approval."
    elif event == "PreCompact":
        path = backup_queue(cwd)
        if path:
            message = "[reflect] Learning queue backed up. Run $reflect to review."
    elif event == "SessionStart":
        if os.environ.get("REFLECT_REMINDER", os.environ.get("CODEX_REFLECT_REMINDER", "true")).lower() == "false":
            return
        count = len(load_queue(cwd))
        if count:
            message = "[reflect] {} pending learning(s). Run $reflect or $view-queue.".format(count)
    elif event == "PostToolUse":
        arguments = data.get("tool_input", {})
        if not isinstance(arguments, dict):
            return
        command = arguments.get("command", arguments.get("cmd", ""))
        if isinstance(command, list):
            command = " ".join(str(v) for v in command)
        if not isinstance(command, str) or "--amend" in command:
            return
        # Canonical hook name is Bash; cmd is accepted for direct CLI fixtures.
        if not re.search(r"(?:^|[;&|\s])git\s+(?:(?:-C\s+\S+|-c\s+\S+)\s+)*commit(?:\s|$)", command):
            return
        if exit_code(data.get("tool_response")) != 0:
            return
        count = len(load_queue(cwd))
        message = "[reflect] Git commit completed; {} queued learning(s). Run $reflect to review.".format(count)
    if message and providers.current() != "codex":
        message = message.replace("$reflect", "/reflect").replace("$view-queue", "/view-queue")
    output = providers.hook_output(original_event, message)
    if output:
        print(json.dumps(output))


def main():
    upstream.ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(providers.PROVIDERS), default=providers.default_provider())
    commands = parser.add_subparsers(dest="action", required=True)
    from installer import add_init_parser, initialize
    add_init_parser(commands)
    hooks = commands.add_parser("hook", help="Read a native provider hook event from stdin")
    hooks.add_argument("event")
    capture_parser = commands.add_parser("capture", help="Capture a correction from stdin (no model call)")
    capture_parser.add_argument("--project", required=True)
    capture_parser.add_argument("--session-id", default="manual")
    for action in ("paths", "queue", "targets", "entries", "clear", "scan", "compare", "contradictions"):
        sub = commands.add_parser(action)
        sub.add_argument("--project", default=None)
        if action == "clear":
            choose = sub.add_mutually_exclusive_group(required=True)
            choose.add_argument("--all", action="store_true", help="Explicitly discard every pending item")
            choose.add_argument("--ids", nargs="+", help="Only remove these reviewed item IDs")
        if action in ("scan", "compare"):
            sub.add_argument("--history", action="append", help="Read normalized JSONL instead of native history (repeatable)")
            sub.add_argument("--days", type=int, default=30)
            sub.add_argument("--all-projects", action="store_true")
            sub.add_argument("--corrections-only", action="store_true")
            sub.add_argument("--include-tool-errors", action="store_true")
        if action in ("queue", "scan", "compare", "contradictions"):
            sub.add_argument("--model", default=None)
            sub.add_argument("--semantic", action="store_true", help="Invoke ephemeral Codex analysis (may incur model usage)")
    args = parser.parse_args()
    providers.select(args.provider)
    if args.action in ("init", "uninstall"):
        try:
            return initialize(args)
        except (ValueError, OSError, RuntimeError) as exc:
            print("[reflect " + args.action + "] " + str(exc), file=sys.stderr)
            return 1
    if args.action == "capture":
        item = capture(sys.stdin.read(), args.project, args.session_id)
        print(json.dumps({"queued": bool(item), "id": item["id"] if item else None}))
        return 0
    if args.action == "hook":
        try:
            hook(args.event, json.load(sys.stdin))
        except Exception as exc:
            # Never let reflection prevent a user prompt, commit, or compaction.
            # Do not include exception text: it may contain fragments of corrupt data.
            print("[reflect] Hook failed ({}); queue was not intentionally cleared.".format(type(exc).__name__), file=sys.stderr)
        return 0
    project = args.project
    if (getattr(args, "semantic", False) or args.action == "compare") and providers.current() != "codex":
        parser.error("This provider uses reasoning in the current conversation; subprocess semantic analysis is Codex-only")
    if args.action == "paths":
        data = {"project": str(project_path(project)), "codex_home": str(codex_home()),
                "state_dir": str(project_state(project)), "queue": str(queue_path(project)),
                "session_files": [str(p) for p in session_files(project)],
                "staging": str(project_state(project) / "staging"),
                "audit": str(project_state(project) / "audit"), "provider": providers.details()}
    elif args.action == "targets":
        data = targets(project)
    elif args.action == "entries":
        data = memory_entries(project)
    elif args.action == "clear":
        data = {"removed": discard(project, set(args.ids) if args.ids else None)}
    elif args.action == "queue":
        data = queue_review(project)
        if args.semantic:
            from semantic import validate_queue_items
            data = validate_queue_items(data, model=args.model)
    elif args.action == "contradictions":
        if not args.semantic:
            parser.error("contradictions requires --semantic; use entries for an offline review")
        from semantic import detect_contradictions
        data = detect_contradictions([e["text"] for e in memory_entries(project)], model=args.model)
    else:
        if args.days < 1:
            parser.error("--days must be positive")
        if not args.history and providers.PROVIDERS[providers.current()]["history"] == "import":
            parser.error("Native history is unavailable for " + providers.current() +
                         "; pass --history FILE (normalized JSONL), or use queue")
        data = scan(project, args.all_projects, args.days, args.corrections_only, args.include_tool_errors, args.history)
        if args.semantic or args.action == "compare":
            from semantic import semantic_analyze
            for row in data:
                row["semantic"] = semantic_analyze(row["message"], model=args.model)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        # Argument/capability errors can be useful, but parsing errors may contain
        # private queue data. Keep the generic failure boundary deliberately terse.
        print("[reflect] {}: operation failed; no queue discard performed. Check provider capabilities and input format.".format(type(exc).__name__), file=sys.stderr)
        sys.exit(1)
