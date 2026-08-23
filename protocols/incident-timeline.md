# Protocol: Incident Timeline

Reconstruct what happened during one incident, as a cited, timestamped table —
evidence first, inference clearly labelled, and a stated impact window.

Use this whenever someone asks "what actually happened" about an incident, a
degradation, or a bad release: a postmortem draft, a review prep, a "walk me
through it" ask. The `{incident}` the task hands you may be an id, a service
name plus a date, or a sentence — start from whatever it is and say in your
first line how you interpreted it.

## Steps

1. **Restate the incident in one line**, including the interpretation you made
   of the input you were given. If it is ambiguous enough that you could be
   reconstructing the wrong event, say so before you spend a tool call.
2. **Read the team's incident rules** with `rb_get_runbook` for `rb-003`
   ("Declaring and running an incident"). It defines what must exist by the end
   of an incident, and this protocol exists to produce exactly that. Follow it
   over anything remembered — including over this file, if the two ever drift.
3. **Gather the evidence in the workspace.** List the working directory and read
   what is there — attached logs, exports, chat transcripts, alert dumps, prior
   notes. Use `bash` to find candidates and `run_python` to parse anything with
   structure, and print what you loaded: file names and line or row counts. A
   file you did not open is not evidence.
4. **Pull the change history if it is available.** With the release tracker
   connected, `rt_list_releases` for the affected service around the window
   narrows the candidate triggers fast. If the connector is dark, note that the
   deploy history was unavailable rather than guessing at it.
5. **Normalize the timestamps.** Put every row in one timezone (UTC unless the
   evidence is unambiguously local), and say which you used. Mixed timezones are
   the single most common way a reconstructed timeline is confidently wrong.
6. **Build the table**, one row per event, ordered earliest first:

   | timestamp | source | what happened |
   | --- | --- | --- |

   `source` names the specific artifact the row came from — a file name, a
   runbook id, a tool result — not "logs" or "the team".
7. **Mark every inferred row as inferred.** A row you reasoned to rather than
   read gets `(inferred)` in its `what happened` cell and, in the `source`
   column, the evidence you inferred it from. Anything you cannot attribute at
   all does not go in the table; put it under "Open questions" instead.
8. **State the impact window** explicitly — start, end, duration — and say what
   fixes each end of it. "Customer-visible errors began at the first failed
   health check" is a defensible start; "roughly mid-morning" is not.
9. **Name the triggering change or condition, or say it is unidentified.**
   rb-003 is blunt about this: a cause that cannot be pointed at is not a cause.
   Name the specific change, config edit, or condition and the evidence that
   ties it to the start of the impact window — or write "Trigger: unidentified"
   and list what evidence would settle it.
10. **Write the timeline to the workspace** as
    `incident-timeline-<incident>-<ISO date>.md` (the date the incident started,
    `YYYY-MM-DD`), then report its path in your final message. The workspace is
    the one filesystem that survives the run and that a human can go read.

## When the evidence is thin

Say so, and produce the short timeline the evidence supports rather than a long
one it does not. Three attributed rows and an honest "Trigger: unidentified"
is a usable postmortem input. Ten rows where six are guesses is a document that
will be quoted back as fact next quarter.

Do not smooth a gap with a plausible-sounding step. If nothing in the evidence
says who was paged, the timeline does not say who was paged.

## If you cannot read a runbook or a file

`rb_get_runbook` is an MCP tool and runs host-side, so it works whenever the
server is connected. If instead a read of a file under `protocols/` refuses or
cannot find it, try `bash` (`cat protocols/…`) before concluding it is missing —
depending on how this deployment is built, the file tools may not reach that
directory even though the file is there. If both fail, say which path you tried.

Evidence files in your own working directory are a different case: if one will
not parse, name it, say what you tried, and carry on with the rest.

## Output shape

- One paragraph: what happened, in plain language, first.
- **Impact window:** start → end (duration), and what defines each end.
- **Trigger:** the named change or condition and its evidence, or `unidentified`.
- The timeline table, earliest first, with `(inferred)` on every reasoned row.
- **Open questions:** what is still unattributed, and what would settle it.
- `Source: Declaring and running an incident (rb-003)`, plus every evidence file
  you read, by name.
- The path of the file you wrote.
