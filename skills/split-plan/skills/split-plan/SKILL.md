---
name: split-plan
description: Split an implementation plan into agent workflows by model - Sonnet 5.5 workstreams that run in a separate Claude Desktop session from a handoff file, and Opus 5.5 stages that run as a workflow here in Claude Code - or say plainly that nothing should be delegated. Use this whenever the user has a plan and asks to split it, delegate it, parallelize it, hand part of it off, "make workstreams", decide what Sonnet or Opus can take, write a handoff for another session, or turn a plan into a workflow, even if they do not name the models.
---

# Split a plan into Sonnet and Opus workflows

The main session (usually Fable, in Claude Code) writes the plan. This skill decides who builds each part of it, then produces what each side needs to start:

- **Sonnet 5.5** work always runs in a **separate Claude Desktop session**. That session knows nothing about this conversation, so it gets a self-contained handoff file.
- **Opus 5.5** work runs **here in Claude Code** as a workflow the main session launches and supervises.
- Some work belongs to **neither** and stays with the main session.

Delegation is a cost, not a default. Every handoff loses context and every agent's mistakes come back sounding confident. Propose the smallest split that pays for itself, and say so when the answer is "none".

## Step 1 - List the work units

Read the plan and break it into units that could each be handed to one agent: a unit has a clear output, a way to check it, and a set of files it would edit. Note what each unit depends on.

If the plan is not written down yet, or is too vague to list units with outputs and checks, stop and say the plan needs that first. A split of a vague plan just spreads the vagueness across more agents.

## Step 2 - Sort each unit

Ask of each unit, in this order:

**Keep it in the main session** when any of these is true:
- It is smaller than its own briefing (a few edits, a lookup, one command).
- It needs this conversation's context or the user's judgment: decisions, trade-offs, anything the user has opinions about.
- It is text other people will read (work item fields, PR descriptions, messages). Voice and accuracy matter more than speed there.
- It touches a remote system, production, credentials, or anything hard to undo.
- It is the judgment stage itself: checking review findings, deciding what gets fixed.

**Sonnet can take it** when all of these are true:
- The spec can be made literal: exact names, signatures, file paths, test names with their expected results. If you cannot write it that precisely, it is not a Sonnet unit yet.
- Success is checkable by running something (build, tests, a diff check), not by taste.
- It owns its files outright, or runs alone.
- Getting it wrong is cheap to detect and fix.

**Opus should take it** when the unit is delegable but Sonnet is the wrong fit:
- The spec cannot be made literal because the work is finding the right shape, not filling one in.
- It involves subtle interplay: concurrency, timing, cancellation, ordering, security boundaries, cross-file reasoning.
- It is adversarial review ("try to break this") or review that has to weigh intent against code.
- A wrong result would be expensive and hard to notice.

When a unit could go either way, prefer the cheaper model and tighten the spec. Reach for Opus because the work needs it, not as insurance.

## Step 3 - Pick the outcome and say it plainly

Exactly one of these, stated in the first line of the proposal:

| Outcome | When | What to produce |
|---|---|---|
| **Nothing to delegate** | Every unit is "keep", or the whole job is under about an hour of direct work | Say so and why, in two or three lines. Offer to just do it. No handoff, no workflow. |
| **Sonnet only** | The delegable units are all literal-spec work and nothing needs Opus | The Sonnet handoff. Do not add an Opus review stage for symmetry; the main session checks the result. |
| **Opus only** | The delegable units all need Opus, or Sonnet units exist but are too small or too entangled to be worth a second session | The Opus workflow. Say which units Sonnet could not take and why. |
| **Both** | There is real literal-spec work AND real Opus work, and the two sets do not edit the same files | Both, with the meeting point defined (Step 5). |

"Both" means the plan is **partitioned**: each unit is built once, by one side. Building the same plan twice on two models to compare them is a different thing; do that only when the user asks for a comparison.

## Step 4 - Shape the streams

These rules exist because parallel agents fail in predictable ways:

- **Ownership is by file, not by task.** Two streams that sound independent will still collide if both append to the same file. If they would, restructure (new files, or one stream owns that file) or run them in sequence.
- **Freeze the contract first.** Names, signatures, message text, expected results: written once, pasted into every stream's prompt. Streams that guess at each other's interfaces do not fit together.
- **Dependencies set the waves.** A unit others compile against goes first, alone, with a check before fan-out. Fan out only what is truly independent.
- **Implementer and test author can be different agents** working from the same contract. Tests written without seeing the implementation cannot mirror its mistakes; they go red in their own worktree and green at integration.
- **Every stream gets a "done when"** that is a command and its expected result.
- **Agents never get remote access** unless the user has said so for this job: no push, no tickets, no cloud CLI. Builds and tests run in scratch worktrees, never the user's main checkout.

Keep the count honest: a stream that takes longer to brief than to do should be folded into a neighbor or kept.

## Step 5 - Effort and model per stage

| Stage | Effort |
|---|---|
| Builders, implementers, fixers | `max` |
| Attack lenses (try to break it) | `max` |
| Reviewers (criteria, tests, conventions) | `high` |
| Refuters, synthesis, cross-check | `medium` |

These apply to both models. Set `model` explicitly on every agent call (`'sonnet'` or `'opus'`); the session default may be something else, and an omitted model silently inherits it.

**Flag, do not choose, Fable.** If a stage looks like it needs more than Opus (judgment over conflicting findings, the single deepest attack lens, a fix inside subtle plumbing), name the stage and the reason in the proposal and let the user decide.

**Refuters are optional.** The usual shape is: lenses report, the main session verifies every finding against the code itself, then a fix stage runs. Add refuter agents only if the user wants the run unattended.

When both sides run, define the meeting point: who integrates, in which worktree, what "green" means there, and which side stops and waits.

## Step 6 - Show the proposal, then wait

Before writing files or launching anything, show the user:

1. The outcome line from Step 3.
2. A table: unit, who takes it (main session / Sonnet / Opus), effort, files owned, depends on, done when.
3. Units kept in the main session, with the reason for each.
4. Any stage flagged for Fable.
5. Agent count per side.

Then wait for a go. The user may move units between columns.

## Step 7 - Produce the artifacts

**Sonnet side: a handoff file.** Write it next to the plan (or where the project keeps working notes) using `assets/handoff-template.md`. The Desktop session starts cold, so the handoff must stand alone:

- The full path of the plan to follow, and which sections apply.
- What differs from the plan as written (stale references, models, where to stop).
- The exact folders and branch names that session owns, in a table.
- A never-touch list: the other side's folders and branches, the main checkout, remotes, any shared notes or memory the main session owns.
- The order of work, with the check that gates each fan-out.
- A stop point and a result file to write, so the main session can pick the work up without the two sessions talking.

Give the user one line to paste into the Desktop session: "Read `<handoff path>` and follow it. Use a workflow for the agent runs."

**Opus side: a workflow, launched here.** Add the stages to the plan, do the setup inline (fetch, drift check against the commit the plan was written for, worktree, baseline build and tests), then launch. Asking for this skill is not by itself permission to spend on a multi-agent run: launch only after the user's go on the proposal. After each run, check that no agent died silently before trusting a clean result, and re-run the tests yourself.

**When both run at once,** the two sides get different folder and branch names, and the handoff says so in both directions. Let the cold session keep the plan's names exactly as written and give the supervised side the renamed ones; the session with context is the one that can translate.

## What to tell the user at the end

Where the handoff is, the line to paste, what is running here and how to watch it, what each side will produce, and what comes back to them for a decision.
