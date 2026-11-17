# Handoff - {item or project} {model} build (start a workflow from this file)

Written {date} for a NEW session (Claude Desktop). {One sentence on what else is running in parallel, if anything, and that the two must never touch each other's folders or branches.}

## What to do

{One sentence: what gets built.} Follow the saved plan exactly:

`{full path to the plan}`

Read the whole plan first. {Name the sections that apply: contract, workstreams, agent rules, order of work.} Use a workflow (multi-agent orchestration) for the agent runs; the user has opted in.

## What is different from the plan as written

1. {Anything that moved since the plan was written, and that it was checked: commit, line references.}
2. **Implementers are {model}** (`model: '{sonnet|opus}'`, effort `{level}`) for {streams}. Set `model` on every agent call.
3. **Stop after {stage}.** Do NOT run {later stages}. Report and wait.
4. {Units this session skips because the other side owns them.}

## Names you own (use exactly these)

| Thing | Yours |
|---|---|
| Integration worktree / branch | `{path}` / `{branch}` |
| Side worktrees / branches | `{paths}` / `{branches}` |

## Never touch

- {The other side's folders and branches.}
- {The main checkout}: no checkout, build or edit there. {What is allowed, if anything, such as reading files or writing to an ignored notes folder.}
- Any remote: no push, no pull request, no ticket or cloud writes.
- {Shared notes or memory the main session owns.}

## Order of work

1. {Setup: fetch, drift check, worktree, baseline build and tests; stop if the baseline fails.}
2. {First wave and the gate that must pass before fan-out.}
3. {Fan-out: which streams, what each prompt contains.}
4. {Integration: how streams are combined, what must be green, how many repair rounds before stopping.}

## When done, write the result and stop

Write `{full path to result file}` with: head commit; commits per stream; files changed; test counts before and after; anything still failing and why; every deviation from the contract an agent reported; repair rounds used; agent count. Leave the worktrees in place.

## Rules that apply to everything you write

{Project conventions the agents must follow: comment rules, commit subject format, staging by name, restoring files a build rewrote.}
