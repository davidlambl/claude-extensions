"""Sends one GET the way a Postman collection would and prints the exchange as JSON: the api-call record.

Usage:
  python3 postman.py --env ENVIRONMENT.json --collection COLLECTION.json --path /path
                     [--folder NAME] [--base-url-var baseUrl] [--vars FILE.json] [--cert PEM --key PEM] [--timeout 60]

Reads Postman's exports, read-only:
  - the environment export, for its enabled variables
  - the collection export (schema v2.1 or v2.0), for its variables and the auth on the collection or on a top-level
    folder (API key, bearer or basic); a folder named with --folder that has no auth of its own inherits the
    collection's, as in Postman
--folder is matched exactly, in its own letter case, once both names are composed (NFC): an accent stored as a letter
and a combining mark, as macOS can write it, is the same text as the accent typed. When no folder has the name, the
error names those that differ from it only in letter case, else only in letter case or in characters it shows as
escapes, else the closest: three at most, with any secret in them masked. It never chooses one.
--vars lays a flat {"name": "value"} file over the environment, for secrets kept out of the export; every value in
it is a secret. A client certificate is PEM: --cert and --key, with API_CALL_KEY_PASSPHRASE unlocking the key.
Only {{name}} templates are resolved, in a value that holds templates of its own too; names are case-sensitive, as
in Postman; dynamic variables such as {{$guid}} and scripts are not resolved. The path is sent percent-encoded where
it must be (a space, a character outside ASCII), as Postman sends it. A user:password@ in the base URL is sent as
basic auth, as Postman sends it, unless the auth already sets Authorization.

Secrets stay in this process. A secret is a value in a credential position: an API key's value, a bearer token, a
basic auth password and the basic token itself, and the password of a user:password@ in the base URL, templated or
typed; each together with every value substituted into it, at any depth. A basic auth user with an empty password is
the credential itself (an API that takes the key as the user, as curl -u key: sends it), and so is a user@ in the base
URL with no password. Every value in --vars, and every environment value typed secret, is a secret wherever it is
used. The rest is configuration, shown as resolved and never refused for being echoed: the base URL's scheme, host,
port and path, an API key's name, a basic auth user, the environment's and the collection's names. The JSON it prints
shows each secret as ********, and a secret inside what is otherwise shown (a --vars value in the host, say) as well;
the password of a user:password@ in the base URL is found in the URL as resolved, so a template cannot hide it from
the mask. The script refuses to print if any secret would appear in its output or in the image drawn from it: as it
is or trimmed of the whitespace a server trims from a header, plain, URL-encoded or escaped, and in the body as text
or with its JSON, URL or HTML escapes decoded; matching is case-sensitive. The record's own fields are not checked:
its source, method and times, and the environment, collection and folder names, which are labels; none holds anything
from a variable. The body is read as the Insomnia source reads it: by its byte order mark, then by the charset it
declares, then as UTF-8. An error's message is masked for the same forms, and withheld when it cannot be; it never
names a variable found inside another variable's value. When the base URL holds an @ after its scheme, a failed
request's message quotes none of the URL: a / ? or # left unencoded in a password ends the userinfo early, and the
error would quote what came before it. An API key or bearer token that resolves empty, a basic auth password templated
to an empty value or one whose user is empty too, or a header value a request cannot carry (a newline at the end of a
pasted key, say), stops the call before anything is sent, with a message that does not quote the value. A redirect is
recorded as the response, never followed, so a key is sent only to the host the collection named. When the base URL
variable is not defined, the one variable whose name matches it ignoring case is used, if there is exactly one (a
collection written for baseURL). A limit: a key outside ASCII is sent as its Latin-1 bytes, and an echo of those bytes
percent-encoded (%E4 for ä) is not looked for, so keep keys ASCII.
"""
import argparse, base64, email.message, html, json, os, re, ssl, sys, time, unicodedata, urllib.error, urllib.parse
import urllib.request
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
# A URL as scheme://, authority (with any user:password@) and the rest.
AUTHORITY = re.compile(r"([^/?#]*://)?([^/?#]*)(.*)", re.DOTALL)
DEPTH = 5  # how deep a variable's value may hold templates of its own
LONGEST = 65536  # characters a resolved value may run to: a crafted export cannot make one grow without end
# The record's own fields: written by this script (its name, the method, the times) or labels typed in the exports and
# never resolved (the environment, collection and folder names). None holds anything from a variable, so a secret that
# matches one, a --vars value that is today's date or the word postman, is no reason to refuse; the Insomnia source
# leaves out its labels and times too. The record's field names are left out with them.
OWN = ("source", "environment", "collection", "folder", "method", "sent", "sent_utc")
# The byte order marks a body may start with, as the Insomnia source's ConvertFrom-Body looks for them: UTF-32 LE ahead
# of UTF-16 LE, whose mark begins it.
BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"), (b"\x00\x00\xfe\xff", "utf-32-be"))
UNREACHED = ("the request failed: the base URL holds credentials that could not be parsed or reached; "
             "percent-encode any / ? # or @ in its user or password")
