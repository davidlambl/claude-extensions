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

### 0.2.10 — 2026-10-14

- SKILL.md says that a GET is no proof that a call changes nothing. Some endpoints write on one, such as a duplicate check that registers an identifier it has not seen, answering 200 the first time. Before running a step, the session reads the step, its notes included, and what is known about the endpoint for anything it creates, registers or changes.
- A step that neither says nor is known to write is an ordinary read, and runs as written; logging the call does not count as a write. In production it is a production call like any other. Where the step leaves the input open, the session prefers one the system already holds to a made-up one.
- Outside production, for an endpoint that can write, the session uses an input that leaves the call a pure read, such as one the system already holds, and says so when it replaces the step's own. If none will, it asks first and sends the call once, without `--warm`.
- In production the session never calls an endpoint that can write, whatever its method or input. A value the system already holds does not make it safe there, since a mistyped one leaves a real record behind, so the session offers no other value. It leaves the step out and tells the user it is theirs to run.
- The README and SECURITY.md say the same, and SECURITY.md lists it as a known limit: the scripts cannot tell a GET that writes from one that reads, so a script run by hand makes no such check.
- Four eval cases hold these rules. A duplicate check that registers what it has not seen is left to the user on Production, even with a reference Production already holds. On Test it is kept a read with a reference Test already holds, or the step's own is sent once after the user agrees, never with `--warm`. An ordinary read on Production still runs as written.
- In Git Bash on Windows, SKILL.md, the README and `call.py`'s help lead with the doubled leading slash, `GET //api/widgets/42`, and offer `MSYS_NO_PATHCONV=1` second. Either way, they say to write the script's path, and every other file path, as `C:/Users/...`, which Git Bash passes on as written whether the variable is set or not. With the variable set, it converts no path at all, so a script path written as `/c/Users/...` reached Python as typed and was not found, and an `--out` written that way was made under the root of the drive without an error. Without the variable, it leaves a `/c/Users/...` path that holds an apostrophe or a square bracket as typed.
- `call.py`'s refusal of a path Git Bash has rewritten leads with the doubled leading slash too. Where it offers `MSYS_NO_PATHCONV=1`, it says to write this script's path, and every other file path, as `C:/Users/...`. It used to offer the variable with no word about the script's path.
- When an Insomnia global environment, sub-environment, collection or top-level folder is not found, the message names the ones that came near it, on the same line: those that differ only in letter case, else those that differ only in letter case or in characters it shows as escapes, such as a no-break or zero-width space, else the closest, if they are within one edit for every five characters and three edits at most. It lists three names at most, and says how many more. The Postman source does the same for `--folder`. Neither source chooses one, since a name in another letter case may be another environment, such as production.
- The Insomnia source matches a name exactly. It compared names with PowerShell's `-eq`, which ignores letter case, reads a zero-width space or a soft hyphen as nothing, and reads a no-break space as a space. So `Test V2` chose `Test v2` without a word, and two environments that differ only in letter case could not be chosen at all. Such a name now stops the call, and the message names the one it differs from. An accent stored as a letter and a combining mark, as macOS can write it, still matches the accent typed as one character, and now does in the Postman source's `--folder` too.
- Each name in these messages is quoted, with a quote or a backslash escaped, and a control, format, bidi or invisible character, or a space other than U+0020, shown as its `\uXXXX` escape. A name is cut at 80 characters, so a shared collection can neither break the message's line nor make it long. In the Postman source, a secret in a folder name the message shows, such as a `--vars` value or the base URL's password, is `********`.
- The README points to a new Auto mode section in the repository's README. It says why auto mode's classifier can stop these scripts with `[Code from External]`, for instance on their first run after an update, then lists the ways to let them run, from least trust to most. For one run: `!` at the prompt, `r` under Recently denied in `/permissions`, or a reply that names the source. For every run, still reviewed: an `autoMode.allow` entry that answers only where the code came from. With no review: a `permissions.allow` rule for `call.py` at the exact version installed.
- The section says never to put `*` in place of the version: a `*` matches any text, `..` included, so that rule would let Claude run any Python file with no review. It says that auto mode drops a rule with a flag after `python3`, such as `-I`, and that a call from the PowerShell tool needs a `PowerShell(...)` rule.

### 0.2.9 — 2026-10-13

- The Insomnia source says when its data folder is missing, or holds no `insomnia.Workspace.db`, and how to point `INSOMNIA_DATA` at it. It used to report no matching collection. In Git Bash with `MSYS_NO_PATHCONV=1`, a `/c/Users/...` path is not converted, so write it as `C:/Users/...`.
- The screenshot tests that measure the page in a real browser lay it out at the capture's width themselves. On Windows, Chrome 154's headless page dump gives the page a smaller viewport than its screenshots use, so eight of them failed there although the images were right.

