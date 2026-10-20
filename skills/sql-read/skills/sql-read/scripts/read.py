r"""Reads from a SQL Server database through a saved connection: one SELECT per run, checked by a guard, capped, and
previewed as a plan before it runs.

Usage:
  python3 read.py connections [--json]
  python3 read.py rules
  python3 read.py plan  --cx NAME [--sql FILE] [query options]
  python3 read.py check --cx NAME [--timeout S] [--out DIR] [--name NAME]
  python3 read.py query --cx NAME --sql FILE [--max-rows N] [--format csv|csvi|text|html] [--timeout S]
                        [--lock-timeout MS] [--nolock] [--allow-writable-login] [--out DIR] [--name NAME]

  connections  lists the saved connections, named or not; --json prints them as JSON. No password is ever read out.
  rules        prints the allow rules, with this read.py's path, that let query --allow-writable-login past auto
               mode's classifier to the hook's prompt, for the user to add to their Claude Code settings.
  plan         prints what query (with --sql) or check (without it) would run, as one JSON object, and runs
               nothing. Exits 4 when the guard refuses the SQL (the plan then holds no SQL, only the reason), 3 when
               the connection, the transport's tool or their pairing is unusable, else 0.
  check        lists what the login can write: one row for each write role, database permission or schema permission
               it holds. Exits 0 when it has none, 5 when it has any, 1 when it printed no kind,name header.
  query        runs the SQL once: the guard checks it first, then one batch stops before the SQL if the login can
               write (exit 5) and caps the rows the SELECT returns.

Query options:
  --sql FILE              the SQL, as UTF-8: one SELECT or WITH statement, with only DECLARE @ and SET @ before it
  --max-rows N            the most rows the SELECT returns, through SET ROWCOUNT (default 200; 0 for no cap)
  --format F              csv (the default), csvi, text or html; rows are counted for csv and csvi only
  --timeout S             seconds before the run is stopped (default 60)
  --lock-timeout MS       milliseconds a read waits for a lock before it fails (default 5000)
  --nolock                read uncommitted rows rather than wait for locks: dirty reads
  --allow-writable-login  leave out the check that stops the run when the login can write
  --out DIR               where the run's script is written (default: a sql-read folder beside the --sql file;
                          for check, a sql-read folder in the temp folder)
  --name NAME             the script's file name, without extension: letters, digits, '.', '_' and '-' (default:
                          the --sql file's name without .sql; for check, check)

Common options:
  --via lprun9            the transport that runs the batch: lprun9, LINQPad 9's runner (the default and the only one)
  --connections FILE      the saved connections (default: env SQL_READ_CONNECTIONS, else LINQPad's own list:
                          %APPDATA%\LINQPad\ConnectionsV2.xml on Windows, ~/Library/Application Support/LINQPad/
                          ConnectionsV2.xml on macOS)
  --lprun PATH            lprun9 itself (default: env LPRUN9, else lprun9 on PATH, else where LINQPad 9 installs it:
                          under Program Files or %LocalAppData% on Windows, in its app bundle on macOS)

Exit codes: 0 ran; 1 the transport's tool failed (its error is shown), check printed no kind,name header, or another
error; 2 the command line, or a --sql file that is missing, is not a regular file or is not UTF-8 text; 3 the transport's
tool is missing, something sql-read did not write stands where its script goes, the connection is not found or has no
name, or the transport cannot serve the connection's sign-in kind; 4 the guard refused the SQL; 5 the login can write;
6 timed out; 130 stopped with Ctrl+C.

stdout is the tool's output, unchanged. stderr ends with one summary line: the rows (counted for csv and csvi), the
time taken, and whether the cap was reached. Nothing writes the results to disk. The guard is a shape check, and the
login's permissions are the boundary: use a login that can only read.

For a hook, plan_for_command(argv, cwd) gives the plan read.py plan would print, and reason(plan) the text of the
permission prompt; neither runs anything. Importing this file starts no process and opens no file.
"""
import argparse, bisect, codecs, csv, importlib, io, json, math, os, re, stat, sys, time, unicodedata
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from backend import NotNamed, Unusable  # noqa: E402  (the types the backends share; see backend.py)

DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT, DEFAULT_LOCK_TIMEOUT_MS, DEFAULT_FORMAT = 200, 60, 5000, "csv"
FORMATS = ("csv", "csvi", "text", "html")
MAX_SQL_BYTES = 64 * 1024
MAX_TIMEOUT = 86400  # a day; past that a timeout is no timeout
MAX_INT = 2**31 - 1  # the most SET ROWCOUNT and SET LOCK_TIMEOUT take
EXCERPT_LINES, EXCERPT_CHARS = 8, 600  # how much of the SQL the permission prompt shows
EXCERPT_MARK = "|"  # what starts each line of SQL in the prompt, so that none can pass for a line of the prompt's own

# The backends, imported only when a command needs one (see backend.py). The registry is LINQPad's connection list for
# every transport. TRANSPORT_OPTIONS names each transport's own command-line options, passed to its factory by name.
REGISTRY = "registries.linqpad"
TRANSPORTS = {"lprun9": "transports.lprun9"}
TRANSPORT_OPTIONS = {"lprun9": ("lprun",)}

# The words the guard refuses wherever they stand outside a string, a comment or a [bracketed] or "quoted" name:
# whole words, any case, and xp_* and sp_* for any word that starts so. One constant, mirrored in the README. MERGE
# right before JOIN or UNION is a hint, not the MERGE statement, and passes (see _merge_hint).
DENY_LIST = """
INSERT UPDATE DELETE MERGE INTO EXEC EXECUTE CREATE ALTER DROP TRUNCATE GRANT REVOKE DENY BACKUP RESTORE BULK
OPENROWSET OPENQUERY OPENDATASOURCE OPENXML DBCC KILL SHUTDOWN RECONFIGURE WAITFOR RAISERROR THROW PRINT GOTO
RETURN IF WHILE BEGIN COMMIT ROLLBACK SAVE TRAN TRANSACTION USE ENABLE DISABLE SETUSER REVERT
ADD WRITETEXT UPDATETEXT RECEIVE SEND CONVERSATION DIALOG
xp_* sp_*
UPDLOCK XLOCK TABLOCKX HOLDLOCK SERIALIZABLE REPEATABLEREAD TABLOCK
"""
_DENY_WORDS = frozenset(w.upper() for w in DENY_LIST.split() if not w.endswith("*"))
_DENY_PREFIXES = tuple(w[:-1].upper() for w in DENY_LIST.split() if w.endswith("*"))
# Refused words that make plausible column names, for which a refusal says how to write the name so it passes.
_FALSE_FRIENDS = frozenset(("SAVE USE RETURN IF PRINT KILL ENABLE DISABLE ADD SEND RECEIVE BULK DENY BACKUP "
                           "CONVERSATION DIALOG").split())
# Reserved words that begin a statement and never stand inside a SELECT. At the top level one of them starts another
# statement, semicolon or not; only DECLARE and SET may, and only before the SELECT. FETCH after ROW or ROWS is part
# of the SELECT's OFFSET ... FETCH.
_STATEMENT_WORDS = frozenset("DECLARE SET OPEN CLOSE DEALLOCATE FETCH READTEXT CHECKPOINT".split())

