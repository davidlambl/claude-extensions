# Claude extensions

[![check](https://github.com/davidlambl/claude-extensions/actions/workflows/check.yml/badge.svg)](https://github.com/davidlambl/claude-extensions/actions/workflows/check.yml)

Mods and skills for [Claude Code](https://claude.com/claude-code), each packaged as a plugin. The repository is also a plugin marketplace, so each one installs with one command. Each extension has its own folder and README, which says what it needs and what it touches.

| Extension | Kind | What it does |
| --- | --- | --- |
| [context-bar](mods/context-bar/) | mod | Your context window as a meter above the prompt: the conversation, the overhead every request carries, and the room left before compaction, with a drill-down into the overhead. `/context-bar` shows or hides it. |
| [api-call](skills/api-call/) | skill | Calls an API the way your Insomnia or Postman collection would, with its environment, auth and client certificate, records the exchange with every secret masked, and screenshots it on request. |
| [ado-evidence](skills/ado-evidence/) | skill | Posts images with captions as a comment on an Azure DevOps work item, or revises one, and checks that each image rendered. Cuts a screenshot down first, with no imaging library. |

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

## Auto mode

Claude Code starts a terminal session in [auto mode](https://code.claude.com/docs/en/permission-modes#eliminate-prompts-with-auto-mode) by default. There a second model, the classifier, reviews Claude's actions in place of a permission prompt. It can stop the api-call and ado-evidence scripts with `[Code from External]`, for instance on their first run after an update. That rule judges code by where it came from. These scripts run from Claude Code's copy of the plugin, `~/.claude/plugins/cache/davidlambl/<plugin>/<version>/`, which came from GitHub and is outside your working directory. `claude auto-mode defaults --label 'Code from External'` prints the rule. Stopping a script that reads your credentials and calls a live API is a reasonable default. To let one run, choose it deliberately, in one of two ways.

**Run the step yourself.** Type `!` and the command at the prompt. Claude Code runs it directly, without going through Claude, and adds the command and its output to the conversation, where Claude carries on from it. `Ctrl+O` shows the stopped command in full, to copy.

**Or allow the script by its path.** Claude Code settles a command that matches an allow rule before the classifier sees it. This rule in `~/.claude/settings.json` lets Claude run api-call's `call.py` with any arguments, against any environment, with no review:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 /Users/you/.claude/plugins/cache/davidlambl/api-call/*/skills/api-call/scripts/call.py *)"
    ]
  }
}
```

A rule matches the command as Claude writes it, so copy yours from the stopped command and put `*` in place of the version and of the arguments. A command that starts another way, such as with `MSYS_NO_PATHCONV=1` in front on Windows, needs a rule of its own. The `*` matches any text, `..` included, so this rule also approves any other `call.py` in a `skills/api-call/scripts` folder, reached from the cache with `..`. Write the version, such as `0.2.9`, in place of that `*` to allow your copy alone. After an update, the classifier then reviews the new copy until you change the rule. Allow each script by its own path, never `python3` itself: auto mode drops a rule as broad as `Bash(python3 *)`. A rule for ado-evidence's `post.py` would let Claude post to Azure DevOps as you with no review, so run that step with `!` instead.

The classifier still reviews a command that matches a rule when `autoMode.classifyAllShell` is on, or when the sandbox is on and the command names the hosts it needs.

## Develop

[CONTRIBUTING.md](CONTRIBUTING.md) has the layout of a mod and of a skill plugin, the commands that validate and test them, how an extension is listed in the marketplace, and how the screenshots and evals are run.

## Security

These extensions read credentials and act under your identity. [SECURITY.md](SECURITY.md) says what each one touches, what the code guarantees, and how to report a vulnerability.

## License

MIT. See [LICENSE](LICENSE).
