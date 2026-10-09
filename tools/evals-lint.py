"""Checks the eval suites for free: the files `claude plugin eval` would load, without running a case.

Usage:
  python3 tools/evals-lint.py [EVALS_DIR ...]     the suites named, or every evals/ of a plugin the marketplace lists
  python3 tools/evals-lint.py --self-test         this script's own tests

`claude plugin validate` does not read evals/, and the eval loader compiles a grader's regex only when it grades,
after the paid session. This reads every case the way the loader would and fails on what would break or quietly
weaken a run: a prompt.md key the loader refuses, a case with no grader, an unknown grader type or option, a
missing required option, a value out of range, a `pattern` or `input_match` that does not compile as a JavaScript
regex (compiled by node, as the harness does), and flags JavaScript does not take.

A grader can carry examples, which pin down what it accepts: `<case>/examples/<grader>.json`, with
  regex:      {"pass": [texts the grader must pass], "fail": [texts it must fail]}
  tool_used:  {"match": [tool inputs input_match must match], "no_match": [inputs it must not]}
A tool input is an object, encoded as JSON the way the harness encodes it, or a string taken as already encoded.

Front matter is read as a small subset of YAML: one `key: value` per line; values plain, 'single-quoted',
"double-quoted" (read as JSON strings), [flow, lists] or {flow: maps}; or a key alone followed by indented
`- item` or `key: value` lines. Anything else is reported rather than guessed at. Needs Python 3.9+ and node.
"""
import json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROMPT_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
               "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env"}
COMMON_GRADER_KEYS = {"type", "weight", "arm"}
GRADER_KEYS = {
    "regex": {"pattern", "flags", "match", "target"},
    "tool_used": {"tool", "input_match", "min", "max"},
    "tool_order": {"before", "after"},
    "file_exists": {"path", "exists"},
    "llm": {"criteria", "focus"},
    "baseline": {"baseline_file", "criteria"},
}
REQUIRED = {"regex": ["pattern"], "tool_used": ["tool"], "tool_order": ["before", "after"], "file_exists": ["path"],
            "baseline": ["baseline_file"]}
VIEWS = {"last_message", "trace", "files", "mock_calls"}


class Unreadable(Exception):
    pass


# ---- the YAML subset ---------------------------------------------------------------------------------------------

KEY = re.compile(r"([A-Za-z_][\w-]*)\s*:(?:\s+(.*))?$")


def split_flow(s, where):
    """Splits the inside of a [..] or {..} on top-level commas, keeping quoted text whole."""
    parts, cur, quote, depth, i = [], "", None, 0, 0
    while i < len(s):
        c = s[i]
        if quote:
            cur += c
            if c == quote:
                if quote == "'" and s[i + 1:i + 2] == "'":
                    cur += "'"
                    i += 1
                else:
                    quote = None
            elif c == "\\" and quote == '"':
                cur += s[i + 1:i + 2]
                i += 1
        elif c in "'\"":
            quote, cur = c, cur + c
        elif c in "[{":
            depth, cur = depth + 1, cur + c
        elif c in "]}":
            depth, cur = depth - 1, cur + c
        elif c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    if quote or depth:
        raise Unreadable(f"{where}: an unclosed quote or bracket")
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts]


