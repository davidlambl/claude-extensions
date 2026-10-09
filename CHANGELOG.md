# Changelog

Each extension carries its own version in its `plugin.json`, and they move independently. An install stays on its cached copy until that string changes, so every published change raises it.

To take a new version: `claude plugin marketplace update davidlambl`, then `claude plugin update <name>@davidlambl`, then `/reload-plugins`.

## context-bar

### 0.3.3 — 2026-10-08

- Licensed under MIT, declared in the manifest.

### 0.3.2 — 2026-10-08

- Added `homepage`, `repository` and `keywords`, which `/plugin` shows before and after install.

### 0.3.1 — 2026-10-06

- With auto-compaction off, the meter fills toward the end of the window less the buffer `/compact` needs, so its free space and percentage read as `/context` does.

### 0.3.0 — 2026-10-06

- `/context-bar` answers with the figures as text where no card can draw, such as VS Code, a `-p` run or the Agent SDK, and the card gained a pane for surfaces without the band.

### 0.2.0 — 2026-10-06

- The overhead is counted the way `/context` counts it, rather than from the free local estimates, which in a long session ran to nearly twice the real figure. It is recounted only when it changes.

### 0.1.0 — 2026-10-06

- First release. The context window as a meter above the prompt, with the overhead broken down.

## api-call

Replaces api-shots, which was published briefly at 0.1.0 and 0.1.1 and did the same job for Insomnia only, with Azure DevOps posting built in. The posting half is now [ado-evidence](skills/ado-evidence/).

### 0.2.5 — 2026-10-08

- Added an eval suite, run with `claude plugin eval`, covering whether the skill fires on a test-step request and stays out of the way of ordinary API coding.

### 0.2.4 — 2026-10-08

- Licensed under MIT, declared in the manifest.

### 0.2.3 — 2026-10-08

- Added `homepage`, `repository` and `keywords`.

### 0.2.2 — 2026-10-08

- A variable substituted into a composite auth value or header is now a secret in its own right, so a response echoing that part alone is caught.
- The refusal check reads the record's raw strings, plain and URL-encoded, as well as its JSON, where escaping could hide a value holding a quote or a backslash.
- A build-time failure in the Postman source is masked like every other error.

### 0.2.1 — 2026-10-08

- A redirect is recorded as its 3xx response with its `Location` header, never followed. Both Python's urllib and .NET's HttpClient resend auth headers to whatever host a redirect names.
- Both sources send and show a `User-Agent`, which HttpClient omits and some servers require.
- The Insomnia source ends an error with its plain message, every secret masked, instead of PowerShell's formatted error view.

### 0.2.0 — 2026-10-08

- First release under this name. One GET through an Insomnia or Postman collection, recorded as JSON with every secret masked, and screenshotted with `--shot`.
- The record is the documented interface between a source and whatever reads it, so a third collection tool or another renderer needs no change elsewhere.
- Postman support is new: environment and collection exports, API key, bearer and basic auth, a `--vars` file for secrets kept out of an export, and PEM client certificates.
- The window frame moved into the skill as `frame.html`, which `tools/screenshots` now reads from there.

## ado-evidence

Split out of api-shots, so that where the records and images go is the user's choice.

### 0.1.3 — 2026-10-08

- Added a demo screenshot of the dry-run preview, and an eval suite covering whether the skill fires on a request to post evidence and holds back until the text is approved.

### 0.1.2 — 2026-10-08

- Licensed under MIT, declared in the manifest.

### 0.1.1 — 2026-10-08

- Added `homepage`, `repository` and `keywords`.

### 0.1.0 — 2026-10-08

- First release. Uploads images as work item attachments, posts or revises a captioned markdown comment, then reads it back and checks that every image rendered.
- The token goes only to `dev.azure.com` or `<org>.visualstudio.com`, and an image is named by its file name alone.
