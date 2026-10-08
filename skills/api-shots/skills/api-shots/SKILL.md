---
name: api-shots
description: Run a ticket's API test steps the way the team's Insomnia collection would, screenshot each request and response (optionally against a "before" environment such as production), and post them as a captioned evidence comment on an Azure DevOps work item. Use when asked to perform QA or post-deployment test steps that call an HTTP API, or to record API test evidence on a work item.
---

# API shots

Runs GET test steps through the user's own Insomnia setup, captures each exchange as an image, and posts the images to Azure DevOps with captions. The scripts are in `scripts/` under this skill's base directory.

## What it needs

- Insomnia desktop on this machine, with a global environment that holds the base URL (default variable `baseUrl`) and a collection whose top-level folder carries the API key or bearer auth. Client certificates the collection holds for the host are sent too.
- PowerShell 7 (`pwsh`), Python 3, and Chrome, Chromium or Edge.
- For posting: the Azure CLI signed in (`az login`), or `AZURE_DEVOPS_TOKEN`.

## Run the steps

1. Read the steps from the work item (Testing Considerations or Post-Deployment Testing). Use only GET steps; anything else is the user's to run.
2. For each step, run `shoot.py`. Pass `--before-env` when the change has not reached that environment yet (usually production before a release), so each step gets a before and an after image. Pass `--warm` so a cold start after a deploy does not show in the timing.

   ```sh
   python3 <base>/scripts/shoot.py --env "API - Test" --before-env "API - Prod" --collection "API" \
     --label "After: Test (this change)" --before-label "Before: Prod (current release)" \
     --expect 404 --before-expect 400 --warm --out <dir> --name step1 GET /api/widgets/not-a-number
   ```

   Exit 3 means a status did not match `--expect`; the image is still written. Report a mismatch as a finding; never retake until it passes.
3. Look at every image before using it. The response body is drawn as returned, so check it holds no customer data.

## Post the evidence

1. Write the comment in markdown: one intro line (environment, build or deploy, what the codes mean), then for each image a bold lead caption directly above it. Put `{{image:<file>.png}}` where each image goes.
2. Preview with `--dry-run` and show the user the full text. Post only after the user approves that exact text:

   ```sh
   python3 <base>/scripts/post_ado.py --org https://dev.azure.com/<org> --project <project> --work-item <id> \
     --comment <dir>/comment.md --dry-run
   ```

   Then the same command without `--dry-run`. To revise a comment already posted, add `--edit <comment id>`.
3. The script reads the comment back and checks that each image renders and matches the local file. Report those checks.

## Rules

- GET only. Production calls only when the user asks for them, and only with inputs that do not return customer data.
- Never print, log or pass a key, token or certificate passphrase anywhere. The scripts keep them inside the PowerShell process and refuse to print output that contains one.
- Only the main session posts or edits comments. A subagent may run `shoot.py`, but returns the images and results, never posts.
