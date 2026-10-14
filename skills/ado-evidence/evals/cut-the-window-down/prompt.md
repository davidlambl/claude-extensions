---
description: A whole-window screenshot given as evidence beside a bar chart; the window is cut down with the skill's crop script and shown first, and the chart is not squeezed.
tags: [trigger, rules]
max_turns: 8
allowed_tools: [Read, Glob, Grep, Skill]
---

I took a screenshot of my whole SSMS window after running the check query, `ssms-full.png`. Most of it is empty editor; only the query, the one-row result and the status bar at the bottom matter. I also have `throughput-chart.png`, a bar chart from the load test. Put both on Azure DevOps work item 70012 as a comment, with a caption over each.
