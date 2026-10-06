# Context Bar

A Claude Code mod that draws your context window as a meter above the prompt: the conversation, the overhead every request carries, and the room left before auto-compaction. It started as the context bar from the Claude Code newsletter, and grew a drill-down that shows what the overhead is made of.

## What this shows

Type `/context-bar` and a card appears in the band above the prompt:

- a header with the tokens in use, where auto-compaction starts, and a badge with how far along you are: green under half, yellow under 80%, red past that;
- a meter, drawn in cells, that fills toward the compaction point: the overhead in gray, the conversation (messages) in orange, and the room left as the dark track;
- a legend, and a line breaking the overhead down, largest first.

Press `overhead ▸` in the legend, or run `/context-bar overhead`, to open the overhead as ranked bars, one row per category. A category that lists what it is made of (mcp tools by server, skills, agents, memory files) shows `▸`: press it, or run `/context-bar overhead skills`, to see its biggest items, then `+ N more` for every item. `show fewer` folds the list back.

The figures come from `$.session.usage({ breakdown: 'summary' })`, the same breakdown `/context` shows, counted from Claude Code's local estimates. The call sends no token-count requests, so refreshing costs nothing.

| Hook | What it does |
|------|--------------|
| `session.start` | Registers `/context-bar`, and shows the card again if you left it on. |
| `command.run` with `{command: "context-bar"}` | Shows or hides the card, or opens the overhead and its categories. |
| `session.measure` | Measures the window again after each turn. |
| `session.compact` and `session.end` | Measure again after a compaction, a `/clear` or a `/resume`. |
| `ui.render` with `{component: "AbovePrompt"}` | Draws the card. |

### What the overhead is

Every request Claude Code sends carries more than your conversation, and the overhead is all of it, paid again on every turn:

| Category | What it is |
| --- | --- |
| tools | The definitions of Claude Code's built-in tools: Bash, Read, Edit and the rest. |
| system | Claude Code's own instructions: how to work, how to use its tools, and details about your environment. |
| mcp tools | The definitions of MCP tools loaded into the context. With tool search, most load only when needed and cost nothing until then. |
| mcp | Guidance that connected MCP servers add to the instructions about using their tools. |
| skills | The list of skills Claude can use, a name and a description for each, so every plugin that brings skills adds to it. |
| agents | The descriptions of the custom agents Claude can hand work to, most of them from plugins. They are listed whether or not one ever runs; Claude Code's built-in agents are not counted. |
| memory files | CLAUDE.md files and auto-memory, loaded when the session starts. |

Messages, the rest of what is in use, is the conversation itself: your prompts, Claude's replies, tool calls and their results, and any text a hook adds, such as a plugin's start-of-session summary. It is the part that grows, and what `/compact` summarizes.

To trim the overhead, open it, see which skills and agents cost the most, and disable the plugins you are not using with `/plugin`: their skills and agents leave every request.

## Demo

After the first turn, which read two modules, 47% of the way to compaction:

![Context Bar after one turn: 78.3k used, compaction at 167k, 47%](screenshots/context-bar-halfway.png)

After reading three more, 81%. The badge turns red as compaction nears:

![Context Bar after two turns: 135k used, 81%](screenshots/context-bar-full.png)

`/context-bar overhead`, or pressing `overhead ▸`, opens the overhead as ranked bars:

![The overhead opened: tools, system, skills, agents and mcp as ranked bars](screenshots/context-bar-overhead.png)

Pressing `skills ▸` lists the skills by what they cost, with the rest one press away:

![Skills opened to their biggest items, with 121 more behind a button](screenshots/context-bar-skills.png)

## How it was built

- **Model:** built with Claude Opus 5.5 in Claude Code. The screenshots come from a session on Claude Haiku 4.5, whose 200k window fills within a couple of turns. The mod itself doesn't call a model.
- **Prompt(s):** the build started from the prompt in the Claude Code newsletter:

  > create a mod that draws my context window as a stacked bar above the prompt, one color per category like /context, toggled with /context-bar

  Then it was steered in conversation: match the newsletter's card, use only documented theme colors, reimagine the colors with UX principles, show the cells, and let me drill into the overhead.
- **Transcript:** not shared.
- **Iterations:**
  - The first version gave every `/context` category its own color. `/context` itself reuses colors (the system prompt and the free space share one), and Claude Code lists the autocompact buffer before the free space, which the first test data hadn't. The tests now use the engine's exact rows.
  - Matching the newsletter's card used theme colors outside the mod API's documented `ThemeKey` list. They work today, but an update could change them silently, so the palette is now typed as `ThemeKey` and `tsc` refuses anything else.
  - Half-cell slices mixed block glyphs with background colors. Terminals that draw glyphs from the font, such as Apple Terminal, draw them shorter than the row, so the bar looked uneven. Every cell is now the same `▉` glyph, whose last eighth leaves a deliberate gap between cells.
  - The colors still blended. The dataviz skill's palette validator failed the per-category palette on four of five checks, and only four documented theme colors pass even on their own. So the bar became a meter with emphasis: one accent for the conversation, gray for the rest, filling toward compaction rather than the end of the window, which also dropped the reserve band.
  - The overhead became something to open: ranked bars per category, then the items inside the categories the API lists, then every item.
  - Tested with `claude plugin test` (67 tests) and by breaking the code on purpose to check the tests catch it. The screenshots came from driving a real Claude Code session in a pseudo-terminal and rendering its screen with xterm.js.

## Run it

**Requirements:**

- Claude Code 2.1.287 or later, in the terminal or the desktop app. Built and tested on 2.1.290 and 2.1.291.

No environment variables or configuration.

**Steps:**

1. Install it from a Claude Code prompt in a terminal:

   ```
   /plugin install context-bar --marketplace davidlambl/claude-extensions
   ```

   Answer `y` to add the marketplace, then choose a scope.

2. Type `/context-bar`. The card stays on in later sessions until you run it again.

To try it for one session from a clone instead:

```bash
git clone https://github.com/davidlambl/claude-extensions.git
claude --plugin-dir ./claude-extensions/mods/context-bar
```

## Notes / limitations

- **The numbers are estimates.** They are Claude Code's local estimates, anchored on the last response; `/context` counts each category with the token-counting API and can differ slightly.
- **The card updates after each turn**, a compaction, a `/clear` and a `/resume`, not during a turn.
- **The meter fills toward compaction**, so its percentage is of the auto-compact point, not the whole window. With auto-compaction off, it fills toward the end of the window.
- **Only some categories open to items.** The mod API lists the parts of mcp tools, skills, agents and memory files, but not of tools, system or mcp.
- **The short labels match `/context`'s category names.** If an update renames a category, it shows under its own name, lowercased.
- **Pressing a button** needs the fullscreen terminal for a click; `/context-bar overhead [category]` works anywhere.
- **Long lists scroll inside the band**, which takes at most half the terminal's height.
- **In 16-color terminal themes**, the overhead and the free space share a gray.
- **One band per session.** Another mod that draws above the prompt competes for the same band.

## Dependencies

| Name | Version | License (SPDX) | Source |
| --- | --- | --- | --- |
| None | | | |

## Third-party notices

None.
