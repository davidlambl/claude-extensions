---
type: llm
focus: last_message
---

PASS if the reply gives a Python function that calls the GitHub API with `requests` and returns a dict.

FAIL if it gives no function, or if instead of answering it redirects the user to running a step through an Insomnia or Postman collection.