# What the probe and check treat as able to write: the server role sysadmin, these database roles, these database-scope
# permissions, and these permissions on any one schema (GRANT INSERT ON SCHEMA::dbo). Neither sees grants on single
# objects, columns or other securables. Each schema is asked for by its own name, as the documented SCHEMA example
# (HAS_PERMS_BY_NAME('S', 'SCHEMA', 'ALTER')) gives it: fn_my_permissions reads a dotted name as several parts and
# answers NULL when it finds nothing, so a schema whose name holds a dot is not reached.
WRITE_ROLES = ("db_owner", "db_datawriter", "db_ddladmin")
WRITE_PERMISSIONS = ("INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "CREATE TABLE", "CREATE PROCEDURE",
                     "CREATE FUNCTION", "CREATE VIEW", "ALTER ANY SCHEMA", "TAKE OWNERSHIP", "ALTER ANY USER",
                     "ALTER ANY ROLE")
SCHEMA_PERMISSIONS = ("INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "TAKE OWNERSHIP")
GUARD_MARK = "sql-read-guard:"
# The user's override of the probe: connection names, comma-separated, or * for all, whose login may write.
WRITABLE_OK = "SQL_READ_WRITABLE_OK"
PROBE = (
    "IF IS_SRVROLEMEMBER('sysadmin') = 1\n"
    "   OR IS_MEMBER('db_owner') = 1 OR IS_MEMBER('db_datawriter') = 1 OR IS_MEMBER('db_ddladmin') = 1\n"
    "   OR EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, 'DATABASE') WHERE permission_name IN\n"
    "      ('INSERT','UPDATE','DELETE','ALTER','CONTROL','CREATE TABLE','CREATE PROCEDURE','CREATE FUNCTION',\n"
    "       'CREATE VIEW','ALTER ANY SCHEMA','TAKE OWNERSHIP','ALTER ANY USER','ALTER ANY ROLE'))\n"
    "   OR EXISTS (SELECT 1 FROM sys.schemas AS s CROSS APPLY sys.fn_my_permissions(s.name, 'SCHEMA') AS p\n"
    "      WHERE p.permission_name IN ('INSERT','UPDATE','DELETE','ALTER','CONTROL','TAKE OWNERSHIP'))\n"
    f"  THROW 50001, '{GUARD_MARK} this login can write to this database', 1;")
# check's query: a row for each way the login can write; none means read-only.
CHECK_SQL = (
    "SELECT kind, name FROM (\n"
    "    SELECT 'role' AS kind, 'sysadmin' AS name WHERE IS_SRVROLEMEMBER('sysadmin') = 1\n"
    "    UNION ALL SELECT 'role', 'db_owner' WHERE IS_MEMBER('db_owner') = 1\n"
    "    UNION ALL SELECT 'role', 'db_datawriter' WHERE IS_MEMBER('db_datawriter') = 1\n"
    "    UNION ALL SELECT 'role', 'db_ddladmin' WHERE IS_MEMBER('db_ddladmin') = 1\n"
    "    UNION ALL SELECT 'permission', permission_name COLLATE DATABASE_DEFAULT\n"
    "        FROM sys.fn_my_permissions(NULL, 'DATABASE')\n"
    "        WHERE permission_name IN ('INSERT','UPDATE','DELETE','ALTER','CONTROL','CREATE TABLE',\n"
    "            'CREATE PROCEDURE','CREATE FUNCTION','CREATE VIEW','ALTER ANY SCHEMA','TAKE OWNERSHIP',\n"
    "            'ALTER ANY USER','ALTER ANY ROLE')\n"
    "    UNION ALL SELECT 'permission',\n"
    "        (p.permission_name COLLATE DATABASE_DEFAULT) + ' ON SCHEMA::' + (s.name COLLATE DATABASE_DEFAULT)\n"
    "        FROM sys.schemas AS s CROSS APPLY sys.fn_my_permissions(s.name, 'SCHEMA') AS p\n"
    "        WHERE p.permission_name IN ('INSERT','UPDATE','DELETE','ALTER','CONTROL','TAKE OWNERSHIP')\n"
    ") AS found\n"
    "ORDER BY kind DESC, name;")
TIMED_OUT = ("if the connection signs in with Azure AD interactively, open it once in LINQPad first to cache the "
             "token")
CAP_NOTE = "\u2014 cap reached; there may be more: raise --max-rows or add TOP and ORDER BY"

# The plan's fields in the order read.py plan prints them: what it is a plan of, the plan's own list, then the exit
# code read.py plan gives it and, when that is not 0, why.
PLAN_KEYS = ("command", "transport", "connection", "server", "database", "production", "auth", "sql", "guard",
             "max_rows", "timeout", "lock_timeout_ms", "nolock", "allow_writable_login", "format", "artifact", "argv",
             "exit", "error")


# --- The guard ---------------------------------------------------------------------------------------------------

_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"  # every character str.splitlines() ends a line at
_LINE_END = re.compile("\r\n|[" + re.escape(_BREAKS) + "]")
# A line a batch-splitting client takes for a command, wherever it stands: GO with a count or a comment after it, and
# sqlcmd's :r, :connect and !! lines.
_CLIENT_LINE = re.compile(r"\s*(?:GO(?:\s+[0-9]+)?\s*;?\s*(?:--.*|/\*.*)?|:.*|!!.*)", re.IGNORECASE)
_WORD = re.compile(r"(?:[^\W\d]|[@#])[\w@#$]*")
# A number ends where SQL Server's would, or later: "1eUPDATE" is 1e then UPDATE, so the word is not hidden in it.
_NUMBER = re.compile(r"0[xX][0-9A-Fa-f]*|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]*)?")
_NOT_NAME = re.compile(r"[^A-Z0-9_@#$]+")  # what cannot be part of a T-SQL keyword or name, once upper-cased
_HIDDEN = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})  # categories a terminal does not show as they are
_CLOSERS = {"'": ("'", "a 'string' is not closed"), "[": ("]", "a [bracketed] name is not closed"),
            '"': ('"', 'a "quoted" name is not closed')}


class _Refusal(Exception):
    """The guard's reason for refusing, raised from inside its scan."""


class _Token:
    """One piece of the SQL's code: a word, a number, a literal (a 'string', a [name] or a "name", whole) or one other
    character. depth is how many parentheses enclose it."""
    __slots__ = ("kind", "text", "start", "depth")

    def __init__(self, kind, text, start):
        self.kind, self.text, self.start, self.depth = kind, text, start, 0


