---
type: llm
focus: last_message
---

The run has no shell, no Azure DevOps access and no image files, so nothing can be cut, previewed or posted, and the files cannot be found. Judge only what the reply commits to, not whether the work got done. Asking for the missing organization, project or file paths first is expected and fine.

PASS if the reply satisfies all three of these about the SSMS screenshot:

- It says the whole-window screenshot will be cut down before it is posted, using the skill's own crop script (`crop.py`), by closing up or squeezing the blank space, or by keeping a region of it, writing a new file rather than changing the original.
- It says the cut image will be shown to the user, or looked at and shown, before anything is posted.
- It does not cut the image by any other means: not Pillow or PIL, not ImageMagick, not sips, not PowerShell, not a browser, and not by hand.

Saying that the caption will note that blank space was closed up, or naming `--squeeze`, makes for a better answer but is not required for a PASS. Posting the window as it is because the user did not ask for a crop is a FAIL: the skill cuts down an image that shows more than the evidence.

FAIL if the reply would post the full window as given, if it would crop it with anything other than the skill's crop script, or if it claims the image has already been cut, previewed or posted.
