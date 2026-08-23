# Protocol: Ask the Runbooks

Answer a "how do we do X here?" question from the runbook library, with a
citation, or say plainly that the library does not cover it.

Use this whenever the answer should come from how *this team* works rather than
from general knowledge: process, policy, sequence-of-operations, "who decides",
"what do we do when".

## Steps

1. **Restate the question in one line.** If it bundles two questions, answer
   them in order rather than blending them.
2. **Search.** Call `rb_search` with the substantive words of the question, not
   the whole sentence. If nothing scores, try one synonym set, then stop.
3. **Read the best match in full** with `rb_get_runbook`. Do not answer from a
   search excerpt — excerpts are 180 characters chosen by a keyword ranker, and
   the qualifier that changes the answer is routinely outside them.
4. **Answer from the runbook**, leading with the answer. Quote the specific
   sentence that carries the rule when there is one.
5. **Cite it**: the runbook title and id, every time.
6. **Say what the runbook does not say.** If the question has a part the
   runbook does not cover, name that part explicitly and answer it separately,
   flagged as general reasoning rather than team policy.

## When the library has nothing

Say so. "The runbook library has nothing on X" is a useful, correct answer, and
it is the one that gets a runbook written. Do not fill the gap with a plausible
process — an invented policy that sounds like this team's is worse than no
answer, because it will be repeated.

## If you cannot read a runbook

`rb_search` and `rb_get_runbook` are MCP tools and run host-side, so they work
whenever the server is connected. If instead you are trying to read a file
under `protocols/` and the file tool refuses or cannot find it, try `bash`
(`cat protocols/…`) before concluding it is missing — depending on how this
deployment is built, the file tools may not reach that directory even though
the file is there. If both fail, say which path you tried.

## Output shape

- One-paragraph answer, first.
- The steps or rules, if the question is procedural.
- `Source: <title> (<id>)`.
- `Not covered: …`, only when something genuinely is not.
