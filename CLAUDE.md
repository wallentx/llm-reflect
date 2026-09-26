# LLM Reflect development

LLM Reflect (`wallentx/llm-reflect`) provides reviewed learning workflows for
Codex, Claude Code, Cursor, Gemini CLI, OpenCode, Copilot CLI, and Antigravity CLI.
See [README.md](README.md) for the current product and [PROVIDERS.md](PROVIDERS.md)
for each adapter's capabilities and installation paths.

## Maintained source

| Path | Responsibility |
|---|---|
| `codex_port/scripts/` | Shared queue/history helpers, provider registry, hooks, installer |
| `codex_port/skills/` and `codex_port/references/` | Review workflows and provider-aware skills |
| `tools/` | Builders, standalone installation, upstream sync |
| `plugins/codex-reflect/` and `plugins/claude-reflect/` | Generated marketplace packages |
| `scripts/`, `commands/`, `hooks/`, root `SKILL.md` and root plugin manifest | Retained upstream Claude implementation |

Edit the overlay and regenerate; do not hand-edit generated packages or installed
plugin caches. Detection/utilities and semantic prompts are copied from upstream
verbatim. Changes to upstream inputs require parity review of the adapters.

## Branches and identity

`dev` contains maintained LLM Reflect changes. `main` is reserved for upstream
synchronization. Keep the user's current branch; the sync helper requires an
existing clean `dev` checkout and never switches branches, commits, or pushes.

The command is still `reflect`. Plugin selectors remain
`codex-reflect@codex-reflect-marketplace` and `reflect@reflect-marketplace`.
Keep existing queue paths, environment variables, and package directory names
compatible when changing repository metadata.

## Runtime boundaries

Hooks only capture detector candidates and provide supported reminders/backups.
They never invoke a model or apply guidance. The shared workflow requires sanitized
auditor evidence, independent review, and subsequent explicit user approval before
applying learning proposals. Missing independent agents means proposal-only output.

Codex and Claude have native history readers. Other providers use explicit
normalized JSONL imports; do not guess at undocumented history databases or use
another provider's sessions. Only Codex supports subprocess semantic analysis.
Use `paths` and `targets` for provider-specific destinations.

## Validation

```sh
python3 tools/build_codex.py
python3 tools/build_providers.py
uv run --no-project --with pytest python -m pytest tests -q
python3 tools/build_codex.py --check
python3 tools/build_providers.py --check
git diff --check
```

Tests use temporary homes and synthetic history. Keep fixtures isolated from the
operator's history, configuration, queues, and credentials. Do not clear a live
queue to test capture or removal. Native IDE hook execution requires separate
provider/runtime validation; fixture success alone does not establish it.

The provider TUI must keep confirmation and cancellation read-only until applied,
restore terminal state on every exit path, and preserve explicit-provider CLI
usage for scripts. The standalone installer must preserve unrelated settings and hooks, refuse
unmanaged or edited files, and keep `--dry-run` read-only. Preserve backups and
ownership tracking when changing registration/removal behavior.

See [CODEX.md](CODEX.md) for upstream synchronization and
[RELEASING.md](RELEASING.md) for package validation.