def guard(sql):
    """(ok, reason): whether sql has the shape of one read, and why not when it has not. A shape check, not a parser:
    the login's permissions are the boundary.

    Comments (-- and nested /* */) are dropped and 'strings', [names] and "names" are taken whole, so nothing in them
    counts. Accepted only when all of these hold:
      1. the SQL is not empty, is at most 64 KB as UTF-8, does not start with < as markup does and holds no
         markup tag (</, <?, <!, /> or <Word>) anywhere, as a .linq script or a connection list would; has no line a
         client would take for GO or a command;
      2. before the main statement there are only DECLARE @... and SET @... statements (so SET ROWCOUNT is refused);
      3. the main statement begins SELECT or WITH and is the last: after its top-level ';' come only more ';', and
         no reserved word that starts a statement (DECLARE, SET, OPEN, FETCH ...) follows it at the top level;
      4. no word of DENY_LIST appears at any depth, nor NEXT VALUE FOR, which advances a sequence; MERGE as the hint
         MERGE JOIN or MERGE UNION is not the MERGE statement, and passes.
    What the scan cannot be sure SQL Server reads the same way is refused rather than guessed: an unclosed comment,
    string or name, unbalanced parentheses, a -- comment holding a line break other than LF or CRLF, and a control or
    invisible character outside a string. A word glued to a character that is not part of a name is checked part by
    part, and in its NFKC form, so a refused word cannot hide in it.

    A reason never quotes the SQL: it names a deny-list entry (a word of DENY_LIST, or sp_* and xp_*), a keyword of the
    shape check, or a line number, so that text a file holds by mistake, a password or an .env line, is not repeated
    on stderr, in the plan or in the prompt.
    """
    if not isinstance(sql, str):
        return False, "the SQL is not text"
    size = len(sql.encode("utf-8", "surrogatepass"))
    if size > MAX_SQL_BYTES:
        return False, f"the SQL is {size:,} bytes, over the 64 KB limit ({MAX_SQL_BYTES:,} bytes)"
    if re.match(r"\s*<", sql):
        # No T-SQL starts so. A .linq script or LINQPad's connection list does, and holds a password: refused whole,
        # before a check that would quote a word or a line of it.
        return False, "the SQL starts with <, as a .linq script or a connection list does: give the T-SQL alone"
    for number, line in enumerate(sql.splitlines(), 1):
        if _CLIENT_LINE.fullmatch(line):
            return False, _client_line(number, line)
    try:
        tokens = _depths(_scan(sql))
    except _Refusal as refusal:
        return False, str(refusal)
    if not tokens:
        return False, "the SQL is empty: it holds nothing but whitespace and comments"
    line_of, last = _line_numbers(sql), 0
    markup = _markup(tokens)
    if markup is not None:
        return False, (f"line {line_of(markup.start)} holds markup, as a .linq script or a connection list does: give "
                       "the T-SQL alone")
    for t in tokens:
        line = line_of(t.start)
        if line != last and t.kind == "word" and _up(t.text) == "GO":
            return False, _client_line(line, "GO")
        last = line
    offenders = {}
    for k, t in enumerate(tokens):
        part = _denied(t.text) if t.kind == "word" and not _merge_hint(tokens, k) else None
        if part and part not in offenders:
            offenders[part] = (t.text, line_of(t.start))
    if offenders:
        # USE HINT and USE PLAN are query hints, which no bracket makes pass: no hint to write USE as a name for them.
        hint = any(t.kind == "word" and _up(t.text) == "USE" and k + 1 < len(tokens) and tokens[k + 1].kind == "word"
                   and _up(tokens[k + 1].text) in ("HINT", "PLAN") for k, t in enumerate(tokens))
        return False, _deny_reason(offenders, brackets=not hint)
    words = [_up(t.text) if t.kind == "word" else None for t in tokens]
    if any(words[k:k + 3] == ["NEXT", "VALUE", "FOR"] for k in range(len(words) - 2)):
        return False, "NEXT VALUE FOR is refused: it advances a sequence, which is a write"
    why = _shape(tokens, line_of)
    if why:
        return False, why
    return True, "one SELECT or WITH statement"


def _client_line(number, line):
    head = line.strip()
    if head[:2].upper() == "GO":
        return f"line {number} starts with GO, a batch separator: send one batch, with no GO"
    sign = "!!" if head.startswith("!!") else ":"
    return f"line {number} starts with {sign}, a client command: send T-SQL alone"


def _scan(sql):
    """The SQL's code as tokens, comments and whitespace left out. Raises _Refusal for what it cannot be sure of."""
    tokens, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in " \t\n\r" or unicodedata.category(c) == "Zs":
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end < 0 else end
            body = sql[i:end]
            body = body[:-1] if body.endswith("\r") else body
            odd = next((ch for ch in body if ch in _BREAKS), None)
            if odd is not None:
                raise _Refusal(f"a -- comment holds {_describe(odd)}, a line break SQL Server may not end the comment "
                               "at: end each line with LF or CRLF")
            i = end
        elif sql.startswith("/*", i):
            # Nested, as SQL Server nests them. Each mark is looked for again only once the scan has passed it, so
            # many openings and one far-off close cost one pass, not one pass each.
            depth, j, opening, closing = 1, i + 2, i, i
            while depth:
                if opening != -1 and opening < j:
                    opening = sql.find("/*", j)
                if closing < j:
                    closing = sql.find("*/", j)
                    if closing < 0:
                        raise _Refusal("a /* comment is not closed")
                if opening != -1 and opening < closing:
                    depth, j = depth + 1, opening + 2
                else:
                    depth, j = depth - 1, closing + 2
            i = j
        elif c in _CLOSERS:
            close, unclosed = _CLOSERS[c]
            j = i + 1
            while True:
                k = sql.find(close, j)
                if k < 0:
                    raise _Refusal(unclosed)
                if not sql.startswith(close * 2, k):
                    break
                j = k + 2
            tokens.append(_Token("literal", sql[i:k + 1], i))
            i = k + 1
        else:
            m = _WORD.match(sql, i) or _NUMBER.match(sql, i)
            if m:
                tokens.append(_Token("word" if m.re is _WORD else "number", m.group(), i))
                i = m.end()
            elif unicodedata.category(c) in _HIDDEN:
                raise _Refusal(f"{_describe(c)} is outside a string, where it could hide what runs")
            else:
                tokens.append(_Token("op", c, i))
                i += 1
    return tokens


def _depths(tokens):
    """Sets each token's depth in parentheses; raises _Refusal when they do not balance."""
    depth = 0
    for t in tokens:
        if t.kind == "op" and t.text == ")":
            depth -= 1
            if depth < 0:
                raise _Refusal("the parentheses do not balance")
        t.depth = depth
        if t.kind == "op" and t.text == "(":
            depth += 1
    if depth:
        raise _Refusal("the parentheses do not balance")
    return tokens


def _line_numbers(sql):
    """A function from an offset in sql to its line number, counting lines as str.splitlines() does."""
    starts = [m.end() for m in _LINE_END.finditer(sql)]
    return lambda offset: bisect.bisect_right(starts, offset) + 1


def _denied(word):
    """The deny-list entry that word is, or holds next to a character no keyword holds: the refused word, upper-cased,
    or its prefix with a *, as SP_*; else None."""
    for form in (word.upper(), unicodedata.normalize("NFKC", word).upper()):
        for part in _NOT_NAME.split(form):
            if part in _DENY_WORDS:
                return part
            for prefix in _DENY_PREFIXES:
                if part.startswith(prefix):
                    return prefix + "*"
    return None


