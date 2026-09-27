# LLM Reflect for Codex

For the standalone installer and other coding agents, see [provider support](PROVIDERS.md).
The Codex marketplace commands and existing queues remain compatible.

A Codex marketplace plugin for correction capture, reviewed learning proposals,
history analysis, and reusable skill discovery. Based on
[claude-reflect](https://github.com/BayramAnnakov/claude-reflect), with its detection
and filtering code reused verbatim.

## Install

Requires Python 3.11+ and a Codex release supporting marketplace plugins and
`SessionStart`, `UserPromptSubmit`, `PreCompact`, and `PostToolUse` command hooks.
The local integration checks use Codex CLI 0.156.1. Ensure `python3` is on PATH;
on Windows install Python with that executable alias, or change the hook commands
in `codex_port/hooks/hooks.json` to your Python 3 executable and regenerate.

From this checkout:

```bash
python3 tools/build_codex.py --check
codex plugin marketplace add "$PWD"
codex plugin add codex-reflect@codex-reflect-marketplace
```

After the port is published to the fork's `dev` branch, the Git source is:

```bash
codex plugin marketplace add wallentx/llm-reflect --ref dev
codex plugin add codex-reflect@codex-reflect-marketplace
```

Open a new Codex session and review/trust the bundled hooks through Codex's hook
trust interface. Installing a plugin does not automatically trust its hooks.
No configuration-merging installer or Claude installation is needed. Marketplace
registration and installation above are explicit user actions; the build only
writes repository artifacts.

## Skills

Use `$reflect`, `$reflect-skills`, `$view-queue`, or `$skip-reflect` (select the
`codex-reflect` plugin's skill if another plugin has the same short name).

| Workflow | Invocation |
|---|---|
| Review captured corrections | `$reflect` |
| Read-only preview | `$reflect --dry-run` |
| Backfill history, including tool errors | `$reflect --scan-history --days 30` |
| Guidance discovery / queue decay review | `$reflect --targets` / `$reflect --review` |
| Consolidation / scope and size review | `$reflect --dedupe` / `$reflect --organize` |
| Explicit semantic model | `$reflect --model <codex-model>` |
| Include repeated tool failures | `$reflect --include-tool-errors` |
| Discover recurring workflows | `$reflect-skills --days 14` |
| Discover across projects | `$reflect-skills --all-projects --dry-run` |
| Inspect / discard pending items | `$view-queue` / `$skip-reflect` |

Hooks queue candidates only. The skills screen and aggregate evidence, use a
read-only learning auditor and independent reviewer, then request user approval
of the exact reviewed diff before an implementer changes persistent guidance.
Auditors never receive session-history files. REVISE requires another review.
If independent agents are unavailable, the workflow remains proposal-only.
This is an agent workflow gate, not an OS security boundary.

## Feature mapping

| Claude feature | Codex implementation |
|---|---|
| Automatic corrections, praise, explicit markers, guardrails | Same upstream detector through a native UserPromptSubmit hook |
| Session / post-commit reminders and compaction backups | Native JSON hook responses; successful commits only |
| Per-project queues, review, decay, discard | Collision-resistant canonical project keys, atomic locked writes, ID-selective removal and backups |
| Historical correction / rejection / error extraction | Current and archived Codex JSONL; event_msg and response_item formats; project and date filters |
| Semantic validation and multilingual analysis | Current Codex conversation, or opt-in ephemeral `codex exec`; no Claude CLI |
| Regex vs semantic diagnostic comparison | `reflect.py compare` |
| Dedupe, contradiction checks, size/scope organization | Native reflect skill plus entries/contradictions helpers |
| Skill discovery and improvement routing | Native SKILL.md workflows; per-record skill context; project/global assignment |
| Memory hierarchy and referenced docs | AGENTS.md, existing AGENTS.override.md, scoped instructions, skills, bounded Markdown links |
| Native memory / promotion | Read consolidated memory and native notes; reviewed changes create native ad-hoc notes, never rewrite generated memory |

Claude's `.claude/rules/*.md`, `CLAUDE.local.md`, and command Markdown are not Codex
formats. Use scoped AGENTS.md or skills instead. Codex `.rules` files are execution
policy, not prose memory. Linked Markdown is discovered for review, not implicitly
loaded by Codex. Existing override files take precedence over AGENTS.md in the
same directory. Installed plugin caches are not editable learning targets.

## Local data and diagnostics

`CODEX_HOME` defaults to `~/.codex`; state defaults to `$CODEX_HOME/reflect` and may
be redirected with `CODEX_REFLECT_HOME`. Each canonical project gets an isolated
queue, backups, audit directory, and optional staging directory. Hooks use the
payload's `cwd`, never the transcript's date directory. `CODEX_REFLECT_DISABLED=1`
disables all capture/reminders; `CODEX_REFLECT_REMINDER=false` disables the startup
reminder only. Hooks never call a model or write active guidance.

```bash
python3 tools/reflect.py status
python3 tools/reflect.py memory
python3 tools/reflect.py queue --format text
python3 tools/reflect.py targets --format text
python3 tools/reflect.py scan --days 30 --include-tool-errors --format text
python3 tools/reflect.py compare --days 14 --format text
```

The checkout launcher preserves your working directory; use its absolute path
from another project or pass `--project PATH`. Installed runtimes provide the
same commands through `reflect`. Text output groups skills and bounds rows with
`--limit N` (default 20); `--format json` returns complete data. `status` avoids
session-history enumeration; `paths` still lists session files when needed.
See [memory adapters](PROVIDERS.md#memory-adapters) for versioned memory roots,
native note creation, and exact reviewed change plans.

Scan and queue inspection do not mutate data. `compare` and `--semantic` run Codex
and can incur model usage; failures are explicitly marked unavailable. Queue
messages and extracted history are local evidence, not a sanitized auditor cache.
Basic credential redaction is defense in depth; the coordinator must screen and
generalize evidence before handing it to learning agents. Explicit rejection
feedback is extracted when recorded; bare approval denials are not invented
preferences. Session formats are not a stable Codex API, so parser fixtures and
future compatibility checks are necessary. Corrupt queues are never silently
replaced. A crashed writer may leave `.queue.lock`; verify no writer is running
before removing that lock manually.

## Repeatable upstream sync

`main` belongs to upstream synchronization. All Codex maintenance belongs on
`dev`. Upstream code stays in its original paths; `codex_port/` is the maintained
native overlay. `plugins/codex-reflect/` is generated and committed so marketplace
installs do not need a build step. Edit the overlay, not generated files.

```bash
# On dev, after main has been updated from upstream:
python3 tools/sync_upstream.py --fetch
# With a clean dev worktree, merge origin/main and validate, leaving it uncommitted:
python3 tools/sync_upstream.py --apply
```

Use `--source main` if the sync commit is on local main. The helper never updates
main, switches branches, resets work, commits, or pushes. It refuses dirty trees,
other branches, and an existing merge/rebase. Conflicts remain for manual
resolution; after resolving, run the build and tests below before committing.
Use `--fetch --apply` to refresh origin/main and apply in one invocation.

Sync validation requires pytest in the selected Python environment. If it is not
installed, the helper stops before merging. An isolated invocation is:

```bash
uv run --no-project --with pytest python tools/sync_upstream.py --fetch --apply
```

The generator copies upstream detection/utilities and semantic prompts verbatim,
adds the Codex overlay and license, records input hashes, and takes its version
from the upstream manifest with a content-derived Codex suffix (native edits also
invalidate the install cache). CI checks generated bytes and
tests both implementations on Linux, macOS, and Windows. A parity-contract test
flags new upstream commands, options, hooks, and scripts for adapter review.
Changes to existing command prose still require human semantic review; the sync
helper lists changed upstream inputs to make this visible.

```bash
python3 tools/build_codex.py
python3 tools/build_providers.py
python3 -m pytest tests -q
python3 tools/build_codex.py --check
python3 tools/build_providers.py --check
git diff HEAD
```

The original commands, hooks, scripts, and root plugin manifest remain the
upstream Claude implementation. The [README](README.md) describes LLM Reflect's
shared runtime and provider adapters. See the native
[reflect skill](codex_port/skills/reflect/SKILL.md) for Codex behavior.

## Runtime references

The adapter follows the official [plugin packaging](https://developers.openai.com/plugins/build/plugins),
[Claude conversion](https://developers.openai.com/plugins/guides/submit-claude-plugin),
and [Codex hook](https://learn.chatgpt.com/docs/hooks) documentation. Hook definitions
require user trust and must be present in the execution environment.
