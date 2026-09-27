# LLM Reflect provider installation

LLM Reflect shares detection, redaction, queues, review gates, and guidance discovery
across the seven provider IDs used by Panoptes. Runtime dependencies: Python 3.11+
and the selected coding agent. OpenCode loads its small JavaScript adapter using
its own runtime. No Python packages, model API keys, or build tools are required.

## Install from a checkout

```sh
sh ./install.sh
~/.local/bin/reflect init
```

`install.sh` copies the runtime to `~/.local/share/reflect`, creates
`~/.local/bin/reflect`, and opens the provider TUI when stdin/stderr are terminals.
`reflect init` opens it again. Installed providers start checked. Use Up/Down or
j/k to move, Space to toggle, and Enter to review changes. Checked providers are
installed/updated; unchecking an installed provider schedules its removal.
The review screen defaults to Cancel; choose Apply or press y to confirm.
Esc/q cancels without provider changes. Provider changes are written only after
confirmation.

`reflect uninstall` (also `reflect init --remove`) opens a removal picker with
installed integrations initially unchecked. Only checked integrations are removed
after confirmation. Native Codex/Claude marketplace installs made outside the
standalone installer are detected too. Upstream `claude-reflect` installs have an
orange `⁕` and a footer legend. In the setup picker, an untouched upstream row
keeps upstream, unchecking removes it, and rechecking replaces it with LLM Reflect.
In the uninstall picker, check the row to remove it. User-scope registrations and
project/local registrations for the current directory are detected; other
projects are left alone. The runtime, plugin data, and learning queues remain
installed; upstream queues are not imported into LLM Reflect.

Explicit `--provider` commands are additive and do not open the picker. Unattended
setup requires those flags. Use `install.sh --no-configure` for runtime-only setup;
noninteractive bootstrap runs do not open the TUI.
An explicit `init --provider claude` replaces detected upstream registrations;
`uninstall --provider claude` removes them. Use `--dry-run` to preview either.

Use `reflect` directly if `~/.local/bin` is already on PATH. `--prefix DIR` changes
the installation prefix. On Windows use `python tools/install.py` and invoke the
resulting `reflect.cmd`. On Termux use `pkg install python` if Python is absent.

```sh
# Preview all filesystem changes and marketplace commands without executing them.
~/.local/bin/reflect init --provider codex --provider gemini --dry-run

# Register several providers; repeated IDs are applied once.
~/.local/bin/reflect init --provider codex --provider cursor --provider opencode

# Explicitly choose direct skills/hooks rather than marketplace packaging.
~/.local/bin/reflect init --provider claude --method local

# Open the removal picker, or inspect status / remove a named integration.
~/.local/bin/reflect uninstall
~/.local/bin/reflect init --list
~/.local/bin/reflect uninstall --provider cursor
```

`--method auto` (default) uses marketplaces for Codex/Claude and local integration
for other providers. `--method marketplace` rejects providers without a packaged
marketplace adapter. Both methods keep the agent's hook trust controls intact;
restart the agent and review its hook trust prompt after installation.

## Capabilities

| Provider ID | Automatic capture | History scan | Installation |
|---|---|---|---|
| `codex` | Prompt, startup/commit reminders, compaction backup | Native current/archive JSONL | Codex marketplace or local skills/hooks |
| `claude` | Prompt, startup/commit reminders, compaction backup | Native primary-session JSONL | Claude marketplace or local skills/hooks |
| `cursor` | Prompt, startup reminder, compaction backup | Explicit normalized JSONL import | Local skills/hooks |
| `gemini` | BeforeAgent, startup reminder, compaction backup | Explicit normalized JSONL import | Local skills/hooks |
| `opencode` | User chat messages through JavaScript plugin | Explicit normalized JSONL import | Local skills/plugin |
| `copilot` | Prompt, startup reminder, compaction backup | Explicit normalized JSONL import | Local skills/hooks |
| `antigravity` | Manual capture | Explicit normalized JSONL import | Local skills; CLI paths match Panoptes |

All providers get `reflect`, `reflect-skills`, `view-queue`, and `skip-reflect`
skills, queue inspection, selective discard with backups, and guidance discovery.
Discovery and reflection still work from the queue or current conversation when
native historical scanning is unavailable. Workflow discovery across older
sessions requires native history or an explicit export; Reflect does not parse
undocumented IDE databases or silently scan another provider's history.

The current conversation performs semantic reasoning. Only the Codex adapter
supports the optional `compare`/`--semantic` subprocess helpers. Other adapters
reject those flags rather than invoking Codex or another model behind the scenes.
Independent audit/review agents and explicit approval remain required to apply
learning proposals. Providers without independent agents remain proposal-only.

## Native installation locations

| Provider | Global skill directory | Capture configuration |
|---|---|---|
| Codex | `$CODEX_HOME/skills` (default `~/.codex/skills`) | `$CODEX_HOME/hooks.json` |
| Claude | `$CLAUDE_CONFIG_DIR/skills` (default `~/.claude/skills`) | `$CLAUDE_CONFIG_DIR/settings.json` |
| Cursor | `~/.cursor/skills` | `~/.cursor/hooks.json` |
| Gemini | `~/.gemini/skills` | `~/.gemini/settings.json` |
| OpenCode | `$XDG_CONFIG_HOME/opencode/skills` (default `~/.config/opencode/skills`) | Same config root, `plugins/reflect.js` |
| Copilot | `~/.copilot/skills` | `~/.copilot/settings.json` |
| Antigravity CLI | `~/.gemini/antigravity-cli/skills` | None |