# A name in a message shows at most NAME_WIDTH characters between its quotes (see shown_name), and a message lists at
# most NAMES_LISTED names. Names that differ only in unseen characters, and close names, are looked for among
# NAMES_COMPARED names at most, each LONGEST_COMPARED characters at most (see name_hint), so a shared export can make
# neither the message long nor the search slow.
NAME_WIDTH, NAMES_LISTED, NAMES_COMPARED, LONGEST_COMPARED = 80, 3, 1000, 128
# What .NET will not compose (see composed): a surrogate, which in a Python string is always a lone one, and U+FFFE.
UNCOMPOSABLE = re.compile(r"[\ud800-\udfff\ufffe]")
# The code points a name shows as escapes besides its control, format, separator, surrogate and unassigned ones, as
# render.py draws a body: the other default-ignorable ones, which draw nothing, and the blank Braille pattern and the
# object replacement character, which draw as a space.
UNSEEN = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x2800, 0x2800), (0x3164, 0x3164),
          (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xFFFC, 0xFFFC), (0xE0100, 0xE01EF))


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


def find(texts, secrets, least=6):
    """The first secret any form of which shows in any view of any of the texts, or None. Matching is case-sensitive."""
    seen = [view for text in texts for view in views(text)]
    for s in secrets:
        if any(form in view for form in forms(s, least) for view in seen):
            return s
    return None


def masked(text, secrets):
    """text with every form of every secret masked, however short, the longest forms first so no part of one is left
    behind."""
    for form in sorted({f for s in secrets for f in forms(s, least=1)}, key=len, reverse=True):
        text = text.replace(form, MASK)
    return text


def scrub(text, secrets):
    """text with every form of every secret masked (see masked); or WITHHELD when a secret would still show once the
    text is decoded."""
    text = masked(text, secrets)
    return WITHHELD if find([text], secrets) else text


def hide(text, secrets):
    """text, which is configuration and shown, with any form of a secret the record is checked for (see forms) in it
    masked, the longest first: a --vars value in the host, say, or a variable that is part of a credential and of the
    base URL too. Without this the record would carry the secret, and be refused."""
    for form in sorted({f for s in secrets for f in forms(s)}, key=len, reverse=True):
        text = text.replace(form, MASK)
    return text


def resolution(text, variables, used=None):
    """text with each {{name}} replaced from variables, as (value, subs): subs lists each value substituted into text
    itself, as (where it starts in value, its own resolution), and so on down. A variable whose value holds templates
    of its own is resolved first, up to five deep; one that names itself, or an unknown or dynamic name, raises
    Problem. The Problem names an unknown variable only when text itself names it: a name inside a variable's value is
    part of that value, which may be a secret. Each variable substituted, at any depth, is appended to `used` fully
    resolved when given, so a secret's parts are known as well as its whole."""
    resolved = {}

    def expand(text, within):
        pieces, subs, length, at = [], [], 0, 0
        for m in TEMPLATE.finditer(text):
            name = m.group(1)
            if name not in variables:
                raise Problem(f"variable '{name}' is not in the environment or the collection" if not within else
                              f"variable '{within[0]}' uses a variable that is not in the environment or the collection")
            if name not in resolved:
                if name in within or len(within) >= DEPTH:
                    raise Problem("a value uses a template this script cannot resolve")
                resolved[name] = expand(str(variables[name]), within + (name,))
                if used is not None:
                    used.append(resolved[name][0])
            sub = resolved[name]
            pieces += [text[at:m.start()], sub[0]]
            length += m.start() - at
            if sub[0]:
                subs.append((length, sub))
            length += len(sub[0])
            at = m.end()
        pieces.append(text[at:])
        value = "".join(pieces)
        if len(value) > LONGEST:
            raise Problem(f"a value resolves to more than {LONGEST} characters")
        return value, subs

    node = expand("" if text is None else str(text), ())
    if "{{" in node[0]:
        raise Problem("a value uses a template this script cannot resolve")
    return node


