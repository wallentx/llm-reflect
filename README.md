# llm-reflect

Capture corrections made to coding agents and turn repeated evidence into
reviewed updates to instructions and reusable skills.

LLM Reflect supports Codex, Claude Code, Cursor, Gemini CLI, OpenCode, Copilot CLI,
and Antigravity CLI. It combines a shared Python runtime with provider-specific
hooks, skills, history readers, and an installer. Detection and filtering build on
[claude-reflect](https://github.com/BayramAnnakov/claude-reflect).

## Install

Requires Python 3.8+ and a coding agent that supports the integration listed below.
The runtime uses the Python standard library. On Termux, install Python with
`pkg install python` if needed.

```sh
git clone --branch dev https://github.com/wallentx/llm-reflect.git
cd llm-reflect
sh ./install.sh
```

`install.sh` installs the `reflect` command under `~/.local/bin` and a standalone
runtime under `~/.local/share/reflect`, then opens the provider TUI when run in a
terminal. Use Up/Down to move, Space to toggle checkboxes, and Enter to review.
Installed providers start checked. Checked providers are installed or updated;
unchecking an installed provider removes that integration after confirmation.
Esc or q cancels. Use `reflect` directly if `~/.local/bin` is on PATH. Restart
selected agents and review hook trust prompts after setup.

The picker uses color to highlight selections and planned removals. Set
`NO_COLOR=1` to use plain text, for example `NO_COLOR=1 reflect init`.

An orange `⁕` marks an installed upstream `claude-reflect` plugin, with a legend
at the bottom. It starts checked and stays unchanged until toggled. Uncheck it
to uninstall upstream; recheck it to replace upstream with LLM Reflect. Review
and confirm the changes before they run. Upstream plugin data and queues are
retained; queues are not imported into LLM Reflect.

For scripted installation, select providers explicitly:

```sh
# Preview changes without writing files or running marketplace commands.
~/.local/bin/reflect init --provider codex --provider gemini --dry-run

# Configure the selected providers.
~/.local/bin/reflect init --provider codex --provider gemini --provider opencode
```

The installer uses native marketplaces for Codex and Claude by default. Other
providers get user-level skills and their supported hook/plugin adapter.
`--method local` installs directly, `--prefix DIR` changes the runtime installation
prefix, and `reflect init --list` shows integration status. Existing settings and
unrelated hooks are preserved; locally edited managed files require review before
replacement. Explicit `--provider` commands are additive; the checkbox picker
reconciles the checked set, including removal of unchecked installed providers.

Open the TUI again, or use its dedicated uninstall picker:

```sh
~/.local/bin/reflect init
~/.local/bin/reflect uninstall
```

The uninstall picker lists installed integrations and removes only checked ones
after confirmation. Learning queues and unrelated settings are retained. Use
`sh ./install.sh --no-configure` to install only the command; scripted provider
setup and `init --remove --provider NAME` remain available.

On Windows, run `python tools/install.py` and use the installed `reflect.cmd`.
See [the provider guide](PROVIDERS.md) for paths, upgrades, removal, and imports.

## Provider support

| Provider | Correction capture | Historical scans | Installation |
|---|---|---|---|
| Codex | Native hooks | Native current/archive JSONL | Marketplace or local |
| Claude Code | Native hooks | Native primary-session JSONL | Marketplace or local |
| Cursor | Native hooks | Explicit normalized JSONL import | Local skills/hooks |
| Gemini CLI | Native hooks | Explicit normalized JSONL import | Local skills/hooks |
| OpenCode | User-message plugin | Explicit normalized JSONL import | Local skills/plugin |
| Copilot CLI | Native hooks | Explicit normalized JSONL import | Local skills/hooks |
| Antigravity CLI | Manual capture | Explicit normalized JSONL import | Local skills |

Every provider gets the same four learning skills and a separate per-project
queue. Startup reminders and compaction backups are available through the native
hook adapters; OpenCode captures user messages through its JavaScript plugin.
Antigravity uses the CLI skill paths shared with Panoptes and requires manual
capture. The table describes implemented adapters; live hook behavior depends on
the installed agent version.

Native history readers are available for Codex and Claude. Other providers need
an explicitly supplied export converted to the documented JSONL format for
historical scans. Queue review and current-conversation reflection work without
an export. Reflect does not silently substitute another provider's history.

## Use the skills

Use `$reflect` in Codex. Other agents expose installed skills through their native
skill interface or slash commands; select the Reflect skill if names overlap.

| Skill | Purpose |
|---|---|
| `reflect` | Review corrections and propose guidance updates |
| `reflect-skills` | Find recurring workflows and propose reusable skills |
| `view-queue` | Inspect pending candidates without changing them |
| `skip-reflect` | Explicitly discard pending candidates, with a backup |

Examples in Codex:

```text
$reflect --dry-run
$reflect --scan-history --days 30
$reflect-skills --days 14 --dry-run
$view-queue
```

`reflect` also supports target discovery, confidence/decay review, deduplication,
and guidance organization. Skills honor the selected provider's instruction and
skill locations, including existing Codex instruction overrides. See
[the shared review workflow](codex_port/references/review-workflow.md) and
[the Codex guide](CODEX.md) for the full behavior.

## What happens to a correction

```text
User correction -> Local queue -> Reviewed proposal -> User approval -> Guidance/skill update
```

Hooks detect candidate corrections, preferences, and explicit `remember:` markers.
They queue local evidence; they do not call a model or edit active instructions.
During reflection, the agent filters one-off requests and vague feedback, groups
repeated evidence, and compares it with existing guidance and skills.

A learning auditor screens sanitized evidence, an independent reviewer evaluates
the exact proposed change, and the user approves it before an implementer applies
it. If independent agents are unavailable, the workflow stops at proposals.
`--dry-run` is read-only. Capture and reviewer approval are separate from user
approval to change persistent guidance.

Semantic reasoning normally happens in the current agent conversation. Codex also
supports optional `--semantic` and `compare` helpers that invoke `codex exec` and
may incur model usage. Other providers reject those subprocess modes.

## Marketplace installation

The repository name is `llm-reflect`. Existing plugin IDs stay the same for
installation compatibility:

| Agent | Plugin selector |
|---|---|
| Codex | `codex-reflect@codex-reflect-marketplace` |
| Claude Code | `reflect@reflect-marketplace` |

From this checkout:

```sh
codex plugin marketplace add "$PWD"
codex plugin add codex-reflect@codex-reflect-marketplace

claude plugin marketplace add "$PWD"
claude plugin install reflect@reflect-marketplace --scope user
```

For a Git-based Codex marketplace source:

```sh
codex plugin marketplace add wallentx/llm-reflect --ref dev
codex plugin add codex-reflect@codex-reflect-marketplace
```

The Claude marketplace also retains the original `claude-reflect` plugin, with
its upstream behavior and separate storage. Choose one integration per agent to
avoid duplicate capture. LLM Reflect's installer refuses conflicting legacy
Claude installations. Cursor and Gemini currently use direct installation even
though their ecosystems also provide plugins or extensions.

## Local data and CLI

Provider queues are isolated by canonical project path. Codex retains
`$CODEX_HOME/reflect` or `$CODEX_REFLECT_HOME`; other providers use
`$XDG_STATE_HOME/reflect/<provider>` by default. `REFLECT_HOME` redirects the shared
state root while retaining provider isolation. Existing queues are not migrated
or cleared by the repository rename.

```sh
reflect --provider gemini paths --project "$PWD"
reflect --provider gemini queue --project "$PWD"
reflect --provider cursor scan --project "$PWD" --history ./history.jsonl --days 30
```

Manual capture, including Antigravity:

```sh
printf '%s\n' 'remember: run focused tests before deployment' |
  reflect --provider antigravity capture --project "$PWD" --session-id manual-example
```

Hooks store detector candidates rather than a new log of every prompt. Basic
credential redaction is applied before capture, but evidence still requires
screening before delegation. `REFLECT_DISABLED=1` disables hooks and
`REFLECT_REMINDER=false` disables startup reminders. Legacy Codex environment
variables remain supported.

## Development

`dev` contains LLM Reflect; `main` is reserved for upstream synchronization.
Edit the maintained source in `codex_port/`, then regenerate the packages.

```text
codex_port/                Shared runtime, provider adapters, skills, review workflow
plugins/codex-reflect/      Generated Codex marketplace package
plugins/claude-reflect/     Generated reviewed Claude marketplace package
tools/                     Package builders, installer, upstream-sync helper
tests/                     Upstream tests and isolated provider/install fixtures
scripts/, commands/, hooks/ Original Claude implementation retained for upstream sync
```

The historical directory names keep package paths and downstream sync compatible.
Generated packages are committed so marketplace installs need no build step.

```sh
python3 tools/build_codex.py
python3 tools/build_providers.py
uv run --no-project --with pytest python -m pytest tests -q
python3 tools/build_codex.py --check
python3 tools/build_providers.py --check
```

CI validates both generated packages and tests on Linux, macOS, and Windows with
Python 3.8 and 3.11. Fixtures use synthetic histories and isolated homes.
See [release validation](RELEASING.md) and the
[upstream-sync procedure](CODEX.md#repeatable-upstream-sync).

## Credits and license

LLM Reflect is maintained at [wallentx/llm-reflect](https://github.com/wallentx/llm-reflect).
It is based on Bayram Annakov's
[claude-reflect](https://github.com/BayramAnnakov/claude-reflect), whose detection,
filtering, and semantic prompt code is vendored during package generation.
The original Claude implementation remains available in this repository.

[MIT license](LICENSE).
