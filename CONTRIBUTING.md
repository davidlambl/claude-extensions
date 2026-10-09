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

## Checks

[`.github/workflows/check.yml`](.github/workflows/check.yml) runs on every push to `main`, on every pull request, and when started by hand from the Actions tab. A push to another branch runs nothing until it has a pull request. It runs:

- strict validation of the marketplace and of every plugin it lists;
- a parse of every Python script and every PowerShell script in the repository, including the ones no test imports;
- every `tests/` folder under `mods/` and `skills/`: `claude plugin test` for one holding `*.test.ts`, `python3 -m unittest discover` for one holding `test*.py`, with `API_CALL_E2E=1` so the tests that call the network run too;
- `tools/evals-lint.py`, which checks that every eval suite would load, without running it.

The plugins come from `marketplace.json` and the test folders from what is on disk, so there is no second list to keep in step. A step fails the build if a folder holding a `.claude-plugin/plugin.json` is not listed in the marketplace, or a listed plugin has no such folder, and a `tests/` folder holding nothing the workflow knows how to run fails rather than being skipped. Nothing signs in: `claude plugin validate` only reads files, `claude plugin test` needs no session, sign-in or network, and the network tests send fake secrets to public echo services such as postman-echo.com and httpbin.org.

Run the same things locally before you push:

```sh
claude plugin validate . --strict
claude plugin validate <mods|skills>/<name> --strict
claude plugin test mods/<name>                                  # a mod's tests/*.test.ts
API_CALL_E2E=1 python3 -m unittest discover -s skills/<name>/tests   # a skill's tests; drop API_CALL_E2E to stay offline
git ls-files -z '*.py' | xargs -0 python3 -m py_compile         # every Python script parses
python3 tools/evals-lint.py                                     # every eval suite would load
```

## Evals

Each skill plugin has an `evals/` suite that tests whether the skill fires when it should, stays quiet when it shouldn't, and holds to its own rules. `claude plugin eval` runs each case with the plugin loaded and again without it, so the score difference shows what the skill actually contributed.

```sh
claude plugin eval skills/api-call --trust-plugin --no-publish --max-cost-usd 10
```

**A run costs real money.** Every case starts a full Claude session on your own credentials and bills against your plan. One case, run once, with no baseline arm, came to between five and thirty cents. The default is three runs per case in each of two arms, so a whole suite is roughly twelve times that. While writing a case, keep it cheap:

```sh
claude plugin eval skills/ado-evidence --case post-the-evidence --runs 1 --ablation none   --trust-plugin --no-publish --max-cost-usd 1
```

That cost is why the evals are not in the CI workflow, which would also need a credential the repository doesn't have. Run them by hand when you change a skill's description or its rules.

Checking that a suite would load costs nothing, and CI does it: `python3 tools/evals-lint.py` reads every case as the loader would and fails on an unknown grader type or option, a missing option, a value out of range, or a `pattern` or `input_match` that does not compile as a JavaScript regex. A grader can also carry examples, in `<case>/examples/<grader>.json`, which the lint checks with node: for a `regex` grader `{"pass": [...], "fail": [...]}`, replies it must pass and must fail; for a `tool_used` grader `{"match": [...], "no_match": [...]}`, tool inputs its `input_match` must and must not match. Write some whenever you write or change a regex; a pattern that vetoes a correct reply fails a whole run.

When a case fails, read the grader's evidence in `evals/results/<timestamp>/aggregate-result.json` before you change the skill. A judge grader can mark a correct answer wrong because the reply is shaped differently from the rubric, and the first failure here was exactly that. Results are gitignored.

## Screenshots

A mod's screenshots come from a real Claude Code session, driven by the scenario next to them; [`tools/screenshots`](tools/screenshots/) takes them again:

```sh
npm install --prefix tools/screenshots
node tools/screenshots/shoot.mjs mods/<name>/screenshots/scenario.json
```

A skill takes its own screenshots, from its own `screenshots/` folder:

```sh
python3 skills/ado-evidence/screenshots/shoot.py   # the dry-run preview
```

api-call's are `call.py --shot` against the environments beside them, as its README shows. Every image in this repository is drawn in one window frame, api-call's `frame.html`; `tools/screenshots` and ado-evidence's `shoot.py` both read it from that skill, so an installed copy of the skill carries it. Nothing is staged or hand-edited: each image is what the tool actually printed.
