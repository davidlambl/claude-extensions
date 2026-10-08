---
name: ado-evidence
description: Post images with captions as a markdown comment on an Azure DevOps work item, or revise a comment already posted, and check that every image rendered. Use when asked to attach test evidence, screenshots or results to a work item, user story, bug or task in Azure DevOps.
---

# ADO evidence

Uploads images as work item attachments and adds, or edits, a markdown comment that embeds them with captions, under the user's own Azure DevOps identity. The script is `scripts/post.py` under this skill's base directory.

## What it needs

- Python 3, and the Azure CLI signed in (`az login`), or `AZURE_DEVOPS_TOKEN`.
- The org as `https://dev.azure.com/<org>` or `https://<org>.visualstudio.com`, the project, the work item id, and the images, usually from the api-call skill.

## Post

1. Write the comment in markdown: one intro line (environment, build or deploy, what the codes mean), then for each image a bold caption directly above it, with `{{image:<file>.png}}` where the image goes. An image is named by its file name alone and looked up in `--images`, or in the comment's own folder.
2. Preview, and show the user the full text:

   ```sh
   python3 <base>/scripts/post.py --org https://dev.azure.com/<org> --project <project> --work-item <id> \
     --comment <dir>/comment.md --dry-run
   ```

3. Post only after the user approves that exact text: the same command without `--dry-run`. To revise a comment already posted, add `--edit <comment id>`; its earlier images stay attached, unreferenced.
4. The script reads the comment back and checks that the text was stored, that each image renders and that each matches its file. Report those checks; exit 1 means one failed.

## Rules

- Only the main session posts or edits. A subagent may prepare the comment, never post it.
- Never post without the user's approval of the exact text, and never post an image the user has not seen.
- The token is sent to Azure DevOps and nowhere else; the script refuses any other `--org`.
