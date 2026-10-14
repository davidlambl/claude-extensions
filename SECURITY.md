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
- **ado-evidence** uploads files and posts a comment to Azure DevOps under your identity, with a token from the Azure CLI or `AZURE_DEVOPS_TOKEN`. Its `crop.py` reads one PNG and writes one beside it, or where `--out` says, never over the input, and reaches no network.
- **context-bar** reads this session's token counts and draws them. To count the overhead as `/context` does, it has Claude Code send token-count requests to the Anthropic API, on the session's own sign-in, one per tool and memory file, the first time it measures a session and again whenever the overhead changes. It makes no other network calls and handles no credentials itself.

## What the code is meant to guarantee

These are the properties a bug report should test against:

- A key, token or certificate passphrase is never printed, written or drawn. Each api-call source keeps secrets inside its own process and masks them in the record it prints. It refuses to print at all when a secret would appear in its output, and then `call.py` writes nothing.
- A secret is a value in a credential position. That is an API key's value, in a header, a query parameter or a cookie; a bearer token; a basic-auth password and the basic token; any header value with a template in it, save for the standard headers below; a client certificate's passphrase; and a password in the base URL's userinfo. A user that goes with no password is the credential too: a basic-auth user with a blank password, or a userinfo user alone, as in `https://TOKEN@host`, which is how GitHub takes a token. Each counts with every part substituted into it, at every depth. Every value in a `--vars` file, and every Postman environment value typed secret, is a secret wherever it lands.
- Everything else is configuration, shown as resolved and never refused for being echoed. That covers hosts, ports, paths and the rest of the base URL, header names, an API key's name, the bearer prefix, a basic-auth user that has a password, and the environment, collection and folder names.
- Only the Insomnia source reads a collection's headers. A templated value of a standard header that never carries a credential is configuration, not a secret: `Accept`, `Accept-Charset`, `Accept-Encoding`, `Accept-Language`, `Cache-Control`, `Connection`, `Content-Language`, `Content-Length`, `Content-Type`, `Host`, `Origin`, `Pragma`, `Referer`, `User-Agent`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto` and `X-Requested-With`, in any letter case. Every other templated header value is a secret.
- A secret that is also part of the configuration a record shows is masked there, not refused. The Insomnia source masks it in the record's `url`, `path` and `auth`, as for a tenant id sent in a templated header and named in the base URL. The Postman source masks it in the base URL, a key's name and a basic-auth user, and refuses a record whose typed path holds one. A response that echoes it is still refused.
- A password in the base URL is masked in the record's url by its place in the text, so nothing a template puts in the scheme, host or port can make it show.
- A secret is looked for in its own letter case, in one layer of encoding: as is, trimmed of surrounding whitespace, percent-encoded, JSON-escaped, or as HTML character references.
- A failed request's error is scrubbed before it is shown. Every secret in it is masked, as is, trimmed and percent-encoded, and if one would still show, the message is withheld whole. When the base URL holds an `@` after its scheme, a failed request prints `the request failed: the base URL holds credentials that could not be parsed or reached; percent-encode any / ? # or @ in its user or password` instead, and quotes no part of the URL or its host.
- The image draws the body exactly as the API returned it, so a secret can show there only in a form the sources look for. A JSON body is only re-indented, every string, number and literal copied as written. Every invisible, format, bidi and non-ASCII space character is drawn as its `\uXXXX` escape, and so is each other character that draws as a space: the blank Braille pattern U+2800 and the object replacement character U+FFFC. Each row is clipped to its own height and cut with an ellipsis where it is too long, and every row runs left to right in the order its characters are written. The sources also check the body with its escapes decoded; that is defense in depth, not a description of the image.
- The record file is ASCII-escaped JSON, so no invisible or bidi character can hide in it.
- A collection cannot make a source resolve without end. Templates nest at most five levels deep, and a value resolves to at most 65536 characters. Past either, the call stops before anything is sent.
- A shared collection cannot add headers or a second request to the GET. Each api-call source refuses a header whose name or value holds a line break, before anything is sent. The Insomnia source also refuses an API key cookie whose name or value would split it into two cookies.
- A redirect is recorded as its 3xx response and never followed, so a key is sent only to the host the collection named.
- ado-evidence sends your token only to Azure DevOps over https. It goes to the `--org`, which must be `https://dev.azure.com/<org>` or `https://<org>.visualstudio.com`, and to the attachments Azure DevOps answers with. An attachment URL must belong to that organization, at either of its two addresses, and hold no token, or the comment is not posted and the URL is never requested. It refuses any other `--org`, and treats a redirect as an error, never following it.
- Everything ado-evidence prints has the token masked before any of it is cut short, in every form listed above for api-call.
- ado-evidence names an image by its file name alone, with no folder, drive or colon, and looks it up in the images folder, so a comment cannot reach a file elsewhere, on Windows as on macOS and Linux.
- Neither skill writes to Insomnia's data or Postman's exports.
- `crop.py`'s output holds only rows and columns of its input, in their order. A row or column is dropped only when it is identical to the one before it and more than `--gap` of them run together; nothing is blended, drawn or moved, and `--grid`'s ruled copy is for reading coordinates, never evidence. It never writes over its input, and replaces a file `--out` names only with `--overwrite`. A PNG's dimensions are checked before anything is allocated for it, 40 megapixels and 256 MB of filtered pixel data at most, no more than that and a byte is read from the file, its compressed data inflates to no more than its header declares, and a bad CRC on a critical chunk or a file cut short is refused.