### 0.2.8 — 2026-10-13

- A screenshot has no title bar by default, only the terminal's content, so it no longer looks like a window from any one system. A label given with `--label` opens the image as its first line instead.
- `--frame native` draws the title bar of the system the capture is taken on, and `--frame windows`, `mac` or `linux` draws that one. `--frame auto`, from 0.2.7, is gone: `native` does what it did.

### 0.2.7 — 2026-10-13

- A screenshot's window frame looks like the system it is taken on: a Windows window on Windows, a macOS window on a Mac, a GNOME window on Linux. It was always the macOS frame. `--frame windows`, `mac` or `linux` picks another, on `call.py` and `render.py`.

### 0.2.6 — 2026-10-13

Secret fixes. Earlier versions said a source never prints, writes or draws a secret, that the variables substituted into one are secrets in their own right, and that a failed request's error is scrubbed. These cases broke that, and are fixed:

- A secret the response echoed back trimmed of whitespace, JSON-escaped as `\u0041` or `\/`, or as an HTML character reference such as `&#65;` was printed. Both sources now refuse each of these, as they refused the plain and URL-encoded forms.
- The image could draw a secret in a form the sources never looked for. It decoded a JSON body's escapes, wrote a number such as `1.2345678E7` as `12345678.0`, and drew zero-width and bidi characters as nothing, so a secret split or reversed by them read in clear. It now draws the body as returned. A JSON body is only re-indented, and each invisible, format, bidi and non-ASCII space character is drawn as `\uXXXX`.
- A variable whose value holds a template of its own counted as a secret as written, not as resolved, so a response echoing what it resolved to was printed. Every part substituted into a secret is now a secret as resolved, at every depth.
- In the Insomnia source, a variable substituted into a templated folder header was not a secret in its own right, as 0.2.2 said it was. Now it is.
- A password in the base URL, as in `https://user:password@host`, showed in the record's url. The Postman source also printed it in the error from the URL it could not send. It is now masked, found by its place in the URL's text, so no template in the scheme, host or port can move it out of reach. A user with no password, as in `https://TOKEN@host`, is the credential, and is masked too.
- A failed request's error was masked only where it quoted a secret as is. Both sources now mask it trimmed and percent-encoded too, and withhold the message whole if a secret would still show.
- The Postman source did not check for a client certificate's passphrase. Now it does, like any other secret.
- A header value with a line break, such as the newline at the end of a pasted key, is refused before anything is sent, with a message that does not quote it. A shared collection cannot use one to add headers or a second request.

In both sources:

- A user and password in the base URL go as basic auth, as the Insomnia and Postman apps send them, unless the auth sets `Authorization`. The record's auth reads `basic auth from the base URL as <user>`, or `basic auth from the base URL, with an empty user` for `https://:password@host`.
- An API key or bearer token that resolves to an empty value stops the call before anything is sent. A step meant to send a blank key for a 401 needs a folder set to No Auth instead.
- A template nests at most five levels deep, and a value resolves to at most 65536 characters. A cycle, or a value past either limit, stops the call before anything is sent.
- An error never names a variable found inside another variable's value, which may be a secret. It names the variable that holds it.
- An unencoded `/`, `?` or `#` in the base URL's user or password ends its userinfo early, so no secret is found there, and an error could quote part of it as the host. When the base URL holds an `@` after its scheme, a failed request now prints `the request failed: the base URL holds credentials that could not be parsed or reached; percent-encode any / ? # or @ in its user or password`. It quotes no part of the URL or its host.

In the Insomnia source:

