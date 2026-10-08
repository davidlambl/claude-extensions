"""What render.py draws for a record, run with: python3 -m unittest discover -s skills/api-call/tests"""
import json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "api-call" / "scripts"))
import render  # noqa: E402

RECORD = {"source": "postman", "environment": "GitHub API", "collection": "GitHub", "folder": None, "method": "GET",
          "path": "/repos/x/y/languages", "url": "https://api.github.com/repos/x/y/languages", "auth": "none",
          "request_headers": {"Accept": "application/json", "X-Api-Key": "********"}, "client_cert": "client.pem",
          "status": 200, "elapsed_ms": 81, "sent": "2026-10-08T16:01:52.869-04:00", "sent_utc": "2026-10-08T20:01:52.869Z",
          "response_headers": {"Content-Type": "application/json; charset=utf-8"}, "body": '{"TypeScript": 1, "Python": 2}'}


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


if __name__ == "__main__":
    unittest.main()