def _markup(tokens):
    """The first token of a markup tag the SQL holds, else None: </, <?, <! or /> with nothing between, or <Word>. No
    T-SQL has these shapes, and a .linq script or a connection list, which hold a password, is made of them."""
    for k in range(len(tokens) - 1):
        a, b = tokens[k], tokens[k + 1]
        if a.kind != "op" or b.start != a.start + 1:
            continue
        if (a.text == "<" and b.kind == "op" and b.text in "/?!") or (a.text == "/" and _is(b, ">")):
            return a
        after = tokens[k + 2] if k + 2 < len(tokens) else None
        if (a.text == "<" and b.kind == "word" and after is not None and after.start == b.start + len(b.text)
                and _is(after, ">")):
            return a
    return None


def _merge_hint(tokens, k):
    """True when tokens[k] is MERGE right before JOIN or UNION: the hint in INNER MERGE JOIN, OPTION (MERGE JOIN) and
    OPTION (MERGE UNION), which picks how a read is done, as HASH and LOOP do. The MERGE statement never takes that
    shape: JOIN and UNION are reserved words, so they cannot name its target unbracketed, and it writes only through
    INSERT, UPDATE or DELETE, which stay refused."""
    after = tokens[k + 1] if k + 1 < len(tokens) else None
    return (_up(tokens[k].text) == "MERGE" and after is not None and after.kind == "word"
            and _up(after.text) in ("JOIN", "UNION"))


def _deny_reason(offenders, brackets=True):
    # What a reason names is the deny-list entry. A word written plainly is shown as written (it is the entry, in some
    # case); anything else, a name that starts sp_ or a spelling that is not plain ASCII, is shown as the entry and the
    # line it stands on, never as the text.
    def name(entry, text, line):
        if not entry.endswith("*") and text.isascii() and text.upper() == entry:
            return text
        return f"{entry.lower() if entry.endswith('*') else entry} (line {line})"
    names = [name(entry, *found) for entry, found in offenders.items()]
    shown = names[:6] + ([f"{len(names) - 6} more"] if len(names) > 6 else [])
    why = f"{_and(shown)} {'is' if len(names) == 1 else 'are'} refused: the SQL may only read, as one SELECT"
    if brackets and all(entry in _FALSE_FRIENDS for entry in offenders):
        text, _ = next(iter(offenders.values()))
        why += ("; a column or table with that name passes in brackets, as "
                f"[{text if text.isascii() else 'name'}]")
    return why


def _shape(tokens, line_of):
    """Why the statements are not one SELECT or WITH with only DECLARE @ and SET @ before it; None when they are."""
    main = next((k for k, t in enumerate(tokens)
                 if t.depth == 0 and t.kind == "word" and _up(t.text) in ("SELECT", "WITH")), None)
    if main is None:
        return "the SQL must be one SELECT or WITH statement, and none was found outside parentheses"
    statement = []
    for k in range(main + 1):
        t = tokens[k] if k < main else None
        if (t is None or t.depth == 0 and (_is(t, ";") or _starts_statement(tokens, k))) and statement:
            first, second = statement[0], statement[1] if len(statement) > 1 else None
            if not (first.kind == "word" and _up(first.text) in ("DECLARE", "SET") and second is not None
                    and second.kind == "word" and second.text.startswith("@") and not second.text.startswith("@@")):
                return (f"before the SELECT, only DECLARE @\u2026 and SET @\u2026 may come, and line "
                        f"{line_of(first.start)} starts {_keyword(first)} instead")
            statement = []
        if t is not None and not _is(t, ";"):
            statement.append(t)
    for k in range(main + 1, len(tokens)):
        t = tokens[k]
        if t.depth:
            continue
        if _is(t, ";"):
            rest = [u for u in tokens[k + 1:] if not _is(u, ";")]
            if rest:
                return f"only one statement may run, and line {line_of(rest[0].start)} follows the SELECT's semicolon"
            return None
        if _starts_statement(tokens, k):
            return (f"only one statement may run, and {_up(t.text)} starts another after the SELECT (line "
                    f"{line_of(t.start)})")
    return None


def _keyword(token):
    """A token named in a reason: the keyword when it is one the shape check knows, else only that it is a name."""
    word = _up(token.text) if token.kind == "word" else ""
    return word if word in _STATEMENT_WORDS or word in ("DECLARE", "SET") else "something else"


def _is(token, character):
    return token.kind == "op" and token.text == character


def _starts_statement(tokens, k):
    t = tokens[k]
    word = _up(t.text) if t.kind == "word" else ""
    if word not in _STATEMENT_WORDS:
        return False
    before = tokens[k - 1] if k else None
    return not (word == "FETCH" and before is not None and before.kind == "word"
                and _up(before.text) in ("ROW", "ROWS"))


def _up(word):
    """A word as the shape checks compare it: NFKC-normalized, then upper-cased, as the deny list's check reads it
    too. Folding more than SQL Server does can only refuse more, or pass text SQL Server will not run."""
    return unicodedata.normalize("NFKC", word).upper()


def _describe(ch):
    name = unicodedata.name(ch, "")
    return f"U+{ord(ch):04X} ({name})" if name else f"U+{ord(ch):04X}"


