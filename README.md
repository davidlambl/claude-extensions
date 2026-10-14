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

Claude Code starts a terminal session in [auto mode](https://code.claude.com/docs/en/permission-modes#eliminate-prompts-with-auto-mode) by default. There a second model, the classifier, reviews Claude's actions in place of a permission prompt. It can stop the api-call and ado-evidence scripts with `[Code from External]`, for instance on their first run after an update. That rule judges code by where it came from. These scripts run from Claude Code's copy of the plugin, `~/.claude/plugins/cache/davidlambl/<plugin>/<version>/`, which came from GitHub and is outside your working directory. `claude auto-mode defaults --label 'Code from External'` prints the rule. Stopping a script that reads your credentials and calls a live API is a reasonable default. To let one run, choose deliberately how far to trust it.

**For one run.** Type `!` and the command at the prompt. Claude Code runs it directly, without going through Claude, and adds the command and its output to the conversation, where Claude carries on from it. `Ctrl+O` shows the stopped command in full, to copy. Or run `/permissions`, press `r` on the step under **Recently denied**, and close the dialog: Claude Code tells Claude it may retry, with your approval. Or reply that you meant it, naming the script and where it comes from, such as "run api-call's call.py from github.com/davidlambl/claude-extensions". The rule gives way only when your own message names the source, and only for that run.

**For every run, still reviewed.** Tell the classifier where these scripts come from, with an `autoMode.allow` entry in `~/.claude/settings.json`. Auto mode never reads `autoMode` from a project's settings.

```json
{
  "autoMode": {
    "allow": [
      "$defaults",
      "Code from External does not apply to the api-call and ado-evidence scripts under ~/.claude/plugins/cache/davidlambl/: the user installed these plugins from github.com/davidlambl/claude-extensions. Every other rule still applies to what they do."
    ]
  }
}
```

Keep `"$defaults"` in the list: without it, the list replaces the built-in allow rules. The entry answers only where the code came from, so the classifier still reviews each call against its other rules, such as those on sending data out and on posting as you. `claude auto-mode config` shows the rules in effect.

**For every run, with no review.** A `permissions.allow` rule settles a matching command before the classifier sees it. This one in `~/.claude/settings.json` lets Claude run your copy of api-call's `call.py` with any arguments, against any environment, with no review:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 /Users/you/.claude/plugins/cache/davidlambl/api-call/0.2.9/skills/api-call/scripts/call.py *)"
    ]
  }
}
```

A rule matches the command as Claude writes it, quotes included, so copy yours from the stopped command and put `*` in place of the arguments alone. Write your version where this says `0.2.9`, and change it after each update: until you do, the classifier reviews the new copy. A command that starts another way, such as with `MSYS_NO_PATHCONV=1` in front on Windows, needs a rule of its own. So does a call from the PowerShell tool, as `PowerShell(...)`, since a `Bash(...)` rule never matches it.

Never put `*` in place of the version. A `*` matches any text, spaces and `..` included, so that rule would let Claude run any Python file on your machine with no review, as `Bash(python3 *)` would. Auto mode drops `Bash(python3 *)`, but keeps that one. It also drops a rule with a flag after `python3`, such as `python3 -I`, so for a command Claude writes that way, use one of the ways above. Write no rule for ado-evidence's `post.py`: it would let Claude post to Azure DevOps as you with no review.

The classifier still reviews a command that matches a rule when `autoMode.classifyAllShell` is on, or when the sandbox is on and the command names the hosts it needs.

## Develop

[CONTRIBUTING.md](CONTRIBUTING.md) has the layout of a mod and of a skill plugin, the commands that validate and test them, how an extension is listed in the marketplace, and how the screenshots and evals are run.

## Security

These extensions read credentials and act under your identity. [SECURITY.md](SECURITY.md) says what each one touches, what the code guarantees, and how to report a vulnerability.

## License

MIT. See [LICENSE](LICENSE).
