"""Posts a markdown comment with screenshots to an Azure DevOps work item.

Usage:
  python3 post.py --org https://dev.azure.com/ORG --project PROJECT --work-item ID --comment comment.md
                  [--images DIR] [--edit COMMENT_ID] [--dry-run]

In the comment, write {{image:file.png}} where a screenshot goes. Each referenced file (looked up in --images,
default: the comment's folder) is uploaded as a work item attachment and embedded as ![file.png](url).
--dry-run prints the comment as it would be posted and touches nothing. --edit replaces the text of an existing
comment instead of adding one; its earlier images are left in place, unreferenced.

Auth: AZURE_DEVOPS_TOKEN if set, otherwise a token from the Azure CLI
(az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798, the Azure DevOps resource).
A Microsoft Entra access token, which is what the CLI issues, is a JWT and is sent as Bearer; anything else is taken
for a personal access token and sent as Basic with an empty user name, the way Azure DevOps documents it.
The token goes only to --org, which must be https://dev.azure.com/ORG or https://ORG.visualstudio.com, and to the
attachments Azure DevOps answers with, which must be on --org's own host, under --org, and hold no token. A redirect
is an error, never followed. After posting it reads the comment back and checks that every image renders and matches
the local file. Whatever it prints has the token masked, as is or in any one layer of encoding a response could carry
it back in, before it is cut short.
"""
import argparse, base64, functools, hashlib, html, html.entities, html.parser, http.client, json, os, re, shutil, ssl
import subprocess, sys
import urllib.error, urllib.parse, urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath

