# Claude extensions

Claude Code mods, skills and plugins. The repo is a plugin marketplace: list every extension in `.claude-plugin/marketplace.json`. README.md has the layout and the commands.

## Mods

- Load the `plugin-authoring` skill before writing or debugging a mod. It names this build's API declarations (`claude-code.d.ts`); they are the reference, so grep them rather than guess.
- A new mod is written in the session's mods folder the skill names (it hot-reloads there), then copied to `mods/<name>/` once it works. An installed mod is read from this repo in place, so edit it here and run `/reload-plugins`.
- Each mod has a README.md following the template of github.com/anthropics/claude-code-playground (`_template/README.md`: what it shows, demo, how it was built, run it, notes, dependencies, third-party notices) and a `screenshots/` folder of `<name>-<state>.png`. Screenshots come from a real Claude Code session driven in a pseudo-terminal and rendered with xterm.js; keep them small and free of local paths.
- Done means `claude plugin validate mods/<name>` and `claude plugin test mods/<name>` pass, and `tsc -p mods/<name>` is clean once the engine has loaded the mod (it lays `.claude-plugin/types/`, gitignored).
- Never mix background fills and block glyphs in one row: many terminals draw glyphs from the font, short of the row's height and with seams, while a background fills its whole cell, so the two stand at different heights. Draw a bar all one way: backgrounds on spaces for a seamless fill, or every cell as `▉` (seven-eighths block) for even cells with a deliberate gap between them.
- Check a palette with the dataviz skill's validator before shipping it; the documented theme keys hold only about four colors that stay distinct, so past that use emphasis (one accent, the rest gray) instead of more hues.
- Paint only with documented theme keys: type colors as `ThemeKey` from `'claude-code'`, so `tsc` refuses any other. Keys like `*_FOR_SUBAGENTS_ONLY` or `rate_limit_empty` resolve today but are internals an update can change without notice.

Rules this build enforces that are easy to trip on:

- A helper that receives `$` must be a top-level function declaration. One nested inside `register` makes the module fail to load.
- Draw from `$.state` (`atom`, `read`, `update`), never module variables, which a reload resets. A render hook never writes.
- Hooks on calls to `$` (`$.session.usage`, `$.command.register`, `$.store.get`) answer `{ value }` or `{ deny }`, test stubs included. Event hooks (`session.start`, `ui.render`) return their result as is.
- Under `claude plugin test` nothing sits beneath the plugin: stub every event it passes on with `next(e)` and every `$` call it makes. `mock.store` and `mock.clock` cover the store and timers; the test's own `$` has no `store`.
- A `Button` never goes inside a `Text` (a Text holds only strings and inline text); put it in a `Box` row beside the `Text` around it.
- A hook that acts after `next(e)` on a gating event (`session.compact`) gets `.catch(($, e, next) => next(e))`, which replays the result instead of running the event twice.
