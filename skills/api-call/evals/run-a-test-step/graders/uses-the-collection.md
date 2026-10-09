---
type: llm
focus: last_message
---

The run has no Insomnia data and no shell, so the check cannot actually be performed. Judge only how the reply proposes to do it.

PASS if the reply proposes running the skill's own script through the user's collection: it names `call.py` or the api-call skill, and shows or describes going via Insomnia with an environment and collection, an expected status, and a screenshot. Asking the user for the environment and collection names first also passes, as long as the collection is the route it proposes.

FAIL if the reply proposes writing ad-hoc code or a shell command against the endpoint, such as `curl`, `requests`, `httpx` or `Invoke-WebRequest`, instead of going through the collection. FAIL if it states a status code or a response body as though the request had been made.