TIMEOUT = 120
# What an attachment url from a response may hold before it is embedded as ![name](url): --org's host, plain, and a
# path of --org's path, at most two of a GUID, the project and DefaultCollection, then /_apis/wit/attachments/GUID.
# Nothing in either can end the markdown link early, or carry a token, whole or in pieces.
PLAIN_HOST = re.compile(r"[A-Za-z0-9.-]+")
GUID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
JWT = re.compile(r"eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
MASK = "********"


class Problem(Exception):
    """A reason the post cannot go on. Its text is printed once the token is scrubbed from it."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error, never followed: urllib would resend the Authorization header to whatever host it names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    # Each 3xx goes on to the default handler, which raises it as an HTTPError, before urllib reads its Location:
    # urllib raises a ValueError on a malformed one, which would not say the request was a redirect.
    def http_error_302(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def azure_devops(url):
    """True for the hosts that may see the token: Azure DevOps Services only, over TLS."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "dev.azure.com" or host.endswith(".visualstudio.com"))


def plain_name(name):
    """True when name is a file name alone under both POSIX and Windows rules: no folder, drive or root, so the lookup
    cannot leave the images folder. 'C:secret.png' is drive-relative on Windows, so a colon is refused everywhere."""
    return (name not in ("", ".", "..") and ":" not in name
            and PurePosixPath(name).name == name and PureWindowsPath(name).name == name)


def attachment_url(name, answer, org, project, secrets=()):
    """The url to embed an uploaded image with, as ![name](url). An upload's answer is not trusted: its url must hold
    no secret, in path or query, in any form, and be an attachment of --org on --org's own host over https, with a
    plain host and path, or it is refused before the comment is posted and never requested. Its query is not kept;
    the file name is added here, from the name the comment gave."""
    url = answer.get("url") if isinstance(answer, dict) else None
    if not isinstance(url, str):
        raise Problem(f"the upload of {name} answered without a url")
    o = urllib.parse.urlsplit(org)
    if scrub(url, secrets) != url:
        why = "holds the token"
    else:
        try:
            u = urllib.parse.urlsplit(url)
        except ValueError:
            u = None
        segment = f"(?:{GUID}|{re.escape(project)}|DefaultCollection)"
        path = re.escape(o.path.rstrip("/")) + f"(?:/{segment}){{0,2}}/_apis/wit/attachments/{GUID}"
        if u and azure_devops(url) and PLAIN_HOST.fullmatch(u.netloc) and u.netloc.lower() == o.hostname \
                and re.fullmatch(path, u.path, re.IGNORECASE):
            return f"https://{o.hostname}{u.path}?fileName={urllib.parse.quote(name, safe='')}"
        why = f"is not an attachment under {org} over https in plain URL characters"
    raise Problem(f"refusing to embed the url Azure DevOps answered for {name}, which {why}: "
                  f"'{printable(url, secrets, 200)}'")


def authorization(token):
    """The Authorization header for the token, and what kind of token it was taken for. A Microsoft Entra access
    token, which the Azure CLI issues, is a JWT and goes as Bearer; a personal access token goes as Basic with an
    empty user name."""
    if not re.fullmatch(r"[\x21-\x7e]+", token):
        raise Problem("the token holds a space, a control character or a non-ASCII character, which no Azure DevOps "
                      "token does; it was not sent")
    if JWT.fullmatch(token):
        return f"Bearer {token}", "a Microsoft Entra token, as Bearer"
    return "Basic " + base64.b64encode(f":{token}".encode("ascii")).decode("ascii"), "a personal access token, as Basic"


def encodings(ch):
    """A regex for one character of a secret in each form a response could carry it back in, one layer deep: as is,
    percent-encoded (and '+' as the space a form decoder makes of it), JSON-escaped, and as an HTML character
    reference, decimal, hexadecimal or named, with or without its semicolon. Hex digits match in either case."""
    def hexes(n, width):
        return "".join(f"[{d.lower()}{d.upper()}]" if d.isalpha() else d for d in f"{n:0{width}X}")
    o = ord(ch)
    forms = [re.escape(ch), "%" + hexes(o, 2), "%[uU]" + hexes(o, 4), r"\\[uU]" + hexes(o, 4),
             f"&#0*{o};?", "&#[xX]0*" + hexes(o, 1) + ";?"]
    forms += [re.escape("&" + name) for name, value in html.entities.html5.items() if value == ch]
    if ch in "\"\\/'":
        forms.append(re.escape("\\" + ch))  # JSON's short escapes, and a quote escaped as JavaScript does
    if ch == "+":
        forms.append(" ")
    return "(?:" + "|".join(sorted(forms, key=len, reverse=True)) + ")"


@functools.lru_cache(maxsize=8)
def masker(secrets):
    """One regex for every secret in each of its forms, the longest secret first; None when there is none."""
    patterns = ["".join(encodings(ch) for ch in s) for s in sorted({s for s in secrets if s}, key=len, reverse=True)]
    return re.compile("|".join(patterns)) if patterns else None


def scrub(text, secrets):
    """text with each secret masked, as is or in any one layer of encoding a response could give it back in."""
    pattern = masker(tuple(secrets))
    return pattern.sub(MASK, text) if pattern else text


def printable(text, secrets=(), limit=300):
    """Text a response chose, fit for a message: each secret masked in it, then one line with no control characters,
    then cut short. The masking comes before the cut, so a cut never leaves part of a secret standing, and again
    once the text is one line, in case that put a hidden one together."""
    text = scrub(str(text), secrets)
    text = scrub("".join(ch if ch.isprintable() else "?" for ch in " ".join(text.split())), secrets)
    return text if limit is None or len(text) <= limit else text[:limit] + "..."


def redirect_target(location, url, secrets):
    """Where a redirect pointed, for its error message: the host its Location names, with no user name or password,
    as written rather than in lower case, which the scrub would no longer match; url's host for a relative one."""
    try:
        host = urllib.parse.urlsplit(location or "").netloc.rpartition("@")[2] or urllib.parse.urlsplit(url).netloc
    except ValueError:
        return "a malformed address, " + printable(location, secrets, 100)
    return printable(host, secrets, 100)


def say(line, secrets):
    """Prints a line with each secret masked in it."""
    print(scrub(line, secrets))


def build_opener():
    """An opener that verifies servers and refuses redirects. Where this Python has no root certificates of its own,
    as python.org's macOS build has none until its Install Certificates.command has run, certifi's are used."""
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"] == 0:
        try:
            import certifi
        except ImportError:
            pass  # roots may still be found in a certificate folder; a failed verification says what to do
        else:
            context.load_verify_locations(certifi.where())
    return urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))


