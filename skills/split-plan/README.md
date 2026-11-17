# split-plan

A skill for Claude Code that takes an implementation plan and decides who builds each part of it:

- **Sonnet** work goes to a separate Claude Desktop session, started from a self-contained handoff file.
- **Opus** work runs as a workflow in the Claude Code session that wrote the plan.
- Work that should not be delegated stays where it is, and the skill says so.

It proposes the smallest split that pays for itself: nothing, Sonnet only, Opus only, or both.

## What it does

1. Lists the plan's work units, each with an output, a check and the files it would edit. A plan too vague for that is sent back.
2. Sorts each unit: keep it (small, needs judgment, text people read, touches a remote system), Sonnet (the spec can be made literal and success is checked by running something), or Opus (shape-finding work, subtle timing or concurrency, adversarial review).
3. States one outcome and shows a table of units, owners, effort, files owned and "done when", then waits for a go.
4. Writes the Sonnet handoff from a template and gives you one line to paste into the Desktop session; launches the Opus workflow in place.

Effort per stage: `max` for builders, fixers and attack lenses, `high` for reviewers, `medium` for refuters, synthesis and cross-check. A stage that may need a stronger model than Opus is flagged for you to decide, never picked silently.

## Run it

```
/plugin install split-plan --marketplace davidlambl/claude-extensions
```

Then, with a plan written or open: `/split-plan`, or ask to split, delegate or hand off the plan.

## Notes

- The skill launches nothing and writes no handoff until you approve its proposal.
- Parallel streams are split by file ownership, not by task, so two agents never edit the same file.
- When both sides run at once they get different folder and branch names, and the handoff carries a never-touch list.

## Dependencies

Claude Code with the Workflow tool for the Opus side; Claude Desktop for the Sonnet side. No scripts, no network access, no files outside the plan's own folder.
