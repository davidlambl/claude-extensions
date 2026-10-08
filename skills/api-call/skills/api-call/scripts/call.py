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
"""
import argparse, json, os, shutil, subprocess, sys
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


def call():
    # MSYS_NO_PATHCONV keeps Git Bash from rewriting /api/... into a Windows path; NO_COLOR keeps errors plain.
    proc = subprocess.run(source_argv(), capture_output=True, text=True, encoding="utf-8", timeout=args.timeout + 120,
                          env={**os.environ, "MSYS_NO_PATHCONV": "1", "NO_COLOR": "1"})
    if proc.returncode != 0:
        sys.exit(f"the {args.via} source failed:\n{(proc.stderr or proc.stdout).strip()[-1500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


out = Path(args.out).resolve()
out.mkdir(parents=True, exist_ok=True)
if args.warm:
    call()
r = call()
verdict = None if args.expect is None else ("PASS" if r["status"] == args.expect else "FAIL")
r.update({"label": args.label or r["environment"], "expected": args.expect, "verdict": verdict})
png = render.render(r, out / f"{args.name}.png") if args.shot else None
if png:
    r["png"] = str(png)
record = out / f"{args.name}.json"
record.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"name": args.name, "via": args.via, "environment": r["environment"], "status": r["status"],
                  "expected": args.expect, "verdict": verdict, "elapsed_ms": r["elapsed_ms"],
                  "record": str(record), "png": str(png) if png else None}))
sys.exit(3 if verdict == "FAIL" else 0)
