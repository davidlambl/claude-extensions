"""Takes this skill's demo screenshot: the dry-run preview, drawn in the terminal window frame.

Usage:
  python3 skills/ado-evidence/screenshots/shoot.py

Runs post.py --dry-run on comment.md, which touches nothing and reaches no network, and captures what
it printed. The images it references are api-call's demo screenshots, so nothing is duplicated here;
both skills draw in the same frame, api-call's frame.html. Run it from anywhere; paths are resolved
from this file. Only ever run from a clone, which is why it reaches across to the other skill.
"""
import html, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POST = HERE.parent / "skills/ado-evidence/scripts/post.py"
IMAGES = HERE.parent.parent / "api-call/screenshots"
FRAME = HERE.parent.parent / "api-call/skills/api-call/scripts/frame.html"
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


for needed in (POST, FRAME, IMAGES):
    if not needed.exists():
        sys.exit(f"missing {needed}; run this from a clone of the repository")

argv = [sys.executable, str(POST), "--org", ORG, "--project", PROJECT, "--work-item", WORK_ITEM,
        "--comment", str(HERE / "comment.md"), "--images", str(IMAGES), "--dry-run"]
proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
if proc.returncode != 0:
    sys.exit(f"post.py --dry-run failed:\n{proc.stderr.strip()}")

# The command as a reader would type it, then exactly what it printed.
shown = (f"$ python3 scripts/post.py --org {ORG} --project {PROJECT} \\\n"
         f"    --work-item {WORK_ITEM} --comment comment.md --dry-run")
rows = [("cmd", line) for line in shown.splitlines()]
rows += [(None, line) for line in proc.stdout.rstrip("\n").splitlines()]

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
