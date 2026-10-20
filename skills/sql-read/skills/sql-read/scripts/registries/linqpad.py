r"""LINQPad's saved connections as sql-read's connection registry: %APPDATA%\LINQPad\ConnectionsV2.xml read into
Connection records (see backend.py).

LINQPad 9 keeps the list as a <Connections> root holding one <Connection> per saved connection, each a flat run of
settings with no attributes: ID, Server, SqlSecurity, DisplayName, Database, UniversalAuthentication, UserName,
Password, IsProduction and more. A connection is read from seven of its own settings, and from nothing else:

  DisplayName              its name, kept exactly as saved, since lprun9's -cxname gets it; absent or blank: no name
  Server, Database         where it connects; no Database means the login's default database
  UniversalAuthentication  true: it signs in with Azure AD, universal with MFA ("aad")
  SqlSecurity              true: it signs in as a SQL Server login with a saved password ("sql"); with neither true,
                           it signs in as the Windows user ("windows"). UniversalAuthentication wins over SqlSecurity,
                           since a universal sign-in carries a user name as a login does.
  UserName                 the login or account, when one is saved
  IsProduction             true: the connection is flagged as production

The parser hands this module the text of those settings alone, and only where they are direct children of a
<Connection> directly under the root; all other text is dropped as it arrives. So the Password (LINQPad's encrypted
copy), a custom connection string, and whatever a driver keeps in DriverData, even under a setting's name, reach no
Connection, no message, no listing and no --json. A file that is not a plain file, is far too large, does not parse,
declares a DOCTYPE or is not a <Connections> list is refused, and the message names its path, never its content.

A run selects a connection by its display name, trimmed and compared ignoring case. A connection with no name is
listed, but --cx cannot select it: lprun9 finds a connection by its name, so it has to be given one in LINQPad. For
the same reason a display name that is also how LINQPad shows a connection with no name (its server, or
server.database) is refused: lprun9 could take either.
"""
from __future__ import annotations

import os
import re
import stat
import xml.etree.ElementTree as ET
from xml.parsers import expat

from backend import Connection, NotFound, NotNamed

ENV = "SQL_READ_CONNECTIONS"
SETTINGS = frozenset({"DisplayName", "Server", "Database", "UniversalAuthentication", "SqlSecurity", "UserName",
                      "IsProduction"})
MAX_BYTES = 16 * 1024 * 1024  # far past any real list: LINQPad saves about 1 KB a connection
MAX_NAMES = 30  # the most names a message lists
UNNAMED = "(no name)"  # what read.py connections shows for a connection with no name
_POINT = f"Point --connections or env {ENV} at LINQPad's ConnectionsV2.xml."


def registry(path: str | None = None) -> LinqPadRegistry:
    r"""The registry for the list at path (--connections, made absolute), or, when path is None, at env
    SQL_READ_CONNECTIONS, else at %APPDATA%\LINQPad\ConnectionsV2.xml. It reads nothing until asked."""
    return LinqPadRegistry(path)


def default_path() -> str:
    r"""LINQPad's own list, %APPDATA%\LINQPad\ConnectionsV2.xml; ~\AppData\Roaming stands in for an unset APPDATA."""
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.abspath(os.path.join(appdata, "LINQPad", "ConnectionsV2.xml"))


