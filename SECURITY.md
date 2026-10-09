# Security

## Reporting a vulnerability

Report a vulnerability privately through GitHub, under this repository's **Security** tab, with **Report a vulnerability**. Please don't open a public issue for one.

Tell me which extension and version, what an attacker can do, and the smallest way to reproduce it. A proof of concept helps; a working exploit against someone else's system does not, so please don't include one.

I maintain this in my own time, so expect a first reply within a week. Fixes ship as a new version of the affected extension, noted in [CHANGELOG.md](CHANGELOG.md).

## Supported versions

The newest version of each extension is the supported one. There are no backports; the fix is the next release.

## What these extensions touch

They run on your machine, with your access. That is the point of them, and it is also the risk.

- **api-call** reads Insomnia's local data and Postman's exports, including the API keys, bearer tokens, basic-auth passwords and client certificates a collection holds. It sends one GET to the environment you name, which may be production, or two with `--warm`: one untimed, then the one it records.
- **ado-evidence** uploads files and posts a comment to Azure DevOps under your identity, with a token from the Azure CLI or `AZURE_DEVOPS_TOKEN`.
- **context-bar** reads this session's token counts and draws them. To count the overhead as `/context` does, it has Claude Code send token-count requests to the Anthropic API, on the session's own sign-in, one per tool and memory file, the first time it measures a session and again whenever the overhead changes. It makes no other network calls and handles no credentials itself.

## What the code is meant to guarantee

These are the properties a bug report should test against:

- A key, token or certificate passphrase is never printed, written or drawn. Each api-call source keeps secrets inside its own process, masks them in the record it prints, and refuses to print at all when a secret would appear in the output in any form a response could carry it back in: as is, trimmed of surrounding whitespace, percent-encoded, or in the body with its JSON escapes decoded, which is how the image draws it. The variables substituted into a secret count as secrets in their own right.
- A failed request's error is scrubbed before it is shown. Every secret in it is masked, as is, trimmed and percent-encoded, and if one would still show, the message is withheld whole.
- A shared collection cannot add headers or a second request to the GET. Each api-call source refuses a header whose name or value holds a line break, before anything is sent.
- A redirect is recorded as its 3xx response and never followed, so a key is sent only to the host the collection named.
- ado-evidence sends your token only to `dev.azure.com` or `<org>.visualstudio.com` over https. It refuses any other `--org` or attachment URL, and treats a redirect as an error, never following it. An image is named by its file name alone, with no folder, drive or colon, and looked up in the images folder, so a comment cannot reach a file elsewhere, on Windows as on macOS and Linux.
- Neither skill writes to Insomnia's data or Postman's exports.

## Known limits, by design

These are not bugs. They are the edges of the guarantees above.

- **A literal header value is not a secret to api-call.** A collection header whose value is typed in rather than drawn from a variable is shown as typed. Keep secrets in environment variables.
- **A value under six characters is not checked for.** It cannot be told apart from ordinary text in a response body.
- **The response body is drawn as returned.** Nothing redacts what the API sends back, so look at an image before you share it.
- **Posting acts as you.** Preview with `--dry-run` and approve the text before it goes up.
- **An eval or screenshot run calls real endpoints and real models** with your credentials.
