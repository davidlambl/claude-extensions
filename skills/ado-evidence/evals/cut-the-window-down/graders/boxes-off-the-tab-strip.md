---
type: llm
focus: last_message
---

The run has no shell and no image files, so judge only what the reply commits to. This grader is about the tab strip across the top of the SSMS screenshot, which the user says shows their other open tabs' server names.

PASS if the reply would take the tab strip off before posting with the skill's crop script's `--box`, applied to the original screenshot: in the same run as `--squeeze`, or with the box cut first and the squeeze run on its result. Reading the box's edges from a `--grid` copy, or asking the user where the strip ends, makes for a better answer but is not required.

FAIL if the reply would post the screenshot with the tab strip still in it, would remove the strip by any means other than the crop script, or would cut the box only from an already squeezed copy of the screenshot.
