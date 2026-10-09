# Claude extensions

[![check](https://github.com/davidlambl/claude-extensions/actions/workflows/check.yml/badge.svg)](https://github.com/davidlambl/claude-extensions/actions/workflows/check.yml)

Mods, skills and plugins for [Claude Code](https://claude.com/claude-code). The repository is also a plugin marketplace, so everything in it installs with one command. Each extension has its own folder and README, which says what it needs and what it touches.

| Extension | Kind | What it does |
| --- | --- | --- |
| [context-bar](mods/context-bar/) | mod | Your context window as a meter above the prompt: the conversation, the overhead every request carries, and the room left before compaction, with a drill-down into the overhead. `/context-bar` shows or hides it. |
| [api-call](skills/api-call/) | skill | Calls an API the way your Insomnia or Postman collection would, with its environment, auth and client certificate, records the exchange with every secret masked, and screenshots it on request. |
| [ado-evidence](skills/ado-evidence/) | skill | Posts images with captions as a comment on an Azure DevOps work item, or revises one, and checks that each image rendered. |

## Install

From a Claude Code prompt in a terminal:

```
/plugin install context-bar --marketplace davidlambl/claude-extensions
/plugin install api-call --marketplace davidlambl/claude-extensions
/plugin install ado-evidence --marketplace davidlambl/claude-extensions
```

Answer `y` to add the marketplace, then choose a scope. Mods need Claude Code 2.1.287 or later.

## Update

Claude Code updates this marketplace only when you ask, unless you turn on auto-update under **Marketplaces** in `/plugin`:

```sh
claude plugin marketplace update davidlambl
claude plugin update context-bar@davidlambl
```

Then run `/reload-plugins`, or start a new session. [CHANGELOG.md](CHANGELOG.md) says what each version changed.

## Develop

[CONTRIBUTING.md](CONTRIBUTING.md) has the layout of a mod and of a skill plugin, the commands that validate and test them, how an extension is listed in the marketplace, and how the screenshots and evals are run.

## Security

These extensions read credentials and act under your identity. [SECURITY.md](SECURITY.md) says what each one touches, what the code guarantees, and how to report a vulnerability.

## License

MIT. See [LICENSE](LICENSE).
