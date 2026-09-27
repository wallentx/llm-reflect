---
name: reflect-skills
description: Discover recurring workflows in provider sessions and propose new or improved reusable skills with independent review and explicit approval.
---

# Discover skills

Read [the reviewed learning workflow](../../references/review-workflow.md).
Use semantic reasoning to find repeated intent across different phrasings and
languages, not just keyword counts. Support `--days N` (default 14),
`--project <path>`, `--all-projects`, `--history FILE`, and `--dry-run`.
Read `status` first. When native history is unavailable, request an explicit
normalized JSONL export and pass `--history FILE`; do not substitute another
provider's history.

1. Resolve the bundled CLI. Run `scan --project <project> --days N`, or explicitly
   add `--all-projects` when requested. Also run `targets` for each candidate
   project to discover existing skills. Use complete JSON for analysis; use
   `scan --format text` for a bounded report with project/session counts, without
   inline Python. Preserve project and session identity;
   unrelated projects with the same folder name must never be merged.
2. Identify workflows repeated in at least two independent sessions. Group
   equivalent intent, inputs, sequence, outputs, and recurring corrections.
   Examples are repeated release checks, API audits, or data reporting. Reject
   one-off tasks, generic advice, private details, and guesses. Prepare sanitized
   aggregates for the learning auditor; do not delegate raw session data.
3. Compare each candidate with existing skills. Prefer an improvement to an
   existing skill where appropriate. Show candidate ID, independent-session
   count, purpose, trigger, inputs/outputs, workflow, guardrails, and proposed
   project/global scope. Include learned corrections only when independently
   supported. Propose the exact SKILL.md and any necessary helper/resource files.
4. Obtain independent reviewer APPROVE and subsequent explicit user approval
   for the exact files using the shared workflow. Ask for scope only if unclear.
   `--dry-run` stops at a read-only proposal. Do not create draft skill files in
   auto-loaded directories before approval.
5. The approved implementer writes to the selected provider's project or global
   skill directory returned by `status --format json`, validates frontmatter, resource paths,
   and helper tests, then records the audit outcome. Report the provider-native
   invocation. Never modify installed plugin caches; use the owning source.
