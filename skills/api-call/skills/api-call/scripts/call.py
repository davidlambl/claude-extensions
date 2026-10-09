"""Calls an API the way a collection tool would, records the exchange, and screenshots it on request.

Usage:
  python3 call.py --via insomnia|postman --env ENV --collection COLLECTION --out DIR --name NAME [options] GET /path

Options:
  --expect CODE        the status the call should return; a mismatch exits 3 (the record and image are still written)
  --warm               send the call once untimed first, so a cold start does not show
  --label TEXT         the window title's lead, e.g. "After: Test"; the environment's name otherwise
  --shot               also draw the exchange and capture it as NAME.png
  --folder NAME        the collection's top-level folder that carries the auth, when it has more than one
  --base-url-var NAME  the variable holding the base URL (default baseUrl)
  --timeout SECONDS    how long to wait for the response (default 60)
  Postman only:
  --vars FILE.json     values laid over the environment, for secrets kept out of the export
  --cert PEM --key PEM a client certificate; API_CALL_KEY_PASSPHRASE unlocks the key

--env and --collection name an Insomnia global environment ("Name", or "Name/Sub-environment") and collection,
or are the paths of a Postman environment export and collection export.

Writes DIR/NAME.json, the record of the exchange, and with --shot DIR/NAME.png with its .html page. Prints one
line of JSON with the result. The source script sends the request; nothing secret reaches this script.

In Git Bash on Windows, run it with MSYS_NO_PATHCONV=1 set, or write the path with a doubled leading slash
(GET //api/widgets/42, read as /api/widgets/42): the shell otherwise rewrites /api/widgets/42 into a Windows path
before this script runs, and the script stops rather than send that.
"""
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render  # noqa: E402

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--via", required=True, choices=["insomnia", "postman"])
p.add_argument("--env", required=True)
p.add_argument("--collection", required=True)
p.add_argument("--out", required=True)
p.add_argument("--name", required=True)
p.add_argument("--expect", type=int)
p.add_argument("--warm", action="store_true")
p.add_argument("--label")
p.add_argument("--shot", action="store_true")
p.add_argument("--folder")
p.add_argument("--base-url-var", default="baseUrl")
p.add_argument("--timeout", type=int, default=60)
p.add_argument("--vars")
p.add_argument("--cert")
p.add_argument("--key")
p.add_argument("method")
p.add_argument("path")
args = p.parse_args()
if args.method.upper() != "GET":
    sys.exit("only GET is supported")
if re.match(r"[A-Za-z]:[\\/]", args.path):
    # Git Bash (MSYS) rewrites an argument that starts with / into a Windows path under its own folder as it launches
    # python, so the path arrives here as C:/Program Files/Git/api/..., which an API would answer with a 404 that
    # a step expecting one would take for a pass. Setting MSYS_NO_PATHCONV here would be too late: that hop is done.
    sys.exit(f"the path {args.path!r} is a Windows path, which is what Git Bash makes of /api/... before this script "
             "runs. Run the command with MSYS_NO_PATHCONV=1 set in the shell, or write the path with a doubled "
             "leading slash (GET //api/widgets/42).")
if args.path.startswith("//"):
    # Git Bash passes //api/widgets/42 through as typed, taking it for a network path, so the doubled slash the
    # message above suggests arrives here; the collection's base URL takes the path with one.
    args.path = "/" + args.path.lstrip("/")
if args.via == "insomnia" and (args.vars or args.cert or args.key):
    sys.exit("--vars, --cert and --key are for --via postman")
if bool(args.cert) != bool(args.key):
    sys.exit("--cert and --key go together")


def source_argv():
    if args.via == "insomnia":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            sys.exit("PowerShell 7 (pwsh) is required for --via insomnia")
        argv = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(HERE / "sources" / "insomnia.ps1"),
                "-Environment", args.env, "-Collection", args.collection, "-Path", args.path,
                "-BaseUrlVar", args.base_url_var, "-TimeoutSec", str(args.timeout)]
        if args.folder:
            argv += ["-Folder", args.folder]
        return argv
    argv = [sys.executable, str(HERE / "sources" / "postman.py"), "--env", args.env, "--collection", args.collection,
            "--path", args.path, "--base-url-var", args.base_url_var, "--timeout", str(args.timeout)]
    if args.folder:
        argv += ["--folder", args.folder]
    if args.vars:
        argv += ["--vars", args.vars]
    if args.cert:
        argv += ["--cert", args.cert, "--key", args.key]
    return argv


def last_line(text):
    """The last non-empty line of a source's output, split on newlines alone. str.splitlines would also break on
    U+0085, U+2028 and U+2029, which a body can carry raw inside the one line of JSON the source prints. A byte order
    mark, which a console set to UTF-8 can put first, goes too."""
    lines = [line.strip(" \t\r\ufeff") for line in text.split("\n")]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ""


def call():
    # NO_COLOR keeps a source's error text plain. PYTHONIOENCODING has a Python source write the UTF-8 read here, where
    # Windows would give a pipe its ANSI code page.
    proc = subprocess.run(source_argv(), capture_output=True, text=True, encoding="utf-8", timeout=args.timeout + 120,
                          env={**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"})
    if proc.returncode != 0:
        sys.exit(f"the {args.via} source failed:\n{(proc.stderr or proc.stdout).strip()[-1500:]}")
    line = last_line(proc.stdout)
    try:
        r = json.loads(line)
    except ValueError:
        r = None
    if not isinstance(r, dict) or "status" not in r:
        sys.exit(f"the {args.via} source did not print a record: {line[:200]}")
    return r


def write_record(r, record):
    # ASCII-escaped, so an invisible or bidi character a response carried cannot hide in the file
    record.write_text(json.dumps(r, indent=2, ensure_ascii=True), encoding="utf-8")


def shoot(r, record):
    """Draws the record and captures it as NAME.png. The record is on disk already, so whatever stops the drawing (no
    browser, one that fails, a record it cannot draw) loses nothing: the script stops with exit 1 and names the record."""
    try:
        return render.render(r, out / f"{args.name}.png")
    except Exception as e:
        why = str(e) if isinstance(e, render.Problem) else f"{type(e).__name__}: {e}"
        outcome = f"HTTP {r['status']}" + (f" ({r['verdict']} against --expect {r['expected']})" if r["verdict"] else "")
        sys.exit(f"the call returned {outcome} and its record is {record}, but the screenshot failed: {why}")


out = Path(args.out).resolve()
out.mkdir(parents=True, exist_ok=True)
if args.warm:
    call()
r = call()
verdict = None if args.expect is None else ("PASS" if r["status"] == args.expect else "FAIL")
r.update({"label": args.label or r["environment"], "expected": args.expect, "verdict": verdict})
record = out / f"{args.name}.json"
write_record(r, record)  # before the screenshot, so a browser that fails never loses the record of a call that was sent
png = None
if args.shot:
    png = shoot(r, record)
    r["png"] = str(png)
    write_record(r, record)
print(json.dumps({"name": args.name, "via": args.via, "environment": r["environment"], "status": r["status"],
                  "expected": args.expect, "verdict": verdict, "elapsed_ms": r["elapsed_ms"],
                  "record": str(record), "png": str(png) if png else None}))
sys.exit(3 if verdict == "FAIL" else 0)
