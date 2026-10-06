# Screenshots

Takes a mod's screenshots by running a real Claude Code session, the way the screenshots in [claude-code-playground's mods](https://github.com/anthropics/claude-code-playground/tree/main/claude-code/mods) were made. Nothing is staged: each image is a moment of a live session, rendered in a window frame.

How it works:

1. `shoot.mjs` writes a throwaway project into a temporary folder, from one of the generators in `projects/`.
2. `drive.py` starts Claude Code there in a pseudo-terminal, types the scenario's prompts and commands, and records everything the session draws, noting the moments the scenario marks.
3. `render.mjs` replays the recording through [xterm.js](https://xtermjs.org), the terminal VS Code uses, and writes each marked screen as a terminal window. A shot starts at the first prompt, past the startup banner, and long empty stretches are squeezed to two rows; nothing drawn is changed.
4. Headless Chrome captures each window as `<name>-<mark>.png`.

## Use it

Requirements: Claude Code, Python 3, Node.js 18 or later, and Google Chrome or Chromium (set `CHROME` to its path if it is not in the usual place). Works on macOS and Linux.

```sh
npm install --prefix tools/screenshots
node tools/screenshots/shoot.mjs mods/context-bar/screenshots/scenario.json
```

The images land next to the scenario. `--out <dir>` writes them elsewhere, and `--keep` keeps the temporary folder with the recording and the pages.

## Write a scenario

A scenario is a JSON file, usually `mods/<name>/screenshots/scenario.json`:

| Field | What it is |
| --- | --- |
| `name` | The file prefix: `<name>-<mark>.png`. |
| `project` | The generator in `projects/` that writes the throwaway project. |
| `model` | The model for the session, passed to `claude --model`. A small window, such as Haiku's 200k, fills within a few turns. |
| `cols`, `rows` | The terminal's size. The band above the prompt takes at most half the rows. |
| `shots` | Each mark to capture, and the title of its window. |
| `steps` | What to do in the session, in order; see the top of `drive.py`. |

Start with a `wait`, then answer the folder-trust question, which every new folder asks (`down`, then `enter`, to choose "Yes, I trust this folder"). Text checks ignore spaces, because Claude Code draws many of them as cursor moves.

## What it costs and touches

- **It runs a real session on your account.** The prompts are answered by the scenario's model and use your usage like any other session.
- **It uses your own setup.** The session loads your settings, plugins and installed mods, so what the screenshots show depends on them. A mod's saved state (`$.store`) is shared with your own sessions: a scenario that toggles a mod changes it for you too.
- **Each run trusts a new temporary folder**, which Claude Code adds to its list of trusted folders.
- **It keeps local paths out of the images.** The project's path and your home folder are redacted, and a shot that still shows either is refused.
