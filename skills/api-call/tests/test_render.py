"""What render.py draws for a record, run with: python3 -m unittest discover -s skills/api-call/tests"""
import base64, contextlib, io, json, os, re, struct, subprocess, sys, tempfile, time, unicodedata, unittest, zlib
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "api-call" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import render  # noqa: E402

RECORD = {"source": "postman", "environment": "GitHub API", "collection": "GitHub", "folder": None, "method": "GET",
          "path": "/repos/x/y/languages", "url": "https://api.github.com/repos/x/y/languages", "auth": "none",
          "request_headers": {"Accept": "application/json", "X-Api-Key": "********"}, "client_cert": "client.pem",
          "status": 200, "elapsed_ms": 81, "sent": "2026-10-08T16:01:52.869-04:00", "sent_utc": "2026-10-08T20:01:52.869Z",
          "response_headers": {"Content-Type": "application/json; charset=utf-8"}, "body": '{"TypeScript": 1, "Python": 2}'}

PNG = b"\x89PNG\r\n\x1a\n"


class Rows(unittest.TestCase):
    def test_rows_in_order(self):
        rows = render.rows(RECORD)
        self.assertEqual(rows[0], ("dim", "Postman environment: GitHub API    collection: GitHub"))
        self.assertEqual(rows[1], ("dim", "sent 2026-10-08 16:01:52 (UTC-04:00) = 20:01:52 UTC"))
        self.assertEqual(rows[3], ("req", "> GET https://api.github.com/repos/x/y/languages"))
        self.assertEqual(rows[4:7], [("dim", "> Accept: application/json"), ("dim", "> X-Api-Key: ********"), ("dim", "> client certificate: client.pem")])
        self.assertEqual(rows[8], ("ok", "< HTTP 200 OK    81 ms"))
        self.assertEqual(rows[9], ("dim", "< Content-Type: application/json; charset=utf-8"))
        self.assertEqual([t for _, t in rows[11:]], ["{", '  "TypeScript": 1,', '  "Python": 2', "}"])

    def test_failed_status_is_red_and_text_bodies_pass_through(self):
        rows = render.rows({**RECORD, "status": 404, "body": "plain text"})
        self.assertEqual(rows[8], ("bad", "< HTTP 404 Not Found    81 ms"))
        self.assertEqual(rows[-1], (None, "plain text"))
        self.assertEqual(render.rows({**RECORD, "body": ""})[-1], (None, "(empty body)"))

    def test_long_bodies_are_cut_at_forty_lines(self):
        body = json.dumps(list(range(100)), indent=1)
        rows = render.rows({**RECORD, "body": body})
        self.assertEqual(rows[-1], (None, "... (62 more lines)"))
        self.assertEqual(len(rows), 11 + 41)

    def test_long_lines_are_clipped(self):
        rows = render.rows({**RECORD, "body": "x" * 200})
        self.assertEqual(len(rows[-1][1]), 150)
        self.assertTrue(rows[-1][1].endswith("..."))

    def test_title_prefers_the_label(self):
        self.assertEqual(render.title_for(RECORD), "GitHub API - GET /repos/x/y/languages")
        self.assertEqual(render.title_for({**RECORD, "label": "After: Test"}), "After: Test - GET /repos/x/y/languages")

    def test_page_fills_the_frame(self):
        page = render.page("A <b> title", "<span>body</span>")
        self.assertIn('<div class="title">A &lt;b&gt; title</div>', page)
        self.assertIn("<pre><span>body</span></pre>", page)
        self.assertNotIn("{{", page)


class FakeBrowser:
    """Stands in for subprocess.run when render() starts the browser: keeps the arguments, checks the page comes as a
    file URI and the image is asked for at an absolute path, and writes the image."""

    def __init__(self):
        self.argv = None

    def __call__(self, argv, **kwargs):
        self.argv = argv
        assert argv[-1].startswith("file:///"), argv[-1]
        shot = next(a for a in argv if a.startswith("--screenshot=")).split("=", 1)[1]
        assert Path(shot).is_absolute(), shot
        Path(shot).write_bytes(PNG)
        return subprocess.CompletedProcess(argv, 0)

    def window(self):
        size = next(a for a in self.argv if a.startswith("--window-size=")).split("=", 1)[1]
        return tuple(int(n) for n in size.split(","))


def drawn(r, png, title=None):
    """render.render with the browser stood in for; returns what it returned and the stand-in."""
    browser = FakeBrowser()
    with patch.object(render, "chrome", return_value="chrome"), patch.object(render.subprocess, "run", browser):
        return render.render(r, png, title), browser