def _and(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# --- The batch ---------------------------------------------------------------------------------------------------

def batch(sql, *, max_rows=DEFAULT_MAX_ROWS, lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS, nolock=False,
          allow_writable_login=False):
    """The T-SQL batch a query sends: the prelude, then sql byte for byte. SET NOCOUNT ON; SET LOCK_TIMEOUT; with
    nolock, READ UNCOMMITTED; unless allow_writable_login, the probe, which THROWs before the SQL when the login can
    write; then, unless max_rows is 0, SET ROWCOUNT, which caps the rows the SELECT returns without rewriting it."""
    lines = ["SET NOCOUNT ON;", f"SET LOCK_TIMEOUT {_whole(lock_timeout_ms)};"]
    if nolock:
        lines.append("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
    if not allow_writable_login:
        lines.append(PROBE)
    if _whole(max_rows):
        lines.append(f"SET ROWCOUNT {max_rows};")
    return "\n".join(lines) + "\n" + sql


def check_batch(*, lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS):
    """The batch check sends: the query's prelude without the cap or the probe, then CHECK_SQL."""
    return f"SET NOCOUNT ON;\nSET LOCK_TIMEOUT {_whole(lock_timeout_ms)};\n{CHECK_SQL}"


def _whole(value):
    """value when it is a whole number SET ROWCOUNT and SET LOCK_TIMEOUT take, so nothing else reaches the prelude."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INT:
        raise ValueError(f"expected a whole number from 0 to {MAX_INT}, not {value!r}")
    return value


# --- The backends ------------------------------------------------------------------------------------------------

def load_registry(path):
    """The connection registry for --connections path (absolute), or for its own default when path is None. Its
    module is imported now, not when read.py is; one that cannot be loaded is Unusable (exit 3)."""
    return _backend(REGISTRY, "the connection registry").registry(path)


def load_transport(via, **options):
    """The transport named by --via, made with its own command-line options by name (lprun9: lprun). Its module is
    imported now, not when read.py is; one that cannot be loaded is Unusable (exit 3)."""
    return _backend(TRANSPORTS[via], f"the {via} transport").transport(**options)


def _backend(module, what):
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise Unusable(f"{what} ({module.replace('.', '/')}.py beside read.py) could not be loaded: {e}. "
                       "Reinstall the sql-read plugin.") from None


# --- The plan ----------------------------------------------------------------------------------------------------

class _Run:
    """What a run needs besides its plan: the backends, the connection, the SQL and where its script goes."""

    def __init__(self):
        self.registry = self.transport = self.cx = self.sql = self.out = self.name = None


class _BadSql(Exception):
    """The --sql file cannot be read as text: exit 2."""


class _TooBig(Exception):
    """The --sql file is far over the guard's limit, so it is refused without being read: exit 4."""


def writable_ok(name):
    """Whether env SQL_READ_WRITABLE_OK lists connection name as one whose login the user lets write: the variable
    holds connection names, comma-separated and compared as --cx is (trimmed, case-insensitive), or * for every one.
    It changes nothing that runs. Only --allow-writable-login, on the command line the gate reads, leaves the probe
    out; the variable is the user's leave for Claude to pass that flag, and the prompt says, as the gate's own
    environment has it, whether the connection is listed."""
    names = {part.strip().casefold() for part in os.environ.get(WRITABLE_OK, "").split(",") if part.strip()}
    return bool(name and name.strip()) and ("*" in names or name.strip().casefold() in names)


def run_prefix():
    """The start of the command that runs this read.py with --allow-writable-login, as the allow rules match it:
    python3, this file's own path, quoted, with forward slashes, then query and the flag."""
    return f'python3 "{Path(__file__).resolve().as_posix()}" query --allow-writable-login'


def allow_rules():
    """The allow rules for the user's settings: run_prefix() then a wildcard, and nothing else wild, since a * before
    the subcommand would approve whatever options stood there."""
    return [f"{tool}({run_prefix()} *)" for tool in ("Bash", "PowerShell")]


def _blank(command=None, code=0, error=None):
    plan = dict.fromkeys(PLAN_KEYS)
    plan.update(command=command, exit=code, error=error)
    return plan


def _plan(opts, cwd, runs=False):
    """The plan for parsed options, and what a run needs besides. With runs, it stops at the first problem, so a SQL
    the guard refuses never loads a backend; without, it carries on and fills in what it can, so a plan that will not
    run still shows where it would have. The first problem found sets the exit code: 2, then 4, then 3."""
    query = opts.command == "query" or (opts.command == "plan" and opts.sql is not None)
    plan, run, problems = _blank("query" if query else "check"), _Run(), []
    plan.update(transport=opts.via, connection=opts.cx, timeout=opts.timeout)
    if query:
        plan.update(max_rows=opts.max_rows, lock_timeout_ms=opts.lock_timeout, nolock=opts.nolock,
                    allow_writable_login=opts.allow_writable_login, format=opts.format)
        path = _local(opts.sql, cwd)
        out, name = path.parent / "sql-read", _default_name(path.stem)
        try:
            run.sql = _read_sql(path)
        except _BadSql as e:
            plan.update(exit=2, error=str(e))
            return plan, run
        except _TooBig as e:
            plan["guard"] = {"ok": False, "reason": str(e)}
            problems.append((4, f"the guard refused the SQL: {e}"))
    else:
        plan.update(max_rows=None, lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS, nolock=False, allow_writable_login=False,
                    format=DEFAULT_FORMAT)
        out, name = _temp() / "sql-read", "check"
        run.sql = CHECK_SQL
    if run.sql is not None:
        # The plan holds the SQL only once the guard passes it. A refused file can be anything, a .linq script or the
        # connection list included, so the plan, the prompt and the messages carry the guard's reason alone.
        ok, why = guard(run.sql)
        plan.update(sql=run.sql if ok else None, guard={"ok": ok, "reason": why})
        if not ok:
            problems.append((4, f"the guard refused the SQL: {why}"))
    run.out = _local(opts.out, cwd) if getattr(opts, "out", None) else out
    run.name = getattr(opts, "name", None) or name
    if not (problems and runs):
        _connect(opts, cwd, plan, run, problems)
    if not (problems and runs):
        _carry(opts, cwd, plan, run, problems)
    if problems:
        plan["exit"], plan["error"] = problems[0]
    return plan, run


def _connect(opts, cwd, plan, run, problems):
    """Resolves --cx through the registry, into the plan."""
    try:
        run.registry = load_registry(str(_local(opts.connections, cwd)) if opts.connections else None)
        cx = run.registry.resolve(opts.cx)
        if not cx.name:
            raise NotNamed(f'"{opts.cx.strip()}" names a connection with no display name: give it one in LINQPad')
    except Unusable as e:
        problems.append((3, str(e)))
        return
    run.cx = cx
    plan.update(connection=cx.name, server=cx.server, database=cx.database, production=bool(cx.production),
                auth=cx.auth)


def _carry(opts, cwd, plan, run, problems):
    """Loads the transport, into the plan: the script it would write, and, given a connection it serves, the command
    it would run."""
    try:
        run.transport = load_transport(opts.via, **{name: _tool(getattr(opts, name), cwd)
                                                    for name in TRANSPORT_OPTIONS[opts.via]})
        artifact = run.transport.artifact(run.out, run.name)
        plan["artifact"] = str(artifact)
        if run.cx is None:
            return
        if run.cx.auth not in run.transport.serves:
            kinds = ", ".join(sorted(run.transport.serves)) or "none"
            raise Unusable(f'the {opts.via} transport cannot sign in with {run.cx.auth}, as "{run.cx.name}" does; '
                           f"it serves {kinds}")
        plan["argv"] = [str(a) for a in run.transport.argv(run.cx, plan["format"], artifact)]
    except Unusable as e:
        problems.append((3, str(e)))


def _local(path, cwd):
    """path made absolute against cwd, without touching the disk. On Windows a Git Bash drive path such as /c/x means
    C:/x: Git Bash converts one before a Windows program starts, but a hook reads the command as it was typed."""
    text = os.path.expanduser(str(path))
    if os.name == "nt":
        text = re.sub(r"\A/([A-Za-z])(?=/|\Z)", lambda m: m.group(1).upper() + ":", text)
    return Path(os.path.abspath(os.path.join(cwd, text)))


def _tool(value, cwd):
    """A transport's tool option as its factory gets it: a path with a folder in it made absolute against cwd, a bare
    program name (looked up on PATH) as given, and None when the option was not given."""
    if not value:
        return None
    return str(_local(value, cwd)) if re.search(r"[/\\]", value) or value.startswith("~") else value


def _temp():
    """The temp folder, found from the environment as tempfile would, but without writing a file to test it."""
    for name in ("TMPDIR", "TEMP", "TMP"):
        if os.environ.get(name):
            return Path(os.path.abspath(os.environ[name]))
    import tempfile
    return Path(tempfile.gettempdir())


_BOMS = ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))


