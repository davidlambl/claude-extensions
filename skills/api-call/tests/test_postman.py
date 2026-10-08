"""The Postman source's request building, run with: python3 -m unittest discover -s skills/api-call/tests"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "api-call" / "scripts" / "sources"))
import postman  # noqa: E402

ENV = {"name": "Test", "values": [
    {"key": "baseUrl", "value": "https://api.example.com/", "type": "default", "enabled": True},
    {"key": "apiKey", "value": "sekret-123456", "type": "secret", "enabled": True},
    {"key": "off", "value": "never", "enabled": False},
]}


def collection(auth=None, folders=(), variables=None):
    return {"info": {"name": "Example"}, "auth": auth, "item": list(folders),
            "variable": variables if variables is not None else [{"key": "version", "value": "v2"}, {"key": "baseUrl", "value": "https://collection"}]}


def folder(name, auth):
    return {"name": name, "item": [], "auth": auth}


APIKEY_HEADER = {"type": "apikey", "apikey": [{"key": "key", "value": "X-Api-Key"}, {"key": "value", "value": "{{apiKey}}"}, {"key": "in", "value": "header"}]}
APIKEY_QUERY = {"type": "apikey", "apikey": [{"key": "key", "value": "api key"}, {"key": "value", "value": "{{apiKey}}"}, {"key": "in", "value": "query"}]}
BEARER = {"type": "bearer", "bearer": [{"key": "token", "value": "{{apiKey}}"}]}
BASIC = {"type": "basic", "basic": [{"key": "username", "value": "amy"}, {"key": "password", "value": "{{apiKey}}"}]}


class Variables(unittest.TestCase):
    def test_environment_over_collection_then_extra_and_secrets(self):
        variables, secrets = postman.load_variables(collection(), ENV, {"token": "t0ken-abcdef"})
        self.assertEqual(variables["baseUrl"], "https://api.example.com/")
        self.assertEqual(variables["version"], "v2")
        self.assertEqual(variables["token"], "t0ken-abcdef")
        self.assertNotIn("off", variables)
        self.assertEqual(secrets, ["sekret-123456", "t0ken-abcdef"])

    def test_templates_resolve_through_each_other(self):
        self.assertEqual(postman.resolve("{{a}}/x", {"a": "{{ b }}", "b": "y"}), "y/x")

    def test_unknown_and_dynamic_names_raise(self):
        with self.assertRaises(postman.Problem):
            postman.resolve("{{nope}}", {})
        with self.assertRaises(postman.Problem):
            postman.resolve("{{$guid}}", {})


class Build(unittest.TestCase):
    def build(self, auth=None, folders=(), folder=None, path="/items/1"):
        return postman.build(collection(auth, folders), ENV, {}, folder, "baseUrl", path)

    def test_plain_get(self):
        b = self.build(path="items/1")
        self.assertEqual(b["url"], "https://api.example.com/items/1")
        self.assertEqual(b["shown_url"], b["url"])
        self.assertEqual(b["headers"], {"Accept": "application/json", "User-Agent": "api-call"})
        self.assertEqual((b["auth"], b["folder"], b["path"], b["secrets"]), ("none", None, "/items/1", ["sekret-123456"]))

    def test_api_key_in_header_is_sent_and_masked(self):
        b = self.build(APIKEY_HEADER)
        self.assertEqual(b["headers"]["X-Api-Key"], "sekret-123456")
        self.assertEqual(b["shown_headers"]["X-Api-Key"], "********")
        self.assertEqual(b["auth"], "API key in header X-Api-Key")

    def test_api_key_in_query_is_sent_and_masked(self):
        b = self.build(APIKEY_QUERY, path="/items?x=1")
        self.assertEqual(b["url"], "https://api.example.com/items?x=1&api+key=sekret-123456")
        self.assertEqual(b["shown_url"], "https://api.example.com/items?x=1&api+key=********")
        self.assertNotIn("sekret", b["shown_url"])

    def test_bearer_and_basic(self):
        b = self.build(BEARER)
        self.assertEqual((b["headers"]["Authorization"], b["shown_headers"]["Authorization"]), ("Bearer sekret-123456", "Bearer ********"))
        b = self.build(BASIC)
        self.assertTrue(b["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual(b["shown_headers"]["Authorization"], "Basic ********")
        self.assertIn(b["headers"]["Authorization"][6:], b["secrets"])
        self.assertEqual(b["auth"], "basic auth as amy")

    def test_folder_auth(self):
        b = self.build(folders=[folder("v1", BEARER), folder("v2", None)])
        self.assertEqual((b["folder"], b["auth"]), ("v1", "bearer token"))
        with self.assertRaises(postman.Problem):
            self.build(folders=[folder("v1", BEARER), folder("v2", APIKEY_HEADER)])
        b = self.build(folders=[folder("v1", BEARER), folder("v2", APIKEY_HEADER)], folder="v2")
        self.assertEqual(b["auth"], "API key in header X-Api-Key")
        with self.assertRaises(postman.Problem):
            self.build(folder="v3")

    def test_collection_auth_wins_over_folders(self):
        b = self.build(BASIC, folders=[folder("v1", BEARER)])
        self.assertEqual((b["folder"], b["auth"]), (None, "basic auth as amy"))

    def test_https_and_base_url_required(self):
        with self.assertRaises(postman.Problem):
            postman.build(collection(), {"values": [{"key": "baseUrl", "value": "http://x"}]}, {}, None, "baseUrl", "/")
        with self.assertRaises(postman.Problem):
            postman.build(collection(variables=[]), {"values": []}, {}, None, "baseUrl", "/")

    def test_unsupported_auth(self):
        with self.assertRaises(postman.Problem):
            self.build({"type": "oauth2"})


if __name__ == "__main__":
    unittest.main()
