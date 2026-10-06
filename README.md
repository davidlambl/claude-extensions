# Claude extensions

Mods, skills and plugins for [Claude Code](https://claude.com/claude-code). The repository is also a plugin marketplace, so everything in it installs with one command. Each extension has its own folder and README.

| Extension | Kind | What it does |
| --- | --- | --- |
| [context-bar](mods/context-bar/) | mod | Your context window as a meter above the prompt: the conversation, the overhead every request carries, and the room left before compaction, with a drill-down into the overhead. `/context-bar` shows or hides it. |

![Context Bar above the prompt: 134k used, compaction at 167k, 80%](mods/context-bar/screenshots/context-bar-full.png)

## Install

From a Claude Code prompt in a terminal:

```
/plugin install context-bar --marketplace davidlambl/claude-extensions
```

Answer `y` to add the marketplace, then choose a scope. Mods need Claude Code 2.1.287 or later, in the terminal or the desktop app.

To work on the extensions, install from a clone instead. It is read in place, so your edits reach a session on `/reload-plugins`:

```sh
git clone https://github.com/davidlambl/claude-extensions
claude plugin marketplace add ./claude-extensions
claude plugin install context-bar@lambl-extensions
```

## Update

Claude Code updates plugins from this marketplace only when you ask; it auto-updates Anthropic's own marketplaces, not others, unless you turn it on. To get a new release:

```sh
claude plugin marketplace update lambl-extensions
claude plugin update context-bar@lambl-extensions
```

In a session, the same is `/plugin`, then **Installed**, `context-bar`, **Update now**. Then run `/reload-plugins`, or start a new session.

To have releases arrive by themselves, open `/plugin`, then **Marketplaces**, `lambl-extensions`, **Enable auto-update**. A session then checks a few minutes after your first message, and `/reload-plugins` applies what it fetched.

Installed from a clone, the extensions are read in place: `git pull`, then `/reload-plugins`.

## Develop

Each mod is a folder under `mods/`:

```
mods/<name>/
  README.md                    what it shows, a demo, how it was built, how to run it
  screenshots/                 the demo's images, <name>-<state>.png, and the scenario that takes them
  .claude-plugin/plugin.json   manifest: name, version, description, "types"
  hooks/hooks.json             { "modules": ["./register.tsx"] }
  hooks/register.tsx           the hooks module: export const register: Register
  types/index.d.ts             the values the mod keeps in $.state
  tests/*.test.ts              run by `claude plugin test`
```

```sh
claude --plugin-dir mods/<name>      # a session with the mod loaded; it reloads on save
claude plugin validate mods/<name>   # what the engine will load, and anything it would refuse
claude plugin test mods/<name>       # the mod's tests, run against the engine itself
```

List each new extension in `.claude-plugin/marketplace.json`, then `claude plugin validate .` checks the whole marketplace.

A mod's screenshots come from a real Claude Code session, driven by the scenario next to them; [`tools/screenshots`](tools/screenshots/) takes them again:

```sh
npm install --prefix tools/screenshots
node tools/screenshots/shoot.mjs mods/<name>/screenshots/scenario.json
```
