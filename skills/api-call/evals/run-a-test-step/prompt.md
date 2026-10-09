---
description: A release check phrased the way a tester would type it, naming no script and no skill.
tags: [trigger]
max_turns: 8
allowed_tools: [Read, Glob, Grep, Skill]
---

Before we ship, I need to check one endpoint on our Test environment and keep the evidence for the ticket: `GET /api/widgets/42` should come back 200. The base URL and the auth are in our Insomnia collection. Can you run that check and capture the request and response as an image?