class DisplayWidth(unittest.TestCase):
    """correctness-18: a row is measured in display cells, so CJK, emoji and tab-indented lines get the ellipsis
    instead of running past the window's edge, and the window is as wide as what is drawn."""

    def test_correctness_18_cells_count_tab_stops_wide_glyphs_and_marks(self):
        self.assertEqual(render.cells("abc"), 3)
        self.assertEqual(render.cells("\t<a>"), 11)
        self.assertEqual(render.cells("ab\tc"), 9)
        self.assertEqual(render.cells("\t\t"), 16)
        self.assertEqual(render.cells("日本語"), 6)
        self.assertEqual(render.cells("日\t"), 8)
        self.assertEqual(render.cells("ｱ"), 1)  # halfwidth katakana
        self.assertEqual(render.cells("🙂"), 2)
        self.assertEqual(render.cells("e\u0301"), 1)  # a combining accent takes no cell
        self.assertEqual(render.cells("a\u200bb"), 2)  # nor does a zero-width space
        self.assertEqual(render.cells("✔\ufe0f"), 2)  # U+FE0F draws a narrow character as a wide emoji
        self.assertEqual(render.cells("👍\ufe0f"), 2)  # and leaves one that is wide already as it is

    def test_correctness_18_cjk_lines_get_the_ellipsis(self):
        last = render.rows({**RECORD, "body": "日" * 100 + " END"})[-1][1]  # 104 characters by len(), 204 cells
        self.assertTrue(last.endswith("..."), last)
        self.assertNotIn("END", last)
        self.assertLessEqual(render.cells(last), 150)
        self.assertGreater(render.cells(last), 140)

    def test_correctness_18_tab_indented_lines_get_the_ellipsis(self):
        detail = "\t" * 6 + "<detail>" + "x" * 90 + " END</detail>"  # 117 characters by len(), 159 cells
        rows = render.rows({**RECORD, "body": f"<error>\n{detail}\n</error>"})
        self.assertEqual(rows[-1], (None, "</error>"))
        line = rows[-2][1]
        self.assertTrue(line.startswith("\t" * 6 + "<detail>"), line)
        self.assertTrue(line.endswith("..."), line)
        self.assertNotIn("END", line)
        self.assertLessEqual(render.cells(line), 150)

    def test_correctness_18_emoji_lines_get_the_ellipsis(self):
        last = render.rows({**RECORD, "body": "🙂" * 80 + " END"})[-1][1]  # 84 characters by len(), 164 cells
        self.assertTrue(last.endswith("..."), last)
        self.assertLessEqual(render.cells(last), 150)

    def test_ascii_clipping_is_unchanged(self):
        self.assertEqual(render.clip("x" * 150), "x" * 150)
        self.assertEqual(render.clip("x" * 151), "x" * 147 + "...")

    def test_correctness_18_the_window_is_as_wide_as_the_cells_drawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 60 characters by len(), 120 cells: not clipped, and twice as wide as len() would make it.
            rows = render.rows({**RECORD, "body": "日" * 60})
            _, browser = drawn({**RECORD, "body": "日" * 60}, Path(tmp) / "cjk.png")
            self.assertEqual(browser.window(), (round(120 * 9.2) + 60, 37 + 22 + len(rows) * 20))
            _, browser = drawn(RECORD, Path(tmp) / "plain.png")
            self.assertEqual(browser.window()[0], 720)
            _, browser = drawn({**RECORD, "body": "日" * 200}, Path(tmp) / "wide.png")
            self.assertLessEqual(browser.window()[0], 1600)

    def test_correctness_18_clipping_takes_one_pass_over_the_line(self):
        # A body is untrusted: a long run of zero-width characters must not make the measuring quadratic.
        line = "\u200b" * 30_000 + "x" * 200
        started = time.monotonic()
        clipped = render.clip(line)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(render.cells(clipped), 150)
        self.assertTrue(clipped.endswith("x" * 147 + "..."))


class FindingTheBrowser(unittest.TestCase):
    """docs-20: Edge is found where it installs on macOS and Linux, as the README's 'Chrome, Chromium or Edge' says."""

    def chrome_with(self, present=(), on_path=()):
        with patch.dict(os.environ, {"CHROME": ""}), \
                patch.object(render.os.path, "exists", side_effect=lambda p: p in present), \
                patch.object(render.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}" if n in on_path else None):
            return render.chrome()

    def test_docs_20_edge_is_found_on_macos(self):
        edge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        self.assertEqual(self.chrome_with(present=[edge]), edge)

    def test_docs_20_edge_is_found_on_linux(self):
        self.assertEqual(self.chrome_with(on_path=["microsoft-edge"]), "/usr/bin/microsoft-edge")
        self.assertEqual(self.chrome_with(on_path=["microsoft-edge-stable"]), "/usr/bin/microsoft-edge-stable")

    def test_docs_20_edge_is_found_in_both_program_files_on_windows(self):
        for edge in (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                     r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
            self.assertEqual(self.chrome_with(present=[edge]), edge)

    def test_chrome_is_preferred_and_chrome_overrides(self):
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        edge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        self.assertEqual(self.chrome_with(present=[chrome, edge]), chrome)
        self.assertEqual(self.chrome_with(on_path=["google-chrome", "microsoft-edge"]), "/usr/bin/google-chrome")
        with patch.dict(os.environ, {"CHROME": "/opt/browser"}), \
                patch.object(render.os.path, "exists", side_effect=lambda p: p == "/opt/browser"):
            self.assertEqual(render.chrome(), "/opt/browser")

    def test_no_browser_is_a_problem_with_advice(self):
        with self.assertRaises(render.Problem) as caught:
            self.chrome_with()
        self.assertIn("set CHROME", str(caught.exception))


class RelativePaths(unittest.TestCase):
    """correctness-7: render.py on its own takes a relative record path, as the README's example does, since the page
    is handed to the browser as a file URI, which only an absolute path has."""

    def test_correctness_7_the_readme_command_with_a_relative_record(self):
        # The README's command as typed, in a folder of its own, with CHROME naming a stand-in that writes the image.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "browser.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                "shot = next(a for a in sys.argv if a.startswith('--screenshot=')).split('=', 1)[1]\n"
                "assert sys.argv[-1].startswith('file:///'), sys.argv[-1]\n"
                f"Path(shot).write_bytes({PNG!r})\n", encoding="utf-8")
            if os.name == "nt":
                browser = tmp / "browser.cmd"
                browser.write_text(f'@echo off\r\n"{sys.executable}" "{tmp / "browser.py"}" %*\r\n', encoding="utf-8")
            else:
                browser = tmp / "browser"
                browser.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp / "browser.py"}" "$@"\n', encoding="utf-8")
                browser.chmod(0o755)
            (tmp / "shots").mkdir()
            (tmp / "shots" / "widget.json").write_text(json.dumps(RECORD), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPTS / "render.py"), "shots/widget.json",
                                   "--title", "After: Test - GET /api/widgets/42"],
                                  cwd=tmp, capture_output=True, text=True, encoding="utf-8", timeout=120,
                                  env={**os.environ, "CHROME": str(browser)})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((tmp / "shots" / "widget.png").read_bytes(), PNG)
            self.assertIn("After: Test - GET /api/widgets/42", (tmp / "shots" / "widget.html").read_text(encoding="utf-8"))
            self.assertEqual(Path(proc.stdout.strip()), (tmp / "shots" / "widget.png").resolve())

    def test_correctness_7_a_relative_out_path(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            Path("shots").mkdir()
            png, _ = drawn(RECORD, "shots/other.png")
            self.assertTrue(png.is_absolute())
            self.assertTrue(Path("shots/other.png").exists())
            self.assertTrue(Path("shots/other.html").exists())

    def test_main_with_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            Path("shots").mkdir()
            Path("shots/widget.json").write_text(json.dumps(RECORD), encoding="utf-8")
            argv = ["render.py", "shots/widget.json", "--out", "shots/again.png"]
            with patch.object(render, "chrome", return_value="chrome"), patch.object(render.subprocess, "run", FakeBrowser()), \
                    patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as printed:
                render.main()
            self.assertTrue(Path("shots/again.png").exists())
            self.assertTrue(Path(printed.getvalue().strip()).is_absolute())

    def test_a_browser_that_fails_is_a_problem(self):
        def failing(argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv)
        with tempfile.TemporaryDirectory() as tmp, patch.object(render, "chrome", return_value="chrome"), \
                patch.object(render.subprocess, "run", failing):
            with self.assertRaises(render.Problem) as caught:
                render.render(RECORD, Path(tmp) / "x.png")
        self.assertIn("status 1", str(caught.exception))


SECRET = "FAKEsecret123"
# Checked here without render.hidden(), so a test does not grade render.py by its own rule: the categories that draw
# nothing or reorder what is around them, and the default-ignorable code points outside them.
NOTHING = {"Cc", "Cf", "Zl", "Zp", "Cs", "Cn"}
IGNORABLE = set("\u034f\u115f\u1160\u17b4\u17b5\u180b\u180c\u180d\u180f\u3164\uffa0") | {chr(c) for c in range(0xFE00, 0xFE10)} \
    | {chr(c) for c in range(0xE0100, 0xE01F0)}
# And those that draw as an ordinary space without being one: the space separators but U+0020, and the blank Braille pattern.
BLANKS = {chr(c) for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) == "Zs"} - {" "} | {"\u2800"}


