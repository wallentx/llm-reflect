# llm-reflect

Turn corrections to your coding agent into better instructions and reusable skills.
Changes need your approval.

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

## Use

In Codex, start with `$reflect`. In other agents, invoke the installed `reflect` skill.

| Skill | Purpose |
|---|---|
| `reflect` | Review corrections and propose instruction updates |
| `reflect-skills` | Turn recurring workflows into reusable skills |
| `view-queue` | Show pending corrections |
| `skip-reflect` | Discard pending corrections |

## Documentation

- [Provider guide](PROVIDERS.md)
- [Codex guide](CODEX.md)
- [Development and releases](RELEASING.md)

Based on [claude-reflect](https://github.com/BayramAnnakov/claude-reflect).
[MIT license](LICENSE).