def _read_sql(path):
    """The SQL in the file at path: UTF-8, or UTF-16 with a byte order mark, which is left out. Line ends and every
    other character are kept as they are."""
    try:
        status = path.stat()
        if not stat.S_ISREG(status.st_mode):
            # A folder, a device or a pipe: reading one could wait for ever, and a hook has seconds.
            raise _BadSql(f"the --sql file {path} is not a regular file")
        if status.st_size > 4 * MAX_SQL_BYTES + 3:
            raise _TooBig(f"the SQL file is {status.st_size:,} bytes, far over the 64 KB limit ({MAX_SQL_BYTES:,} "
                          "bytes)")
        data = path.read_bytes()
    except FileNotFoundError:
        raise _BadSql(f"the --sql file {path} does not exist") from None
    except OSError as e:
        raise _BadSql(f"the --sql file {path} cannot be read: {e.strerror or e}") from None
    encoding = "utf-8"
    for bom, codec in _BOMS:
        if data.startswith(bom):
            data, encoding = data[len(bom):], codec
            break
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        raise _BadSql(f"the --sql file {path} is not UTF-8 text (or UTF-16 with a byte order mark): save it as "
                      "UTF-8") from None


_PLAIN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")
_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                      *(f"LPT{n}" for n in range(1, 10))})


def _plain(name):
    """True for a file name that is the same on every system: no folder, no space, not Windows' device names."""
    return bool(_PLAIN.fullmatch(name)) and not name.endswith(".") and name.split(".")[0].upper() not in _DEVICES


def _default_name(stem):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:100].rstrip(".") or "query"
    return name if _plain(name) else f"q_{name}"[:100].rstrip(".")


# --- The prompt --------------------------------------------------------------------------------------------------

def plan_for_command(argv, cwd=None):
    """The plan read.py plan would print for argv, everything after read.py with the subcommand first (query, check or
    plan; for check, or plan without --sql, the plan of a check). A relative path in it is taken against cwd (the
    current folder when None). It runs nothing, writes nothing, and never raises; it reads the SQL file and the
    connection list, and looks for the transport's tool.

    The dict holds PLAN_KEYS, always with "exit", the code read.py plan would exit with (0, 2, 3 or 4; 1 for an
    error nobody expected), and "error", which says why when that is not 0. A bad command line gives exit 2. For
    "connections" and "rules" the plan is empty with exit 0, and for --help it carries "help": True."""
    try:
        args = [str(a) for a in argv]
    except TypeError:
        return _blank(None, 2, "the command line is not a list of arguments")
    command = args[0] if args and args[0] in ("connections", "rules", "plan", "check", "query") else None
    try:
        opts = _parser().parse_args(args)
    except _Stop as stop:
        if stop.code:
            return _blank(command, 2, stop.message)
        plan = _blank(command)
        plan["help"] = True
        return plan
    try:
        if opts.command in ("connections", "rules"):
            return _blank(opts.command)
        return _plan(opts, os.getcwd() if cwd is None else str(cwd))[0]
    except (Exception, SystemExit) as e:
        return _blank(command, 1, f"unexpected error: {type(e).__name__}: {e}")


def reason(plan):
    """The permission prompt's text for a plan from plan_for_command(), one fact a line. For a plan that runs:

      sql-read: read on "Sales" \u2014 sales-sql.database.windows.net / Product \u2014 PRODUCTION
      up to 200 rows \u00b7 60 s timeout \u00b7 lock timeout 5 s
      the SQL's first 8 lines, each after a | mark, at most 600 characters, then \u2026(n more lines)
      \u26a0 writable login allowed / \u26a0 no row cap / NOLOCK (dirty reads), each when it applies

    A check reads "login check on ..." and shows no SQL, as does a query whose SQL the guard refused. A plan that will
    not run starts "sql-read:" and says why first. Control and invisible characters are shown as escapes, never sent
    to the terminal. Never raises."""
    try:
        return _reason(plan)
    except Exception:
        return "sql-read: this command's plan could not be read; approve only if you have read the SQL"


def _reason(plan):
    command, code, error = plan.get("command"), plan.get("exit"), plan.get("error")
    if plan.get("help"):
        return f"sql-read: prints the usage of read.py{' ' + command if command else ''}; runs nothing"
    if command == "connections":
        return "sql-read: lists the saved connections; touches no database"
    if command == "rules":
        return "sql-read: prints the allow rules for this install; touches no database"
    if code == 2 or command not in ("query", "check"):
        return f"sql-read: the command line is not valid, so nothing will run: {_visible(error or 'no command')}"
    if code == 1:
        return f"sql-read: the plan could not be built ({_visible(error)}); approve only if you have read the SQL"
    lines = [f"sql-read: will not run (exit {code}): {_visible(error)}", _head(plan)] if code else \
        [f"sql-read: {_head(plan)}"]
    lines.append(_limits(plan))
    if command == "query" and isinstance(plan.get("sql"), str):
        lines += _excerpt(plan["sql"])
    lines += _warnings(plan)
    return "\n".join(lines)


def _head(plan):
    verb = "read" if plan.get("command") == "query" else "login check"
    text = f'{verb} on "{_visible(plan.get("connection") or "")}"'
    if plan.get("server"):
        text += f" \u2014 {_where(plan['server'], plan.get('database'))}"
    if plan.get("production"):
        text += " \u2014 PRODUCTION"
    return text


def _limits(plan):
    if plan.get("command") == "query":
        rows = plan.get("max_rows")
        first = "row cap unknown" if rows is None else f"up to {rows} row{'' if rows == 1 else 's'}" if rows else \
            "no row cap"
    else:
        first = "lists what this login can write"
    ms = plan.get("lock_timeout_ms")
    lock = ms / 1000 if isinstance(ms, (int, float)) else None
    return f"{first} \u00b7 {_secs(plan.get('timeout'))} timeout \u00b7 lock timeout {_secs(lock)}"


def _secs(value):
    return f"{value:g} s" if isinstance(value, (int, float)) else "? s"


def _excerpt(sql):
    lines = re.split(r"\r\n|\r|\n", sql)
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    shown, room = [], EXCERPT_CHARS
    for line in lines[:EXCERPT_LINES]:
        if room <= 0:
            break
        text = _visible(line[:room])
        if len(line) > room or len(text) > room:
            shown.append(text[:room] + "\u2026")
            room = 0
        else:
            shown.append(text)
            room -= len(text)
    more = len(lines) - len(shown)
    # A string or a comment can span lines, and hold a line that reads as one of the prompt's own, such as the head
    # for another connection: marked, every line of SQL stands apart from them.
    shown = [f"{EXCERPT_MARK} {text}" if text else EXCERPT_MARK for text in shown]
    if more > 0:
        shown.append(f"\u2026({more} more line{'' if more == 1 else 's'})")
    return shown


def _warnings(plan):
    warnings = []
    if plan.get("allow_writable_login"):
        listed = "listed" if writable_ok(plan.get("connection")) else "NOT listed"
        warnings.append(f"\u26a0 writable login allowed ({listed} in env {WRITABLE_OK})")
    if plan.get("command") == "query" and plan.get("max_rows") == 0:
        warnings.append("\u26a0 no row cap")
    if plan.get("nolock"):
        warnings.append("NOLOCK (dirty reads)")
    return warnings