def body_rows(body, **record):
    """The rows render.rows draws, as text, for RECORD with this body and any other fields changed."""
    return [t for _, t in render.rows({**RECORD, **record, "body": body})]


def drawn_text(body, **record):
    """The rows drawn, one to a line. A row never holds a line break of its own: it is one row of the image."""
    rows = body_rows(body, **record)
    assert not any("\n" in t for t in rows), rows
    return "\n".join(rows)


def hidden_in(text):
    """The characters of drawn text that draw nothing or reorder the text: none may reach the image. A tab, and the
    line break drawn_text() puts between rows, are no such characters."""
    return [ch for i, ch in enumerate(text) if ch not in "\t\n" and (
        unicodedata.category(ch) in NOTHING or ch in BLANKS or
        (ch in IGNORABLE and not (ch in "\ufe0e\ufe0f" and i and unicodedata.category(text[i - 1]) == "So")))]


def seen(text):
    """What a reader makes of a drawn row: the characters that draw nothing dropped."""
    return "".join(ch for ch in text if ch == "\n" or not hidden_in(ch))


class DrawnAsReturned(unittest.TestCase):
    """D3: a JSON body is laid out again without decoding or re-serializing any token. Strings, numbers, true, false
    and null are copied as returned and only the whitespace between tokens changes, so the image draws the body as
    the API sent it, the form the sources check, and never a form json.loads and json.dumps made of it."""

    def test_d3_tokens_are_copied_as_returned(self):
        body = ('{"s":"a\\u0041\\n\\/\\"\\\\","n":1.2345678E7,"z":-0.0,"big":1e400,"t":true,"f":false,"x":null,'
                '"e":{ },"a":[ \r\n],"k":"one","k":"two"}')
        self.assertEqual(body_rows(body)[11:], [
            "{", '  "s": "a\\u0041\\n\\/\\"\\\\",', '  "n": 1.2345678E7,', '  "z": -0.0,', '  "big": 1e400,',
            '  "t": true,', '  "f": false,', '  "x": null,', '  "e": {},', '  "a": [],', '  "k": "one",', '  "k": "two"', "}"])

    def test_d3_the_layout_is_json_dumps_with_indent_2(self):
        values = [{"TypeScript": 1, "Python": 2}, [], {}, "x", 12, True, None, [1, [2, [3, {"a": None}]], {"b": {"c": [False]}}],
                  {"ü": "日本", "e": "🙂", "nested": {"list": [{}, [], [[]], {"k": [1, 2.5, -3]}]}}]
        for value in values:
            for separators in ((",", ":"), (", ", ": ")):
                body = json.dumps(value, separators=separators, ensure_ascii=False)
                expected = json.dumps(value, indent=2, ensure_ascii=False).splitlines()
                with self.subTest(body=body):
                    self.assertEqual(render.json_lines(body, keep=10**6), (expected, len(expected)))
                    self.assertEqual(body_rows(body)[11:], expected)
        spaced = ' \r\n\t{ "a" :\n[ 1 ,2 ] }\n '
        self.assertEqual(body_rows(spaced)[11:], ["{", '  "a": [', "    1,", "    2", "  ]", "}"])

    def test_d3_a_body_that_is_not_json_is_drawn_as_text(self):
        # NaN, Infinity and -Infinity are taken as json.loads takes them (see Round3Driver); no other spelling is.
        for body in ['{"a":1,}', "[1,]", "[1 2]", "nan", "[+Infinity]", "-NaN", "[infinity]", "[NaN,]", "Infinit",
                     '{"a" 1}', '"abc', "01", "{'a':1}", '"a\tb"', "[1]x", "[", "]", '{"a":}', "-", "1.", ".5", "1e",
                     "+1", "tru", "[,1]", '{"a":1 "b":2}', '{"a"}', "{NaN: 1}"]:
            with self.subTest(body=body):
                self.assertIsNone(render.json_lines(body))
                self.assertEqual(body_rows(body)[11:], [body])

    def test_d3_text_lines_split_at_lf_and_cr_lf_only(self):
        # Any other break is a control or separator character, drawn as its escape on the line it is on.
        self.assertEqual(body_rows("one\r\ntwo\nthree\rfour\u2028five\x0bsix\x85seven\x0ceight\u2029nine")[11:],
                         ["one", "two", "three\\u000dfour\\u2028five\\u000bsix\\u0085seven\\u000ceight\\u2029nine"])

    def test_d3_long_text_bodies_are_cut_at_forty_lines(self):
        rows = body_rows("\n".join(f"line {i}" for i in range(100)))
        self.assertEqual(rows[-2:], ["line 39", "... (60 more lines)"])

    def test_d3_no_nesting_is_too_deep_to_draw(self):
        # json.loads raised RecursionError here, which is no ValueError, so --shot failed.
        depth = 100_000
        started = time.monotonic()
        rows = body_rows("[" * depth + "]" * depth)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(rows[-2:], ["  " * 39 + "[", f"... ({2 * depth - 1 - 40} more lines)"])
        self.assertEqual(body_rows("[" * depth)[-1], "[" * 147 + "...")  # never closed: text

    def test_d3_a_large_body_is_counted_not_kept(self):
        body = json.dumps([{"id": i, "name": "x" * 20} for i in range(100_000)])
        started = time.monotonic()
        rows = body_rows(body)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(rows[-1], f"... ({100_000 * 4 + 2 - 40} more lines)")


