# Contributing

An extension is developed from a clone of this repository. A clone is read in place, so an edit reaches a session on `/reload-plugins`:

```sh
git clone https://github.com/davidlambl/claude-extensions
claude plugin marketplace add ./claude-extensions
claude plugin install context-bar@davidlambl
```

`git pull`, then `/reload-plugins`, brings in later changes.

## A mod

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

## A skill plugin

Each skill plugin is a folder under `skills/`:

```
skills/<name>/
  README.md                    what it does, a demo, how to run it, what it costs and touches
  screenshots/                 the demo's images, <name>-<state>.png, and what takes them
  .claude-plugin/plugin.json   manifest: name, version, description
  skills/<name>/SKILL.md       the skill: when Claude uses it, and how
  skills/<name>/scripts/       what the skill runs
  tests/                       the scripts' tests, when they have any
```

```sh
claude --plugin-dir skills/<name>                      # a session with the skill loaded
claude plugin validate skills/<name>                   # the manifest and what the engine will load
python3 -m unittest discover -s skills/<name>/tests    # the scripts' tests
```

## List it in the marketplace

Every extension is an entry in `.claude-plugin/marketplace.json`, with its `name`, its `source`, the `description` people read in `/plugin`, a `category` and `tags`. Then `claude plugin validate .` checks the whole marketplace, and `--strict` turns its warnings into failures.

The extension's own `plugin.json` carries `homepage`, `repository` and `keywords`. Claude Code shows those in `/plugin` both before and after install, because every entry here points at a folder inside this repository.

## Raise the version with every release

Claude Code keeps each install on its cached copy until the `version` in `plugin.json` changes, however many commits land. Raise it with every change you publish, metadata included.

## Screenshots

A mod's screenshots come from a real Claude Code session, driven by the scenario next to them; [`tools/screenshots`](tools/screenshots/) takes them again:

```sh
npm install --prefix tools/screenshots
node tools/screenshots/shoot.mjs mods/<name>/screenshots/scenario.json
```

api-call takes its own screenshots with `call.py --shot`, against the environments in its `screenshots/` folder; its README has the commands. The window frame both draw is api-call's `frame.html`, which `tools/screenshots` reads from the skill, so that an installed copy of the skill carries it.
