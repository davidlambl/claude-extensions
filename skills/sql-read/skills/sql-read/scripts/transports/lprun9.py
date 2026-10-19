r"""LINQPad 9's command-line runner, lprun9, as sql-read's transport (see backend.py): one T-SQL batch written into a
.linq script and run as

    lprun9 -cxname=<the connection's name> -format=<CSV|CSVi|Text|HTML> <the script's absolute path>

The script is <Query Kind="SQL" />, a blank line, then the batch byte for byte, in UTF-8. It replaces only a script
sql-read wrote, one that begins with that header and the first lines of the prelude read.py puts before every batch.
Anything else where the script goes, a query of the user's above all, is left as it is, and the run is refused.

Only the connection's name reaches lprun9: LINQPad finds the saved connection by it and signs in by itself, with the
password it keeps or the Azure AD token it caches. That is why this transport serves every auth kind, and why sql-read
handles no credential.

lprun9 runs with no shell, no input and both outputs piped, in a process group of its own (on Windows a new process
group, which a Ctrl+C in the console does not reach; elsewhere a new session). At the timeout, or on an interrupt,
the run is stopped with every process it started: lprun9 hosts the query in a child process, so on Windows
taskkill /PID <pid> /T /F, run from System32 by its full path, stops the tree; elsewhere the process group is killed.
Then kill() makes sure of lprun9 itself.

Where lprun9 is found, the first that answers:
  --lprun       a path, made absolute, or a bare program name, looked up on PATH
  env LPRUN9    the same
  PATH          lprun9 (lprun9.exe on Windows), in PATH's absolute folders only, so never the current folder
  C:\Program Files\LINQPad9\LPRun9.exe, then %LocalAppData%\Programs\LINQPad9\LPRun9.exe
A tool that --lprun or LPRUN9 names but that is not there is reported, not looked for elsewhere. On Windows the tool
must be an .exe: a .cmd or .bat runs through cmd.exe, which would read the connection's name and the script's path
as commands of its own.
"""
from __future__ import annotations

import ntpath
import os
import re
import signal
import stat
import subprocess
import tempfile
from pathlib import Path

from backend import AUTH_KINDS, Connection, NotNamed, Result, ToolMissing, Unusable

NAME = "lprun9"
SERVES = AUTH_KINDS  # LINQPad signs in with every kind by itself
FORMATS = {"csv": "CSV", "csvi": "CSVi", "text": "Text", "html": "HTML"}  # sql-read's names, then lprun9's
HEADER = '<Query Kind="SQL" />\n\n'
# How every script sql-read writes begins: HEADER, then the prelude's first lines, which read.py puts before a query's
# batch and a check's alike. Its line ends may have become CRLF, as a checkout with git's core.autocrlf makes them.
OWN = re.compile(rb'<Query Kind="SQL" />\r?\n\r?\nSET NOCOUNT ON;\r?\nSET LOCK_TIMEOUT \d{1,10};\r?\n')
ENV = "LPRUN9"
PROGRAM = "lprun9"
PROGRAM_FILES = r"C:\Program Files\LINQPad9\LPRun9.exe"
LOCAL_PROGRAMS = r"Programs\LINQPad9\LPRun9.exe"  # under %LocalAppData%
WINDOWS = os.name == "nt"
STOP_WAIT = 30  # seconds taskkill gets to stop the tree
DRAIN_WAIT = 10  # seconds to collect what a stopped run wrote
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_HELD_OPEN = (b"(lprun9 was stopped, but a process it started still holds its output open: the output is left out, "
              b"and that process may still be running)\n")


def transport(lprun: str | None = None) -> Lprun9:
    """The lprun9 transport, with --lprun when it was given: a path, made absolute, or a bare program name. It looks
    for nothing until a call needs the tool."""
    return Lprun9(lprun)


def installs() -> list[str]:
    """Where LINQPad 9's installer puts lprun9: for every user, then for this user alone."""
    local = os.environ.get("LOCALAPPDATA")
    return [PROGRAM_FILES, ntpath.join(local, LOCAL_PROGRAMS) if local else "%LocalAppData%\\" + LOCAL_PROGRAMS]


class Lprun9:
    """The lprun9 transport; backend.Transport describes each member."""

    name = NAME
    serves = SERVES

    def __init__(self, lprun: str | None = None):
        self.lprun = lprun

    def locate(self) -> str:
        """lprun9's absolute path, found by looking at files alone; raises ToolMissing naming every place looked."""
        if self.lprun:
            return _named(self.lprun, "--lprun")
        given = _unquoted(os.environ.get(ENV, ""))
        if given:
            return _named(given, f"env {ENV}")
        found = _on_path(PROGRAM)
        if found:
            return found
        places = installs()
        for place in places:
            if os.path.isabs(place) and _runnable(place):
                return os.path.abspath(place)
        raise ToolMissing(f"lprun9 was not found. Looked at --lprun (not given), env {ENV} (not set), {PROGRAM} on "
                          f"PATH, {', '.join(places)}. Install LINQPad 9, which brings lprun9, or point --lprun or "
                          f"env {ENV} at LPRun9.exe.")

    def artifact(self, out: Path, name: str) -> Path:
        """out / f"{name}.linq", with out made absolute. Computes the path; touches nothing."""
        name = str(name)
        if not name or name in (".", "..") or re.search(r"[\\/:\x00]", name):
            raise ValueError(f"the script's name must be a plain file name, not {name!r}")
        return Path(os.path.abspath(out)) / f"{name}.linq"

    def argv(self, cx: Connection, fmt: str, artifact: Path) -> list[str]:
        """[lprun9, -cxname=<name>, -format=<lprun9's name for fmt>, the script's absolute path]: what execute()
        runs. Runs nothing."""
        if not cx.name:
            raise NotNamed("lprun9 selects a connection by its name, and this one has none: give it a name in LINQPad")
        if fmt not in FORMATS:
            raise ValueError(f"the format must be one of {', '.join(FORMATS)}, not {fmt!r}")
        return [self.locate(), f"-cxname={cx.name}", f"-format={FORMATS[fmt]}", os.path.abspath(artifact)]

    def execute(self, cx: Connection, batch: str, fmt: str, timeout: float, out: Path, name: str) -> Result:
        """Writes the script, runs lprun9 on it, and returns what it gave back; stops it at the timeout. Raises
        Unusable, with nothing written or run, when something sql-read did not write stands where the script goes."""
        script = self.artifact(out, name)
        command = self.argv(cx, fmt, script)  # a missing tool is reported before anything is written
        if _foreign(script):
            raise Unusable(f"{script} is already there and is not a script sql-read wrote, so it is left as it is and "
                           "nothing ran: choose another --name or --out for the script")
        _write(script, (HEADER + batch).encode("utf-8"))
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   **_own_group())
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop(process)
            stdout, stderr = _collect(process)
            return Result(_exit(process), stdout, stderr, True, script)
        except BaseException:  # an interrupt above all: lprun9 is in a group of its own, so it would go on running
            _stop(process)
            _reap(process)
            raise
        return Result(process.returncode, stdout, stderr, False, script)


