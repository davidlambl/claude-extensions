"""Sends one GET the way a Postman collection would and prints the exchange as JSON: the api-call record.

Usage:
  python3 postman.py --env ENVIRONMENT.json --collection COLLECTION.json --path /path
                     [--folder NAME] [--base-url-var baseUrl] [--vars FILE.json] [--cert PEM --key PEM] [--timeout 60]

Reads Postman's exports, read-only:
  - the environment export, for its enabled variables
  - the collection export (v2.1), for its variables and the auth on the collection or on a top-level folder
    (API key, bearer or basic)
--vars lays a flat {"name": "value"} file over the environment, for secrets kept out of the export; every value in
it is a secret. A client certificate is PEM: --cert and --key, with API_CALL_KEY_PASSPHRASE unlocking the key.
Only {{name}} templates are resolved; dynamic variables such as {{$guid}} and scripts are not.

Secrets stay in this process. The JSON it prints shows auth values as ********, and the script refuses to print
if any resolved secret would appear in its output. A redirect is recorded as the response, never followed, so a key
is sent only to the host the collection named.
"""
import argparse, base64, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

TEMPLATE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class Problem(Exception):
    """A reason the request cannot be built; its text is safe to print."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is recorded as the response, never followed: urllib would resend the auth headers to any host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def scrub(text, secrets):
    for s in secrets:
        text = text.replace(s, "********")
    return text


def resolve(text, variables):
    """{{name}} replaced from variables, up to five passes deep; an unknown or dynamic name raises Problem."""
    value = "" if text is None else str(text)
    for _ in range(5):
        if "{{" not in value:
            break

        def substitute(m):
            name = m.group(1)
            if name not in variables:
                raise Problem(f"variable '{name}' is not in the environment or the collection")
            return str(variables[name])

        value = TEMPLATE.sub(substitute, value)
    if "{{" in value:
        raise Problem("a value uses a template this script cannot resolve")
    return value


def kv(entries):
    """Postman's [{key, value}] lists as a dict."""
    return {e["key"]: e.get("value") for e in (entries or []) if isinstance(e, dict) and e.get("key") is not None}


def load_variables(collection, environment, extra):
    """The collection's variables, the environment's enabled values over them, then extra; and the secrets among them."""
    variables, secrets = {}, []
    for v in collection.get("variable") or []:
        if isinstance(v, dict) and v.get("key") is not None and not v.get("disabled"):
            variables[v["key"]] = v.get("value", "")
    for v in environment.get("values") or []:
        if isinstance(v, dict) and v.get("key") is not None and v.get("enabled", True):
            variables[v["key"]] = v.get("value", "")
            if v.get("type") == "secret":
                secrets.append(str(v.get("value", "")))
    for k, v in (extra or {}).items():
        variables[k] = v
        secrets.append(str(v))
    return variables, secrets


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


def build(collection, environment, extra, folder_name, base_url_var, path):
    """Everything the request needs, with what may be shown kept apart from what is sent."""
    variables, secrets = load_variables(collection, environment, extra)
    if base_url_var not in variables:
        raise Problem(f"the environment has no '{base_url_var}' variable")
    base = resolve(variables[base_url_var], variables).rstrip("/")
    if not base.startswith("https://"):
        raise Problem(f"'{base_url_var}' must be an https:// URL")
    if not path.startswith("/"):
        path = "/" + path
    folder = pick_folder(collection, folder_name)
    auth = (folder if folder is not None else collection).get("auth") or {}
    kind = auth.get("type") or "noauth"
    headers = {"Accept": "application/json", "User-Agent": "api-call"}
    shown = {"Accept": "application/json", "User-Agent": "api-call"}
    auth_text = "none"
    query = None
    if kind == "apikey":
        a = kv(auth.get("apikey"))
        key, value = resolve(a.get("key"), variables), resolve(a.get("value"), variables)
        secrets.append(value)
        if a.get("in") == "query":
            query, auth_text = (key, value), f"API key in query parameter {key}"
        else:
            headers[key], shown[key], auth_text = value, "********", f"API key in header {key}"
    elif kind == "bearer":
        token = resolve(kv(auth.get("bearer")).get("token"), variables)
        secrets.append(token)
        headers["Authorization"], shown["Authorization"], auth_text = f"Bearer {token}", "Bearer ********", "bearer token"
    elif kind == "basic":
        b = kv(auth.get("basic"))
        user, password = resolve(b.get("username"), variables), resolve(b.get("password"), variables)
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        secrets += [password, token]
        headers["Authorization"], shown["Authorization"], auth_text = f"Basic {token}", "Basic ********", f"basic auth as {user}"
    elif kind != "noauth":
        raise Problem(f"auth type '{kind}' is not supported (API key, bearer and basic only)")
    url = shown_url = base + path
    if query:
        joiner = "&" if "?" in url else "?"
        url += joiner + urllib.parse.urlencode([query])
        shown_url += joiner + urllib.parse.quote_plus(query[0]) + "=********"
    return {"url": url, "shown_url": shown_url, "headers": headers, "shown_headers": shown, "auth": auth_text,
            "folder": folder.get("name") if folder is not None else None, "path": path,
            "secrets": [s for s in secrets if s]}


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


def main():
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
    try:
        collection = json.loads(Path(args.collection).read_text(encoding="utf-8"))
        environment = json.loads(Path(args.env).read_text(encoding="utf-8"))
        extra = json.loads(Path(args.vars).read_text(encoding="utf-8")) if args.vars else {}
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read an export: {e}")
    if not isinstance(extra, dict):
        sys.exit("--vars must hold one flat JSON object")
    try:
        b = build(collection, environment, extra, args.folder, args.base_url_var, args.path)
    except Problem as e:
        sys.exit(str(e))
    try:
        status, response_headers, body, elapsed, sent = send(b["url"], b["headers"], args.cert, args.key, args.timeout)
    except Problem as e:
        sys.exit(scrub(str(e), b["secrets"]))
    except Exception as e:  # a failed request's message can quote the URL, and with it a key in the query
        sys.exit(scrub(f"the request failed: {type(e).__name__}: {e}", b["secrets"]))
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
        "body": body.decode("utf-8", errors="replace"),
    }
    out = json.dumps(record, ensure_ascii=False)
    for s in b["secrets"]:
        if len(s) >= 6 and s in out:
            sys.exit("refusing to print: a secret would appear in the output")
    print(out)


if __name__ == "__main__":
    main()