class HiddenCharacters(unittest.TestCase):
    """D3: a character that draws nothing or reorders what is around it is drawn as its escape, in the body and in
    every other row, so no invisible character can stand between two characters of a secret in the image."""

    CASES = {"\u200b": "\\u200b", "\u200c": "\\u200c", "\u200d": "\\u200d", "\u00ad": "\\u00ad", "\u2060": "\\u2060",
             "\u2063": "\\u2063", "\ufeff": "\\ufeff", "\u180e": "\\u180e", "\u200e": "\\u200e", "\u200f": "\\u200f",
             "\u061c": "\\u061c", "\u202a": "\\u202a", "\u202b": "\\u202b", "\u202c": "\\u202c", "\u202d": "\\u202d",
             "\u202e": "\\u202e", "\u2066": "\\u2066", "\u2067": "\\u2067", "\u2068": "\\u2068", "\u2069": "\\u2069",
             "\u2028": "\\u2028", "\u2029": "\\u2029", "\x00": "\\u0000", "\x1b": "\\u001b", "\x7f": "\\u007f",
             "\x85": "\\u0085", "\r": "\\u000d", "\n": "\\u000a", "\u034f": "\\u034f", "\u3164": "\\u3164",
             "\ufe0f": "\\ufe0f", "\U000e0041": "\\U000e0041", "\U0001d173": "\\U0001d173", "\U000e01ef": "\\U000e01ef",
             "\U000effff": "\\U000effff", "\ud800": "\\ud800", "\udfff": "\\udfff",
             # Round 3: a space other than U+0020 and the blank Braille pattern draw as an ordinary space.
             "\u00a0": "\\u00a0", "\u2007": "\\u2007", "\u202f": "\\u202f", "\u3000": "\\u3000", "\u2800": "\\u2800"}

    def test_d3_each_hidden_character_is_drawn_as_its_escape(self):
        for ch, shown in self.CASES.items():
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                body = drawn_text('{"k": "FAKE' + ch + 'secret123"}')  # JSON where it may stand raw, else text
                header = drawn_text("", response_headers={"X-Echo": f"FAKE{ch}secret123"})
                for drawn in (body, header) if ch != "\n" else (header,):  # a text body breaks its lines at LF
                    self.assertIn(f"FAKE{shown}secret123", drawn)
                    self.assertEqual(hidden_in(drawn), [])
                    self.assertNotIn(SECRET, seen(drawn))

    def test_d3_every_row_is_made_visible(self):
        record = {**RECORD, "environment": "Prod\u202e", "collection": "Git\u200bHub",
                  "url": "https://api.example.com/x?k=FAKE\u200bsecret123", "request_headers": {"X-\u2060Key": "********"},
                  "response_headers": {"Set-\u200dCookie": "id=FAKE\u00adsecret123"}, "client_cert": "client\u202e.pem"}
        rows = [t for _, t in render.rows({**record, "body": "plain"})]
        self.assertEqual(rows[0], "Postman environment: Prod\\u202e    collection: Git\\u200bHub")
        self.assertEqual(rows[3], "> GET https://api.example.com/x?k=FAKE\\u200bsecret123")
        self.assertEqual(rows[4:6], ["> X-\\u2060Key: ********", "> client certificate: client\\u202e.pem"])
        self.assertEqual(rows[8], "< Set-\\u200dCookie: id=FAKE\\u00adsecret123")
        self.assertEqual(hidden_in("\n".join(rows)), [])
        with tempfile.TemporaryDirectory() as tmp:
            drawn({**record, "label": "After \u202eTest", "body": "plain"}, Path(tmp) / "r.png")
            page = (Path(tmp) / "r.html").read_text(encoding="utf-8")
        self.assertIn("After \\u202eTest - GET", page)
        self.assertEqual(hidden_in(page.replace("\n", "")), [])

    def test_d3_ordinary_text_is_drawn_as_it_is(self):
        # A no-break space was ordinary text here until round 3; it is drawn as its escape now (see Round3Driver).
        text = "日本語 ü café 🙂 ⚠\ufe0f e\u0301 ｱ 한국어 \u05e9\u05dc\u05d5\u05dd \u0627\u0644\u0639\u062f\u062f"
        self.assertEqual(body_rows(json.dumps({"k": text}, ensure_ascii=False))[-2], f'  "k": "{text}"')
        self.assertEqual(body_rows(text + "\ttab")[-1], text + "\ttab")

    def test_d3_a_variation_selector_stays_only_after_a_symbol(self):
        self.assertEqual(body_rows("ok ⚠\ufe0f ❤\ufe0e x\ufe0f ⚠\ufe0f\ufe0f")[-1], "ok ⚠\ufe0f ❤\ufe0e x\\ufe0f ⚠\ufe0f\\ufe0f")

    def test_d3_hidden_characters_count_their_escapes_cells(self):
        # 24 escapes of six cells fit in 147, so a run of them is cut like any long line, and quickly.
        started = time.monotonic()
        rows = body_rows("\u200b" * 2_000_000 + "x")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(rows[-1], "\\u200b" * 24 + "\\u2" + "...")
        self.assertEqual(render.cells(rows[-1]), 150)


