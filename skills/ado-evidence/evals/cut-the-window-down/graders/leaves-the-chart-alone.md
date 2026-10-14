---
type: llm
focus: last_message
---

The run has no shell and no image files, so judge only what the reply commits to. This grader is about the bar chart, `throughput-chart.png`, only.

PASS if the reply would post the chart as the user gave it, or cut it only to a region with the crop script's `--box`, and would not squeeze it. Saying why, that squeezing closes up uniform areas and a bar's length carries its meaning, makes for a better answer but is not required. A reply that does not mention cutting the chart at all, and so leaves it as given, is a PASS. Copying the chart's file unchanged into another folder before posting it is posting it as given, and a reply that names cropping or squeezing only to say it will not do them to the chart has done neither.

FAIL if the reply would apply `--squeeze`, or any closing up of blank or identical rows and columns, to the chart, or would otherwise reshape it.