class Client:
    """Requests to Azure DevOps with the token on them. The token goes to an Azure DevOps host over TLS and nowhere
    else: any other URL is refused before a request is made, and a redirect is an error, never followed."""

    def __init__(self, token, opener=None):
        self.header, self.kind = authorization(token)
        value = self.header.split(" ", 1)[1]
        # The token, the header value it goes as, and that value without its '=' padding, which carries nothing.
        self.secrets = [token, value, value.rstrip("=")]
        self.opener = opener or build_opener()

    def call(self, method, url, body=None, content_type="application/json", raw=False):
        if not azure_devops(url):
            raise Problem(f"refusing {method} {url}: the token goes to dev.azure.com or ORG.visualstudio.com over https and nowhere else")
        data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": self.header, "Content-Type": content_type})
        try:
            with self.opener.open(req, timeout=TIMEOUT) as r:
                status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                where = redirect_target(e.headers.get("Location") if e.headers else None, url, self.secrets)
                raise Problem(f"{method} {url} answered HTTP {e.code}, a redirect to {where}; not followed, "
                              "since the token goes to Azure DevOps only") from None
            sent = f" (the token was sent as {self.kind})" if e.code in (401, 403) else ""
            try:
                answered = e.read()
            except (http.client.HTTPException, OSError, ValueError):
                answered = b""
            body = printable(answered.decode("utf-8", errors="replace"), self.secrets)
            raise Problem(f"{method} {url} answered HTTP {e.code} {printable(e.reason, self.secrets, 100)}{sent}"
                          + (f": {body}" if body else "")) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            roots = isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError)
            hint = "; if this Python has no root certificates, run its Install Certificates.command, or pip install certifi" if roots else ""
            raise Problem(f"{method} {url} failed: {printable(e, self.secrets)}{hint}") from e
        if raw:
            return payload
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError:
            # Azure DevOps answers a refused token with HTTP 203 and its sign-in page, not a 401.
            why = "its sign-in page: the token was not accepted" if status == 203 else "something other than JSON"
            raise Problem(f"{method} {url} answered HTTP {status} with {why} (the token was sent as {self.kind})") from None


