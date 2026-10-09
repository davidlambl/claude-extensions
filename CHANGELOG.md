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

### 0.2.6 — 2026-10-09

Secret fixes. Earlier versions said a source never prints a secret and scrubs a failed request's error. These cases broke that, and are fixed:

- A secret the response echoed back trimmed of whitespace, or JSON-escaped as `\u0041` or `\/`, was printed, and the image drew it in clear, since it decodes a JSON body's escapes. Both sources now refuse it, as they refused the plain and URL-encoded forms.
- A failed request's error was masked only where it quoted a secret as is. Both sources now mask it trimmed and percent-encoded too, and withhold the message whole if a secret would still show.
- In the Insomnia source, a variable substituted into a templated folder header was not a secret in its own right, as 0.2.2 said it was. Now it is.
- The Postman source did not check for a client certificate's passphrase. Now it does, like any other secret.
- A header value with a line break, such as the newline at the end of a pasted key, is refused before anything is sent, with a message that does not quote it. A shared collection cannot use one to add headers or a second request.

In the Insomnia source:

- A top-level folder's own environment is laid over the global one, as Insomnia does. A value that names its own variable, such as `{{ baseUrl }}/v2`, extends the one beneath it.
- A folder set to No Auth counts as no auth.
- An API key set to Add to Cookie goes as a cookie, recorded as `API key in cookie <name>`.
- A client certificate's host with a port matches as Insomnia matches it, with 443 assumed when none is given.
- `sent` and `sent_utc` are ISO 8601 in every regional format.
- Output reaches `call.py` as UTF-8 on any console code page.
- A response whose charset .NET does not know, such as `utf8`, is read as UTF-8 instead of being lost.
- ISO dates and booleans in Insomnia's data are sent as written.
- `Content-Type` is no longer listed as sent on a GET, which carries no content. A header name .NET rejects is refused rather than dropped.

In the Postman source:

- Collection exports in schema v2.0 are read as well as v2.1, and another schema is refused by name.
- A folder named with `--folder` that has no auth of its own takes the collection's, as in Postman.
- An API key, bearer token or basic password that resolves to an empty value stops the call before anything is sent. A step meant to send a blank key for a 401 needs a folder set to No Auth instead.
- A path with a space or a character outside ASCII is sent percent-encoded, as Postman sends it.
- An export saved with a byte order mark is read, and a byte order mark before a JSON body is dropped, so the body still pretty-prints.
- Output is UTF-8 on Windows.

In `call.py` and `render.py`:

- `call.py` writes the record before the screenshot, so a failed capture keeps it. The script exits 1 with a message naming the record.
- `call.py` reads the source's record whatever line or paragraph separators the body holds.
- `call.py` stops on a path Git Bash has rewritten into a Windows path, where setting `MSYS_NO_PATHCONV` inside `call.py` had no effect. It reads a doubled leading slash as one.
- `render.py` takes a relative record or `--out` path.
- `render.py` finds Edge on macOS and Linux.
- `render.py` measures lines in display cells, so CJK, emoji and tab-indented lines end in `...` instead of being cut at the window's edge.

In the docs:

- The README's demo commands write into `screenshots/`, so they refresh the committed images.
- The README documents exit code 2, for a command line `call.py` cannot parse.
- SKILL.md says that `--warm` sends a second, untimed GET.
- The README has the line that installs it.

### 0.2.5 — 2026-10-09

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

### 0.1.4 — 2026-10-09

Secret fixes. 0.1.0 said the token goes only to Azure DevOps, and that an image is named by its file name alone. These cases broke that, and are fixed:

- A redirect was followed, and Python's urllib resends the token to whatever host a redirect names. A redirect is now an error, never followed.
- A drive-relative image name such as `C:shot.png` could reach a file outside the images folder on Windows. A name with a colon is now refused.

In `post.py`:

- The URL Azure DevOps answers an upload with is embedded only if it is an attachment on Azure DevOps over https, in plain URL characters. Otherwise the comment is not posted.
- A personal access token in `AZURE_DEVOPS_TOKEN` goes as Basic. An Entra token still goes as Bearer.
- Where Python has no root certificates of its own, `post.py` uses certifi's if it is installed.
- The images-rendered check finds each uploaded image by its URL, so a comment can show an image twice or carry another.
- Output is UTF-8 whatever the console code page.
- An error is a plain message, scrubbed of the token, with no traceback. One that comes after the comment was added begins `comment N was posted`.

In the docs and evals:

- The demo image shows the command it ran, which is run exactly as drawn.
- SKILL.md says that exit 1 with no check lines means the script stopped on an error, perhaps after the comment was added, and to look at the work item before running it again.
- The README has the line that installs it.
- The eval's check for a reply that claims it posted no longer fails a correct reply that says "I've added a caption" or "once I've posted it". It now catches a claim written with a curly apostrophe, in the passive, or as "Posted!".
- The preview check now fails a reply that would post after a dry run without waiting for the user's approval.

### 0.1.3 — 2026-10-09

- Added a demo screenshot of the dry-run preview, and an eval suite covering whether the skill fires on a request to post evidence and holds back until the text is approved.

### 0.1.2 — 2026-10-08

- Licensed under MIT, declared in the manifest.

### 0.1.1 — 2026-10-08

- Added `homepage`, `repository` and `keywords`.

### 0.1.0 — 2026-10-08

- First release. Uploads images as work item attachments, posts or revises a captioned markdown comment, then reads it back and checks that every image rendered.
- The token goes only to `dev.azure.com` or `<org>.visualstudio.com`, and an image is named by its file name alone.
