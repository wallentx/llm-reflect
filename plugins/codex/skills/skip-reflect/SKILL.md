---
name: skip-reflect
description: Discard the current project's pending Reflect queue when the user explicitly requests skipping queued learnings.
---

# Discard pending learnings

Resolve `../../scripts/reflect.py` relative to this SKILL.md directory. Determine
the current project, then run `queue --project <absolute-project> --format text` to show the
count. For an explicit request to discard all, run
`python3 <absolute-script> clear --project <absolute-project> --all`.
For a selected subset, use `--ids ID ...` instead. If the user's intent or scope
is unclear, ask before discarding. The CLI backs up removed entries first.
Read the queue's text report again and report the removed and remaining counts. Do not edit
AGENTS.md, skills, applied learnings, or any other project's queue.