def resolve(text, variables, used=None):
    """text with each {{name}} replaced from variables (see resolution)."""
    return resolution(text, variables, used)[0]


def parts_within(node, start, end, into):
    """Every value substituted into the characters from start to end of a resolution's value (see resolution), at
    any depth, appended to `into`: one that lies wholly inside, with every part of its own; and of one that runs
    across an edge, the piece inside and those of its own parts that lie inside that piece. As the Insomnia source's
    Add-PartsWithin: a credential's parts are found by where they were substituted, never by matching their text."""
    value, subs = node
    for at, sub in subs:
        if at >= end:
            break
        lo, hi = max(start, at), min(end, at + len(sub[0]))
        if lo < hi:
            into.append(value[lo:hi])
            parts_within(sub, lo - at, hi - at, into)


def split_userinfo(url):
    """(scheme://, user, password, the rest from the host on) of a URL as resolved, found on its text: the authority
    runs from after :// to the first /, ? or #, and a user:password@ in it ends at its last @, as Postman reads it.
    user is None when there is no @, password when the user has no colon after it. The URL is resolved before it is
    split, so a template that holds the scheme, the host or the port cannot move the password out of reach."""
    m = AUTHORITY.match(url)
    scheme, authority, rest = m.group(1) or "", m.group(2), m.group(3)
    if "@" not in authority:
        return scheme, None, None, authority + rest
    info, _, host = authority.rpartition("@")
    user, colon, password = info.partition(":")
    return scheme, user, password if colon else None, host + rest


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


def unseen(ch):
    """Whether a name in a message shows ch as its escape (see shown_name): a control or format character, the bidi
    marks among them, a line or paragraph separator, a space other than U+0020, a lone surrogate, an unassigned code
    point, and those in UNSEEN. Each draws nothing, draws as a space it is not, or could reorder or break the line. As
    the Insomnia source's Test-Unseen."""
    cp = ord(ch)
    return not " " <= ch <= "~" and (unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Zs", "Cs", "Cn")
                                     or any(lo <= cp <= hi for lo, hi in UNSEEN))


def shown_name(name, secrets=()):
    """name as a message shows it: in single quotes, a ' or a \\ in it escaped with a \\, and each character unseen
    names as its \\uXXXX escape (\\U and eight hex digits past the Basic Multilingual Plane). A name that would show more
    than NAME_WIDTH characters is cut before the first that does not fit, and ... follows its closing quote. Names come
    from a shared export, so none can break the message's line, fake its quoting or make it long. Every form of a
    secret in it is masked first (see masked), since part of one cut short, or one with a quote escaped, is a form the
    message's own mask does not look for. As the Insomnia source's Format-Name, whose names are looked up before any
    secret is known."""
    out, width = [], 0
    for ch in masked(name, secrets):
        cp = ord(ch)
        if ch in "'\\":
            piece = "\\" + ch
        elif unseen(ch):
            piece = "\\" + (f"u{cp:04x}" if cp <= 0xFFFF else f"U{cp:08x}")
        else:
            piece = ch
        if width + len(piece) > NAME_WIDTH:
            return "'" + "".join(out) + "'..."
        out.append(piece)
        width += len(piece)
    return "'" + "".join(out) + "'"


def listed(names, secrets=()):
    """Names as a message lists them, each as shown_name shows it: 'A', 'A' and 'B', or 'A', 'B' and 'C'; past
    NAMES_LISTED, the first of them and how many more."""
    shown = [shown_name(n, secrets) for n in names[:NAMES_LISTED]]
    if len(names) > NAMES_LISTED:
        return ", ".join(shown) + f" and {len(names) - NAMES_LISTED} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def composed(name):
    """name in Unicode's composed form (NFC), so that a name stored decomposed, as macOS can write an accent (an e and
    a combining acute), is the same text as one typed composed (U+00E9). One that .NET will not compose (see
    UNCOMPOSABLE) stays as it is. As the Insomnia source's Get-Composed."""
    return name if UNCOMPOSABLE.search(name) else unicodedata.normalize("NFC", name)


def same_name(name, wanted):
    """Whether a name from an export is the one wanted: compared ordinal once both are composed (see composed), so that
    letter case and every other character count. As the Insomnia source's Test-SameName."""
    return isinstance(name, str) and composed(name) == composed(wanted)


