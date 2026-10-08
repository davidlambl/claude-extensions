"""Posts a markdown comment with screenshots to an Azure DevOps work item.

Usage:
  python3 post_ado.py --org https://dev.azure.com/ORG --project PROJECT --work-item ID --comment comment.md
                      [--images DIR] [--edit COMMENT_ID] [--dry-run]

In the comment, write {{image:file.png}} where a screenshot goes. Each referenced file (looked up in --images,
default: the comment's folder) is uploaded as a work item attachment and embedded as ![file.png](url).
--dry-run prints the comment as it would be posted and touches nothing. --edit replaces the text of an existing
comment instead of adding one; its earlier images are left in place, unreferenced.

Auth: AZURE_DEVOPS_TOKEN if set, otherwise a token from the Azure CLI
(az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798, the Azure DevOps resource).
After posting it reads the comment back and checks that every image renders and matches the local file.
"""
import argparse, hashlib, html, json, os, re, shutil, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--org", required=True, help="https://dev.azure.com/ORG")
p.add_argument("--project", required=True)
p.add_argument("--work-item", required=True, type=int)
p.add_argument("--comment", required=True)
p.add_argument("--images")
p.add_argument("--edit", type=int)
p.add_argument("--dry-run", action="store_true")
args = p.parse_args()

org = args.org.rstrip("/")
project = urllib.parse.quote(args.project)
comment_file = Path(args.comment).resolve()
images_dir = Path(args.images).resolve() if args.images else comment_file.parent
text = comment_file.read_text(encoding="utf-8")
tokens = re.findall(r"\{\{image:([^}]+)\}\}", text)
files = {name: images_dir / name for name in dict.fromkeys(t.strip() for t in tokens)}
missing = [str(f) for f in files.values() if not f.is_file()]
if missing:
    sys.exit("missing images: " + ", ".join(missing))

if args.dry_run:
    preview = text
    for name, f in files.items():
        preview = re.sub(r"\{\{image:\s*" + re.escape(name) + r"\s*\}\}", f"![{name}](<upload {f.stat().st_size} bytes>)", preview)
    action = f"replace comment {args.edit}" if args.edit else "add a comment"
    print(f"DRY RUN - would {action} on work item {args.work_item} with {len(files)} image(s):\n")
    print(preview)
    sys.exit(0)


def token():
    if os.environ.get("AZURE_DEVOPS_TOKEN"):
        return os.environ["AZURE_DEVOPS_TOKEN"]
    az = shutil.which("az")
    if not az:
        sys.exit("set AZURE_DEVOPS_TOKEN or install the Azure CLI")
    return subprocess.run([az, "account", "get-access-token", "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
                           "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True, check=True).stdout.strip()


TOKEN = token()


def call(method, url, body=None, content_type="application/json", raw=False):
    data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = r.read()
    return payload if raw else json.loads(payload.decode("utf-8"))


urls = {}
for name, f in files.items():
    a = call("POST", f"{org}/{project}/_apis/wit/attachments?fileName={urllib.parse.quote(name)}&api-version=7.1",
             f.read_bytes(), "application/octet-stream")
    urls[name] = a["url"] if "fileName=" in a["url"] else a["url"] + "?fileName=" + urllib.parse.quote(name)
    print("uploaded", name)

for name, url in urls.items():
    text = re.sub(r"\{\{image:\s*" + re.escape(name) + r"\s*\}\}", lambda _m: f"![{name}]({url})", text)

base = f"{org}/{project}/_apis/wit/workItems/{args.work_item}/comments"
if args.edit:
    c = call("PATCH", f"{base}/{args.edit}?format=markdown&api-version=7.1-preview.4", {"text": text})
else:
    c = call("POST", f"{base}?format=markdown&api-version=7.1-preview.4", {"text": text})

back = call("GET", f"{base}/{c['id']}?$expand=renderedText&api-version=7.1-preview.4")
rendered = back.get("renderedText") or ""
# ADO may store quotes and angle brackets as HTML entities; they render the same.
checks = {"stored text matches": html.unescape(back["text"]) == html.unescape(text),
          "images rendered": rendered.count("<img") == len(urls)}
for name, url in urls.items():
    stored = call("GET", url.split("?")[0] + "?download=true&api-version=7.1", raw=True)
    checks[f"{name} matches local file"] = hashlib.sha256(stored).digest() == hashlib.sha256(files[name].read_bytes()).digest()
for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
print(f"comment {c['id']} (version {back.get('version')}) on {org}/{project}/_workitems/edit/{args.work_item}")
sys.exit(0 if all(checks.values()) else 1)
