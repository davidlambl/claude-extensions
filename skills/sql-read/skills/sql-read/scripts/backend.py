"""The types sql-read's generic layer (read.py) and its backends share: a connection record, a run's result, the two
interfaces a backend implements, and the errors that mean a run cannot go ahead.

A registry lists the saved connections and resolves one by name; registries/linqpad.py reads LINQPad's
ConnectionsV2.xml, and it is the registry for every transport. A transport runs one T-SQL batch against a connection;
transports/lprun9.py runs it through LINQPad 9's lprun9, which signs in by itself. No backend ever hands sql-read a
password or a token: a Connection has no field for one.

read.py imports a backend module only when a command needs it, so the guard and the plan of a refused SQL never load
one. Each module exposes one factory, which read.py calls with its own command-line options:

  registries/linqpad.py   def registry(path: str | None = None) -> Registry
                          path is --connections made absolute, or None for the default: env SQL_READ_CONNECTIONS,
                          else LINQPad's own list, as registries.linqpad.default_path finds it (%APPDATA%\\LINQPad
                          where APPDATA is set, else ~/Library/Application Support/LINQPad on macOS)
  transports/lprun9.py    def transport(lprun: str | None = None) -> Transport
                          lprun is --lprun (absolute when it names a folder, as given when it is a bare program name),
                          or None for the default: env LPRUN9, then lprun9 on PATH, then LINQPad 9's install folders

registries/ and transports/ are packages, each with an __init__.py. A factory only records its arguments: reading a
file or looking for a tool waits for the first call that needs it. Anything that means the connection, the tool or
the pairing cannot be used is raised as Unusable or one of its subclasses; read.py prints the message after
"sql-read: " and exits 3, so a message names what to fix and holds no secret. Every other exception is a bug, and
read.py reports it as one (exit 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# How a connection signs in. "sql" is a SQL Server login with a saved password; "windows" is integrated security;
# "aad" is Azure AD (Microsoft Entra ID). A transport serves the kinds it can connect with while sql-read handles no
# credential; read.py refuses any other pairing (exit 3).
AUTH_KINDS = frozenset({"sql", "windows", "aad"})


@dataclass(frozen=True)
class Connection:
    """One saved connection, as a registry lists it. Every field is configuration, shown in the listing, the plan and
    the permission prompt and never refused. There is no field for a password or a token, and a registry puts none in
    any field.

    name        the display name a run selects it by (LINQPad's DisplayName), or None when it has none. An unnamed
                connection is listed but cannot be selected: lprun9's -cxname needs a name to find it by.
    server      the server as saved, such as "sales-sql.database.windows.net"
    database    the database, or None when the connection names none (the login's default database is used)
    auth        how it signs in: one of AUTH_KINDS
    user        the login or account name, when the connection saves one
    production  True when the connection is flagged as production
    """
    name: str | None
    server: str
    database: str | None
    auth: str
    user: str | None
    production: bool


@dataclass(frozen=True)
class Result:
    """What a transport's execute() returns for one run.

    exit       the tool's exit code; after a timeout, whatever the stopped process returned, or -1
    stdout     everything the tool wrote to standard output, as bytes, unchanged: read.py passes them through as is,
               and counts CSV rows in them
    stderr     everything the tool wrote to standard error, as bytes, unchanged
    timed_out  True when the run was stopped because it reached its timeout
    artifact   the file execute() wrote to run the batch (lprun9's .linq script), or None if it wrote none
    """
    exit: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    artifact: Path | None = None


class Unusable(Exception):
    """The connection, the transport's tool or their pairing cannot be used, so nothing runs: read.py exits 3 with
    this message. The message says what to fix, and holds no secret."""


class NotFound(Unusable):
    """No connection answers to the name given (the message lists the names there are), more than one does, or the
    registry's own file is missing or unreadable (the message names the path, and --connections and
    SQL_READ_CONNECTIONS as the ways to point at another)."""


class NotNamed(Unusable):
    """The name given matches only a connection that has no display name, such as by its server. A run selects a
    connection by its display name alone, so the message says to give it one in LINQPad."""


class ToolMissing(Unusable):
    """The transport's tool cannot be found. The message names every place that was looked at, and how to point at
    the tool (for lprun9: --lprun, or env LPRUN9)."""


@runtime_checkable
class Registry(Protocol):
    """A list of saved connections. registries/linqpad.py reads LINQPad's ConnectionsV2.xml; its Password elements
    are never read out, printed, logged or written anywhere."""

    source: str
    """Where the list comes from, for messages and the connections listing: the file's absolute path."""

    def connections(self) -> list[Connection]:
        """Every saved connection, named or not, in the registry's own order. Raises NotFound (an Unusable) when the
        list itself cannot be read."""
        ...

    def resolve(self, name: str) -> Connection:
        """The one connection whose display name equals name, both trimmed and compared case-insensitively. Raises
        NotFound when none does, or more than one; NotNamed when name matches only an unnamed connection; NotFound
        when the list cannot be read."""
        ...


@runtime_checkable
class Transport(Protocol):
    """A way to run one T-SQL batch against a saved connection, with no credential passing through sql-read.

    Timeout is the transport's concern: server-side where its tool has one, otherwise a kill of the process and of
    every process it started. A transport never prints: whatever its tool wrote comes back in the Result."""

    name: str
    """The transport's name, which is also its --via value and the plan's "transport": "lprun9"."""

    serves: frozenset[str]
    """The auth kinds (AUTH_KINDS) it connects with while sql-read handles no credential. lprun9 serves all three,
    since LINQPad signs in by itself. read.py refuses a connection of any other kind before anything runs (exit 3)."""

    def locate(self) -> str:
        """The tool's absolute path, found without running it. Raises ToolMissing, whose message names every place it
        looked and how to point at the tool."""
        ...

    def artifact(self, out: Path, name: str) -> Path:
        """The file execute(..., out, name) writes: for lprun9, out / f"{name}.linq". Computes the path; touches
        nothing. out is absolute; name is a plain file name without an extension (letters, digits, '.', '_', '-')."""
        ...

    def argv(self, cx: Connection, fmt: str, artifact: Path) -> list[str]:
        """The command execute() runs for this connection, output format and artifact: the tool's path from locate()
        first, every path absolute. For lprun9: [tool, f"-cxname={cx.name}", f"-format={fmt}", str(artifact)]. Runs
        nothing; raises ToolMissing as locate() does. The plan shows it, so it is exactly what execute() runs."""
        ...

    def execute(self, cx: Connection, batch: str, fmt: str, timeout: float, out: Path, name: str) -> Result:
        """Runs one T-SQL batch against cx and returns what came back.

        Writes artifact(out, name), creating out if it is missing, as UTF-8 holding the batch byte for byte (open
        it with newline="", so a CRLF in the user's SQL is not doubled), then runs argv(cx, fmt, that file) with no
        shell. Only the connection's name reaches the tool; the tool supplies the credential itself. fmt is one of
        csv, csvi, text and html, which the transport maps to its tool's own names. timeout is in seconds; at the
        timeout the run is stopped, child processes included, and the Result has timed_out=True; an interrupt
        (KeyboardInterrupt) stops it the same way before it goes on up. Raises ToolMissing when the tool cannot be
        found, and OSError when the file cannot be written or the tool cannot be started."""
        ...