## Known limits, by design

These are not bugs. They are the edges of the guarantees above.

- **A key belongs in the auth, not in the base URL.** A key written into the base URL's path or query, such as an Azure Functions `?code=`, is configuration to api-call. It shows in the record and the image, and a response that echoes it is printed. Put it in the auth, as an API key in a query parameter.
- **Percent-encode a user or password in the base URL.** An unencoded `/`, `?` or `#` in one ends the host early, so the request goes to a host made of part of the credential, and the record shows it. Only the error message is guarded.
- **A literal header value is not a secret to api-call.** A collection header whose value is typed in rather than drawn from a variable is shown as typed. In the Insomnia source, a templated value of a standard header such as `Referer` is shown as resolved. Keep secrets in environment variables.
- **The fixed request headers are checked like any other text.** Both sources send `Accept: application/json` and `User-Agent: api-call`, so a secret equal to one of those names or values, such as a `--vars` value of `application/json`, refuses every call.
- **A value under six characters is not checked for.** It cannot be told apart from ordinary text in a response body. An error message still masks it, which can garble a word around it.
- **Matching is case-sensitive.** A server echoes a credential as it was sent, so a secret in another letter case is not looked for, and is printed. The Insomnia source records response headers as .NET reads them, so a key carried in a redirect's `Location` host is compared, and can be printed, in lower case.
- **One layer of encoding is looked for.** A secret encoded twice, such as percent-encoded twice or a JSON string inside a JSON string, is not found. Nor is one re-encoded in another charset: the Postman source sends a key outside ASCII as its Latin-1 bytes, and an echo of those bytes percent-encoded is printed, so keep keys ASCII. ado-evidence likewise does not mask a token encoded twice or split into pieces.
- **A body is decoded by the charset it declares.** One whose bytes do not match that charset is drawn garbled, and a secret echoed inside it may not be found.
- **The response body is drawn as returned.** Nothing redacts what the API sends back, so look at an image before you share it. Look-alike letters, such as a Cyrillic e in place of a Latin one, are drawn as sent.
- **Some differences do not show in the image.** Trailing spaces on a text line or a header value cannot be seen, a literal `\uXXXX` in a text body reads the same as an escaped character, and a line that really ends in `...` looks like one cut short.
- **The Insomnia source uses a top-level folder only when it carries auth or `--folder` names it.** Otherwise that folder's environment and headers do not apply.
- **An Insomnia global environment whose name holds a `/` cannot be chosen.** `--env` reads `Name/Sub-environment`, so the text after the first `/` names a sub-environment.
- **Postman's `--timeout` bounds each socket read, not the whole response.** A server that sends slowly can keep a call open past it.
- **Posting acts as you.** Preview with `--dry-run` and approve the text before it goes up.
- **A squeeze shrinks a uniform area whose size carries meaning**, a bar in a chart or a progress bar, exactly as it shrinks blank space. The skill does not squeeze those, and says in the caption when blank space was closed up.
- **`crop.py` keeps a 16-bit sample's high byte**, and drops text, time and EXIF chunks; the colour chunks are carried over.
- **An eval or screenshot run calls real endpoints and real models** with your credentials.
