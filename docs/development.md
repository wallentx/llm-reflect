# LLM Reflect development

LLM Reflect (`wallentx/llm-reflect`) provides reviewed learning workflows for
Codex, Claude Code, Cursor, Gemini CLI, OpenCode, Copilot CLI, and Antigravity CLI.
See [README.md](../README.md) for the current product and the [provider guide](providers.md)
for each adapter's capabilities and installation paths.

## Maintained source

| Path | Responsibility |
|---|---|
| `reflect/` | Maintained runtime, memory/history adapters, skills and review references for all providers |
| `providers/codex/` and `providers/claude/` | Native plugin manifests and hook definitions |
| `packages/reflect/` | Generated standalone runtime for all providers |
| `plugins/codex/` and `plugins/claude/` | Generated bundles for their native plugin hosts |
| `tools/` | Package generation, standalone installation and upstream sync |

The root `scripts/`, `commands/`, `hooks/`, `SKILL.md` and plugin manifest retain
the upstream Claude implementation in its original paths for synchronization.

Edit `reflect/` or `providers/` and regenerate; do not hand-edit generated packages or installed
plugin caches. Detection/utilities and semantic prompts are copied from upstream
verbatim. Changes to upstream inputs require parity review of the adapters.

## Branches and identity

`dev` contains maintained LLM Reflect changes. `main` is reserved for upstream
synchronization. Keep the user's current branch; the sync helper requires an
existing clean `dev` checkout and never switches branches, commits, or pushes.

The command is still `reflect`. Plugin selectors remain
`codex-reflect@codex-reflect-marketplace` and `reflect@reflect-marketplace`.
Keep existing queue paths, environment variables and native plugin IDs
compatible when reorganizing the repository. Package directories describe their
contents; they are separate from provider-owned plugin IDs.

## Runtime boundaries

Hooks only capture detector candidates and provide supported reminders/backups.
They never invoke a model or apply guidance. The shared workflow requires sanitized
auditor evidence, independent review, and subsequent explicit user approval before
applying learning proposals. Missing independent agents means proposal-only output.

Codex and Claude have native history readers. Other providers use explicit
normalized JSONL imports; do not guess at undocumented history databases or use
another provider's sessions. Only Codex supports subprocess semantic analysis.
Use `paths` and `targets` for provider-specific destinations.

## Validation and release checks

```sh
python3 tools/build_packages.py
uv run --no-project --with pytest python -m pytest tests -q
python3 tools/build_packages.py --check
git diff --check
claude plugin validate plugins/claude
```

The upstream version in `.claude-plugin/plugin.json` is the base for generated
packages. Content-derived versions change when shared code or native metadata
changes; keep existing native plugin IDs and queue paths compatible.

Before publishing, smoke-test the installer and selected native plugins in
isolated profiles. Check that repeated setup preserves settings and removal
retains queues. CI covers Linux, macOS and Windows with Python 3.11-3.14.

Tests use temporary homes and synthetic history. Keep fixtures isolated from the
operator's history, configuration, queues, and credentials. Do not clear a live
queue to test capture or removal. Native IDE hook execution requires separate
provider/runtime validation; fixture success alone does not establish it.

The provider TUI must keep confirmation and cancellation read-only until applied,
restore terminal state on every exit path, and preserve explicit-provider CLI
usage for scripts. The standalone installer must preserve unrelated settings and hooks, refuse
unmanaged or edited files, and keep `--dry-run` read-only. Preserve backups and
ownership tracking when changing registration/removal behavior.

See the [Codex guide](codex.md) for Codex-specific usage and
[upstream release history](upstream/changelog.md) for the inherited version history.

## Repeatable upstream sync

`main` belongs to upstream synchronization. Maintained LLM Reflect work belongs on
`dev`. Upstream code stays in its original paths; `reflect/` is the maintained
shared runtime and skills. `packages/reflect/` and `plugins/` are generated and committed so marketplace
installs do not need a build step. Edit `reflect/` or `providers/`, not generated files.

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
adds shared code, provider metadata and the license, records input hashes, and takes its version
from the upstream manifest with content-derived package suffixes (native edits also
invalidate the install cache). CI checks generated bytes and
tests both implementations on Linux, macOS, and Windows. A parity-contract test
flags new upstream commands, options, hooks, and scripts for adapter review.
Changes to existing command prose still require human semantic review; the sync
helper lists changed upstream inputs to make this visible.

```bash
python3 tools/build_packages.py
python3 -m pytest tests -q
python3 tools/build_packages.py --check
git diff HEAD
```

The original commands, hooks, scripts, and root plugin manifest remain the
upstream Claude implementation. The [README](../README.md) describes LLM Reflect's
shared runtime and provider adapters. See the native
[reflect skill](../reflect/skills/reflect/SKILL.md) for the shared review workflow.
