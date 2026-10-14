---
name: ado-evidence
description: Post images with captions as a markdown comment on an Azure DevOps work item, or revise a comment already posted, and check that every image rendered. Use when asked to attach test evidence, screenshots or results to a work item, user story, bug or task in Azure DevOps.
---

# ADO evidence

Uploads images as work item attachments and adds, or edits, a markdown comment that embeds them with captions, under the user's own Azure DevOps identity. The script is `scripts/post.py` under this skill's base directory.

## What it needs

- Python 3, and the Azure CLI signed in (`az login`), or `AZURE_DEVOPS_TOKEN` holding either a Microsoft Entra access token or a personal access token. `post.py` tells them apart by shape: an Entra token is a JWT and goes as Bearer, and anything else goes as Basic with an empty user name.
- The org as `https://dev.azure.com/<org>` or `https://<org>.visualstudio.com`, the project, the work item id, and the images, usually from the api-call skill.

## Prepare the images

An image is posted as the user gave it, unless it shows more than the evidence: a whole window, or blank space around what matters. Then `scripts/crop.py` under this skill's base directory cuts it down. It reads one PNG and writes another, never over the input, with no imaging library:

```sh
python3 <base>/scripts/crop.py <shot>.png --squeeze --out <dir>/<name>.png
```

- `--squeeze` closes up every run of identical rows, then of identical columns, longer than 16 pixels (`--gap` sets it) to that many. Every row and column that differs from its neighbour stays, so a blank editor, an empty pane or the space between a status bar's two ends goes, and nothing else. Never squeeze a chart, a progress bar, a gauge or anything whose size carries its meaning: a uniform area shrinks like blank space.
- `--box LEFT,TOP,RIGHT,BOTTOM` keeps one region, in pixels, RIGHT and BOTTOM not included; with `--squeeze`, the box is cut first. `--grid` writes `<shot>-grid.png`, the image ruled every 100 pixels and labelled with its coordinates, to read the edges from, since the image you see is scaled down.
- `--out` puts the result in the evidence folder, beside the comment. The script prints one line of JSON with the sizes, the box as used and the rows and columns squeezed. A file `--out` names that exists is replaced only with `--overwrite`.
- A squeeze keeps window chrome, since it differs from its neighbours: a tab strip, a toolbar, a zoom bar. A tab strip can show the titles of other tabs, such as another server's name, which should not go on the work item unless the user wants them there. Chrome across the image also holds open the blank columns beneath it. So when the squeezed image still shows chrome, read the edge past it from the original's `--grid` copy, then run once more from the original with `--box` and `--squeeze` together. A box cut from the squeezed copy instead takes the chrome off but leaves those columns open.
- Then check the edges at full size, since the image you see is scaled down and a sliver of chrome or a clipped line is easy to miss there. Cut the top and bottom 80 pixels of the result into two strips with `python3 <base>/scripts/crop.py <dir>/<name>.png --edges 80`, or add `--edges 80` to the run that writes it, and look at both. The JSON line gives their paths under `edges`, and a result 80 pixels tall or less comes back whole in each. Judge two things only: whether any chrome is still visible in either strip, and whether the line at the result's own edge, the top of the top strip or the bottom of the bottom strip, is clipped. A line sliced where a strip was cut, at the bottom of the top strip or the top of the bottom strip, is the strip's own cut and not the result's. If either is wrong, move the edge and run again from the original. A result a little wider than it could be is not worth another run: an invisible row of chrome can hold blank columns open, which costs space and shows nothing. The strips go in a fresh temporary folder of their own, outside the evidence folder, and are never posted.
- Look at the result before using it. When the squeezed counts are not zero, the caption says that blank space was closed up.
- A JPEG, an interlaced PNG or any other refusal means post the image as given and tell the user why. Never crop another way.

## Post

1. Write the comment in markdown: one intro line (environment, build or deploy, what the codes mean), then for each image a bold caption directly above it, with `{{image:<file>.png}}` where the image goes. An image is named by its file name alone and looked up in `--images`, or in the comment's own folder.
2. Preview, and show the user the full text:

   ```sh
   python3 <base>/scripts/post.py --org https://dev.azure.com/<org> --project <project> --work-item <id> \
     --comment <dir>/comment.md --dry-run
   ```

3. Post only after the user approves that exact text: the same command without `--dry-run`. To revise a comment already posted, add `--edit <comment id>`; its earlier images stay attached, unreferenced.
4. The script reads the comment back and checks that the text was stored, that every uploaded image is drawn in the rendered comment, and that each matches its file. An image may appear more than once, or beside other images. It prints an `ok` or `FAIL` line for each check. Report those lines; exit 1 after them means a check failed. Exit 1 with no check lines means the script stopped on an error, shown as a message with no traceback. If it stopped after the comment was added, while reading it back, the message begins `comment N was posted`: fix that comment with `--edit N` rather than adding a second one. If the comment request itself failed in a way that does not say it was refused, the message begins `the comment may have been posted`, or `comment N may have been posted` with `--edit`. Whatever the message, look at the work item before running the script again.

## Rules

- Only the main session posts or edits. A subagent may prepare the comment, never post it.
- Never post without the user's approval of the exact text, and never post an image the user has not seen.
- An image is posted as the user gave it or as `crop.py` cut it; nothing else edits one.
- The token is sent to Azure DevOps and nowhere else; the script refuses any other `--org`, and an attachment URL outside its organization.
