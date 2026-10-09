"""Sends one GET the way a Postman collection would and prints the exchange as JSON: the api-call record.

Usage:
  python3 postman.py --env ENVIRONMENT.json --collection COLLECTION.json --path /path
                     [--folder NAME] [--base-url-var baseUrl] [--vars FILE.json] [--cert PEM --key PEM] [--timeout 60]

Reads Postman's exports, read-only:
  - the environment export, for its enabled variables
  - the collection export (schema v2.1 or v2.0), for its variables and the auth on the collection or on a top-level
    folder (API key, bearer or basic); a folder named with --folder that has no auth of its own inherits the
    collection's, as in Postman
--vars lays a flat {"name": "value"} file over the environment, for secrets kept out of the export; every value in
it is a secret. A client certificate is PEM: --cert and --key, with API_CALL_KEY_PASSPHRASE unlocking the key.
Only {{name}} templates are resolved, in a value that holds templates of its own too; names are case-sensitive, as
in Postman; dynamic variables such as {{$guid}} and scripts are not resolved. The path is sent percent-encoded where
it must be (a space, a character outside ASCII), as Postman sends it. A user:password@ in the base URL is sent as
basic auth, as Postman sends it, unless the auth already sets Authorization.

Secrets stay in this process. Every value a variable puts into the request is a secret, at any depth: the auth's
values, an API key's name, and a variable inside the base URL. The JSON it prints shows each as ********: the auth
values whole, and the templated parts of a header name, a query parameter's name, a basic auth user and the URL;
the base URL's own text is shown, except the password of a user:password@ in it, which is always masked. The script
refuses to print if any secret would appear in its output or in the image drawn from it: as it is or trimmed of
the whitespace a server trims from a header, plain, URL-encoded or escaped, and in the body as text or with its
JSON, URL or HTML escapes decoded; a header name's part in any letter case, since servers echo header names
case-folded. An error's message is masked for the same forms, and withheld when it cannot be; it never names a
variable found inside another variable's value. A credential that resolves empty, or a header value a request
cannot carry (a newline at the end of a pasted key, say), stops the call before anything is sent, with a message
that does not quote the value. A redirect is recorded as the response, never followed, so a key is sent only to
the host the collection named.
"""
import argparse, base64, html, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

