---
name: api-call
description: Call an HTTP API the way the team's Insomnia or Postman collection would, with its environment, auth and client certificate, and record the exchange as JSON, with a screenshot on request. Use when asked to run an API test step, check an endpoint in an environment, compare an endpoint across environments, or capture evidence of a request and its response.
---

# API call

Sends one GET (two with `--warm`, the first untimed) through the user's own collection tool setup and writes a record of the exchange, and an image of it when asked. The scripts are in `scripts/` under this skill's base directory; `call.py` is the entry point.

## What it needs

- Python 3.11 or later.
- Insomnia on this machine, with a global environment that holds the base URL (variable `baseUrl` unless told otherwise) and a collection whose top-level folder carries the API key or bearer auth, plus PowerShell 7 (`pwsh`); or Postman exports, an environment file and a collection file.
- For `--shot`: Chrome, Chromium or Edge.

## Run a step

```sh
python3 <base>/scripts/call.py --via insomnia --env "API - Test" --collection "API" \
  --expect 200 --warm --shot --out <dir> --name step1 GET /api/widgets/42
```

- `--via postman` takes the export files instead: `--env "<dir>/API - Test.postman_environment.json" --collection "<dir>/API.postman_collection.json"`. Secrets kept out of the export go in a `--vars` file; a client certificate is `--cert` and `--key`, in PEM.
- `--expect` is the status the step should return. A mismatch exits 3; the record and the image are still written. Report a mismatch as a finding; never retake until it passes. A redirect is recorded as its 3xx, never followed.
- `--warm` sends once untimed first, so a cold start after a deploy does not show in the timing.
- `--label` leads the image's title, such as `"After: Test (this change)"`. For a before-and-after pair, run the step twice, once per environment, with a label for each.
- In Git Bash on Windows, which is also the shell Claude Code's Bash tool uses there, put `MSYS_NO_PATHCONV=1` in front of the command, or write the path with a doubled leading slash, as `GET //api/widgets/42`, which `call.py` reads as `/api/widgets/42`. Otherwise Git Bash turns `/api/widgets/42` into a Windows path such as `C:/Program Files/Git/api/widgets/42` before `call.py` starts, and `call.py` stops with exit 1 rather than send it.
- Only GET. Anything else is the user's to run.

## What comes out

`<dir>/<name>.json` is the record: the source, environment and collection, the method and path, the URL and request headers as sent with every secret as `********`, the client certificate's file name, the status, how long it took, when it was sent, the response headers and the body. With `--shot`, `<dir>/<name>.png` draws the same, and the `.html` it was captured from sits beside it. The script prints one line of JSON with the status, the verdict and the file paths.

The record is written before the screenshot is taken. If the screenshot fails, `<name>.json` is still written, without `png`, and the script exits 1 with a message naming the record and the status.

Look at every image before using it: the body is drawn as returned, so check it holds nothing that should not be shared.

## What happens next

The record and the image are files. Where they go is the user's call: a work item, a pull request, a message, a report. Say where they are and what they show, and ask when it is not already clear. If the ado-evidence skill is installed, it posts images with captions to an Azure DevOps work item.

## Rules

- Production calls only when the user asks for them, and only with inputs that do not return customer data.
- Never print, log or pass a key, token or certificate passphrase anywhere. The sources keep them in their own process and refuse to print output that contains one; keep it that way.
- The sources read Insomnia's data and Postman's exports and never write them.