class Round2Attacks(unittest.TestCase):
    """The attacks that got past both sources in round 2, each drawn by render.rows from the body the API sent: the
    secret is drawn only in the form the API sent it, which the sources check, and never in a form decoding or
    re-serializing made of it, or with an invisible character the image hides."""

    def assertDrawnAsSent(self, body, secret, token, **record):
        drawn = drawn_text(body, **record)
        self.assertNotIn(secret, drawn)
        self.assertNotIn(secret, seen(drawn))
        self.assertEqual(hidden_in(drawn), [])
        self.assertIn(token, drawn)

    def test_driver_numeric_secret_in_double_form(self):
        # Jackson writes 12345678 as a double; json.dumps drew 12345678.0, the secret.
        self.assertDrawnAsSent('{"n":1.2345678E7}', "12345678", '"n": 1.2345678E7')
        self.assertDrawnAsSent('{"n":1234567.8e1}', "12345678", '"n": 1234567.8e1')

    def test_driver_secret_with_a_backslash_is_not_re_escaped(self):
        # json.dumps wrote the decoded newline and quote back as \n and \", which is the secret as typed.
        self.assertDrawnAsSent('{"k":"abc\\u000adefg"}', "abc\\ndefg", '"k": "abc\\u000adefg"')
        self.assertDrawnAsSent('{"k":"pa\\u0022ssword"}', 'pa\\"ssword', '"k": "pa\\u0022ssword"')
        self.assertDrawnAsSent('{"k":"p\\u005css"}', "p\\\\ss", '"k": "p\\u005css"')

    def test_driver_format_characters_inside_the_secret(self):
        for name, inside in (("zero-width space", "FAKE\u200bsecret123"), ("soft hyphen", "FAKE\u00adsecret123"),
                             ("word joiner", "\u2060".join(SECRET)), ("byte order mark", "FAKE\ufeffsecret123"),
                             ("grapheme joiner", "FAKE\u034fsecret123"), ("tag", "FAKE\U000e0020secret123")):
            escaped = json.dumps(inside)  # as the API sends it escaped
            raw = json.dumps(inside, ensure_ascii=False)  # and raw
            shown = "".join((f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}") if hidden_in(ch) else ch
                            for ch in inside)
            with self.subTest(name):
                self.assertNotIn(SECRET, shown)
                self.assertDrawnAsSent('{"k":' + escaped + "}", SECRET, '"k": ' + escaped)
                self.assertDrawnAsSent('{"k":' + raw + "}", SECRET, f'"k": "{shown}"')
                self.assertDrawnAsSent(f"key={inside}", SECRET, f"key={shown}")  # a text body

    def test_driver_rlo_override_reversing_the_secret(self):
        backwards = SECRET[::-1]  # 321tercesEKAF, which the override draws as FAKEsecret123
        reversed_ = "\u202e" + backwards + "\u202c"
        self.assertDrawnAsSent('{"k":' + json.dumps(reversed_) + "}", SECRET, f'"k": "\\u202e{backwards}\\u202c"')
        self.assertDrawnAsSent('{"k":"' + reversed_ + '"}', SECRET, f'"k": "\\u202e{backwards}\\u202c"')
        self.assertDrawnAsSent('{"k":"\u2067' + backwards + '\u2069"}', SECRET, f'"k": "\\u2067{backwards}\\u2069"')
        self.assertDrawnAsSent("\u202d\u202e" + backwards, SECRET, f"\\u202d\\u202e{backwards}")  # a text body

    def test_driver_lone_surrogate_draws_and_the_shot_completes(self):
        # json.loads made a str UTF-8 cannot hold, and writing the page failed after cutting it to nothing.
        for body in ('{"k":"\\ud800 hello"}', '{"k":"\\udc00"}', '["\\ud83d"]'):
            token = body[body.index("\\"):body.index("\\") + 6]
            with self.subTest(body=body):
                self.assertDrawnAsSent(body, SECRET, token)
                with tempfile.TemporaryDirectory() as tmp:
                    png, _ = drawn({**RECORD, "body": body}, Path(tmp) / "r.png")
                    self.assertEqual(png.read_bytes(), PNG)
                    self.assertIn(token, (Path(tmp) / "r.html").read_text(encoding="utf-8"))
        # A lone surrogate standing raw in the record, in the body or a header, is drawn as its escape too.
        with tempfile.TemporaryDirectory() as tmp:
            drawn({**RECORD, "body": "a\ud800b", "response_headers": {"X": "\udcff"}}, Path(tmp) / "r.png")
            page = (Path(tmp) / "r.html").read_text(encoding="utf-8")
        self.assertIn("a\\ud800b", page)
        self.assertIn("&lt; X: \\udcff", page)

    def test_driver_nested_json_string_is_drawn_as_sent(self):
        # Encoded twice: a documented limit. The image keeps every escape the API wrote.
        php_twice = '{"inner":"{\\"k\\":\\"abc+def\\\\\\/ghi12==\\"}"}'
        self.assertDrawnAsSent(php_twice, "abc+def/ghi12==", '"inner": "{\\"k\\":\\"abc+def\\\\\\/ghi12==\\"}"')
        u_inside = '{"inner":"{\\"k\\":\\"\\\\u0046AKEsecret123\\"}"}'
        self.assertDrawnAsSent(u_inside, SECRET, '"inner": "{\\"k\\":\\"\\\\u0046AKEsecret123\\"}"')
        non_ascii = '{"inner":"{\\"k\\":\\"fake-\\\\u00fc-1234567\\"}"}'
        self.assertDrawnAsSent(non_ascii, "fake-ü-1234567", '"inner": "{\\"k\\":\\"fake-\\\\u00fc-1234567\\"}"')

    def test_driver_escape_of_an_escape_is_drawn_as_sent(self):
        self.assertDrawnAsSent('{"k":"\\\\u0046AKEsecret123"}', SECRET, '"k": "\\\\u0046AKEsecret123"')
        self.assertDrawnAsSent('{"k":"%5Cu0046AKEsecret123"}', SECRET, '"k": "%5Cu0046AKEsecret123"')

    def test_driver_html_character_references_stay_text(self):
        for body in ('{"k":"&#70;AKEsecret123"}', '{"k":"&#x46;&#x41;KEsecret123"}', '{"k":"\\u0026#70;AKEsecret123"}',
                     '{"k":"abc&plus;def&sol;ghi12&equals;&equals;"}'):
            token = body[6:-2]  # the string as sent, between its quotes
            with self.subTest(body=body):
                self.assertDrawnAsSent(body, SECRET, token)
                with tempfile.TemporaryDirectory() as tmp:
                    drawn({**RECORD, "body": body}, Path(tmp) / "r.png")
                    page = (Path(tmp) / "r.html").read_text(encoding="utf-8")
                self.assertIn(token.replace("&", "&amp;"), page)  # the browser shows the reference, not what it stands for
                self.assertNotIn(SECRET, page)
                self.assertNotIn("abc+def/ghi12==", page)

    def test_driver_location_header_is_drawn_as_sent(self):
        location = "https://example.com/?k=%5Cu0046%5Cu0041%5Cu004BEsecret123"
        self.assertDrawnAsSent("", SECRET, f"< Location: {location}", response_headers={"Location": location})
        self.assertDrawnAsSent("", SECRET, "< Location: https://example.com/?k=FAKE\\u200bsecret123",
                               response_headers={"Location": "https://example.com/?k=FAKE\u200bsecret123"})

    def test_driver_homoglyphs_are_drawn_as_sent_a_known_limit(self):
        # Not fixed here: a Cyrillic \u0435 or a fullwidth form is ordinary text, drawn as the API sent it. It reads like
        # the secret; the sources or SECURITY.md have to answer it.
        self.assertDrawnAsSent('{"k":"FAKEs\u0435cret123"}', SECRET, '"k": "FAKEs\u0435cret123"')
        self.assertDrawnAsSent('{"k":"\\uff26AKEsecret123"}', SECRET, '"k": "\\uff26AKEsecret123"')
        self.assertDrawnAsSent('{"k":"\uff26AKEsecret123"}', SECRET, '"k": "\uff26AKEsecret123"')


