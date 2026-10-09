"""Takes this skill's demo screenshot: the dry-run preview, drawn in the terminal window frame.

Usage:
  python3 skills/ado-evidence/screenshots/shoot.py

Runs post.py --dry-run on comment.md, which touches nothing and reaches no network, and draws the command above
what it printed. The command is run exactly as drawn, word for word: python3 from the PATH, in the plugin folder
(skills/ado-evidence, where the README's commands are typed), with every path relative to there. So a reader who
types it there gets the preview shown. The images it references are api-call's demo screenshots, so nothing is
duplicated here; both skills draw in the same frame, api-call's frame.html. Run it from anywhere; paths are resolved
from this file. Only ever run from a clone, which is why it reaches across to the other skill.
"""
import html, os, shlex, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent  # skills/ado-evidence, where the README's commands are typed
POST = PLUGIN / "skills/ado-evidence/scripts/post.py"
IMAGES = PLUGIN.parent / "api-call/screenshots"
FRAME = PLUGIN.parent / "api-call/skills/api-call/scripts/frame.html"
PNG = HERE / "ado-evidence-dry-run.png"
ORG, PROJECT, WORK_ITEM = "https://dev.azure.com/contoso", "Widgets", "70012"
TITLE_BAR, PADDING, ROW = 37, 22, 20  # pixels, as api-call's render.py counts them


def chrome():
    candidates = [os.environ.get("CHROME"),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for c in filter(None, candidates):
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    sys.exit("no Chrome, Chromium or Edge found; set CHROME to its path")


def command():
    """The dry run as a reader types it in PLUGIN: python3, then every path relative to that folder."""
    def rel(path):
        return Path(os.path.relpath(path, PLUGIN)).as_posix()
    return ["python3", rel(POST), "--org", ORG, "--project", PROJECT, "--work-item", WORK_ITEM,
            "--comment", rel(HERE / "comment.md"), "--images", rel(IMAGES), "--dry-run"]


def drawn(argv):
    """argv as typed at a prompt: each word shell-quoted, wrapped with a backslash before --work-item and --images."""
    lines, line = [], "$"
    for word in argv:
        if word in ("--work-item", "--images"):
            lines.append(line + " \\")
            line = "   "
        line += " " + shlex.quote(word)
    return lines + [line]


def terminal():
    """The rows the frame draws: the command, then what running exactly that command in PLUGIN printed."""
    for needed in (POST, FRAME, IMAGES):
        if not needed.exists():
            sys.exit(f"missing {needed}; run this from a clone of the repository")
    if not shutil.which("python3"):
        sys.exit("python3 is not on the PATH; the demo runs its command exactly as drawn")
    argv = command()
    proc = subprocess.run(argv, cwd=PLUGIN, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        sys.exit(f"post.py --dry-run failed:\n{proc.stderr.strip()}")
    return [("cmd", line) for line in drawn(argv)] + [(None, line) for line in proc.stdout.rstrip("\n").splitlines()]


def main():
    rows = terminal()
    colors = {"cmd": "color:#74bdf5;font-weight:700"}
    body = "\n".join(f'<span style="{colors[c]}">{html.escape(t)}</span>' if c else html.escape(t) for c, t in rows)
    page = (FRAME.read_text(encoding="utf-8")
            .replace("{{title}}", html.escape(f"ado-evidence - preview for work item {WORK_ITEM}"))
            .replace("{{body}}", body))
    page_file = PNG.with_suffix(".html")
    page_file.write_text(page, encoding="utf-8")

    width = min(1600, max(720, round(max(len(t) for _, t in rows) * 9.2) + 60))
    height = TITLE_BAR + PADDING + len(rows) * ROW
    if PNG.exists():
        PNG.unlink()
    subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size={width},{height}", f"--screenshot={PNG}", page_file.as_uri()],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    if not PNG.exists():
        sys.exit(f"the browser did not write {PNG}")
    print(PNG)


if __name__ == "__main__":
    main()
