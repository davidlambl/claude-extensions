"""Draws an api-call record in the terminal window frame and captures it with headless Chrome.

Usage:
  python3 render.py RECORD.json [--out FILE.png] [--title TEXT]

Writes the image and, beside it, the .html page it was captured from; the default name is the record's with .png.
The frame is frame.html next to this script, which tools/screenshots in this repository shares.
"""
import argparse, html, http, json, os, re, shutil, subprocess, sys, unicodedata
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRAME = HERE / "frame.html"
TITLE_BAR, PADDING, ROW = 37, 22, 20  # pixels: what a capture's height is made of
CELL, MARGIN, MIN_WIDTH, MAX_WIDTH = 9.2, 60, 720, 1600  # pixels: a monospace cell, and what a capture's width is made of
WIDTH, LINES = 150, 40  # cells a row is cut to, and lines a body is cut to
COLORS = {"dim": "color:#a1a1aa", "req": "color:#74bdf5;font-weight:700",
          "ok": "color:#98c379;font-weight:700", "bad": "color:#ef7b85;font-weight:700"}

# A JSON text's tokens as RFC 8259 writes them, and the whitespace it allows between them.
SPACE = re.compile(r"[ \t\n\r]*")
STRING = re.compile(r'"[^"\\\x00-\x1f]*(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})[^"\\\x00-\x1f]*)*"')
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
LITERAL = re.compile(r"true|false|null")

# Every character but a tab and printable ASCII: the only ones hidden() has to look at.
UNUSUAL = re.compile(r"[^\t\x20-\x7e]")
# The default-ignorable code points (Unicode's DerivedCoreProperties) outside the categories hidden() shows anyway:
# a browser draws nothing for them. The rest of the set is Cf or unassigned.
IGNORABLE = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x3164, 0x3164),
             (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xE0100, 0xE01EF))


class Problem(Exception):
    """A reason the image cannot be captured; its text is safe to print."""