def _visible(value):
    """value as text a terminal shows and does not act on: a control, format, separator, private-use or unassigned
    character becomes an escape such as \\x1b or \\u202e. A tab stays. A line break is escaped too,
    as \\x0a: a message on stderr stays one line, and a name from the registry cannot start a line that
    reads as read.py's own."""
    return "".join(ch if ch == "\t" or unicodedata.category(ch) not in _HIDDEN else _escape(ch) for ch in str(value))


def _escape(ch):
    code = ord(ch)
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def _where(server, database):
    return _visible(server) + (f" / {_visible(database)}" if database else "")


# --- The command line --------------------------------------------------------------------------------------------

class _Stop(Exception):
    """How the parser stops: code 0 with the help text, or 2 with an error and the usage line."""

    def __init__(self, code, message, usage=""):
        super().__init__(message)
        self.code, self.message, self.usage = code, message, usage


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that raises _Stop rather than print and exit, so plan_for_command can answer for a command
    line that does not parse, and main() prints what it says as UTF-8."""

    def error(self, message):
        raise _Stop(2, f"{self.prog}: error: {message}", self.format_usage())

    def exit(self, status=0, message=None):
        raise _Stop(status, message or "")

    def print_help(self, file=None):
        raise _Stop(0, self.format_help())


def _count(text):
    if not re.fullmatch(r"[0-9]{1,10}", text) or int(text) > MAX_INT:
        raise argparse.ArgumentTypeError(f"must be a whole number from 0 to {MAX_INT:,}, not {text!r}")
    return int(text)


def _seconds(text):
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not 0 < value <= MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(f"must be a number of seconds above 0 and at most {MAX_TIMEOUT}, not {text!r}")
    return int(value) if value.is_integer() else value


def _connection_name(text):
    if not text.strip():
        raise argparse.ArgumentTypeError("must name a saved connection")
    return text


def _file_name(text):
    if not _plain(text):
        raise argparse.ArgumentTypeError(
            f"must be a plain file name without an extension, of letters, digits, '.', '_' and '-', at most 100, "
            f"starting with a letter, digit or '_', not ending with '.', and not a device name such as CON; "
            f"not {text!r}")
    return text


def _parser():
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--via", choices=tuple(TRANSPORTS), default="lprun9",
                        help="the transport that runs the batch (default lprun9)")
    common.add_argument("--connections", metavar="FILE",
                        help="the saved connections (default: env SQL_READ_CONNECTIONS, else LINQPad's own)")
    common.add_argument("--lprun", metavar="PATH",
                        help="lprun9 (default: env LPRUN9, else lprun9 on PATH, else LINQPad 9's install folders)")
    top = _Parser(prog="read.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                  allow_abbrev=False)
    commands = top.add_subparsers(dest="command", metavar="COMMAND", required=True)
    listing = commands.add_parser("connections", parents=[common], allow_abbrev=False,
                                  help="list the saved connections")
    listing.add_argument("--json", action="store_true", help="print them as JSON")
    commands.add_parser("rules", allow_abbrev=False,
                        help="print the allow rules for query --allow-writable-login, with this read.py's path")
    for name, summary in (("plan", "print what query or check would run, as JSON; run nothing"),
                          ("check", "list what the login can write"), ("query", "run one SELECT")):
        command = commands.add_parser(name, parents=[common], allow_abbrev=False, help=summary)
        command.add_argument("--cx", required=True, type=_connection_name, metavar="NAME",
                             help="the connection's display name")
        command.add_argument("--timeout", type=_seconds, default=DEFAULT_TIMEOUT, metavar="S",
                             help=f"seconds before the run is stopped (default {DEFAULT_TIMEOUT})")
        command.add_argument("--out", metavar="DIR", help="where the run's script goes (default: beside --sql; for "
                             "check, a sql-read folder in the temp folder)")
        command.add_argument("--name", type=_file_name, metavar="NAME",
                             help="the script's file name, without extension (default: --sql's; for check, check)")
        if name == "check":
            continue
        command.add_argument("--sql", required=name == "query", metavar="FILE",
                             help="the SQL file" + ("" if name == "query" else "; without it, the plan of a check"))
        command.add_argument("--max-rows", type=_count, default=DEFAULT_MAX_ROWS, metavar="N",
                             help=f"the most rows the SELECT returns (default {DEFAULT_MAX_ROWS}; 0 for no cap)")
        command.add_argument("--format", choices=FORMATS, default=DEFAULT_FORMAT,
                             help="the output format (default csv); rows are counted for csv and csvi")
        command.add_argument("--lock-timeout", type=_count, default=DEFAULT_LOCK_TIMEOUT_MS, metavar="MS",
                             help=f"milliseconds a read waits for a lock (default {DEFAULT_LOCK_TIMEOUT_MS})")
        command.add_argument("--nolock", action="store_true", help="read uncommitted rows: dirty reads")
        command.add_argument("--allow-writable-login", action="store_true",
                             help="leave out the check that stops the run when the login can write")
    return top


def main(argv=None):
    """Runs read.py with argv (sys.argv[1:] when None); returns the exit code."""
    try:
        opts = _parser().parse_args(sys.argv[1:] if argv is None else [str(a) for a in argv])
    except _Stop as stop:
        if stop.code:
            _say(stop.usage + stop.message)
        else:
            _say(stop.message.rstrip("\n"), sys.stdout)
        return stop.code
    try:
        if opts.command == "connections":
            return _list(opts)
        if opts.command == "plan":
            return _show(opts)
        if opts.command == "rules":
            _say(json.dumps({"permissions": {"allow": allow_rules()}}, indent=2, ensure_ascii=True), sys.stdout)
            _say("sql-read: add these to the permissions.allow list of your Claude Code settings; they let query "
                 "--allow-writable-login past auto mode's classifier, and the hook still asks for every run")
            return 0
        return _go(opts)
    except Unusable as e:
        _say(f"sql-read: {_visible(e)}")
        return 3
    except KeyboardInterrupt:
        _say("sql-read: stopped")
        return 130
    except Exception as e:
        _say(f"sql-read: unexpected error: {type(e).__name__}: {_visible(e)}")
        return 1


def _show(opts):
    plan, _ = _plan(opts, os.getcwd())
    _say(json.dumps(plan, indent=2, ensure_ascii=True), sys.stdout)
    if plan["exit"]:
        _say(f"sql-read: {_visible(plan['error'])}")
    elif plan["command"] == "query" and not plan["allow_writable_login"] and writable_ok(plan["connection"]):
        _say(f"sql-read: {_visible(plan['connection'])} is listed in {WRITABLE_OK}: if its login can write, run it as "
             f"{run_prefix()} --cx ... with the plan's other options; the prompt will say the connection is listed, "
             "and the allow rules read.py rules prints match that form")
    return plan["exit"]


def _go(opts):
    """Runs a query or a check: its plan first, which must come back clean, then the batch, once."""
    plan, run = _plan(opts, os.getcwd(), runs=True)
    if plan["exit"]:
        _say(f"sql-read: {_visible(plan['error'])}")
        return plan["exit"]
    if plan["command"] == "check":
        text = check_batch(lock_timeout_ms=plan["lock_timeout_ms"])
    else:
        text = batch(run.sql, max_rows=plan["max_rows"], lock_timeout_ms=plan["lock_timeout_ms"],
                     nolock=plan["nolock"], allow_writable_login=plan["allow_writable_login"])
    who = _who(run.cx)
    started = time.monotonic()
    try:
        result = run.transport.execute(run.cx, text, plan["format"], plan["timeout"], run.out, run.name)
    except OSError as e:
        _say(f"sql-read: {who}: {opts.via} could not run: {_visible(e)}")
        return 1
    elapsed = time.monotonic() - started
    stdout, stderr = _raw(result.stdout), _raw(result.stderr)
    _emit(sys.stdout, stdout)
    if result.timed_out:
        _emit(sys.stderr, stderr)
        _say(f"sql-read: {who}: timed out after {_secs(plan['timeout'])}; {TIMED_OUT}")
        return 6
    if plan["command"] == "query" and not plan["allow_writable_login"] and GUARD_MARK in _text(stderr):
        # The probe's THROW: the tool's own report of it (an error number, a stack) is left out for one clean line.
        _say(f"sql-read: {who}: this login can write to this database, so nothing ran. The login is the boundary: "
             "use one that can only read (read.py check lists what this one can write).")
        return 5
    if result.exit != 0:
        _emit(sys.stderr, stderr)
        _say(f"sql-read: {who}: {opts.via} failed with exit code {result.exit}"
             + ("; its error is above" if stderr.strip() else ", and printed no error"))
        return 1
    _emit(sys.stderr, stderr)
    if plan["command"] == "check":
        return _verdict(who, stdout)
    _say(_summary(who, plan, stdout, elapsed))
    return 0


def _summary(who, plan, stdout, elapsed):
    took, cap = f"{elapsed:.1f} s", plan["max_rows"]
    if plan["format"] not in ("csv", "csvi"):
        return (f"sql-read: {who}: ran in {took}; rows are counted for --format csv and csvi only"
                + (f" (cap {cap})" if cap else ""))
    rows = count_rows(stdout)
    if rows is None:
        return f"sql-read: {who}: ran in {took}; the output could not be read as CSV, so its rows were not counted"
    line = f"sql-read: {who}: {rows} row{'' if rows == 1 else 's'} in {took}"
    return line + f" {CAP_NOTE}" if cap and rows >= cap else line


def _verdict(who, stdout):
    """check's verdict from its rows: 0 for none, read-only; 5 when the login can write. Output that does not start
    with the kind,name header, empty output included, is no verdict: exit 1, never read-only."""
    records = _records(stdout)
    if records is None:
        _say(f"sql-read: {who}: the check's output could not be read as CSV; read it above")
        return 1
    if not records or [f.strip().lower() for f in records[0]] != ["kind", "name"]:
        _say(f"sql-read: {who}: the check printed no kind,name header, so it did not run to its end and says "
             "nothing about what the login can write" + ("" if records else "; it printed nothing"))
        return 1
    found = [" ".join(_visible(f.strip()) for f in r) for r in records[1:] if any(f.strip() for f in r)]
    if found:
        _say(f"sql-read: {who}: this login can write ({', '.join(found)}); query refuses to run with it, so ask for a "
             "login that can only read")
        return 5
    _say(f"sql-read: {who}: read-only: no write role, no database permission and no schema permission to write "
         "(grants on single objects are not checked)")
    return 0


def count_rows(data):
    """The data rows in a tool's CSV output, counted with the csv module: the records after the header, blank lines
    at either end left out (a blank line in the middle is a one-column row holding an empty value). None when the
    output cannot be read as CSV."""
    records = _records(data)
    return None if records is None else max(len(records) - 1, 0)


def _records(data):
    csv.field_size_limit(MAX_INT)
    try:
        records = list(csv.reader(io.StringIO(_text(data), newline="")))
    except csv.Error:
        return None
    while records and not records[-1]:
        records.pop()
    start = 0
    while start < len(records) and not records[start]:
        start += 1
    return records[start:]


def _list(opts):
    registry = load_registry(str(_local(opts.connections, os.getcwd())) if opts.connections else None)
    items = list(registry.connections())
    if opts.json:
        listing = {"source": str(registry.source), "connections": [_fields(c) for c in items]}
        _say(json.dumps(listing, indent=2, ensure_ascii=True), sys.stdout)
        return 0
    if not items:
        _say(f"no saved connections in {_visible(registry.source)}", sys.stdout)
        return 0
    rows = [("name", "auth", "production", "server / database", "user")]
    rows += [(_visible(c.name) if c.name else "(no name)", _visible(c.auth), "yes" if c.production else "no",
              _where(c.server, c.database), _visible(c.user or "")) for c in items]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = [f"connections in {_visible(registry.source)}:"]
    lines += ["  ".join(r[i].ljust(widths[i]) for i in range(4)) + "  " + r[4] for r in rows]
    unnamed = sum(1 for c in items if not c.name)
    if unnamed:
        lines.append(f"{unnamed} connection{'s have' if unnamed > 1 else ' has'} no name, so --cx cannot select "
                     f"{'them' if unnamed > 1 else 'it'}: give {'each' if unnamed > 1 else 'it'} a name in LINQPad.")
    _say("\n".join(line.rstrip() for line in lines), sys.stdout)
    return 0


def _fields(cx):
    """A connection's six fields for --json, named one by one, so a field a registry adds never reaches the output."""
    return {"name": cx.name, "server": cx.server, "database": cx.database, "auth": cx.auth, "user": cx.user,
            "production": bool(cx.production)}


