# Claude extensions

Claude Code mods, skills and plugins. The repo is a plugin marketplace: list every extension in `.claude-plugin/marketplace.json`. README.md has the layout and the commands.

## Mods

- Load the `plugin-authoring` skill before writing or debugging a mod. It names this build's API declarations (`claude-code.d.ts`); they are the reference, so grep them rather than guess.
- A new mod is written in the session's mods folder the skill names (it hot-reloads there), then copied to `mods/<name>/` once it works. Then remove the session copy: while both load, the session copy draws in place of the installed one, and edits here seem to do nothing. An installed mod is read from this repo in place, so edit it here and run `/reload-plugins`.
- Each mod has a README.md following the template of github.com/anthropics/claude-code-playground (`_template/README.md`: what it shows, demo, how it was built, run it, notes, dependencies, third-party notices) and a `screenshots/` folder of `<name>-<state>.png`. Screenshots come from a real Claude Code session: write `screenshots/scenario.json` and run `node tools/screenshots/shoot.mjs` on it (see its README), never stage or hand-edit an image.
- Raise the mod's `version` in its `plugin.json` with every change you publish. Claude Code keeps each install on its cached copy until that string changes, however many commits land.
- Done means `claude plugin validate mods/<name>` and `claude plugin test mods/<name>` pass, and `tsc -p mods/<name>` is clean once the engine has loaded the mod (it lays `.claude-plugin/types/`, gitignored).
- Never mix background fills and block glyphs in one row: many terminals draw glyphs from the font, short of the row's height and with seams, while a background fills its whole cell, so the two stand at different heights. Draw a bar all one way: backgrounds on spaces for a seamless fill, or every cell as `▉` (seven-eighths block) for even cells with a deliberate gap between them.
- Check a palette with the dataviz skill's validator before shipping it; the documented theme keys hold only about four colors that stay distinct, so past that use emphasis (one accent, the rest gray) instead of more hues.
- A `summary` usage breakdown takes its total from the last response, but its categories are local estimates that can run to twice what `/context` counts. Where the categories matter, count with `full`, which sends one token-count request per tool and memory file, and only when they change.
- The mod API lists `vscode` and `mobile` among the surfaces, but check that a client really draws before relying on it. Claude Code for VS Code (2.1.291) draws no mod UI: it never attaches, and `$.session.surfaces()` comes back empty there. Give a session that draws nowhere a text answer from the mod's command.
- Paint only with documented theme keys: type colors as `ThemeKey` from `'claude-code'`, so `tsc` refuses any other. Keys like `*_FOR_SUBAGENTS_ONLY` or `rate_limit_empty` resolve today but are internals an update can change without notice.

Rules this build enforces that are easy to trip on:

- A helper that receives `$` must be a top-level function declaration. One nested inside `register` makes the module fail to load.
- Draw from `$.state` (`atom`, `read`, `update`), never module variables, which a reload resets. A render hook never writes.
- Hooks on calls to `$` (`$.session.usage`, `$.command.register`, `$.store.get`) answer `{ value }` or `{ deny }`, test stubs included. Event hooks (`session.start`, `ui.render`) return their result as is.
- Under `claude plugin test` nothing sits beneath the plugin: stub every event it passes on with `next(e)` and every `$` call it makes. `mock.store` and `mock.clock` cover the store and timers; the test's own `$` has no `store`.
- A `Button` never goes inside a `Text` (a Text holds only strings and inline text); put it in a `Box` row beside the `Text` around it.
- A hook that acts after `next(e)` on a gating event (`session.compact`) gets `.catch(($, e, next) => next(e))`, which replays the result instead of running the event twice.

## Skills

- A skill plugin's scripts are tested with Python's unittest in the plugin's `tests/` folder; network tests skip unless `API_CALL_E2E` is set, which CI sets. Raise the plugin's `version` with every published change, as for a mod, and add a CHANGELOG entry.
- Every fix comes with a test that fails without it: run the new test against the unfixed file before you trust it. A test must isolate the path it guards. The 0.2.2 echo test for templated folder headers passed only because folder auth registered the same variable, so the header path shipped broken.
- Test secrets are obviously fake, such as `FAKE-tok-7f3a9c2e`. A vendor-shaped value, even the example key from a vendor's own docs, gets the push blocked by GitHub's secret scanning.
- Write a `\uXXXX` escape in a source or test file with a Python edit, not the Edit or Write tool, which turns it into the raw character. Before committing, scan changed files for raw Cf, Zl and Zp characters.

Secrets in api-call and ado-evidence, which SECURITY.md promises to users:

- A secret is a value in a credential position: an API key's value, a bearer token, a basic-auth password and token, a templated header value (except standard headers such as `Accept` or `Content-Type`), a password in the base URL, and a URL user with no password. Hosts, paths, header names, prefixes, usernames and labels are configuration: shown, never refused. Don't widen this without first hunting for false refusals in ordinary steps. A broader rule once made templated hosts and `/me` responses refuse every call.
- A fix to one api-call source goes in the other, and a credential fix goes in `post.py` too. Two of the four leaks the 2026-10-09 review found were a fix applied in one place and not its sibling.
- Never send a credential through a client that follows redirects. Python's default opener resends every header, `Authorization` included, to wherever a redirect points, and .NET resends a key header such as `X-Api-Key`. Record a redirect as the response.
- The image draws the body exactly as the API returned it. Decoding escapes or re-serializing JSON before drawing showed a secret the source's check had never seen.
- In PowerShell, a list passed through `$( )` or `@( )` reaches the function as a copy, so whatever the function adds to it is lost. Pass the variable itself, or return values instead.
- Verifying a change to either skill means hunting both directions: secrets that get through, and ordinary steps that get refused.
