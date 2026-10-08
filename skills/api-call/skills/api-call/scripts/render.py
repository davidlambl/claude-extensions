"""Draws an api-call record in the terminal window frame and captures it with headless Chrome.

Usage:
  python3 render.py RECORD.json [--out FILE.png] [--title TEXT]

Writes the image and, beside it, the .html page it was captured from; the default name is the record's with .png.
The frame is frame.html next to this script, which tools/screenshots in this repository shares.
"""
import argparse, html, http, json, os, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAME = HERE / "frame.html"
TITLE_BAR, PADDING, ROW = 37, 22, 20  # pixels: what a capture's height is made of
COLORS = {"dim": "color:#a1a1aa", "req": "color:#74bdf5;font-weight:700",
          "ok": "color:#98c379;font-weight:700", "bad": "color:#ef7b85;font-weight:700"}


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


def phrase(code):
    try:
        return http.HTTPStatus(code).phrase
    except ValueError:
        return ""


def clip(line, width=150):
    return line if len(line) <= width else line[:width - 3] + "..."


def rows(r):
    """The lines drawn for a record, each with its color, or None for plain."""
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
    source = str(r.get("source") or "collection").capitalize()
    out = [("dim", f"{source} environment: {r['environment']}    collection: {r['collection']}"),
           ("dim", f"sent {sent:%Y-%m-%d %H:%M:%S} ({offset}) = {r['sent_utc'][11:19]} UTC"),
           (None, ""),
           ("req", f"> {r['method']} {r['url']}")]
    out += [("dim", f"> {k}: {v}") for k, v in (r.get("request_headers") or {}).items()]
    if r.get("client_cert"):
        out.append(("dim", f"> client certificate: {r['client_cert']}"))
    out += [(None, ""), ("ok" if r["status"] < 400 else "bad", f"< HTTP {r['status']} {phrase(r['status'])}    {r['elapsed_ms']} ms")]
    out += [("dim", f"< {k}: {v}") for k, v in (r.get("response_headers") or {}).items()]
    out += [(None, "")] + [(None, line) for line in lines]
    return [(c, clip(t)) for c, t in out]


def title_for(r):
    return f"{r.get('label') or r['environment']} - {r['method']} {r.get('path') or r['url']}"


def page(title, body):
    """The frame with the title (escaped here) and the body's HTML in place."""
    return FRAME.read_text(encoding="utf-8").replace("{{title}}", html.escape(title)).replace("{{body}}", body)


def render(r, png, title=None):
    """Writes png, and its .html page beside it, for the record r. Returns png."""
    png = Path(png)
    drawn = rows(r)
    body = "\n".join(f'<span style="{COLORS[c]}">{html.escape(t)}</span>' if c else html.escape(t) for c, t in drawn)
    page_file = png.with_suffix(".html")
    page_file.write_text(page(title or title_for(r), body), encoding="utf-8")
    width = min(1600, max(720, round(max(len(t) for _, t in drawn) * 9.2) + 60))
    height = TITLE_BAR + PADDING + len(drawn) * ROW
    if png.exists():
        png.unlink()
    subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}",
                    f"--screenshot={png}", page_file.as_uri()], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    if not png.exists():
        sys.exit(f"the browser did not write {png}")
    return png


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("record")
    p.add_argument("--out")
    p.add_argument("--title")
    args = p.parse_args()
    record = Path(args.record)
    r = json.loads(record.read_text(encoding="utf-8"))
    print(render(r, args.out or record.with_suffix(".png"), args.title))


if __name__ == "__main__":
    main()