Each installed skill binds its helper commands to its provider. Do not copy a
bound skill between providers. Use the installer for each provider instead.
Cursor workspaces with multiple roots require a hook payload with an explicit
`cwd`; ambiguous prompts are skipped rather than assigned to an arbitrary root.
The Antigravity entry targets the CLI layout used by Panoptes, not a claim of
automatic integration with every Antigravity desktop release.

## Marketplace packages

The repository is `wallentx/llm-reflect`. The `reflect` command, plugin IDs,
marketplace IDs, and state directories keep their existing names so installed
integrations and queues continue to work after the repository rename.

The Codex marketplace remains `codex-reflect@codex-reflect-marketplace`. The
reviewed Claude adapter is `reflect@reflect-marketplace`:

```sh
codex plugin marketplace add "$PWD"
codex plugin add codex-reflect@codex-reflect-marketplace

claude plugin marketplace add "$PWD"
claude plugin install reflect@reflect-marketplace --scope user
```

The fork's Claude marketplace has a distinct name from the original upstream
`claude-reflect-marketplace`. The original plugin remains available as
`claude-reflect@reflect-marketplace`; it retains upstream behavior and storage.
Use only one Reflect integration per provider to avoid duplicate captures.
Local setup refuses existing LLM Reflect marketplace installations. To change
methods, remove the existing integration first, then install the desired one.
Upstream Claude Reflect can be replaced with either installation method.

Marketplace installation delegates to the provider CLI and retains the checkout
as the local marketplace source. Keep that checkout available for upgrades.
An already registered marketplace keeps its configured source. External CLI
operations cannot be rolled back as one transaction; a failure identifies the
provider and leaves local registration writes unapplied. Rerun to reconcile.
Cursor has a marketplace and Gemini has extensions, but this fork currently
ships their direct integrations only; no published listing is implied.

## Data and explicit history imports

Codex retains its existing `$CODEX_REFLECT_HOME` / `$CODEX_HOME/reflect` state.
Other providers use `$XDG_STATE_HOME/reflect/<provider>` (default
`~/.local/state/reflect/<provider>`). `REFLECT_HOME` overrides the shared state
root, with a separate `<provider>` child. Projects retain collision-resistant
canonical path keys. Installation never migrates or clears existing queues.
The original Claude plugin's queue is not silently migrated to the new adapter.

```sh
reflect --provider gemini paths --project "$PWD"
reflect --provider gemini queue --project "$PWD"
printf '%s\n' 'remember: run focused tests before deployment' |
  reflect --provider antigravity capture --project "$PWD" --session-id manual-example
reflect --provider cursor scan --project "$PWD" --history ./history.jsonl --days 30
```

The normalized JSONL import is an explicit interchange format, not a claim that
provider exports already match it. Each record requires these string fields:

```json
{"provider":"cursor","project":"/absolute/project","session_id":"session-1","id":"message-1","timestamp":"2026-09-26T12:00:00Z","role":"user","text":"no, run tests before deployment"}
```

Provide stable message IDs within each session. The importer verifies provider,
absolute project, timestamp, and fields; only user messages become evidence.
It filters project/date and ignores assistant/system/tool messages. `--history`
is repeatable and replaces native scanning for that invocation. It never writes
the queue. Add `--all-projects` only when cross-project inspection is intended.
Hooks store detector candidates, not a new log of every prompt. No hook calls a
model or writes active guidance. `REFLECT_DISABLED=1` disables hooks and
`REFLECT_REMINDER=false` disables startup reminders; the legacy Codex variables
continue to work. Capture is not approval to change instructions.

## Upgrade, ownership, and validation

Rerun `install.sh` followed by `reflect init --provider ...` to refresh runtime
and integrations. The installer preserves unrelated JSON values and hook entries,
deduplicates its own registrations, and refuses unmanaged or edited skill files,
modified managed hooks, malformed configuration, and symlink destinations.
Backups and the ownership registry live under `$XDG_STATE_HOME/reflect`.
`uninstall` / `--remove` only remove recorded, unchanged local files/hooks or the
selected native marketplace plugin; queues and unrelated
provider installations remain. Interrupted setup may leave `installation.lock`;
confirm no installer is running before removing a stale lock manually.

Source lives in `codex_port/`; the name is retained for downstream sync stability.
Both marketplace packages are generated. After editing the overlay:

```sh
python3 tools/build_codex.py
python3 tools/build_providers.py
uv run --no-project --with pytest python -m pytest tests -q
python3 tools/build_codex.py --check
python3 tools/build_providers.py --check
```

Tests use synthetic histories and isolated homes, plus POSIX pseudo-terminals to
verify arrow/Space input, confirmation, cancellation, and restoration of terminal
settings. Native agent UI behavior still
needs testing in each installed provider/version; passing fixtures is not proof
of a live IDE hook execution.

Provider contracts: [Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Cursor hooks](https://cursor.com/docs/hooks),
[Gemini hooks](https://geminicli.com/docs/hooks/reference/),
[OpenCode plugins](https://opencode.ai/docs/plugins/),
[OpenCode hook types](https://github.com/anomalyco/opencode/blob/dev/packages/plugin/src/index.ts),
[Copilot hooks](https://docs.github.com/en/copilot/reference/hooks-reference), and
[Copilot instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions).
