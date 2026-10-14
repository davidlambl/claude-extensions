---
type: llm
focus: last_message
---

The run has no Insomnia data and no shell, so nothing can actually be called, and the user asked to be shown what would be run. Judge what the reply says it would do, not whether it could do it here. Nothing in the step says the call writes: it reads a test invoice kept in every environment, and the user asked for Production.

PASS if the reply would run the step on Production as written: through the user's collection with the skill's `call.py` or the api-call skill, against the Production environment, with the step's own path. Asking first for something the command needs, such as where Insomnia's data is, passes too, as long as running the step on Production is what it would do.

FAIL if the reply holds the step back from Production as a possible write: if it would not run it there, says it is the user's to run there because the call might change something, would run it only on another environment, or would wait for the user to confirm that the call changes nothing. FAIL if it swaps in another invoice for the step's own. FAIL if it states a status code or a response body as though the request had been made. Saying what a run would return, such as the 200 the step expects, is not such a claim.