def folded(name):
    """name composed (see composed) and case-folded, to compare in any letter case: ordinal, and the same in every
    culture. As the Insomnia source's Get-Folded."""
    return composed(name).casefold()


def skeleton(name):
    """name as the near-miss search reads it (see name_hint): each space other than U+0020 as one, every other
    character unseen names dropped, then composed and folded (see folded). Names with the same skeleton differ only
    in letter case or in characters a message shows as escapes. As the Insomnia source's Get-Skeleton."""
    return folded("".join(" " if unicodedata.category(ch) == "Zs" else "" if unseen(ch) else ch for ch in name))


def close_edits(a, b):
    """How many edits apart names of lengths a and b may be and still count as close: one for every five characters
    of the longer, one at least and three at most; none when the longer is under three characters."""
    longer = max(a, b)
    return 0 if longer < 3 else min(3, max(1, longer // 5))


def edits(a, b, most):
    """How many single-character edits turn a into b (an insertion, a deletion, a substitution, or a swap of two
    neighbours), or most + 1 when that is more than most. Only the cells within most of the diagonal are worked out,
    and the work stops once two rows running are past most, since no way to the end can skip both. As the Insomnia
    source's Get-Edits."""
    n, m, past = len(a), len(b), most + 1
    if abs(n - m) > most:
        return past
    before, row = None, [min(j, past) for j in range(m + 1)]
    for i in range(1, n + 1):
        new = [past] * (m + 1)
        new[0] = min(i, past)
        for j in range(max(1, i - most), min(m, i + most) + 1):
            d = min(row[j - 1] + (a[i - 1] != b[j - 1]), row[j] + 1, new[j - 1] + 1)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d = min(d, before[j - 2] + 1)
            new[j] = min(d, past)
        if min(new) > most and min(row) > most:
            return past
        before, row = row, new
    return row[m]


def name_hint(wanted, names, secrets=()):
    """What the message for a name that matched none adds about those that came near it, or "". In turn: the names that
    differ from it only in letter case, compared folded (see folded); else those that differ only in letter case or in
    characters a message shows as escapes, compared as skeletons (see skeleton); else the closest, the fewest edits
    apart within a few (see close_edits), compared as skeletons too. It lists NAMES_LISTED at most and counts the rest
    (see listed), each with any secret in it masked. Each is shown, never chosen: a name in another letter case may be
    another environment, such as production. Names only, never a value. Among more than NAMES_COMPARED names, or for a
    name past LONGEST_COMPARED characters, only letter case is looked at, and otherwise only names of LONGEST_COMPARED
    characters at most are. As the Insomnia source's Get-NameHint."""
    names = [n for n in sorted({"" if n is None else str(n) for n in names}) if not same_name(n, wanted)]
    want = folded(wanted)
    cased = [n for n in names if folded(n) == want]
    if cased:
        return f"; names are case-sensitive, and {listed(cased, secrets)} {differ(cased)} only in letter case"
    if len(names) > NAMES_COMPARED or len(wanted) > LONGEST_COMPARED:
        return ""
    names = [n for n in names if len(n) <= LONGEST_COMPARED]
    want, shapes = skeleton(wanted), [skeleton(n) for n in names]
    same_shape = [n for n, s in zip(names, shapes) if s == want]
    if same_shape:
        return (f"; names are matched exactly, and {listed(same_shape, secrets)} {differ(same_shape)} only in letter "
                "case or in characters shown as escapes")
    closest, least = [], 4  # more edits than close_edits ever allows
    for n, s in zip(names, shapes):
        most = min(close_edits(len(want), len(s)), least)  # one further off than the closest yet is of no use
        if most and (d := edits(want, s, most)) <= most:
            if d < least:
                closest, least = [], d
            closest.append(n)
    if not closest:
        return ""
    return f"; the closest {'name is' if len(closest) == 1 else 'names are'} {listed(closest, secrets)}"


def differ(names):
    """The verb for names that differ from the one wanted: one differs, more differ."""
    return "differs" if len(names) == 1 else "differ"


def pick_folder(collection, name, secrets=()):
    """The top-level folder whose auth applies: the one named, exactly (see same_name); else none when the collection
    has its own auth; else the only folder with auth. When no folder has the name, the message names those that came
    near it (see name_hint). Every name it shows has the secrets known so far masked (see shown_name)."""
    folders = [i for i in collection.get("item") or [] if isinstance(i, dict) and "item" in i]
    if name:
        match = [f for f in folders if same_name(f.get("name"), name)]
        if len(match) != 1:
            hint = "" if match else name_hint(name, [f.get("name") for f in folders], secrets)
            raise Problem(f"expected one top-level folder {shown_name(name, secrets)}, found {len(match)}{hint}")
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


def base_url_variable(variables, name):
    """The variable that holds the base URL: the one named, or when none is, the only one whose name matches it
    ignoring case (a collection written for baseURL); Problem when there is no such variable, or more than one."""
    if name in variables:
        return name
    near = [k for k in variables if str(k).lower() == name.lower()]
    if len(near) == 1:
        return near[0]
    if near:
        raise Problem(f"the environment has no '{name}' variable, and {len(near)} differ from it in letter case only "
                      f"({', '.join(repr(str(k)) for k in near)}): pass --base-url-var")
    raise Problem(f"the environment has no '{name}' variable")


def build(collection, environment, extra, folder_name, base_url_var, path):
    """Everything the request needs, with what may be shown kept apart from what is sent."""
    check_exports(collection, environment)
    variables, secrets = load_variables(collection, environment, extra)
    for declared in [s for s in secrets if "{{" in s]:  # a --vars or secret-typed value that holds templates
        try:
            secrets.append(resolve(declared, variables, secrets))
        except Problem:
            pass  # unresolvable: if the request uses it, resolving it there says so
    base_url_var = base_url_variable(variables, base_url_var)
    # The base URL is configuration, shown as resolved; only a user:password@ in it holds a credential.
    base_tree = resolution(variables[base_url_var], variables)
    base = base_tree[0].rstrip("/")
    if not base.startswith("https://"):
        raise Problem(f"'{base_url_var}' must be an https:// URL")
    scheme, url_user, url_password, host_on = split_userinfo(base)  # http.client cannot send it; Postman sends basic auth
    credential = []  # the userinfo's credential, and every part a template put into it, found by where it went
    if url_password:
        start = len(scheme) + len(url_user) + 1
        credential = [url_password]
        parts_within(base_tree, start, start + len(url_password), credential)
    elif url_user is not None:  # https://KEY@host, or KEY:@host: with no password, the user is the credential
        credential = [url_user]
        parts_within(base_tree, len(scheme), len(scheme) + len(url_user), credential)
    secrets += [form for c in credential for form in (c, urllib.parse.unquote(c))]
    if not path.startswith("/"):
        path = "/" + path
    folder = pick_folder(collection, folder_name, secrets)
    # A folder with no auth of its own inherits the collection's, as in Postman; only an explicit noauth means none.
    auth = (folder or {}).get("auth") or collection.get("auth") or {}
    kind = auth.get("type") or "noauth"
    headers = {"Accept": "application/json", "User-Agent": "api-call"}
    shown = {"Accept": "application/json", "User-Agent": "api-call"}
    auth_text = "none"
    query = None
    if kind == "apikey":
        a = kv(auth.get("apikey"))
        key, value = resolve(a.get("key"), variables), resolve(a.get("value"), variables, secrets)  # the name is shown
        if not value:
            raise Problem("the API key resolves to an empty value: is a --vars file missing?")
        secrets.append(value)
        if a.get("in") == "query":
            if not key:
                raise Problem("the API key auth names no query parameter")
            query, auth_text = key, f"API key in query parameter {hide(key, secrets)}"
        else:
            shown_key = hide(key, secrets)
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
        user_parts = []
        user, password = resolve(b.get("username"), variables, user_parts), resolve(b.get("password"), variables, secrets)
        if not password and (not user or TEMPLATE.search(str(b.get("password") or ""))):
            # A blank password typed in is how an API that takes the key as the user is called; a templated one that
            # resolves empty, or no user either, is a value kept out of the export and not supplied.
            raise Problem("the basic auth password resolves to an empty value: is a --vars file missing?")
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        secrets += [password, token]
        if not password:  # the user is the credential, as with curl -u key:
            secrets += [user] + user_parts
        headers["Authorization"], shown["Authorization"] = f"Basic {token}", f"Basic {MASK}"
        auth_text = (f"basic auth as {hide(user, secrets) if password else MASK}" if user
                     else "basic auth, with an empty user")
    elif kind != "noauth":
        raise Problem(f"auth type '{kind}' is not supported (API key, bearer and basic only)")
    if url_user is not None and not any(h.lower() == "authorization" for h in headers):
        # As Postman does: the URL's user and password, percent-decoded, as basic auth, unless the auth set its own.
        user = urllib.parse.unquote(url_user)
        token = base64.b64encode(f"{user}:{urllib.parse.unquote(url_password or '')}".encode("utf-8")).decode("ascii")
        secrets.append(token)
        headers["Authorization"], shown["Authorization"] = f"Basic {token}", f"Basic {MASK}"
        via = (f"basic auth from the base URL as {hide(url_user, secrets) if url_password else MASK}" if url_user
               else "basic auth from the base URL, with an empty user")  # the Insomnia source's wording
        auth_text = via if auth_text == "none" else f"{auth_text}, and {via}"
    shown_base = scheme + host_on
    if url_user is not None:  # the password masked by its place in the URL as resolved, and the user when it is the key
        shown_base = (scheme + (MASK if url_user and not url_password else url_user)
                      + ("" if url_password is None else ":" + MASK) + "@" + host_on)
    url, shown_url = encoded(scheme + host_on + path), encoded(hide(shown_base, secrets) + path)
    if query:
        joiner = "&" if "?" in url else "?"
        url += joiner + urllib.parse.urlencode([(query, value)])
        shown_url += joiner + hide(urllib.parse.quote_plus(query), secrets) + "=" + MASK
    return {"url": url, "shown_url": shown_url, "headers": headers, "shown_headers": shown, "auth": auth_text,
            "folder": folder.get("name") if folder is not None else None, "path": path,
            "secrets": list(dict.fromkeys(s for s in secrets if s)),
            # An @ after the scheme may be userinfo that a / ? or # in it kept split_userinfo from finding: then an
            # error from the request can quote a credential nothing knows to mask (see main).
            "userinfo": "@" in base[len(scheme):]}


def texts(record):
    """Every string the record shows that came from the request or the response: each value but the record's own
    fields (see OWN), its request headers' names and values, its response headers' values, all of them as JSON
    writes them, and the body as a renderer draws it, pretty-printed when it is JSON."""
    out = [v for k, v in record.items() if isinstance(v, str) and k not in OWN]
    request = record.get("request_headers") or {}
    out += [str(k) for k in request] + [str(v) for v in request.values()]
    out += [str(v) for v in (record.get("response_headers") or {}).values()]  # the names are the four this script picks
    out.append(json.dumps(out, ensure_ascii=False))
    try:
        out.append(json.dumps(json.loads(record.get("body") or ""), indent=2, ensure_ascii=False))
    except (ValueError, RecursionError):
        pass
    return out


def exposed(record, secrets):
    """The first secret that would be printed or drawn, in any form (see forms) and any view (see views), or None.
    A value under six characters is too short to tell from ordinary text, and is not checked for."""
    return find(texts(record), secrets)


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


def body_text(body, content_type):
    """The body as text, as the Insomnia source's ConvertFrom-Body reads it: a byte order mark wins (UTF-8, or UTF-16
    or UTF-32 in either byte order) and is dropped, since one before a JSON body would keep it from pretty-printing;
    then the charset Content-Type declares, if Python knows it; else UTF-8. An unknown charset, such as a misspelling,
    is no reason to lose a response, and a byte that does not decode reads as U+FFFD."""
    for bom, codec in BOMS:
        if body.startswith(bom):
            return body[len(bom):].decode(codec, "replace")
    message = email.message.Message()
    message["Content-Type"] = content_type or ""
    charset = (message.get_content_charset() or "").strip().strip("\"'")
    if charset:
        try:
            return body.decode(charset, "replace")
        except (LookupError, UnicodeError):  # unknown, not a text encoding (base64), or one that never decodes
            pass
    return body.decode("utf-8", "replace")


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
    secrets = b["secrets"] + ([passphrase] if args.cert and passphrase else [])
    try:
        status, response_headers, body, elapsed, sent = send(b["url"], b["headers"], args.cert, args.key, args.timeout)
    except Problem as e:
        sys.exit(scrub(str(e), secrets))
    except Exception as e:  # a failed request's message can quote the URL, and with it a key in the query
        if b["userinfo"]:  # it may quote a password that a / ? or # cut short, which no secret masks (nonnumeric port)
            sys.exit(UNREACHED)
        sys.exit(f"the request failed: {type(e).__name__}: {scrub(str(e), secrets)}")
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
        "body": body_text(body, response_headers.get("Content-Type")),
    }
    if exposed(record, secrets):
        sys.exit("refusing to print: a secret would appear in the output")
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