def chrome():
    """The browser to capture with: CHROME, else Chrome, Chromium or Edge where each installs on Windows, macOS and Linux."""
    local = os.environ.get("LOCALAPPDATA")
    candidates = [os.environ.get("CHROME"),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  local and os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for c in filter(None, candidates):
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise Problem("no Chrome, Chromium or Edge found; set CHROME to its path")


def phrase(code):
    try:
        return http.HTTPStatus(code).phrase
    except ValueError:
        return ""


def advances(text):
    """The cells text takes in the frame's pre after each of its characters in turn. A tab reaches the next tab stop,
    a wide glyph (CJK, emoji) takes two cells, and so does a narrow one followed by U+FE0F, which asks for its emoji
    form; a combining mark or format character takes none. Counting with len() measures such a line short, and a line
    measured short runs past the window's edge and is cut there without a marker. Where the font draws a glyph
    narrower than this counts, the window is only a little wider than it needs to be."""
    used = last = 0
    for ch in text:
        if ch == "\t":
            step = 8 - used % 8
        elif ch == "\ufe0f":
            step = 1 if last == 1 else 0
        elif unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            step = 0
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            step = 2
        else:
            step = 1
        used, last = used + step, step
        yield used


def cells(text):
    """How many monospace cells text takes in the frame's pre."""
    used = 0
    for used in advances(text):
        pass
    return used


def clip(line, width=WIDTH):
    """The line as drawn: whole when it fits in width cells, else cut with an ellipsis so the window never cuts it.
    One pass, which stops once the line is known to be too long, so a long line from the body costs little."""
    keep = None
    for i, used in enumerate(advances(line)):
        if keep is None and used > width - 3:
            keep = i
        if used > width:
            return line[:keep] + "..."
    return line


def hidden(ch, before=""):
    """Whether ch draws nothing or changes how the text around it draws, so the image shows its escape instead: a
    control character (Cc, tab aside), a format character (Cf: zero-width space and joiners, soft hyphen, word joiner,
    byte order mark, the bidi marks, overrides and isolates), a line or paragraph separator (Zl, Zp), a lone surrogate
    (Cs), an unassigned code point (Cn, which a newer browser may know as one of these), or another default-ignorable
    code point. A variation selector right after a symbol (So) stays, since it picks that visible symbol's text or
    emoji form: U+26A0 then U+FE0F is the warning sign drawn as an emoji."""
    if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs", "Cn"):
        return ch != "\t"
    cp = ord(ch)
    if any(lo <= cp <= hi for lo, hi in IGNORABLE):
        return not (ch in "\ufe0e\ufe0f" and before and unicodedata.category(before) == "So")
    return False


def escape(ch):
    """ch as JSON would escape it, \\uXXXX, or as \\UXXXXXXXX past the Basic Multilingual Plane."""
    cp = ord(ch)
    return f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{cp:08x}"


def visible(text, width=None):
    """text as drawn: each character hidden() names as its escape, so nothing invisible can stand between two
    characters of the image, and no bidi control can reorder what it draws. With width, stops once what it has made
    is known to be wider than that many cells, which keeps a long line from the body cheap; clip() cuts there anyway."""
    out, last, least = [], 0, 0
    for m in UNUSUAL.finditer(text):
        i = m.start()
        least += i - last  # printable ASCII and tabs, a cell or more each
        ch = text[i]
        if hidden(ch, text[i - 1] if i else ""):
            shown = escape(ch)
            least += len(shown)
        else:
            shown = ch
            least += unicodedata.category(ch) not in ("Mn", "Me")  # a mark takes no cell, anything else one or two
        out += (text[last:i], shown)
        last = i + 1
        if width is not None and least > width:
            return "".join(out)
    out.append(text[last:])
    return "".join(out)


def json_lines(text, keep=LINES + 1):
    """A JSON text laid out as json.dumps(indent=2) lays it out, or None when text is not JSON (RFC 8259: no NaN,
    no trailing comma). Only the whitespace between tokens changes: every string, number, true, false and null is
    copied character for character, never decoded and written again, so an escape stays escaped and 1.2345678E7 stays
    1.2345678E7, and the image draws the body as it was returned. Returns the first keep lines and how many there are
    in all; past keep, lines are only counted. No recursion, so no nesting is too deep."""
    lines, line, stack, count = [], "", [], 0
    i, want = SPACE.match(text).end(), "value"

    def end_line(text_of_line, depth):
        nonlocal count
        if count < keep:
            lines.append(text_of_line)
        count += 1
        return "  " * depth if count < keep else ""

    while True:
        c = text[i:i + 1]
        if want == "value":
            if c in ("{", "["):
                close = "}" if c == "{" else "]"
                j = SPACE.match(text, i + 1).end()
                if text[j:j + 1] == close:
                    line, i, want = line + c + close, j + 1, "next"
                else:
                    stack.append(close)
                    line, i, want = end_line(line + c, len(stack)), j, ("key" if c == "{" else "value")
            else:
                m = STRING.match(text, i) or NUMBER.match(text, i) or LITERAL.match(text, i)
                if not m:
                    return None
                line, i, want = line + m.group(), m.end(), "next"
        elif want == "key":
            m = STRING.match(text, i)
            if not m:
                return None
            i = SPACE.match(text, m.end()).end()
            if text[i:i + 1] != ":":
                return None
            line, i, want = line + m.group() + ": ", i + 1, "value"
        elif not stack:  # the whole text is one value
            if i < len(text):
                return None
            end_line(line, 0)
            return lines, count
        elif c == ",":
            line, i = end_line(line + ",", len(stack)), i + 1
            want = "key" if stack[-1] == "}" else "value"
        elif c == stack[-1]:
            stack.pop()
            line, i = end_line(line, len(stack)) + c, i + 1
        else:
            return None
        i = SPACE.match(text, i).end()


def body_lines(text):
    """The body's lines as drawn, before they are made visible and clipped: a JSON body laid out by json_lines(),
    anything else as text, its lines split at LF or CR LF, and either cut at LINES with a count of the rest."""
    if not text.strip():
        return ["(empty body)"]
    laid = json_lines(text)
    if laid:
        lines, count = laid
    else:
        lines = re.split(r"\r?\n", text.strip())
        count = len(lines)
    return lines[:LINES] + [f"... ({count - LINES} more lines)"] if count > LINES else lines


def rows(r):
    """The lines drawn for a record, each with its color, or None for plain. Every one is made visible() and clipped,
    the headers and the URL as much as the body, since an API writes the response headers and an export the names."""
    lines = body_lines(r.get("body") or "")
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
    return [(c, clip(visible(t, WIDTH))) for c, t in out]


def size(drawn):
    """The window's (width, height) in pixels for the rows drawn: a cell per display cell of the widest row, plus the margins."""
    width = min(MAX_WIDTH, max(MIN_WIDTH, round(max(cells(t) for _, t in drawn) * CELL) + MARGIN))
    return width, TITLE_BAR + PADDING + len(drawn) * ROW


def title_for(r):
    return f"{r.get('label') or r['environment']} - {r['method']} {r.get('path') or r['url']}"


def page(title, body):
    """The frame with the title (made visible() and escaped here) and the body's HTML in place."""
    return FRAME.read_text(encoding="utf-8").replace("{{title}}", html.escape(visible(title))).replace("{{body}}", body)


def render(r, png, title=None):
    """Writes png, and its .html page beside it, for the record r. Returns png as an absolute path.
    Raises Problem when no browser is found or the browser does not deliver the image."""
    png = Path(png).resolve()  # the page is given to the browser as a file URI, which only an absolute path has
    drawn = rows(r)
    body = "\n".join(f'<span style="{COLORS[c]}">{html.escape(t)}</span>' if c else html.escape(t) for c, t in drawn)
    page_file = png.with_suffix(".html")
    # Encoded before the file is opened, so a character UTF-8 cannot hold never leaves the page cut short; visible()
    # has shown every lone surrogate already, and backslashreplace would write one the same way.
    page_file.write_bytes(page(title or title_for(r), body).encode("utf-8", "backslashreplace"))
    width, height = size(drawn)
    if png.exists():
        png.unlink()
    try:
        subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}",
                        f"--screenshot={png}", page_file.as_uri()], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    except subprocess.CalledProcessError as e:
        raise Problem(f"the browser exited with status {e.returncode} instead of writing {png}") from None
    except subprocess.TimeoutExpired:
        raise Problem(f"the browser did not write {png} within 90 seconds") from None
    except OSError as e:
        raise Problem(f"cannot run the browser: {e}") from None
    if not png.exists():
        raise Problem(f"the browser did not write {png}")
    return png


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("record")
    p.add_argument("--out")
    p.add_argument("--title")
    args = p.parse_args()
    record = Path(args.record)
    r = json.loads(record.read_text(encoding="utf-8"))
    try:
        print(render(r, args.out or record.with_suffix(".png"), args.title))
    except Problem as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