- A top-level folder's own environment is laid over the global one, as Insomnia does. A value that names its own variable, such as `{{ baseUrl }}/v2`, extends the one beneath it, resolved after the rest of its layer in the file's order.
- Variable names are case-sensitive, as in Insomnia. The default base URL variable, `baseUrl`, still finds the one variable spelt `baseURL` or `BaseUrl`, if there is only one.
- A folder set to No Auth counts as no auth.
- An API key set to Add to Cookie goes as a cookie, recorded as `API key in cookie <name>`. One whose name or value would split it into two cookies is refused, unsent.
- A client certificate's host with a port matches as Insomnia matches it, with 443 assumed when none is given.
- `sent` and `sent_utc` are ISO 8601 in every regional format, and the base URL and the path are read the same in each, Thai included.
- Output reaches `call.py` as UTF-8 on any console code page.
- A response whose charset .NET does not know, such as `utf8`, is read as UTF-8 instead of being lost.
- ISO dates and booleans in Insomnia's data are sent as written.
- `Content-Type` is no longer listed as sent on a GET, which carries no content. A header name .NET rejects is refused rather than dropped.
- The environment, collection and folder names are labels, and no longer a reason to refuse a record.
- A secret that also sits in the base URL, the typed path or the auth's text shows as `********` in the record's `url`, `path` and `auth`, instead of refusing every call. A tenant id sent in a templated header and named in the URL is one. A response that echoes it is still refused.
- A templated value of a standard header that never carries a credential is shown as resolved and is not a secret. These are `Accept`, `Accept-Charset`, `Accept-Encoding`, `Accept-Language`, `Cache-Control`, `Connection`, `Content-Language`, `Content-Length`, `Content-Type`, `Host`, `Origin`, `Pragma`, `Referer`, `User-Agent`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto` and `X-Requested-With`, in any letter case. Every other templated header value is still a secret.

In the Postman source:

- Collection exports in schema v2.0 are read as well as v2.1, and another schema is refused by name.
- A folder named with `--folder` that has no auth of its own takes the collection's, as in Postman.
- A basic password typed blank beside a username is sent, for an API that takes the key as the user, as `curl -u key:` does. That user is then the credential, shown as `********`. A templated password that resolves to an empty value stops the call, since it usually means a `--vars` file is missing.
- Basic auth with an empty user reads `basic auth, with an empty user`.
- A `--vars` value, or an environment value typed secret, in the base URL or in a key's name shows there as `********`, where the record used to be refused.
- Every value substituted into a base URL user with no password is a secret at every depth, as for a password. Each is found by where it was substituted, as the Insomnia source finds it.
- The record's own fields and its labels are left out of the refusal check, as in the Insomnia source. The fields are `source`, `method`, `sent` and `sent_utc`, and the labels are the environment, collection and folder names. So a `--vars` value equal to today's date no longer refuses every call.
- When the base URL variable is not defined, the one variable whose name matches it ignoring case is used, such as `baseURL`. With two or more, the message names them.
- A path with a space or a character outside ASCII is sent percent-encoded, as Postman sends it.
- An export saved with a byte order mark is read.
- A body is read as the Insomnia source reads it: by its byte order mark, then by a declared charset Python knows, then as UTF-8. The byte order mark is dropped, so a JSON body after one is still laid out as JSON.
- Output is UTF-8 on Windows.

In `call.py` and `render.py`:

- `call.py` writes the record before the screenshot, so a failed capture keeps it. The script exits 1 with a message naming the record.
- `call.py` writes the record as ASCII-escaped JSON, so no invisible or bidi character a response carried can hide in the file.
- `call.py` reads the source's record whatever line or paragraph separators the body holds.
- `call.py` reports a source that does not finish in one line, with no traceback and none of the source's partial output.
- `call.py` stops on a path Git Bash has rewritten into a Windows path, where setting `MSYS_NO_PATHCONV` inside `call.py` had no effect. It reads a doubled leading slash as one.
- `render.py` takes a relative record or `--out` path.
- `render.py` finds Edge on macOS and Linux.
- `render.py` measures lines in display cells, so CJK, emoji and tab-indented lines end in `...` instead of being cut at the window's edge. A row the font draws wider than counted ends in an ellipsis at the edge, and so does a long title, instead of running under the window's buttons.
- Every row and the title are drawn left to right in the order their characters are written, so right-to-left text cannot reorder the digits and punctuation around it.
- Each row is clipped to its own height, so stacked combining marks cannot paint over another row.
- `render.py` draws U+FFFC, the object replacement character, as its escape. Many fonts draw it as a space, so it joins the non-ASCII spaces and the blank Braille pattern U+2800, which are escaped too.
- A `{{title}}` or `{{body}}` written in a label, a name or a body is drawn as text.
- A text body loses only the empty lines at its start and end. Every other character is drawn, its indentation and trailing spaces included, with control characters and separators as escapes. A body of line breaks alone is drawn as their escapes, and only a body of zero length as `(empty body)`.
- A body holding a lone surrogate escape, or JSON nested too deep for Python to parse, is drawn, where `--shot` used to fail.

In the docs:

- The README's demo commands write into `screenshots/`, so they refresh the committed images.
- The README documents exit code 2, for a command line `call.py` cannot parse.
- SKILL.md says that `--warm` sends a second, untimed GET.
- The README has the line that installs it.
- SECURITY.md, the README and SKILL.md say what counts as a secret and what is configuration, shown as resolved. They say plainly that a key belongs in the auth, not in the base URL's path or query.

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

### 0.1.11 — 2026-10-14

- `crop.py --edges N` writes the first and last N rows of the result at its full width, as two strips named after it, such as `shot-cropped-top.png` and `shot-cropped-bottom.png`, in a fresh temporary folder, never beside the evidence. The JSON line gives their paths under `edges`. With no `--box` or `--squeeze` it cuts them from the input, to check a result already written. An N at or past the height makes each strip the whole image, and `--edges` does not go with `--grid`. A run that fails part way leaves neither the strips nor the result behind.
- SKILL.md's edge check is that one command, where it had the session build two `--box` arguments from the width and height in the JSON line. On Windows, Git Bash read that line with a trailing carriage return, the shell arithmetic failed, and the first attempt wrote nothing.
- The README's options table and SECURITY.md say where the strips go: a fresh folder in the system's temporary folder, which `crop.py` leaves there.
- The README points to the Auto mode section in the repository's README, which says why auto mode's classifier can stop these scripts with `[Code from External]`, and how to let them run. That section says to write no allow rule for `post.py`, which would let Claude post to Azure DevOps as you with no review.

### 0.1.10 — 2026-10-14

- SKILL.md says which edges of the check's strips to judge for clipping: the result's own, the top of the top strip and the bottom of the bottom strip. A line sliced where a strip itself was cut is the strip's cut, so a correct edge is not moved for it. On the real SSMS capture on Windows, the bottom strip of a correct result began with a column header sliced in half.

### 0.1.9 — 2026-10-14

- SKILL.md says how to check a box's edge, since the grid rules only every 100 pixels and a thin strip of chrome falls between its rules. The session cuts the top and bottom 80 pixels of the result into scratch files, outside the evidence folder, and looks at them at full size, judging only whether chrome is still visible and whether the first or last line of content is clipped, and moves the edge and runs again from the original if so. A result a little wider than it could be is left alone.
- The cut-the-window-down eval's tab-strip grader passes a squeezed copy that is boxed and squeezed again with edges from its own grid, which is sound. It fails a squeezed copy boxed without squeezing again, or boxed with edges read from the original. Strips cut into scratch files to check the edges are not counted.

### 0.1.8 — 2026-10-14

- SKILL.md says that a squeeze keeps window chrome such as a tab strip, which can show other tabs' titles and holds open the blank columns beneath it. When chrome remains, the session boxes it off from the original in the same run as the squeeze, with the edge read from the original's grid. On a real SSMS window on Windows that took the result from 1508 x 269 to 1236 x 243, without the other tabs' server names.
- The README's SSMS example says the same.

### 0.1.7 — 2026-10-13

- `crop.py` cuts a PNG screenshot down before it is posted, with the standard library alone. `--squeeze` closes up every run of identical rows, then of identical columns, longer than `--gap` (16 pixels) to the gap, so a full-screen window comes down to its content with every row and column that differs from its neighbour kept; `--box LEFT,TOP,RIGHT,BOTTOM` keeps one region; `--grid` writes a copy ruled every 100 pixels, to read a box from. It writes beside the input or at `--out`, never over the input, and replaces a file `--out` names only with `--overwrite`. It prints one line of JSON with the sizes, the box as used and what was squeezed.
- It reads any PNG that is not interlaced, at every depth and colour type, and writes 8-bit RGB or RGBA with the source's colour chunks carried over. A JPEG, an interlaced PNG, a damaged file, or one past 40 megapixels, 65535 pixels a side or 256 MB of pixel data, is refused with one line, and a crafted file cannot take it past those bounds.
- SKILL.md says when to cut an image down: squeeze first, box with the grid after, never squeeze a chart or anything whose size carries its meaning, say in the caption when blank space was closed up, and post the image as given when `crop.py` refuses it, never cropping another way.

### 0.1.6 — 2026-10-13

- The demo screenshot has no title bar, as api-call's screenshots have by default.

### 0.1.5 — 2026-10-13

- The demo screenshot's window frame looks like the system it is taken on, as api-call's does.

### 0.1.4 — 2026-10-13

Secret fixes. 0.1.0 said the token goes only to Azure DevOps, and that an image is named by its file name alone. These cases broke that, and are fixed:

- A redirect was followed, and Python's urllib resends the token to whatever host a redirect names. A redirect is now an error, never followed.
- The URL Azure DevOps answered an upload with was embedded as given, and its image fetched with the token from any Azure DevOps host, another organization's included. It must now be an attachment of the `--org`'s organization over https, at `dev.azure.com/ORG` or `ORG.visualstudio.com`, in plain URL characters and holding no token. Otherwise the comment is not posted, and the URL is never requested.
- A drive-relative image name such as `C:shot.png` could reach a file outside the images folder on Windows. A name with a colon is now refused.

In `post.py`:

- Everything it prints has the token masked, and the Basic value a personal access token goes as, before any of it is cut short. Each is masked as is, percent-encoded, JSON-escaped or as HTML character references. An error body in UTF-16 is decoded before it is masked. An HTTP error is closed once read, so Python 3.14 cannot print its reason phrase in a warning.
- An error is a plain message with no traceback. One that comes after the comment was added begins `comment N was posted`, or `the comment was posted` when Azure DevOps answered without a usable id, so it is not posted twice.
- When the comment request fails in a way that does not say it was refused, such as a 5xx, a dropped connection or an answer that is not the JSON expected, the message begins `the comment may have been posted`, or `comment N may have been posted` with `--edit`.
- A personal access token in `AZURE_DEVOPS_TOKEN` goes as Basic. An Entra token still goes as Bearer.
- A token holding a space, a control character or a character outside ASCII is refused before it is sent.
- Where Python has no root certificates of its own, `post.py` uses certifi's if it is installed.
- The images-rendered check finds each uploaded image by its URL, so a comment can show an image twice or carry another.
- Output is UTF-8 whatever the console code page.

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

## linqpad

### 0.1.0 — 2026-10-20

- First release. Runs C# and .NET code from a file with LINQPad 9's `lprun9`, as a .NET 10 file-based app (a `.cs` file of top-level statements, which `dotnet run` runs too) or as a `.linq` script, and answers from what the run printed.
- A PreToolUse hook on the Bash and PowerShell tools asks before any `lprun9` run that touches a saved LINQPad connection: one with `-cxname=`, or of a script whose first 8 KB hold a `<Connection>`. The prompt names the connection. A run it cannot read asks too, and in the `bypassPermissions` and `dontAsk` modes each ask becomes a deny.
- The hook reads only the scripts a command names, and the skill never opens `ConnectionsV2.xml`, where LINQPad keeps the saved connections and their passwords. Reads from a database go through sql-read.
- On macOS `lprun9` is in LINQPad 9's app bundle, `/Applications/LINQPad 9.app/Contents/MacOS/lprun9`, and the command names it by that path. LINQPad 9.10.20 there ran a `.cs` file only with `-lang=program`, which the skill passes when lprun9 asks for a language; 9.11.10 on Windows ran it bare.

## sql-read

### 0.1.0 — 2026-10-20

- First release. `read.py` reads from a SQL Server database through a connection saved in LINQPad, production included, with LINQPad 9's `lprun9` running the SQL and supplying the credential: only the connection's name reaches it, and the list's `Password` element is never read out. `connections` lists the saved connections, `plan` shows what a run would do without running it, `query` runs one SELECT, `check` lists what the login can write, and `rules` prints the allow rules that let `--allow-writable-login` past auto mode's classifier.
- The guard accepts one `SELECT` or `WITH` statement, with only `DECLARE @…` and `SET @…` before it, no markup and no word from the deny list, and refuses anything else before a connection is looked up. A refusal's reason names the word, the keyword or the line number, never the SQL.
- The probe, in the batch before the SELECT, stops the run with exit 5 and nothing run when the login holds `sysadmin`, `db_owner`, `db_datawriter` or `db_ddladmin`, a database permission to write, or a write permission on a schema other than an empty one a fixed database role owns, so a `db_datareader` login passes. Only `--allow-writable-login` leaves it out, which Claude passes for a connection the user lists in `SQL_READ_WRITABLE_OK`; the probe's refusal, and `check`'s, say so for a listed connection. The prompt and `plan` warn when a connection names no database. `SET ROWCOUNT` caps the rows at 200, and the run has a timeout and a lock timeout.
- A PreToolUse hook on the Bash and PowerShell tools asks before every `query` and `check`, with the plan as its reason: the connection, its server and database, the production flag, the row cap and the SQL. `plan` and `connections` never prompt. In the `bypassPermissions` and `dontAsk` modes it refuses instead.
- `lprun9` is found by `--lprun`, the environment variable `LPRUN9`, the `PATH`, then where LINQPad 9 installs it: under Program Files or `%LOCALAPPDATA%` on Windows, in its app bundle under `/Applications` or `~/Applications` on macOS. The connection list is `%APPDATA%\LINQPad\ConnectionsV2.xml` where `APPDATA` is set, else `~/Library/Application Support/LINQPad/ConnectionsV2.xml` on macOS. Exit 3 lists every place looked at.
