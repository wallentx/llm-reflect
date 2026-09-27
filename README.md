# llm-reflect

Turn corrections to your coding agent into better instructions, native memories,
and reusable skills.

Supports Codex, Claude Code, Cursor, Gemini CLI, OpenCode, Copilot CLI, and
Antigravity CLI. See [provider support](PROVIDERS.md#capabilities) for capabilities.

## Install

Requires **Python 3.11+**.

```sh
git clone --branch dev https://github.com/wallentx/llm-reflect.git
cd llm-reflect
sh ./install.sh
```

On Windows, run `python tools/install.py`. Restart your agent after installation.

Update all installed providers with `reflect update`, or one with
`reflect update --provider gemini`. `sh ./install.sh -u` and the installer's `u` key
also work. Restart your agent afterward.

## Use

In Codex, start with `$reflect`. In other agents, invoke the installed `reflect` skill.

| Skill | Purpose | Options |
|---|---|---|
| `reflect` | Review corrections and propose memory/instruction updates | `--dry-run`, `--scan-history`, `--days N`, `--history FILE`, `--targets`, `--memory`, `--memory-dir DIR`, `--review`, `--dedupe`, `--organize`, `--include-tool-errors`, `--model MODEL` (Codex) |
| `reflect-skills` | Turn recurring workflows into reusable skills | `--days N`, `--project PATH`, `--all-projects`, `--history FILE`, `--dry-run` |
| `view-queue` | Show pending corrections | - |
| `skip-reflect` | Discard pending corrections | - |

```text
$reflect --scan-history --days 30
$reflect-skills --all-projects --days 14 --dry-run
```

Direct inspection from this checkout:

```sh
python3 tools/reflect.py status
python3 tools/reflect.py memory --all-providers
python3 tools/reflect.py targets --format text
python3 tools/reflect.py queue --format text
python3 tools/reflect.py scan --days 14 --format text
```

Add `--provider claude` before the command to select another provider.
Use `--limit N` for longer text reports or `--format json` for complete data.
See [memory adapters](PROVIDERS.md#memory-adapters) for reading and reviewed writes.

## Documentation

- [Provider guide](PROVIDERS.md)
- [Codex guide](CODEX.md)
- [Development](DEVELOPMENT.md) and [releases](RELEASING.md)

Based on [claude-reflect](https://github.com/BayramAnnakov/claude-reflect).
[MIT license](LICENSE).