TALL_MARKS = "\u0305\u033f\u0304" * 27 + "\u0331\u0347" * 30  # overlines stacked up, and lines stacked down
WIDE_LINES = ("OK " + "\u2e3b" * 45 + " ERROR: assertion failed, build broken",  # each counted as fitting in 150 cells
              "\ufdfd" * 20 + " but the test FAILED", "\U0001242b" * 60 + " FAIL")


class Round3Driver(unittest.TestCase):
    """The defects round 3 found in what render.py draws and how frame.html draws it, each closed here. The ones only a
    browser can show are checked in a real one, in InTheBrowser below."""

    def test_driver_a_placeholder_in_the_title_is_drawn_as_text(self):
        # An environment named 'E2E {{body}}' pulled the whole body into the title bar: {{title}} was filled first,
        # then every {{body}}, the one now in the title too.
        page = render.page("E2E {{body}} {{title}}", "<span>{{title}} {{body}}</span>")
        self.assertIn('<div class="title">E2E {{body}} {{title}}</div>', page)
        self.assertIn("<pre><span>{{title}} {{body}}</span></pre>", page)
        self.assertEqual(page.count("<span>"), 1)
        page = render.page(r"a\1 \g<0> $1 $&", r"<b>\g<0> \\ $1</b>")  # and nothing in either is a replacement pattern
        self.assertIn(r'<div class="title">a\1 \g&lt;0&gt; $1 $&amp;</div>', page)
        self.assertIn(r"<pre><b>\g<0> \\ $1</b></pre>", page)
        with tempfile.TemporaryDirectory() as tmp:
            record = {**RECORD, "label": "E2E {{body}}", "body": "the body {{title}}"}
            drawn(record, Path(tmp) / "r.png")
            page = (Path(tmp) / "r.html").read_text(encoding="utf-8")
        self.assertIn('<div class="title">E2E {{body}} - GET /repos/x/y/languages</div>', page)
        self.assertEqual(page.count("the body {{title}}"), 1)
        self.assertEqual(page.count('class="row"'), len(render.rows(record)))

    def test_driver_each_row_is_a_block_of_its_own(self):
        self.assertEqual(render.markup([("dim", "a <b>"), (None, ""), ("bad", "x & y")]),
                         '<span class="row" style="color:#a1a1aa">a &lt;b&gt;</span><span class="row"></span>'
                         '<span class="row" style="color:#ef7b85;font-weight:700">x &amp; y</span>')
        with tempfile.TemporaryDirectory() as tmp:
            drawn(RECORD, Path(tmp) / "r.png")
            page = (Path(tmp) / "r.html").read_text(encoding="utf-8")
        pre = page[page.index("<pre>") + 5:page.index("</pre>")]
        self.assertNotIn("\n", pre)  # a line break between two rows would be drawn as a row of its own
        self.assertEqual(re.findall(r'<span class="row"(?: style="[^"]*")?>[^<]*</span>', pre),
                         [pre[m.start():m.end()] for m in re.finditer(r"<span.*?</span>", pre)])
        self.assertEqual(pre.count('class="row"'), len(render.rows(RECORD)))

    def test_driver_the_frame_clips_each_row_and_draws_it_left_to_right(self):
        # unicode-bidi is not inherited, and a block starts a paragraph of its own, so the pre, the title and each row
        # all need the override. A row is the window's width and ROW high, and clips what it draws to that box.
        rules = dict(re.findall(r"^([.\w]+)\{(.*)\}$", render.FRAME.read_text(encoding="utf-8"), re.M))
        css = {selector: dict(d.split(":", 1) for d in body.split(";") if d) for selector, body in rules.items()}
        ltr = {"direction": "ltr", "unicode-bidi": "bidi-override"}
        cut = {"white-space": "pre", "overflow": "hidden", "text-overflow": "ellipsis"}
        self.assertLessEqual({**ltr, "display": "block", "height": f"{render.ROW}px", "overflow": "hidden",
                              "text-overflow": "ellipsis"}.items(), css[".row"].items())
        self.assertLessEqual({**ltr, "white-space": "pre"}.items(), css["pre"].items())
        self.assertLessEqual({**ltr, **cut}.items(), css[".title"].items())

    def test_driver_a_text_body_loses_only_the_line_breaks_at_its_ends(self):
        # str.strip() took control characters, separators and the first line's indentation with it.
        cases = {"\x1f": ["\\u001f"], "PASS\x1e": ["PASS\\u001e"], "\u2028FAIL": ["\\u2028FAIL"],
                 "FAIL\u2029": ["FAIL\\u2029"], "\x85": ["\\u0085"], "\u3000": ["\\u3000"], "x\x1c\x1d": ["x\\u001c\\u001d"],
                 "    indented first line\nsecond": ["    indented first line", "second"], "\ttab first\n": ["\ttab first"],
                 "   ": ["   "], "trailing   \n": ["trailing   "], "\r\n\nbody\r\n\n": ["body"], "a\n\n\nb\n": ["a", "", "", "b"],
                 "a\r": ["a\\u000d"], "a\r\r\n": ["a\\u000d"], "\r": ["\\u000d"],
                 # Line breaks alone are no empty body: they are drawn as their escapes.
                 "\n": ["\\u000a"], "\r\n": ["\\u000d\\u000a"], "\n\n": ["\\u000a\\u000a"], "": ["(empty body)"]}
        for body, lines in cases.items():
            with self.subTest(body=body):
                self.assertEqual(body_rows(body)[11:], lines)
        self.assertEqual(body_rows("\n" * 3 + "\n".join(f"line {i}" for i in range(100)) + "\n" * 3)[-2:],
                         ["line 39", "... (60 more lines)"])

    def test_driver_a_text_body_of_many_line_breaks_is_quick(self):
        started = time.monotonic()
        self.assertEqual(body_rows("x" + "\n" * 1_000_000 + "y")[-1], "... (999961 more lines)")
        self.assertEqual(body_rows("\r\n" * 1_000_000)[-1], "\\u000d\\u000a" * 12 + "\\u0" + "...")
        self.assertLess(time.monotonic() - started, 10)

    def test_driver_blank_looking_characters_are_drawn_as_escapes(self):
        self.assertEqual(len(BLANKS), 17)  # U+00A0, U+1680, U+2000 to U+200A, U+202F, U+205F, U+3000 and U+2800
        for ch in sorted(BLANKS):
            shown = f"\\u{ord(ch):04x}"
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                self.assertEqual(body_rows(f"x{ch}y")[-1], f"x{shown}y")
                self.assertEqual(body_rows(f'{{"k": "x{ch}y"}}')[-2], f'  "k": "x{shown}y"')
                self.assertIn(f"> X-Echo: x{shown}y", body_rows("", request_headers={"X-Echo": f"x{ch}y"}))
        self.assertEqual(body_rows("x\u2800\u2800\u2800y")[-1], "x\\u2800\\u2800\\u2800y")
        self.assertEqual(body_rows("a b\tc")[-1], "a b\tc")  # U+0020 and the tab are drawn as they are

    def test_driver_nan_and_infinity_are_laid_out_as_json(self):
        # Some APIs written in Python send NaN; the body was drawn as one raw line, cut at 150 cells.
        body = '{"a": NaN, "b": [1,2,3], "c": {"d": -Infinity, "e": Infinity}}'
        self.assertEqual(body_rows(body)[11:], json.dumps(json.loads(body), indent=2).splitlines())
        self.assertEqual(body_rows("[NaN,-Infinity,Infinity]")[11:], ["[", "  NaN,", "  -Infinity,", "  Infinity", "]"])
        self.assertEqual(render.json_lines("NaN"), (["NaN"], 1))
        self.assertEqual(render.json_lines("-Infinity"), (["-Infinity"], 1))
        self.assertEqual(body_rows('{"n":-0.0,"x":NaN,"y":1.2345678E7}')[11:],  # copied as written
                         ["{", '  "n": -0.0,', '  "x": NaN,', '  "y": 1.2345678E7', "}"])


