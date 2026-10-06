# Claude extensions

Mods, skills and plugins for [Claude Code](https://claude.com/claude-code). The repository is also a plugin marketplace, so everything in it installs with one command.

| Extension | Kind | What it does |
| --- | --- | --- |
| [context-bar](mods/context-bar) | mod | Your context window as a meter above the prompt: the conversation, the overhead every request carries, and the room left before compaction, with a drill-down into the overhead. `/context-bar` shows or hides it. |

## context-bar

Type `/context-bar` and a card appears above the prompt:

- a header with the tokens in use, where auto-compaction starts, and a percentage badge that turns from green to yellow to red as compaction nears;
- a meter that fills toward compaction, drawn in cells: the overhead every request carries (system prompt, tools, MCP, agents, memory files, skills) in gray, the conversation in orange, and the room left as the track;
- a legend, and a line breaking the overhead down, largest first.

Press `overhead ▸` in the legend, or run `/context-bar overhead`, to open the overhead as ranked bars, one row per category; press it again to close them. A category that lists what it is made of (mcp tools by server, skills, agents, memory files) shows `▸`: press it, or run `/context-bar overhead skills`, to see its biggest items, then `+ N more` under them for every item (`show fewer` folds the list back).

It refreshes after every turn, a compaction, a `/clear` and a `/resume`, and stays on in later sessions until you turn it off. Every color is a theme key the mod API documents, so the card follows your theme.

The numbers are Claude Code's local estimates, anchored on the last API response, so the bar costs nothing to refresh; `/context` counts each category with the token-counting API and can differ slightly.

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

## Install

From a Claude Code prompt in a terminal:

```
/plugin install context-bar --marketplace davidlambl/claude-extensions
```

Answer `y` to add the marketplace, then choose a scope. Mods need Claude Code 2.1.287 or later, in the terminal or the desktop app.

To work on the mods, install from a clone instead. It is read in place, so your edits reach a session on `/reload-plugins`:

```sh
git clone https://github.com/davidlambl/claude-extensions
claude plugin marketplace add ./claude-extensions
claude plugin install context-bar@lambl-extensions
```

## Develop

Each mod is a folder under `mods/`:

```
mods/<name>/
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
