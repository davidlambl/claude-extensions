"""Screenshots an API call made the way an Insomnia collection makes it.

Usage:
  python3 shoot.py --env ENV --collection NAME --out DIR --name NAME [options] GET /path

Options:
  --expect CODE            the status the call should return; a mismatch exits 3 (the image is still written)
  --before-env ENV         also run the call in ENV first, for a before-and-after pair
  --before-expect CODE     the status expected in --before-env
  --label TEXT             window title prefix, e.g. "After: Test"; --before-label for the before shot
  --folder NAME            the collection's top-level folder that carries the auth, when it has more than one
  --base-url-var NAME      the environment variable holding the base URL (default baseUrl)
  --warm                   send each call once untimed first, so a cold start does not show in the capture

Writes <name>.png and <name>.json, or <name>-before.* and <name>-after.* with --before-env.
call.ps1 sends the request; nothing secret reaches this script.
"""
import argparse, html, http, json, os, platform, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--env", required=True)
p.add_argument("--collection", required=True)
p.add_argument("--out", required=True)
p.add_argument("--name", required=True)
p.add_argument("--expect", type=int)
p.add_argument("--before-env")
p.add_argument("--before-expect", type=int)
p.add_argument("--label")
p.add_argument("--before-label")
p.add_argument("--folder")
p.add_argument("--base-url-var", default="baseUrl")
p.add_argument("--warm", action="store_true")
p.add_argument("method")
p.add_argument("path")
args = p.parse_args()
if args.method.upper() != "GET":
    sys.exit("only GET is supported")

pwsh = shutil.which("pwsh")
if not pwsh:
    sys.exit("PowerShell 7 (pwsh) is required")


def chrome():
    candidates = [os.environ.get("CHROME"),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for c in filter(None, candidates):
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    sys.exit("no Chrome, Chromium or Edge found; set CHROME to its path")


def call(env):
    argv = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(HERE / "call.ps1"), "-Environment", env,
            "-Collection", args.collection, "-Path", args.path, "-BaseUrlVar", args.base_url_var]
    if args.folder:
        argv += ["-Folder", args.folder]
    # MSYS_NO_PATHCONV keeps Git Bash from rewriting /api/... into a Windows path; NO_COLOR keeps pwsh errors plain.
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=180,
                          env={**os.environ, "MSYS_NO_PATHCONV": "1", "NO_COLOR": "1"})
    if proc.returncode != 0:
        sys.exit(f"call.ps1 failed for {env}:\n{proc.stderr.strip()[-1500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def phrase(code):
    try:
        return http.HTTPStatus(code).phrase
    except ValueError:
        return ""


def clip(line, width=150):
    return line if len(line) <= width else line[:width - 3] + "..."


def render(r, title, png):
    text = r.get("body") or ""
    try:
        pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False) if text.strip() else "(empty body)"
    except ValueError:
        pretty = text.strip() or "(empty body)"
    lines = pretty.splitlines()
    if len(lines) > 40:
        lines = lines[:40] + [f"... ({len(lines) - 40} more lines)"]
    sent = datetime.fromisoformat(r["sent"])
    z = sent.strftime("%z")
    offset = f"UTC{z[:3]}:{z[3:]}" if z else "local time"
    rows = [("dim", f"Insomnia environment: {r['environment']}    collection: {r['collection']}"),
            ("dim", f"sent {sent:%Y-%m-%d %H:%M:%S} ({offset}) = {r['sent_utc'][11:19]} UTC"),
            (None, ""),
            ("req", f"> GET {r['url']}")]
    rows += [("dim", f"> {k}: {v}") for k, v in (r.get("request_headers") or {}).items()]
    if r.get("client_cert"):
        rows.append(("dim", f"> client certificate: {r['client_cert']}"))
    rows += [(None, ""), ("ok" if r["status"] < 400 else "bad", f"< HTTP {r['status']} {phrase(r['status'])}    {r['elapsed_ms']} ms")]
    rows += [("dim", f"< {k}: {v}") for k, v in (r.get("response_headers") or {}).items()]
    rows += [(None, "")] + [(None, line) for line in lines]
    rows = [(c, clip(t)) for c, t in rows]

    colors = {"dim": "color:#a1a1aa", "req": "color:#74bdf5;font-weight:700",
              "ok": "color:#98c379;font-weight:700", "bad": "color:#ef7b85;font-weight:700"}
    body = "\n".join(f'<span style="{colors[c]}">{html.escape(t)}</span>' if c else html.escape(t) for c, t in rows)
    # The window frame from tools/screenshots/render.mjs, with Windows monospace fonts added.
    page = f"""<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;background:#1c1c1f}}
.bar{{height:36px;display:flex;align-items:center;position:relative;background:#2a2a2e;border-bottom:1px solid #111}}
.dots{{position:absolute;left:14px;display:flex;gap:8px}}.dots i{{width:12px;height:12px;border-radius:50%;display:block}}
.title{{width:100%;text-align:center;color:#a1a1aa;font:13px "SF Mono",Menlo,"Cascadia Mono",Consolas,monospace}}
pre{{margin:0;padding:10px 18px 12px;color:#e4e4e7;font:15px/20px "SF Mono",Menlo,"Cascadia Mono",Consolas,monospace;white-space:pre}}
</style><div class="bar"><div class="dots"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div><div class="title">{html.escape(title)}</div></div><pre>{body}</pre>"""
    page_file = png.with_suffix(".html")
    page_file.write_text(page, encoding="utf-8")
    width = min(1600, max(720, round(max(len(t) for _, t in rows) * 9.2) + 60))
    height = 37 + 22 + len(rows) * 20
    if png.exists():
        png.unlink()
    subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}",
                    f"--screenshot={png}", page_file.as_uri()], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    if not png.exists():
        sys.exit(f"the browser did not write {png}")


out = Path(args.out).resolve()
out.mkdir(parents=True, exist_ok=True)
shots = []
if args.before_env:
    shots.append((args.before_env, args.before_label or f"Before: {args.before_env}", args.before_expect, f"{args.name}-before"))
    shots.append((args.env, args.label or f"After: {args.env}", args.expect, f"{args.name}-after"))
else:
    shots.append((args.env, args.label or args.env, args.expect, args.name))

failed = False
for env, label, expect, stem in shots:
    if args.warm:
        call(env)
    r = call(env)
    png = out / f"{stem}.png"
    render(r, f"{label} - GET {args.path}", png)
    verdict = None if expect is None else ("PASS" if r["status"] == expect else "FAIL")
    failed |= verdict == "FAIL"
    r.update({"label": label, "expected": expect, "verdict": verdict, "png": str(png)})
    (out / f"{stem}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"shot": stem, "env": env, "status": r["status"], "expected": expect, "verdict": verdict,
                      "elapsed_ms": r["elapsed_ms"], "png": str(png)}))
sys.exit(3 if failed else 0)