TEMPLATE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
MASK = "********"
WITHHELD = "(withheld: it would show a secret)"
# A path keeps RFC 3986's reserved characters as typed, and each %XX escape already in it; anything else outside
# the unreserved set (a space, a control character, a lone %, a character outside ASCII) is percent-encoded, as
# Postman and .NET's Uri, which the Insomnia source uses, send them.
URL_SAFE = "!$&'()*+,;=:@/?#[]"
URL_ESCAPE = re.compile(r"(%[0-9A-Fa-f]{2})")
JSON_ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|(["\\/bfnrt]))')
JSON_SHORT = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
SCHEMA = re.compile(r"/v2\.[01]\.0/")
# A URL as scheme://, authority (with any user:password@) and the rest; a shown URL may have its scheme masked.
AUTHORITY = re.compile(r"([^/?#]*://)?([^/?#]*)(.*)", re.DOTALL)
DEPTH = 5  # how deep a variable's value may hold templates of its own
LONGEST = 65536  # characters a resolved value may run to: a crafted export cannot make one grow without end


class Problem(Exception):
    """A reason the request cannot be built; its text quotes no value, so it is safe to print."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is recorded as the response, never followed: urllib would resend the auth headers to any host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def forms(secret, least=6):
    """Every form text could carry a secret in. The secret as it is and trimmed of whitespace (a server trims a
    header value, and echoes it trimmed); each plain, URL-encoded by quote and by quote_plus and as a path is,
    JSON-escaped, and as repr shows it as text or as bytes (an exception quoting a header value). A base form under
    `least` characters, or all whitespace, cannot be told from ordinary text and is left out."""
    out = []
    for base in (secret, secret.strip()):
        if len(base) < least or not base.strip():
            continue
        candidates = [base, urllib.parse.quote(base, safe=""), urllib.parse.quote_plus(base),
                      urllib.parse.quote(base, safe=URL_SAFE), json.dumps(base)[1:-1],
                      json.dumps(base, ensure_ascii=False)[1:-1], repr(base)[1:-1]]
        for codec in ("latin-1", "utf-8"):  # http.client encodes a header value as latin-1 before it quotes it
            try:
                candidates.append(repr(base.encode(codec))[2:-1])
            except UnicodeEncodeError:
                pass
        out += [form for form in candidates if form and form not in out]
    return out


def unescaped(text):
    """text with its JSON string escapes decoded (\\u00fc, \\u002B, \\/, \\"), which is what a renderer that
    pretty-prints a JSON body draws."""
    out = JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else JSON_SHORT[m.group(2)], text)
    return out.encode("utf-16", "surrogatepass").decode("utf-16", "replace")  # joins an escaped surrogate pair


def views(text):
    """text, and text as a reader could decode it: its JSON escapes, its URL escapes, its HTML character references."""
    out = [text]
    for decode in (unescaped, urllib.parse.unquote, urllib.parse.unquote_plus, html.unescape):
        view = decode(text)
        if view not in out:
            out.append(view)
    return out


def find(texts, secrets, least=6, folded=()):
    """The first secret any form of which shows in any view of any of the texts, or None. A secret in `folded` (a
    part of a header name, which servers echo in their own letter case) is found in any letter case."""
    seen = [view for text in texts for view in views(text)]
    for s in secrets:
        if any(form in view for form in forms(s, least) for view in seen):
            return s
    lowered = [view.lower() for view in seen] if folded else []
    for s in folded:
        if any(form.lower() in view for form in forms(s, least) for view in lowered):
            return s
    return None


def scrub(text, secrets, folded=()):
    """text with every form of every secret masked, the longest forms first so no part of one is left behind, and
    those of a `folded` secret in any letter case; or WITHHELD when a secret would still show once the text is decoded."""
    for form in sorted({f for s in secrets for f in forms(s, least=1)}, key=len, reverse=True):
        text = text.replace(form, MASK)
    for form in sorted({f for s in folded for f in forms(s, least=1)}, key=len, reverse=True):
        text = re.sub(re.escape(form), MASK, text, flags=re.IGNORECASE)
    return WITHHELD if find([text], secrets, folded=folded) else text


def resolve(text, variables, used=None):
    """text with each {{name}} replaced from variables. A variable whose value holds templates of its own is
    resolved first, up to five deep; one that names itself, or an unknown or dynamic name, raises Problem. The
    Problem names an unknown variable only when text itself names it: a name inside a variable's value is part of
    that value, which may be a secret. Each variable substituted, at any depth, is appended to `used` fully resolved
    when given, so a secret's parts are known as well as its whole."""
    resolved = {}

    def expand(text, within):
        def substitute(m):
            name = m.group(1)
            if name not in variables:
                raise Problem(f"variable '{name}' is not in the environment or the collection" if not within else
                              f"variable '{within[0]}' uses a variable that is not in the environment or the collection")
            if name not in resolved:
                if name in within or len(within) >= DEPTH:
                    raise Problem("a value uses a template this script cannot resolve")
                resolved[name] = expand(str(variables[name]), within + (name,))
                if used is not None:
                    used.append(resolved[name])
            return resolved[name]

        value = TEMPLATE.sub(substitute, text)
        if len(value) > LONGEST:
            raise Problem(f"a value resolves to more than {LONGEST} characters")
        return value

    value = expand("" if text is None else str(text), ())
    if "{{" in value:
        raise Problem("a value uses a template this script cannot resolve")
    return value


def masked(text, quote=str):
    """text as the record shows it: each {{name}} as ******** (what a variable puts in is a secret), and the literal
    text between passed through quote."""
    pieces = TEMPLATE.split("" if text is None else str(text))  # odd indexes are the names
    return "".join(MASK if i % 2 else quote(p) for i, p in enumerate(pieces))


def userinfo(url):
    """(url without the user:password@ before its host, the user, the password): None for a part it does not have."""
    m = AUTHORITY.match(url)
    if "@" not in m.group(2):
        return url, None, None
    info, _, host = m.group(2).rpartition("@")
    user, colon, password = info.partition(":")
    return (m.group(1) or "") + host + m.group(3), user, password if colon else None


def without_password(url):
    """url with the password of a user:password@ before its host masked, templated or not."""
    m = AUTHORITY.match(url)
    if "@" not in m.group(2):
        return url
    info, _, host = m.group(2).rpartition("@")
    user, colon, _ = info.partition(":")
    return (m.group(1) or "") + user + (":" + MASK if colon else "") + "@" + host + m.group(3)


def kv(entries):
    """Postman's auth parameters as a dict: a v2.1 export's [{key, value}] list, or a v2.0 export's object."""
    if isinstance(entries, dict):
        return dict(entries)
    return {e["key"]: e.get("value") for e in (entries or []) if isinstance(e, dict) and e.get("key") is not None}


def check_exports(collection, environment):
    """The files are a Postman v2.0 or v2.1 collection export and an environment export, or Problem says which is not."""
    info = collection.get("info") if isinstance(collection, dict) else None
    if not isinstance(info, dict):
        raise Problem("the collection file is not a Postman v2 collection export: it has no 'info' block")
    schema = str(info.get("schema") or "")
    if schema and not SCHEMA.search(schema):
        raise Problem(f"collection schema {schema} is not supported: export the collection as v2.1")
    if not isinstance(environment, dict) or not isinstance(environment.get("values"), list):
        raise Problem("the environment file is not a Postman environment export: it has no 'values' list")


def load_variables(collection, environment, extra):
    """The collection's variables, the environment's enabled values over them, then extra; and the secrets among them."""
    variables, secrets = {}, []
    for v in collection.get("variable") or []:
        name = v.get("key", v.get("id")) if isinstance(v, dict) else None  # a v2.0 export may name one by id
        if name is not None and not v.get("disabled"):
            variables[name] = v.get("value", "")
    for v in environment.get("values") or []:
        if isinstance(v, dict) and v.get("key") is not None and v.get("enabled", True):
            variables[v["key"]] = v.get("value", "")
            if v.get("type") == "secret":
                secrets.append(str(v.get("value", "")))
    for k, v in (extra or {}).items():
        variables[k] = v
        secrets.append(str(v))
    return variables, secrets


def known_secrets(collection, environment, extra):
    """What is known to be secret when a request could not be built: for masking the message that says so."""
    try:
        return load_variables(collection, environment, extra)[1]
    except Exception:
        return [str(v) for v in (extra or {}).values()]


def has_auth(item):
    return bool(item.get("auth")) and (item["auth"].get("type") or "noauth") != "noauth"


def pick_folder(collection, name):
    """The top-level folder whose auth applies: the one named; else none when the collection has its own auth;
    else the only folder with auth."""
    folders = [i for i in collection.get("item") or [] if isinstance(i, dict) and "item" in i]
    if name:
        match = [f for f in folders if f.get("name") == name]
        if len(match) != 1:
            raise Problem(f"expected one top-level folder '{name}', found {len(match)}")
        return match[0]
    if has_auth(collection):
        return None
    with_auth = [f for f in folders if has_auth(f)]
    if len(with_auth) > 1:
        raise Problem(f"{len(with_auth)} top-level folders carry auth; pass --folder")
    return with_auth[0] if with_auth else None


def check_header(name, value, shown=None):
    """A header http.client can send, or Problem naming the header as the record shows it (`shown`, its templated
    parts masked) and never quoting its value. A malformed name, a control character in the value (a newline or tab
    at the end of a pasted key, say) or a character outside Latin-1 would otherwise fail inside the request, with
    the value in the error."""
    shown = name if shown is None else shown
    if not name or not name.isascii() or not re.fullmatch(r"[^:\s][^:\r\n]*", name):
        raise Problem(f"the API key auth names no header, or one HTTP cannot carry ({shown!r})")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise Problem(f"the value for header {shown} holds a control character (a newline or tab at the end of a "
                      "pasted key, perhaps), which a request cannot carry; fix the stored value")
    if any(ord(c) > 0xFF for c in value):
        raise Problem(f"the value for header {shown} holds a character outside Latin-1, which a request cannot carry")


def encoded(url):
    """url with its path and query percent-encoded as Postman and .NET's Uri send them (see URL_SAFE)."""
    m = AUTHORITY.match(url)
    parts = URL_ESCAPE.split(m.group(3))  # odd indexes are the %XX escapes already there, kept as they are
    return (m.group(1) or "") + m.group(2) + "".join(p if i % 2 else urllib.parse.quote(p, safe=URL_SAFE)
                                                     for i, p in enumerate(parts))


def build(collection, environment, extra, folder_name, base_url_var, path):
    """Everything the request needs, with what may be shown kept apart from what is sent."""
    check_exports(collection, environment)
    variables, secrets = load_variables(collection, environment, extra)
    names = []  # the parts a variable put into a header name, which a server may echo in another letter case
    if base_url_var not in variables:
        raise Problem(f"the environment has no '{base_url_var}' variable")
    # The base URL's own text is configuration, and shown; a variable inside it is a secret, masked in the record.
    base = resolve(variables[base_url_var], variables, secrets).rstrip("/")
    if not base.startswith("https://"):
        raise Problem(f"'{base_url_var}' must be an https:// URL")
    shown_base = without_password(masked(variables[base_url_var]).rstrip("/"))
    base, url_user, url_password = userinfo(base)  # http.client cannot send a user:password@; Postman sends basic auth
    if url_password:
        secrets += [url_password, urllib.parse.unquote(url_password)]
    if not path.startswith("/"):
        path = "/" + path
    folder = pick_folder(collection, folder_name)
    # A folder with no auth of its own inherits the collection's, as in Postman; only an explicit noauth means none.
    auth = (folder or {}).get("auth") or collection.get("auth") or {}
    kind = auth.get("type") or "noauth"
    headers = {"Accept": "application/json", "User-Agent": "api-call"}
    shown = {"Accept": "application/json", "User-Agent": "api-call"}
    auth_text = "none"
    query = None
    if kind == "apikey":
        a = kv(auth.get("apikey"))
        key_parts = []
        key, value = resolve(a.get("key"), variables, key_parts), resolve(a.get("value"), variables, secrets)
        secrets += key_parts
        shown_key = masked(a.get("key"))
        if not value:
            raise Problem("the API key resolves to an empty value: is a --vars file missing?")
        secrets.append(value)
        if a.get("in") == "query":
            if not key:
                raise Problem("the API key auth names no query parameter")
            query, auth_text = (key, value), f"API key in query parameter {shown_key}"
        else:
            names += key_parts
            check_header(key, value, shown_key)
            headers[key], shown[shown_key], auth_text = value, MASK, f"API key in header {shown_key}"
    elif kind == "bearer":
        token = resolve(kv(auth.get("bearer")).get("token"), variables, secrets)
        if not token:
            raise Problem("the bearer token resolves to an empty value: is a --vars file missing?")
        secrets.append(token)
        check_header("Authorization", f"Bearer {token}")
        headers["Authorization"], shown["Authorization"], auth_text = f"Bearer {token}", f"Bearer {MASK}", "bearer token"
    elif kind == "basic":
        b = kv(auth.get("basic"))
        user, password = resolve(b.get("username"), variables, secrets), resolve(b.get("password"), variables, secrets)
        if not password:
            raise Problem("the basic auth password resolves to an empty value: is a --vars file missing?")
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        secrets += [password, token]
        headers["Authorization"], shown["Authorization"] = f"Basic {token}", f"Basic {MASK}"
        auth_text = f"basic auth as {masked(b.get('username'))}"
    elif kind != "noauth":
        raise Problem(f"auth type '{kind}' is not supported (API key, bearer and basic only)")
    if url_user is not None and not any(h.lower() == "authorization" for h in headers):
        # As Postman does: the URL's user and password, percent-decoded, as basic auth, unless the auth set its own.
        user = urllib.parse.unquote(url_user)
        token = base64.b64encode(f"{user}:{urllib.parse.unquote(url_password or '')}".encode("utf-8")).decode("ascii")
        secrets.append(token)
        headers["Authorization"], shown["Authorization"] = f"Basic {token}", f"Basic {MASK}"
        shown_user = userinfo(shown_base)[1]
        via = f"basic auth from the base URL as {MASK if shown_user is None else shown_user}"
        auth_text = via if auth_text == "none" else f"{auth_text}, and {via}"
    url, shown_url = encoded(base + path), encoded(shown_base + path)
    if query:
        joiner = "&" if "?" in url else "?"
        url += joiner + urllib.parse.urlencode([query])
        shown_url += joiner + masked(a.get("key"), urllib.parse.quote_plus) + "=" + MASK
    return {"url": url, "shown_url": shown_url, "headers": headers, "shown_headers": shown, "auth": auth_text,
            "folder": folder.get("name") if folder is not None else None, "path": path,
            "secrets": list(dict.fromkeys(s for s in secrets if s)), "names": list(dict.fromkeys(s for s in names if s))}


def texts(record):
    """Every string the record shows: its values, its header names and values, the record as JSON, and the body as
    a renderer draws it, pretty-printed when it is JSON."""
    out = [v for v in record.values() if isinstance(v, str)]
    for d in (record.get("request_headers") or {}, record.get("response_headers") or {}):
        out += [str(k) for k in d] + [str(v) for v in d.values()]
    out.append(json.dumps(record, ensure_ascii=False))
    try:
        out.append(json.dumps(json.loads(record.get("body") or ""), indent=2, ensure_ascii=False))
    except (ValueError, RecursionError):
        pass
    return out


def exposed(record, secrets, names=()):
    """The first secret that would be printed or drawn, in any form (see forms) and any view (see views), or None;
    a part of a header name (`names`) in any letter case. A value under six characters is too short to tell from
    ordinary text, and is not checked for."""
    return find(texts(record), secrets, folded=names)


def send(url, headers, cert, key, timeout):
    """The GET itself: (status, response headers, body bytes, elapsed ms, sent time)."""
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"] == 0:  # python.org's macOS build has no roots until certifi is installed
        try:
            import certifi
            context.load_verify_locations(certifi.where())
        except ImportError:
            raise Problem("this Python has no root certificates to verify servers with: run its Install Certificates.command, or pip install certifi")
    if cert:
        context.load_cert_chain(cert, key, password=os.environ.get("API_CALL_KEY_PASSPHRASE") or None)
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    sent = datetime.now().astimezone()
    start = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            body, status, response_headers = response.read(), response.status, response.headers
    except urllib.error.HTTPError as e:  # a status of 300 or more, a redirect among them
        body, status, response_headers = e.read(), e.code, e.headers
    return status, response_headers, body, round((time.perf_counter() - start) * 1000), sent


def utf8_output():
    """Output as UTF-8 whatever the console's code page: on Windows a pipe takes the ANSI code page, which cannot
    hold every character a body may carry, and call.py reads UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    utf8_output()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", required=True, help="the environment export")
    p.add_argument("--collection", required=True, help="the collection export")
    p.add_argument("--path", required=True)
    p.add_argument("--folder")
    p.add_argument("--base-url-var", default="baseUrl")
    p.add_argument("--vars")
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--timeout", type=int, default=60)
    args = p.parse_args()
    if bool(args.cert) != bool(args.key):
        sys.exit("--cert and --key go together")
    try:  # utf-8-sig: an export saved by Windows PowerShell 5 starts with a BOM
        collection = json.loads(Path(args.collection).read_text(encoding="utf-8-sig"))
        environment = json.loads(Path(args.env).read_text(encoding="utf-8-sig"))
        extra = json.loads(Path(args.vars).read_text(encoding="utf-8-sig")) if args.vars else {}
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read an export: {e}")
    if not isinstance(extra, dict):
        sys.exit("--vars must hold one flat JSON object")
    try:
        b = build(collection, environment, extra, args.folder, args.base_url_var, args.path)
    except Problem as e:
        sys.exit(scrub(str(e), known_secrets(collection, environment, extra)))
    except Exception as e:  # an export of an unexpected shape; the message is masked with what is known to be secret
        sys.exit(f"cannot build the request from the exports: {type(e).__name__}: "
                 + scrub(str(e), known_secrets(collection, environment, extra)))
    passphrase = os.environ.get("API_CALL_KEY_PASSPHRASE")
    secrets, names = b["secrets"] + ([passphrase] if args.cert and passphrase else []), b["names"]
    try:
        status, response_headers, body, elapsed, sent = send(b["url"], b["headers"], args.cert, args.key, args.timeout)
    except Problem as e:
        sys.exit(scrub(str(e), secrets, names))
    except Exception as e:  # a failed request's message can quote the URL, and with it a key in the query
        sys.exit(f"the request failed: {type(e).__name__}: {scrub(str(e), secrets, names)}")
    record = {
        "source": "postman",
        "environment": environment.get("name") or Path(args.env).stem,
        "collection": (collection.get("info") or {}).get("name") or Path(args.collection).stem,
        "folder": b["folder"],
        "method": "GET",
        "path": b["path"],
        "url": b["shown_url"],
        "auth": b["auth"],
        "request_headers": b["shown_headers"],
        "client_cert": Path(args.cert).name if args.cert else None,
        "status": status,
        "elapsed_ms": elapsed,
        "sent": sent.isoformat(timespec="milliseconds"),
        "sent_utc": sent.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{sent.microsecond // 1000:03d}Z",
        "response_headers": {h: response_headers[h] for h in ("Content-Type", "Content-Length", "Date", "Location") if response_headers.get(h)},
        "body": body.decode("utf-8-sig", errors="replace"),  # a BOM before a JSON body would keep it from pretty-printing
    }
    if exposed(record, secrets, names):
        sys.exit("refusing to print: a secret would appear in the output")
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