def png_rows(data):
    """The pixel rows of an image the browser wrote, as bytes: as much of PNG as its captures use (8 bits, RGB or
    RGBA, not interlaced), so the test needs no imaging library."""
    assert data[:8] == PNG, data[:8]
    pos, idat, header = 8, b"", None
    while pos < len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        kind, chunk = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat += chunk
    width, height, depth, color, _, _, interlace = header
    assert depth == 8 and color in (2, 6) and not interlace, header
    bpp = 3 if color == 2 else 4
    stride, raw, rows, prev = width * bpp, zlib.decompress(idat), [], bytearray(width * bpp)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride) if kind else ():
            a, b, c = (line[i - bpp] if i >= bpp else 0), prev[i], (prev[i - bpp] if i >= bpp else 0)
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return rows


# Added to a page render() wrote, it reads what the browser laid out: each element in the pre, with its box and the
# style it was given, and for each target string the left edge of each of its characters and the box that holds it.
MEASURE = """<script>(() => {
  const style = e => { const s = getComputedStyle(e), r = e.getBoundingClientRect()
    return { text: e.textContent, display: s.display, top: r.top, height: r.height, right: r.right, overflowX: s.overflowX,
             overflowY: s.overflowY, textOverflow: s.textOverflow, bidi: s.unicodeBidi, direction: s.direction,
             scrollWidth: e.scrollWidth, clientWidth: e.clientWidth } }
  const at = target => {
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n; (n = walk.nextNode());) {
      const i = n.data.indexOf(target)
      if (i < 0) continue
      const range = document.createRange(), lefts = []
      for (let k = 0; k < target.length; k++) {
        range.setStart(n, i + k); range.setEnd(n, i + k + 1); lefts.push(range.getBoundingClientRect().left)
      }
      let box = n.parentElement
      while (getComputedStyle(box).display === 'inline') box = box.parentElement
      return { lefts, box: style(box) }
    }
    return null
  }
  const out = { width: innerWidth, rows: [...document.querySelectorAll('pre > *')].map(style),
                at: Object.fromEntries(TARGETS.map(t => [t, at(t)])) }
  document.body.setAttribute('data-measured', btoa(unescape(encodeURIComponent(JSON.stringify(out)))))
})()</script>"""