def token():
    """AZURE_DEVOPS_TOKEN, or an Azure DevOps access token from the signed-in Azure CLI."""
    if os.environ.get("AZURE_DEVOPS_TOKEN", "").strip():
        return os.environ["AZURE_DEVOPS_TOKEN"].strip()
    az = shutil.which("az")
    if not az:
        raise Problem("set AZURE_DEVOPS_TOKEN or install the Azure CLI")
    run = subprocess.run([az, "account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
                          "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True)
    if run.returncode != 0 or not run.stdout.strip():
        raise Problem("the Azure CLI gave no token (az login first?): " + run.stderr.strip())
    return run.stdout.strip()


def embed(text, urls):
    """The comment with each {{image:name}} replaced by ![name](url)."""
    for name, url in urls.items():
        text = re.sub(r"\{\{image:\s*" + re.escape(name) + r"\s*\}\}", lambda _m: f"![{name}]({url})", text)
    return text


class ImageSources(html.parser.HTMLParser):
    """The src of every <img> in a piece of HTML, its entities decoded."""

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.sources = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.sources += [value for key, value in attrs if key == "src" and value]


def rendered(urls, markup):
    """True when every uploaded image is drawn by an <img> in the rendered comment. Each is found by its attachment's
    path, which holds its id, rather than by counting tags: a comment may show one image twice, or carry another."""
    drawn = {url_path(src) for src in ImageSources(markup).sources}
    return all(url_path(url) in drawn for url in urls.values())


def url_path(url):
    """A url's path, to compare by; None for a src that is not a url at all."""
    try:
        return urllib.parse.urlsplit(url).path.rstrip("/").lower()
    except ValueError:
        return None


def comment_id(answer, edit, secrets):
    """The id of the comment just posted, which the url it is read back from is built with, so a number and nothing
    else. Anything else stops the post with an error that says the comment was posted, and what its id came back as."""
    posted = f"comment {edit} was posted" if edit else "the comment was posted"
    if not isinstance(answer, dict) or "id" not in answer:
        raise Problem(f"{posted}, but Azure DevOps answered without its id; look at the work item before posting again")
    id = answer["id"]
    if type(id) is int:
        return id
    raise Problem(f"{posted}, but Azure DevOps answered its id as '{printable(id, secrets, 100)}', which is not a "
                  "number, so it was not read back; look at the work item before posting again")


def publish(client, org, project, work_item, edit, text, files):
    """Uploads the images, adds or edits the comment, reads it back and prints the checks; returns the exit code."""
    urls = {}
    for name, f in files.items():
        answer = client.call("POST", f"{org}/{project}/_apis/wit/attachments?fileName={urllib.parse.quote(name)}&api-version=7.1",
                             f.read_bytes(), "application/octet-stream")
        urls[name] = attachment_url(name, answer, org, project, client.secrets)
        say(f"uploaded {name}", client.secrets)
    text = embed(text, urls)

    base = f"{org}/{project}/_apis/wit/workItems/{work_item}/comments"
    if edit:
        c = client.call("PATCH", f"{base}/{edit}?format=markdown&api-version=7.1-preview.4", {"text": text})
    else:
        c = client.call("POST", f"{base}?format=markdown&api-version=7.1-preview.4", {"text": text})
    id = comment_id(c, edit, client.secrets)

    try:  # the comment is up: whatever stops the checks must say so, or it may be posted twice
        back = client.call("GET", f"{base}/{id}?$expand=renderedText&api-version=7.1-preview.4")
        if not isinstance(back, dict):
            raise Problem("the comment read back as something other than an object")
        version = back.get("version")
        # ADO may store quotes and angle brackets as HTML entities; they render the same.
        checks = {"stored text matches": html.unescape(str(back.get("text") or "")) == html.unescape(text),
                  "images rendered": rendered(urls, str(back.get("renderedText") or ""))}
        for name, url in urls.items():
            stored = client.call("GET", url.split("?")[0] + "?download=true&api-version=7.1", raw=True)
            checks[f"{name} matches local file"] = hashlib.sha256(stored).digest() == hashlib.sha256(files[name].read_bytes()).digest()
    except Exception as e:
        why = str(e) if isinstance(e, Problem) else printable(f"{type(e).__name__}: {e}", client.secrets)
        raise Problem(f"comment {id} was posted, but the checks could not run: {why}") \
            from (e.__cause__ if isinstance(e, Problem) else e)
    for k, v in checks.items():
        say(("ok   " if v else "FAIL ") + k, client.secrets)
    version = version if type(version) is int else "unknown"  # an answer's own text is not printed
    say(f"comment {id} (version {version}) on {org}/{project}/_workitems/edit/{work_item}", client.secrets)
    return 0 if all(checks.values()) else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows writes a piped stdout in the ANSI code page, which lacks most of Unicode
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--org", required=True, help="https://dev.azure.com/ORG")
    p.add_argument("--project", required=True)
    p.add_argument("--work-item", required=True, type=int)
    p.add_argument("--comment", required=True)
    p.add_argument("--images")
    p.add_argument("--edit", type=int)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    org = args.org.rstrip("/")
    if not azure_devops(org):
        sys.exit("--org must be https://dev.azure.com/ORG or https://ORG.visualstudio.com; the token goes nowhere else")
    project = urllib.parse.quote(args.project)
    comment_file = Path(args.comment).resolve()
    images_dir = Path(args.images).resolve() if args.images else comment_file.parent
    try:
        text = comment_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"cannot read the comment: {e}")
    names = list(dict.fromkeys(t.strip() for t in re.findall(r"\{\{image:([^}]+)\}\}", text)))
    not_names = [n for n in names if not plain_name(n)]
    if not_names:
        sys.exit("an image is named by its file name alone, looked up in the images folder: " + ", ".join(not_names))
    files = {name: images_dir / name for name in names}
    missing = [str(f) for f in files.values() if not f.is_file()]
    if missing:
        sys.exit("missing images: " + ", ".join(missing))

    if args.dry_run:
        preview = embed(text, {name: f"<upload {f.stat().st_size} bytes>" for name, f in files.items()})
        action = f"replace comment {args.edit}" if args.edit else "add a comment"
        print(f"DRY RUN - would {action} on work item {args.work_item} with {len(files)} image(s):\n")
        print(preview)
        sys.exit(0)

    secrets = []
    try:
        client = Client(token())
        secrets = client.secrets
        code = publish(client, org, project, args.work_item, args.edit, text, files)
    except Problem as e:
        sys.exit(scrub(str(e), secrets))
    except Exception as e:  # anything unforeseen is shown without a traceback, scrubbed like the rest
        sys.exit(printable(f"stopped by {type(e).__name__}: {e}", secrets, None))
    sys.exit(code)


if __name__ == "__main__":
    main()