def _who(cx):
    return f"{_visible(cx.name)} ({_where(cx.server, cx.database)}{', production' if cx.production else ''})"


# --- Output ------------------------------------------------------------------------------------------------------

def _raw(value):
    """A tool's output as bytes: what a transport should return, and text it returned instead encoded as UTF-8."""
    if value is None:
        return b""
    return value.encode("utf-8", "surrogateescape") if isinstance(value, str) else bytes(value)


def _text(data):
    """A tool's bytes as text to count CSV records or find a marker in: UTF-8 or UTF-16 when a byte order mark says
    so, else Latin-1, which keeps every ASCII byte (quotes, commas, line ends) in place whatever the code page."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return data[len(bom):].decode(codec, "replace")
    return data.decode("latin-1")


def _emit(stream, data):
    """Writes bytes to a standard stream as they are, below its text layer and whatever encoding that has."""
    if not data:
        return
    stream.flush()
    buffer = getattr(stream, "buffer", None)
    if buffer is None:  # a stream with no bytes below it, such as a StringIO
        stream.write(data.decode("utf-8", "replace"))
        stream.flush()
    else:
        buffer.write(data)
        buffer.flush()


def _say(text, stream=None):
    """Writes one line to stderr, or to stream, as UTF-8. A console whose code page lacks a character (Git Bash gives
    Python cp1252 on Windows) still gets bytes rather than an error, and Claude Code reads them as UTF-8."""
    _emit(stream or sys.stderr, (text + "\n").encode("utf-8", "backslashreplace"))


if __name__ == "__main__":
    sys.exit(main())
