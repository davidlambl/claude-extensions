---
name: ado-evidence
description: Post images with captions as a markdown comment on an Azure DevOps work item, or revise a comment already posted, and check that every image rendered. Use when asked to attach test evidence, screenshots or results to a work item, user story, bug or task in Azure DevOps.
---

# ADO evidence

Uploads images as work item attachments and adds, or edits, a markdown comment that embeds them with captions, under the user's own Azure DevOps identity. The script is `scripts/post.py` under this skill's base directory.

## What it needs

- Python 3, and the Azure CLI signed in (`az login`), or `AZURE_DEVOPS_TOKEN` holding either a Microsoft Entra access token or a personal access token. `post.py` tells them apart by shape: an Entra token is a JWT and goes as Bearer, and anything else goes as Basic with an empty user name.
- The org as `https://dev.azure.com/<org>` or `https://<org>.visualstudio.com`, the project, the work item id, and the images, usually from the api-call skill.

## Post

1. Write the comment in markdown: one intro line (environment, build or deploy, what the codes mean), then for each image a bold caption directly above it, with `{{image:<file>.png}}` where the image goes. An image is named by its file name alone and looked up in `--images`, or in the comment's own folder.
2. Preview, and show the user the full text:

   ```sh
   python3 <base>/scripts/post.py --org https://dev.azure.com/<org> --project <project> --work-item <id> \
     --comment <dir>/comment.md --dry-run
   ```

3. Post only after the user approves that exact text: the same command without `--dry-run`. To revise a comment already posted, add `--edit <comment id>`; its earlier images stay attached, unreferenced.
4. The script reads the comment back and checks that the text was stored, that every uploaded image is drawn in the rendered comment, and that each matches its file. An image may appear more than once, or beside other images. It prints an `ok` or `FAIL` line for each check. Report those lines; exit 1 after them means a check failed. Exit 1 with no check lines means the script stopped on an error, shown as a message with no traceback. If it stopped after the comment was added, while reading it back, the message begins `comment N was posted`: fix that comment with `--edit N` rather than adding a second one. Whatever the message, look at the work item before running the script again.

## Rules

- Only the main session posts or edits. A subagent may prepare the comment, never post it.
- Never post without the user's approval of the exact text, and never post an image the user has not seen.
- The token is sent to Azure DevOps and nowhere else; the script refuses any other `--org`.