def _named(value: str, origin: str) -> str:
    """The tool --lprun or LPRUN9 names: a path, which must be the tool itself, or a bare program name, looked up on
    PATH. Either way it is not looked for anywhere else."""
    if not re.search(r"[/\\]", value) and not value.startswith("~"):
        found = _on_path(value)
        if found:
            return found
        raise ToolMissing(f"{value}, which {origin} names, is not on PATH: point {origin} at LPRun9.exe by its full "
                          "path")
    path = os.path.abspath(os.path.expanduser(value))
    if not os.path.exists(path):
        elsewhere = f"leave it out to look in env {ENV}, on PATH" if origin == "--lprun" else "unset it to look on PATH"
        raise ToolMissing(f"lprun9 was not found at {path}, which {origin} names: point {origin} at LPRun9.exe, or "
                          f"{elsewhere} and in LINQPad 9's install folders")
    if not os.path.isfile(path):
        raise Unusable(f"{path}, which {origin} names, is not a file: point {origin} at LPRun9.exe")
    if WINDOWS and not path.lower().endswith(".exe"):
        raise Unusable(f"{path}, which {origin} names, is not an .exe: point {origin} at LPRun9.exe itself (a .cmd "
                       "or .bat would read the connection's name and the script's path as commands)")
    if not WINDOWS and not os.access(path, os.X_OK):
        raise Unusable(f"{path}, which {origin} names, is not executable")
    return path


def _on_path(program: str) -> str | None:
    """program in the absolute folders on PATH, the first found; on Windows as an .exe only. A relative PATH entry is
    passed over, so the current folder is never searched."""
    name = program if not WINDOWS or program.lower().endswith(".exe") else program + ".exe"
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip()
        if WINDOWS:
            folder = folder.strip('"')
        if folder and os.path.isabs(folder):
            candidate = os.path.join(folder, name)
            if _runnable(candidate):
                return os.path.abspath(candidate)
    return None


def _runnable(path: str) -> bool:
    """A file there that can run. Every name looked for on Windows ends in .exe already."""
    return os.path.isfile(path) and (WINDOWS or os.access(path, os.X_OK))


def _unquoted(value: str) -> str:
    """An environment variable's value without spaces at either end, or the double quotes around it that a value set
    by hand on Windows can carry."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].strip()
    return value


def _foreign(path: Path) -> bool:
    """True when something sql-read did not write stands at path: anything but a regular file (a folder, or a link,
    which sql-read never makes), or a file that does not begin as its scripts do (OWN), a query of the user's above all.
    Nothing there is not foreign; a folder above path that is missing or is a file is left for the write to report."""
    try:
        status = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    if not stat.S_ISREG(status.st_mode):
        return True
    with open(path, "rb") as f:
        return not OWN.match(f.read(256))


def _write(path: Path, data: bytes) -> None:
    """data into the file at path, whole: written to a new file beside it, then moved over it, so a script left by an
    earlier run is replaced whole. execute() has made sure that nothing else stands at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _own_group() -> dict:
    """Popen's options for a process group of lprun9's own."""
    return {"creationflags": _NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}


def _stop(process: subprocess.Popen) -> None:
    """Stops process and every process it started, then kills process itself in case that missed it."""
    if WINDOWS:
        try:
            subprocess.run([_taskkill(), "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=STOP_WAIT)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, getattr(signal, "SIGKILL", 9))
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _taskkill() -> str:
    """taskkill by its full path in System32: a bare name would be looked for in the current folder first."""
    return ntpath.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")


def _collect(process: subprocess.Popen) -> tuple[bytes, bytes]:
    """What a stopped run wrote. A process outside the stopped tree that still holds the output open would keep it
    from ending, so after DRAIN_WAIT seconds the output is given up on, with a note in its place."""
    try:
        return process.communicate(timeout=DRAIN_WAIT)
    except subprocess.TimeoutExpired:
        return b"", _HELD_OPEN


def _reap(process: subprocess.Popen) -> None:
    try:
        process.wait(timeout=DRAIN_WAIT)
    except subprocess.TimeoutExpired:
        pass


def _exit(process: subprocess.Popen) -> int:
    code = process.poll()
    return -1 if code is None else code