class LinqPadRegistry:
    """LINQPad's connection list at source, read once, on the first call that needs it."""

    def __init__(self, path: str | None = None):
        given = _unquoted(os.environ.get(ENV, ""))
        if path:
            self.source, self._origin = _absolute(path), "--connections"
        elif given:
            self.source, self._origin = _absolute(given), f"env {ENV}"
        else:
            self.source, self._origin = default_path(), None
        self._items: tuple[Connection, ...] | None = None

    def connections(self) -> list[Connection]:
        """Every saved connection, named or not, in the list's order. Raises NotFound when the list cannot be read."""
        if self._items is None:
            self._items = tuple(_connection(found) for found in _parse(self.source, self._origin))
        return list(self._items)

    def resolve(self, name: str) -> Connection:
        """The one connection whose display name is name, both trimmed and compared ignoring case. Raises NotFound when
        none is, or more than one, or when the name is also how LINQPad shows a connection with no name (lprun9 could
        take either), or the list cannot be read; NotNamed when name matches only connections with no name, by their
        server or by "(no name)"."""
        items = self.connections()
        key, typed = _key(name), str(name).strip()
        if not key:
            raise NotFound("no connection name was given: pass --cx a name that read.py connections lists")
        named = [cx for cx in items if cx.name is not None and _key(cx.name) == key]
        if len(named) == 1:
            # lprun9's -cxname finds a connection by the name LINQPad shows for it, which for one with no name is its
            # server, or server.database: a display name that is also that could be taken for the other connection.
            shown = [cx for cx in items if cx.name is None and key in _shown(cx)]
            if shown:
                which = "; ".join(_describe(cx) for cx in shown[:MAX_NAMES])
                raise NotFound(f'"{typed}" names "{named[0].name}" ({_describe(named[0])}), but LINQPad shows a '
                               f"connection with no name ({which}) by the same name, so lprun9 could take either: give "
                               f"{'that one' if len(shown) == 1 else 'each of those'} a name in LINQPad")
            return named[0]
        if named:
            raise NotFound(f'{len(named)} connections in {self.source} are named "{typed}", ignoring case and spaces '
                           "at either end, so --cx cannot tell them apart: give each a different name in LINQPad")
        names = [cx.name for cx in items if cx.name is not None]
        unnamed = [cx for cx in items if cx.name is None and key in _labels(cx)]
        if unnamed:
            one = len(unnamed) == 1
            which = "; ".join(_describe(cx) for cx in unnamed[:MAX_NAMES])
            message = (f'"{typed}" matches only {"a connection" if one else f"{len(unnamed)} connections"} with no '
                       f"name ({which}). --cx selects a connection by its name, as lprun9 does, so give "
                       f"{'it' if one else 'each'} a name in LINQPad, then pass that name to --cx")
            raise NotNamed(message + (f". The named ones are {_names(names)}" if names else ""))
        if not items:
            raise NotFound(f'no connection is named "{typed}": there are no saved connections in {self.source}; save '
                           "one in LINQPad first")
        if not names:
            raise NotFound(f'no connection is named "{typed}": {self.source} holds {len(items)} saved '
                           f"connection{'' if len(items) == 1 else 's'}, but none of them has a name; give the one to "
                           "read from a name in LINQPad")
        message = f'no connection in {self.source} is named "{typed}"; the named ones are {_names(names)}'
        rest = len(items) - len(names)
        if rest:
            message += (f" ({rest} more {'has' if rest == 1 else 'have'} no name, which --cx cannot select until "
                        f"{'it is' if rest == 1 else 'they are'} given one in LINQPad)")
        raise NotFound(message)