def scalar(s, where, nested=False):
    s = s.strip()
    if not s:
        return None
    if s[0] == "'":
        m = re.fullmatch(r"'((?:[^']|'')*)'(\s+#.*)?", s)
        if not m:
            raise Unreadable(f"{where}: a single-quoted value must end at its closing quote (write ' inside as '')")
        return m.group(1).replace("''", "'")
    if s[0] == '"':
        m = re.fullmatch(r'("(?:[^"\\]|\\.)*")(\s+#.*)?', s)
        if not m:
            raise Unreadable(f"{where}: a double-quoted value must end at its closing quote")
        try:
            return json.loads(m.group(1))
        except ValueError:
            raise Unreadable(f"{where}: evals-lint reads a double-quoted value as a JSON string; use single quotes")
    if s[0] in "[{":
        if nested:
            raise Unreadable(f"{where}: evals-lint reads flow collections one level deep only")
        close = "]" if s[0] == "[" else "}"
        body = re.sub(r"\s+#.*$", "", s) if not s.endswith(close) else s
        if not body.endswith(close):
            raise Unreadable(f"{where}: a flow collection must close on the same line")
        items = split_flow(body[1:-1], where)
        if s[0] == "[":
            return [scalar(x, where, nested=True) for x in items]
        out = {}
        for item in items:
            m = KEY.match(item)
            if not m or m.group(2) is None:
                raise Unreadable(f"{where}: {item!r} is not a key: value pair")
            if m.group(1) in out:
                raise Unreadable(f"{where}: {m.group(1)} appears twice")
            out[m.group(1)] = scalar(m.group(2), where, nested=True)
        return out
    if s[0] in "|>":
        raise Unreadable(f"{where}: evals-lint does not read block scalars; put the value on one line, quoted")
    if s[0] in "&*!%@`":
        raise Unreadable(f"{where}: quote a value that starts with {s[0]}")
    s = re.sub(r"\s+#.*$", "", s)
    if re.search(r":\s", s) or s.endswith(":"):
        raise Unreadable(f"{where}: quote a value that holds ': '")
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s in ("null", "Null", "NULL", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


def front_matter(path):
    """The front matter as a dict, and the body after it."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise Unreadable(f"{path}: must start with a --- front matter line")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise Unreadable(f"{path}: the front matter has no closing --- line")
    data, i, block = {}, 1, lines[1:end]
    while i - 1 < len(block):
        line = block[i - 1]
        where = f"{path}:{i + 1}"
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            raise Unreadable(f"{where}: an indented line with no key above it")
        m = KEY.match(line)
        if not m:
            raise Unreadable(f"{where}: not a key: value line")
        key, rest = m.group(1), m.group(2)
        if key in data:
            raise Unreadable(f"{where}: {key} appears twice")
        if rest is not None and rest.strip() and not rest.lstrip().startswith("#"):
            data[key] = scalar(rest, where)
            continue
        items = []
        while i - 1 < len(block) and (not block[i - 1].strip() or block[i - 1][0] in " \t"):
            if block[i - 1].strip() and not block[i - 1].lstrip().startswith("#"):
                items.append((f"{path}:{i + 1}", block[i - 1].strip()))
            i += 1
        if not items:
            data[key] = None
        elif all(t.startswith("- ") or t == "-" for _, t in items):
            data[key] = [scalar(t[1:], w, nested=True) for w, t in items]
        else:
            sub = {}
            for w, t in items:
                mm = KEY.match(t)
                if not mm or mm.group(2) is None:
                    raise Unreadable(f"{w}: not a key: value line")
                if mm.group(1) in sub:
                    raise Unreadable(f"{w}: {mm.group(1)} appears twice")
                sub[mm.group(1)] = scalar(mm.group(2), w, nested=True)
            data[key] = sub
    return data, "\n".join(lines[end + 1:]).strip()


# ---- the checks --------------------------------------------------------------------------------------------------

def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check_view(v, key, where, errors):
    if v is None or v in VIEWS:
        return
    if isinstance(v, dict) and v.get("source") == "file" and isinstance(v.get("path"), str) and set(v) == {"source", "path"}:
        return
    errors.append(f"{where}: {key} must be one of {', '.join(sorted(VIEWS))} or {{ source: file, path: <path> }}")


def check_prompt(path, errors):
    try:
        fm, body = front_matter(path)
    except Unreadable as e:
        errors.append(str(e))
        return
    for k in sorted(set(fm) - PROMPT_KEYS):
        errors.append(f"{path}: unknown key {k}; prompt.md takes {', '.join(sorted(PROMPT_KEYS))}")
    if not body:
        errors.append(f"{path}: the prompt, the text after the front matter, is empty")
    for k, lo, hi in (("runs", 1, 50), ("max_turns", 1, 200), ("timeout_seconds", 1, 3600)):
        if k in fm and not (is_int(fm[k]) and lo <= fm[k] <= hi):
            errors.append(f"{path}: {k} must be a whole number from {lo} to {hi}")
    for k in ("tags", "allowed_tools", "plugins"):
        if k in fm and not (isinstance(fm[k], list) and all(isinstance(x, str) and x for x in fm[k])):
            errors.append(f"{path}: {k} must be a list of names")
    if "env" in fm:
        if not isinstance(fm["env"], dict):
            errors.append(f"{path}: env must be a map")
        else:
            for k in fm["env"]:
                if not re.fullmatch(r"EVAL_[A-Z0-9_]*", k):
                    errors.append(f"{path}: env key {k} must match EVAL_[A-Z0-9_]*, or the run fails")


def check_grader(path, case_dir, errors, regexes, examples):
    try:
        fm, body = front_matter(path)
    except Unreadable as e:
        errors.append(str(e))
        return
    t = fm.get("type")
    if t not in GRADER_KEYS:
        errors.append(f"{path}: type must be one of {', '.join(GRADER_KEYS)}, not {t!r}")
        return
    allowed = COMMON_GRADER_KEYS | GRADER_KEYS[t]
    for k in sorted(set(fm) - allowed):
        errors.append(f"{path}: {k} is not an option of a {t} grader, which takes {', '.join(sorted(allowed - {'type'}))}")
    for k in REQUIRED.get(t, []):
        if fm.get(k) in (None, ""):
            errors.append(f"{path}: a {t} grader needs {k}")
    if "weight" in fm and not (isinstance(fm["weight"], (int, float)) and not isinstance(fm["weight"], bool) and fm["weight"] > 0):
        errors.append(f"{path}: weight must be a positive number")
    if "arm" in fm and fm["arm"] not in ("with-only", "both"):
        errors.append(f"{path}: arm must be with-only or both")

    if t == "regex":
        check_view(fm.get("target"), "target", path, errors)
        match = fm.get("match", "contains")
        if not (match in ("contains", "not_contains") or (isinstance(match, str) and re.fullmatch(r"count:\d+", match))):
            errors.append(f"{path}: match must be contains, not_contains or count:N")
        flags = fm.get("flags", "")
        if not isinstance(flags, str):
            errors.append(f"{path}: flags must be a string such as i")
            flags = ""
        if isinstance(fm.get("pattern"), str) and fm["pattern"]:
            regexes.append({"where": f"{path}: pattern", "pattern": fm["pattern"], "flags": flags})
            ex = case_dir / "examples" / f"{path.stem}.json"
            if ex.exists():
                examples.append({"where": str(ex), "kind": "regex", "pattern": fm["pattern"], "flags": flags,
                                 "match": match, "cases": load_examples(ex, ("pass", "fail"), errors)})
        elif "pattern" in fm:
            errors.append(f"{path}: pattern must be a string")
    elif t == "tool_used":
        if not isinstance(fm.get("tool"), str):
            errors.append(f"{path}: tool must be a tool name")
        for k in ("min", "max"):
            if k in fm and not (is_int(fm[k]) and fm[k] >= 0):
                errors.append(f"{path}: {k} must be a whole number, 0 or more")
        if is_int(fm.get("min", 1)) and is_int(fm.get("max", 10**9)) and fm.get("min", 1) > fm.get("max", 10**9):
            errors.append(f"{path}: min is above max, so the grader can never pass")
        im = fm.get("input_match")
        if im is not None:
            if not isinstance(im, str) or not im:
                errors.append(f"{path}: input_match must be a regex")
            else:
                regexes.append({"where": f"{path}: input_match", "pattern": im, "flags": ""})
                ex = case_dir / "examples" / f"{path.stem}.json"
                if ex.exists():
                    examples.append({"where": str(ex), "kind": "input_match", "pattern": im, "flags": "",
                                     "cases": load_examples(ex, ("match", "no_match"), errors)})
    elif t == "tool_order":
        for k in ("before", "after"):
            v = fm.get(k)
            if isinstance(v, dict):
                if not isinstance(v.get("tool"), str) or set(v) - {"tool", "input_match"}:
                    errors.append(f"{path}: {k} must be a tool name or {{ tool, input_match }}")
                elif isinstance(v.get("input_match"), str):
                    regexes.append({"where": f"{path}: {k}.input_match", "pattern": v["input_match"], "flags": ""})
            elif v is not None and not isinstance(v, str):
                errors.append(f"{path}: {k} must be a tool name or {{ tool, input_match }}")
    elif t == "file_exists":
        if "exists" in fm and not isinstance(fm["exists"], bool):
            errors.append(f"{path}: exists must be true or false")
    elif t == "llm":
        check_view(fm.get("focus"), "focus", path, errors)
        if not (body or fm.get("criteria")):
            errors.append(f"{path}: an llm grader needs its rubric, the text after the front matter")
    elif t == "baseline":
        if not (body or fm.get("criteria")):
            errors.append(f"{path}: a baseline grader needs its criteria, the text after the front matter")
        bf = fm.get("baseline_file")
        if isinstance(bf, str) and bf and not (case_dir / bf).is_file():
            errors.append(f"{path}: baseline_file {bf} is not in the case directory")


def load_examples(path, keys, errors):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        errors.append(f"{path}: not JSON: {e}")
        return {}
    if not isinstance(data, dict) or set(data) - set(keys) or not any(data.get(k) for k in keys):
        errors.append(f"{path}: must be an object with {' and/or '.join(keys)}, each a list")
        return {}
    for k in keys:
        if not isinstance(data.get(k, []), list):
            errors.append(f"{path}: {k} must be a list")
            return {}
    return data


def check_suite(evals, errors, regexes, examples):
    cases = 0
    for d, subdirs, _files in os.walk(evals):
        d = Path(d)
        subdirs[:] = sorted(s for s in subdirs if not (d == evals and s == "results"))
        is_case = (d / "prompt.md").is_file() or (d / "case.yaml").is_file()
        if not is_case:
            continue
        subdirs[:] = []
        cases += 1
        graders = sorted((d / "graders").glob("*.md"))
        if (d / "case.yaml").is_file():
            print(f"note: {d / 'case.yaml'} is not read by evals-lint; only its graders/*.md and prompt.md are")
        elif not graders:
            errors.append(f"{d}: a case needs at least one graders/*.md, or it fails to load")
        if (d / "prompt.md").is_file():
            check_prompt(d / "prompt.md", errors)
        for g in graders:
            check_grader(g, d, errors, regexes, examples)
        ex_dir = d / "examples"
        if ex_dir.is_dir():
            for ex in sorted(ex_dir.glob("*.json")):
                if not (d / "graders" / f"{ex.stem}.md").is_file():
                    errors.append(f"{ex}: names no grader; examples/<grader>.json goes with graders/<grader>.md")
    if cases == 0:
        errors.append(f"{evals}: holds no case (a folder with a prompt.md or case.yaml)")
    return cases


# The harness builds `new RegExp(pattern, flags)` and, for not_contains, passes when .test() is false;
# for tool_used, it tests input_match, without flags, against the JSON-encoded tool input.
NODE = r"""
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = [];
for (const r of job.regexes) {
  try { new RegExp(r.pattern, r.flags); } catch (e) { out.push(`${r.where} does not compile in JavaScript: ${e.message}`); }
}
const passes = (ex, text) => {
  const re = new RegExp(ex.pattern, ex.flags);
  if (ex.match === 'not_contains') return !re.test(text);
  if (ex.match && ex.match.startsWith('count:')) {
    const g = new RegExp(ex.pattern, ex.flags.includes('g') ? ex.flags : ex.flags + 'g');
    return (text.match(g) || []).length === Number(ex.match.slice(6));
  }
  return re.test(text);
};
const show = (t) => JSON.stringify(t.length > 90 ? t.slice(0, 87) + '...' : t);
for (const ex of job.examples) {
  try { new RegExp(ex.pattern, ex.flags); } catch (e) { continue; }
  if (ex.kind === 'regex') {
    for (const t of ex.cases.pass || []) if (!passes(ex, t)) out.push(`${ex.where}: the grader fails a reply listed under pass: ${show(t)}`);
    for (const t of ex.cases.fail || []) if (passes(ex, t)) out.push(`${ex.where}: the grader passes a reply listed under fail: ${show(t)}`);
  } else {
    const enc = (i) => typeof i === 'string' ? i : JSON.stringify(i);
    for (const i of ex.cases.match || []) if (!new RegExp(ex.pattern).test(enc(i))) out.push(`${ex.where}: input_match misses ${show(enc(i))}`);
    for (const i of ex.cases.no_match || []) if (new RegExp(ex.pattern).test(enc(i))) out.push(`${ex.where}: input_match matches ${show(enc(i))}`);
  }
}
process.stdout.write(JSON.stringify(out));
"""


def check_regexes(regexes, examples, errors):
    for r in regexes:
        if not re.fullmatch(r"[dgimsuvy]*", r["flags"]) or len(set(r["flags"])) != len(r["flags"]):
            errors.append(f"{r['where']}: flags {r['flags']!r} are not JavaScript regex flags (d g i m s u v y, each once)")
        if "(?i)" in r["pattern"]:
            errors.append(f"{r['where']}: inline (?i) is not JavaScript; use flags: i")
    for e in examples:
        for k, v in e["cases"].items():
            for x in v:
                if e["kind"] == "regex" and not isinstance(x, str):
                    errors.append(f"{e['where']}: every {k} entry must be a string")
    if not regexes and not examples:
        return
    node = shutil.which("node")
    if not node:
        sys.exit("evals-lint needs node, to compile the graders' regexes as JavaScript does")
    proc = subprocess.run([node, "-e", NODE], input=json.dumps({"regexes": regexes, "examples": examples}),
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        sys.exit("evals-lint: node failed:\n" + proc.stderr.strip())
    errors.extend(json.loads(proc.stdout))


def listed_suites():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    return [ROOT / p["source"] / "evals" for p in market["plugins"] if (ROOT / p["source"] / "evals").is_dir()]


def lint(suites):
    errors, regexes, examples, cases = [], [], [], 0
    for s in suites:
        s = Path(s).resolve()
        if not s.is_dir():
            errors.append(f"{s}: no such folder")
            continue
        cases += check_suite(s, errors, regexes, examples)
    check_regexes(regexes, examples, errors)
    return errors, cases, len(examples)


def main(argv):
    if argv[:1] == ["--self-test"]:
        import unittest
        sys.argv = [sys.argv[0]] + argv[1:]
        unittest.main(module=__name__, argv=sys.argv, verbosity=2)
    suites = argv or listed_suites()
    errors, cases, examples = lint(suites)
    for e in errors:
        print("error:", e.replace(str(ROOT) + os.sep, ""))
    if errors:
        sys.exit(f"evals-lint: {len(errors)} problem(s) in {len(suites)} suite(s)")
    print(f"evals-lint: {len(suites)} suite(s), {cases} case(s), {examples} example set(s), all well formed")


# ---- this script's own tests -------------------------------------------------------------------------------------

import tempfile, textwrap, unittest  # noqa: E402


class EvalsLintTest(unittest.TestCase):
    PROMPT = "---\ndescription: d\ntags: [trigger]\nmax_turns: 8\nallowed_tools: [Read, Skill]\n---\n\nDo the thing.\n"
    SKILL_FIRED = "---\ntype: tool_used\ntool: Skill\ninput_match: '\"skill\"\\s*:\\s*\"(?:[\\w-]+:)?api-call\"'\n---\n"

    def suite(self, graders, prompt=None, examples=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        case = tmp / "evals" / "a-case"
        (case / "graders").mkdir(parents=True)
        (case / "prompt.md").write_text(self.PROMPT if prompt is None else prompt, encoding="utf-8")
        for name, text in graders.items():
            (case / "graders" / f"{name}.md").write_text(textwrap.dedent(text), encoding="utf-8")
        for name, data in (examples or {}).items():
            (case / "examples").mkdir(exist_ok=True)
            (case / "examples" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        return lint([tmp / "evals"])[0]

    def assertFlags(self, errors, *needles):
        for n in needles:
            self.assertTrue(any(n in e for e in errors), f"no error mentions {n!r}: {errors}")

    def test_a_well_formed_suite_passes(self):
        self.assertEqual(self.suite({"skill-fired": self.SKILL_FIRED}), [])

    def test_checks_10_an_unknown_grader_type_fails(self):
        errors = self.suite({"skill-fired": self.SKILL_FIRED.replace("tool_used", "tool_usedd")})
        self.assertFlags(errors, "type must be one of", "tool_usedd")

    def test_checks_10_an_input_match_that_does_not_compile_fails(self):
        errors = self.suite({"skill-fired": "---\ntype: tool_used\ntool: Skill\ninput_match: '\"skill\"\\s*:\\s*\"(api-call'\n---\n"})
        self.assertFlags(errors, "input_match does not compile in JavaScript")

    def test_checks_10_a_pattern_that_does_not_compile_or_bad_flags_or_match_fail(self):
        errors = self.suite({"r": "---\ntype: regex\npattern: '(unclosed'\nflags: zq\nmatch: sometimes\n---\n"})
        self.assertFlags(errors, "pattern does not compile", "flags 'zq'", "match must be")

    def test_checks_10_a_misspelt_option_fails_rather_than_being_ignored(self):
        errors = self.suite({"skill-fired": self.SKILL_FIRED.replace("input_match", "input_macth")})
        self.assertFlags(errors, "input_macth is not an option of a tool_used grader")

    def test_checks_10_a_missing_required_option_fails(self):
        self.assertFlags(self.suite({"r": "---\ntype: regex\nflags: i\n---\n"}), "a regex grader needs pattern")
        self.assertFlags(self.suite({"j": "---\ntype: llm\n---\n"}), "an llm grader needs its rubric")

    def test_checks_10_a_case_with_no_grader_fails(self):
        self.assertFlags(self.suite({}), "needs at least one graders/*.md")

    def test_checks_10_a_prompt_key_the_loader_refuses_fails(self):
        errors = self.suite({"skill-fired": self.SKILL_FIRED},
                            prompt="---\nmax_turn: 8\nruns: 0\nenv: {TOKEN: x}\n---\n\nDo it.\n")
        self.assertFlags(errors, "unknown key max_turn", "runs must be", "env key TOKEN")

    def test_checks_10_min_above_max_and_a_bad_target_fail(self):
        errors = self.suite({"t": "---\ntype: tool_used\ntool: Bash\nmin: 2\nmax: 1\n---\n",
                             "r": "---\ntype: regex\npattern: x\ntarget: last_reply\n---\n"})
        self.assertFlags(errors, "min is above max", "target must be one of")

    def test_checks_6_a_regex_grader_must_hold_to_its_examples(self):
        grader = "---\ntype: regex\npattern: '\\bI(?:''ve| have)\\s+(?:posted|added)\\b'\nflags: i\nmatch: not_contains\n---\n"
        errors = self.suite({"claims": grader},
                            examples={"claims": {"pass": ["I've added a caption."], "fail": ["I’ve posted it."]}})
        self.assertFlags(errors, "fails a reply listed under pass", "passes a reply listed under fail")

    def test_an_input_match_must_hold_to_its_examples(self):
        errors = self.suite({"skill-fired": self.SKILL_FIRED},
                            examples={"skill-fired": {"match": [{"skill": "api-call"}, {"skill": "x:api-call"}],
                                                      "no_match": [{"skill": "my-api-call"}, {"skill": "api-call-v2"}]}})
        self.assertEqual(errors, [])
        errors = self.suite({"skill-fired": self.SKILL_FIRED}, examples={"skill-fired": {"no_match": [{"skill": "api-call"}]}})
        self.assertFlags(errors, "input_match matches")

    def test_examples_without_a_grader_fail(self):
        self.assertFlags(self.suite({"skill-fired": self.SKILL_FIRED}, examples={"gone": {"pass": ["x"]}}), "names no grader")

    def test_front_matter_the_lint_cannot_read_is_reported_not_guessed(self):
        self.assertFlags(self.suite({"r": "---\ntype: regex\npattern: >-\n  x\n---\n"}), "block scalars")
        self.assertFlags(self.suite({"r": "---\ntype: regex\npattern: 'it's'\n---\n"}), "single-quoted")
        self.assertFlags(self.suite({"r": "---\ntype: regex\ntype: llm\n---\n"}), "appears twice")

    def test_the_yaml_subset_reads_what_the_suites_use(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        f = tmp / "g.md"
        f.write_text("---\na: 'x''s \"y\"'  # note\nb: [Read, 'Skill', \"Bash\"]\nc: { source: file, path: out/r.md }\n"
                     "d: 3\ne: true\nf:\n  - one\n  - 'two'\ng:\n  tool: Bash\n  input_match: 'npm test'\nh: plain text\n---\nbody\n",
                     encoding="utf-8")
        fm, body = front_matter(f)
        self.assertEqual(fm, {"a": "x's \"y\"", "b": ["Read", "Skill", "Bash"], "c": {"source": "file", "path": "out/r.md"},
                              "d": 3, "e": True, "f": ["one", "two"], "g": {"tool": "Bash", "input_match": "npm test"},
                              "h": "plain text"})
        self.assertEqual(body, "body")

    def test_the_repository_suites_are_well_formed(self):
        errors, cases, _ = lint(listed_suites())
        self.assertEqual(errors, [])
        self.assertGreater(cases, 0)


if __name__ == "__main__":
    main(sys.argv[1:])