class InTheBrowser(unittest.TestCase):
    """Round 3: what the browser draws from the page render() writes, checked in the real browser, since only it can
    show it. Skipped where no browser is found or it cannot capture a page."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.browser = render.chrome()
        except render.Problem as e:
            raise unittest.SkipTest(str(e))
        cls.tmp = tempfile.TemporaryDirectory()
        try:
            render.render(RECORD, Path(cls.tmp.name) / "probe.png")
        except render.Problem as e:
            cls.tmp.cleanup()
            raise unittest.SkipTest(f"the browser cannot capture a page here: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def shoot(self, record, name):
        """render.render in the real browser: the image and the window size it was captured at."""
        argv, real = [], subprocess.run

        def run(command, **kwargs):
            argv[:] = command
            return real(command, **kwargs)
        with patch.object(render.subprocess, "run", run):
            png = render.render(record, Path(self.tmp.name) / f"{name}.png")
        size = next(a for a in argv if a.startswith("--window-size=")).split("=", 1)[1]
        return png, tuple(int(n) for n in size.split(","))

    def measure(self, record, name, targets=()):
        """The layout the browser makes of the page render.render writes for record, at the window size it captures."""
        png, (width, height) = self.shoot(record, name)
        page = png.with_name(f"{name}-measured.html")
        page.write_bytes(png.with_suffix(".html").read_bytes() + MEASURE.replace("TARGETS", json.dumps(list(targets))).encode())
        proc = subprocess.run([self.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                               f"--window-size={width},{height}", "--dump-dom", page.as_uri()],
                              capture_output=True, timeout=90)
        found = re.search(rb'data-measured="([A-Za-z0-9+/=]+)"', proc.stdout)
        self.assertIsNotNone(found, proc.stderr[-2000:])
        laid = json.loads(base64.b64decode(found[1]).decode("utf-8"))
        self.assertEqual(laid["width"], width)
        return laid

    def test_driver_each_row_is_a_clipped_block_of_one_row(self):
        record = {**RECORD, "body": WIDE_LINES[0] + "\nnext"}
        laid, drawn_rows = self.measure(record, "rows"), render.rows(record)
        self.assertEqual(len(laid["rows"]), len(drawn_rows))
        for i, (row, (_, text)) in enumerate(zip(laid["rows"], drawn_rows)):
            with self.subTest(row=i):
                self.assertEqual(row["text"], text)
                self.assertEqual(row["display"], "block")
                self.assertEqual((row["top"], row["height"]), (render.TITLE_BAR + 10 + i * render.ROW, render.ROW))
                self.assertEqual((row["overflowX"], row["overflowY"], row["textOverflow"]), ("hidden", "hidden", "ellipsis"))
                self.assertEqual((row["bidi"], row["direction"]), ("bidi-override", "ltr"))
                self.assertLessEqual(row["right"], laid["width"] - 18)  # inside the window and its padding

    def test_driver_a_line_drawn_wider_than_counted_ends_in_an_ellipsis(self):
        # Counted as fitting, so clip() adds no '...', but drawn wider than 150 cells: the line ran past the window's
        # edge with nothing to show it was cut. The browser now cuts it inside the window and marks the cut.
        for n, line in enumerate(WIDE_LINES):
            with self.subTest(line=line[:8]):
                record = {**RECORD, "body": line}
                self.assertEqual(render.rows(record)[-1][1], line)
                box = self.measure(record, f"wide{n}", [line[-4:]])["at"][line[-4:]]["box"]
                if box["scrollWidth"] <= box["clientWidth"]:
                    self.skipTest("this browser's fonts draw the line no wider than it is counted")
                self.assertEqual((box["overflowX"], box["textOverflow"]), ("hidden", "ellipsis"))
                self.assertEqual((box["display"], box["height"]), ("block", render.ROW))
                self.assertLessEqual(box["right"], box["clientWidth"] + 18)

    def test_driver_right_to_left_letters_keep_their_order(self):
        # '"\u05d0": 1,' was drawn '"1 :"\u05d0,' and 'result \u05d1 = 7' as 'result 7 = \u05d1': the image showed another
        # order than the body and the record. Every character is now drawn right of the one before it.
        targets = ['"\u05d0": 1,', '"amount": "\u05d1 100 200"', "result \u05d1 = 7",
                   "\u0627\u0644\u0639\u062f\u062f \u0661\u0662\u0663 = 123", "\u05d1\u05d3\u05d9\u05e7\u05d4 1 2 - GET"]
        record = {**RECORD, "label": "\u05d1\u05d3\u05d9\u05e7\u05d4 1 2", "body": '{"\u05d0": 1, "amount": "\u05d1 100 200"}',
                  "response_headers": {"X-Note": targets[2], "X-Arabic": targets[3]}}
        laid = self.measure(record, "bidi", targets)
        for target in targets:
            with self.subTest(target=target):
                lefts = laid["at"][target]["lefts"]
                self.assertTrue(all(a < b for a, b in zip(lefts, lefts[1:])), lefts)

    def test_driver_stacked_marks_stay_in_their_row(self):
        # Hundreds of combining marks on three characters painted a column over the rows around them, the status
        # code among them. The image now differs from the same line without the marks only inside that line's row.
        tall = {**RECORD, "status": 404, "body": "first line\n" + " " * 7 + ("\u2588" + TALL_MARKS) * 3 + "\nlast line"}
        flat = {**RECORD, "status": 404, "body": "first line\n" + " " * 7 + "\u2588" * 3 + "\nlast line"}
        k = len(render.rows(tall)) - 2  # the row of marks
        (tall_png, tall_size), (flat_png, flat_size) = self.shoot(tall, "tall"), self.shoot(flat, "flat")
        self.assertEqual(tall_size, flat_size)
        a, b = png_rows(tall_png.read_bytes()), png_rows(flat_png.read_bytes())
        top = render.TITLE_BAR + 10 + k * render.ROW
        self.assertEqual([y for y in range(len(a)) if not top <= y < top + render.ROW and a[y] != b[y]], [])
        self.assertNotEqual(a[top:top + render.ROW], b[top:top + render.ROW])  # the marks are drawn, in their row



class ObjectReplacement(unittest.TestCase):
    """U+FFFC, which text pasted from rich-text apps carries, draws as a space in many fonts: it is drawn as its escape."""

    def test_the_object_replacement_character_is_drawn_as_its_escape(self):
        self.assertTrue(render.hidden("\ufffc"))
        drawn = "\n".join(t for _, t in render.rows({**RECORD, "body": "a\ufffcb"}))
        self.assertIn("a\\ufffcb", drawn)
        self.assertNotIn("\ufffc", drawn)

if __name__ == "__main__":
    unittest.main()