def _absolute(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def _key(name: str) -> str:
    return str(name).strip().casefold()


def _true(value: str | None) -> bool:
    """An XML boolean as LINQPad saves one: true or 1, ignoring case and spaces at either end."""
    return value is not None and value.strip().lower() in ("true", "1")


def _connection(found: dict[str, str]) -> Connection:
    """A Connection from the settings read for one <Connection>."""
    name = found.get("DisplayName")
    if _true(found.get("UniversalAuthentication")):
        auth = "aad"
    elif _true(found.get("SqlSecurity")):
        auth = "sql"
    else:
        auth = "windows"
    return Connection(name=name if name and name.strip() else None, server=(found.get("Server") or "").strip(),
                      database=(found.get("Database") or "").strip() or None, auth=auth,
                      user=(found.get("UserName") or "").strip() or None,
                      production=_true(found.get("IsProduction")))


def _shown(cx: Connection) -> set[str]:
    """The names LINQPad shows a connection with no name by, compared as resolve() compares: its server, and
    server.database when it names a database."""
    server = cx.server.strip()
    return {_key(label) for label in (server, f"{server}.{cx.database}" if cx.database else "") if label.strip()}


def _labels(cx: Connection) -> set[str]:
    """What a connection with no name answers to, for a message that says it needs one: the names LINQPad shows it
    by, the server's host part, and "(no name)", as read.py connections shows it."""
    host = re.split(r"[.\\,]", re.sub(r"(?i)\A(?:tcp|np|lpc|admin):", "", cx.server.strip()), maxsplit=1)[0]
    return _shown(cx) | {_key(label) for label in (host, UNNAMED) if label.strip()}


def _unquoted(value: str) -> str:
    """An environment variable's value without spaces at either end, or the double quotes around it that a value set
    by hand on Windows can carry."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].strip()
    return value


def _describe(cx: Connection) -> str:
    where = cx.server + (f" / {cx.database}" if cx.database else "")
    return f"{where}, {cx.auth}" + (f", user {cx.user}" if cx.user else "")


def _names(names: list[str]) -> str:
    shown = ", ".join(f'"{name}"' for name in names[:MAX_NAMES])
    rest = len(names) - MAX_NAMES
    return shown + (f" and {rest} more (read.py connections lists them all)" if rest > 0 else "")


# --- Reading the file ----------------------------------------------------------------------------------------------

class _Refused(Exception):
    """The file parses, but this module will not read it."""


class _Reader:
    """An XMLParser target that keeps only what a Connection is made from: the text of the SETTINGS that are direct
    children of a <Connection> directly under the root, the first of each. All other text is dropped unread."""

    def __init__(self):
        self.depth = 0
        self.root: str | None = None
        self.records: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.setting: str | None = None
        self.text: list[str] = []

    def start(self, tag, attrib):
        self.depth += 1
        if self.depth == 1:
            self.root = tag
        elif self.depth == 2 and tag == "Connection" and self.root == "Connections":
            self.current = {}
        elif self.depth == 3 and self.current is not None and tag in SETTINGS and tag not in self.current:
            self.setting, self.text = tag, []

    def data(self, text):
        if self.setting is not None and self.depth == 3:
            self.text.append(text)

    def end(self, tag):
        if self.depth == 3 and self.setting is not None:
            self.current[self.setting] = "".join(self.text)
            self.setting, self.text = None, []
        elif self.depth == 2 and self.current is not None:
            self.records.append(self.current)
            self.current = None
        self.depth -= 1

    def doctype(self, name, pubid, system):
        # LINQPad never writes one, and a DOCTYPE is what entity expansion needs: stop before its declarations.
        raise _Refused("declares a DOCTYPE, which LINQPad's list never has")

    def close(self):
        return self.records


def _parse(path: str, origin: str | None) -> list[dict[str, str]]:
    """The settings read for each <Connection> in the list at path, in the file's order."""
    data = _read(path, origin)
    reader = _Reader()
    parser = ET.XMLParser(target=reader)
    try:
        parser.feed(data)
        parser.close()
    except ET.ParseError as e:
        # The expat error's own words and its place, never the text around it: that could be a password.
        why = expat.ErrorString(e.code) if isinstance(e.code, int) else "it is not well-formed"
        line, column = e.position
        raise NotFound(f"{_named(path, origin)} could not be read as XML ({why}, at line {line}, column {column}). "
                       f"{_POINT}") from None
    except _Refused as e:
        raise NotFound(f"{_named(path, origin)} {e}, so it is not read. {_POINT}") from None
    except LookupError:
        # The declaration names an encoding Python has no codec for; its name is the file's text, so it is not quoted.
        raise NotFound(f"{_named(path, origin)} declares an encoding Python does not know, so it is not read. "
                       f"{_POINT}") from None
    if reader.root != "Connections":
        raise NotFound(f"{_named(path, origin)} is not LINQPad's connection list: its root element is not "
                       f"<Connections>. {_POINT}")
    return reader.records


def _read(path: str, origin: str | None) -> bytes:
    """The file's bytes. A folder, a device or a pipe is refused unread, since reading one could wait for ever."""
    try:
        status = os.stat(path)
        if not stat.S_ISREG(status.st_mode):
            raise NotFound(f"{_named(path, origin)} is not a file. {_POINT}")
        if status.st_size > MAX_BYTES:
            raise NotFound(_too_large(path, origin, status.st_size))
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except FileNotFoundError:
        raise NotFound(_missing(path, origin)) from None
    except OSError as e:
        raise NotFound(f"{_named(path, origin)} cannot be read: {e.strerror or type(e).__name__}. {_POINT}") from None
    if len(data) > MAX_BYTES:
        raise NotFound(_too_large(path, origin, len(data)))
    return data


def _named(path: str, origin: str | None) -> str:
    return f"the connection list at {path}" + (f" (from {origin})" if origin else "")


def _missing(path: str, origin: str | None) -> str:
    if origin is None:
        return (f"LINQPad's connection list was not found at {path}: save a connection in LINQPad 9 first, or point "
                f"--connections or env {ENV} at the list")
    return f"the connection list {origin} names, {path}, does not exist. {_POINT}"


def _too_large(path: str, origin: str | None, size: int) -> str:
    return (f"{_named(path, origin)} is too large to be a connection list ({size:,} bytes; the limit is "
            f"{MAX_BYTES:,}). {_POINT}")
